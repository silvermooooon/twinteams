"""Independent real PG/dual API/WS participant acceptance; no Worker or real provider."""

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

SEED = """
import json
from app.service import service
from app.models import Chat
service.initialize(create_schema=True)
with service.transaction() as db:
    service.append_message(db,db.get(Chat,'private-alex'),author_id='owner-alex',kind='human',body='PRIVATE_REFERENCE_DO_NOT_SHARE_HISTORY')
    msg=service.append_message(db,db.get(Chat,'group-alex-maya'),author_id='owner-maya',kind='human',body='Please coordinate a group task')
    mid=msg.id
request=service.manual_request('owner-maya','group-alex-maya',mid,'twin-alex','platform-acceptance')
context=service.get_runtime_context(request['id'])
context.update(runtime_session_id='isolated-group-session',runtime_run_id='isolated-group-run')
print(json.dumps({'context':context,'request_id':request['id']}))
"""
DRIVER = """
import asyncio,json,sys
from app.service import service
from app.notifications import notifications
async def run():
    await notifications.start()
    try:
        name,args,context=json.loads(sys.argv[1])
        result=await service.runtime_tool(name,args,context)
        await asyncio.sleep(.15)
        print(json.dumps(result))
    finally:
        await notifications.close()
asyncio.run(run())
"""


