"""Owner-approved forum topics, administrator membership and consent."""
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select

from .models import (
    Approval,
    Chat,
    ContextManifest,
    DispatchCommand,
    DuetContactGrant,
    DuetConversation,
    Membership,
    Message,
    Owner,
    ReplyRequest,
    uid,
)
from .service import digest, fail, iso


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
        initiating_owner_id=conversation.initiating_owner_id, admin_owner_id=conversation.initiating_owner_id,
        revision=conversation.revision, purpose=conversation.purpose,
        opening_message=conversation.opening_message, status=conversation.status,
        close_reason=conversation.close_reason, causal_chain_id=conversation.causal_chain_id,
        participant_owner_ids=[m.owner_id for m in service.members(db, chat.id)],
        approvals=[service.approval_json(row) for row in approvals(db, chat.id)],
        created_at=iso(conversation.created_at), can_close=conversation.status in {"pending", "active"})
    return result


def propose(service, db, args, context):
    request, twin, runtime_origin, _, _ = service._message_tool_origin(db, context)
    require_active(db, runtime_origin)
    participants = args.get("participant_owner_ids", [])
    if (not isinstance(participants, list) or len(participants) > 99
            or not all(isinstance(owner, str) for owner in participants)):
        fail(400, "participant_owner_ids must contain other owner IDs")
    if twin.owner_id in participants or len(set(participants)) != len(participants):
        fail(400, "Participants must be distinct other owners")
    owners = [twin.owner_id, *participants]
    mentions = args.get("mention_owner_ids", [])
    if (not isinstance(mentions, list) or any(not isinstance(owner, str) for owner in mentions)
            or len(set(mentions)) != len(mentions) or not set(mentions).issubset(owners)):
        fail(400, "mention_owner_ids must contain distinct topic participant owner IDs")
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
        opening_message=values["opening_message"], opening_mention_owner_ids=mentions, source_message_ids=source_ids,
        causal_chain_id=service.request_chain(db, request).id, status="pending")
    db.add(conversation)
    for owner in owners:
        peers = [peer for peer in owners if peer != owner]
        standing = all(db.get(DuetContactGrant, (owner, peer)) for peer in peers)
        db.add(Approval(id=uid(), chat_id=chat.id, approver_id=owner,
            title=f"Duet topic: {chat.title}"[:256], description=conversation.purpose,
            target=", ".join(service.twin(db, peer).name for peer in peers),
            impact=conversation.opening_message, status="approved" if standing else "pending",
            source_message_ids=[], source_provenance=[],
            sdk_reference={"type": "duet_conversation", "request_id": request.id,
                "scope": "standing" if standing else "once", "peer_owner_ids": peers}))
    db.flush()
    activate(service, db, conversation)
    return {"status": conversation.status, "chat_id": chat.id,
            "conversation": project(service, db, conversation, twin.owner_id)}


def activate(service, db, conversation, *, resume=False):
    if conversation.status != "pending":
        return
    rows = [row for row in approvals(db, conversation.chat_id) if not row.sdk_reference.get("invitation_key")]
    if not rows or any(row.status != "approved" for row in rows):
        return
    conversation.status = "active"
    chat = db.get(Chat, conversation.chat_id)
    require_active(db, chat)
    opening = service.append_message(db, chat, author_id=service.twin(db, conversation.initiating_owner_id).id,
        kind="twin", body=conversation.opening_message, mention_owner_ids=conversation.opening_mention_owner_ids,
        request_id=conversation.initiating_request_id,
        causal_chain_id=conversation.causal_chain_id,
        sdk_output_key=f"duet-opening:{conversation.chat_id}")
    if resume:
        return resume_initiator(service, db, conversation, opening)


