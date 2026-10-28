"""Independent product and SDK capacity pools on disposable PostgreSQL, offline."""
import asyncio
from uuid import uuid4

from agent_runtime import AssistantMessageEventStream, assistant_message
from sqlalchemy import select
from test_memory_dream import product as product  # noqa: PLC0414
from test_request_concurrency import accept

from app import questions, scheduled_tasks
from app.models import Actor, Chat, Membership, Owner, ReplyRequest
from app.question_models import QuestionRequest
from app.runtime_bridge import RuntimeBridge, text_content


class HeldModels:
    """Observe real model entry, keeping streams open until the test releases them."""

    def __init__(self):
        self.entered = set()
        self.changed = asyncio.Condition()
        self.release = asyncio.Event()
        self.producers = []

    def stream(self, model, options, tag):
        stream = AssistantMessageEventStream()

        async def finish():
            async with self.changed:
                self.entered.add(tag)
                self.changed.notify_all()
            released = asyncio.create_task(self.release.wait())
            aborted = asyncio.create_task(options["signal"].wait())
            try:
                await asyncio.wait({released, aborted}, return_when=asyncio.FIRST_COMPLETED)
                if options["signal"].aborted:
                    stream.push({"type": "error", "error": assistant_message(model, content=[],
                        stopReason="aborted", errorMessage="Offline stream stopped")})
                else:
                    stream.push({"type": "done", "message": assistant_message(model,
                        content=[{"type": "text", "text": tag + "-RESULT"}], stopReason="stop")})
            finally:
                released.cancel(); aborted.cancel()
                await asyncio.gather(released, aborted, return_exceptions=True)

        stream.task = asyncio.create_task(finish())
        self.producers.append(stream.task)
        return stream

    async def wait_for(self, tags):
        async with asyncio.timeout(30):
            async with self.changed:
                await self.changed.wait_for(lambda: set(tags) <= self.entered)

    async def wait_count(self, prefix, count):
        async with asyncio.timeout(30):
            async with self.changed:
                await self.changed.wait_for(lambda: sum(tag.startswith(prefix) for tag in self.entered) >= count)


def fourth_owner(product):
    with product.transaction() as db:
        db.add(Owner(id="owner-fourth", auth_subject="capacity-fourth")); db.flush()
        db.add_all([Actor(id=("owner" if kind == "human" else "twin") + "-fourth", owner_id="owner-fourth", kind=kind,
            name="Fourth", initials="F", color="blue") for kind in ("human", "twin")])
        db.add(Chat(id="private-fourth", kind="private", title="Fourth private", private_owner_id="owner-fourth")); db.flush()
        db.add(Membership(chat_id="private-fourth", owner_id="owner-fourth", history_policy="all"))


def schedule(product, owner, tag):
    task = scheduled_tasks.write(product, "owner-" + owner, values={"title": tag, "instructions": tag,
        "timezone": "UTC", "schedule": {"kind": "interval", "seconds": 60}})
    return scheduled_tasks.run_now(product, "owner-" + owner, task["id"], uuid4().hex)["request_id"]


def latest_user(context):
    return text_content(next(message for message in reversed(context.messages) if message["role"] == "user"))


def launch_claimed(product):
    ids = product.claim_dispatches()
    tasks = [asyncio.create_task(product.execute_request(request_id)) for request_id in ids]
    product.tasks.update(zip(ids, tasks, strict=True))
    return ids, tasks


def test_background_full_still_allows_eight_real_foreground_roots_and_isolated_stop(product):
    fourth_owner(product)

    async def run():
        runtime, held = RuntimeBridge(product), HeldModels()
        tags = {}

        def offline(model, context, options):
            user = latest_user(context)
            tag = next(tag for tag in tags if tag in user)
            return held.stream(model, options, tag)

        runtime.models.register_api("offline-regression", offline)
        await runtime.start(); product.bind_runtime(runtime)
        tasks = []
        try:
            background = [schedule(product, owner, "BG-" + owner) for owner in ("alex", "maya")]
            tags.update({"BG-alex": background[0], "BG-maya": background[1]})
            selected, started = launch_claimed(product); tasks.extend(started)
            assert set(selected) == set(background)
            await held.wait_for(tags)
            # Both the same-owner and global background ceilings are already full.
            queued_background = [schedule(product, owner, "WAIT-BG-" + owner) for owner in ("alex", "jordan")]
            foreground = []
            for owner in ("alex", "maya", "jordan", "fourth"):
                for index in range(2):
                    tag = f"FG-{owner}-{index}"
                    row = product.send_message("owner-" + owner, "private-" + owner, tag, uuid4().hex)["request"]
                    tags[tag] = row["id"]; foreground.append(row["id"])
            selected, started = launch_claimed(product); tasks.extend(started)
            assert set(selected) == set(foreground)
            await held.wait_for(tags)
            assert len(held.entered) == 10
            extra = product.send_message("owner-alex", "private-alex", "WAIT-FG", uuid4().hex)["request"]["id"]
            assert product.claim_dispatches() == []
            assert all(product.get_request(key)["status"] == "queued" for key in [extra, *queued_background])
            assert all(product.get_request(key)["status"] == "running" for key in [*foreground, *background])
            # Stop is routed to the actual background host; other roots stay live.
            await product.cancel_request("owner-alex", background[0])
            await runtime.signal_stop(product.get_request(background[0]))
            await asyncio.wait_for(tasks[0], 15)
            assert product.get_request(background[0])["status"] == "cancelled"
            assert all(product.get_request(key)["status"] == "running" for key in foreground)
        finally:
            held.release.set()
            await asyncio.gather(*tasks, *held.producers, return_exceptions=True)
            await runtime.close()
        assert all(product.get_request(key)["status"] == "completed" for key in foreground)
        assert product.get_request(background[1])["status"] == "completed"
    asyncio.run(run())


