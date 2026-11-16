"""Real SDK topic reads/posts, owner questions and child results, without network."""
import asyncio
import json
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from sqlalchemy import func, select
from test_memory_dream import product as product  # noqa: PLC0414

from app.models import Approval, ContextManifest, Message, ReplyRequest
from app.question_models import QuestionRequest
from app.runtime_bridge import RuntimeBridge, text_content


@pytest.mark.parametrize("worker", [False, True])
def test_topic_mention_owner_answer_and_admin_report_settle_without_final_posts(product, worker):
    async def run():
        runtime = RuntimeBridge(product)
        calls = []
        topic_id = None

        def tool(tool_name, **arguments):
            return {"type": "toolCall", "id": uuid4().hex, "name": tool_name, "arguments": arguments}

        def offline(model, context, options):
            position = max(index for index, row in enumerate(context.messages) if row["role"] == "user")
            user = text_content(context.messages[position])
            results = [row for row in context.messages[position + 1:] if row["role"] == "toolResult"]
            calls.append(user)
            assert not any(result.get("isError") for result in results), results
            content = []
            if user == "ROOT-ASK-PEER":
                if not results:
                    content = [tool("create_duet_topic", participant_owner_ids=["owner-maya"],
                        title="Authorized coordination", purpose="Ask the owner to choose a time",
                        opening_message="PEER-ASK-OWNER", mention_owner_ids=["owner-maya"])]
            elif user.startswith("All owners approved Duet topic "):
                pass
            elif user.startswith("A Duet topic update notification arrived."):
                assert "PEER-ASK-OWNER" not in user and "PEER-FINAL-FRIDAY" not in user
                notice = json.loads(user.split("\n", 1)[1])
                if not results:
                    content = [tool("read_duet_topic", chat_id=notice["topic_id"])]
                elif len(results) == 1:
                    page = results[0].get("details") or json.loads(text_content(results[0]))
                    latest = page["messages"][-1]["body"]
                    if notice["mentioned"]:
                        assert latest == "PEER-ASK-OWNER"
                        content = [tool("spawn_agent", name="worker", task="CHILD-ASK-OWNER")] if worker else [tool(
                            "ask_question", question="Which time works?", options=["Friday 15", "Monday 10"])]
                    elif latest == "PEER-FINAL-FRIDAY":
                        content = [tool("send_chat_message", chat_id="private-alex",
                            body="The peer owner chose Friday 15", source_message_ids=[])]
                    else:
                        content = [{"type": "text", "text": "ADMIN-SILENT-JOURNAL-ONLY"}]
            elif user == "CHILD-ASK-OWNER":
                if not results:
                    content = [tool("ask_question", question="Which time works?", options=["Friday 15", "Monday 10"])]
            elif user == "Friday 15":
                if not results:
                    with product.transaction() as db:
                        question = db.scalar(select(QuestionRequest).where(QuestionRequest.owner_id == "owner-maya"))
                        answer = db.scalar(select(Message).where(Message.author_id == "owner-maya", Message.body == user))
                        content = [tool("answer_question", question_id=question.id, message_id=answer.id)]
            elif user.startswith("The owner answered"):
                if not results:
                    content = [tool("history", chat_id="private-maya", limit=10)]
                elif worker:
                    assert "Friday 15" in str(results)
                    content = [{"type": "text", "text": "CHILD-CHOSE-FRIDAY"}]
                elif len(results) == 1:
                    content = [tool("post_duet_reply", chat_id=topic_id, body="PEER-FINAL-FRIDAY", mention_owner_ids=[])]
            elif user.startswith("A background worker"):
                assert "CHILD-CHOSE-FRIDAY" in user
                if not results:
                    content = [tool("post_duet_reply", chat_id=topic_id, body="PEER-FINAL-FRIDAY", mention_owner_ids=[])]
            else:
                raise AssertionError("Unexpected offline input " + user[:140])
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
            raise AssertionError("The finite offline topic workflow did not settle")

        runtime.models.register_api("offline-regression", offline)
        await runtime.start(); product.bind_runtime(runtime)
        try:
            original = product.send_message("owner-alex", "private-alex", "ROOT-ASK-PEER", uuid4().hex)["request"]
            await product.execute_request(original["id"])
            with product.transaction() as db:
                approvals = [(row.approver_id, row.id, row.chat_id) for row in db.scalars(select(Approval).where(Approval.status == "pending"))]
            assert len(approvals) == 2
            topic_id = approvals[0][2]
            for owner, approval, _ in approvals:
                await product.decide_approval(owner, approval, "approved", "once")
            async with asyncio.timeout(20):
                while True:
                    await dispatch()
                    with product.transaction() as db:
                        question = db.scalar(select(QuestionRequest))
                    if question and (not worker or (await runtime.store.get_command("local", question.runtime_command_id))["state"] == "completed"):
                        break
                    await asyncio.sleep(.02)
            with product.transaction() as db:
                assert [row.body for row in db.scalars(select(Message).where(Message.chat_id == topic_id))] == ["PEER-ASK-OWNER"]
            answer = product.send_message("owner-maya", "private-maya", "Friday 15", uuid4().hex)
            assert answer["message"].get("reply_to") is None
            async with asyncio.timeout(20):
                while True:
                    await dispatch()
                    with product.transaction() as db:
                        report = db.scalar(select(Message).where(Message.body == "The peer owner chose Friday 15"))
                    if report:
                        break
                    await asyncio.sleep(.02)
            await dispatch()
            with product.transaction() as db:
                requests = list(db.scalars(select(ReplyRequest)))
                messages = list(db.scalars(select(Message)))
                assert all(row.status == "completed" for row in requests), [(row.source, row.status, row.error) for row in requests]
                assert sum(row.source == "question_result" for row in requests) == 1
                assert sum(row.source == "subagent_result" for row in requests) == int(worker)
                assert sum(row.body == "PEER-FINAL-FRIDAY" for row in messages) == 1
                assert sum(row.body == "The peer owner chose Friday 15" and row.chat_id == "private-alex" for row in messages) == 1
                assert not any(row.body in {"CHILD-CHOSE-FRIDAY", "ADMIN-SILENT-JOURNAL-ONLY"} for row in messages)
                assert any(source.get("kind") == "duet_post_revision"
                    for manifest in db.scalars(select(ContextManifest)) for source in manifest.sources)
                request_ids = [row.id for row in requests]
                before = len(requests), len(messages), len(calls)
            for request_id in request_ids:
                await product.execute_request(request_id)
            await dispatch()
            with product.transaction() as db:
                assert (db.scalar(select(func.count()).select_from(ReplyRequest)),
                        db.scalar(select(func.count()).select_from(Message)), len(calls)) == before
        finally:
            await runtime.close()
    asyncio.run(run())
