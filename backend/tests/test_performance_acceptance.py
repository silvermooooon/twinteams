"""Repeatable query-count acceptance, synthetic disposable PostgreSQL only."""
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from test_chat_attachments_ui_acceptance import attachments as attachments  # noqa: PLC0414
from test_chat_attachments_ui_acceptance import send, upload

from app import service as service_module
from app.models import Chat, ChatAttachment, Message


def test_snapshot_and_bootstrap_query_growth_is_bounded(attachments, tmp_path):
    product, client = attachments
    # Include realistic dependent Twin outputs, without model calls or fixture secrets.
    humans = []
    with product.transaction() as db:
        chat = db.get(Chat, "private-alex")
        for seq in range(1, 101):
            identifier = uuid4().hex
            twin = seq % 2 == 0
            sources = humans[-10:] if twin else []
            db.add(Message(id=identifier, chat_id=chat.id, seq=seq,
                author_id="twin-alex" if twin else "owner-alex", kind="twin" if twin else "human",
                body=f"Synthetic performance message {seq}",
                source_message_ids=sources,
                source_provenance=[{"kind": "message", "id": source} for source in sources]))
            if not twin:
                humans.append(identifier)
        chat.next_message_seq = 101
    for index in range(12):
        product.create_chat("owner-alex", ["owner-maya", "owner-jordan"], f"Synthetic performance room {index}")

    samples = []
    def measure(path, action=None):
        statements = []
        def before(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.split(None, 1)[0].upper())
        event.listen(service_module.engine, "before_cursor_execute", before)
        try:
            started = time.perf_counter()
            response = action() if action else client.get(path)
            elapsed = (time.perf_counter() - started) * 1000
            if not action:
                assert response.status_code == 200, response.status_code
        finally:
            event.remove(service_module.engine, "before_cursor_execute", before)
        row = {"path": path, "queries": len(statements), "selects": statements.count("SELECT"), "elapsed_ms": round(elapsed, 2)}
        samples.append(row)
        return response if action else response.json(), row

    # Warm only service metadata/schema caches; the measured requests use new DB transactions.
    client.get("/api/chats/private-alex?limit=10")
    small, ten = measure("/api/chats/private-alex?limit=10")
    large, hundred = measure("/api/chats/private-alex?limit=100")
    bootstrap, boot = measure("/api/bootstrap")
    assert len(small["messages"]) == 10 and len(large["messages"]) == 100
    assert len(bootstrap["chats"]) >= 13
    assert [row["seq"] for row in large["messages"]] == list(range(1, 101))
    assert large["messages"][-1]["body"] == "Synthetic performance message 100"
    request = product.manual_request("owner-alex", "private-alex", humans[-1], "twin-alex", uuid4().hex)
    old_context = None
    if os.environ.get("TWINTEAMS_PERFORMANCE_COMPARE_HEAD") == "1":
        # Optional local comparison without replacing source or opening the application DB.
        import importlib.util
        import subprocess
        import sys
        source = subprocess.check_output(["git", "show", "HEAD:backend/app/service.py"], cwd=Path(__file__).resolve().parents[2])
        path = tmp_path / "service_before.py"
        path.write_bytes(source)
        spec = importlib.util.spec_from_file_location("app._performance_before", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        module.engine, module.SessionLocal, module.settings = service_module.engine, service_module.SessionLocal, service_module.settings
        old = module.ProductService()
        try:
            old_context, _ = measure("get_runtime_context:HEAD", lambda: old.get_runtime_context(request["id"]))
        finally:
            old.db_pool.shutdown()
            module.service.db_pool.shutdown()
            sys.modules.pop(spec.name, None)
    current_context, context_queries = measure("get_runtime_context:current", lambda: product.get_runtime_context(request["id"]))
    # Before deterministic maintenance, runtime exposes an explicitly incomplete
    # bounded window. Chat/history pagination remains the complete journal above.
    assert current_context["summary"] is None and current_context["context_incomplete"]
    assert len(current_context["messages"]) == 80
    assert [row["id"] for row in current_context["messages"]] == [row["id"] for row in large["messages"][-80:]]
    assert [row["seq"] for row in current_context["messages"]] == list(range(21, 101))
    if old_context:
        assert current_context["messages"] == old_context["messages"][-80:]
    report = {"disposable_pg": True, "model_calls": 0, "synthetic_messages": 100,
              "dependent_twin_messages": 50, "additional_rooms": 12, "samples": samples}
    target = Path(os.environ.get("TWINTEAMS_PERFORMANCE_DB_REPORT", "/tmp/twinteams-performance-db-report.json"))
    target.write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
    if os.environ.get("TWINTEAMS_PERFORMANCE_BASELINE") != "1":
        assert hundred["queries"] - ten["queries"] <= 10, "Adding 90 visible rows must not add per-message SQL"
        assert hundred["queries"] <= 80
        assert boot["queries"] <= 160
        # This measures the bounded initial window; the separate context tests
        # prove maintenance precedes admission and checkpoints preserve history.
        assert context_queries["queries"] <= 180


def test_batched_projection_keeps_attachment_quote_and_source_grants_owner_scoped(attachments):
    product, client = attachments
    chat = product.create_chat("owner-alex", ["owner-maya"], "Synthetic projection grants")["id"]
    shared = upload(client, chat=chat, name="shared.txt", mime="text/plain", data=b"synthetic shared attachment").json()
    source = send(client, chat=chat, body="Synthetic quoted source", ids=[shared["id"]]).json()["message"]
    private = upload(client, chat=chat, name="private.txt", mime="text/plain", data=b"synthetic owner input").json()
    with product.transaction() as db:
        db.get(ChatAttachment, private["id"]).private_owner_input = True
    with product.transaction() as db:
        output = product.append_message(db, db.get(Chat, chat), author_id="owner-alex", kind="human",
            body="Synthetic sourced output", source_message_ids=[source["id"]],
            source_provenance=[{"kind": "message", "id": source["id"]}], reply_to_message_id=source["id"],
            attachment_ids=[shared["id"], private["id"]])
        output_id = output.id
    own = product.chat_detail("owner-alex", chat)
    other = product.chat_detail("owner-maya", chat)
    own_output = next(row for row in own["messages"] if row["id"] == output_id)
    other_output = next(row for row in other["messages"] if row["id"] == output_id)
    assert {row["id"] for row in own_output["attachments"]} == {shared["id"], private["id"]}
    assert [row["id"] for row in other_output["attachments"]] == [shared["id"]]
    assert other_output["reply_to"]["body"] == "Synthetic quoted source"
    assert private["id"] not in json.dumps(other)
    product.add_members("owner-alex", chat, ["owner-jordan"], "no_prior")
    assert output_id not in {row["id"] for row in product.chat_detail("owner-jordan", chat)["messages"]}
    with product.transaction() as db:
        db.get(Message, source["id"]).state = "withdrawn"
    # Delivery is independent; the quote and original file still obey withdrawal.
    for owner in ("owner-alex", "owner-maya"):
        rows = product.chat_detail(owner, chat)["messages"]
        assert source["id"] not in {row["id"] for row in rows}
        assert next(row for row in rows if row["id"] == output_id)["reply_to"]["unavailable"]
        with pytest.raises(HTTPException):
            product.attachment_file(owner, chat, shared["id"])


def test_chat_page_does_not_revalidate_audit_ancestors(attachments):
    from app.conversation_context import message_source

    product, client = attachments
    chat_id = "private-alex"
    attachment = upload(client, chat=chat_id, name="source.txt", mime="text/plain", data=b"source").json()
    source_id = send(client, chat=chat_id, body="Evidence", ids=[attachment["id"]]).json()["message"]["id"]
    with product.transaction() as db:
        chat = db.get(Chat, chat_id)
        source = message_source(db, db.get(Message, source_id))
        derived_ids = []
        for index in range(24):
            msg = product.append_message(db, chat, kind="twin", author_id="twin-alex",
                body=f"Derived output {index}", source_provenance=[source, {
                    "kind": "history_range", "chat_id": chat_id, "lower_seq": 1,
                    "private_generation": chat.reset_generation}])
            derived_ids.append(msg.id)

    calls = []
    def before(conn, cursor, statement, parameters, context, executemany):
        calls.append(statement)
    event.listen(service_module.engine, "before_cursor_execute", before)
    try:
        rows = product.chat_detail("owner-alex", chat_id)["messages"]
    finally:
        event.remove(service_module.engine, "before_cursor_execute", before)
    assert set(derived_ids).issubset(row["id"] for row in rows)
    assert sum("FROM tt_chat_attachments" in sql for sql in calls) <= 3
    assert sum("FROM tt_memberships" in sql for sql in calls) <= 5
    with product.transaction() as db:
        db.get(ChatAttachment, attachment["id"]).sha256 = "0" * 64
    assert set(derived_ids).issubset(row["id"] for row in product.chat_detail("owner-alex", chat_id)["messages"])
    with pytest.raises(HTTPException):
        product.attachment_content("owner-alex", chat_id, attachment["id"])


def test_long_dependent_history_is_batched_without_reusing_stale_authorization(attachments):
    from checks.chat_history_fixture import seed_chat_history

    product, _ = attachments
    seeded = seed_chat_history(product, 1000)
    statements = []
    def before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(service_module.engine, "before_cursor_execute", before)
    try:
        page = product.chat_detail("owner-alex", seeded["private_chat_id"], limit=30)
        page_queries = len(statements)
        statements.clear()
        bootstrap = product.bootstrap("owner-alex")
        bootstrap_queries = len(statements)
        statements.clear()
        product.read("owner-alex", seeded["private_chat_id"], [message["id"] for message in page["messages"]])
        read_queries = len(statements)
    finally:
        event.remove(service_module.engine, "before_cursor_execute", before)
    assert len(page["messages"]) == 30
    assert page["messages"][-1]["id"] == seeded["latest_message_id"]
    assert page_queries < 100 and bootstrap_queries < 100
    assert read_queries < 30, "Read receipts must validate and persist a batch"
    preview = next(chat for chat in bootstrap["chats"] if chat["id"] == seeded["private_chat_id"])
    assert preview["last_message"] == seeded["latest_message_body"] and preview["unread_count"] == 0

    with product.transaction() as db:
        db.get(Message, f"performance-message-{seeded['first_seq']}").body = "Changed original evidence"
    refreshed = product.chat_detail("owner-alex", seeded["private_chat_id"], limit=30)
    assert [row["id"] for row in refreshed["messages"]] == [row["id"] for row in page["messages"]]
    assert refreshed["messages"][-1]["body"] == page["messages"][-1]["body"]
    product.read("owner-alex", seeded["private_chat_id"], [seeded["latest_message_id"]])
    with product.transaction() as db:
        db.get(Message, seeded["latest_message_id"]).state = "withdrawn"
    with pytest.raises(HTTPException) as denied:
        product.read("owner-alex", seeded["private_chat_id"], [seeded["latest_message_id"]])
    assert denied.value.status_code == 404


def test_presence_switch_keeps_audit_without_invalidating_chat_content(attachments):
    from sqlalchemy import func, select

    from app.models import ChangeOutbox, Presence, Receipt, ViewInterval, now

    product, _ = attachments
    ids = ["private-alex", "group-alex-maya"]
    with product.transaction() as db:
        message = product.append_message(db, db.get(Chat, ids[1]), author_id="owner-maya",
            kind="human", body="Synthetic unread message")
        message_id = message.id
    with product.transaction() as db:
        revisions = {chat.id: chat.revision for chat in db.scalars(select(Chat).where(Chat.id.in_(ids)))}
        notices = db.scalar(select(func.count()).select_from(ChangeOutbox))
    device = "presence-performance-fixture"
    product.presence("owner-alex", device, ids[0], True)
    product.presence("owner-alex", device, ids[1], True)
    product.presence("owner-alex", device, None, False)
    with product.transaction() as db:
        intervals = list(db.scalars(select(ViewInterval).where(ViewInterval.device_id == device)))
        assert {row.chat_id for row in intervals} == set(ids)
        assert all(row.ended_at <= now() for row in intervals)
        presence = db.get(Presence, ("owner-alex", device))
        assert not presence.visible and presence.focused_chat_id is None
        assert {chat.id: chat.revision for chat in db.scalars(select(Chat).where(Chat.id.in_(ids)))} == revisions
        assert db.scalar(select(func.count()).select_from(ChangeOutbox)) == notices
    product.read("owner-alex", ids[1], [message_id])
    with product.transaction() as db:
        assert db.get(Receipt, (message_id, "owner-alex"))
        assert db.get(Chat, ids[1]).revision > revisions[ids[1]]


def test_runtime_preparation_batches_history_and_rechecks_each_boundary(attachments):
    from app.models import ReplyLane
    from checks.chat_history_fixture import seed_chat_history

    product, _ = attachments
    seed = seed_chat_history(product, 1000)
    with product.transaction() as db:
        lane = db.get(ReplyLane, ("private-alex", "twin-alex"))
        if lane is None:
            lane = ReplyLane(chat_id="private-alex", twin_id="twin-alex")
            db.add(lane)
        lane.next_accept_seq = seed["completed_requests"] + 1
    request = product.send_message("owner-alex", "private-alex", "Continue the plan", uuid4().hex)["request"]
    statements = []
    def before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(service_module.engine, "before_cursor_execute", before)
    try:
        context = product.get_runtime_context(request["id"])
        assert len(context["messages"]) == 80 and context["context_incomplete"]
        assert context["messages"][-1]["body"] == "Continue the plan"
        assert len(statements) < 180
        statements.clear()
        assert product.authorize_runtime_generation(context)
        assert len(statements) < 200
        statements.clear()
        refreshed = product.get_conversation_context(context["_summary_scope"])
        assert refreshed["messages"] == context["messages"]
        assert len(statements) < 180
        statements.clear()
        prepared = product.prepare_conversation_summary(context["_summary_scope"])
        assert prepared and prepared["messages"][0]["seq"] == seed["first_seq"]
        assert len(statements) < 150
    finally:
        event.remove(service_module.engine, "before_cursor_execute", before)
    while prepared:
        product.save_conversation_summary(prepared["scope"], "Synthetic conversation checkpoint")
        prepared = product.prepare_conversation_summary(context["_summary_scope"])
    statements.clear()
    event.listen(service_module.engine, "before_cursor_execute", before)
    try:
        checkpointed = product.get_conversation_context(context["_summary_scope"])
        assert checkpointed["summary"] and not checkpointed["context_incomplete"]
        assert checkpointed["messages"][-1] == context["messages"][-1]
        assert len(statements) < 180
    finally:
        event.remove(service_module.engine, "before_cursor_execute", before)
    with product.transaction() as db:
        db.get(Message, f"performance-message-{seed['first_seq']}").body = "Original evidence corrected"
    with pytest.raises(HTTPException) as denied:
        product.authorize_runtime_generation(context)
    assert denied.value.status_code == 409
    with pytest.raises(HTTPException):
        product.get_conversation_context(context["_summary_scope"])


def test_bootstrap_batches_related_profiles_and_keeps_departed_participants(attachments):
    from app.models import Actor, Membership, Owner, now

    product, _ = attachments
    with product.transaction() as db:
        db.add(Owner(id="unrelated-owner", auth_subject="test:unrelated"))
        db.flush()
        db.add_all([Actor(id="unrelated-owner", owner_id="unrelated-owner", kind="human",
            name="Unrelated Person", initials="UP", color="#444444"),
            Actor(id="unrelated-twin", owner_id="unrelated-owner", kind="twin",
                name="Unrelated Duet", initials="UD", color="#555555")])
    assert "unrelated-owner" not in {actor["owner_id"] for actor in product.bootstrap("owner-alex")["actors"]}
    chat = product.create_chat("owner-alex", ["unrelated-owner"])
    with product.transaction() as db:
        member = db.scalar(select(Membership).where(Membership.chat_id == chat["id"],
            Membership.owner_id == "unrelated-owner"))
        member.left_at = now()
    for index in range(8):
        product.create_chat("owner-alex", ["owner-maya", "owner-jordan"], f"Related room {index}")
    statements = []
    def before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(service_module.engine, "before_cursor_execute", before)
    try:
        snapshot = product.bootstrap("owner-alex")
    finally:
        event.remove(service_module.engine, "before_cursor_execute", before)
    assert "unrelated-owner" in {actor["owner_id"] for actor in snapshot["actors"]}
    assert "unrelated-owner" not in next(row for row in snapshot["chats"] if row["id"] == chat["id"])["member_owner_ids"]
    assert next(row for row in snapshot["chats"] if row["id"] == "group-alex-maya")["title"] == "BBB"
    assert sum("tt_actors" in statement for statement in statements) == 1
    assert sum("tt_memberships" in statement for statement in statements) == 4


def test_bootstrap_and_chat_gets_are_read_only_with_default_settings(attachments):
    from app.models import ChatSetting, ScheduledTask
    from app.scheduled_tasks import system_id

    product, _ = attachments
    with product.transaction() as db:
        before = {chat.id: chat.revision for chat in db.scalars(select(Chat))}
        assert db.get(ChatSetting, ("owner-alex", "private-alex")) is None
        assert db.get(ScheduledTask, system_id("owner-alex")).schedule["seconds"] == 14400
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.upper())
    event.listen(service_module.engine, "before_cursor_execute", capture)
    try:
        for _ in range(2):
            product.bootstrap("owner-alex")
            private = product.chat_detail("owner-alex", "private-alex")
            group = product.chat_detail("owner-alex", "group-alex-maya")
    finally:
        event.remove(service_module.engine, "before_cursor_execute", capture)
    assert private["settings"] == {"reply_for_me_enabled": False, "prompt_override": None,
        "sidebar_open": True, "sidebar_tab": "activity"}
    assert group["settings"] == {**private["settings"], "sidebar_tab": "workspace"}
    assert all(sql.startswith("SELECT") and "FOR UPDATE" not in sql for sql in statements)
    with product.transaction() as db:
        assert {chat.id: chat.revision for chat in db.scalars(select(Chat))} == before
        assert db.get(ChatSetting, ("owner-alex", "private-alex")) is None
    product.patch_chat_settings("owner-alex", "private-alex", {"sidebar_open": False})
    assert product.chat_detail("owner-alex", "private-alex")["settings"]["sidebar_open"] is False


