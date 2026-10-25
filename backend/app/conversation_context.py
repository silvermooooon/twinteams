"""Rebuildable conversation checkpoints; originals remain the history authority.

Only maintenance reads a bounded old prefix. Normal reads use one checkpoint and
an indexed recent window. Cumulative dependency IDs are storage-only invalidation
metadata, never model input or a reason to reload the historical prefix.
"""
from __future__ import annotations

from sqlalchemy import cast, inspect, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import load_only

from .conversation_summary import SummaryPolicy, estimate_tokens
from .models import (
    Chat,
    ChatAttachment,
    ContextManifest,
    Membership,
    Message,
    ReplyRequest,
    Summary,
)

PAGE_MESSAGES = 256
MAX_INPUT_TOKENS = 48000
MAX_SUMMARY_CHARS = 32000


def upgrade_schema(engine):
    from sqlalchemy import text
    with engine.begin() as connection:
        for name, declaration in (
            ("owner_id", "VARCHAR(80) REFERENCES tt_owners(id)"),
            ("grant_version", "INTEGER"), ("private_generation", "INTEGER")):
            connection.execute(text(f"ALTER TABLE tt_summaries ADD COLUMN IF NOT EXISTS {name} {declaration}"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS tt_summary_sources_gin ON tt_summaries USING GIN ((source_message_ids::jsonb)) WHERE state = 'valid'"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_tt_summaries_owner_id ON tt_summaries (owner_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS tt_summary_checkpoint ON tt_summaries (chat_id, owner_id, grant_version, coverage_end_seq)"))


def invalidate_changed_messages(db):
    """Called only inside the product transaction's existing before_flush hook."""
    acl_fields = ("owner_id", "chat_id", "lower_seq", "grant_version", "history_policy", "joined_at", "left_at")
    owners = {row.owner_id for row in (*db.new, *db.dirty, *db.deleted) if isinstance(row, Membership)
        and (row in db.new or row in db.deleted or any(inspect(row).attrs[key].history.has_changes() for key in acl_fields))}
    if owners:
        # Permission expansion can reveal formerly denied rows before coverage.
        # Retire cache reuse, preserving valid historical output provenance.
        db.execute(update(Summary).where(Summary.owner_id.in_(owners), Summary.state == "valid",
            Summary.grant_version.is_not(None)).values(grant_version=None),
            execution_options={"synchronize_session": False})
        for row in db.identity_map.values():
            if isinstance(row, Summary) and row.owner_id in owners:
                db.expire(row, ["grant_version"])
    fields = ("body", "raw_owner_input", "attachment_ids", "state", "source_message_ids",
              "source_floor_seq", "source_provenance", "chat_id", "seq")
    ids = [row.id for row in (*db.dirty, *db.deleted) if isinstance(row, Message)
           and (row in db.deleted or any(inspect(row).attrs[key].history.has_changes() for key in fields))]
    attachment_fields = ("sha256", "name", "mime_type", "kind", "width", "height", "private_owner_input")
    ids.extend(row.message_id for row in (*db.dirty, *db.deleted) if isinstance(row, ChatAttachment)
        and row.message_id and (row in db.deleted or any(
            inspect(row).attrs[key].history.has_changes() for key in attachment_fields)))
    if ids:
        # New checkpoints include transitive IDs. Legacy checkpoints still get
        # their original direct-source invalidation behavior.
        db.execute(update(Summary).where(Summary.state == "valid", or_(
            *(cast(Summary.source_message_ids, JSONB).contains([source_id]) for source_id in ids)
        )).values(state="stale"), execution_options={"synchronize_session": False})
        for row in db.identity_map.values():
            if isinstance(row, Summary):
                db.expire(row, ["state"])


