"""Explicitly isolated browser-acceptance server, never an application fallback.

Run from backend with its Python 3.13 virtual environment:
    .venv/bin/python checks/acceptance_fixture.py --port 8011

Manual dialogue-cap check on this labelled fixture only:
    Sign in as DEV-AAA, open Team workspace, turn Reply for me off, send: dialogue
    Keep BBB and CCC offline or outside this chat. Leave AAA viewing the chat.
    Simulated replies target BBB then alternate CCC/BBB, never observing AAA.
    Numbered replies stop after the product's six automatic acceptances. Click
    Continue in the paused-dialogue control to grant the next recorded allowance;
    the same numbered causal chain continues instead of starting again at one.

Manual proactive-tool check in Team workspace, Reply for me off:
    Send: proactive
    The simulated Twin calls the real send_chat_message tool for this same chat
    with an authorized source ID. Its final acknowledgement needs no response.

All model responses are deterministic and labelled. Product service, HTTP/auth,
PostgreSQL journal, SDK execution/tools, FIFO, child delivery and approvals are real.
No third-party model or effect adapter is used. Database records use a disposable
test schema; files use backend/data/acceptance-fixture. Application tables are untouched.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import sys

BACKEND = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = (BACKEND / "data" / "acceptance-fixture").resolve()
FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
API = "twinteams-browser-acceptance-offline"
LABEL = "Acceptance test instance · Simulated model responses for testing only."
DIALOGUE_STEP = re.compile(r"^Acceptance fixture simulated dialogue \[([A-Za-z0-9_-]+)\]: step (\d+)\.", re.IGNORECASE)

# Always override inherited deployment settings BEFORE importing any app module.
# No configurable database path is accepted here: an accidental shell variable
# must never make this fixture touch the live/local-development instance.
for name, value in {
    "TWINTEAMS_ENV": "local",
    "TWINTEAMS_DEVELOPMENT_MODE": "true",
    "TWINTEAMS_TENANT_ID": "acceptance-fixture",
    "TWINTEAMS_WORKSPACE_ROOT": str(FIXTURE_ROOT / "workspace"),
    "AGENT_MEMORY_DIR": str(FIXTURE_ROOT / "memory"),
    "AGENT_PROVIDER": "openai",
    "AGENT_MODEL": "acceptance-fixture-offline",
    "AGENT_API": API,
    "TWINTEAMS_SUBAGENT_MODEL": "acceptance-fixture-offline",
    "AGENT_REASONING": "off",
    "OPENAI_API_KEY": "acceptance-fixture-placeholder-not-a-real-key",
    "ANTHROPIC_API_KEY": "acceptance-fixture-placeholder-not-a-real-key",
    "OPENAI_BASE_URL": "http://127.0.0.1:9/fixture-network-disabled",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:9/fixture-network-disabled",
}.items():
    os.environ[name] = value
sys.path.insert(0, str(BACKEND))

from postgres import TestDatabase
DATABASE = TestDatabase().configure()

from agent_runtime import AssistantMessageEventStream, OperationAborted, assistant_message
from fastapi import Request
from fastapi.responses import JSONResponse
from app.main import app
from app.runtime_bridge import RuntimeBridge, text_content
from app.service import service


def _current_turn(context):
    last_user = max((index for index, message in enumerate(context.messages)
                     if message.get("role") == "user"), default=-1)
    prompt = text_content(context.messages[last_user]) if last_user >= 0 else ""
    tools = [message for message in context.messages[last_user + 1:]
             if message.get("role") == "toolResult"]
    system = "\n".join(text_content(message) for message in context.messages
                       if message.get("role") == "system")
    return prompt, tools, system


def _final(model, content, *, tool=False):
    stream = AssistantMessageEventStream()
    stream.push({"type": "start", "partial": assistant_message(model)})
    stream.push({"type": "done", "message": assistant_message(
        model, content=content, stopReason="toolUse" if tool else "stop",
    )})
    return stream


def _text(value):
    return [{"type": "text", "text": value}]


def _fixture_request(context):
    """Resolve only this adapter turn's accepted product request, without guessing."""
    message = next((row for row in reversed(context.messages) if row.get("role") == "user"), {})
    request_id = message.get("queueId")
    return service.get_request(request_id) if request_id else None


def _authorized_message_ids(system):
    prefix = "Authorized recent messages, with no gap after summary: "
    if prefix not in system:
        return []
    try:
        rows, _ = json.JSONDecoder().raw_decode(system.split(prefix, 1)[1])
    except (ValueError, TypeError):
        return []
    return [row["id"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)] if isinstance(rows, list) else []