def test_explicit_owner_provision_creates_system_review_before_first_read(attachments, monkeypatch):
    from app import provision_owner
    from app.models import ScheduledTask
    from app.scheduled_tasks import system_id

    product, _ = attachments
    monkeypatch.setattr(provision_owner, "service", product)
    monkeypatch.setattr("sys.argv", ["provision_owner", "--subject", "test:provisioned",
        "--name", "Provisioned Owner", "--owner-id", "owner-provisioned"])
    provision_owner.main()
    with product.transaction() as db:
        task = db.get(ScheduledTask, system_id("owner-provisioned"))
        assert task and task.schedule["seconds"] == 14400 and task.kind == "system_review"


def test_approval_inbox_and_shared_files_use_batches(attachments):
    from app.models import Approval, ProductOperation

    product, client = attachments
    with product.transaction() as db:
        private = db.get(Chat, "private-alex")
        shared = db.get(Chat, "group-alex-maya")
        for index in range(100):
            source = product.append_message(db, private, author_id="owner-alex", kind="human", body=f"Task {index}")
            request = product.accept_request(db, private, "twin-alex", "owner-alex", "private", uuid4().hex,
                source_message_id=source.id)
            db.add(Approval(id=f"batched-approval-{index}", chat_id=private.id, approver_id="owner-alex", title=f"Approval {index}",
                description="Synthetic decision", target="fixture", impact="fixture",
                sdk_reference={"request_id": request.id}, source_message_ids=[]))
            message = product.append_message(db, shared, author_id="owner-alex", kind="human", body=f"File {index}")
            identifier = f"batched-file-{index}"
            db.add(ChatAttachment(id=identifier, chat_id=shared.id, owner_id="owner-alex", name=f"result-{index}.txt",
                mime_type="text/plain", kind="file", size=20, sha256="a" * 64,
                relative_path=f"group-alex-maya/result-{index}.txt", message_id=message.id))
            db.add(ProductOperation(operation_id=f"batched-share-{index}", chat_id=shared.id, owner_id="owner-alex",
                name="present_files", arguments_hash="b" * 64,
                result={"shared_files": [{"attachment_id": identifier, "peer_owner_ids": ["owner-maya"]}]}))
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(service_module.engine, "before_cursor_execute", capture)
    try:
        detail = product.chat_detail("owner-alex", "private-alex", limit=30)
        assert len(detail["approvals"]) == 100
        assert len(statements) < 35
        statements.clear()
        shared = client.get("/api/chats/group-alex-maya/shared-files")
        assert shared.status_code == 200
        assert len(shared.json()["files"]) == 100
        assert len(statements) < 10
    finally:
        event.remove(service_module.engine, "before_cursor_execute", capture)