def invoke(env, fixture, name, args, operation=None):
    context = {**fixture["context"], "operation_id": operation or uuid4().hex}
    result = subprocess.run(
        [sys.executable, "-c", DRIVER, json.dumps([name, args, context])],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


async def event(socket, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = json.loads(
            await asyncio.wait_for(socket.recv(), max(0.01, deadline - time.monotonic()))
        )
        if predicate(value):
            return value
    raise TimeoutError("Expected owner-scoped event missing")


async def quiet(socket):
    while True:
        try:
            await asyncio.wait_for(socket.recv(), 0.2)
        except TimeoutError:
            return


async def verify(urls, database, env, fixture):
    count = 0

    def check(value, message):
        nonlocal count
        assert value, message
        count += 1

    clients = [httpx.AsyncClient(base_url=urls[index % 2], trust_env=False) for index in range(3)]
    sockets = []
    try:
        for client, username, private in zip(
            clients,
            ("DEV-AAA", "DEV-BBB", "DEV-CCC"),
            ("private-alex", "private-maya", "private-jordan"),
            strict=True,
        ):
            check(
                (await client.post("/api/auth/login", json={"username": username})).status_code
                == 200,
                "Login",
            )
            cookie = "; ".join(f"{key}={value}" for key, value in client.cookies.items())
            url = str(client.base_url).rstrip("/")
            socket = await websockets.connect(
                url.replace("http://", "ws://") + "/api/ws",
                origin=url,
                proxy=None,
                additional_headers={"Cookie": cookie},
            )
            sockets.append(socket)
            await socket.send(json.dumps({"v": 1, "type": "subscribe", "chat_id": private}))
            await event(
                socket,
                lambda value, private=private: (
                    value.get("type") == "changed" and value.get("chat_id") == private
                ),
            )
        aaa, bbb, ccc = clients
        created = await asyncio.to_thread(
            invoke, env, fixture, "create_chat", {"owner_ids": ["owner-jordan"]}
        )
        check(
            set(created["member_owner_ids"]) == {"owner-alex", "owner-jordan"},
            "Runtime creates owner-membership pair",
        )
        for socket in (sockets[0], sockets[2]):
            notice = await event(
                socket,
                lambda value: (
                    value.get("type") == "personal_changed" and value.get("section") == "chats"
                ),
            )
            check("body" not in notice, "Membership notification is metadata only")
        try:
            await event(sockets[1], lambda value: value.get("section") == "chats", 0.35)
        except TimeoutError:
            check(True, "Nonmember receives no new-chat notification")
        else:
            raise AssertionError("New-chat notification leaked to nonmember")
        boot = (await ccc.get("/api/bootstrap")).json()
        check(
            any(row["id"] == created["id"] for row in boot["chats"]),
            "New member discovers chat across API",
        )
        for socket in sockets:
            await quiet(socket)
        reused = await asyncio.to_thread(
            invoke, env, fixture, "create_chat", {"owner_ids": ["owner-jordan"]}
        )
        check(reused["id"] == created["id"], "Runtime pair creation reuses existing chat")
        try:
            await event(sockets[2], lambda value: value.get("section") == "chats", 0.35)
        except TimeoutError:
            check(True, "Pair reuse does not emit new-membership notification")
        else:
            raise AssertionError("Pair reuse produced unnecessary new-chat notification")
        report = await asyncio.to_thread(
            invoke,
            env,
            fixture,
            "send_chat_message",
            {
                "chat_id": created["id"],
                "body": "New owner-attributed report for CCC.",
                "source_message_ids": [],
            },
        )
        check(
            report["sent"] and report["author_id"] == "twin-alex",
            "New cross-chat report retains Twin attribution",
        )
        visible = (await ccc.get("/api/chats/" + created["id"])).json()
        check(
            any(row["id"] == report["message_id"] for row in visible["messages"]),
            "Recipient sees prepared new report",
        )
        check(
            (await ccc.get("/api/chats/group-alex-maya")).status_code == 403,
            "Report does not grant original chat history",
        )
        check(
            (await bbb.get("/api/chats/private-alex")).status_code == 403,
            "Other owner private stays denied",
        )
        await asyncio.to_thread(
            invoke, env, fixture, "history", {"chat_id": "private-alex", "limit": 10}
        )
        for socket in sockets:
            await quiet(socket)
        approval = await asyncio.to_thread(
            invoke,
            env,
            fixture,
            "create_approval",
            {
                "title": "Approve concrete reminder",
                "description": "One exact in-app reminder",
                "target": "Owner",
                "impact": "One reminder",
                "action": {
                    "tool": "create_reminder",
                    "arguments": {"title": "Platform approved reminder"},
                },
            },
            "approval-platform-once",
        )
        await event(
            sockets[0],
            lambda value: value.get("type") == "changed" and value.get("chat_id") == "private-alex",
        )
        inbox = (await aaa.get("/api/chats/private-alex")).json()
        check(
            any(
                row["id"] == approval["id"] and row["chat_id"] == "group-alex-maya"
                for row in inbox["approvals"]
            ),
            "Private inbox projects pending origin-group approval",
        )
        check(
            (
                await bbb.post(
                    "/api/approvals/" + approval["id"] + "/decision", json={"decision": "approved"}
                )
            ).status_code
            in (403, 404),
            "Other participant cannot decide owner approval",
        )
        check(
            (
                await aaa.post(
                    "/api/approvals/" + approval["id"] + "/decision", json={"decision": "approved"}
                )
            ).status_code
            == 200,
            "Owner can approve concrete group action from private inbox",
        )
        with psycopg.connect(database.url) as db:
            check(
                db.execute(
                    "SELECT count(*) FROM tt_reply_requests WHERE chat_id='private-alex'"
                ).fetchone()[0]
                == 0,
                "Reports/notices do not self-trigger owner private Agent",
            )
            check(
                db.execute(
                    "SELECT count(*) FROM tt_messages WHERE sdk_output_key=%s",
                    ["approval-notice:" + approval["id"]],
                ).fetchone()[0]
                == 1,
                "Approval notice committed exactly once",
            )
            check(
                db.execute(
                    "SELECT chat_id FROM tt_approval_projections WHERE id=%s", [approval["id"]]
                ).fetchone()[0]
                == "group-alex-maya",
                "Approval durable row remains at originating group",
            )
            check(
                db.execute("SELECT count(*) FROM agent_model_usage").fetchone()[0] == 0,
                "No model invocation",
            )
        return count
    finally:
        for socket in sockets:
            await socket.close()
        for client in clients:
            await client.post("/api/auth/logout")
            await client.aclose()


def main():
    os.environ["TWINTEAMS_TEST_DB_URL"] = read_env(BACKEND / ".env")["DATABASE_URL"]
    database, children = TestDatabase(), []
    try:
        with tempfile.TemporaryDirectory(prefix="tt-platform-acceptance-") as temp:
            root = Path(temp)
            env = environment("api")
            env.update(
                DATABASE_URL=database.url,
                AGENT_DB_URL=database.url,
                TWINTEAMS_REDIS_PREFIX="tt-platform-" + uuid4().hex,
                AGENT_MEMORY_DIR=str(root / "memory"),
                AGENT_SECRETS_DIR=str(root / "secrets"),
                TWINTEAMS_WORKSPACE_ROOT=str(root / "workspace"),
            )
            for name in ("OPENAI_API_KEY", "AGENT_SECRETS_KEY"):
                env.pop(name, None)
            result = subprocess.run(
                [sys.executable, "-c", SEED],
                cwd=BACKEND,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            fixture = json.loads(result.stdout.strip().splitlines()[-1])
            urls = []
            for _ in range(2):
                port = free_port()
                urls.append(f"http://127.0.0.1:{port}")
                with (root / f"api-{port}.log").open("w") as log:
                    children.append(
                        subprocess.Popen(
                            [
                                sys.executable,
                                "-m",
                                "uvicorn",
                                "app.main:app",
                                "--host",
                                "127.0.0.1",
                                "--port",
                                str(port),
                            ],
                            cwd=BACKEND,
                            env=env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                        )
                    )
            for url in urls:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    try:
                        if (
                            httpx.get(url + "/health", trust_env=False, timeout=0.5).status_code
                            == 200
                        ):
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Disposable platform API startup failed")
            count = asyncio.run(verify(urls, database, env, fixture))
            print(
                f"PASS: {count} real PG/dual API/WS platform checks; no Worker/provider; disposable state cleaned"
            )
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
        database.close()


if __name__ == "__main__":
    main()
