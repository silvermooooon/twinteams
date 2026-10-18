"""Real HTTP/WS personal Twin acceptance in a disposable PostgreSQL schema.

Run from backend: .venv/bin/python -m checks.personal_twin_acceptance
No worker or model call is started. All filesystem/Redis state is isolated.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
import websockets
from cryptography.fernet import Fernet
from PIL import Image

from checks.local_stack import BACKEND, environment, read_env
from checks.postgres import TestDatabase


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


FIXTURE = r'''
import asyncio, json
from pathlib import Path
from agent_runtime.memory import MemorySource
from app import personal, credentials
from app.service import service
service.initialize(create_schema=True)
root = personal.resolve_twin_workspace("owner-alex") / "skills" / "acceptance-demo"
(root / "references").mkdir(parents=True)
(root / "SKILL.md").write_text("---\ndescription: Isolated acceptance skill\n---\nUse short sentences.")
(root / "references/style.md").write_text("Short sentences")
async def memory():
    store = personal.own_memory_store("owner-alex")
    for number in range(3):
        await store.write(f"acceptance/{number}", f"Isolated acceptance memory {number}",
            sources=(MemorySource("acceptance-session", "acceptance-event"),), expected_version=None)
asyncio.run(memory())
request = service.send_message("owner-alex", "private-alex", "Offline acceptance only", "acceptance-origin")["request"]
context = service.get_runtime_context(request["id"])
context["operation_id"] = "acceptance-credential"
result = credentials.create_request(service, {"name":"acceptance-token", "keynames":["FAKE_TOKEN"],
    "description":"Isolated fake value", "reason":"Offline acceptance only"}, context)
print(json.dumps({"credential_request_id":result["id"]}))
'''


async def verify(first, second, request_id):
    count = 0

    def check(condition, message):
        nonlocal count
        assert condition, message
        count += 1

    async with httpx.AsyncClient(base_url=first) as owner, httpx.AsyncClient(base_url=second) as other:
        check((await owner.post("/api/auth/login", json={"username": "DEV-AAA"})).status_code == 200, "Owner login")
        check((await other.post("/api/auth/login", json={"username": "DEV-BBB"})).status_code == 200, "Other owner login")
        async with httpx.AsyncClient(base_url=first) as anonymous:
            for path in ("", "/runtime", "/skills", "/memory", "/credentials", "/credential-requests", "/soul", "/memory/status"):
                check((await anonymous.get("/api/me/twin" + path)).status_code == 401, "Anonymous denied")
        owner_cookie = "; ".join(f"{k}={v}" for k, v in owner.cookies.items())
        other_cookie = "; ".join(f"{k}={v}" for k, v in other.cookies.items())
        # Both sockets use API #2; mutations use API #1, exercising Redis delivery.
        ws_url = second.replace("http://", "ws://") + "/api/ws"
        async with websockets.connect(ws_url, origin=second, proxy=None, additional_headers={"Cookie": owner_cookie}) as own_ws, websockets.connect(ws_url, origin=second, proxy=None, additional_headers={"Cookie": other_cookie}) as other_ws:
            changed = (await owner.patch("/api/me/twin", json={"name": "Acceptance Twin", "avatar_preset": "orbit"})).json()
            check(changed["name"] == "Acceptance Twin" and changed["avatar_preset"] == "orbit", "Profile update")
            owner_events = [json.loads(await asyncio.wait_for(own_ws.recv(), 3)) for _ in range(2)]
            check({event["type"] for event in owner_events} == {"actor_changed", "personal_changed"}, "Owner profile invalidations")
            event = json.loads(await asyncio.wait_for(other_ws.recv(), 3))
            check(event["type"] == "actor_changed", "Public actor invalidation")
            try:
                await asyncio.wait_for(other_ws.recv(), 0.3)
            except TimeoutError:
                check(True, "Personal invalidation does not leak to another owner")
            else:
                raise AssertionError("Personal invalidation leaked to another owner")
        check((await owner.get(second + "/api/me/twin")).json()["actor"]["name"] == "Acceptance Twin", "Profile persists across API processes")
        check((await other.get("/api/me/twin")).json()["actor"]["name"] != "Acceptance Twin", "Profile owner scope")
        buffer = BytesIO()
        Image.new("RGB", (600, 300), "purple").save(buffer, format="PNG")
        avatar = (await owner.post("/api/me/twin/avatar", content=buffer.getvalue())).json()
        image = await owner.get(avatar["avatar_url"])
        check(image.status_code == 200 and Image.open(BytesIO(image.content)).size == (256, 256), "Avatar normalized")
        check((await owner.get(avatar["avatar_url"], headers={"If-None-Match": image.headers["etag"]})).status_code == 304, "Avatar conditional cache")
        check((await owner.post("/api/me/twin/avatar", content=b"invalid")).status_code == 422, "Bad image rejected")
        check((await owner.delete("/api/me/twin/avatar")).json()["avatar_url"] is None, "Avatar removed")
        skills = (await owner.get("/api/me/twin/skills")).json()["items"]
        check(len(skills) == 1 and not skills[0]["enabled"], "Skill discovery")
        check((await owner.patch("/api/me/twin/skills/acceptance-demo", json={"enabled": True})).json()["enabled"], "Skill enable")
        check((await owner.get("/api/me/twin/skills/detail", params={"name": "acceptance-demo"})).json()["content"].endswith("Use short sentences."), "Skill detail")
        check((await other.get("/api/me/twin/skills")).json()["items"] == [], "Skill owner scope")
        page = (await owner.get("/api/me/twin/memory", params={"limit": 2})).json()
        check(len(page["items"]) == 2 and page["next_cursor"] == "acceptance/1", "Memory page one")
        page2 = (await owner.get("/api/me/twin/memory", params={"limit": 2, "after": page["next_cursor"]})).json()
        check(len(page2["items"]) == 1 and page2["next_cursor"] is None, "Memory page two")
        check(len((await owner.get("/api/me/twin/memory/search", params={"q": "Isolated acceptance"})).json()["items"]) == 3, "Memory search")
        check((await other.get("/api/me/twin/memory/entry", params={"key": "acceptance/0"})).status_code == 404, "Memory owner scope")
        check((await owner.post("/api/me/twin/memory", json={})).status_code == 405, "Unsupported memory route rejected")
        soul = (await owner.get("/api/me/twin/soul")).json()
        check("SOUL.md" in soul["content"] and soul["version"], "Soul initializes canonical default file")
        async with websockets.connect(ws_url, origin=second, proxy=None, additional_headers={"Cookie": owner_cookie}) as own_ws, websockets.connect(ws_url, origin=second, proxy=None, additional_headers={"Cookie": other_cookie}) as other_ws:
            saved = (await owner.put("/api/me/twin/soul", json={"content": "Use concise friendly answers.", "expected_version": soul["version"]})).json()
            event = json.loads(await asyncio.wait_for(own_ws.recv(), 3))
            check(event["type"] == "personal_changed" and event["section"] == "soul", "Soul owner WS invalidation crosses API")
            try:
                await asyncio.wait_for(other_ws.recv(), 0.3)
            except TimeoutError:
                check(True, "Soul invalidation owner-only")
            else:
                raise AssertionError("Soul invalidation leaked")
        check((await owner.get(second + "/api/me/twin/soul")).json() == saved, "Soul shared by API replicas")
        stale = await owner.put(second + "/api/me/twin/soul", json={"content": "Stale overwrite", "expected_version": soul["version"]})
        check(stale.status_code == 409 and stale.json()["current"] == saved, "Soul CAS conflict returns current")
        check("Use concise friendly answers." not in (await other.get("/api/me/twin/soul")).json()["content"], "Soul owner scope")
        check((await owner.delete("/api/me/twin/soul", params={"expected_version": saved["version"]})).json()["content"] == "", "Soul explicit clear")
        params = {"key": "acceptance/crud"}
        created = (await owner.put("/api/me/twin/memory/entry", params=params, json={"content": "Manual owner fact", "expected_version": None})).json()
        check(len(created["version"]) == 40 and created["sources"][0]["session_id"] == "twin-config:owner-alex", "Memory Git version and genuine edit audit source")
        check((await owner.get(second + "/api/me/twin/memory/entry", params=params)).json() == created, "Memory write persists across API")
        stale = await owner.put(second + "/api/me/twin/memory/entry", params=params, json={"content": "Stale overwrite", "expected_version": None})
        check(stale.status_code == 409 and stale.json()["current"] == created, "Memory create CAS conflict")
        changed = (await owner.put("/api/me/twin/memory/entry", params=params, json={"content": "Owner correction", "expected_version": created["version"]})).json()
        check(changed["version"] != created["version"], "Memory edit publishes new Git commit")
        check((await owner.delete(second + "/api/me/twin/memory/entry", params={**params, "expected_version": created["version"]})).status_code == 409, "Memory stale delete rejected")
        check((await owner.delete("/api/me/twin/memory/entry", params={**params, "expected_version": changed["version"]})).json()["deleted"], "Memory owner delete")
        check((await owner.get(second + "/api/me/twin/memory/entry", params=params)).status_code == 404, "Memory deletion shared by replicas")
        check((await owner.get("/api/me/twin/memory/status")).json()["mode"] == "git", "Memory status reflects Git backend")
        fake = "fake-value-" + uuid4().hex
        response = await owner.put("/api/me/twin/credentials/acceptance-token", json={"values": {"FAKE_TOKEN": fake}, "description": "Fake test"})
        check(response.status_code == 200 and fake not in response.text, "Credential create excludes value")
        check(response.headers["cache-control"] == "no-store", "Credential response cache disabled")
        response = await owner.get(second + "/api/me/twin/credentials")
        check(len(response.json()["credentials"]) == 1 and fake not in response.text, "Credential persists across API processes")
        check((await other.get("/api/me/twin/credentials")).json()["credentials"] == [], "Credential owner scope")
        response = await owner.put("/api/me/twin/credentials/invalid", json={"value": {"unexpected": fake}})
        check(response.status_code == 422 and fake not in response.text, "Credential invalid input excludes value")
        check((await other.post(f"/api/me/twin/credential-requests/{request_id}/complete", json={"existing_name": "acceptance-token"})).status_code == 404, "Credential request owner scope")
        response = await owner.post(f"/api/me/twin/credential-requests/{request_id}/complete", json={"existing_name": "acceptance-token"})
        completed = response.json()
        check(response.status_code == 200 and completed["status"] == "fulfilled" and completed["continuation_request_id"], "Credential request queues continuation without worker")
        repeat = (await owner.post(f"/api/me/twin/credential-requests/{request_id}/complete", json={"existing_name": "acceptance-token"})).json()
        check(repeat["continuation_request_id"] == completed["continuation_request_id"], "Credential completion idempotent")
        check((await owner.delete("/api/me/twin/credentials/acceptance-token")).json()["deleted"], "Credential delete")
        check((await owner.get("/api/me/twin/credentials")).json()["credentials"] == [], "Credential deletion persisted")
        check((await owner.get("/api/me/twin/runtime")).json()["worker"]["online"] is False, "No fictitious worker status")
    return count, fake


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-marker", type=Path, help="Keep isolated APIs up until this marker is deleted (maximum 10 minutes)")
    args = parser.parse_args()
    private = read_env(BACKEND / ".env")
    os.environ["TWINTEAMS_TEST_DB_URL"] = private["DATABASE_URL"]
    database = TestDatabase()
    children = []
    try:
        with tempfile.TemporaryDirectory(prefix="tt-personal-http-") as temporary:
            root = Path(temporary)
            env = environment("api")
            env.update(DATABASE_URL=database.url, AGENT_DB_URL=database.url,
                       TWINTEAMS_REDIS_PREFIX="tt-acceptance-" + uuid4().hex,
                       AGENT_MEMORY_DIR=str(root / "memory"), AGENT_SECRETS_DIR=str(root / "secrets"),
                       AGENT_SECRETS_KEY=Fernet.generate_key().decode(), TWINTEAMS_WORKSPACE_ROOT=str(root / "workspace"))
            env.pop("OPENAI_API_KEY", None)
            result = subprocess.run([sys.executable, "-c", FIXTURE], cwd=BACKEND, env=env,
                                    capture_output=True, text=True, check=True)
            fixture = json.loads(result.stdout.strip().splitlines()[-1])
            ports = [free_port(), free_port()]
            logs = []
            for port in ports:
                log = root / f"api-{port}.log"
                logs.append(log)
                with log.open("w") as stream:
                    children.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                        cwd=BACKEND, env=env, stdout=stream, stderr=subprocess.STDOUT))
            urls = [f"http://127.0.0.1:{port}" for port in ports]
            for url in urls:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    try:
                        if httpx.get(url + "/health", timeout=0.5).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Isolated API readiness failed")
            count, fake = asyncio.run(verify(*urls, fixture["credential_request_id"]))
            if args.browser_marker:
                args.browser_marker.write_text(json.dumps({"urls": urls, "username": "DEV-AAA"}))
                args.browser_marker.chmod(0o600)
                print("Isolated browser fixture ready: " + str(args.browser_marker), flush=True)
                deadline = time.monotonic() + 600
                while args.browser_marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.5)
                args.browser_marker.unlink(missing_ok=True)
            for child in children:
                child.terminate()
            for child in children:
                child.wait(timeout=10)
            assert all(fake not in log.read_text() for log in logs), "Submitted value reached an API log"
            print(f"PASS: {count + 1} real HTTP/WS assertions; no model call; disposable state cleaned")
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
        database.close()


if __name__ == "__main__":
    main()
