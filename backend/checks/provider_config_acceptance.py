"""Bounded, isolated provider-config checks. FAKE key + MockTransport only.

Run: .venv/bin/python checks/provider_config_acceptance.py
Never opens the normal application database, credential file, or provider network.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory(prefix="twinteams-provider-config-check-")
ROOT = Path(TEMP.name)
(ROOT / "data").mkdir()
for name, value in {
    "TWINTEAMS_WORKSPACE_ROOT": str(ROOT / "workspace"),
    "TWINTEAMS_ENV": "local",
    "TWINTEAMS_DEVELOPMENT_MODE": "true",
    "TWINTEAMS_LOCAL_PROVIDER_CONFIG": "true",
    "TWINTEAMS_TENANT_ID": "provider-config-acceptance",
    "AGENT_API": "openai-responses",
}.items():
    os.environ[name] = value
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from postgres import TestDatabase
DATABASE = TestDatabase().configure()

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app import provider_config as pc
from app.api import router as api_router
from app.db import engine
from app.service import service

# This single clearly fake value is the only credential used by these checks.
FAKE_KEY = "sk-test-placeholder-NEVER-A-REAL-KEY-9281"
BASE = "http://127.0.0.1:8019"
GUARDS = {"Origin": BASE, "X-Twinteams-Config": "1", "Sec-Fetch-Site": "same-origin"}
BODY = {"api_key": FAKE_KEY, "confirm_billed_call": True, "save_for_later": False}
pc.ROOT = ROOT
pc.SECRET_DIRECTORY = ROOT / "data" / "provider-secrets"
pc.SECRET_PATH = pc.SECRET_DIRECTORY / "openai.env"
service.initialize()
app = FastAPI()
app.include_router(api_router)
app.include_router(pc.router)
client = TestClient(app, base_url=BASE, client=("127.0.0.1", 50000))


class CaptureLogs(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(self.format(record))


logs = CaptureLogs()
logging.getLogger().addHandler(logs)
logging.getLogger().setLevel(logging.DEBUG)


async def refuse_network(*args, **kwargs):
    raise AssertionError("Provider HTTP traffic must use the controlled test transport")


def response_body(**overrides):
    return {
        "id": "provider-request-id-must-not-escape",
        "model": pc.MODEL,
        "status": "completed",
        "usage": {"total_tokens": 99},
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}],
        **overrides,
    }


calls = []


def good_transport(request):
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.method == "POST"
    assert request.headers["authorization"] == "Bearer " + FAKE_KEY
    payload = json.loads(request.content)
    assert payload == {
        "model": "gpt-6-luna",
        "input": "Reply with OK.",
        "store": False,
        "max_output_tokens": 32,
        "reasoning": {"effort": "none"},
    }
    calls.append(request)
    logging.getLogger("httpcore.http11").debug(
        "Intentionally simulated unsafe wire log: %s", FAKE_KEY
    )
    logging.getLogger("httpx").debug("Intentionally simulated unsafe HTTPX log: %s", FAKE_KEY)
    return httpx.Response(200, json=response_body())


def fake_client(transport=good_transport):
    return httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(pc.PROVIDER_TIMEOUT_SECONDS, connect=pc.PROVIDER_CONNECT_TIMEOUT_SECONDS),
    )


def fresh_gate():
    pc._attempt_gate = pc._AttemptGate()


def login(username="DEV-AAA", target=client):
    target.cookies.clear()
    result = target.post("/api/auth/login", json={"username": username})
    assert result.status_code == 200


def verify(*, body=None, headers=None, transport=good_transport):
    with patch.object(pc, "_provider_client", lambda: fake_client(transport)):
        result = client.post(
            "/api/local-provider-config/verify",
            json=BODY if body is None else body,
            headers=GUARDS if headers is None else headers,
        )
    safe(result)
    return result


def verify_saved(*, body=None, headers=None, transport=good_transport):
    with patch.object(pc, "_provider_client", lambda: fake_client(transport)):
        result = client.post(
            "/api/local-provider-config/verify-saved",
            json={"confirm_billed_call": True} if body is None else body,
            headers=GUARDS if headers is None else headers,
        )
    safe(result)
    return result


def safe(result):
    assert result.headers.get("cache-control") == "no-store", (result.status_code, result.text)
    assert FAKE_KEY not in result.text
    assert "provider-request-id-must-not-escape" not in result.text
    assert "total_tokens" not in result.text
    assert result.headers.get("x-request-id") is None


def count_business_rows():
    with engine.connect() as connection:
        return {
            name: connection.scalar(text(f'SELECT count(*) FROM "{name}"'))
            for name in inspect(engine).get_table_names()
            if name.startswith("tt_")
        }


def clear_fake_store():
    if pc.SECRET_DIRECTORY.is_symlink():
        pc.SECRET_DIRECTORY.unlink()
    elif pc.SECRET_DIRECTORY.exists():
        for file in pc.SECRET_DIRECTORY.iterdir():
            file.unlink()
        pc.SECRET_DIRECTORY.rmdir()


class UnreadableErrorBody(httpx.AsyncByteStream):
    async def __aiter__(self):
        raise AssertionError("Provider error bodies must never be read")
        yield b""


async def check_client_defaults():
    async with pc._provider_client() as actual:
        assert actual._trust_env is False
        assert actual.follow_redirects is False
        assert pc.PROVIDER_TIMEOUT_SECONDS == 90.0
        assert pc.PROVIDER_CONNECT_TIMEOUT_SECONDS == 10.0
        assert actual.timeout == httpx.Timeout(90.0, connect=10.0)
        assert actual._transport._pool._retries == 0


def main():
    asyncio.run(check_client_defaults())
    with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse_network), client:
        for path in ("/api/local-provider-config", "/api/local-provider-config/verify", "/api/local-provider-config/save-page", "/api/local-provider-config/save"):
            result = verify() if path.endswith("verify") else client.post(path, json={"api_key": FAKE_KEY, "confirm_local_save": True}, headers=GUARDS) if path.endswith("/save") else client.get(path)
            assert result.status_code == 401, (result.status_code, result.json())
            safe(result)
        login("DEV-BBB")
        assert client.get("/api/local-provider-config").status_code == 403
        assert verify().status_code == 403
        assert client.get("/api/local-provider-config/save-page").status_code == 403
        assert client.post("/api/local-provider-config/save", json={"api_key": FAKE_KEY, "confirm_local_save": True}, headers=GUARDS).status_code == 403
        login()
        result = client.get("/api/local-provider-config")
        assert result.status_code == 200
        safe(result)
        assert result.json() == {
            "available": True,
            "model": pc.MODEL,
            "provider": "openai",
            "endpoint_host": "api.openai.com",
            "storage": "not_saved",
            "saved": False,
            "storage_available": True,
            "storage_path": str(pc.SECRET_PATH),
            "enabled": False,
            "admin_owner_id": "owner-alex",
        }
        assert not pc.SECRET_DIRECTORY.exists()
        assert service.runtime is None and service.model_configured is False
        print("PASS local AAA cookie-only metadata, unauthenticated 401 and BBB 403")

        for environment in (
            {"TWINTEAMS_LOCAL_PROVIDER_CONFIG": "false"},
            {"AGENT_DB_URL": "postgresql://example.invalid/not-used"},
            {"AGENT_API": "twinteams-browser-acceptance-offline"},
        ):
            with patch.dict(os.environ, environment):
                assert verify().status_code == 403
        for configured in (
            replace(pc.settings, environment="production"),
            replace(pc.settings, development_mode=False),
            replace(pc.settings, database_url="postgresql://example.invalid/not-used"),
            replace(pc.settings, tenant_id="acceptance-fixture"),
        ):
            with patch.object(pc, "settings", configured):
                assert verify().status_code == 403
        remote = TestClient(app, base_url=BASE, client=("203.0.113.1", 40000))
        remote.cookies.update(client.cookies)
        assert remote.get("/api/local-provider-config").status_code == 403
        for headers in (
            {**GUARDS, "Host": "evil.example"},
            {**GUARDS, "Host": "127.0.0.1.evil.example"},
            {**GUARDS, "Host": "localhost:0"},
            {**GUARDS, "Host": "[::1]:99999"},
            {**GUARDS, "Forwarded": "for=127.0.0.1"},
            {**GUARDS, "X-Forwarded-For": "127.0.0.1"},
            {**GUARDS, "X-Real-IP": "127.0.0.1"},
            {**GUARDS, "Origin": "http://localhost:8019"},
            {**GUARDS, "Origin": "http://127.0.0.1:8020"},
            {**GUARDS, "Origin": "null"},
            {**GUARDS, "Origin": "https://evil.example"},
            {**GUARDS, "Sec-Fetch-Site": "cross-site"},
            {**GUARDS, "Sec-Fetch-Site": "same-site"},
            {"X-Twinteams-Config": "1"},
            {"Origin": BASE},
        ):
            assert verify(headers=headers).status_code == 403, headers
        for host in ("localhost:8019", "[::1]:8019"):
            alternate = TestClient(app, base_url="http://" + host, client=("::1", 40000))
            login(target=alternate)
            assert alternate.get("/api/local-provider-config").status_code == 200
        assert not calls
        print(
            "PASS production/fixture disabled, strict loopback Host/Origin/port/proxy/fetch/header guards"
        )

        for body in (
            {"api_key": FAKE_KEY},
            {**BODY, "confirm_billed_call": 1},
            {**BODY, "confirm_billed_call": False},
            {**BODY, "save_for_later": "yes"},
            {**BODY, "model": "other-model"},
            {**BODY, "base_url": "https://evil.example"},
            {**BODY, "api_key": 42},
            {**BODY, "api_key": "short"},
            {**BODY, "api_key": FAKE_KEY + "\n"},
            [],
            {"api_key": {"key": FAKE_KEY}},
        ):
            assert verify(body=body).status_code == 400
        for raw in (
            b'{"api_key":"' + FAKE_KEY.encode() + b'", malformed',
            (
                '{"api_key":"'
                + FAKE_KEY
                + '","api_key":"'
                + FAKE_KEY
                + '","confirm_billed_call":true}'
            ).encode(),
            b"\xff\xfe",
        ):
            result = client.post(
                "/api/local-provider-config/verify",
                content=raw,
                headers={**GUARDS, "Content-Type": "application/json"},
            )
            assert result.status_code == 400
            safe(result)
        result = client.post(
            "/api/local-provider-config/verify",
            content=b"x" * 4097,
            headers={**GUARDS, "Content-Type": "application/json"},
        )
        assert result.status_code == 413
        safe(result)
        result = client.post(
            "/api/local-provider-config/verify",
            content=iter([b"x" * 2000] * 3),
            headers={**GUARDS, "Content-Type": "application/json"},
        )
        assert result.status_code == 413
        safe(result)
        for extra in (
            {"Content-Length": "invalid"},
            {"Content-Length": "999999"},
            {"Content-Length": "1"},
            {"Content-Type": "text/plain"},
        ):
            result = client.post(
                "/api/local-provider-config/verify",
                content=json.dumps(BODY),
                headers={**GUARDS, "Content-Type": "application/json", **extra},
            )
            assert result.status_code in {400, 413, 415}
            safe(result)
        assert not calls
        print(
            "PASS bounded raw JSON parsing, invalid/duplicate fields, explicit billed-call consent"
        )

        before = count_business_rows()
        page = client.get("/api/local-provider-config/save-page")
        assert page.status_code == 200
        safe(page)
        assert 'id="api-key" type="password"' in page.text
        assert 'id="confirm-save" type="checkbox">' in page.text
        assert str(pc.SECRET_PATH) in page.text
        assert "0600" in page.text and "OPENAI_API_KEY" in page.text
        assert "'unsafe-inline'" not in page.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        save_body = {"api_key": FAKE_KEY, "confirm_local_save": True}
        with (
            patch.object(pc, "_provider_client", side_effect=AssertionError("Save must not use HTTP")),
            patch.object(pc, "_validate_provider", side_effect=AssertionError("Save must not validate")),
        ):
            for bad_body in (
                {"api_key": FAKE_KEY},
                {**save_body, "confirm_local_save": 1},
                {**save_body, "confirm_local_save": False},
                {**save_body, "save_for_later": True},
                {**save_body, "confirm_billed_call": True},
                *({**save_body, "api_key": FAKE_KEY + suffix} for suffix in ("\n", "\r", " ", "$x", "`x`", "'", '"', ";x", "#x", "\\x")),
            ):
                result = client.post("/api/local-provider-config/save", json=bad_body, headers=GUARDS)
                assert result.status_code == 400
                safe(result)
                assert not pc.SECRET_DIRECTORY.exists()
            for bad_headers in ({"X-Twinteams-Config": "1"}, {**GUARDS, "Origin": "https://evil.example"}, {**GUARDS, "X-Forwarded-For": "127.0.0.1"}):
                result = client.post("/api/local-provider-config/save", json=save_body, headers=bad_headers)
                assert result.status_code == 403
                safe(result)
            result = client.post("/api/local-provider-config/save", json=save_body, headers=GUARDS)
            assert result.status_code == 200
            safe(result)
            assert result.json()["saved"] is True and result.json()["validated"] is False
            assert result.json()["enabled"] is False
            assert pc.SECRET_PATH.read_text() == "OPENAI_API_KEY=" + FAKE_KEY + "\n"
            assert stat.S_IMODE(pc.SECRET_DIRECTORY.stat().st_mode) == 0o700
            assert stat.S_IMODE(pc.SECRET_PATH.stat().st_mode) == 0o600
            assert list(pc.SECRET_DIRECTORY.iterdir()) == [pc.SECRET_PATH]
            assert not calls and before == count_business_rows()
            assert service.runtime is None and not service.model_configured
        clear_fake_store()
        print("PASS standalone AAA save page, explicit save-only consent, safe env alphabet/0600, no provider call or model enable")

        # Every file in this group is isolated under TEMP and contains fake data only.
        assert "safeSaved=false" in client.get("/api/local-provider-config/save-page").text
        with patch.object(pc, "_read_saved_key", side_effect=AssertionError("No premature secret read")):
            client.cookies.clear()
            assert verify_saved().status_code == 401
            login("DEV-BBB")
            assert verify_saved().status_code == 403
            login()
            before = count_business_rows()
            for bad in ({}, {"confirm_billed_call": False}, {"confirm_billed_call": 1},
                        {"confirm_billed_call": True, "api_key": FAKE_KEY},
                        {"confirm_billed_call": True, "save_for_later": True}, []):
                assert verify_saved(body=bad).status_code == 400
            for headers in ({"X-Twinteams-Config": "1"}, {**GUARDS, "Origin": "https://evil.example"},
                            {**GUARDS, "X-Forwarded-For": "127.0.0.1"}):
                assert verify_saved(headers=headers).status_code == 403
            assert client.get("/api/local-provider-config").status_code == 200
            page = client.get("/api/local-provider-config/save-page")
            assert page.status_code == 200 and "Reply with OK." in page.text
            assert "small billed API call" in page.text and "No app chats, history, memory or workspace content are sent." in page.text
            assert '<html lang="en">' in page.text and "Save OpenAI API key" in page.text
        for raw in (b'{"confirm_billed_call":true,"confirm_billed_call":true}', b"x" * 4097, b"\xff"):
            response = client.post("/api/local-provider-config/verify-saved", content=raw,
                                   headers={**GUARDS, "Content-Type": "application/json"})
            assert response.status_code in {400, 413}
            safe(response)
        fresh_gate()
        assert verify_saved().json()["error_code"] == "storage_unavailable"
        assert not calls
        pc._save_key(FAKE_KEY)
        original = pc.SECRET_PATH.read_bytes()
        with patch.object(pc, "_read_saved_key", side_effect=AssertionError("GET must not read saved key")):
            assert client.get("/api/local-provider-config").json()["saved"] is True
            page = client.get("/api/local-provider-config/save-page")
            assert "safeSaved=true" in page.text and FAKE_KEY not in page.text
        fresh_gate()
        with patch.object(pc, "_save_key", side_effect=AssertionError("Verification must never save")):
            result = verify_saved()
        assert result.status_code == 200 and result.json()["validated"] is True
        assert result.json()["enabled"] is False and len(calls) == 1
        assert "authorization" not in calls[-1].headers
        assert pc.SECRET_PATH.read_bytes() == original
        # The same attempt gate covers both validation endpoints, before a stored read.
        with patch.object(pc, "_read_saved_key", side_effect=AssertionError("Gate must precede read")):
            assert verify_saved().status_code == 429
        assert verify().status_code == 429
        for content in (b"OPENAI_API_KEY=bad\n", original + b"OTHER=value\n",
                        b"OPENAI_API_KEY=" + FAKE_KEY.encode() + b"\r\n",
                        b"OPENAI_API_KEY=" + FAKE_KEY.encode() + b";echo\n", b"x" * 1024):
            pc.SECRET_PATH.write_bytes(content)
            fresh_gate()
            assert verify_saved().json()["error_code"] == "storage_unavailable"
        pc.SECRET_PATH.write_bytes(original)
        pc.SECRET_PATH.chmod(0o644)
        fresh_gate()
        assert verify_saved().json()["error_code"] == "storage_unavailable"
        pc.SECRET_PATH.chmod(0o600)
        for transport, expected in (
            (lambda request: httpx.Response(401, stream=UnreadableErrorBody()), "provider_authentication_failed"),
            (lambda request: httpx.Response(404, stream=UnreadableErrorBody()), "model_unavailable"),
        ):
            fresh_gate()
            assert verify_saved(transport=transport).json()["error_code"] == expected
        for error, category in ((httpx.ConnectError(FAKE_KEY), "provider_connection_failed"),
                                (httpx.ConnectTimeout(FAKE_KEY), "provider_timeout")):
            fresh_gate()
            attempts = []
            def fail_saved(request, error=error):
                attempts.append(request)
                raise error
            assert verify_saved(transport=fail_saved).json()["error_code"] == category
            assert len(attempts) == 1 and not pc._attempt_gate.active
        pc.SECRET_PATH.unlink()
        fake_target = ROOT / "fake-saved-target.env"
        fake_target.write_bytes(original)
        fake_target.chmod(0o600)
        for link in ("symbolic", "hard"):
            if link == "symbolic":
                pc.SECRET_PATH.symlink_to(fake_target)
            else:
                os.link(fake_target, pc.SECRET_PATH)
            fresh_gate()
            assert verify_saved().json()["error_code"] == "storage_unavailable"
            pc.SECRET_PATH.unlink()
        assert fake_target.read_bytes() == original
        clear_fake_store()
        assert before == count_business_rows() and service.runtime is None and not service.model_configured
        assert FAKE_KEY not in "\n".join(logs.messages)
        calls.clear()
        fresh_gate()
        print("PASS saved-key explicit billed POST only, metadata-only GET, fixed mock call, safe bounded file read, shared gate, redaction and no save/enable/backlog")

        result = verify()
        assert result.status_code == 200 and result.json()["validated"] is True
        assert result.json()["saved"] is False and result.json()["enabled"] is False
        assert len(calls) == 1
        assert "authorization" not in calls[-1].headers
        assert before == count_business_rows()
        assert not pc.SECRET_DIRECTORY.exists()
        assert service.runtime is None and not service.model_configured
        assert FAKE_KEY not in "\n".join(logs.messages)
        assert verify().status_code == 429
        assert len(calls) == 1
        print(
            "PASS fixed official Responses payload, sanitized result/logs, no save/model enable/backlog writes, duplicate charge guard"
        )

        for code, expected, category in (
            (401, 422, "provider_authentication_failed"),
            (403, 422, "provider_authentication_failed"),
            (404, 422, "model_unavailable"),
            (429, 429, "provider_limit"),
            (503, 502, "provider_unavailable"),
            (307, 502, "provider_redirect_refused"),
            (400, 422, "provider_rejected"),
        ):
            fresh_gate()

            def error_transport(request, code=code):
                return httpx.Response(
                    code, headers={"Location": "https://evil.example"}, stream=UnreadableErrorBody()
                )

            result = verify(transport=error_transport)
            assert result.status_code == expected and result.json()["error_code"] == category
        for body in (
            response_body(status="failed", error={"message": FAKE_KEY}),
            response_body(model="other-model"),
            response_body(output=[]),
            {"error": FAKE_KEY},
        ):
            fresh_gate()
            result = verify(transport=lambda request, body=body: httpx.Response(200, json=body))
            assert result.status_code == 502 and result.json()["error_code"] == "provider_invalid_response"
        fresh_gate()
        result = verify(transport=lambda request: httpx.Response(200, content=FAKE_KEY.encode()))
        assert result.status_code == 502 and result.json()["error_code"] == "provider_invalid_response"
        fresh_gate()
        result = verify(transport=lambda request: httpx.Response(200, content=b"x" * 65537))
        assert result.status_code == 502 and result.json()["error_code"] == "provider_invalid_response"
        for error, expected, category in (
            (httpx.ConnectTimeout(FAKE_KEY), 504, "provider_timeout"),
            (httpx.ReadTimeout(FAKE_KEY), 504, "provider_timeout"),
            (httpx.WriteTimeout(FAKE_KEY), 504, "provider_timeout"),
            (httpx.PoolTimeout(FAKE_KEY), 504, "provider_timeout"),
            (httpx.ConnectError(FAKE_KEY), 502, "provider_connection_failed"),
            (httpx.ReadError(FAKE_KEY), 502, "provider_connection_failed"),
            (httpx.RemoteProtocolError(FAKE_KEY), 502, "provider_connection_failed"),
            (RuntimeError(FAKE_KEY), 502, "provider_connection_failed"),
        ):
            fresh_gate()
            failed_calls = []

            def failing_transport(request, error=error):
                failed_calls.append(request)
                raise error

            result = verify(transport=failing_transport)
            assert result.status_code == expected and result.json()["error_code"] == category
            assert len(failed_calls) == 1 and pc._attempt_gate.active is False
            assert result.json()["saved"] is False and result.json()["enabled"] is False
            assert not pc.SECRET_DIRECTORY.exists()
        fresh_gate()
        timeout_calls = []

        async def total_timeout_transport(request):
            timeout_calls.append(request)
            await asyncio.sleep(1)
            return httpx.Response(200, json=response_body())

        with patch.object(pc, "PROVIDER_TIMEOUT_SECONDS", 0.01):
            result = verify(transport=total_timeout_transport)
        assert result.status_code == 504 and result.json()["error_code"] == "provider_timeout"
        assert len(timeout_calls) == 1 and pc._attempt_gate.active is False
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1",
            "POST",
            "/api/local-provider-config/verify?accidental=" + FAKE_KEY,
            "1.1",
            400,
        )
        assert FAKE_KEY not in "\n".join(logs.messages)
        print(
            "PASS distinct rejected/invalid-response/connection-failed/phase and total timeout codes; single attempts never echo or log fake credentials"
        )

        fresh_gate()
        entered, release = threading.Event(), threading.Event()

        async def waiting_transport(request):
            entered.set()
            await asyncio.to_thread(release.wait, 5)
            return httpx.Response(200, json=response_body())

        with (
            patch.object(pc, "_provider_client", lambda: fake_client(waiting_transport)),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            pending = pool.submit(
                client.post, "/api/local-provider-config/verify", json=BODY, headers=GUARDS
            )
            assert entered.wait(3)
            result = client.post("/api/local-provider-config/verify", json=BODY, headers=GUARDS)
            assert (
                result.status_code == 409
                and result.json()["error_code"] == "validation_in_progress"
            )
            release.set()
            assert pending.result(timeout=5).status_code == 200
        gate = pc._AttemptGate(interval=0, hourly_limit=2)
        assert gate.begin() is None
        gate.finish()
        assert gate.begin() is None
        gate.finish()
        assert gate.begin() == "validation_rate_limited"
        print("PASS one concurrent validation and bounded hourly attempt budget")

        fresh_gate()
        result = verify(
            body={**BODY, "save_for_later": True},
            transport=lambda request: httpx.Response(401, json={"error": FAKE_KEY}),
        )
        assert result.status_code == 422 and not pc.SECRET_DIRECTORY.exists()
        fresh_gate()
        result = verify(body={**BODY, "save_for_later": True})
        assert result.status_code == 200 and result.json()["saved"] is True
        assert stat.S_IMODE(pc.SECRET_DIRECTORY.stat().st_mode) == 0o700
        assert stat.S_IMODE(pc.SECRET_PATH.stat().st_mode) == 0o600
        # This reads only the fake credential written to this check's temporary file.
        assert pc.SECRET_PATH.read_text() == "OPENAI_API_KEY=" + FAKE_KEY + "\n"
        result = client.get("/api/local-provider-config")
        safe(result)
        assert result.json()["saved"] is True and result.json()["enabled"] is False
        assert before == count_business_rows() and service.runtime is None
        real_open = os.open

        def no_credential_read(path, *args, **kwargs):
            assert path != "openai.env", "GET metadata must not open the credential file"
            return real_open(path, *args, **kwargs)

        with patch.object(pc.os, "open", no_credential_read):
            assert client.get("/api/local-provider-config").json()["saved"] is True
        # Atomic-save failure preserves the previous fake file and leaves no temp file.
        original = pc.SECRET_PATH.read_bytes()
        with patch.object(pc.os, "replace", side_effect=OSError(FAKE_KEY)):
            try:
                pc._save_key(FAKE_KEY)
            except OSError:
                pass
            else:
                raise AssertionError("Expected simulated atomic replace failure")
        assert pc.SECRET_PATH.read_bytes() == original
        assert list(pc.SECRET_DIRECTORY.iterdir()) == [pc.SECRET_PATH]
        fresh_gate()
        with patch.object(pc, "_save_key", side_effect=OSError(FAKE_KEY)):
            result = verify(body={**BODY, "save_for_later": True})
        assert (
            result.status_code == 500
            and result.json()["validated"] is True
            and result.json()["saved"] is False
        )
        safe(result)
        print(
            "PASS opt-in save only after success, private 0700/0600 atomic file, metadata never reads key, no automatic model use"
        )

        pc.SECRET_PATH.chmod(0o644)
        fresh_gate()
        result = verify(body={**BODY, "save_for_later": True})
        assert result.status_code == 409
        pc.SECRET_PATH.chmod(0o600)
        pc.SECRET_PATH.unlink()
        target = ROOT / "untouched.txt"
        target.write_text("unchanged")
        pc.SECRET_PATH.symlink_to(target)
        assert verify(body={**BODY, "save_for_later": True}).status_code == 409
        assert target.read_text() == "unchanged"
        pc.SECRET_PATH.unlink()
        linked = ROOT / "linked-fake.json"
        linked.write_text("fake-placeholder")
        linked.chmod(0o600)
        os.link(linked, pc.SECRET_PATH)
        assert verify(body={**BODY, "save_for_later": True}).status_code == 409
        pc.SECRET_PATH.unlink()
        pc.SECRET_DIRECTORY.chmod(0o755)
        assert verify(body={**BODY, "save_for_later": True}).status_code == 409
        pc.SECRET_DIRECTORY.chmod(0o700)
        clear_fake_store()
        other_directory = ROOT / "untouched-directory"
        other_directory.mkdir()
        pc.SECRET_DIRECTORY.symlink_to(other_directory, target_is_directory=True)
        assert verify(body={**BODY, "save_for_later": True}).status_code == 409
        assert not list(other_directory.iterdir())
        clear_fake_store()
        assert not service.model_configured and service.runtime is None
        assert FAKE_KEY not in "\n".join(logs.messages)
        print(
            "PASS unsafe modes/symlinks/hardlinks refused; no real key, provider call, normal DB or credential file accessed"
        )
    engine.dispose()
    TEMP.cleanup()


if __name__ == "__main__":
    main()
