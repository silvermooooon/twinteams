"""Host-only values and nonblocking credential requests, without action authorization."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import unicodedata
from pathlib import Path

from agent_runtime.execution.tools import check_bindings
from agent_runtime.secrets import LocalSecretStore, SecretInfo, SecretStore
from agent_runtime.secrets.store import validate_values
from agent_runtime.sessions import SessionInfo
from fastapi import HTTPException
from sqlalchemy import select

from .config import ROOT, settings
from .credential_models import CredentialRequest
from .models import (
    Actor,
    ContextManifest,
    Owner,
    PendingChildResult,
    ProductOperation,
    ReplyLane,
    ReplyRequest,
)


def fail(status, detail):
    raise HTTPException(status, detail)


def validate_ref(ref):
    if not isinstance(ref, str):
        fail(422, "Credential name must be text")
    ref = ref.strip()
    if (not ref or len(ref) > 128 or ref in {".", ".."} or "/" in ref or "\\" in ref
            or any(unicodedata.category(char).startswith("C") for char in ref)):
        fail(422, "Credential name must contain 1–128 visible characters without path separators")
    return ref


def make_secret_store(identity):
    # Never inherit the SDK's relative default inside an Agent workspace.
    directory = Path(os.getenv("AGENT_SECRETS_DIR") or ROOT / "data" / "runtime-secrets").expanduser().resolve()
    if directory.is_relative_to(settings.workspace_root):
        fail(503, "Credential storage must be outside the workspace")
    try:
        return LocalSecretStore(identity, directory)
    except (ImportError, ValueError):
        fail(503, "Credential storage is not configured")


def configured():
    try:
        make_secret_store(SessionInfo("credential-configuration", settings.tenant_id, "configuration"))
        return True
    except HTTPException:
        return False


def owner_store(owner_id):
    return make_secret_store(SessionInfo("credential-management", settings.tenant_id, owner_id))


def info_json(info):
    return {"name": info.ref, "ref": info.ref, "description": info.description,
            "keynames": list(info.env_names)}


def request_json(row):
    return {"id": row.id, "owner_id": row.owner_id, "chat_id": row.chat_id,
            "origin_request_id": row.origin_request_id, "name": row.ref, "ref": row.ref,
            "description": row.description, "keynames": row.keynames, "reason": row.reason,
            "status": row.status, "continuation_request_id": row.continuation_request_id,
            "created_at": row.created_at.isoformat()}


def runtime_origin(service, db, owner_id, request_id, *, executable=True):
    request = db.get(ReplyRequest, request_id)
    if not request:
        fail(409, "Original request is unavailable")
    service._check_private_request(db, request)
    chat, member = service.membership(db, owner_id, request.chat_id, True)
    target = db.get(Actor, request.target_twin_id)
    if not target or target.owner_id != owner_id:
        fail(403, "Execution belongs to the target Twin's owner")
    if executable and (request.cancel_requested or request.status in {"cancelled", "failed"}):
        fail(409, "Original request was cancelled or failed")
    if request.source == "credential_result":
        fulfilled = fulfilled_for_continuation(db, owner_id, request)
        if not fulfilled:
            fail(409, "Credential continuation has no completed owner request")
        runtime_origin(service, db, owner_id, fulfilled.origin_request_id, executable=executable)
    elif request.source == "subagent_result":
        child = db.scalar(select(PendingChildResult).where(PendingChildResult.accepted_request_id == request.id,
            PendingChildResult.owner_id == owner_id, PendingChildResult.state == "accepted"))
        if not child:
            fail(409, "Worker continuation has no retained owner request")
        runtime_origin(service, db, owner_id, child.parent_request_id, executable=executable)
    elif request.source == "followup_result":
        from .followups import context as followup_context
        from .followups import continuation
        followup_context(service, db, request, owner_id)
        followup = continuation(db, request, owner_id)
        runtime_origin(service, db, owner_id, followup.origin_request_id, executable=executable)
    return request, chat, member



def private_origin(service, db, owner_id, request_id, *, executable=True):
    """Narrow private-task gate retained for existing cross-chat retry contracts."""
    request, chat, member = runtime_origin(service, db, owner_id, request_id, executable=executable)
    if chat.kind != "private" or chat.private_owner_id != owner_id or request.requester_id != owner_id:
        fail(403, "This action requires your own private Twin conversation")
    return request, chat, member

def fulfilled_for_continuation(db, owner_id, request):
    # Explicit retries have a new ID/generation but retain the canonical intent.
    # The original fulfilled form remains the source of the authorization link.
    return db.scalar(select(CredentialRequest).join(ReplyRequest,
        CredentialRequest.continuation_request_id == ReplyRequest.id).where(
        ReplyRequest.intent_key == request.intent_key, ReplyRequest.chat_id == request.chat_id,
        ReplyRequest.target_twin_id == request.target_twin_id,
        CredentialRequest.owner_id == owner_id, CredentialRequest.status == "fulfilled"))


def list_requests(service, owner_id):
    with service.transaction() as db:
        service.twin(db, owner_id)
        rows = db.scalars(select(CredentialRequest).where(CredentialRequest.owner_id == owner_id)
                          .order_by(CredentialRequest.created_at.desc()).limit(200))
        return {"configured": configured(), "requests": [request_json(row) for row in rows]}


async def list_credentials(owner_id):
    if not configured():
        return {"configured": False, "credentials": []}
    return {"configured": True, "credentials": [info_json(info) for info in await owner_store(owner_id).list()]}


def normalize_values(ref, values=None, value=None):
    if values is not None and value is not None:
        fail(422, "Supply one credential value mapping")
    values = values if values is not None else {ref: value}
    try:
        validate_values(values)
        check_bindings(values)
    except (ValueError, TypeError):
        fail(422, "Provide nonempty credential values with valid environment variable names")
    if len(values) > 32 or any(len(v) > 65536 for v in values.values()):
        fail(422, "Credential values exceed the supported limits")
    return values


def manage(service, owner_id, ref, *, values=None, value=None, description="", delete=False):
    ref = validate_ref(ref)
    with service.transaction() as db:
        # Existing owner row serializes administrative writes across API workers.
        if not db.scalar(select(Owner).where(Owner.id == owner_id).with_for_update()):
            fail(404, "Owner not found")
        store = owner_store(owner_id)
        if delete:
            asyncio.run(store.delete(ref))
            return {"deleted": True, "name": ref}
        values = normalize_values(ref, values, value)
        return info_json(asyncio.run(store.put(ref, values, description=description)))


def create_request(service, args, context):
    owner_id = context["owner_id"]
    request_id = context.get("request_id") or context.get("origin_request_id")
    ref = validate_ref(args.get("name") or args.get("ref"))
    keys = args.get("keynames")
    if (not isinstance(keys, list) or not keys or len(keys) > 32
            or any(not isinstance(key, str) for key in keys) or len(set(keys)) != len(keys)):
        fail(422, "Provide distinct environment variable names")
    normalize_values(ref, {key: "metadata-validation" for key in keys})
    description, reason = args.get("description", ""), args.get("reason", "")
    if not isinstance(description, str) or len(description) > 2000 or not isinstance(reason, str) or len(reason) > 4000:
        fail(422, "Credential request metadata is invalid")
    operation_id = context["operation_id"]
    fingerprint = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
    with service.transaction() as db:
        origin, chat, member = runtime_origin(service, db, owner_id, request_id)
        prior = db.get(ProductOperation, operation_id)
        if prior:
            if prior.owner_id != owner_id or prior.name != "request_credential" or prior.arguments_hash != fingerprint:
                fail(409, "Credential operation key was already used")
            row = db.get(CredentialRequest, prior.result["id"])
            return prior.result if "status" in prior.result else request_json(row)
        provenance = service.manifest_provenance(db, origin.id)
        if not service.provenance_allowed(db, member, provenance):
            fail(409, "Original context is no longer authorized")
        row = CredentialRequest(owner_id=owner_id, chat_id=chat.id, origin_request_id=origin.id,
            runtime_session_id=context.get("runtime_session_id"), runtime_run_id=context.get("runtime_run_id"),
            runtime_command_id=context.get("runtime_command_id"),
            ref=ref, description=description, keynames=sorted(keys), reason=reason,
            source_provenance=provenance)
        db.add(row)
        db.flush()
        result = request_json(row)
        db.add(ProductOperation(operation_id=operation_id, owner_id=owner_id, chat_id=chat.id,
            name="request_credential", arguments_hash=fingerprint, result=result))
        return result


def complete(service, owner_id, request_id, *, existing_name=None, name=None, values=None,
             value=None, description=None):
    with service.transaction() as db:
        db.scalar(select(Owner).where(Owner.id == owner_id).with_for_update())
        row = db.get(CredentialRequest, request_id)
        if not row or row.owner_id != owner_id:
            fail(404, "Credential request not found")
        if row.status == "fulfilled":
            return request_json(row)
        if row.status != "pending":
            fail(409, "Credential request is no longer pending")
        origin, chat, member = runtime_origin(service, db, owner_id, row.origin_request_id)
        if not service.provenance_allowed(db, member, row.source_provenance):
            fail(409, "Original context is no longer authorized")
        ref = validate_ref(existing_name or name or row.ref)
        # A new ref would change the model's requested binding; make it a new request instead.
        if ref != row.ref:
            fail(422, "Choose the credential name requested by this task")
        store = owner_store(owner_id)
        if existing_name:
            if values is not None or value is not None:
                fail(422, "Choose an existing credential or supply new values")
            info = asyncio.run(store.describe(ref))
        else:
            supplied = normalize_values(ref, values, value)
            if sorted(supplied) != sorted(row.keynames):
                fail(422, "Credential environment variable names do not match this request")
            info = asyncio.run(store.put(ref, supplied, description=description if description is not None else row.description))
        if sorted(info.env_names) != sorted(row.keynames):
            fail(422, "Credential environment variable names do not match this request")
        # File and PG are intentionally not atomic. A crash here leaves pending;
        # the owner explicitly retries or chooses the existing saved credential.
        # Lock the origin after writing to serialize cancellation before queue admission.
        db.scalar(select(ReplyLane).where(ReplyLane.chat_id == origin.chat_id,
            ReplyLane.twin_id == origin.target_twin_id).with_for_update())
        db.refresh(origin, with_for_update=True)
        runtime_origin(service, db, owner_id, origin.id)
        key = f"credential-request:{row.id}:fulfilled"
        continuation = service.accept_request(db, chat, origin.target_twin_id, origin.requester_id,
            "credential_result", key, source_message_id=origin.source_message_id,
            intent_key=key, causal_chain_id=origin.causal_chain_id, source_owner_id=origin.source_owner_id,
            input_body=f"The owner completed credential request {row.id}. Credential {row.ref} is now available with environment variables {', '.join(row.keynames)}. "
                       "Continue only the remaining work from the original task and existing history. "
                       "Do not repeat completed shell commands. This supplies credentials; it does not approve any external action.")
        row.status = "fulfilled"
        row.continuation_request_id = continuation.id
        db.add(ContextManifest(request_id=continuation.id, owner_id=owner_id,
            grant_version=member.grant_version, high_water_seq=chat.next_message_seq - 1,
            sources=row.source_provenance, source_digest=hashlib.sha256(json.dumps(row.source_provenance, sort_keys=True, default=str).encode()).hexdigest()))
        db.flush()
        return request_json(row)


def dismiss(service, owner_id, request_id):
    with service.transaction() as db:
        db.scalar(select(Owner).where(Owner.id == owner_id).with_for_update())
        row = db.get(CredentialRequest, request_id)
        if not row or row.owner_id != owner_id:
            fail(404, "Credential request not found")
        if row.status == "pending":
            row.status = "dismissed"
        return request_json(row)


def continuation_origin(service, owner_id, request_id, manifest_id):
    """Authorize the exact originating run before its facts enter a new model request."""
    with service.transaction() as db:
        current = db.get(ReplyRequest, request_id)
        if not current or current.source != "credential_result":
            return None
        runtime_origin(service, db, owner_id, request_id)
        row = fulfilled_for_continuation(db, owner_id, current)
        if not row:
            fail(409, "Credential continuation has no completed owner request")
        origin, _, member = runtime_origin(service, db, owner_id, row.origin_request_id)
        sources = service.manifest_provenance(db, origin.id)
        if not service.provenance_allowed(db, member, sources):
            fail(409, "Original tool context is no longer authorized")
        manifest = db.get(ContextManifest, manifest_id)
        if not manifest or manifest.request_id != request_id or manifest.owner_id != owner_id:
            fail(409, "Credential continuation has no matching context manifest")
        inherited = {hashlib.sha256(json.dumps(source, sort_keys=True, default=str).encode()).hexdigest(): source
                     for source in [*manifest.sources, *sources]}
        manifest.sources = list(inherited.values())
        manifest.source_digest = hashlib.sha256(json.dumps(manifest.sources, sort_keys=True, default=str).encode()).hexdigest()
        if not origin.session_id or not origin.run_id or origin.status != "completed":
            fail(409, "Original work has not settled; reconcile it before continuing")
        return {"request_id": origin.id, "request_session_id": origin.session_id,
                "request_run_id": origin.run_id,
                "session_id": row.runtime_session_id or origin.session_id,
                "run_id": row.runtime_run_id or origin.run_id,
                "command_id": row.runtime_command_id or origin.id}


def queued_continuations_for_run(service, session_id, run_id):
    with service.transaction() as db:
        return [{"id": request.id, "chat_id": request.chat_id} for request in db.scalars(
            select(ReplyRequest).join(CredentialRequest,
                CredentialRequest.continuation_request_id == ReplyRequest.id).where(
                CredentialRequest.runtime_session_id == session_id,
                CredentialRequest.runtime_run_id == run_id,
                ReplyRequest.status == "queued", ReplyRequest.cancel_requested.is_(False)))]


class FrozenSecretStore(SecretStore):
    """Agent contract is fixed; values are resolved afresh on each shell invocation."""
    def __init__(self, delegate, catalog):
        super().__init__(delegate.identity)
        self.delegate = delegate
        self.catalog = {item["ref"]: SecretInfo(item["ref"], item["description"], tuple(item["env_names"])) for item in catalog}

    async def describe(self, ref):
        if ref not in self.catalog:
            fail(403, "Credential is outside this request's frozen catalog")
        return self.catalog[ref]

    async def list(self):
        return list(self.catalog.values())

    async def resolve(self, ref):
        await self.describe(ref)
        return await self.delegate.resolve(ref)

    async def put(self, *args, **kwargs):
        raise PermissionError("Credential management is host-only")

    async def delete(self, *args, **kwargs):
        raise PermissionError("Credential management is host-only")