def resume_initiator(service, db, conversation, opening):
    origin = db.get(ReplyRequest, conversation.initiating_request_id)
    if not origin or origin.cancel_requested or origin.status == "cancelled":
        return None
    chat = db.get(Chat, origin.chat_id)
    if not chat or chat.kind != "private" or chat.private_owner_id != conversation.initiating_owner_id:
        return None
    try:
        service._check_private_request(db, origin)
        _, member = service.membership(db, conversation.initiating_owner_id, chat.id, True)
    except HTTPException:
        return None
    sources = service.manifest_provenance(db, origin.id)
    if not service.provenance_allowed(db, member, sources):
        return None
    if origin.source_message_id and not service.visible_message(db, member, origin.source_message_id, False):
        return None
    key = "duet-activation:" + conversation.chat_id
    state = "The opening post was published. The administrator and explicitly mentioned participants were notified."
    request = service.accept_request(db, chat, origin.target_twin_id, origin.requester_id,
        "approval_result", key, source_message_id=origin.source_message_id,
        continuation_of_request_id=origin.id, intent_key=key, causal_chain_id=origin.causal_chain_id,
        source_owner_id=origin.source_owner_id,
        input_body=f"All owners approved Duet topic {conversation.chat_id}. {state} "
            "This approves the connection only; the original task's authorization and file-sharing permissions are unchanged. "
            "Continue the remaining work authorized in the original task. Do not resend the opening or poll for approval.")
    request.execution_config = deepcopy(origin.execution_config)
    db.add(ContextManifest(request_id=request.id, owner_id=conversation.initiating_owner_id,
        grant_version=member.grant_version, high_water_seq=chat.next_message_seq - 1,
        sources=sources, source_digest=digest(sources)))
    return chat.id


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
    for message in db.scalars(select(Message).where(Message.chat_id == conversation.chat_id,
            Message.state == "pending")):
        message.state = "withdrawn"


def decide(service, db, owner_id, approval, decision, scope):
    conversation = db.get(DuetConversation, approval.chat_id)
    if not conversation:
        fail(404, "Duet conversation not found")
    if approval.sdk_reference.get("invitation_key"):
        return decide_invitation(service, db, owner_id, approval, conversation, decision, scope)
    service.membership(db, owner_id, approval.chat_id, True)
    db.refresh(approval)
    db.refresh(conversation)
    if approval.approver_id != owner_id:
        fail(404, "Approval not found")
    if approval.status != "pending":
        if approval.status == decision and (decision == "rejected" or approval.sdk_reference.get("scope") == scope):
            return {**service.approval_json(approval), "accepted": "business_persisted"}, None
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
    wake_chat_id = activate(service, db, conversation, resume=True)
    return {**service.approval_json(approval), "accepted": "business_persisted"}, wake_chat_id


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
        chat, member = service.membership(db, owner_id, chat_id)
        conversation = db.get(DuetConversation, chat_id)
        if not conversation:
            fail(404, "Duet conversation not found")
        metadata = project(service, db, conversation, owner_id)
        from .duet_messages import view
        sends = view(service, db, chat, member)
    result = service.chat_detail(owner_id, chat_id, limit, before_seq)
    return {**result, **sends, "conversation": metadata, "approvals": metadata["approvals"]}


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
        for approval in db.scalars(query.order_by(Approval.chat_id)):
            if (approval.sdk_reference.get("type") == "duet_conversation"
                    and approval.sdk_reference.get("scope") in {"standing", "always"}
                    and peer_owner_id in approval.sdk_reference.get("peer_owner_ids", [])):
                conversation = db.get(DuetConversation, approval.chat_id)
                invitation_key = approval.sdk_reference.get("invitation_key")
                if invitation_key and not approval.sdk_reference.get("admitted"):
                    changed = False
                    for row in approvals(db, approval.chat_id):
                        if row.sdk_reference.get("invitation_key") == invitation_key and row.status == "pending":
                            row.status = "rejected"
                            changed = True
                    if changed and conversation.status == "active":
                        conversation.revision += 1
                        from .duet_messages import notify_update
                        notify_update(service, db, db.get(Chat, conversation.chat_id),
                            conversation.initiating_owner_id, "invitation_revoked", invitation_key, 1)
                    continue
                if conversation and conversation.status in {"pending", "active"}:
                    service.membership(db, owner_id, conversation.chat_id, True)
                    close(service, db, conversation, "authorization_revoked")
                    ids.append(conversation.chat_id)
        return {"revoked": True, "peer_owner_id": peer_owner_id, "closed_chat_ids": ids}