async def _slow_stream(model, options):
    stream = AssistantMessageEventStream()
    signal = options["signal"]

    async def produce():
        partial = assistant_message(model)
        try:
            await stream.send({"type": "start", "partial": deepcopy(partial)})
            body = "Acceptance fixture slow response: "
            for index in range(1, 41):
                await signal.run(asyncio.sleep(0.5))
                delta = body if index == 1 else f"test chunk {index:02d}. "
                if index == 1:
                    body = delta
                else:
                    body += delta
                partial = assistant_message(model, content=_text(body))
                await stream.send({"type": "text_delta", "delta": delta,
                                   "partial": deepcopy(partial), "contentIndex": 0})
            final = assistant_message(model, content=_text(body + "Fixture operation complete."), stopReason="stop")
            await stream.send({"type": "done", "message": final})
        except OperationAborted:
            final = assistant_message(model, content=partial.get("content", []),
                                      stopReason="aborted", errorMessage="Acceptance fixture stopped by owner")
            await stream.send({"type": "error", "error": final})
        except asyncio.CancelledError as error:
            stream.fail(error)
            raise
        except Exception as error:
            stream.fail(error)

    stream.task = asyncio.create_task(produce())
    return stream


async def offline_adapter(model, context, options):
    """Only this explicitly launched fixture registers this SDK adapter."""
    if model.api != API:
        raise RuntimeError("Acceptance fixture refuses any non-fixture model API")
    options["signal"].throw_if_aborted()
    prompt, tools, system = _current_turn(context)
    normalized = prompt.strip().casefold()
    if "choose which human owners a group-chat message is addressed to" in system.casefold():
        # Exercise the real model-routing contract without producing an invalid
        # prose response where the service expects a structured response decision.
        allowed = json.loads(system.rsplit("Allowed owners: ", 1)[-1])
        try:
            message = json.loads(prompt).get("message", {})
            author = message.get("author_owner_id") or message.get("source_owner_id") or message.get("author_id")
            body = str(message.get("body", "")).strip().casefold().rstrip(".! ")
        except (ValueError, AttributeError):
            author, body = None, ""
        dialogue = body == "dialogue" or body.startswith("dialogue ") or DIALOGUE_STEP.match(body)
        if dialogue:
            # Explicit test-only routing keeps the viewing AAA owner out of the
            # target set, so presence does not hide the product's global cap.
            target = "owner-jordan" if author == "owner-maya" else "owner-maya"
            target = target if target in allowed and target != author else None
        else:
            target = next((owner for owner in allowed if owner != author), None)
        informational = body.startswith(("acceptance fixture proactive test message [", "acceptance fixture proactive test complete ["))
        needs_response = bool(target) and not informational and body not in {"thanks", "thank you", "ok", "okay", "done", "completed", "got it"}
        return _final(model, _text(json.dumps({
            "target_owner_ids": [target] if needs_response else [],
            "needs_response": needs_response,
            "reason": ("Acceptance fixture simulated numbered dialogue: BBB/CCC alternate while AAA observes."
                       if dialogue else "Acceptance fixture structured routing decision; simulated for testing only."),
        })))
    if "this request is reply-for-me" in system.casefold():
        return _final(model, _text("Acceptance fixture processed owner text: " + prompt.strip()))
    dialogue_step = DIALOGUE_STEP.match(prompt.strip())
    if normalized == "dialogue" or normalized.startswith("dialogue ") or dialogue_step:
        request = _fixture_request(context) or {}
        label = dialogue_step.group(1) if dialogue_step else re.sub(
            r"[^A-Za-z0-9_-]", "", str(request.get("causal_chain_id") or request.get("source_message_id") or request.get("id") or "isolated"))[:12]
        step = int(dialogue_step.group(2)) + 1 if dialogue_step else 1
        return _final(model, _text(
            f"Acceptance fixture simulated dialogue [{label}]: step {step}. "
            "BBB/CCC, please respond to this next numbered test turn. This is simulated test data only."
        ))
    if normalized == "proactive" or normalized.startswith("proactive "):
        sent = next((row for row in reversed(tools) if row.get("toolName") == "send_chat_message"), None)
        request = _fixture_request(context) or {}
        label = re.sub(r"[^A-Za-z0-9_-]", "", str(request.get("causal_chain_id") or request.get("id") or "isolated"))[:12]
        if sent is not None:
            outcome = ("The product refused this test send; no message was posted."
                       if sent.get("isError") else "The replay-safe in-app tool confirmed this test message was posted.")
            return _final(model, _text(f"Acceptance fixture proactive test complete [{label}]: {outcome} No further reply is needed."))
        source_id = request.get("source_message_id")
        if not request.get("chat_id") or source_id not in _authorized_message_ids(system):
            return _final(model, _text(f"Acceptance fixture proactive test complete [{label}]: no send was attempted because this turn has no resolved authorized source. No further reply is needed."))
        return _final(model, [{"type": "toolCall", "id": "fixture-send-chat-message", "name": "send_chat_message",
                              "arguments": {"chat_id": request["chat_id"],
                                            "body": f"Acceptance fixture proactive test message [{label}]: a simulated same-chat update, delivered by the real replay-safe product tool. No reply is needed.",
                                            "source_message_ids": [source_id]}}], tool=True)
    if normalized.startswith("a background worker returned"):
        return _final(model, _text("Acceptance fixture: the background result arrived through the durable reply queue and has been summarized."))
    if normalized.startswith("acceptance fixture child task:"):
        await options["signal"].run(asyncio.sleep(0.75))
        return _final(model, _text("Acceptance fixture child result: the isolated background task completed successfully."))
    if normalized == "slow" or normalized.startswith("slow "):
        return await _slow_stream(model, options)
    if normalized == "delegate" or normalized.startswith("delegate "):
        if not tools:
            return _final(model, [{"type": "toolCall", "id": "fixture-spawn-child", "name": "spawn_agent",
                                  "arguments": {"name": "worker", "task": "Acceptance fixture child task: finish this deterministic browser test."}}], tool=True)
        return _final(model, _text("Acceptance fixture: background work was delegated. The main reply is complete; the child result will arrive separately."))
    if normalized == "approve" or normalized.startswith("approve "):
        if not tools:
            return _final(model, [{"type": "toolCall", "id": "fixture-create-approval", "name": "create_approval",
                                  "arguments": {
                                      "title": "Acceptance fixture approval",
                                      "description": "Test-only scoped consent record for this chat. No email, purchase, payment or external action will run.",
                                      "target": "Acceptance fixture in-app example only",
                                      "impact": "Creates only an isolated approval record. Approval does not trigger an external effect.",
                                      "action": {"tool": "create_reminder", "arguments": {"title": "Approved fixture reminder"}},
                                  }}], tool=True)
        return _final(model, _text("Acceptance fixture: a scoped approval is waiting in Approvals. Choose Approve or Reject; no external action is installed."))
    return _final(model, _text("Acceptance fixture response: " + (prompt.strip() or "plain test response")))