def scope_for(service, db, supplied):
    from .service import fail
    chat, member = service.membership(db, supplied["owner_id"], supplied["chat_id"])
    if chat.kind == "private" and chat.reset_pending:
        fail(409, "Private conversation reset is pending")
    scope = {"chat_id": chat.id, "owner_id": member.owner_id,
             "history_lower_seq": member.lower_seq, "grant_version": member.grant_version,
             "private_generation": chat.reset_generation if chat.kind == "private" else None,
             "high_water_seq": min(supplied.get("high_water_seq", chat.next_message_seq - 1), chat.next_message_seq - 1),
             "exclude_request_id": supplied.get("exclude_request_id")}
    for field in ("history_lower_seq", "grant_version", "private_generation"):
        if field in supplied and supplied[field] != scope[field]:
            fail(409, "Conversation history authorization changed; retry with a fresh context")
    if supplied.get("context_manifest_id"):
        scope["context_manifest_id"] = supplied["context_manifest_id"]
    return chat, member, scope


def _query(scope, after=None):
    query = select(Message).where(Message.chat_id == scope["chat_id"],
        Message.seq >= scope["history_lower_seq"], Message.seq <= scope["high_water_seq"],
        Message.state == "published")
    if after is not None:
        query = query.where(Message.seq > after)
    if scope.get("exclude_request_id"):
        query = query.where(or_(Message.request_id.is_(None), Message.request_id != scope["exclude_request_id"]))
    return query


def summary_metadata(db, summary_id):
    return db.get(Summary, summary_id, options=[load_only(
        Summary.id, Summary.chat_id, Summary.owner_id, Summary.grant_version,
        Summary.private_generation, Summary.coverage_start_seq, Summary.coverage_end_seq,
        Summary.state, Summary.source_provenance)])


def summary_allowed(service, db, member, summary, seen=None, cache=None):
    if not summary or summary.state != "valid" or summary.chat_id != member.chat_id or summary.coverage_start_seq < member.lower_seq:
        return False
    # A checkpoint has already validated each original source; compact grant
    # requirements retain access checks without walking historical IDs again.
    return service.provenance_allowed(db, member, summary.source_provenance, seen, cache)


def latest_summary(service, db, chat, member, scope):
    candidates = db.scalars(select(Summary).options(load_only(
        Summary.id, Summary.chat_id, Summary.owner_id, Summary.grant_version,
        Summary.private_generation, Summary.body, Summary.coverage_start_seq,
        Summary.coverage_end_seq, Summary.state, Summary.source_provenance)).where(
        Summary.chat_id == chat.id, Summary.owner_id == member.owner_id,
        Summary.grant_version == member.grant_version,
        Summary.coverage_start_seq == member.lower_seq,
        Summary.private_generation == scope["private_generation"], Summary.state == "valid",
        Summary.coverage_end_seq <= scope["high_water_seq"]).order_by(Summary.coverage_end_seq.desc()).limit(8))
    for summary in candidates:
        if not summary_allowed(service, db, member, summary):
            continue
        if scope.get("exclude_request_id") and db.scalar(select(Message.id).where(
                Message.chat_id == chat.id, Message.request_id == scope["exclude_request_id"],
                Message.seq <= summary.coverage_end_seq, Message.seq >= summary.coverage_start_seq).limit(1)):
            continue
        return summary
    return None


def summary_json(summary):
    return ({"id": summary.id, "body": summary.body,
             "coverage_start_seq": summary.coverage_start_seq,
             "coverage_end_seq": summary.coverage_end_seq} if summary else None)


def message_source(db, message):
    from .service import digest
    return {"kind": "message", "id": message.id, "seq": message.seq, "hash_version": 1,
            "hash": digest([message.body, message.raw_owner_input,
                [[aid, row.sha256] for aid in (message.attachment_ids or []) if (row := db.get(ChatAttachment, aid))]])}


