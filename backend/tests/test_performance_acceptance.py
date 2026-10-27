"""Repeatable query-count acceptance, synthetic disposable PostgreSQL only."""
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import event
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
    # A fresh request rechecks grants; primed metadata never survives a transaction.
    for owner in ("owner-alex", "owner-maya"):
        assert output_id not in {row["id"] for row in product.chat_detail(owner, chat)["messages"]}
