"""Regression tests for rejecting/cancelling historical and stale approval requests."""
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from test_recovery import bridge_for
from test_recovery import product as product  # noqa: PLC0414

from app.models import Approval, Chat, ReplyRequest


def key():
    return uuid4().hex


def test_legacy_and_failed_origin_approvals_can_be_rejected_without_reappearing(product):
    async def run():
        first = product.send_message("owner-alex", "private-alex", "Legacy request", key())["request"]
        failed = product.send_message("owner-alex", "private-alex", "Failed request", key())["request"]
        legacy_id = uuid4().hex
        stuck_legacy_id = uuid4().hex
        failed_id = uuid4().hex
        with product.transaction() as db:
            db.get(ReplyRequest, failed["id"]).status = "failed"
            db.add(Approval(
                id=legacy_id, chat_id="private-alex", approver_id="owner-alex",
                title="Legacy approval", description="No action payload", target="owner", impact="legacy",
                sdk_reference={"request_id": first["id"]},
            ))
            db.add(Approval(
                id=stuck_legacy_id, chat_id="private-alex", approver_id="owner-alex",
                title="Already clicked legacy approval", description="No action payload", target="owner", impact="legacy",
                status="pending",
                sdk_reference={"request_id": first["id"], "requested_decision": "rejected"},
            ))
            db.add(Approval(
                id=failed_id, chat_id="private-alex", approver_id="owner-alex",
                title="Failed origin approval", description="Origin failed", target="owner", impact="local",
                sdk_reference={
                    "request_id": failed["id"], "session_id": "failed-session", "run_id": "failed-run",
                    "action": {"tool": "bash", "arguments": {"command": "echo hi"}},
                    "execution_config_digest": "frozen-digest", "target_twin_id": "twin-alex",
                },
            ))

        inbox_ids = {item["id"] for item in product.chat_detail("owner-alex", "private-alex")["approvals"]}
        assert legacy_id in inbox_ids
        assert failed_id in inbox_ids
        assert stuck_legacy_id not in inbox_ids

        with pytest.raises(HTTPException) as exc:
            await product.decide_approval("owner-alex", legacy_id, "approved")
        assert exc.value.status_code == 409

        with pytest.raises(HTTPException) as exc:
            await product.decide_approval("owner-alex", failed_id, "approved")
        assert exc.value.status_code == 409

        legacy_result = await product.decide_approval("owner-alex", legacy_id, "rejected")
        failed_result = await product.decide_approval("owner-alex", failed_id, "rejected")
        assert legacy_result["status"] == "rejected"
        assert failed_result["status"] == "rejected"

        runtime = await bridge_for(product)
        try:
            await product.reconcile_approvals()
        finally:
            await runtime.close()

        with product.transaction() as db:
            assert db.get(Approval, legacy_id).status == "rejected"
            assert db.get(Approval, stuck_legacy_id).status == "rejected"
            assert db.get(Approval, failed_id).status == "rejected"

        remaining_ids = {item["id"] for item in product.chat_detail("owner-alex", "private-alex")["approvals"]}
        assert {legacy_id, stuck_legacy_id, failed_id}.isdisjoint(remaining_ids)

    asyncio.run(run())


def test_historical_approvals_do_not_starve_rejection_and_inbox_hides_decided_immediately(product):
    async def run():
        runtime = await bridge_for(product)
        try:
            cancelled = product.send_message("owner-alex", "private-alex", "Cancelled task", key())["request"]
            product._cancel_intent("owner-alex", cancelled["id"])
            active = product.send_message("owner-alex", "private-alex", "Active task", key())["request"]
            context = product.get_runtime_context(active["id"])
            context.update(
                operation_id="approval-reject-target",
                runtime_session_id="approval-reject-session",
                runtime_run_id="approval-reject-run",
            )
            created = await product.runtime_tool(
                "create_approval",
                {
                    "title": "Action to reject",
                    "description": "Create reminder",
                    "target": "owner",
                    "impact": "one reminder",
                    "action": {"tool": "create_reminder", "arguments": {"title": "Never create"}},
                },
                context,
            )
            with product.transaction() as db:
                base = datetime(2025, 1, 1, tzinfo=UTC)
                for index in range(60):
                    db.add(Approval(
                        id=f"hist-duet-{index}", chat_id="private-alex", approver_id="owner-alex",
                        title="Duet", description="d", target="t", impact="i", status="approved",
                        sdk_reference={"type": "duet_conversation", "request_id": active["id"]},
                        created_at=base + timedelta(seconds=index),
                    ))
                    db.add(Approval(
                        id=f"hist-reset-{index}", chat_id="private-alex", approver_id="owner-alex",
                        title="", description="", target="", impact="", status="rejected",
                        sdk_reference={},
                        created_at=base + timedelta(days=1, seconds=index),
                    ))
                    db.add(Approval(
                        id=f"hist-cancelled-{index}", chat_id="private-alex", approver_id="owner-alex",
                        title="Cancelled orphan", description="d", target="t", impact="i", status="pending",
                        sdk_reference={
                            "request_id": cancelled["id"], "session_id": f"s-{index}", "run_id": f"r-{index}",
                            "action": {"tool": "create_reminder", "arguments": {"title": "Orphan"}},
                        },
                        created_at=base + timedelta(days=2, seconds=index),
                    ))

            with product.transaction() as db:
                rev_before = db.get(Chat, "private-alex").revision
            decision = await product.decide_approval("owner-alex", created["id"], "rejected")
            assert decision["accepted"] == "business_persisted"
            with product.transaction() as db:
                assert db.get(Chat, "private-alex").revision > rev_before

            # Decided approval leaves the inbox immediately, even before worker reconciliation.
            inbox_before_reconcile = product.chat_detail("owner-alex", "private-alex")["approvals"]
            assert created["id"] not in {item["id"] for item in inbox_before_reconcile}

            # Historical rows must not starve reconciliation of the rejected approval.
            pending_ids = [row["id"] for row in product._pending_approvals()]
            assert created["id"] in pending_ids

            await product.reconcile_approvals()
            with product.transaction() as db:
                saved = db.get(Approval, created["id"])
                assert saved.status == "rejected"
                assert saved.continuation_request_id is not None
        finally:
            await runtime.close()

    asyncio.run(run())


def test_cancelling_origin_request_rejects_pending_action_approvals(product):
    async def run():
        queued = product.send_message("owner-alex", "private-alex", "Queued task", key())["request"]
        context = product.get_runtime_context(queued["id"])
        context.update(
            operation_id="approval-cancel-origin",
            runtime_session_id="approval-cancel-session",
            runtime_run_id="approval-cancel-run",
        )
        created = await product.runtime_tool(
            "create_approval",
            {
                "title": "Cancel with origin",
                "description": "Create reminder",
                "target": "owner",
                "impact": "one reminder",
                "action": {"tool": "create_reminder", "arguments": {"title": "Cancelled"}},
            },
            context,
        )
        product._cancel_intent("owner-alex", queued["id"])
        with product.transaction() as db:
            assert db.get(Approval, created["id"]).status == "rejected"
        assert created["id"] not in {
            item["id"] for item in product.chat_detail("owner-alex", "private-alex")["approvals"]
        }

    asyncio.run(run())