def _authorized_page(service, db, member, query, *, limit, descending=False, cache=None):
    """Page authorization metadata, fetching bodies only for the visible window.

    A denied-only raw page is not an omission of authorized history. Such pages
    must be skipped, otherwise a long hidden prefix can stall initial context.
    """
    cache = cache if cache is not None else {}
    authorized, cursor = [], None
    metadata = load_only(Message.id, Message.chat_id, Message.seq, Message.author_id,
        Message.state, Message.source_floor_seq, Message.source_message_ids, Message.source_provenance)
    while True:
        page = query
        if cursor is not None:
            page = page.where(Message.seq < cursor if descending else Message.seq > cursor)
        rows = list(db.scalars(page.options(metadata).order_by(
            Message.seq.desc() if descending else Message.seq).limit(PAGE_MESSAGES)))
        for row in rows:
            if service.message_sources_visible(db, member, row, cache=cache):
                authorized.append(row)
                if len(authorized) > limit:
                    return authorized[:limit], True
        if len(rows) < PAGE_MESSAGES:
            return authorized, False
        cursor = rows[-1].seq


def _load_bodies(db, rows):
    if rows:
        # Populate all deferred attributes in one bounded query, retaining order.
        list(db.scalars(select(Message).where(Message.id.in_([row.id for row in rows]))))


def read_context(service, db, chat, member, scope, cache=None):
    policy = SummaryPolicy.from_env()
    summary = latest_summary(service, db, chat, member, scope)
    after = summary.coverage_end_seq if summary else member.lower_seq - 1
    limit = min(PAGE_MESSAGES, max(80, policy.after_messages * 2, policy.keep_messages * 2))
    checked, selected, tokens = cache if cache is not None else {}, [], estimate_tokens(summary_json(summary))
    if summary:
        checked[("covering_summary", member.chat_id)] = summary.id
    authorized, incomplete = _authorized_page(service, db, member, _query(scope, after),
        limit=limit, descending=True, cache=checked)
    _load_bodies(db, authorized)
    projections = service.messages_json(db, authorized, member, cache=checked)
    selected_json = []
    for row, projected in zip(authorized, projections):
        cost = estimate_tokens(projected)
        if selected and tokens + cost > MAX_INPUT_TOKENS:
            incomplete = True
            break
        selected.append(row)
        selected_json.append(projected)
        tokens += cost
    selected_json.reverse()
    selected.reverse()
    sources = ([{"kind": "summary", "id": summary.id}] if summary else []) + [message_source(db, row) for row in selected]
    return {"summary": summary_json(summary), "messages": selected_json,
            "_summary_scope": scope, "context_incomplete": incomplete}, sources, selected


def get_context(service, supplied):
    from .service import digest, fail
    with service.transaction() as db:
        chat, member, scope = scope_for(service, db, supplied)
        checked = {}
        result, sources, _ = read_context(service, db, chat, member, scope, cache=checked)
        if chat.kind == "private" and scope.get("exclude_request_id"):
            request = db.get(ReplyRequest, scope["exclude_request_id"])
            if request and request.chat_id == chat.id:
                from .questions import pending_questions
                result["pending_questions"] = pending_questions(service, db, request, member.owner_id)
                sources.extend({"kind": "message", "id": item["message_id"], "context_role": "pending_question"}
                    for item in result["pending_questions"])
        if scope.get("context_manifest_id"):
            manifest = db.get(ContextManifest, scope["context_manifest_id"])
            if not manifest or manifest.owner_id != member.owner_id:
                fail(409, "Conversation context manifest is unavailable")
            if not service.provenance_allowed(db, member, manifest.sources, cache=checked):
                fail(409, "Previously supplied conversation context is no longer authorized")
            manifest.sources = [source for source in manifest.sources if source.get("kind") not in {"message", "summary"}] + sources
            manifest.source_digest = digest(manifest.sources)
        return result


def prepare(service, supplied):
    with service.transaction() as db:
        chat, member, scope = scope_for(service, db, supplied)
        return _prepare(service, db, chat, member, scope)


