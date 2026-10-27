"""Owner-approved Duet conversations, reusing chat execution and approval records."""
from sqlalchemy import select

from .models import (
    Approval,
    Chat,
    DispatchCommand,
    DuetContactGrant,
    DuetConversation,
    Membership,
    Message,
    Owner,
    ReplyRequest,
    RoutingDecision,
    TriggerCandidate,
    TwinFollowup,
    now,
    uid,
)
from .service import fail, iso


def lock_consent(db, owner_ids):
    # Grant changes and standing-consent admission lock the same bounded owner
    # rows in stable order, always before any conversation Chat lock.
    db.scalars(select(Owner).where(Owner.id.in_(set(owner_ids))).order_by(Owner.id).with_for_update()).all()


def approvals(db, chat_id):
    return list(db.scalars(select(Approval).where(Approval.chat_id == chat_id,
        Approval.sdk_reference["type"].as_string() == "duet_conversation")
                           .order_by(Approval.created_at, Approval.approver_id)))


def require_active(db, chat):
    if chat.kind == "group":
        fail(403, "Human conversations do not accept Duet participation")
    if chat.kind != "duet":
        return
    conversation = db.get(DuetConversation, chat.id)
    if not conversation or conversation.status != "active":
        fail(409, "Duet conversation is not approved and active")


def project(service, db, conversation, owner_id):
    chat, _ = service.membership(db, owner_id, conversation.chat_id)
    result = {"id": chat.id, "kind": "duet", "title": chat.title}
    result.update(chat_id=chat.id, origin_chat_id=conversation.origin_chat_id,
        initiating_owner_id=conversation.initiating_owner_id, purpose=conversation.purpose,
        opening_message=conversation.opening_message, status=conversation.status,
        close_reason=conversation.close_reason, causal_chain_id=conversation.causal_chain_id,
        participant_owner_ids=[m.owner_id for m in service.members(db, chat.id)],
        approvals=[service.approval_json(row) for row in approvals(db, chat.id)],
        created_at=iso(conversation.created_at), can_close=conversation.status in {"pending", "active"})
    return result


def propose(service, db, args, context):
    request, twin, runtime_origin, _, _ = service._message_tool_origin(db, context)
    require_active(db, runtime_origin)
    participants = args.get("participant_owner_ids")
    if (not isinstance(participants, list) or not 1 <= len(participants) <= 99
            or not all(isinstance(owner, str) for owner in participants)):
        fail(400, "participant_owner_ids must contain other owner IDs")
    if twin.owner_id in participants or len(set(participants)) != len(participants):
        fail(400, "Participants must be distinct other owners")
    owners = [twin.owner_id, *participants]
    for owner in owners:
        service.twin(db, owner)
    values = {}
    for name, maximum in (("title", 256), ("purpose", 30000), ("opening_message", 30000)):
        value = args.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            fail(400, f"{name} must contain 1 to {maximum} characters")
        values[name] = value.strip()
    origin_id = args.get("origin_chat_id")
    if origin_id:
        origin, _ = service.membership(db, twin.owner_id, origin_id)
        if origin.kind != "group" or not set(owners).issubset({m.owner_id for m in service.members(db, origin.id)}):
            fail(403, "Origin must be a human conversation containing every participant")
    else:
        origin = db.scalar(select(Chat).where(Chat.kind == "private", Chat.private_owner_id == twin.owner_id))
        if not origin:
            fail(409, "Owner private conversation is unavailable")
    source_ids = args.get("source_message_ids", [])
    if not isinstance(source_ids, list) or len(source_ids) > 500 or not all(isinstance(item, str) for item in source_ids):
        fail(400, "source_message_ids must be message IDs")
    # Retain citations as internal audit only. No transcript, source record, or
    # private model-input manifest is copied into the new audience's history.
    for source_id in source_ids:
        source = db.get(Message, source_id)
        if not source:
            fail(404, "Source message is unavailable")
        source_member = service.membership(db, twin.owner_id, source.chat_id)[1]
        service.visible_message(db, source_member, source_id)
    chat = Chat(kind="duet", title=values["title"])
    db.add(chat)
    db.flush()
    db.add_all([Membership(chat_id=chat.id, owner_id=owner, history_policy="all", lower_seq=1) for owner in owners])
    conversation = DuetConversation(chat_id=chat.id, origin_chat_id=origin.id,
        initiating_request_id=request.id, initiating_owner_id=twin.owner_id,
        operation_id=context["operation_id"], purpose=values["purpose"],
        opening_message=values["opening_message"], source_message_ids=source_ids,
        causal_chain_id=service.request_chain(db, request).id, status="pending")
    db.add(conversation)
    for owner in owners:
        peers = [peer for peer in owners if peer != owner]
        standing = all(db.get(DuetContactGrant, (owner, peer)) for peer in peers)
        db.add(Approval(id=uid(), chat_id=chat.id, approver_id=owner,
            title=f"Duet conversation: {chat.title}"[:256], description=conversation.purpose,
            target=", ".join(service.twin(db, peer).name for peer in peers),
            impact=conversation.opening_message, status="approved" if standing else "pending",
            source_message_ids=[], source_provenance=[],
            sdk_reference={"type": "duet_conversation", "request_id": request.id,
                "scope": "standing" if standing else "once", "peer_owner_ids": peers}))
    db.flush()
    activate(service, db, conversation)
    return {"status": conversation.status, "chat_id": chat.id,
            "conversation": project(service, db, conversation, twin.owner_id)}


