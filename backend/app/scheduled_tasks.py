"""Owner schedules: PostgreSQL intent, disposable Redis due index, existing FIFO execution."""
import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from .config import settings
from .models import (
    Approval,
    Chat,
    Owner,
    PendingChildResult,
    ReplyRequest,
    ScheduledTask,
    TwinFollowup,
    now,
    uid,
)
from .notifications import notifications

SYSTEM_INSTRUCTIONS = ("Review my current commitments, pending work, approvals, delegated tasks and time-sensitive "
    "items using current evidence and my memory. Follow up when useful. Notify me only about meaningful new "
    "progress, a material change, a decision I must make, or an approaching deadline. Stay silent otherwise. "
    "Do not repeat an earlier notice without a material change. Do not create or change scheduled tasks.")
TOOLS = {"create_scheduled_task", "list_scheduled_tasks", "update_scheduled_task", "cancel_scheduled_task"}
MUTATIONS = TOOLS - {"list_scheduled_tasks"}


def fail(code, message):
    raise HTTPException(code, message)


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def iso(value):
    return aware(value).isoformat() if value is not None else None


def local_instant(value, zone):
    """A gap moves forward by the DST gap; an ambiguous wall clock uses its first fold."""
    first = value.replace(tzinfo=zone, fold=0).astimezone(UTC)
    return first  # zoneinfo's fold=0 maps a gap forward on a round trip.