def _prepare(service, db, chat, member, scope):
    from .service import digest
    policy = SummaryPolicy.from_env()
    previous = latest_summary(service, db, chat, member, scope)
    after = previous.coverage_end_seq if previous else member.lower_seq - 1
    query = _query(scope, after)
    # A current request's published partial output may not enter its checkpoint.
    # Do not advance coverage across that gap, because a later request needs it.
    if scope.get("exclude_request_id"):
        excluded = db.scalar(select(Message.seq).where(Message.chat_id == chat.id,
            Message.request_id == scope["exclude_request_id"], Message.seq > after,
            Message.seq <= scope["high_water_seq"]).order_by(Message.seq).limit(1))
        if excluded is not None:
            query = query.where(Message.seq < excluded)
    checked = {}
    if previous:
        checked[("covering_summary", member.chat_id)] = previous.id
    rows, more = _authorized_page(service, db, member, query, limit=PAGE_MESSAGES, cache=checked)
    _load_bodies(db, rows)
    projected, bounded, tokens = [], [], estimate_tokens(summary_json(previous))
    for row, value in zip(rows, service.messages_json(db, rows, member, cache=checked)):
        cost = estimate_tokens(value)
        if bounded and tokens + cost > MAX_INPUT_TOKENS:
            more = True
            break
        bounded.append(row)
        projected.append(value)
        tokens += cost
    if not bounded:
        return None
    prefix = projected if more else policy.prefix({"summary": summary_json(previous), "messages": projected})
    if not prefix:
        return None
    end = prefix[-1]["seq"]
    selected = [row for row in bounded if row.seq <= end]
    scope = {**scope, "previous_summary_id": previous.id if previous else None,
             "coverage_end_seq": end,
             "batch_digest": digest([[message_source(db, row), row.source_message_ids, row.source_provenance, row.source_floor_seq] for row in selected])}
    return {"previous_summary": summary_json(previous), "messages": prefix, "scope": scope}


def _lineage(service, db, member, messages, previous):
    """Flatten transitive source IDs once at maintenance, with compact ACL floors."""
    from .service import digest
    ids = set(previous.source_message_ids if previous else [])
    requirements = {digest(source): source for source in (previous.source_provenance if previous else [])}
    visited = set()
    floors = {}
    def floor(chat_id, seq):
        floors[chat_id] = min(floors.get(chat_id, seq), seq)
    def sources(items, origin_id):
        for source in items or []:
            kind = source.get("kind")
            if kind == "message":
                visit(source["id"])
            elif kind == "summary":
                checkpoint = db.get(Summary, source["id"])
                if checkpoint:
                    ids.update(checkpoint.source_message_ids)
                    for requirement in checkpoint.source_provenance:
                        requirements[digest(requirement)] = requirement
            elif kind == "chat_context":
                # Even an empty origin context retains its membership/reset ACL.
                preserved = {key: value for key, value in source.items() if key != "sources"}
                origin = db.get(Chat, source["chat_id"])
                if origin and origin.kind == "private":
                    preserved["private_generation"] = origin.reset_generation
                preserved["sources"] = []
                requirements[digest(preserved)] = preserved
                sources(source.get("sources", []), source["chat_id"])
            elif kind == "history_range":
                requirements[digest(source)] = source
            else:
                preserved = dict(source)
                if kind == "tracking":
                    for source_id in source.get("source_message_ids", []):
                        visit(source_id)
                    preserved["source_message_ids"] = []
                if origin_id != member.chat_id:
                    preserved = {"kind": "chat_context", "chat_id": origin_id, "sources": [preserved]}
                requirements[digest(preserved)] = preserved
    def visit(message_id):
        if message_id in visited:
            return
        visited.add(message_id)
        row = service._message_metadata(db, message_id)
        if not row:
            return
        ids.add(row.id)
        floor(row.chat_id, min(row.seq, row.source_floor_seq if row.source_floor_seq is not None else row.seq))
        for source_id in row.source_message_ids or []:
            visit(source_id)
        sources(row.source_provenance, row.chat_id)
    for row in messages:
        visit(row.id)
    for chat_id, lower in floors.items():
        chat = db.get(Chat, chat_id)
        requirement = {"kind": "history_range", "chat_id": chat_id, "lower_seq": lower,
                       "private_generation": chat.reset_generation if chat.kind == "private" else None}
        requirements[digest(requirement)] = requirement
    # Merge previous and new floors for a stable number of chat dependencies.
    ranges = {}
    others = {}
    for key, source in requirements.items():
        if source.get("kind") == "history_range":
            existing = ranges.get(source["chat_id"])
            if not existing or source["lower_seq"] < existing["lower_seq"]:
                ranges[source["chat_id"]] = source
        else:
            others[key] = source
    return sorted(ids), [*ranges.values(), *others.values()]


