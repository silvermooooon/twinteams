"""Focused owner questions use real messages/FIFO and independent SDK contexts."""
import asyncio
from uuid import uuid4

import pytest
from duet_test_support import send_message
from fastapi import HTTPException
from sqlalchemy import func, select
from test_credentials import product as product  # noqa: PLC0414
from test_nested_worker_parity import call, response, text

from app import questions
from app.models import ActivityProjection, Message, ReplyRequest
from app.question_models import QuestionRequest
from app.runtime_bridge import RuntimeBridge


def pending(product, chat_id="private-alex"):
    if chat_id == "private-alex":
        request = send_message(product, "owner-alex", chat_id, "Prepare a useful report", uuid4().hex)["request"]
    else:
        from duet_test_support import seed_collaborations
        seed_collaborations(product)
        sent = send_message(product, "owner-maya", chat_id, "Prepare a useful report", uuid4().hex)
        with product.transaction() as db:
            chat, _ = product.membership(db, "owner-alex", chat_id)
            request = product.request_payload(product.accept_request(db, chat, product.twin(db, "owner-alex").id,
                "owner-maya", "auto", uuid4().hex, source_message_id=sent["message"]["id"]))
    session_id = product.get_request(request["id"])["session_id"]
    product.bind_runtime_run(request["id"], session_id, "run-" + request["id"])
    context = product.get_runtime_context(request["id"])
    context.update(operation_id=uuid4().hex, runtime_session_id=session_id,
        runtime_run_id="run-" + request["id"], runtime_command_id=request["id"])
    args = {"question": "Which format should I use?", "options": ["Short", "Detailed"]}
    row = questions.create(product, args, context)
    return request, context, row, args


def test_real_card_idempotency_and_owner_scope(product):
    _request, context, row, args = pending(product)
    assert questions.create(product, args, context) == row
    assert product.tasks == {}
    chat = product.chat_detail("owner-alex", "private-alex")
    assert chat["questions"] == [row]
    assert next(m for m in chat["messages"] if m["id"] == row["message_id"])["body"] == row["question"]
    assert product.chat_detail("owner-alex", "group-alex-maya")["questions"] == []
    assert product.chat_detail("owner-maya", "private-maya")["questions"] == []
    with pytest.raises(HTTPException):
        questions.answer(product, "owner-maya", row["id"], "Short")
    with pytest.raises(HTTPException):
        questions.create(product, args, {**context, "operation_id": uuid4().hex})
    assert questions.pending_for_run(product, context["runtime_session_id"], context["runtime_run_id"])


@pytest.mark.parametrize("mode", ["endpoint", "reply", "dismiss"])
def test_exact_answer_creates_only_one_original_task_continuation(product, mode):
    request, _, row, _ = pending(product)
    product.complete_request(request["id"])
    if mode == "endpoint":
        result = questions.answer(product, "owner-alex", row["id"], "Detailed, verbatim owner text.")
        assert questions.answer(product, "owner-alex", row["id"], result["answer"]) == result
        with pytest.raises(HTTPException):
            questions.answer(product, "owner-alex", row["id"], "A different answer")
    elif mode == "reply":
        sent = send_message(product, "owner-alex", "private-alex", "Detailed, verbatim owner text.", uuid4().hex, reply_to=row["message_id"])
        assert sent["request"]["source"] == "question_result"
    else:
        result = questions.dismiss(product, "owner-alex", row["id"])
        assert result["status"] == "cancelled" and result["answer"] is None
        assert questions.dismiss(product, "owner-alex", row["id"]) == result
    with product.transaction() as db:
        saved = db.get(QuestionRequest, row["id"])
        resumed = db.get(ReplyRequest, saved.continuation_request_id)
        assert resumed.source == "question_result" and resumed.causal_chain_id == request["causal_chain_id"]
        assert resumed.requester_id == request["requester_id"]
        assert db.scalar(select(func.count()).select_from(ReplyRequest)) == 2
        assert "not new action approval" in resumed.input_body
        assert (saved.answer in resumed.input_body) if saved.answer else ('"owner_declined": true' in resumed.input_body)


