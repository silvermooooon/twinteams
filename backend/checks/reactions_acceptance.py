"""Disposable real PG/dual API/WS reaction checks; no Worker or model requests."""

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
from checks.platform_tools_acceptance import event, invoke, quiet
from checks.postgres import TestDatabase

SEED = """
import json
from app.service import service
from app.models import Chat
service.initialize(create_schema=True)
with service.transaction() as db:
    private=service.append_message(db,db.get(Chat,'private-alex'),author_id='owner-alex',kind='human',body='Private reaction target').id
    message=service.append_message(db,db.get(Chat,'group-alex-maya'),author_id='owner-maya',kind='human',body='Reaction target').id
request=service.manual_request('owner-maya','group-alex-maya',message,'twin-alex','reaction-sdk-task')
print(json.dumps({'context':service.get_runtime_context(request['id']),'message':message,'private':private}))
"""


def snapshot(database):
    tables = (
        "tt_messages",
        "tt_reply_requests",
        "tt_routing_decisions",
        "tt_causal_chains",
        "tt_automatic_reservations",
    )
    with psycopg.connect(database.url) as db:
        return {
            table: db.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in tables
        }


async def verify(urls, database, env, fixture):
    checks = 0

    def check(value, reason):
        nonlocal checks
        assert value, reason
        checks += 1

    clients = [httpx.AsyncClient(base_url=url, trust_env=False) for url in urls]
    sockets = []
    try:
        for client, username in zip(clients, ("DEV-AAA", "DEV-BBB"), strict=True):
            check(
                (await client.post("/api/auth/login", json={"username": username})).status_code
                == 200,
                "Login",
            )
            origin = str(client.base_url).rstrip("/")
            cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
            socket = await websockets.connect(
                origin.replace("http://", "ws://") + "/api/ws",
                origin=origin,
                proxy=None,
                additional_headers={"Cookie": cookie},
            )
            sockets.append(socket)
            await socket.send(
                json.dumps({"v": 1, "type": "subscribe", "chat_id": "group-alex-maya"})
            )
            await event(
                socket,
                lambda v: v.get("type") == "changed" and v.get("chat_id") == "group-alex-maya",
            )
        aaa, bbb = clients
        path = f"/api/chats/group-alex-maya/messages/{fixture['message']}/reactions"
        before = snapshot(database)

        async def detail(client=aaa):
            response = await client.get("/api/chats/group-alex-maya")
            check(response.status_code == 200, "Chat GET")
            return response.json()

        async def put(client, active, key, **extra):
            return await client.put(
                path, json={"emoji": "👍", "active": active, "client_request_key": key, **extra}
            )

        # Existing chat_detail lazily creates per-owner default ChatSettings.
        # Warm that unrelated first-read write before measuring reaction revisions.
        await detail(aaa)
        await detail(bbb)
        first = await detail()
        responses = await asyncio.gather(*(put(aaa, True, "concurrent-add") for _ in range(12)))
        check(all(r.status_code == 200 for r in responses), "Same-key concurrent adds all succeed")
        check(
            all(r.json().get("revision") == first["revision"] + 1 for r in responses),
            "Mutation/replay response revisions describe their exact aggregate snapshot",
        )
        added = await detail(bbb)
        check(
            added["revision"] == first["revision"] + 1,
            f"Concurrent add advances revision once: {first['revision']} -> {added['revision']}",
        )
        state = next(m for m in added["messages"] if m["id"] == fixture["message"])["reactions"]
        check(
            state[0]["count"] == 1
            and state[0]["actors"][0]["id"] == "owner-alex"
            and not state[0]["mine"],
            "Replica sees human actor and viewer-specific mine",
        )
        for socket in sockets:
            hint = await event(
                socket,
                lambda v: v.get("type") == "changed" and v.get("chat_id") == "group-alex-maya",
            )
            check(
                "body" not in hint and "reactions" not in hint, "WS is metadata invalidation only"
            )
            await quiet(socket)
        noops = await asyncio.gather(*(put(aaa, True, "noop-" + str(i)) for i in range(8)))
        check(all(r.status_code == 200 for r in noops), "Different-key desired-state noops succeed")
        check(
            all(r.json().get("revision") == added["revision"] for r in noops),
            "No-op response revisions remain current without extra chat changes",
        )
        check(
            (await detail())["revision"] == added["revision"],
            "Different-key noops do not advance revision",
        )
        for socket in sockets:
            try:
                await event(socket, lambda v: v.get("type") == "changed", timeout=0.3)
            except TimeoutError:
                check(True, "No-op does not publish WS changed")
            else:
                raise AssertionError("No-op published unnecessary changed")
        check(
            (await put(aaa, False, "concurrent-add")).status_code == 409,
            "Same-key different desired state conflicts",
        )
        check((await put(bbb, True, "other-add")).status_code == 200, "Second human reaction")
        twin = await asyncio.to_thread(
            invoke,
            env,
            fixture,
            "set_message_reaction",
            {
                "chat_id": "group-alex-maya",
                "message_id": fixture["message"],
                "emoji": "👍",
                "active": True,
            },
            "twin-add",
        )
        check(
            {a["id"] for a in twin["reactions"][0]["actors"]}
            == {"owner-alex", "owner-maya", "twin-alex"},
            "Runtime reaction belongs to Twin separately",
        )
        check(
            (await put(aaa, False, "human-remove")).status_code == 200, "Remove own human reaction"
        )
        check(
            (await put(aaa, True, "concurrent-add")).status_code == 200,
            "Replay old add accepted without resurrection",
        )
        remaining = next(m for m in (await detail())["messages"] if m["id"] == fixture["message"])[
            "reactions"
        ][0]
        check(
            {a["id"] for a in remaining["actors"]} == {"owner-maya", "twin-alex"}
            and not remaining["mine"],
            "Old replay neither resurrects nor removes other actors",
        )
        history = await asyncio.to_thread(invoke, env, fixture, "history", {"limit": 10})
        messages = history.get("messages", history) if isinstance(history, dict) else history
        seen = next(m for m in messages if m["id"] == fixture["message"])["reactions"][0]
        check(
            {a["kind"] for a in seen["actors"]} == {"human", "twin"},
            "SDK history includes actor distinction",
        )
        check(
            (await put(aaa, True, "inject", actor_id="twin-alex")).status_code == 422,
            "Actor injection rejected",
        )
        check(
            (
                await bbb.put(
                    f"/api/chats/private-alex/messages/{fixture['private']}/reactions",
                    json={"emoji": "👍", "active": True, "client_request_key": "private-denied"},
                )
            ).status_code
            == 403,
            "Other owner private denied",
        )
        async with httpx.AsyncClient(base_url=urls[0], trust_env=False) as anonymous:
            check(
                (await put(anonymous, True, "anonymous")).status_code == 401,
                "Anonymous mutation denied",
            )
        check(
            snapshot(database) == before,
            "Reactions create no message, request, routing, chain, reservation",
        )
        private_path = f"/api/chats/private-alex/messages/{fixture['private']}/reactions"
        check(
            (
                await aaa.put(
                    private_path,
                    json={"emoji": "👍", "active": True, "client_request_key": "private-add"},
                )
            ).status_code
            == 200,
            "Own private reaction",
        )
        reset = await aaa.post(
            "/api/me/twin/reset",
            json={"expected_generation": 0, "idempotency_key": "isolated-reaction-reset"},
        )
        check(reset.status_code == 200, "Isolated reset succeeds with reaction FK")
        with psycopg.connect(database.url) as db:
            check(
                db.execute(
                    "SELECT count(*) FROM tt_message_reactions WHERE message_id=%s",
                    (fixture["private"],),
                ).fetchone()[0]
                == 0,
                "Reset deletes private reactions",
            )
            check(
                db.execute(
                    "SELECT count(*) FROM tt_message_reactions WHERE message_id=%s",
                    (fixture["message"],),
                ).fetchone()[0]
                == 2,
                "Reset preserves group reactions",
            )
        check(
            (
                await aaa.put(
                    private_path,
                    json={"emoji": "👍", "active": True, "client_request_key": "private-add"},
                )
            ).status_code
            in (404, 409),
            "Old private replay cannot resurrect reset message",
        )
        return checks
    finally:
        for socket in sockets:
            await socket.close()
        for client in clients:
            await client.aclose()


def main():
    os.environ["TWINTEAMS_TEST_DB_URL"] = read_env(BACKEND / ".env")["DATABASE_URL"]
    database, children = TestDatabase(), []
    try:
        with tempfile.TemporaryDirectory(prefix="tt-reaction-acceptance-") as temp:
            root = Path(temp)
            env = environment("api")
            env.update(
                DATABASE_URL=database.url,
                AGENT_DB_URL=database.url,
                TWINTEAMS_REDIS_PREFIX="tt-reaction-" + uuid4().hex,
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
                    raise RuntimeError("Disposable reaction API startup failed")
            count = asyncio.run(verify(urls, database, env, fixture))
            print(
                f"PASS: {count} real PG/dual API/WS reaction checks; no Worker/provider; disposable state cleaned"
            )
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
        database.close()


if __name__ == "__main__":
    main()