def save(service, supplied, body):
    from .service import fail
    if not isinstance(body, str) or not body.strip() or len(body) > MAX_SUMMARY_CHARS:
        fail(400, "Conversation summary must be nonempty and within its output budget")
    with service.transaction() as db:
        db.scalar(select(Chat).where(Chat.id == supplied["chat_id"]).with_for_update())
        chat, member, scope = scope_for(service, db, supplied)
        stable_fields = ("chat_id", "owner_id", "history_lower_seq", "grant_version", "private_generation",
                         "previous_summary_id", "coverage_end_seq", "batch_digest")
        from .service import digest
        checkpoint_id = digest({field: supplied.get(field) for field in stable_fields})
        existing = db.get(Summary, checkpoint_id)
        if existing and existing.grant_version == member.grant_version and summary_allowed(service, db, member, existing):
            if existing.body != body.strip():
                fail(409, "This conversation checkpoint already has a different committed result")
            return summary_json(existing)
        current = latest_summary(service, db, chat, member, scope)
        if current and current.id != supplied.get("previous_summary_id"):
            previous = db.get(Summary, supplied["previous_summary_id"]) if supplied.get("previous_summary_id") else None
            if not supplied.get("previous_summary_id") or (previous and current.coverage_end_seq > previous.coverage_end_seq):
                # Another reader already made legitimate progress. Discard this
                # now obsolete model result; the caller reloads/replans normally.
                return summary_json(current)
        prepared = _prepare(service, db, chat, member, scope)
        if not prepared or any(prepared["scope"].get(key) != supplied.get(key) for key in
                ("previous_summary_id", "coverage_end_seq", "batch_digest")):
            fail(409, "Conversation history changed during summarization; retry from retained originals")
        previous = db.get(Summary, supplied["previous_summary_id"]) if supplied.get("previous_summary_id") else None
        ids = [message["id"] for message in prepared["messages"]]
        messages = [db.get(Message, message_id) for message_id in ids]
        source_ids, provenance = _lineage(service, db, member, messages, previous)
        if existing:
            # A membership change retired eligibility without revoking sources.
            # Exact freshly recomputed batch validation above permits reusing the
            # immutable checkpoint identity, including durable model replay.
            if existing.state != "valid" or not summary_allowed(service, db, member, existing) or existing.body != body.strip():
                fail(409, "This conversation checkpoint cannot be reactivated from the supplied snapshot")
            existing.grant_version = member.grant_version
            existing.source_message_ids, existing.source_provenance = source_ids, provenance
            return summary_json(existing)
        summary = Summary(id=checkpoint_id, chat_id=chat.id, owner_id=member.owner_id, grant_version=member.grant_version,
            private_generation=scope["private_generation"], body=body.strip(), source_message_ids=source_ids,
            coverage_start_seq=member.lower_seq, coverage_end_seq=supplied["coverage_end_seq"],
            source_provenance=provenance)
        db.add(summary)
        db.flush()
        return summary_json(summary)