def test_background_child_pool_cannot_consume_eight_foreground_child_slots(product):
    async def run():
        runtime, held = RuntimeBridge(product), HeldModels()

        def offline(model, context, options):
            user = latest_user(context)
            if user.startswith(("BG-CHILD-", "FG-CHILD-")):
                return held.stream(model, options, user)
            stream = AssistantMessageEventStream()
            if any(message.get("role") == "toolResult" for message in context.messages):
                content = []
            else:
                background = "BACKGROUND-SPAWNER" in user
                prefix, count = ("BG", 3) if background else ("FG", 8)
                content = [{"type": "toolCall", "id": uuid4().hex, "name": "spawn_agent",
                    "arguments": {"name": "worker", "task": f"{prefix}-CHILD-{index}"}} for index in range(count)]
            stream.push({"type": "done", "message": assistant_message(model, content=content,
                stopReason="toolUse" if content else "stop")})
            return stream

        runtime.models.register_api("offline-regression", offline)
        await runtime.start(); product.bind_runtime(runtime)
        try:
            background = schedule(product, "alex", "BACKGROUND-SPAWNER")
            await product.execute_request(background)
            await held.wait_count("BG-CHILD-", 2)
            root = product.send_message("owner-alex", "private-alex", "FOREGROUND-SPAWNER", uuid4().hex)["request"]
            await product.execute_request(root["id"])
            await held.wait_for({f"FG-CHILD-{index}" for index in range(8)})
            assert len(held.entered) == 10 and sum(tag.startswith("BG-CHILD-") for tag in held.entered) == 2
            assert product.get_request(background)["status"] == product.get_request(root["id"])["status"] == "completed"
            # Each manager retains its selected pool, even for the same owner.
            bg = product.get_request(background)
            assert runtime.managers[bg["session_id"], True].host is runtime.background_host
            assert runtime.managers[root["session_id"], False].host is runtime.host
            # Shutdown retains both active and accepted-but-not-started child
            # commands. A fresh bridge must recover each into its original pool.
            old_hosts = runtime.host, runtime.background_host
            await runtime.close()
            assert all(host._closed for host in old_hosts)
            await asyncio.gather(*held.producers, return_exceptions=True)
            held = HeldModels()
            runtime = RuntimeBridge(product)
            runtime.models.register_api("offline-regression", offline)
            await runtime.start(); product.bind_runtime(runtime)
            await runtime.recover_pending()
            await held.wait_count("BG-CHILD-", 2)
            await held.wait_for({f"FG-CHILD-{index}" for index in range(8)})
            assert len(held.entered) == 10
            assert sum(tag.startswith("BG-CHILD-") for tag in held.entered) == 2
        finally:
            held.release.set()
            await asyncio.gather(*held.producers, return_exceptions=True)
            await runtime.close()
    asyncio.run(run())


def test_background_continuation_stays_ordered_without_using_foreground_quota(product):
    root = accept(product, "system_review")
    first = accept(product, "approval_result", origin=root)
    second = accept(product, "credential_result", origin=first)
    foreground = [accept(product) for _ in range(2)]
    assert set(product.claim_dispatches()) == {root, *foreground}
    product.tasks.update({key: object() for key in foreground})
    try:
        product.finish_request(root, "completed")
        assert product.claim_dispatches() == [first]
        product.finish_request(first, "completed")
        assert product.claim_dispatches() == [second]
        with product.transaction() as db:
            rows = list(db.scalars(select(ReplyRequest).where(ReplyRequest.id.in_([root, first, second]))))
            assert all(row.background for row in rows)
            assert len({row.session_id for row in rows}) == 1
    finally:
        product.tasks.clear()


