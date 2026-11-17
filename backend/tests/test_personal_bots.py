"""Personal BOT behavior on real product storage, without a model provider."""
import asyncio
from dataclasses import replace
from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, select, text
from test_memory_dream import product as product  # noqa: PLC0414

from app import api, bot_api, personal_api, personal_bots
from app import service as service_module
from app.models import Actor, Chat, ContextManifest, Membership, ProductOperation, ReplyRequest
from app.personal_bots_schema import check_personal_bots_schema, upgrade_personal_bots_schema


def create(product, name="Research"):
    return personal_bots.create_bot(product, "owner-alex", name, "Check evidence and report useful findings.")


@pytest.mark.parametrize("from_bot", [False, True])
def test_primary_topic_draft_requires_exact_owner_confirmation(product, from_bot):
    from duet_test_support import approved_chat

    from app.models import Approval, Message
    approved_chat(product, "confirmed-topic")
    if from_bot:
        bot = create(product)
        accepted = personal_bots.dispatch_tasks(product, "owner-alex", [bot["id"]], "Review evidence", uuid4().hex)
        complete(product, accepted["request_ids"][0], "Reviewed evidence")
    context = origin(product)
    args = {"chat_id": "confirmed-topic", "body": "Reviewed draft", "mention_owner_ids": []}
    with pytest.raises(HTTPException, match="owner's confirmation"):
        call(product, context, "post_duet_reply", args)
    with product.transaction() as db:
        assert not list(db.scalars(select(Message).where(Message.chat_id == "confirmed-topic")))
        request = db.get(ReplyRequest, context["request_id"])
        request.run_id = "synthetic-review-run"
        context.update(runtime_session_id=request.session_id, runtime_run_id=request.run_id)
    approval = call(product, context, "create_approval", {"title": "Review topic post", "description": "Reviewed BOT evidence",
        "target": "Confirmed topic", "impact": "Publish the exact draft", "action": {"tool": "post_duet_reply", "arguments": args}})
    assert approval["status"] == "pending"
    with product.transaction() as db:
        db.get(Approval, approval["id"]).status = "approved"
    approved = {**context, "approved_approval_id": approval["id"]}
    with pytest.raises(HTTPException, match="exact approved action"):
        call(product, approved, "post_duet_reply", {**args, "body": "Unreviewed change"})
    posted = call(product, approved, "post_duet_reply", args)
    assert posted["message_id"]


def test_bot_discovery_tool_returns_metadata_without_transcript_preview(product):
    bot = create(product)
    product.send_message("owner-alex", bot["chat_id"], "Only explicit history inspection should read this", uuid4().hex)
    catalog = call(product, origin(product, bot["chat_id"]), "list_personal_bots", {})
    with product.transaction() as db:
        main = product.twin(db, "owner-alex")
        assert catalog["main_duet"] == {"id": main.id, "name": main.name}
    assert set(catalog["bots"][0]) == {"id", "chat_id", "name", "assignment"}


def test_bot_waiting_for_two_peer_results_returns_only_its_final_review(product):
    first, second, third = [create(product, name) for name in ("Research", "Review", "Planning")]
    accepted = personal_bots.dispatch_tasks(product, "owner-alex", [first["id"]], "Gather both reviews", uuid4().hex)
    root_id = accepted["request_ids"][0]
    context = product.get_runtime_context(root_id)
    peers = [call(product, context, "message_personal_agent", {"recipient_actor_id": peer["id"], "body": "Review your part"})
             for peer in (second, third)]
    complete(product, root_id, "")
    assert not any((row.get("coordination") or {}).get("kind") == "result"
                   for row in product.chat_detail("owner-alex", "private-alex")["messages"])
    complete(product, peers[0]["request_id"], "First review")
    with product.transaction() as db:
        continuation = db.scalar(select(ReplyRequest).where(ReplyRequest.chat_id == first["chat_id"],
            ReplyRequest.source == "bot_result", ReplyRequest.status == "queued"))
        first_continuation = continuation.id
    complete(product, first_continuation, "Reviewed first part; second is pending")
    assert not any((row.get("coordination") or {}).get("kind") == "result"
                   for row in product.chat_detail("owner-alex", "private-alex")["messages"])
    complete(product, peers[1]["request_id"], "Second review")
    with product.transaction() as db:
        continuation = db.scalar(select(ReplyRequest).where(ReplyRequest.chat_id == first["chat_id"],
            ReplyRequest.source == "bot_result", ReplyRequest.status == "queued"))
        second_continuation = continuation.id
    complete(product, second_continuation, "Both reviews verified")
    returned = [row for row in product.chat_detail("owner-alex", "private-alex")["messages"]
                if (row.get("coordination") or {}).get("kind") == "result"]
    assert [row["body"] for row in returned] == ["Both reviews verified"]


