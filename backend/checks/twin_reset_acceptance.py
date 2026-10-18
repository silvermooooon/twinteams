"""Real dual-API/WS reset acceptance in a random PG schema; no worker/provider."""
import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import websockets
from cryptography.fernet import Fernet

from checks.local_stack import BACKEND, environment, read_env
from checks.personal_twin_acceptance import free_port
from checks.postgres import TestDatabase

FIXTURE = r'''
import asyncio
from agent_runtime.memory import MemorySource
from app import personal
from app.service import service
service.initialize(create_schema=True)
service.send_message("owner-alex","private-alex","Old private fixture content","reset-old")
service.send_message("owner-maya","group-team","Public fixture remains unchanged","reset-group")
async def seed():
    for owner in ("owner-alex","owner-maya"):
        await personal.own_memory_store(owner).write("MEMORY",owner+" isolated memory",
            expected_version=None,sources=[MemorySource("isolated-source","source-event")])
asyncio.run(seed())
'''


def group_hash(database):
    with psycopg.connect(database.url) as db:
        rows = [row[0] for row in db.execute("SELECT row_to_json(t)::text FROM (SELECT * FROM tt_messages WHERE chat_id='group-team' ORDER BY seq) t")]
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


async def verify(urls, database):
    count = 0
    def check(value, message):
        nonlocal count
        assert value, message
        count += 1
    before_group = group_hash(database)
    async with httpx.AsyncClient(base_url=urls[0], trust_env=False) as own, httpx.AsyncClient(base_url=urls[1], trust_env=False) as other:
        check((await own.post("/api/auth/login", json={"username": "DEV-AAA"})).status_code == 200, "Owner login")
        check((await other.post("/api/auth/login", json={"username": "DEV-BBB"})).status_code == 200, "Other owner login")
        async with httpx.AsyncClient(base_url=urls[0], trust_env=False) as anon:
            check((await anon.post("/api/me/twin/reset", json={"expected_generation": 0, "idempotency_key": uuid4().hex})).status_code == 401, "Anonymous reset rejected")
        check((await other.get("/api/chats/private-alex")).status_code == 403, "Other private chat denied")
        check((await other.post("/api/me/twin/reset", json={"owner_id": "owner-alex", "expected_generation": 0, "idempotency_key": uuid4().hex})).status_code == 422, "Forged reset owner rejected")
        profile = (await own.get("/api/me/twin")).json()["actor"]
        soul = (await own.get("/api/me/twin/soul")).json()
        other_memory = (await other.get("/api/me/twin/memory/entry", params={"key": "MEMORY"})).json()
        state = (await own.get("/api/me/twin/reset")).json()
        check(state["generation"] == 0, "Initial generation")
        cookies = "; ".join(f"{k}={v}" for k,v in own.cookies.items())
        other_cookies = "; ".join(f"{k}={v}" for k,v in other.cookies.items())
        ws = urls[1].replace("http://", "ws://") + "/api/ws"
        key = uuid4().hex
        body = {"expected_generation": 0, "idempotency_key": key}
        async with websockets.connect(ws, origin=urls[1], proxy=None, additional_headers={"Cookie": cookies}) as own_ws, websockets.connect(ws, origin=urls[1], proxy=None, additional_headers={"Cookie": other_cookies}) as other_ws:
            await own_ws.send(json.dumps({"v": 1, "type": "subscribe", "chat_id": "private-alex"}))
            await asyncio.wait_for(own_ws.recv(), 3)
            response = await own.post("/api/me/twin/reset", json=body)
            result = response.json()
            check(response.status_code == 200 and result["status"] == "completed" and result["generation"] == 1, "Reset completed")
            events = []
            for _ in range(8):
                event = json.loads(await asyncio.wait_for(own_ws.recv(), 3))
                events.append(event)
                if any(e.get("type") == "changed" for e in events) and any(e.get("type") == "personal_changed" and e.get("section") == "reset" for e in events):
                    break
            check(any(e.get("type") == "changed" for e in events), "Private revision crosses API replicas")
            check(any(e.get("section") == "reset" for e in events), "Owner reset event crosses replicas")
            try:
                await asyncio.wait_for(other_ws.recv(), 0.3)
            except TimeoutError:
                check(True, "Reset event not sent to another owner")
            else:
                raise AssertionError("Reset event leaked to another owner")
        check((await own.get(urls[1]+"/api/chats/private-alex")).json()["messages"] == [], "Private history empty across replicas")
        check((await own.get("/api/me/twin/memory")).json()["items"] == [], "Owner memory empty")
        check((await own.get("/api/me/twin/soul")).json() == soul, "Soul preserved")
        check((await own.get("/api/me/twin")).json()["actor"] == profile, "Name/avatar/settings preserved")
        check((await other.get("/api/me/twin/memory/entry", params={"key": "MEMORY"})).json() == other_memory, "Other owner memory preserved")
        check(group_hash(database) == before_group, "Group message SQL hash unchanged")
        # New generation work and memory must survive replay of the old reset key.
        new = await own.post("/api/chats/private-alex/messages", json={"body": "New generation message", "client_request_key": uuid4().hex})
        check(new.status_code == 200, "New private work accepted")
        memory = await own.put("/api/me/twin/memory/entry", params={"key": "after-reset"}, json={"content": "New memory survives replay", "expected_version": None})
        check(memory.status_code == 200, "New memory accepted")
        check((await own.post(urls[1]+"/api/me/twin/reset", json=body)).json() == result, "Reset replay returns same result")
        check((await own.get("/api/chats/private-alex")).json()["messages"][-1]["body"] == "New generation message", "Reset replay retains later message")
        check((await own.get("/api/me/twin/memory/entry", params={"key": "after-reset"})).json() == memory.json(), "Reset replay retains later memory")
        check((await own.post("/api/me/twin/reset", json={"expected_generation": 0, "idempotency_key": uuid4().hex})).status_code == 409, "Stale reset tab rejected")
        check((await other.get("/api/me/twin/reset")).json()["generation"] == 0, "Other owner generation unchanged")
        check((await own.post("/api/auth/logout")).status_code == 200, "Owner logout")
        check((await other.post("/api/auth/logout")).status_code == 200, "Other logout")
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-marker", type=Path)
    args = parser.parse_args()
    os.environ["TWINTEAMS_TEST_DB_URL"] = read_env(BACKEND/".env")["DATABASE_URL"]
    database, children = TestDatabase(), []
    try:
        with tempfile.TemporaryDirectory(prefix="tt-reset-http-") as temp:
            root = Path(temp)
            env = environment("api")
            env.update(DATABASE_URL=database.url, AGENT_DB_URL=database.url, TWINTEAMS_REDIS_PREFIX="tt-reset-"+uuid4().hex,
                AGENT_MEMORY_DIR=str(root/"memory"), AGENT_SECRETS_DIR=str(root/"secrets"),
                AGENT_SECRETS_KEY=Fernet.generate_key().decode(), TWINTEAMS_WORKSPACE_ROOT=str(root/"workspace"))
            env.pop("OPENAI_API_KEY", None)
            subprocess.run([sys.executable,"-c",FIXTURE],cwd=BACKEND,env=env,capture_output=True,text=True,check=True)
            urls = []
            for _ in range(2):
                port = free_port()
                urls.append(f"http://127.0.0.1:{port}")
                with (root/f"api-{port}.log").open("w") as log:
                    children.append(subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app","--host","127.0.0.1","--port",str(port)],cwd=BACKEND,env=env,stdout=log,stderr=subprocess.STDOUT))
            for url in urls:
                deadline = time.monotonic()+15
                while time.monotonic()<deadline:
                    try:
                        if httpx.get(url+"/health",trust_env=False,timeout=0.5).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Disposable reset API did not become ready")
            count = asyncio.run(verify(urls,database))
            if args.browser_marker:
                args.browser_marker.write_text(json.dumps({"urls":urls,"username":"DEV-AAA"}))
                args.browser_marker.chmod(0o600)
                print("Isolated reset browser fixture ready: "+str(args.browser_marker),flush=True)
                deadline=time.monotonic()+600
                while args.browser_marker.exists() and time.monotonic()<deadline:
                    time.sleep(0.5)
                args.browser_marker.unlink(missing_ok=True)
            print(f"PASS: {count} real reset HTTP/WS assertions; no Worker/LLM; disposable state cleaned")
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
        database.close()


if __name__ == "__main__":
    main()