def invitation_visible(approval, conversation, owner_id):
    return bool(conversation and conversation.status == "active"
        and approval.approver_id == owner_id and approval.status == "pending"
        and approval.sdk_reference.get("type") == "duet_conversation"
        and approval.sdk_reference.get("invitation_key")
        and owner_id in approval.sdk_reference.get("participant_owner_ids", []))


def invite(service, db, args, context):
    request, twin, _, _, _ = service._message_tool_origin(db, context)
    chat, _ = service.membership(db, twin.owner_id, args.get("chat_id"), True)
    require_active(db, chat)
    topic = db.get(DuetConversation, chat.id)
    if not topic or topic.initiating_owner_id != twin.owner_id:
        fail(403, "Only the topic administrator can invite Duets")
    current = [member.owner_id for member in service.members(db, chat.id)]
    invited = args.get("participant_owner_ids")
    if (not isinstance(invited, list) or not invited or len(current) + len(invited) > 100
            or any(not isinstance(owner, str) for owner in invited)
            or len(set(invited)) != len(invited) or set(invited).intersection(current)):
        fail(400, "participant_owner_ids must contain distinct new participant owner IDs")
    for owner in invited:
        service.twin(db, owner)
    requirements = args.get("requirements")
    if not isinstance(requirements, str) or not requirements.strip():
        fail(400, "Invitation requirements must contain text")
    proposed = [*current, *invited]
    mentions = args.get("mention_owner_ids", [])
    if (not isinstance(mentions, list) or any(not isinstance(owner, str) for owner in mentions)
            or len(set(mentions)) != len(mentions) or not set(mentions).issubset(proposed)):
        fail(400, "mention_owner_ids must contain distinct proposed participant owner IDs")
    if any(row.status == "pending" and row.sdk_reference.get("invitation_key") for row in approvals(db, chat.id)):
        fail(409, "Resolve the pending participant invitation before inviting more Duets")
    invitation_key = uid()
    for owner in proposed:
        peers = [peer for peer in (proposed if owner in invited else invited) if peer != owner]
        standing = all(db.get(DuetContactGrant, (owner, peer)) for peer in peers)
        db.add(Approval(id=uid(), chat_id=chat.id, approver_id=owner,
            title=f"Invite Duets to topic: {chat.title}"[:256],
            description="Approve the new topic audience, including access to its existing posts. Files retain their original sharing audience.",
            target=", ".join(service.twin(db, peer).name for peer in peers),
            impact=requirements.strip(), status="approved" if standing else "pending",
            source_message_ids=[], source_provenance=[],
            sdk_reference={"type": "duet_conversation", "request_id": request.id,
                "scope": "standing" if standing else "once", "peer_owner_ids": peers,
                "invitation_key": invitation_key, "invited_owner_ids": invited,
                "participant_owner_ids": proposed, "prior_owner_ids": current,
                "requirements": requirements.strip(), "mention_owner_ids": mentions}))
    db.flush()
    activated = activate_invitation(service, db, topic, invitation_key)
    if not activated:
        topic.revision += 1
        from .duet_messages import notify_update
        notify_update(service, db, chat, twin.owner_id, "invitation_pending", invitation_key, 1)
    return {"status": "active" if activated else "pending", "chat_id": chat.id,
        "invitation_key": invitation_key, "invited_owner_ids": invited,
        "conversation": project(service, db, topic, twin.owner_id)}


def activate_invitation(service, db, topic, invitation_key):
    rows = [row for row in approvals(db, topic.chat_id) if row.sdk_reference.get("invitation_key") == invitation_key]
    if not rows or any(row.status != "approved" for row in rows):
        return False
    reference = rows[0].sdk_reference
    if reference.get("admitted"):
        return True
    current = {member.owner_id for member in service.members(db, topic.chat_id)}
    if current != set(reference["prior_owner_ids"]):
        fail(409, "Topic audience changed; prepare a new invitation")
    chat = db.get(Chat, topic.chat_id)
    require_active(db, chat)
    db.add_all([Membership(chat_id=chat.id, owner_id=owner, history_policy="all", lower_seq=1)
        for owner in reference["invited_owner_ids"]])
    for row in rows:
        row.sdk_reference = {**row.sdk_reference, "admitted": True}
    topic.revision += 1
    db.flush()
    service.append_message(db, chat, author_id=service.twin(db, topic.initiating_owner_id).id,
        kind="twin", body=reference["requirements"], mention_owner_ids=reference["mention_owner_ids"],
        request_id=reference["request_id"], causal_chain_id=topic.causal_chain_id,
        sdk_output_key=f"duet-invitation:{invitation_key}")
    return True