@pytest.mark.parametrize("in_room", [False, True])
def test_bot_delivers_relative_file_from_its_own_workspace(product, monkeypatch, tmp_path, in_room):
    monkeypatch.setattr(service_module, "settings", replace(service_module.settings, workspace_root=tmp_path))
    bot = create(product)
    path = tmp_path / bot["chat_id"] / "report.md"
    path.parent.mkdir()
    path.write_text("BOT evidence")
    if in_room:
        room = personal_bots.create_room(product, "owner-alex", "Review", [bot["id"]])
        product.send_message("owner-alex", room["id"], "Share your report", uuid4().hex,
                             recipient_actor_ids=[bot["id"]])
        with product.transaction() as db:
            request_id = db.scalar(select(ReplyRequest.id).where(ReplyRequest.chat_id == room["id"]))
        context = product.get_runtime_context(request_id)
    else:
        context = origin(product, bot["chat_id"])
    delivered = call(product, context, "present_files", {"paths": ["report.md"], "caption": "Evidence"})
    assert delivered["sent"]
    attachment = delivered["message"]["attachments"][0]
    data, _ = product.attachment_content("owner-alex", context["chat_id"], attachment["id"])
    assert data == b"BOT evidence"


def test_bot_message_cannot_enter_primary_while_reset_is_pending(product):
    from app.models import Message

    bot = create(product)
    with product.transaction() as db:
        db.get(Chat, "private-alex").reset_pending = True
    with pytest.raises(HTTPException, match="reset cleanup is pending"):
        personal_bots.message_agent(product, "owner-alex", bot["id"], "twin-alex", "Review", uuid4().hex)
    with product.transaction() as db:
        assert not db.scalar(select(Message.id).where(Message.coordination.is_not(None)))


def test_transcript_inspection_batches_attachment_hash_reads(product, monkeypatch, tmp_path):
    monkeypatch.setattr(service_module, "settings", replace(service_module.settings, workspace_root=tmp_path))
    bot = create(product)
    counts = []
    statements = []
    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    for count in (1, 20):
        for index in range(1 if count == 20 else 0, count):
            attachment = product.upload_attachment("owner-alex", bot["chat_id"], f"note-{index}.txt", b"Evidence", "text/plain")
            product.send_message("owner-alex", bot["chat_id"], f"Inspect note {index}", uuid4().hex,
                                 attachment_ids=[attachment["id"]])
        context = origin(product)
        statements.clear()
        event.listen(service_module.engine, "before_cursor_execute", record)
        try:
            result = call(product, context, "inspect_bot_history", {"bot_id": bot["id"], "limit": 100})
            assert len(result["messages"]) == count
            counts.append(len(statements))
        finally:
            event.remove(service_module.engine, "before_cursor_execute", record)
    assert counts[0] == counts[1], counts


def call(product, context, name, args, operation=None):
    return asyncio.run(product.runtime_tool(name, args,
        {**context, "operation_id": operation or uuid4().hex}))


def origin(product, chat_id="private-alex", body="Coordinate the task"):
    sent = product.send_message("owner-alex", chat_id, body, uuid4().hex)
    return product.get_runtime_context(sent["request"]["id"])


def complete(product, request_id, body="Verified result"):
    request = product.get_request(request_id)
    product.complete_request(request_id, body=body, session_id=request["session_id"], run_id="run-" + request_id)


@pytest.fixture
def client(product, monkeypatch):
    monkeypatch.setattr(api, "service", product)
    monkeypatch.setattr(bot_api, "service", product)
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(bot_api.router)
    app.include_router(personal_api.router)
    def test_identity(x_test_owner: Annotated[str, Header()] = "owner-alex"):
        return x_test_owner
    app.dependency_overrides[api.identity] = test_identity
    with TestClient(app) as client:
        yield client


