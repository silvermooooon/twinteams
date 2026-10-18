"""Bounded product regressions, isolated SQLite, no network/model/provider calls.

Run from backend: uv run --python 3.13 python checks/product_acceptance.py
This script creates only its own temporary database. It does not reset local data.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

TEMP = tempfile.TemporaryDirectory(prefix="twinteams-product-acceptance-")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(TEMP.name) / "product.db")
os.environ["TWINTEAMS_WORKSPACE_ROOT"] = str(Path(TEMP.name) / "workspace")
os.environ["TWINTEAMS_ENV"] = "local"
os.environ["TWINTEAMS_DEVELOPMENT_MODE"] = "true"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException
from sqlalchemy import event, select
from app.db import engine
from app.models import Membership, Message, ReplyLane, ReplyRequest, TriggerCandidate, now
from app.service import ProductService


def key():
    return uuid4().hex


def expect_http(code, call):
    try:
        call()
    except HTTPException as exc:
        assert exc.status_code == code, (exc.status_code, exc.detail)
    else:
        raise AssertionError(f"Expected HTTP {code}")


async def main():
    # Fresh processes verify defaults rather than this fixture's explicit opt-in.
    backend = Path(__file__).resolve().parents[1]
    def guard_environment(**overrides):
        environment = dict(os.environ)
        for variable in ("TWINTEAMS_ENV", "TWINTEAMS_DEVELOPMENT_MODE", "DATABASE_URL", "AGENT_DB_URL"):
            environment.pop(variable, None)
        environment.update(overrides)
        return environment
    def identity_guard(**overrides):
        result = subprocess.run([sys.executable, "-c", "from app.config import settings; print(settings.local_identity_enabled)"], cwd=backend, env=guard_environment(**overrides), capture_output=True, text=True, check=True)
        return result.stdout.strip()
    assert identity_guard() == "False"
    assert identity_guard(TWINTEAMS_ENV="production", TWINTEAMS_DEVELOPMENT_MODE="true") == "False"
    assert identity_guard(TWINTEAMS_ENV="local", TWINTEAMS_DEVELOPMENT_MODE="true", DATABASE_URL="postgresql://example.invalid/product") == "False"
    assert identity_guard(TWINTEAMS_ENV="local", TWINTEAMS_DEVELOPMENT_MODE="true", DATABASE_URL="sqlite:///local.db") == "True"
    for overrides in ({"TWINTEAMS_ENV": "production"}, {"DATABASE_URL": "postgresql://example.invalid/product"}, {"AGENT_DB_URL": "postgresql://example.invalid/runtime"}):
        result = subprocess.run([sys.executable, "run_local.py"], cwd=backend, env=guard_environment(**overrides), capture_output=True, text=True, timeout=5)
        assert result.returncode == 2, result.stderr
    print("PASS secure defaults and explicit local/SQLite development guards")
    s = ProductService()
    s.initialize()
    # Real cookie-session business primitives, with explicit local identities.
    token, account = s.login_local("DEV-AAA")
    assert account["owner"]["name"] == "AAA"
    assert s.authenticate_local(token) == "owner-alex"
    s.logout_local(token)
    expect_http(401, lambda: s.authenticate_local(token))
    expect_http(403, lambda: s.chat_detail("owner-alex", "private-maya"))
    print("PASS local session and private ownership")

    # Fixed seven days is a persisted join boundary, not a rolling query filter.
    seven = s.create_chat("owner-alex", ["owner-maya"], "Fixed seven days")["id"]
    old = s.send_message("owner-alex", seven, "older than seven days", key())["message"]
    recent = s.send_message("owner-alex", seven, "inside seven days", key())["message"]
    with s.transaction() as db:
        db.get(Message, old["id"]).created_at = now() - timedelta(days=8)
        db.get(Message, recent["id"]).created_at = now() - timedelta(days=6)
    s.add_members("owner-alex", seven, ["owner-jordan"], "fixed_7d")
    assert [m["id"] for m in s.chat_detail("owner-jordan", seven)["messages"]] == [recent["id"]]
    with s.transaction() as db:
        membership = db.scalar(select(Membership).where(Membership.chat_id == seven, Membership.owner_id == "owner-jordan"))
        assert membership.lower_seq == recent["seq"]
        # Changing timestamps later cannot roll the fixed sequence boundary.
        db.get(Message, recent["id"]).created_at = now() - timedelta(days=20)
    assert s.chat_detail("owner-jordan", seven)["messages"][0]["id"] == recent["id"]
    all_chat = s.create_chat("owner-alex", ["owner-maya"], "All history")["id"]
    all_old = s.send_message("owner-alex", all_chat, "all prior history", key())["message"]
    s.add_members("owner-alex", all_chat, ["owner-jordan"], "all")
    assert s.chat_detail("owner-jordan", all_chat)["messages"][0]["id"] == all_old["id"]
    print("PASS all history and fixed seven-day history")

    chat = s.create_chat("owner-alex", ["owner-maya"], "No prior and source taint")["id"]
    hidden = s.send_message("owner-alex", chat, "SECRET_OLD_HISTORY", key())["message"]
    old_topic = await s.tracking_action("create_tracking", "owner-alex", chat, {"title": "Old metadata secret", "description": "source-free metadata", "source_message_ids": []}, True)
    s.add_members("owner-alex", chat, ["owner-jordan"], "no_prior")
    fresh = s.send_message("owner-alex", chat, "Current source", key(), reply_to=hidden["id"])["message"]
    view = s.chat_detail("owner-jordan", chat)
    assert len(view["messages"]) == 1 and view["messages"][0]["reply_to"]["unavailable"]
    assert old_topic["id"] not in {t["id"] for t in view["tracking"]}
    expect_http(404, lambda: s.read("owner-jordan", chat, [hidden["id"]]))
    req = s.manual_request("owner-alex", chat, fresh["id"], "twin-alex", key())
    context = s.get_runtime_context(req["id"])
    derived = await s.runtime_tool("create_tracking", {"title": "SECRET_OLD_HISTORY", "description": "Derived hidden original", "source_message_ids": []}, {**context, "operation_id": key()})
    assert derived["id"] not in {t["id"] for t in s.chat_detail("owner-jordan", chat)["tracking"]}
    visible = await s.tracking_action("create_tracking", "owner-alex", chat, {"title": "Visible authored topic", "description": "Current", "source_message_ids": [fresh["id"]]}, True)
    await s.runtime_tool("update_tracking", {"id": visible["id"], "description": "SECRET_OLD_HISTORY"}, {**context, "operation_id": key()})
    assert visible["id"] not in {t["id"] for t in s.chat_detail("owner-jordan", chat)["tracking"]}
    # Model cannot launder source-free Tracking metadata through empty citations.
    metadata_derived = await s.runtime_tool("create_tracking", {"title": "Metadata-derived secret", "description": "Old metadata secret", "source_message_ids": []}, {**context, "operation_id": key()})
    assert metadata_derived["id"] not in {t["id"] for t in s.chat_detail("owner-jordan", chat)["tracking"]}
    await s.runtime_tool("save_summary", {"body": "SECRET_OLD_HISTORY summary", "source_message_ids": [hidden["id"], fresh["id"]]}, {**context, "operation_id": key()})
    own_request = s.manual_request("owner-jordan", chat, fresh["id"], "twin-jordan", key())
    assert s.get_runtime_context(own_request["id"])["summary"] is None
    metadata_chat = s.create_chat("owner-alex", ["owner-maya"], "Metadata-only taint")["id"]
    await s.tracking_action("create_tracking", "owner-alex", metadata_chat, {"title": "METADATA_ONLY_SECRET", "description": "No message sources exist yet", "source_message_ids": []}, True)
    s.add_members("owner-alex", metadata_chat, ["owner-jordan"], "no_prior")
    metadata_source = s.send_message("owner-alex", metadata_chat, "Current visible message", key())["message"]
    metadata_request = s.manual_request("owner-alex", metadata_chat, metadata_source["id"], "twin-alex", key())
    metadata_context = s.get_runtime_context(metadata_request["id"])
    metadata_result = await s.runtime_tool("create_tracking", {"title": "Copied metadata secret", "description": "METADATA_ONLY_SECRET", "source_message_ids": []}, {**metadata_context, "operation_id": key()})
    assert metadata_result["id"] not in {topic["id"] for topic in s.chat_detail("owner-jordan", metadata_chat)["tracking"]}
    print("PASS no-prior quote/receipt ACL and model-derived Tracking/summary taint")

    # Explicit shared uploads obey independent file time plus source permissions.
    files = s.create_chat("owner-alex", ["owner-maya"], "Workspace ACL")["id"]
    old_file = s.upload_workspace_file("owner-alex", files, "old.txt", b"before joining")
    s.add_members("owner-alex", files, ["owner-jordan"], "no_prior")
    expect_http(404, lambda: s.workspace_file("owner-jordan", files, old_file["id"]))
    new_file = s.upload_workspace_file("owner-alex", files, "new.txt", b"after joining")
    assert s.workspace_file("owner-jordan", files, new_file["id"]).read_bytes() == b"after joining"
    expect_http(400, lambda: s.upload_workspace_file("owner-alex", files, "../escape", b"blocked"))
    print("PASS workspace scope and traversal rejection")

    # Presence suppresses before acceptance; accepted requests survive later views.
    presence = s.create_chat("owner-alex", ["owner-maya"], "Presence")["id"]
    class PresenceRoutingFixture:
        configured = True
        async def route_targets(self, chat_id, message, context, owners):
            return {"needs_response": chat_id == presence, "target_owner_ids": ["owner-maya"] if chat_id == presence else [], "reason": "Explicit offline presence fixture"}
    s.bind_runtime(PresenceRoutingFixture())
    s.presence("owner-maya", "device-B", presence, True)
    watched = s.send_message("owner-alex", presence, "Watched message", key())["message"]
    await s.route_pending()
    s.presence("owner-maya", "device-B", None, False)
    s.process_candidates()
    assert not s.chat_detail("owner-alex", presence)["requests"]
    waiting = s.send_message("owner-alex", presence, "New grace window", key())["message"]
    await s.route_pending()
    s.presence("owner-maya", "device-B", presence, True)
    s.presence("owner-maya", "device-B", None, False)
    with s.transaction() as db:
        candidate = db.scalar(select(TriggerCandidate).where(TriggerCandidate.message_id == waiting["id"]))
        assert candidate.state == "suppressed"
        candidate.due_at = now() - timedelta(seconds=1)
    s.process_candidates()
    assert not s.chat_detail("owner-alex", presence)["requests"]
    accepted = s.manual_request("owner-alex", presence, watched["id"], "twin-maya", key())
    s.presence("owner-maya", "device-B", presence, True)
    assert s.get_request(accepted["id"])["status"] == "queued"
    await s.cancel_request("owner-maya", accepted["id"])
    s.bind_runtime(None)
    print("PASS durable viewing suppression and accepted-request independence")

    # Requester/owner permissions, logical idempotency and same-lane FIFO.
    queue = s.create_chat("owner-alex", ["owner-maya", "owner-jordan"], "FIFO permissions")["id"]
    q1 = s.send_message("owner-alex", queue, "first", key())["message"]
    q2 = s.send_message("owner-alex", queue, "second", key())["message"]
    r1 = s.manual_request("owner-alex", queue, q1["id"], "twin-maya", key())
    duplicate = s.manual_request("owner-alex", queue, q1["id"], "twin-maya", key())
    assert duplicate["id"] == r1["id"]
    r2 = s.manual_request("owner-alex", queue, q2["id"], "twin-maya", key())
    with s.transaction() as db:
        assert db.get(ReplyRequest, r1["id"]).accepted_seq < db.get(ReplyRequest, r2["id"]).accepted_seq
    try:
        await s.cancel_request("owner-jordan", r1["id"])
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("Unrelated member canceled a queued request")
    assert (await s.cancel_request("owner-alex", r1["id"]))["status"] == "cancelled"
    print("PASS logical idempotency, FIFO acceptance order and cancellation permissions")

    # Exact delayed-RuntimeBusy regression: a stale worker cannot resurrect R1
    # or clear the newer active R2 lane. This stub never generates model output.
    a, b = ProductService(), ProductService()
    entered, release = asyncio.Event(), asyncio.Event()
    class RuntimeBusy(Exception):
        pass
    class BusyRuntime:
        configured = True
        async def execute(self, *args):
            entered.set()
            await release.wait()
            raise RuntimeBusy("another executor owns the SDK lease")
    b.bind_runtime(BusyRuntime())
    private1 = a.send_message("owner-maya", "private-maya", "race first", key())["request"]["id"]
    private2 = a.send_message("owner-maya", "private-maya", "race second", key())["request"]["id"]
    assert private1 in a.claim_dispatches()
    assert private1 in b.claim_dispatches()
    task = asyncio.create_task(b.execute_request(private1))
    await entered.wait()
    a.finish_request(private1, "completed")
    assert private2 in a.claim_dispatches()
    release.set()
    await task
    with a.transaction() as db:
        assert db.get(ReplyRequest, private1).status == "completed"
        assert db.get(ReplyRequest, private2).status == "running"
        assert db.get(ReplyLane, ("private-maya", "twin-maya")).active_request_id == private2
    print("PASS delayed RuntimeBusy cannot overwrite newer terminal/lane state")
    # SQLite's BEGIN IMMEDIATE serializes publish against Stop across independent
    # service instances. A projection committed before Stop is withdrawn; a late
    # projection after confirmed Stop is rejected, with semantic audit retained.
    publisher, stopper = ProductService(), ProductService()
    class StopRuntime:
        async def cancel(self, request):
            return {"terminal": True}
    stopper.bind_runtime(StopRuntime())
    stop_request = publisher.send_message("owner-jordan", "private-jordan", "Stop projection fixture", key())["request"]["id"]
    publisher.claim_dispatches()
    publisher.get_runtime_context(stop_request)
    publisher.bind_runtime_run(stop_request, "fixture-session", "fixture-run")
    loaded, proceed, stop_started, stop_finished = (threading.Event() for _ in range(4))
    errors = []
    def barrier(conn, cursor, statement, parameters, context, executemany):
        if threading.current_thread().name == "projection-fixture" and "FROM tt_chats" in statement:
            loaded.set()
            if not proceed.wait(5):
                raise AssertionError("Projection fixture barrier timed out")
    def publish_fixture():
        try:
            publisher.publish_runtime_output(stop_request, "Projection fixture before Stop", "fixture-session", "fixture-run", "fixture-output")
        except BaseException as exc:
            errors.append(exc)
    def stop_fixture():
        try:
            stop_started.set()
            asyncio.run(stopper.cancel_request("owner-jordan", stop_request))
        except BaseException as exc:
            errors.append(exc)
        finally:
            stop_finished.set()
    event.listen(engine, "before_cursor_execute", barrier)
    producer = threading.Thread(target=publish_fixture, name="projection-fixture")
    consumer = threading.Thread(target=stop_fixture, name="stop-fixture")
    try:
        producer.start()
        assert loaded.wait(5)
        consumer.start()
        assert stop_started.wait(5)
        time.sleep(0.05)
        assert not stop_finished.is_set(), "Stop bypassed the outstanding SQLite transaction"
        proceed.set()
        producer.join(5)
        consumer.join(5)
        assert not producer.is_alive() and not consumer.is_alive() and not errors, errors
    finally:
        proceed.set()
        event.remove(engine, "before_cursor_execute", barrier)
    assert stopper.get_request(stop_request)["status"] == "cancelled"
    assert publisher.publish_runtime_output(stop_request, "LATE OUTPUT MUST NOT PUBLISH", "fixture-session", "fixture-run", "late-output") is None
    assert not any(message.get("request_id") == stop_request for message in stopper.chat_detail("owner-jordan", "private-jordan")["messages"])
    print("PASS concurrent Stop/projection serialization and no late publication")
    # Reply-for-me is one product message across multiple durable model outputs.
    # Output fixtures represent SDK committed semantic events; no model is invoked.
    rfm = s.send_message("owner-alex", "private-alex", "Original owner input", key(), reply_for_me=True)["request"]
    s.get_runtime_context(rfm["id"])
    first = s.publish_runtime_output(rfm["id"], "Earlier complete semantic output", "rfm-session", "rfm-run", "event-10", 10)
    resumed_context = s.get_runtime_context(rfm["id"])
    assert all(message.get("request_id") != rfm["id"] for message in resumed_context["messages"])
    latest = s.publish_runtime_output(rfm["id"], "Final processed reply", "rfm-session", "rfm-run", "event-20", 20)
    assert first == latest
    # Replayed older output and repeated current output cannot downgrade or append.
    assert s.publish_runtime_output(rfm["id"], "STALE BODY", "rfm-session", "rfm-run", "event-10", 10) == latest
    assert s.publish_runtime_output(rfm["id"], "Final processed reply", "rfm-session", "rfm-run", "event-20", 20) == latest
    s.complete_request(rfm["id"], {"session_id": "rfm-session", "run_id": "rfm-run", "body": "Final processed reply", "event_id": "event-20", "event_seq": 20})
    assert s.publish_runtime_output(rfm["id"], "LATE OLD BODY", "rfm-session", "rfm-run", "event-15", 15) == latest
    bubbles = [message for message in s.chat_detail("owner-alex", "private-alex")["messages"] if message.get("request_id") == rfm["id"]]
    assert len(bubbles) == 1 and bubbles[0]["id"] == first
    assert bubbles[0]["body"] == "Final processed reply" and bubbles[0]["raw_owner_input"] == "Original owner input"
    print("PASS single stable reply-for-me bubble with monotonic semantic projection")
    print("Product acceptance: all bounded checks passed; no provider was called.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        engine.dispose()
        TEMP.cleanup()