def decide_invitation(service, db, owner_id, approval, topic, decision, scope):
    db.refresh(approval)
    db.refresh(topic)
    if approval.approver_id != owner_id:
        fail(404, "Approval not found")
    service.membership(db, topic.initiating_owner_id, topic.chat_id, True)
    if approval.status != "pending":
        if approval.status == decision and (decision == "rejected" or approval.sdk_reference.get("scope") == scope):
            return {**service.approval_json(approval), "accepted": "business_persisted"}, None
        fail(409, "This invitation already has a different decision")
    if not invitation_visible(approval, topic, owner_id):
        fail(409, "This invitation is no longer awaiting approval")
    if scope not in {"once", "always"}:
        fail(400, "Consent scope must be once or always")
    reference = approval.sdk_reference
    if decision == "approved" and scope == "always":
        for peer in reference["peer_owner_ids"]:
            if not db.get(DuetContactGrant, (owner_id, peer)):
                db.add(DuetContactGrant(owner_id=owner_id, peer_owner_id=peer))
    approval.status = decision
    approval.sdk_reference = {**reference, "scope": scope}
    if decision == "rejected":
        for row in approvals(db, topic.chat_id):
            if row.sdk_reference.get("invitation_key") == reference["invitation_key"] and row.status == "pending":
                row.status = "rejected"
    db.flush()
    admitted = activate_invitation(service, db, topic, reference["invitation_key"])
    if not admitted:
        topic.revision += 1
        from .duet_messages import notify_update
        # The invitation approver may not be a member until admission completes.
        notify_update(service, db, db.get(Chat, topic.chat_id), topic.initiating_owner_id,
            "invitation_decided", approval.id, 1)
    return {**service.approval_json(approval), "accepted": "business_persisted"}, topic.chat_id


def update_topic(service, db, args, context):
    _, twin, _, _, _ = service._message_tool_origin(db, context)
    chat, _ = service.membership(db, twin.owner_id, args.get("chat_id"), True)
    require_active(db, chat)
    topic = db.get(DuetConversation, chat.id)
    if not topic or topic.initiating_owner_id != twin.owner_id:
        fail(403, "Only the topic administrator can update the topic")
    expected = args.get("expected_revision")
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
        fail(400, "expected_revision must be a positive integer")
    if expected != topic.revision:
        fail(409, "Topic changed; read it before updating again")
    values = {}
    for name, maximum in (("title", 256), ("purpose", 30000)):
        if name not in args:
            continue
        value = args[name]
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            fail(400, f"{name} must contain 1 to {maximum} characters")
        values[name] = value.strip()
    if not values:
        fail(400, "Supply a title or purpose to update")
    if all(value == (chat.title if name == "title" else topic.purpose) for name, value in values.items()):
        return {**project(service, db, topic, twin.owner_id), "changed": False}
    if "title" in values:
        chat.title = values["title"]
    if "purpose" in values:
        topic.purpose = values["purpose"]
    topic.revision += 1
    from .duet_messages import notify_update
    notify_update(service, db, chat, twin.owner_id, "topic_updated", chat.id, topic.revision)
    return {**project(service, db, topic, twin.owner_id), "changed": True}


def close_topic(service, db, args, context):
    _, twin, _, _, _ = service._message_tool_origin(db, context)
    chat, _ = service.membership(db, twin.owner_id, args.get("chat_id"), True)
    topic = db.get(DuetConversation, chat.id)
    if not topic or topic.initiating_owner_id != twin.owner_id:
        fail(403, "Only the topic administrator can close the topic through Duet tools")
    close(service, db, topic)
    return project(service, db, topic, twin.owner_id)