def test_owner_bot_lifecycle_api_and_other_owner_isolation(product, client):
    assert client.get("/api/bots").json() == []
    created = client.post("/api/bots", json={"name": "  Research  ", "assignment": "  Check evidence  "})
    assert created.status_code == 200
    bot = created.json()
    assert (bot["name"], bot["assignment"], bot["actor"]["kind"], bot["chat"]["kind"]) == (
        "Research", "Check evidence", "bot", "bot")
    second = create(product, "Planning")
    assert {row["id"] for row in client.get("/api/bots").json()} == {bot["id"], second["id"]}
    updated = client.patch(f"/api/bots/{bot['id']}", json={"name": "Evidence", "assignment": "Verify sources"})
    assert updated.json()["chat"]["title"] == "Evidence"
    sent = client.post(f"/api/chats/{bot['chat_id']}/messages",
        json={"body": "Check this claim", "client_request_key": uuid4().hex}).json()
    assert sent["request"]["target_twin_id"] == bot["id"]
    context = product.get_runtime_context(sent["request"]["id"])
    assert context["target_twin_id"] == bot["id"]
    complete(product, sent["request"]["id"], "The claim is supported.")
    detail = client.get(f"/api/chats/{bot['chat_id']}").json()
    assert [row["body"] for row in detail["messages"]] == ["Check this claim", "The claim is supported."]
    assert detail["messages"][-1]["author_id"] == bot["id"]
    inspected = client.get(f"/api/bots/{bot['id']}/history?limit=1").json()
    assert inspected["has_more"] and inspected["messages"][0]["body"] == "The claim is supported."
    other = {"x-test-owner": "owner-maya"}
    assert client.get("/api/bots", headers=other).json() == []
    assert bot["id"] not in {row["id"] for row in client.get("/api/bootstrap", headers=other).json()["actors"]}
    for endpoint in (f"/api/chats/{bot['chat_id']}", f"/api/bots/{bot['id']}/history"):
        assert client.get(endpoint, headers=other).status_code in {403, 404}
    assert client.patch(f"/api/bots/{bot['id']}", headers=other,
        json={"name": "Taken", "assignment": "Change"}).status_code == 404
    assert client.post(f"/api/chats/{bot['chat_id']}/messages", headers=other,
        json={"body": "Not my BOT", "client_request_key": uuid4().hex}).status_code == 403
    assert client.get("/api/bots/missing/history").status_code == 404
    assert client.post("/api/bots", json={"name": " ", "assignment": "x"}).status_code == 400


def test_private_tool_creates_owner_only_bot_once_and_notifies_sidebar(product, client, monkeypatch):
    from app.notifications import notifications

    context = origin(product, body="创建一个 bot 用来专门排查 CEAIA 的 bug")
    events = []
    monkeypatch.setattr(notifications, "_emit", events.append)
    args = {"name": "  CEAIA Bug Investigator  ", "assignment": "  Investigate CEAIA bugs.  "}
    operation = uuid4().hex
    bot = call(product, context, "create_personal_bot", args, operation)
    assert (bot["name"], bot["assignment"]) == ("CEAIA Bug Investigator", "Investigate CEAIA bugs.")
    assert bot["actor"]["kind"] == bot["chat"]["kind"] == "bot"
    assert bot["chat"]["bot_id"] == bot["id"]
    assert bot["chat"]["member_actor_ids"] == [bot["id"]]
    assert bot["chat"]["member_owner_ids"] == ["owner-alex"]
    hints = [event for event in events if event.get("type") == "personal_changed" and event["section"] == "chats"]
    assert hints == [{"type": "personal_changed", "owner_id": "owner-alex", "section": "chats"}]
    events.clear()
    assert call(product, context, "create_personal_bot", args, operation) == bot
    assert not events
    with pytest.raises(HTTPException) as conflict:
        call(product, context, "create_personal_bot", {**args, "name": "Changed"}, operation)
    assert conflict.value.status_code == 409
    with product.transaction() as db:
        assert db.get(ProductOperation, operation).result == bot
        assert list(db.scalars(select(Membership.owner_id).where(Membership.chat_id == bot["chat_id"]))) == ["owner-alex"]
    own = client.get("/api/bootstrap").json()
    assert [row["id"] for row in own["bots"]] == [bot["id"]]
    assert bot["chat_id"] in {row["id"] for row in own["chats"]}
    assert client.get(f"/api/bots/{bot['id']}/history").json()["messages"] == []
    other = {"x-test-owner": "owner-maya"}
    foreign = client.get("/api/bootstrap", headers=other).json()
    assert foreign["bots"] == []
    assert bot["id"] not in {row["id"] for row in foreign["actors"]}
    assert bot["chat_id"] not in {row["id"] for row in foreign["chats"]}
    assert client.get("/api/bots", headers=other).json() == []
    for path in (f"/api/chats/{bot['chat_id']}", f"/api/bots/{bot['id']}/history", f"/api/actors/{bot['id']}"):
        assert client.get(path, headers=other).status_code in {403, 404}


