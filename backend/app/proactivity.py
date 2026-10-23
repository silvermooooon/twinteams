"""Small owner policy on the existing system-review task, not a scheduler."""
import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

TOOLS = {"get_proactivity", "update_proactivity"}
MUTATIONS = {"update_proactivity"}


def payload(row):
    policy = (row.details or {}).get("proactivity") or {}
    return {"level": policy.get("level", "normal") if row.enabled else "off",
        "quiet_hours": policy.get("quiet_hours"), "review_interval_seconds": row.schedule["seconds"]}


def validate_quiet(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"start", "end", "timezone"}
            or any(not isinstance(value[key], str) for key in value)
            or any(not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value[key]) for key in ("start", "end"))
            or value["start"] == value["end"]):
        raise HTTPException(422, "Quiet hours need distinct HH:MM start/end and an IANA timezone")
    try:
        ZoneInfo(value["timezone"])
    except (ValueError, ZoneInfoNotFoundError):
        raise HTTPException(422, "Quiet hours require a valid IANA timezone") from None
    return dict(value)


def quiet_until(row, moment):
    """Only automatic system-review admission observes this window."""
    if row.kind != "system_review":
        return None
    quiet = (row.details or {}).get("proactivity", {}).get("quiet_hours")
    if not quiet:
        return None
    zone = ZoneInfo(quiet["timezone"])
    local = moment.astimezone(zone)
    clock = local.strftime("%H:%M")
    start, end = quiet["start"], quiet["end"]
    if start < end:
        if not start <= clock < end:
            return None
        day = local.date()
    elif clock >= start:
        day = local.date() + timedelta(days=1)
    elif clock < end:
        day = local.date()
    else:
        return None
    closing = datetime.combine(day, time.fromisoformat(end)).replace(tzinfo=zone)
    instant = closing.astimezone(UTC)
    # An ambiguous closing hour must remain in the future during its second fold.
    if instant <= moment:
        instant = closing.replace(fold=1).astimezone(UTC)
    return instant if instant > moment else None


def dispatch(service, db, name, args, context):
    from .credentials import private_origin
    from .models import now
    from .scheduled_tasks import ensure_system, next_run, snapshot
    from .service import fail
    request, chat, _ = private_origin(service, db, context["owner_id"], context["request_id"])
    if context.get("raw_owner_input") is not None:
        fail(403, "Reply-for-me input cannot change personal review preferences")
    row = ensure_system(service, db, context["owner_id"])
    if name == "get_proactivity":
        return payload(row)
    if context.get("scheduled_task") or snapshot(request):
        fail(403, "Scheduled execution cannot change owner review preferences")
    if not args or set(args) - {"level", "quiet_hours"}:
        fail(422, "Change only the explicitly requested proactivity fields")
    level = args.get("level")
    if "level" in args and (not isinstance(level, str) or level not in {"off", "low", "normal", "high"}):
        fail(422, "Proactivity level must be off, low, normal or high")
    quiet = validate_quiet(args["quiet_hours"]) if "quiet_hours" in args else None
    policy = {**((row.details or {}).get("proactivity") or {})}
    was_enabled, planned = row.enabled, row.next_run_at
    if "level" in args:
        row.enabled = level != "off"
        if row.enabled:
            policy["level"] = level
    if "quiet_hours" in args:
        policy["quiet_hours"] = quiet
    moment = now()
    row.details = {**(row.details or {}), "proactivity": policy, "private_generation": chat.reset_generation}
    row.revision += 1
    row.updated_at, row.index_dirty = moment, True
    row.next_run_at = (planned if was_enabled and planned else next_run(row.schedule, row.timezone, moment)) if row.enabled else None
    if row.next_run_at:
        row.next_run_at = quiet_until(row, row.next_run_at) or row.next_run_at
    db.flush()
    return payload(row)


def policy_prompt(task):
    if not task or task.get("kind") != "system_review":
        return ""
    level = (task.get("proactivity") or {}).get("level", "normal")
    guidance = {"low": "Notify only important results, material risks or blockers, approaching deadlines and decisions the owner must make.",
        "high": "Actively advance useful next steps already authorized and report meaningful progress or needed decisions, without routine check-ins.",
        "normal": "Report meaningful changes, useful progress, actionable blockers or needed decisions."}
    return "Owner's autonomous-review preference: " + guidance.get(level, guidance["normal"]) + " Do not create new ongoing commitments or expand authorization.\n"


def tool_schemas():
    text = {"type": "string"}
    return [
        ("get_proactivity", "Read your owner's autonomous-review level and quiet hours in their own private conversation. Explicit saved tasks and requested reminders are separate.", {}, []),
        ("update_proactivity", "Save only the owner's explicitly requested autonomous-review preferences from their private task. Off pauses system reviews, not explicit tasks or reminders. Low means important results, blockers and decisions; normal balances useful changes; high actively advances already authorized work. Levels do not change the interval or create commitments. Quiet hours defer only automatic review starts; accepted work and manual Run now continue. Require an explicit IANA timezone; clarify if missing. Null clears quiet hours. Confirm only after saving succeeds.",
         {"level": {"type": "string", "enum": ["off", "low", "normal", "high"]}, "quiet_hours": {"anyOf": [
             {"type": "object", "properties": {"start": text, "end": text, "timezone": text},
              "required": ["start", "end", "timezone"], "additionalProperties": False}, {"type": "null"}]}}, []),
    ]