def test_unquoted_new_task_not_swallowed_and_model_uses_only_current_real_owner_text(product):
    _, _, row, _ = pending(product)
    key = uuid4().hex
    sent = send_message(product, "owner-alex", "private-alex", "Short please; keep this exact punctuation!", key)
    assert sent["request"]["source"] == "private"
    with product.transaction() as db:
        assert db.get(QuestionRequest, row["id"]).status == "pending"
    context = product.get_runtime_context(sent["request"]["id"])
    result = questions.answer_from_message(product, {"question_id": row["id"], "message_id": sent["message"]["id"]}, context)
    assert result["answer"] == sent["message"]["body"]
    assert questions.answer_from_message(product, {"question_id": row["id"], "message_id": sent["message"]["id"]}, context) == result
    repeated = send_message(product, "owner-alex", "private-alex", sent["message"]["body"], key)
    assert repeated["message"]["id"] == sent["message"]["id"] and repeated["message"].get("reply_to") is None
    with pytest.raises(HTTPException):
        questions.answer_from_message(product, {"question_id": row["id"], "message_id": row["message_id"]}, context)


def test_natural_association_rejects_existing_unrelated_quote(product):
    _, _, row, _ = pending(product)
    other = send_message(product, "owner-alex", "private-alex", "Another topic", uuid4().hex)
    sent = send_message(product, "owner-alex", "private-alex", "Short", uuid4().hex, reply_to=other["message"]["id"])
    context = product.get_runtime_context(sent["request"]["id"])
    with pytest.raises(HTTPException) as caught:
        questions.answer_from_message(product, {"question_id": row["id"], "message_id": sent["message"]["id"]}, context)
    assert caught.value.status_code == 409


@pytest.mark.parametrize("revoke", ["withdraw", "leave", "cancel"])
def test_group_owner_only_question_revocation_never_resumes_or_leaks_known_id(product, revoke):
    request, _, row, _ = pending(product, "duet-alex-maya")
    with product.transaction() as db:
        if revoke == "withdraw":
            db.get(Message, request["source_message_id"]).state = "withdrawn"
        elif revoke == "leave":
            product.membership(db, "owner-alex", "duet-alex-maya")[1].left_at = db.get(Message, request["source_message_id"]).created_at
    if revoke == "cancel":
        asyncio.run(product.cancel_request("owner-alex", request["id"]))
    for action in [lambda: questions.answer(product, "owner-alex", row["id"], "Short"),
                   lambda: questions.dismiss(product, "owner-alex", row["id"])]:
        with pytest.raises(HTTPException):
            action()
    detail = product.chat_detail("owner-alex", "private-alex")
    assert detail["questions"] == []
    if revoke != "cancel":
        assert row["message_id"] not in {m["id"] for m in detail["messages"]}