def test_recovery_unknown_origin_preserves_command_and_reports_without_model_call(product, caplog):
    async def run():
        runtime = RuntimeBridge(product)
        calls = []
        runtime.models.register_api("offline-regression", lambda *args: calls.append(args))
        await runtime.start()
        sid, command_id = uuid4().hex, uuid4().hex
        try:
            await runtime.store.accept_command(runtime.tenant_id, sid, user_id="owner-alex",
                command_id=command_id, kind="prompt", payload={"message": "Unresolved historical child",
                    "subagent": {"name": "worker", "task": "Unresolved historical child",
                        "parent_session_id": "missing-parent", "parent_run_id": "missing-run",
                        "root_run_id": "missing-parent/missing-run", "operation_id": uuid4().hex}})
            await runtime.recover_pending()
            await runtime.recover_pending()
            saved = await runtime.store.get_command(runtime.tenant_id, command_id)
            assert saved["state"] == "accepted" and not saved.get("cancel_requested_at")
            assert not runtime.recovery_tasks and not calls
            assert "original product task is unavailable" in caplog.text
        finally:
            await runtime.close()
    asyncio.run(run())


def test_background_child_answer_resumes_child_and_reports_to_its_original_task(product):
    async def run():
        runtime, held = RuntimeBridge(product), HeldModels()

        def offline(model, context, options):
            user = latest_user(context)
            if user.startswith("The owner answered"):
                return held.stream(model, options, "ANSWERED-BACKGROUND-CHILD")
            position = max(index for index, message in enumerate(context.messages) if message["role"] == "user")
            results = [message for message in context.messages[position + 1:] if message["role"] == "toolResult"]
            if user == "BACKGROUND-QUESTION-CHILD":
                content = [] if results else [{"type": "toolCall", "id": uuid4().hex, "name": "ask_question",
                    "arguments": {"question": "Which format?", "options": ["Short", "Detailed"]}}]
            elif "BACKGROUND-QUESTION-ROOT" in user:
                content = [] if results else [{"type": "toolCall", "id": uuid4().hex, "name": "spawn_agent",
                    "arguments": {"name": "worker", "task": "BACKGROUND-QUESTION-CHILD"}}]
            else:
                content = [{"type": "text", "text": "Reviewed background answer" if "ANSWERED-BACKGROUND-CHILD-RESULT" in user else "Foreground reply"}]
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=content,
                stopReason="toolUse" if any(part["type"] == "toolCall" for part in content) else "stop")})
            return stream

        runtime.models.register_api("offline-regression", offline)
        await runtime.start(); product.bind_runtime(runtime)
        pending = None
        try:
            original = schedule(product, "alex", "BACKGROUND-QUESTION-ROOT")
            await product.execute_request(original)
            async with asyncio.timeout(15):
                while True:
                    with product.transaction() as db:
                        row = db.scalar(select(QuestionRequest))
                        question = (row.id, row.runtime_session_id, row.runtime_command_id) if row else None
                    if question and (await runtime.store.get_command(runtime.tenant_id, question[2]))["state"] == "completed":
                        break
                    await asyncio.sleep(.02)
            questions.answer(product, "owner-alex", question[0], "Detailed")
            with product.transaction() as db:
                carrier_id = db.get(QuestionRequest, question[0]).continuation_request_id
            pending = asyncio.create_task(product.execute_request(carrier_id))
            await held.wait_for({"ANSWERED-BACKGROUND-CHILD"})
            carrier = product.get_request(carrier_id)
            assert carrier["background"] and carrier["session_id"] == question[1]
            assert runtime.executors[carrier_id].host is runtime.background_host
            foreground = product.send_message("owner-alex", "private-alex", "Independent foreground", uuid4().hex)["request"]
            await product.execute_request(foreground["id"])
            assert product.get_request(foreground["id"])["status"] == "completed"
            assert product.get_request(carrier_id)["status"] == "running"
            held.release.set(); await pending
            async with asyncio.timeout(15):
                while True:
                    await runtime.recover_background()
                    with product.transaction() as db:
                        continuations = list(db.scalars(select(ReplyRequest.id).where(ReplyRequest.source == "subagent_result")))
                    if continuations:
                        break
                    await asyncio.sleep(.02)
            assert len(continuations) == 1
            resumed = product.get_request(continuations[0])
            assert resumed["background"] and resumed["session_id"] == product.get_request(original)["session_id"]
            await product.execute_request(resumed["id"])
            assert product.get_request(resumed["id"])["status"] == "completed"
        finally:
            held.release.set()
            await asyncio.gather(*([pending] if pending else []), *held.producers, return_exceptions=True)
            await runtime.close()
    asyncio.run(run())