@pytest.mark.parametrize("name,assignment", [(" ", "Investigate bugs"), ("x" * 129, "Investigate bugs"), ("CEAIA", " ")])
def test_create_bot_tool_uses_existing_profile_validation(product, name, assignment):
    with pytest.raises(HTTPException) as denied:
        call(product, origin(product), "create_personal_bot", {"name": name, "assignment": assignment})
    assert denied.value.status_code == 400
    assert personal_bots.list_bots(product, "owner-alex") == []


@pytest.mark.parametrize("scope", ["bot", "internal", "duet"])
def test_create_bot_tool_rejects_non_private_origins(product, scope):
    if scope == "duet":
        from duet_test_support import append_duet, approved_chat, request_duet_reply
        approved_chat(product, "foreign-admin-topic", ["owner-maya", "owner-alex"])
        message = append_duet(product, "foreign-admin-topic", "owner-maya", "Create a BOT")
        request = request_duet_reply(product, "owner-maya", "foreign-admin-topic", message["id"], "twin-alex")
        context = product.get_runtime_context(request["id"])
    else:
        bot = create(product)
        if scope == "bot":
            context = origin(product, bot["chat_id"])
        else:
            room = personal_bots.create_room(product, "owner-alex", "Internal work", [bot["id"]])
            sent = product.send_message("owner-alex", room["id"], "Create a BOT", uuid4().hex,
                                        recipient_actor_ids=["twin-alex"])
            with product.transaction() as db:
                request_id = db.scalar(select(ReplyRequest.id).where(ReplyRequest.source_message_id == sent["message"]["id"]))
            context = product.get_runtime_context(request_id)
    before = personal_bots.list_bots(product, "owner-alex")
    with pytest.raises(HTTPException) as denied:
        call(product, context, "create_personal_bot", {"name": "Not permitted", "assignment": "Investigate bugs"})
    assert denied.value.status_code == 403
    assert personal_bots.list_bots(product, "owner-alex") == before


def test_main_dispatch_is_visible_independent_and_returns_once_to_original_task(product):
    bot = create(product)
    context = origin(product)
    operation = uuid4().hex
    args = {"bot_ids": [bot["id"]], "body": "Review the evidence and return findings."}
    accepted = call(product, context, "dispatch_bot_tasks", args, operation)
    assert accepted == call(product, context, "dispatch_bot_tasks", args, operation)
    assert len(accepted["request_ids"]) == 1
    request_id = accepted["request_ids"][0]
    request = product.get_request(request_id)
    assert request["chat_id"] == bot["chat_id"]
    assert request["session_id"] != product.get_request(context["request_id"])["session_id"]
    main = product.chat_detail("owner-alex", "private-alex")
    assignment = next(row for row in main["messages"] if row.get("coordination"))
    assert assignment["author_id"] == "twin-alex" and assignment["coordination"]["target_chat_id"] == bot["chat_id"]
    assert product.chat_detail("owner-alex", bot["chat_id"])["messages"][0]["body"] == args["body"]
    complete(product, request_id)
    complete(product, request_id)
    results = [row for row in product.chat_detail("owner-alex", "private-alex")["messages"]
               if (row.get("coordination") or {}).get("kind") == "result"]
    assert len(results) == 1 and results[0]["body"] == "Verified result"
    with product.transaction() as db:
        continuation, = list(db.scalars(select(ReplyRequest).where(ReplyRequest.source == "bot_result")))
        assert continuation.target_twin_id == "twin-alex"
        assert continuation.continuation_of_request_id == context["request_id"]
        assert continuation.session_id == db.get(ReplyRequest, context["request_id"]).session_id
        assert continuation.bot_handoff is None