def activate(service, db, conversation):
    if conversation.status != "pending":
        return
    rows = approvals(db, conversation.chat_id)
    if not rows or any(row.status != "approved" for row in rows):
        return
    conversation.status = "active"
    chat = db.get(Chat, conversation.chat_id)
    require_active(db, chat)
    opening = service.append_message(db, chat, author_id=service.twin(db, conversation.initiating_owner_id).id,
        kind="twin", body=conversation.opening_message, request_id=conversation.initiating_request_id,
        causal_chain_id=conversation.causal_chain_id,
        sdk_output_key=f"duet-opening:{conversation.chat_id}")
    service.route_committed_message(db, chat, opening)
    origin = db.get(ReplyRequest, conversation.initiating_request_id)
    origin_chat = db.get(Chat, origin.chat_id)
    if (origin_chat.kind == "private" and origin_chat.private_owner_id == conversation.initiating_owner_id
            and origin.requester_id == conversation.initiating_owner_id):
        from .followups import create
        create(service, db, origin, conversation.initiating_owner_id, chat, opening,
               service.manifest_provenance(db, origin.id))


def close(service, db, conversation, reason="owner_closed"):
    if conversation.status not in {"pending", "active"}:
        return
    conversation.status, conversation.close_reason = "closed", reason
    for row in approvals(db, conversation.chat_id):
        if row.status == "pending":
            row.status = "rejected"
    for request in db.scalars(select(ReplyRequest).where(ReplyRequest.chat_id == conversation.chat_id,
            ReplyRequest.status.in_(["queued", "running"]))):
        request.cancel_requested = True
        command = db.get(DispatchCommand, request.id)
        if command:
            command.kind, command.state = "cancel", "pending"
        if request.status == "queued":
            request.status = "cancelled"
    for followup in db.scalars(select(TwinFollowup).where(TwinFollowup.target_chat_id == conversation.chat_id)):
        if followup.status == "waiting":
            followup.status, followup.settled_at = "cancelled", now()
        followup.notification_state, followup.pause_reason = "blocked", reason
        pending = db.get(ReplyRequest, followup.continuation_request_id) if followup.continuation_request_id else None
        if pending and pending.status in {"queued", "running"}:
            pending.cancel_requested = True
            if pending.status == "queued":
                pending.status = "cancelled"
            command = db.get(DispatchCommand, pending.id)
            if command:
                command.kind, command.state = "cancel", "pending"
    for candidate in db.scalars(select(TriggerCandidate).where(TriggerCandidate.chat_id == conversation.chat_id,
            TriggerCandidate.state.in_(["waiting", "paused"]))):
        candidate.state = "suppressed"
        candidate.evidence = {**candidate.evidence, "suppression": reason}
    for decision in db.scalars(select(RoutingDecision).join(Message, Message.id == RoutingDecision.message_id)
            .where(Message.chat_id == conversation.chat_id, RoutingDecision.status.in_(["pending", "paused"]))):
        decision.status, decision.reason = "completed", reason