@asynccontextmanager
async def fixture_lifespan(application):
    # Same ProductService and RuntimeBridge as the real app. Only model adapter
    # registration and the clearly labelled initial input are fixture-specific.
    service.initialize()
    runtime = RuntimeBridge(service)
    runtime.models.register_api(API, offline_adapter)
    if runtime.config.api != API or runtime.config.provider != "openai":
        raise RuntimeError("Acceptance fixture model configuration was unexpectedly changed")
    try:
        await runtime.start()
        service.bind_runtime(runtime)
        await runtime.recover_background()
        service.send_message(
            "owner-alex", "private-alex",
            "Acceptance fixture ready. Try plain, slow (20-second stream for Stop), delegate, or approve. In Team workspace with Reply for me off, send dialogue to test the six-reply cap and Continue while AAA observes, or proactive to test a same-chat tool send. All responses and records on this instance are simulated test data.",
            "acceptance-fixture-introduction-v2",
        )
        await service.start()
        yield
    finally:
        await service.close()
        await runtime.close()


app.router.lifespan_context = fixture_lifespan


@app.middleware("http")
async def fixture_metadata(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Twinteams-Acceptance-Fixture"] = "true"
    if request.url.path not in {"/health", "/api/config", "/api/bootstrap"} or response.status_code != 200:
        return response
    body = b"".join([part async for part in response.body_iterator])
    result = json.loads(body)
    result.update({"acceptance_fixture": True, "fixture_label": LABEL,
                   "fixture_modes": ["plain", "slow", "delegate", "approve", "dialogue", "proactive"],
                   "fixture_external_effects": False})
    headers = {key: value for key, value in response.headers.items()
               if key.casefold() not in {"content-length", "content-type", "content-encoding"}}
    return JSONResponse(result, status_code=response.status_code, headers=headers)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    print(f"{LABEL}\nOffline adapter only. Fixture data: {FIXTURE_ROOT}\nOpen http://localhost:{args.port}; sign in with DEV-AAA, DEV-BBB or DEV-CCC.", flush=True)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info", timeout_graceful_shutdown=3)


if __name__ == "__main__":
    main()