def test_owner_dispatch_multiple_bots_is_idempotent_and_failure_is_visible(product, client):
    bots = [create(product, name) for name in ("Research", "Review")]
    payload = {"bot_ids": [bot["id"] for bot in bots], "body": "Check your assigned part", "client_request_key": uuid4().hex}
    accepted = client.post("/api/bots/dispatch", json=payload).json()
    assert accepted == client.post("/api/bots/dispatch", json=payload).json()
    assert len(accepted["request_ids"]) == 2
    assert client.post("/api/bots/dispatch", json={**payload, "body": "Different"}).status_code == 409
    product.fail_request(accepted["request_ids"][0], "Test failure")
    results = [row for row in product.chat_detail("owner-alex", "private-alex")["messages"]
               if (row.get("coordination") or {}).get("kind") == "result"]
    assert len(results) == 1 and results[0]["coordination"]["status"] == "failed"


def test_same_owner_bot_message_and_room_share_one_thread(product, client):
    first, second = create(product, "Research"), create(product, "Review")
    accepted = client.post("/api/bots/messages", json={"sender_actor_id": first["id"],
        "recipient_actor_id": second["id"], "body": "Please check this result", "client_request_key": uuid4().hex}).json()
    assert accepted["source_chat_id"] == first["chat_id"] and accepted["chat_id"] == second["chat_id"]
    complete(product, accepted["request_id"], "Checked.")
    assert product.chat_detail("owner-alex", first["chat_id"])["messages"][-1]["body"] == "Checked."
    room = client.post("/api/bot-rooms", json={"title": "Review room", "bot_ids": [first["id"], second["id"]]}).json()
    assert room["kind"] == "internal"
    assert set(room["member_actor_ids"]) == {"twin-alex", first["id"], second["id"]}
    sent = client.post(f"/api/chats/{room['id']}/messages", json={"body": "Compare findings",
        "recipient_actor_ids": room["member_actor_ids"], "client_request_key": uuid4().hex})
    assert sent.status_code == 200
    with product.transaction() as db:
        requests = list(db.scalars(select(ReplyRequest).where(ReplyRequest.chat_id == room["id"])))
        assert {row.target_twin_id for row in requests} == set(room["member_actor_ids"])
        request_ids = [row.id for row in requests]
    for request_id in request_ids:
        complete(product, request_id, "Room finding " + request_id)
    history = product.chat_detail("owner-alex", room["id"])
    assert len(history["messages"]) == 4
    assert {row["author_id"] for row in history["messages"][1:]} == set(room["member_actor_ids"])
    assert client.get(f"/api/chats/{room['id']}", headers={"x-test-owner": "owner-maya"}).status_code == 403
    assert client.post(f"/api/chats/{room['id']}/messages", json={"body": "No outside participants",
        "recipient_actor_ids": ["twin-maya"], "client_request_key": uuid4().hex}).status_code == 403


def test_bot_tools_can_message_main_and_peers_and_post_in_room(product):
    first, second = create(product, "Research"), create(product, "Review")
    context = origin(product, first["chat_id"])
    message = call(product, context, "message_personal_agent", {"recipient_actor_id": "twin-alex", "body": "A useful finding"})
    assert message["chat_id"] == "private-alex"
    message = call(product, context, "message_personal_agent", {"recipient_actor_id": second["id"], "body": "Check my finding"})
    assert message["chat_id"] == second["chat_id"]
    room = personal_bots.create_room(product, "owner-alex", "Shared work", [first["id"], second["id"]])
    post = call(product, context, "post_bot_room", {"chat_id": room["id"], "body": "Evidence ready",
        "recipient_actor_ids": [second["id"], "twin-alex"]})
    assert len(post["request_ids"]) == 2
    assert product.chat_detail("owner-alex", room["id"])["messages"][0]["author_id"] == first["id"]