def decide(service, db, owner_id, approval, decision, scope):
    conversation = db.get(DuetConversation, approval.chat_id)
    if not conversation:
        fail(404, "Duet conversation not found")
    service.membership(db, owner_id, approval.chat_id, True)
    db.refresh(approval)
    db.refresh(conversation)
    if approval.approver_id != owner_id:
        fail(404, "Approval not found")
    if approval.status != "pending":
        if approval.status == decision and (decision == "rejected" or approval.sdk_reference.get("scope") == scope):
            return {**service.approval_json(approval), "accepted": "business_persisted"}
        fail(409, "This approval already has a different decision")
    if conversation.status != "pending":
        fail(409, "This Duet conversation is no longer awaiting approval")
    if scope not in {"once", "always"}:
        fail(400, "Consent scope must be once or always")
    if decision == "approved" and scope == "always":
        for peer in approval.sdk_reference["peer_owner_ids"]:
            if not db.get(DuetContactGrant, (owner_id, peer)):
                db.add(DuetContactGrant(owner_id=owner_id, peer_owner_id=peer))
    approval.status = decision
    approval.sdk_reference = {**approval.sdk_reference, "scope": scope}
    if decision == "rejected":
        close(service, db, conversation, "owner_rejected")
        conversation.status = "rejected"
    db.flush()
    activate(service, db, conversation)
    return {**service.approval_json(approval), "accepted": "business_persisted"}


def list_conversations(service, owner_id, origin_chat_id=None):
    with service.transaction() as db:
        query = select(DuetConversation).join(Membership, Membership.chat_id == DuetConversation.chat_id).where(
            Membership.owner_id == owner_id, Membership.left_at.is_(None))
        if origin_chat_id:
            origin, _ = service.membership(db, owner_id, origin_chat_id)
            if origin.kind == "group":
                query = query.where(DuetConversation.origin_chat_id == origin.id)
            elif origin.kind != "private":
                fail(400, "Origin must be a human or owner private conversation")
        return {"conversations": [project(service, db, row, owner_id)
            for row in db.scalars(query.order_by(DuetConversation.created_at.desc()))]}


def detail(service, owner_id, chat_id, limit=100, before_seq=None):
    with service.transaction() as db:
        service.membership(db, owner_id, chat_id)
        conversation = db.get(DuetConversation, chat_id)
        if not conversation:
            fail(404, "Duet conversation not found")
        metadata = project(service, db, conversation, owner_id)
    result = service.chat_detail(owner_id, chat_id, limit, before_seq)
    return {**result, "conversation": metadata, "approvals": metadata["approvals"]}


def close_conversation(service, owner_id, chat_id):
    with service.transaction() as db:
        service.membership(db, owner_id, chat_id, True)
        conversation = db.get(DuetConversation, chat_id)
        if not conversation:
            fail(404, "Duet conversation not found")
        close(service, db, conversation)
        return project(service, db, conversation, owner_id)


def authorizations(service, owner_id):
    with service.transaction() as db:
        return {"authorizations": [{"peer_owner_id": grant.peer_owner_id,
            "peer_twin_id": service.twin(db, grant.peer_owner_id).id,
            "peer_name": service.twin(db, grant.peer_owner_id).name, "created_at": iso(grant.created_at)}
            for grant in db.scalars(select(DuetContactGrant).where(DuetContactGrant.owner_id == owner_id)
                .order_by(DuetContactGrant.created_at))]}


def revoke(service, owner_id, peer_owner_id):
    with service.transaction() as db:
        lock_consent(db, [owner_id])
        grant = db.get(DuetContactGrant, (owner_id, peer_owner_id))
        if grant:
            db.delete(grant)
        ids = []
        query = select(Approval).join(DuetConversation, DuetConversation.chat_id == Approval.chat_id).where(
            Approval.approver_id == owner_id, DuetConversation.status.in_(["pending", "active"]),
            Approval.sdk_reference["type"].as_string() == "duet_conversation")
        for approval in db.scalars(query):
            if (approval.sdk_reference.get("type") == "duet_conversation"
                    and approval.sdk_reference.get("scope") in {"standing", "always"}
                    and peer_owner_id in approval.sdk_reference.get("peer_owner_ids", [])):
                conversation = db.get(DuetConversation, approval.chat_id)
                if conversation and conversation.status in {"pending", "active"}:
                    service.membership(db, owner_id, conversation.chat_id, True)
                    close(service, db, conversation, "authorization_revoked")
                    ids.append(conversation.chat_id)
        return {"revoked": True, "peer_owner_id": peer_owner_id, "closed_chat_ids": ids}