@pytest.mark.parametrize("worker,dismiss,restart", [(False, False, False), (True, False, False), (True, True, True)])
def test_actual_sdk_answer_restores_exact_task_context_and_child_returns_to_actual_parent(product, worker, dismiss, restart):
    async def run():
        runtime = RuntimeBridge(product)
        calls = []
        def offline(model, context, options):
            user = text(next(message for message in reversed(context.messages) if message["role"] == "user"))
            position = max(i for i, message in enumerate(context.messages) if message["role"] == "user")
            results = [message for message in context.messages[position + 1:] if message["role"] == "toolResult"]
            calls.append((user, context))
            if user == "UNRELATED-NEW-TASK":
                return response(model, [{"type": "text", "text": "UNRELATED-TASK-COMPLETED"}])
            if user.startswith("The owner answered"):
                assert any(text(message) == ("WORKER-ORIGINAL-TASK" if worker else "ROOT-ORIGINAL-TASK") for message in context.messages)
                if worker:
                    assert "ROOT-ORIGINAL-TASK" not in text(context.messages[0])
                if not results:
                    # A product tool verifies the resumed context/manifest, not just a shell.
                    return response(model, call("list_authorized_chats"))
                assert not results[-1].get("isError"), results[-1]
                assert ('"owner_declined": true' in user) if dismiss else ("Detailed" in user)
                return response(model, [{"type": "text", "text": "WORKER-FINAL-REVIEWABLE" if worker else "MAIN-ANSWER-RESUMED"}])
            if "WORKER-FINAL-REVIEWABLE" in user:
                return response(model, [{"type": "text", "text": "ROOT-REVIEWED-ANSWER"}])
            if worker and user == "ROOT-ORIGINAL-TASK":
                return response(model, call("spawn_agent", name="worker", task="WORKER-ORIGINAL-TASK")) if not results else response(model, [])
            if not results:
                return response(model, call("ask_question", question="Which report format?", options=["Short", "Detailed"]))
            return response(model, [])
        runtime.models.register_api("offline-regression", offline)
        await runtime.start(); product.bind_runtime(runtime)
        try:
            original = send_message(product, "owner-alex", "private-alex", "ROOT-ORIGINAL-TASK", uuid4().hex)["request"]
            await product.execute_request(original["id"])
            for _ in range(300):
                with product.transaction() as db:
                    row = db.scalar(select(QuestionRequest)); question_id = row.id if row else None
                if question_id and (not worker or await runtime.store.pending_child_results() == []):
                    break
                await asyncio.sleep(.02)
            assert question_id
            for _ in range(200):
                if not worker:
                    break
                await runtime.recover_background()
                with product.transaction() as db:
                    row = db.get(QuestionRequest, question_id)
                    child_command = row.runtime_command_id
                saved = await runtime.store.get_command("local", child_command)
                if saved["state"] == "completed":
                    break
                await asyncio.sleep(.02)
            assert await runtime.has_unresolved_scheduled_work(product.get_request(original["id"]))
            with product.transaction() as db:
                assert db.scalar(select(func.count()).select_from(ReplyRequest).where(ReplyRequest.source == "subagent_result")) == 0
                row = db.get(QuestionRequest, question_id); old_session, old_run = row.runtime_session_id, row.runtime_run_id
            if restart:
                await runtime.close(); runtime = RuntimeBridge(product)
                runtime.models.register_api("offline-regression", offline)
                await runtime.start();product.bind_runtime(runtime)
            unrelated = send_message(product, "owner-alex", "private-alex", "UNRELATED-NEW-TASK", uuid4().hex)["request"]
            await product.execute_request(unrelated["id"])
            assert product.get_request(unrelated["id"])["session_id"] != old_session
            (questions.dismiss(product, "owner-alex", question_id) if dismiss else questions.answer(product, "owner-alex", question_id, "Detailed"))
            with product.transaction() as db:
                continuation_id = db.get(QuestionRequest, question_id).continuation_request_id
            await product.execute_request(continuation_id)
            resumed = product.get_request(continuation_id)
            with product.transaction() as db:
                error = db.get(ReplyRequest, continuation_id).error
            if resumed["status"] != "completed":
                from agent_runtime.sessions import DatabaseSession
                failed_session = await DatabaseSession.open(runtime.store, tenant_id=runtime.tenant_id, session_id=old_session)
                error = (error, failed_session.snapshot.get("error"))
            assert resumed["status"] == "completed", error
            assert resumed["session_id"] == old_session and resumed["run_id"] != old_run
            if worker:
                for _ in range(300):
                    await runtime.recover_background()
                    with product.transaction() as db:
                        parent_ids = list(db.scalars(select(ReplyRequest.id).where(ReplyRequest.source == "subagent_result")))
                    if parent_ids:
                        break
                    await asyncio.sleep(.02)
                assert len(parent_ids) == 1
                await product.execute_request(parent_ids[0])
                expected = "ROOT-REVIEWED-ANSWER"
                assert product.get_request(parent_ids[0])["session_id"] == product.get_request(original["id"])["session_id"]
                assert product.get_request(parent_ids[0])["session_id"] != old_session
            else:
                expected = "MAIN-ANSWER-RESUMED"
                with product.transaction() as db:
                    title = db.scalar(select(ActivityProjection.title).where(ActivityProjection.session_id == resumed["session_id"], ActivityProjection.run_id == resumed["run_id"]))
                    assert title == "Continuing your task"
            detail = product.chat_detail("owner-alex", "private-alex")
            assert sum(message["body"] == expected for message in detail["messages"]) == 1
            assert all(message["body"] != "WORKER-FINAL-REVIEWABLE" for message in detail["messages"])
        finally:
            await runtime.close()
    asyncio.run(run())


def test_frozen_answer_remains_task_input_after_message_withdrawal_but_not_before_association(product):
    _, _, row, _ = pending(product)
    sent = send_message(product, "owner-alex", "private-alex", "Detailed", uuid4().hex)
    context = product.get_runtime_context(sent["request"]["id"])
    with product.transaction() as db:
        db.get(Message, sent["message"]["id"]).state = "withdrawn"
    with pytest.raises(HTTPException):
        questions.answer_from_message(product, {"question_id": row["id"], "message_id": sent["message"]["id"]}, context)
    answered = questions.answer(product, "owner-alex", row["id"], "Short")
    with product.transaction() as db:
        saved = db.get(QuestionRequest, row["id"])
        continuation_id = saved.continuation_request_id
        db.get(Message, answered["answer_message_id"]).state = "withdrawn"
    assert product.get_runtime_context(continuation_id)["input_body"].endswith('"owner_declined": false}')
    assert questions.continuation_context(product, continuation_id)["id"] == row["id"]