def test_main_inspection_is_explicit_paged_and_retains_sources(product, client):
    bot = create(product)
    assert client.get(f"/api/bots/{bot['id']}/history").json()["messages"] == []
    with product.transaction() as db:
        chat = db.get(Chat, bot["chat_id"])
        ids = [product.append_message(db, chat, author_id="owner-alex", kind="human", body=f"Evidence {index}").id
               for index in range(4)]
    context = origin(product)
    assert not any(row["chat_id"] == bot["chat_id"] for row in context["messages"])
    page = call(product, context, "inspect_bot_history", {"bot_id": bot["id"], "limit": 2})
    assert [row["id"] for row in page["messages"]] == ids[2:] and page["has_more"]
    earlier = call(product, context, "inspect_bot_history", {"bot_id": bot["id"], "limit": 2, "before_seq": page["before_seq"]})
    assert [row["id"] for row in earlier["messages"]] == ids[:2] and not earlier["has_more"]
    with product.transaction() as db:
        manifest = db.get(ContextManifest, context["context_manifest_id"])
        scopes = [source for source in manifest.sources if source.get("chat_id") == bot["chat_id"]]
        assert scopes and scopes[0]["kind"] == "chat_context"
    assert len(product.chat_detail("owner-alex", "private-alex")["messages"]) == 1


@pytest.mark.parametrize("runtime_created", [False, True])
def test_bots_cannot_join_external_chats_topics_or_other_owner_agents(product, runtime_created):
    from duet_test_support import approved_chat
    bot = (call(product, origin(product), "create_personal_bot", {"name": "CEAIA", "assignment": "Investigate bugs"})
           if runtime_created else create(product))
    approved_chat(product, "approved-topic")
    for chat_id in ("group-team", "approved-topic", "private-maya"):
        with product.transaction() as db, pytest.raises(HTTPException) as denied:
            product.accept_request(db, db.get(Chat, chat_id), bot["id"], "owner-alex", "bot_message", uuid4().hex)
        assert denied.value.status_code == 403
    with pytest.raises(HTTPException):
        product.add_members("owner-alex", "group-team", [bot["id"]], "all")
    for sender, recipient in ((bot["id"], "twin-maya"), ("twin-maya", bot["id"])):
        with pytest.raises(HTTPException, match="same owner"):
            personal_bots.message_agent(product, "owner-alex", sender, recipient, "Blocked", uuid4().hex)
    context = origin(product, bot["chat_id"])
    for name, args in (("send_chat_message", {"chat_id": "group-team", "body": "Blocked", "source_message_ids": []}),
                       ("create_duet_topic", {"title": "Blocked", "purpose": "Blocked", "opening_message": "Blocked", "participant_owner_ids": ["owner-maya"]}),
                       ("reply_as_owner", {"chat_id": "group-team", "body": "Blocked"})):
        with pytest.raises(HTTPException) as denied:
            call(product, context, name, args)
        assert denied.value.status_code == 403
    sent = product.send_message("owner-alex", "group-team", "Still human only", uuid4().hex)
    assert sent["request"] is None
    assert product.send_message("owner-alex", "private-alex", "Main entry", uuid4().hex)["request"]["target_twin_id"] == "twin-alex"


def test_bootstrap_and_list_queries_are_batched_and_preserve_existing_chats(product):
    from duet_test_support import approved_chat
    approved_chat(product, "listed-topic")
    first = create(product)
    statements = []
    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    event.listen(service_module.engine, "before_cursor_execute", record)
    try:
        personal_bots.list_bots(product, "owner-alex")
        one_list = len(statements)
        statements.clear()
        product.bootstrap("owner-alex")
        one_bootstrap = len(statements)
        for index in range(8):
            create(product, f"BOT {index}")
        statements.clear()
        bots = personal_bots.list_bots(product, "owner-alex")
        assert len(bots) == 9 and len(statements) == one_list
        statements.clear()
        bootstrap = product.bootstrap("owner-alex")
        assert len(statements) == one_bootstrap
    finally:
        event.remove(service_module.engine, "before_cursor_execute", record)
    assert len(bootstrap["bots"]) == 9
    assert {"private-alex", "group-team", "group-alex-maya", first["chat_id"]} <= {row["id"] for row in bootstrap["chats"]}
    assert "listed-topic" not in {row["id"] for row in bootstrap["chats"]}
    from app.duet_conversations import list_conversations
    assert any(row["id"] == "listed-topic" for row in list_conversations(product, "owner-alex")["conversations"])


