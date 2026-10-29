"""Real SDK, message admission, owner questions and child results, without network."""
import asyncio
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from sqlalchemy import func, select
from test_memory_dream import product as product  # noqa: PLC0414

from app.models import Approval, CausalChain, ContextManifest, Message, ReplyRequest
from app.question_models import QuestionRequest
from app.runtime_bridge import RuntimeBridge, text_content


@pytest.mark.parametrize("worker", [False, True])
def test_progress_then_ordinary_owner_answer_reaches_peer_and_private_owner_once(product, worker):
    async def run():
        runtime = RuntimeBridge(product)
        calls = []

        def tool(tool_name, **arguments):
            return {"type": "toolCall", "id": uuid4().hex, "name": tool_name, "arguments": arguments}

        def offline(model, context, options):
            position = max(index for index, row in enumerate(context.messages) if row["role"] == "user")
            user = text_content(context.messages[position])
            results = [row for row in context.messages[position + 1:] if row["role"] == "toolResult"]
            calls.append(user)
            if user.startswith("A message was delivered in this approved Duet channel."):
                user = user.split("\n", 1)[1]
            if user == "ROOT-ASK-PEER":
                content = [{"type": "text", "text": "Owner coordination requested"}] if results else [tool(
                    "request_duet_conversation", participant_owner_ids=["owner-maya"], title="Authorized coordination",
                    purpose="Ask the owner to choose a time", opening_message="PEER-ASK-OWNER")]
            elif user == "PEER-ASK-OWNER" and worker:
                content = [{"type": "text", "text": "PEER-PROGRESS-WAITING"}] if results else [tool(
                    "spawn_agent", name="worker", task="CHILD-ASK-OWNER")]
            elif user in {"PEER-ASK-OWNER", "CHILD-ASK-OWNER"}:
                content = ([] if worker else [{"type": "text", "text": "PEER-PROGRESS-WAITING"}]) if results else [tool(
                    "ask_question", question="Which time works?", options=["Friday 15", "Monday 10"])]
            elif user == "PEER-PROGRESS-WAITING":
                content = []  # Receipt completes silently; final is a separate future message.
            elif user == "Friday 15":
                if results:
                    content = []
                else:
                    with product.transaction() as db:
                        question = db.scalar(select(QuestionRequest).where(QuestionRequest.owner_id == "owner-maya"))
                        answer = db.scalar(select(Message).where(Message.author_id == "owner-maya", Message.body == user))
                        arguments = {"question_id": question.id, "message_id": answer.id}
                    content = [tool("answer_question", **arguments)]
            elif user.startswith("The owner answered"):
                if not results:
                    content = [tool("history", chat_id="private-maya", limit=10)]
                else:
                    assert "Friday 15" in str(results), "The real private history tool must return the owner's answer"
                    content = [{"type": "text", "text": "CHILD-CHOSE-FRIDAY" if worker else "PEER-FINAL-FRIDAY"}]
            elif user.startswith("A background worker"):
                assert "CHILD-CHOSE-FRIDAY" in user
                content = [{"type": "text", "text": "PEER-FINAL-FRIDAY"}]
            elif user == "PEER-FINAL-FRIDAY":
                content = [] if results else [tool("send_chat_message", chat_id="private-alex",
                    body="The peer owner chose Friday 15", source_message_ids=[])]
            else:
                raise AssertionError("Unexpected offline input " + user[:100])
            assert not any(result.get("isError") for result in results), results
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=content,
                stopReason="toolUse" if any(row["type"] == "toolCall" for row in content) else "stop")})
            return stream

        async def dispatch():
            for _ in range(20):
                await runtime.recover_background()
                ids = product.claim_dispatches()
                if not ids:
                    return
                for request_id in ids:
                    await product.execute_request(request_id)
            raise AssertionError("The finite offline dialogue did not settle")

        runtime.models.register_api("offline-regression", offline)
        await runtime.start(); product.bind_runtime(runtime)
        try:
            original = product.send_message("owner-alex", "private-alex", "ROOT-ASK-PEER", uuid4().hex)["request"]
            await product.execute_request(original["id"])
            with product.transaction() as db:
                approvals = [(row.approver_id, row.id) for row in db.scalars(select(Approval).where(Approval.status == "pending"))]
            assert len(approvals) == 2
            for owner, approval in approvals:
                await product.decide_approval(owner, approval, "approved", "once")
            await dispatch()
            async with asyncio.timeout(15):
                while True:
                    with product.transaction() as db:
                        question = db.scalar(select(QuestionRequest))
                        child_command = question.runtime_command_id if question else None
                    if question and (not worker or (await runtime.store.get_command("local", child_command))["state"] == "completed"):
                        break
                    await asyncio.sleep(.02)
            await dispatch()
            with product.transaction() as db:
                progress = db.scalar(select(Message).where(Message.body == "PEER-PROGRESS-WAITING"))
                assert progress
                silent = db.scalar(select(ReplyRequest).where(ReplyRequest.source_message_id == progress.id))
                silent_id = silent.id
                assert silent.status == "completed" and silent.source == "duet_message"
                assert list(db.scalars(select(Message).where(Message.request_id == silent_id))) == []
                before_count = db.scalar(select(func.count()).select_from(ReplyRequest))
                before_spent = db.get(CausalChain, original["causal_chain_id"]).spent
            for _ in range(3):
                await dispatch()
            with product.transaction() as db:
                assert db.scalar(select(func.count()).select_from(ReplyRequest)) == before_count
                assert db.get(CausalChain, original["causal_chain_id"]).spent == before_spent == 2
            # This is a regular unquoted private input, not the card answer API.
            owner_answer = product.send_message("owner-maya", "private-maya", "Friday 15", uuid4().hex)
            assert owner_answer["message"].get("reply_to") is None
            await dispatch()
            async with asyncio.timeout(15):
                while True:
                    await dispatch()
                    with product.transaction() as db:
                        report = db.scalar(select(Message).where(Message.body == "The peer owner chose Friday 15"))
                    if report:
                        break
                    await asyncio.sleep(.02)
            with product.transaction() as db:
                requests = list(db.scalars(select(ReplyRequest)))
                messages = list(db.scalars(select(Message)))
                assert all(row.status == "completed" for row in requests), [(row.source, row.status, row.error) for row in requests]
                assert sum(row.source == "question_result" for row in requests) == 1
                assert sum(row.source == "subagent_result" for row in requests) == int(worker)
                assert sum(row.body == "PEER-FINAL-FRIDAY" for row in messages) == 1
                assert sum(row.body == "The peer owner chose Friday 15" and row.chat_id == "private-alex" for row in messages) == 1
                assert not any(row.body == "CHILD-CHOSE-FRIDAY" for row in messages)
                assert db.get(CausalChain, original["causal_chain_id"]).spent == 3
                assert any(source.get("kind") == "chat_context" and source.get("chat_id") == "private-maya"
                           for manifest in db.scalars(select(ContextManifest).where(ContextManifest.owner_id == "owner-maya"))
                           for source in manifest.sources)
                request_ids = [row.id for row in requests]
                before_replay = len(requests), len(messages), len(calls)
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as denied:
                product.chat_detail("owner-alex", "private-maya")
            assert denied.value.status_code == 403
            for request_id in request_ids:
                await product.execute_request(request_id)
            await dispatch()
            with product.transaction() as db:
                assert (db.scalar(select(func.count()).select_from(ReplyRequest)),
                        db.scalar(select(func.count()).select_from(Message)), len(calls)) == before_replay
        finally:
            await runtime.close()
    asyncio.run(run())