def test_reset_erases_pending_questions_and_replay_cannot_reenroll_or_reveal(product):
    _request, context, row, args = pending(product)
    product.personal_reset("owner-alex", uuid4().hex, 0)
    with product.transaction() as db:
        saved = db.get(QuestionRequest, row["id"])
        assert saved.status == "cancelled" and saved.question == "" and saved.answer is None
    with pytest.raises(HTTPException):
        questions.create(product, args, context)
    with pytest.raises(HTTPException):
        questions.answer(product, "owner-alex", row["id"], "Short")


@pytest.mark.parametrize("running", [False, True])
def test_private_reset_hard_stops_only_answer_carriers_from_group_tasks(product, running):
    request, _, row, _ = pending(product, "duet-alex-maya")
    questions.answer(product, "owner-alex", row["id"], "Short")
    with product.transaction() as db:
        continuation_id = db.get(QuestionRequest, row["id"]).continuation_request_id
        if running:
            db.get(ReplyRequest, continuation_id).status = "running"
    product.personal_reset("owner-alex", uuid4().hex, 0)
    with product.transaction() as db:
        saved = db.get(QuestionRequest, row["id"])
        carrier = db.get(ReplyRequest, continuation_id)
        assert saved.continuation_request_id is None and carrier.cancel_requested
        assert db.get(ReplyRequest, request["id"]).status == "running", "Unrelated original group task remains intact"
    with pytest.raises(HTTPException):
        questions.continuation_context(product, continuation_id)


def test_api_authenticated_answer_and_schema_upgrade_are_repeatable(product):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import inspect

    from app import api, question_api
    from app import service as module
    from app.question_schema import check_question_schema, upgrade_question_schema
    _, _, row, _ = pending(product)
    app = FastAPI(); app.include_router(question_api.router)
    app.dependency_overrides[api.identity] = lambda: "owner-alex"
    old = question_api.service; question_api.service = product
    try:
        client = TestClient(app)
        response = client.post('/api/questions/' + row['id'] + '/answer', json={'answer': 'Detailed'})
        assert response.status_code == 200 and response.json()['status'] == 'answered'
        assert client.post('/api/questions/' + row['id'] + '/answer', json={'answer': 'Detailed'}).json() == response.json()
        app.dependency_overrides[api.identity] = lambda: "owner-maya"
        assert client.post('/api/questions/' + row['id'] + '/dismiss').status_code == 404
    finally:
        question_api.service = old
    upgrade_question_schema(module.engine); upgrade_question_schema(module.engine)
    check_question_schema(inspect(module.engine))


def test_private_prompt_projection_does_not_reverse_group_answer_lock_order(product, monkeypatch):
    import threading

    from sqlalchemy import event

    from app import service as module
    _, _, row, _ = pending(product, "duet-alex-maya")
    fresh = send_message(product, "owner-alex", "private-alex", "Another ordinary task", uuid4().hex)["request"]
    private_locked, group_locked = threading.Event(), threading.Event()
    original = questions.pending_questions
    errors = []
    def delayed(*args):
        if threading.current_thread().name == "question-projection":
            private_locked.set()
            assert group_locked.wait(5)
        return original(*args)
    monkeypatch.setattr(questions, "pending_questions", delayed)
    def observed(connection, cursor, statement, parameters, context, many):
        if (threading.current_thread().name == "question-answer" and "FOR UPDATE" in statement
                and "tt_chats" in statement and "duet-alex-maya" in str(parameters)):
            group_locked.set()
    event.listen(module.engine, "after_cursor_execute", observed)
    def execute(action):
        try:
            action()
        except BaseException as error:  # noqa: BLE001 - assert thread failures in the test thread
            errors.append(error)
    projection = threading.Thread(name="question-projection", target=execute,
        args=(lambda: product.get_runtime_context(fresh["id"]),))
    answering = threading.Thread(name="question-answer", target=execute,
        args=(lambda: questions.answer(product, "owner-alex", row["id"], "Short"),))
    try:
        projection.start(); assert private_locked.wait(5)
        answering.start(); projection.join(10); answering.join(10)
        assert not projection.is_alive() and not answering.is_alive()
        assert not errors, errors
        with product.transaction() as db:
            assert db.get(QuestionRequest, row["id"]).status == "answered"
    finally:
        event.remove(module.engine, "after_cursor_execute", observed)