def test_schema_upgrade_preserves_old_rows_and_is_repeatable_with_bots(product):
    with service_module.engine.begin() as connection:
        connection.execute(text("ALTER TABLE tt_actors DROP COLUMN assignment"))
        connection.execute(text("DROP INDEX tt_owner_primary_actors"))
        connection.execute(text("ALTER TABLE tt_actors ADD UNIQUE(owner_id,kind)"))
        connection.execute(text("ALTER TABLE tt_actors DROP CONSTRAINT tt_actor_kind"))
        connection.execute(text("ALTER TABLE tt_actors ADD CONSTRAINT tt_actor_kind CHECK(kind IN ('human','twin'))"))
    upgrade_personal_bots_schema(service_module.engine)
    bot = create(product)
    upgrade_personal_bots_schema(service_module.engine)
    check_personal_bots_schema(inspect(service_module.engine))
    product.initialize(create_schema=True)
    with product.transaction() as db:
        assert db.get(Actor, "twin-alex") and db.get(Chat, "private-alex")
        assert db.get(Actor, bot["id"]).assignment == bot["assignment"]
        assert db.get(Chat, bot["chat_id"]).bot_id == bot["id"]


def test_dispatch_reads_are_batched_for_one_and_twenty_bots(product):
    bots = [create(product, f"BOT {index}") for index in range(20)]
    statements, counts = [], []
    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    event.listen(service_module.engine, "before_cursor_execute", record)
    try:
        for count in (1, 20):
            statements.clear()
            result = personal_bots.dispatch_tasks(product, "owner-alex",
                [bot["id"] for bot in bots[:count]], "Check the assigned part", uuid4().hex)
            assert len(result["request_ids"]) == count
            counts.append(len(statements))
    finally:
        event.remove(service_module.engine, "before_cursor_execute", record)
    assert counts[0] == counts[1], counts


def test_queued_cancel_returns_one_visible_handoff(product, client):
    bot = create(product)
    accepted = personal_bots.dispatch_tasks(product, "owner-alex", [bot["id"]], "Check this", uuid4().hex)
    request_id, = accepted["request_ids"]
    for _ in range(2):
        cancelled = client.post(f"/api/reply-requests/{request_id}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    results = [row for row in product.chat_detail("owner-alex", "private-alex")["messages"]
               if (row.get("coordination") or {}).get("kind") == "result"]
    assert len(results) == 1
    assert results[0]["coordination"]["status"] == "cancelled"
    assert not results[0]["coordination"]["has_result"] and results[0]["body"] == ""
    with product.transaction() as db:
        assert len(list(db.scalars(select(ReplyRequest).where(ReplyRequest.source == "bot_result")))) == 1


def test_result_does_not_reenter_primary_private_after_reset_generation_changes(product):
    bot = create(product)
    accepted = personal_bots.dispatch_tasks(product, "owner-alex", [bot["id"]], "Check this", uuid4().hex)
    with product.transaction() as db:
        db.get(Chat, "private-alex").reset_generation += 1
    complete(product, accepted["request_ids"][0])
    assert not any((row.get("coordination") or {}).get("kind") == "result"
                   for row in product.chat_detail("owner-alex", "private-alex")["messages"])
    with product.transaction() as db:
        assert not list(db.scalars(select(ReplyRequest).where(ReplyRequest.source == "bot_result")))


def test_bot_actor_profile_is_owner_only(product, client):
    bot = create(product)
    profile = client.get(f"/api/actors/{bot['id']}")
    assert profile.status_code == 200 and profile.json()["id"] == bot["id"]
    assert client.get(f"/api/actors/{bot['id']}", headers={"x-test-owner": "owner-maya"}).status_code == 404


def test_bot_tool_context_cannot_substitute_main_duet_or_omit_origin_provenance(product):
    bot = create(product)
    context = origin(product, bot["chat_id"])
    context.pop("context_manifest_id")
    context.update(target_twin_id="twin-alex", chat_id="private-alex",
                   chat={"id": "private-alex", "kind": "private"})
    with pytest.raises(HTTPException) as denied:
        call(product, context, "dispatch_bot_tasks", {"bot_ids": [bot["id"]], "body": "Forged main assignment"})
    assert denied.value.status_code == 403
