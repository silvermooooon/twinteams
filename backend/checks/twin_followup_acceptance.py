"""Real HTTP/WS follow-up acceptance; disposable PG, two APIs, no Worker/LLM.

Run from backend: .venv/bin/python -m checks.twin_followup_acceptance
"""
from __future__ import annotations

import asyncio
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

from checks.local_stack import BACKEND, environment, read_env
from checks.personal_twin_acceptance import free_port
from checks.postgres import TestDatabase

FIXTURE = r'''
import asyncio,json
from app.service import service
service.initialize(create_schema=True)
service.send_message("owner-maya","private-maya","PRIVATE_FIXTURE_HISTORY_NEVER_PUBLIC","fixture-private")
origin=service.send_message("owner-maya","private-maya","Ask AAA whether Friday works","fixture-question")
context=service.get_runtime_context(origin["request"]["id"])
context["operation_id"]="fixture-send-question"
sent=asyncio.run(service.runtime_tool("send_chat_message",{
    "chat_id":"group-alex-maya","body":"AAA, does Friday work?",
    "source_message_ids":[origin["message"]["id"]],"wait_for_reply":True},context))
print(json.dumps({"message_id":sent["message_id"],"chain_id":origin["request"]["causal_chain_id"]}))
'''


async def verify(urls, fixture, database):
    count = 0

    def check(condition, message):
        nonlocal count
        assert condition, message
        count += 1

    async with httpx.AsyncClient(base_url=urls[0], trust_env=False) as bbb, httpx.AsyncClient(base_url=urls[1], trust_env=False) as aaa:
        check((await bbb.post("/api/auth/login", json={"username": "DEV-BBB"})).status_code == 200, "BBB login")
        check((await aaa.post("/api/auth/login", json={"username": "DEV-AAA"})).status_code == 200, "AAA login")
        group = await aaa.get("/api/chats/group-alex-maya")
        check(group.status_code == 200, "AAA target access")
        visible = next(row for row in group.json()["messages"] if row["id"] == fixture["message_id"])
        check(visible["body"] == "AAA, does Friday work?" and visible["author_id"] == "twin-maya", "Public owner-voice body visible")
        check("PRIVATE_FIXTURE_HISTORY_NEVER_PUBLIC" not in group.text, "Private history not exposed")
        check((await aaa.get("/api/chats/private-maya")).status_code == 403, "Private history access remains denied")
        cookie = "; ".join(f"{key}={value}" for key, value in bbb.cookies.items())
        async with websockets.connect(urls[0].replace("http://", "ws://") + "/api/ws", origin=urls[0], proxy=None,
                                      additional_headers={"Cookie": cookie}) as socket:
            await socket.send(json.dumps({"v": 1, "type": "subscribe", "chat_id": "private-maya"}))
            first = json.loads(await asyncio.wait_for(socket.recv(), 3))
            check(first["type"] == "changed" and first["chat_id"] == "private-maya", "Owner private scope subscribed")
            response = await aaa.post("/api/chats/group-alex-maya/messages", json={
                "body": "Friday works", "client_request_key": "fixture-explicit-reply",
                "reply_to_message_id": fixture["message_id"]})
            check(response.status_code == 200, "Explicit reply HTTP accepted")
            reply_id = response.json()["message"]["id"]
            changed = json.loads(await asyncio.wait_for(socket.recv(), 3))
            check(changed["type"] == "changed" and changed["chat_id"] == "private-maya" and changed["revision"] > first["revision"],
                  "Cross-API reply produces owner private invalidation")
            check("Friday works" not in json.dumps(changed), "WS carries no message content")
        # A reconnect/read sees the same canonical queue even with no Worker.
        detail = await bbb.get("/api/chats/private-maya")
        check(detail.status_code == 200, "Owner canonical private state readable")
        with psycopg.connect(database.url) as connection:
            row = connection.execute("SELECT status,reply_message_id,continuation_request_id,causal_chain_id,due_at FROM tt_twin_followups WHERE sent_message_id=%s",
                                     [fixture["message_id"]]).fetchone()
            check(row[0] == "replied" and row[1] == reply_id and row[2] and row[3] == fixture["chain_id"], "Reply settled once on original chain")
            request = connection.execute("SELECT chat_id,source,status,causal_chain_id FROM tt_reply_requests WHERE id=%s", [row[2]]).fetchone()
            check(request == ("private-maya", "followup_result", "queued", fixture["chain_id"]), "Existing FIFO delivery persists without Worker")
            check(any(item["id"] == row[2] for item in detail.json()["requests"]), "HTTP exposes queued owner continuation")
            original_deadline = row[4]
            repeated = await aaa.post("/api/chats/group-alex-maya/messages", json={
                "body": "Friday works", "client_request_key": "fixture-explicit-reply",
                "reply_to_message_id": fixture["message_id"]})
            check(repeated.status_code == 200 and repeated.json()["message"]["id"] == reply_id, "HTTP reply retry is idempotent")
            check(connection.execute("SELECT count(*) FROM tt_reply_requests WHERE source='followup_result' AND causal_chain_id=%s", [fixture["chain_id"]]).fetchone()[0] == 1,
                  "Only one continuation")
            check(connection.execute("SELECT due_at FROM tt_twin_followups WHERE sent_message_id=%s", [fixture["message_id"]]).fetchone()[0] == original_deadline,
                  "Retry preserves deadline")
            check(connection.execute("SELECT count(*) FROM tt_dispatch_outbox WHERE request_id=%s", [row[2]]).fetchone()[0] == 1, "One durable dispatch")
            check(connection.execute("SELECT count(*) FROM agent_model_usage").fetchone()[0] == 0, "No model invocation")
        await bbb.post("/api/auth/logout")
        await aaa.post("/api/auth/logout")
    return count


def main():
    os.environ["TWINTEAMS_TEST_DB_URL"] = read_env(BACKEND / ".env")["DATABASE_URL"]
    database = TestDatabase()
    children = []
    try:
        with tempfile.TemporaryDirectory(prefix="tt-followup-http-") as temporary:
            root = Path(temporary)
            env = environment("api")
            env.update(DATABASE_URL=database.url, AGENT_DB_URL=database.url,
                       TWINTEAMS_REDIS_PREFIX="tt-followup-check-" + uuid4().hex,
                       AGENT_MEMORY_DIR=str(root / "memory"), AGENT_SECRETS_DIR=str(root / "secrets"),
                       TWINTEAMS_WORKSPACE_ROOT=str(root / "workspace"))
            for name in ("OPENAI_API_KEY", "AGENT_SECRETS_KEY"):
                env.pop(name, None)
            initialized = subprocess.run([sys.executable, "-c", FIXTURE], cwd=BACKEND, env=env,
                                         capture_output=True, text=True, check=True)
            fixture = json.loads(initialized.stdout.strip().splitlines()[-1])
            ports = [free_port()]
            while len(ports) < 2:
                candidate = free_port()
                if candidate not in ports:
                    ports.append(candidate)
            for port in ports:
                with (root / f"api-{port}.log").open("w") as log:
                    children.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                        cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT))
            urls = [f"http://127.0.0.1:{port}" for port in ports]
            for url in urls:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    try:
                        if httpx.get(url + "/health", trust_env=False, timeout=0.5).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Isolated API startup failed")
            count = asyncio.run(verify(urls, fixture, database))
            print(f"PASS: {count} real follow-up HTTP/WS/PG assertions; no Worker/LLM; disposable state cleaned")
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
        database.close()


if __name__ == "__main__":
    main()