def validate(schedule, timezone, *, moment=None):
    if not isinstance(timezone, str):
        fail(422, "timezone must be an IANA timezone")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        fail(422, "Unknown IANA timezone")
    if not isinstance(schedule, dict):
        fail(422, "schedule must be an object")
    kind = schedule.get("kind")
    required = {"once": {"kind", "at"}, "daily": {"kind", "time"},
                "weekly": {"kind", "time", "weekdays"}, "interval": {"kind", "seconds"}}
    if kind not in required or set(schedule) != required[kind]:
        fail(422, "Invalid schedule fields")
    value = dict(schedule)
    if kind == "once":
        try:
            at = datetime.fromisoformat(value["at"])
            if at.tzinfo is None:
                at = local_instant(at, zone)
            value["at"] = iso(at)
        except (TypeError, ValueError):
            fail(422, "once.at must be an ISO date and time")
        if moment is not None and at <= moment:
            fail(422, "One-time execution must be in the future")
    elif kind == "interval":
        seconds = value["seconds"]
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not settings.scheduled_task_min_interval_seconds <= seconds <= 31536000:
            fail(422, f"Interval must be {settings.scheduled_task_min_interval_seconds} to 31536000 seconds")
    else:
        import re
        if not isinstance(value["time"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value["time"]):
            fail(422, "time must be HH:MM")
        if kind == "weekly":
            days = value["weekdays"]
            if not isinstance(days, list) or not days or any(isinstance(day, bool) or not isinstance(day, int) or day not in range(7) for day in days):
                fail(422, "weekdays must contain Monday=0 through Sunday=6")
            value["weekdays"] = sorted(set(days))
    return value


def next_run(schedule, timezone, after, *, anchor=None):
    """Strictly after a UTC instant; missed recurring occurrences coalesce into one."""
    after = aware(after)
    kind = schedule["kind"]
    if kind == "once":
        at = aware(datetime.fromisoformat(schedule["at"]))
        return at if at > after else None
    if kind == "interval":
        step = timedelta(seconds=schedule["seconds"])
        if anchor is None:
            return after + step
        anchor = aware(anchor)
        return anchor + max(1, int((after - anchor) // step) + 1) * step
    zone = ZoneInfo(timezone)
    day = after.astimezone(zone).date()
    hour, minute = map(int, schedule["time"].split(":"))
    for offset in range(9):
        candidate_day = day + timedelta(days=offset)
        if kind == "weekly" and candidate_day.weekday() not in schedule["weekdays"]:
            continue
        candidate = local_instant(datetime.combine(candidate_day, datetime.min.time()).replace(hour=hour, minute=minute), zone)
        if candidate > after:
            return candidate
    raise ValueError("No next schedule occurrence")


def snapshot(request):
    if request is None:
        return None
    config = request.execution_config or {}
    if config.get("scheduled_task"):
        return config["scheduled_task"]
    if request.source in {"scheduled_task", "system_review"}:
        try:
            return json.loads(request.input_body)
        except (TypeError, ValueError):
            fail(409, "Scheduled request snapshot is unavailable")
    return None


def system_id(owner_id):
    return "system-review:" + hashlib.sha256(owner_id.encode()).hexdigest()[:40]


def ensure_system(service, db, owner_id, moment=None):
    owner = db.scalar(select(Owner).where(Owner.id == owner_id).with_for_update())
    if not owner:
        fail(401, "Unknown owner identity")
    row = db.get(ScheduledTask, system_id(owner_id))
    if row:
        return row
    moment = moment or now()
    # Stable per-owner stagger avoids a four-hour thundering herd after startup.
    offset = int(hashlib.sha256(owner_id.encode()).hexdigest()[:8], 16) % 14400
    due = datetime.fromtimestamp((int(moment.timestamp()) // 14400) * 14400 + offset, UTC)
    if due <= moment:
        due += timedelta(seconds=14400)
    chat = db.scalar(select(Chat).where(Chat.private_owner_id == owner_id))
    row = ScheduledTask(id=system_id(owner_id), owner_id=owner_id, kind="system_review",
        title="System review", instructions="", timezone="UTC",
        schedule={"kind": "interval", "seconds": 14400}, enabled=True, revision=1,
        next_run_at=due, details={"private_generation": chat.reset_generation if chat else 0},
        created_at=moment, updated_at=moment)
    db.add(row)
    db.flush()
    return row


def owned(db, owner_id, task_id, *, lock=True):
    query = select(ScheduledTask).where(ScheduledTask.id == task_id, ScheduledTask.owner_id == owner_id)
    row = db.scalar(query)
    if not row or (row.details or {}).get("deleted"):
        fail(404, "Scheduled task not found")
    return row


def busy(db, row):
    active_id = (row.details or {}).get("active_request_id")
    request = db.get(ReplyRequest, active_id) if active_id else None
    if not request:
        return False
    chain = request.causal_chain_id
    if db.scalar(select(ReplyRequest.id).where(ReplyRequest.causal_chain_id == chain, ReplyRequest.status.in_(["queued", "running"])).limit(1)):
        return True
    if db.scalar(select(PendingChildResult.id).where(PendingChildResult.causal_chain_id == chain, PendingChildResult.state.in_(["pending", "paused"])).limit(1)):
        return True
    if db.scalar(select(TwinFollowup.id).where(TwinFollowup.causal_chain_id == chain, TwinFollowup.notification_state.in_(["waiting", "pending", "paused"])).limit(1)):
        return True
    requests = select(ReplyRequest.id).where(ReplyRequest.causal_chain_id == chain)
    from .credential_models import CredentialRequest
    if db.scalar(select(CredentialRequest.id).where(CredentialRequest.origin_request_id.in_(requests), CredentialRequest.status == "pending").limit(1)):
        return True
    request_ids = set(db.scalars(requests))
    # Approval SDK references are JSON; keep the bounded inspection inside the owner's private chat.
    for approval in db.scalars(select(Approval).where(Approval.chat_id == request.chat_id, Approval.status == "pending").limit(100)):
        if approval.sdk_reference.get("request_id") in request_ids:
            return True
    return False


def payload(db, row):
    details = row.details or {}
    request = db.get(ReplyRequest, details.get("active_request_id")) if details.get("active_request_id") else None
    active = db.scalar(select(ReplyRequest).where(ReplyRequest.causal_chain_id == request.causal_chain_id,
        ReplyRequest.status.in_(["queued", "running"]), ReplyRequest.target_twin_id == request.target_twin_id,
        ReplyRequest.chat_id == request.chat_id).order_by(ReplyRequest.accepted_at.desc()).limit(1)) if request else None
    pending = busy(db, row) if request else False
    return {"id": row.id, "kind": row.kind, "title": row.title, "instructions": SYSTEM_INSTRUCTIONS if row.kind == "system_review" else row.instructions,
        "timezone": row.timezone, "schedule": row.schedule, "enabled": row.enabled,
        "revision": row.revision, "next_run_at": iso(row.next_run_at),
        "last_run_at": details.get("last_run_at"), "last_request_id": details.get("last_request_id"),
        "active_request_id": active.id if active else None, "pending_work": pending,
        "last_status": active.status if active else details.get("last_status") or (request.status if request else None),
        "last_error": details.get("last_error", request.error if request else None),
        "created_at": iso(row.created_at), "updated_at": iso(row.updated_at)}


def mutate(service, db, owner_id, task_id=None, values=None, *, delete=False):
    values = values or {}
    chat = db.scalar(select(Chat).where(Chat.private_owner_id == owner_id).with_for_update())
    if not chat or chat.reset_pending:
        fail(409, "Private Twin is unavailable while resetting")
    ensure_system(service, db, owner_id)
    moment = now()
    if any(value is None for value in values.values()):
        fail(422, "Scheduled task fields cannot be null")
    if set(values) - {"title", "instructions", "timezone", "schedule", "enabled"}:
        fail(422, "Invalid scheduled task fields")
    if task_id is None:
        if db.scalar(select(func.count()).select_from(ScheduledTask).where(ScheduledTask.owner_id == owner_id, ScheduledTask.kind == "user", ScheduledTask.details["deleted"].as_boolean().is_not(True))) >= settings.scheduled_task_max_per_owner:
            fail(422, "Scheduled task limit reached")
        if set(values) - {"title", "instructions", "timezone", "schedule", "enabled"}:
            fail(422, "Invalid scheduled task fields")
        row = ScheduledTask(id=uid(), owner_id=owner_id, kind="user", revision=1, details={"private_generation": chat.reset_generation}, created_at=moment)
        db.add(row)
    else:
        row = owned(db, owner_id, task_id)
        if row.kind == "system_review" and (delete or set(values) - {"enabled", "schedule"} or "schedule" in values and (not isinstance(values["schedule"], dict) or values["schedule"].get("kind") != "interval")):
            fail(422, "System review allows only pause and interval changes")
        row.revision += 1
    if delete and busy(db, row):
        fail(409, "Stop the current request or wait for its unresolved work before deleting this task")
    if delete:
        row.enabled, row.next_run_at, row.details = False, None, {**(row.details or {}), "deleted": True}
    else:
        for key, maximum in (("title", 256), ("instructions", 20000)):
            value = values.get(key, SYSTEM_INSTRUCTIONS if key == "instructions" and row.kind == "system_review" else getattr(row, key, None))
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                fail(422, f"{key} must contain 1 to {maximum} characters")
            if row.kind != "system_review" or key != "instructions":
                setattr(row, key, value.strip())
        timezone = values.get("timezone", row.timezone or "UTC")
        schedule = validate(values.get("schedule", row.schedule), timezone, moment=moment if "schedule" in values and values["schedule"].get("kind") == "once" else None)
        enabled = values.get("enabled", row.enabled if row.enabled is not None else True)
        if not isinstance(enabled, bool):
            fail(422, "enabled must be a boolean")
        changed = task_id is None or "schedule" in values or "timezone" in values or enabled != row.enabled
        row.timezone, row.schedule, row.enabled = timezone, schedule, enabled
        if changed:
            row.next_run_at = next_run(schedule, timezone, moment) if enabled else None
        row.details = {**(row.details or {}), "private_generation": chat.reset_generation}
    row.updated_at, row.index_dirty = moment, True
    db.flush()
    return payload(db, row)


def list_tasks(service, owner_id):
    with service.transaction() as db:
        ensure_system(service, db, owner_id)
        return {"items": [payload(db, row) for row in db.scalars(select(ScheduledTask).where(ScheduledTask.owner_id == owner_id).order_by(ScheduledTask.created_at, ScheduledTask.id)) if not (row.details or {}).get("deleted")]}


def write(service, owner_id, task_id=None, values=None, *, delete=False):
    with service.transaction() as db:
        result = mutate(service, db, owner_id, task_id, values, delete=delete)
    return {"ok": True} if delete else result


def changed(owner_id):
    notifications.emit_personal(owner_id, "scheduled_tasks")
    notifications._emit({"kind": "scheduled_tasks", "owner_id": owner_id})


def review_snapshot(service, db, row, moment):
    details = row.details or {}
    cutoff = details.get("last_successful_cutoff") or iso(row.created_at)
    # Metadata only; model can fetch current content through existing permission-aware tools.
    private, member = service.membership(db, row.owner_id,
        db.scalar(select(Chat.id).where(Chat.private_owner_id == row.owner_id)))
    tracking = [{"id": topic.id, "chat_id": topic.chat_id, "title": projection.get("title", topic.title),
        "status": projection.get("status", topic.status), "updated_at": iso(topic.updated_at)}
        for topic, _, _, projection in service._tracking_scope(db, private, member, limit=100) if projection]
    approvals = [{"id": item.id, "status": item.status, "created_at": iso(item.created_at)} for item in db.scalars(select(Approval).where(Approval.approver_id == row.owner_id, Approval.status == "pending").order_by(Approval.created_at).limit(30))]
    followups = [{"id": item.id, "status": item.status, "due_at": iso(item.due_at)} for item in db.scalars(select(TwinFollowup).where(TwinFollowup.owner_id == row.owner_id, TwinFollowup.notification_state.in_(["waiting", "pending", "paused"])).order_by(TwinFollowup.due_at).limit(30))]
    children = [{"id": item.id, "state": item.state, "created_at": iso(item.created_at)} for item in db.scalars(select(PendingChildResult).where(PendingChildResult.owner_id == row.owner_id, PendingChildResult.state.in_(["pending", "paused"])).order_by(PendingChildResult.created_at).limit(30))]
    from .models import Message
    recent = []
    for item, request in db.execute(select(Message, ReplyRequest).outerjoin(ReplyRequest, ReplyRequest.id == Message.request_id).where(
            Message.chat_id == private.id, Message.state == "published", Message.created_at >= moment - timedelta(days=30))
            .order_by(Message.created_at.desc()).limit(100)):
        task = snapshot(request)
        if request and (request.source == "system_review" or task and task.get("kind") == "system_review"):
            continue
        recent.append({"id": item.id, "created_at": iso(item.created_at), "kind": item.kind})
        if len(recent) == 30:
            break
    changes = [item for item in recent if datetime.fromisoformat(item["created_at"]) > datetime.fromisoformat(cutoff)]
    result = {"as_of": iso(moment), "cutoff": cutoff, "changes": changes,
        "approvals": approvals, "followups": followups, "children": children,
        "previous_notice": {key: value for key, value in (details.get("previous_notice") or {}).items() if key in {"fingerprint", "at"}}}
    # Open work and recent context deserve temporal judgement even without local writes:
    # deadlines and external checks cannot safely be inferred from structured metadata alone.
    from .models import MemoryDreamState, MemoryEditAudit, Reminder
    memory = db.scalar(select(MemoryEditAudit.id).where(MemoryEditAudit.owner_id == row.owner_id,
        MemoryEditAudit.state == "published").limit(1))
    dream = db.get(MemoryDreamState, row.owner_id)
    result["deadlines"] = [{"id": reminder.id, "title": reminder.title, "due_at": iso(reminder.due_at)}
        for reminder in db.scalars(select(Reminder).where(Reminder.owner_id == row.owner_id,
            Reminder.done.is_(False)).order_by(Reminder.due_at.asc().nulls_last()).limit(30))]
    conversation = db.scalar(select(Message.id).where(Message.chat_id == private.id,
        Message.seq >= member.lower_seq, Message.state == "published",
        Message.kind.in_(["human", "twin", "reply_for_me"])).limit(1))
    result["needs_review"] = bool(tracking or conversation or approvals or followups or children or memory or dream or result["deadlines"])
    return result


def enqueue(service, db, row, planned, moment, *, manual_key=None):
    chat = db.scalar(select(Chat).where(Chat.private_owner_id == row.owner_id).with_for_update())
    if not chat or chat.reset_pending:
        return None
    details = row.details or {}
    if details.get("private_generation", chat.reset_generation) != chat.reset_generation:
        row.enabled, row.next_run_at, row.index_dirty = False, None, True
        return None
    key = "scheduled:" + hashlib.sha256(f"{row.id}:{row.revision}:{iso(planned)}:{manual_key or ''}".encode()).hexdigest()
    if manual_key:
        key = "scheduled-now:" + hashlib.sha256(f"{row.id}:{manual_key}".encode()).hexdigest()
    existing = db.scalar(select(ReplyRequest).where(ReplyRequest.requester_id == row.owner_id, ReplyRequest.client_request_key == key))
    if existing:
        return existing
    if busy(db, row):
        if manual_key:
            fail(409, "Scheduled task still has unresolved work")
        return None
    context = {"id": row.id, "kind": row.kind, "title": row.title, "instructions": SYSTEM_INSTRUCTIONS if row.kind == "system_review" else row.instructions,
        "timezone": row.timezone, "schedule": row.schedule, "revision": row.revision,
        "planned_at": iso(planned), "occurrence_id": key}
    if row.kind == "system_review":
        context["system_review"] = review_snapshot(service, db, row, moment)
        if not context["system_review"]["needs_review"] and not manual_key:
            row.details = {**details, "last_status": "silent", "last_run_at": iso(moment), "last_successful_cutoff": iso(moment)}
            return None
    request = service.accept_request(db, chat, service.twin(db, row.owner_id).id, row.owner_id,
        "system_review" if row.kind == "system_review" else "scheduled_task", key,
        input_body=json.dumps(context, ensure_ascii=False), intent_key=key)
    request.command_fingerprint = row.id
    row.details = {**details, "active_request_id": request.id, "last_request_id": request.id,
        "last_run_at": iso(moment), "last_status": "queued", "last_error": None}
    db.flush()
    return request


def run_now(service, owner_id, task_id, key):
    with service.transaction() as db:
        db.scalar(select(Chat).where(Chat.private_owner_id == owner_id).with_for_update())
        row = owned(db, owner_id, task_id)
        request = enqueue(service, db, row, now(), now(), manual_key=key)
        if not request:
            fail(409, "Scheduled task is unavailable")
        result = {"request_id": request.id, "task": payload(db, row)}
    return result


def history(service, owner_id, task_id, limit=20):
    with service.transaction() as db:
        owned(db, owner_id, task_id, lock=False)
        return {"items": [{"request_id": request.id, "status": request.status, "accepted_at": iso(request.accepted_at),
            "planned_at": (snapshot(request) or {}).get("planned_at"), "trigger": "manual" if request.client_request_key.startswith("scheduled-now:") else "scheduled", "run_id": request.run_id, "error": request.error}
            for request in db.scalars(select(ReplyRequest).where(ReplyRequest.command_fingerprint == task_id,
                ReplyRequest.requester_id == owner_id, ReplyRequest.source.in_(["scheduled_task", "system_review"]))
                .order_by(ReplyRequest.accepted_at.desc()).limit(limit))]}


def process_due(service, task_ids=None, *, moment=None, blocked_task_ids=None):
    moment = moment or now()
    accepted = []
    with service.transaction() as db:
        query = select(ScheduledTask).where(ScheduledTask.enabled.is_(True), ScheduledTask.next_run_at <= moment)
        if task_ids is not None:
            if not task_ids:
                return []
            query = query.where(ScheduledTask.id.in_(task_ids))
        candidates = list(db.scalars(query.order_by(ScheduledTask.next_run_at, ScheduledTask.id).limit(settings.scheduled_task_batch_size)))
        owner_ids = {candidate.owner_id for candidate in candidates}
        # Reuse the platform's existing Chat serialization, in its global ID order.
        if owner_ids:
            list(db.scalars(select(Chat).where(Chat.private_owner_id.in_(owner_ids)).order_by(Chat.id).with_for_update()))
        for candidate in candidates:
            row = db.scalar(select(ScheduledTask).where(ScheduledTask.id == candidate.id).execution_options(populate_existing=True))
            if not row or not row.enabled or row.next_run_at is None or aware(row.next_run_at) > moment:
                continue
            if row.id in (blocked_task_ids or set()):
                continue
            planned = aware(row.next_run_at)
            request = enqueue(service, db, row, planned, moment)
            if request:
                accepted.append(request.id)
            # Busy work retains the pending planned occurrence and retries in compensation.
            if busy(db, row) and not request:
                continue
            row.next_run_at = next_run(row.schedule, row.timezone, moment, anchor=planned)
            row.index_dirty = True
            row.updated_at = moment
    return accepted


def runtime_dispatch(service, db, name, args, context, chat):
    if chat.kind != "private" or chat.private_owner_id != context["owner_id"]:
        fail(403, "Scheduled task tools require your own private Twin chat")
    origin = db.get(ReplyRequest, context.get("request_id")) if context.get("request_id") else None
    if name != "list_scheduled_tasks" and (context.get("scheduled_task") or snapshot(origin)):
        fail(403, "Scheduled execution cannot change scheduled tasks")
    owner_id = context["owner_id"]
    if name == "list_scheduled_tasks":
        ensure_system(service, db, owner_id)
        return {"items": [payload(db, row) for row in db.scalars(select(ScheduledTask).where(ScheduledTask.owner_id == owner_id).order_by(ScheduledTask.created_at)) if not (row.details or {}).get("deleted")]}
    if name == "create_scheduled_task":
        return mutate(service, db, owner_id, values=args)
    task_id = args.get("id")
    if name == "update_scheduled_task":
        return mutate(service, db, owner_id, task_id, {key: value for key, value in args.items() if key != "id"})
    row = owned(db, owner_id, task_id)
    return mutate(service, db, owner_id, task_id, {"enabled": False} if row.kind == "system_review" else {}, delete=row.kind != "system_review")


def finish(db, request, status):
    task = snapshot(request)
    if not task:
        return
    row = db.get(ScheduledTask, task["id"])
    active = db.get(ReplyRequest, (row.details or {}).get("active_request_id")) if row and (row.details or {}).get("active_request_id") else None
    if not row or not active or active.causal_chain_id != request.causal_chain_id:
        return
    details = {**(row.details or {}), "last_status": status, "last_error": request.error, "last_request_id": request.id}
    if status == "completed" and row.kind == "system_review":
        details["last_successful_cutoff"] = task.get("system_review", {}).get("as_of", iso(now()))
    row.details, row.index_dirty = details, True


class DueIndex:
    """Bounded dirty repair + indexed due fallback; Redis loss never drops durable intent."""
    def __init__(self, service):
        self.service = service
        self.key = settings.redis_prefix + ":scheduled-tasks:due"
        self.last_reconcile = float("-inf")
        self.rebuild_after = None
        self.healthy = False
        self.dirty = True

    def rows(self, rebuild=False):
        with self.service.transaction() as db:
            query = select(ScheduledTask)
            if rebuild:
                if self.rebuild_after:
                    query = query.where(ScheduledTask.id > self.rebuild_after)
                query = query.order_by(ScheduledTask.id)
            else:
                query = query.where(ScheduledTask.index_dirty.is_(True)).order_by(ScheduledTask.id)
            return [(r.id, r.revision, iso(r.next_run_at) if r.enabled and not (r.details or {}).get("deleted") else None) for r in db.scalars(query.limit(settings.scheduled_task_batch_size))]

    def acknowledge(self, rows):
        from sqlalchemy import update
        with self.service.transaction() as db:
            for task_id, revision, due in rows:
                # Concurrent edit with the same revision is only scheduler advance; compare due too.
                query = update(ScheduledTask).where(ScheduledTask.id == task_id, ScheduledTask.revision == revision)
                query = query.where(ScheduledTask.next_run_at == datetime.fromisoformat(due)) if due else query.where(or_(ScheduledTask.next_run_at.is_(None), ScheduledTask.enabled.is_(False)))
                db.execute(query.values(index_dirty=False))

    async def tick(self):
        loop = asyncio.get_running_loop()
        reconcile = loop.time() - self.last_reconcile >= settings.scheduled_task_reconcile_seconds
        client = notifications.redis
        try:
            if not client:
                raise RuntimeError("Redis is unavailable")
            # A missing generation marker means FLUSHDB/restart; rebuild bounded primary-key pages.
            marker = self.key + ":ready"
            if not await client.exists(marker) and self.rebuild_after is None:
                self.rebuild_after = ""
            if self.rebuild_after is not None:
                rows = await self.service.db_call(self.rows, True)
                if rows:
                    self.rebuild_after = rows[-1][0]
                if len(rows) < settings.scheduled_task_batch_size:
                    self.rebuild_after = None
                    await client.set(marker, "1")
            elif self.dirty or reconcile:
                self.dirty = False
                rows = await self.service.db_call(self.rows)
                if len(rows) == settings.scheduled_task_batch_size:
                    self.dirty = True
            else:
                rows = []
            if rows:
                async with client.pipeline(transaction=True) as pipe:
                    for task_id, revision, due in rows:
                        # Redis has one member per task; all current fields are revalidated by PG.
                        member = task_id
                        if due:
                            pipe.zadd(self.key, {member: datetime.fromisoformat(due).timestamp()})
                        else:
                            pipe.zrem(self.key, member)
                    await pipe.execute()
                await self.service.db_call(self.acknowledge, rows)
            due_members = await client.zrangebyscore(self.key, "-inf", now().timestamp(), start=0, num=settings.scheduled_task_batch_size)
            if due_members:
                await admit_due(self.service, list(set(due_members)))
                self.dirty = True
                await client.zrem(self.key, *due_members)
            self.healthy = True
        except Exception:  # noqa: BLE001 — index failures must not drop durable schedule intent.
            self.healthy = False
        if reconcile:
            # Range-index query, bounded batch; never scan owners/tasks on a short polling loop.
            self.last_reconcile = loop.time()
            await admit_due(self.service)
        return 1.0 if self.healthy else min(30, settings.scheduled_task_reconcile_seconds)


def reset_owner(db, owner_id, generation):
    """Called inside the existing reset fence, under the same private Chat lock."""
    moment = now()
    for row in db.scalars(select(ScheduledTask).where(ScheduledTask.owner_id == owner_id)):
        row.revision += 1
        row.details = {"private_generation": generation, **({"deleted": True} if (row.details or {}).get("deleted") else {})}
        if row.kind == "user":
            row.enabled, row.next_run_at = False, None
        else:
            row.next_run_at = next_run(row.schedule, row.timezone, moment) if row.enabled else None
        row.updated_at, row.index_dirty = moment, True


def prior_work(service, task_ids=None, due_only=True):
    with service.transaction() as db:
        query = select(ScheduledTask)
        if task_ids is not None:
            query = query.where(ScheduledTask.id.in_(task_ids))
        if due_only:
            query = query.where(ScheduledTask.enabled.is_(True), ScheduledTask.next_run_at <= now())
        result = {}
        for row in db.scalars(query.order_by(ScheduledTask.next_run_at, ScheduledTask.id).limit(settings.scheduled_task_batch_size)):
            prior = db.get(ReplyRequest, (row.details or {}).get("active_request_id")) if (row.details or {}).get("active_request_id") else None
            result[row.id] = [service.request_payload(request) for request in db.scalars(select(ReplyRequest).where(ReplyRequest.causal_chain_id == prior.causal_chain_id).order_by(ReplyRequest.accepted_at).limit(100))] if prior else []
        return result


async def blocked_work(service, task_ids=None, *, due_only=True):
    groups = await service.db_call(prior_work, service, task_ids, due_only)
    blocked = set()
    runtime = service.runtime
    for task_id, requests in groups.items():
        for request in requests:
            if request["status"] in {"queued", "running"}:
                blocked.add(task_id)
                break
            if request.get("session_id") and request.get("run_id") and (not runtime or not hasattr(runtime, "has_unresolved_scheduled_work")):
                blocked.add(task_id)
                break
            if runtime and hasattr(runtime, "has_unresolved_scheduled_work"):
                try:
                    if await runtime.has_unresolved_scheduled_work(request):
                        blocked.add(task_id)
                        break
                except Exception:  # noqa: BLE001 — SDK read unavailable: preserve pending intent until recovery.
                    blocked.add(task_id)
                    break
    return blocked


async def admit_due(service, task_ids=None):
    blocked = await blocked_work(service, task_ids)
    return await service.db_call(process_due, service, task_ids, blocked_task_ids=blocked)


async def run_now_async(service, owner_id, task_id, key):
    # Authorize before querying any runtime journal for the requested task.
    def authorize():
        with service.transaction() as db:
            owned(db, owner_id, task_id, lock=False)
    await service.db_call(authorize)
    if task_id in await blocked_work(service, [task_id], due_only=False):
        # Replay the same manual key remains idempotent even when its work is running.
        def replay():
            with service.transaction() as db:
                row = owned(db, owner_id, task_id, lock=False)
                client_key = "scheduled-now:" + hashlib.sha256(f"{task_id}:{key}".encode()).hexdigest()
                request = db.scalar(select(ReplyRequest).where(ReplyRequest.requester_id == owner_id, ReplyRequest.client_request_key == client_key))
                return {"request_id": request.id, "task": payload(db, row)} if request else None
        result = await service.db_call(replay)
        if result:
            return result
        fail(409, "Scheduled task still has unresolved work")
    return await service.db_call(run_now, service, owner_id, task_id, key)


async def delete_async(service, owner_id, task_id):
    def authorize():
        with service.transaction() as db:
            owned(db, owner_id, task_id, lock=False)
    await service.db_call(authorize)
    if task_id in await blocked_work(service, [task_id], due_only=False):
        fail(409, "Wait for this task's delegated work to settle before deleting it")
    return await service.db_call(write, service, owner_id, task_id, delete=True)


async def list_tasks_async(service, owner_id):
    result = await service.db_call(list_tasks, service, owner_id)
    terminal = [item["id"] for item in result["items"] if item["last_request_id"] and not item["pending_work"]]
    if terminal:
        blocked = await blocked_work(service, terminal, due_only=False)
        for item in result["items"]:
            if item["id"] in blocked:
                item["pending_work"] = True
    return result


def notice(db, request, body):
    task = snapshot(request)
    if not task or task.get("kind") != "system_review":
        return True
    row = db.get(ScheduledTask, task["id"])
    if not row:
        return False
    fingerprint = hashlib.sha256(" ".join(body.split()).encode()).hexdigest()
    previous = (row.details or {}).get("previous_notice") or {}
    if previous.get("fingerprint") == fingerprint:
        return False
    row.details = {**(row.details or {}), "previous_notice": {"fingerprint": fingerprint, "at": iso(now())}}
    return True
