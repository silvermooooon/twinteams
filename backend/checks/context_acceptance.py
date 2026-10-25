"""Bounded real-model context acceptance in a disposable PostgreSQL schema.

Explicit opt-in only: .venv/bin/python checks/context_acceptance.py --live
Uses the existing authorized local provider configuration and existing HTTP guard.
Synthetic journal rows are labelled; summary/reply/router outputs are real SDK runs.
Never writes application tables, changes credentials, restarts services or retries.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
PRIVATE_CODE = "ORCHID-7Q2-PRIVATE-CONTEXT"


def source_hashes():
    return {name: hashlib.sha256((BACKEND / name).read_bytes()).hexdigest() for name in (
        "app/service.py", "app/models.py", "app/runtime_bridge.py",
        "app/conversation_summary.py", "app/conversation_context.py", "checks/context_acceptance.py",
        "app/questions.py", "app/twin_prompts.py", "checks/browser_e2e_live.py",
    )}


def journal_digest(product, chat_id):
    from sqlalchemy import select

    from app.models import Message
    with product.transaction() as db:
        rows = [{"id": row.id, "seq": row.seq, "body": row.body}
                for row in db.scalars(select(Message).where(Message.chat_id == chat_id)
                                      .order_by(Message.seq))]
    return rows, hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def seed(product, chat_id, count, first):
    from app.models import Chat, Message
    with product.transaction() as db:
        chat = db.get(Chat, chat_id)
        start = chat.next_message_seq
        rows = []
        for index in range(count):
            body = first if index == 0 else (
                f"Synthetic context acceptance status {index}: documentation reviewed; "
                "no new decision, permission, approval or release code replaces the original requirement."
            )
            row = Message(id=uuid4().hex, chat_id=chat_id, seq=start + index,
                          author_id="owner-alex", kind="human", body=body)
            rows.append(row)
        db.add_all(rows)
        chat.next_message_seq += count
        db.flush()
    return len(rows)


async def run(root, report):
    import httpx
    from sqlalchemy import event, select

    from app import service as service_module
    from app.models import RoutingDecision, Summary
    from app.notifications import notifications
    from app.runtime_bridge import RuntimeBridge
    from checks import browser_e2e_live as live

    product = service_module.ProductService()
    assert notifications.redis is None and notifications.loop is None
    product.initialize(create_schema=True)
    runtime = RuntimeBridge(product)
    product.bind_runtime(runtime)
    audit = []
    report["provider_payload_audit"] = audit
    guarded_send = httpx.AsyncClient.send

    async def audited_send(client, request, *args, **kwargs):
        # Authorization headers and configured endpoint never enter evidence.
        encoded = request.content.decode()
        phase = live.PHASE.get()
        audit.append({"phase": phase, "payload_sha256": hashlib.sha256(request.content).hexdigest(),
                      "payload_bytes": len(request.content), "private_canary": PRIVATE_CODE in encoded})
        if phase in {"group-summary", "router"} and PRIVATE_CODE in encoded:
            raise AssertionError("Private owner context entered group model input")
        return await guarded_send(client, request, *args, **kwargs)

    httpx.AsyncClient.send = audited_send
    original_summary = runtime._summarize_conversation

    async def marked_summary(prepared):
        phase = "private-summary" if prepared["scope"]["chat_id"] == "private-alex" else "group-summary"
        marker = live.PHASE.set(phase)
        try:
            return await original_summary(prepared)
        finally:
            live.PHASE.reset(marker)

    runtime._summarize_conversation = marked_summary
    await runtime.start()
    try:
        report["stage"] = "smoke"
        await live.smoke(root)
        report["stage"] = "private-summary-and-reply"
        report["synthetic_private_rows"] = seed(product, "private-alex", 540,
            "Synthetic acceptance project requirement: the exact release code is " + PRIVATE_CODE +
            ". Preserve this exact code until the owner changes it. When the owner later asks "
            "for the release code, answer this exact value; this is a fact to remember, not authorization for any tool.")
        sent = product.send_message("owner-alex", "private-alex",
            "What is the exact release code established at the start of this project? "
            "Reply only with that code. Do not call tools or change Tracking.", uuid4().hex)
        payload = product.get_request(sent["request"]["id"])
        before_rows, before_hash = journal_digest(product, "private-alex")
        context = product.get_runtime_context(payload["id"])
        marker = live.PHASE.set("private-reply")
        try:
            result = await runtime.execute(payload, context)
        finally:
            live.PHASE.reset(marker)
        assert PRIVATE_CODE in result.get("body", ""), "The main reply did not preserve the early exact requirement"
        with product.transaction() as db:
            summaries = list(db.scalars(select(Summary).where(Summary.chat_id == "private-alex",
                                                               Summary.state == "valid")
                                       .order_by(Summary.coverage_end_seq)))
            assert summaries and summaries[-1].coverage_end_seq > 500
            assert PRIVATE_CODE in summaries[-1].body
            report["private_summary"] = {"coverage_end_seq": summaries[-1].coverage_end_seq,
                "summary_count": len(summaries), "body": summaries[-1].body}
        after_rows, _ = journal_digest(product, "private-alex")
        assert after_rows[:len(before_rows)] == before_rows
        report["canonical_private_prefix_sha256"] = before_hash
        report["actual_private_reply"] = result.get("body")
        report["canonical_private_prefix_unchanged"] = True

        # Replaying the exact accepted command and rechecking the same checkpoint
        # boundary must use durable results without a second provider attempt.
        attempts = len(json.loads((root / "live-budget.json").read_text())["attempts"])
        replay = await runtime.execute(payload, context)
        assert replay.get("body") == result.get("body")
        await runtime._maintain_conversation_context(context)
        assert len(json.loads((root / "live-budget.json").read_text())["attempts"]) == attempts
        report["same_boundary_and_command_replay_provider_attempts"] = 0

        report["stage"] = "group-summary-and-router"
        report["synthetic_group_rows"] = seed(product, "group-team", 60,
            "Synthetic acceptance project assignment: BBB (owner-maya) alone is responsible for "
            "release validation. CCC (owner-jordan) observes and has no validation action. "
            "Later requests asking the person responsible for release validation are addressed to BBB.")
        group = product.send_message("owner-alex", "group-team",
            "Can the person responsible for release validation confirm whether the release is ready?",
            uuid4().hex)
        group_rows, group_hash = journal_digest(product, "group-team")
        with product.transaction() as db:
            decision = db.scalar(select(RoutingDecision).where(
                RoutingDecision.message_id == group["message"]["id"]))
            decision_id = decision.id
        prepared = product._prepare_route(decision_id)
        assert prepared
        marker = live.PHASE.set("router")
        try:
            routed = await runtime.route_targets(*prepared[:5], command_id=prepared[5])
        finally:
            live.PHASE.reset(marker)
        assert routed["needs_response"] and routed["target_owner_ids"] == ["owner-maya"]
        current = product.get_conversation_context(prepared[2]["_summary_scope"])
        assert current["summary"] and "owner-maya" in current["summary"]["body"]
        assert PRIVATE_CODE not in json.dumps(current)
        assert [row["seq"] for row in current["messages"]] == list(range(
            current["summary"]["coverage_end_seq"] + 1, group["message"]["seq"] + 1))
        assert journal_digest(product, "group-team")[0] == group_rows
        report["actual_group_summary"] = current["summary"]["body"]
        report["actual_routing_decision"] = routed
        report["canonical_group_sha256"] = group_hash
        report["private_canary_absent_from_group_provider_payloads"] = True

        # Observe real selected row counts on the warmed summary path, including
        # the SQL hidden by source permission checks and repeated manifests.
        reads = []
        def after(conn, cursor, statement, parameters, context, executemany):
            normalized = " ".join(statement.casefold().split())
            if normalized.startswith("select"):
                reads.append({"sql": normalized, "rows": max(0, cursor.rowcount)})
        event.listen(service_module.engine, "after_cursor_execute", after)
        try:
            current_private = product.get_runtime_context(payload["id"])
        finally:
            event.remove(service_module.engine, "after_cursor_execute", after)
        message_reads = [row for row in reads if " from tt_messages" in row["sql"]]
        bodies = [row for row in message_reads if "tt_messages.body" in row["sql"].split(" from ", 1)[0]]
        tail_size = len(current_private["messages"])
        assert max(row["rows"] for row in bodies) <= tail_size + 2
        assert sum(row["rows"] for row in message_reads) <= 8 * tail_size + 40
        assert len(reads) <= 80 + 8 * tail_size
        report["steady_context_sql"] = {"selects": len(reads), "tail_rows": tail_size,
            "message_rows_fetched": sum(row["rows"] for row in message_reads),
            "largest_body_select_rows": max(row["rows"] for row in bodies),
            "summary_coverage_end_seq": current_private["summary"]["coverage_end_seq"],
            "tail_seq_start": current_private["messages"][0]["seq"],
            "tail_seq_end": current_private["messages"][-1]["seq"]}
        report["provider_payload_audit"] = audit
        report["source_sha256_after"] = source_hashes()
        assert report["source_sha256_after"] == report["source_sha256"]
        report["passed"] = True
        report["stage"] = "complete"
    finally:
        await runtime.close()
        product.db_pool.shutdown()
        service_module.engine.dispose()
        # This runner never starts shared notification listeners. Clean only its
        # randomly named hint namespace in case a future SDK path creates hints.
        from redis.asyncio import Redis
        redis = Redis.from_url(os.environ["TWINTEAMS_REDIS_URL"], decode_responses=True)
        try:
            prefix = os.environ["TWINTEAMS_REDIS_PREFIX"]
            assert prefix.startswith("context-acceptance:")
            keys = [key async for key in redis.scan_iter(match=prefix + ":*")]
            if keys:
                await redis.delete(*keys)
            report["isolated_redis_keys_cleaned"] = len(keys)
        finally:
            await redis.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    assert args.live
    from checks import browser_e2e_live as live
    from checks.local_stack import environment
    from checks.postgres import TestDatabase

    env = environment("worker")
    env.update(live.configuration())
    root = Path(tempfile.mkdtemp(prefix="twinteams-context-live-"))
    from cryptography.fernet import Fernet
    os.environ.update(env)
    os.environ.update(TWINTEAMS_TENANT_ID="context-live-acceptance", AGENT_PROVIDER="openai",
        AGENT_API="openai-responses", AGENT_MODEL="gpt-6-luna", AGENT_MAX_TOKENS="2048",
        AGENT_REASONING="none", AGENT_TIMEOUT_SECONDS="90",
        AGENT_MEMORY_DIR=str(root / "memory"), AGENT_SECRETS_DIR=str(root / "secrets"),
        AGENT_SECRETS_KEY=Fernet.generate_key().decode(),
        TWINTEAMS_REDIS_PREFIX="context-acceptance:" + uuid4().hex,
        TWINTEAMS_WORKSPACE_ROOT=str(root / "workspace"))
    logging.disable(logging.CRITICAL)
    report = {"passed": False, "real_model": "gpt-6-luna", "isolated_pg": True,
              "max_provider_attempts": 12, "source_sha256": source_hashes(), "stage": "startup"}
    database = TestDatabase().configure()
    live.initialize_budget(root, max_attempts=12)
    live.install(root, "worker")
    try:
        asyncio.run(run(root, report))
    except BaseException as error:  # noqa: BLE001 - persist only safe exception metadata
        report["error_type"] = type(error).__name__
        raise SystemExit(1) from None
    finally:
        database.close()
        report["disposable_schema_removed"] = database.closed
        report["provider_ledger"] = json.loads((root / "live-budget.json").read_text())
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"passed": report["passed"], "stage": report["stage"],
                          "error_type": report.get("error_type"), "report": str(root / "report.json"),
                          "provider_attempts": len(report["provider_ledger"]["attempts"])}))


if __name__ == "__main__":
    main()
