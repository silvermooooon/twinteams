"""Independent real-PG/Redis/two-API/worker transport acceptance.

Temporary SQL schema, Redis namespace, files and child processes are cleaned up.
The adapter is explicit and offline; final application handoff is tested separately.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_stack import environment
from postgres import TestDatabase

BACKEND = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("distributed_fixture.py")


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


async def receive(ws, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = json.loads(await asyncio.wait_for(ws.recv(), deadline - time.monotonic()))
        if predicate(frame):
            return frame
    raise AssertionError("Expected WebSocket frame did not arrive")


async def main():
    from functools import partial

    from websockets.asyncio.client import connect as websocket_connect

    connect = partial(websocket_connect, proxy=None)

    private = environment("worker")
    # Configure the DB helper from the same private source as the normal app.
    os.environ["DATABASE_URL"] = private["DATABASE_URL"]
    database = TestDatabase()
    processes = []
    handles = []
    namespace = "distributed-test-" + uuid4().hex
    with tempfile.TemporaryDirectory(prefix="twinteams-distributed-") as directory:
        root = Path(directory)
        env = {
            **private,
            "DATABASE_URL": database.url,
            "AGENT_DB_URL": database.url,
            "AGENT_API": "distributed-offline",
            "OPENAI_API_KEY": "explicit-offline-no-network",
            "TWINTEAMS_DISTRIBUTED_TEST": "true",
            "TWINTEAMS_REDIS_PREFIX": namespace,
            "TWINTEAMS_COMPENSATION_SECONDS": "60",
            "TWINTEAMS_WORKSPACE_ROOT": str(root / "workspaces"),
            "AGENT_MEMORY_DIR": str(root / "memory"),
            "DISTRIBUTED_SQL_FILE": str(root / "sql.jsonl"),
            "DISTRIBUTED_CALLS_FILE": str(root / "calls.jsonl"),
            "DISTRIBUTED_HINT_DROP_FILE": str(root / "drop-hints"),
        }

        def spawn(role, port=None, overrides=None):
            log = (root / f"{role}-{port or 0}.log").open("w")
            handles.append(log)
            command = [sys.executable, "-u", str(FIXTURE), role]
            if port:
                command += ["--port", str(port)]
            child_env = dict(env)
            if role == "worker":
                child_env["TWINTEAMS_COMPENSATION_SECONDS"] = "2"
            child_env.update(overrides or {})
            process = subprocess.Popen(
                command,
                cwd=BACKEND,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            processes.append(process)
            return process

        async def ready(client):
            for _ in range(100):
                try:
                    response = await client.get("/health")
                    if response.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            raise AssertionError("Fixture API did not become ready")

        try:
            migration = spawn("init")
            assert await asyncio.to_thread(migration.wait, 30) == 0, "Fixture migration failed"
            port_a, port_b = free_port(), free_port()
            assert port_a != port_b
            api_a, api_b = spawn("api", port_a), spawn("api", port_b)
            async with (
                httpx.AsyncClient(base_url=f"http://127.0.0.1:{port_a}", timeout=10) as first,
                httpx.AsyncClient(base_url=f"http://127.0.0.1:{port_b}", timeout=10) as second,
            ):
                await asyncio.gather(ready(first), ready(second))
                login = await first.post("/api/auth/login", json={"username": "DEV-AAA"})
                login.raise_for_status()
                cookie = "; ".join(f"{name}={value}" for name, value in first.cookies.items())
                second.cookies.update(first.cookies)
                assert (await second.get("/api/auth/me")).status_code == 200
                print("PASS local identity accepted by independent API processes")
                async with (
                    connect(
                        f"ws://127.0.0.1:{port_a}/api/ws",
                        origin=f"http://127.0.0.1:{port_a}",
                        additional_headers={"Cookie": cookie},
                    ) as ws_a,
                    connect(
                        f"ws://127.0.0.1:{port_b}/api/ws",
                        origin=f"http://127.0.0.1:{port_b}",
                        additional_headers={"Cookie": cookie},
                    ) as ws_b,
                ):
                    for ws in (ws_a, ws_b):
                        await ws.send(
                            json.dumps({"v": 1, "type": "subscribe", "chat_id": "private-alex"})
                        )
                        await receive(ws, lambda event: event.get("type") == "changed")
                    presence_payload = {
                        "device_id": "distributed-presence",
                        "focused_chat_id": None,
                        "visible": False,
                    }

                    async def renew_presence():
                        renewal_id = uuid4().hex
                        await ws_a.send(
                            json.dumps(
                                {
                                    "v": 1,
                                    "type": "command",
                                    "command": "presence",
                                    "command_id": renewal_id,
                                    "payload": presence_payload,
                                }
                            )
                        )
                        ack = await receive(
                            ws_a, lambda event: event.get("command_id") == renewal_id
                        )
                        assert ack.get("accepted") == "presence_renewed", ack

                    await renew_presence()

                    def api_sql_count():
                        records = [
                            json.loads(line)
                            for line in (root / "sql.jsonl").read_text().splitlines()
                        ]
                        return sum(record["role"] == "api" for record in records)

                    before_sql = api_sql_count()
                    for _ in range(100):
                        await ws_a.send(json.dumps({"v": 1, "type": "pong"}))
                        await renew_presence()
                    after_sql = api_sql_count()
                    assert after_sql == before_sql, (
                        f"100 pong/presence renewals issued {after_sql - before_sql} SQL statements"
                    )
                    print(
                        "PASS measured application SQL: 100 pong + 100 presence renewals = 0 PG statements"
                    )
                    before_handshakes = api_sql_count()
                    sockets = await asyncio.gather(
                        *[
                            connect(
                                f"ws://127.0.0.1:{port_a}/api/ws",
                                origin=f"http://127.0.0.1:{port_a}",
                                additional_headers={"Cookie": cookie},
                            )
                            for _ in range(100)
                        ],
                        return_exceptions=True,
                    )
                    opened = [socket for socket in sockets if not isinstance(socket, BaseException)]
                    try:
                        assert len(opened) == 100, (
                            "The bounded 100-socket local sample failed to connect"
                        )
                        handshake_sql = api_sql_count() - before_handshakes
                        idle_before = api_sql_count()
                        await asyncio.gather(
                            *[
                                socket.send(json.dumps({"v": 1, "type": "pong"}))
                                for socket in opened
                            ]
                        )
                        await asyncio.sleep(0.2)
                        assert api_sql_count() == idle_before, (
                            "Connected idle/pong sockets issued PG statements"
                        )
                        print(
                            f"PASS measured local sample: 100 WS connections, handshake SQL={handshake_sql}, idle/pong SQL=0"
                        )
                    finally:
                        await asyncio.gather(*[socket.close() for socket in opened])
                    command_id = uuid4().hex
                    command = {
                        "v": 1,
                        "type": "command",
                        "command_id": command_id,
                        "chat_id": "private-alex",
                        "command": "send",
                        "payload": {
                            "body": "Independent distributed test " + command_id,
                            "client_request_key": command_id,
                        },
                    }
                    await ws_a.send(json.dumps(command))
                    accepted = await receive(
                        ws_a,
                        lambda event: (
                            event.get("type") == "ack" and event.get("command_id") == command_id
                        ),
                    )
                    assert accepted.get("accepted") == "business_persisted", accepted
                    persisted = (await second.get("/api/chats/private-alex")).json()
                    message = next(
                        item
                        for item in persisted["messages"]
                        if item["body"] == command["payload"]["body"]
                    )
                    message_id = message["id"]
                    queued = next(
                        item
                        for item in persisted["requests"]
                        if item["source_message_id"] == message_id
                    )
                    assert queued["status"] == "queued", queued
                    await ws_a.send(json.dumps(command))
                    duplicate = await receive(
                        ws_a,
                        lambda event: (
                            event.get("type") == "ack" and event.get("command_id") == command_id
                        ),
                    )
                    assert duplicate.get("accepted") == "business_persisted"
                    replayed = (await second.get("/api/chats/private-alex")).json()
                    assert (
                        len(
                            [
                                item
                                for item in replayed["messages"]
                                if item["body"] == command["payload"]["body"]
                            ]
                        )
                        == 1
                    )
                    changed = await receive(
                        ws_b,
                        lambda event: (
                            event.get("type") == "changed"
                            and event.get("chat_id") == "private-alex"
                        ),
                    )
                    assert set(changed) <= {"v", "type", "chat_id", "revision"}, changed
                    assert not (root / "calls.jsonl").exists(), "API attempted model execution"
                    print(
                        "PASS WS accepts durable work with no worker; ACK replay is idempotent; second API receives change"
                    )
                    worker = spawn("worker")
                    completed = None
                    from psycopg import AsyncConnection

                    async with await AsyncConnection.connect(database.url) as oracle:
                        deadline = time.monotonic() + 20
                        while time.monotonic() < deadline:
                            await receive(
                                ws_b,
                                lambda event: event.get("type") == "changed",
                                timeout=deadline - time.monotonic(),
                            )
                            cursor = await oracle.execute(
                                "SELECT status, error FROM tt_reply_requests WHERE id = %s",
                                (queued["id"],),
                            )
                            completed = await cursor.fetchone()
                            if completed and completed[0] == "completed":
                                break
                    assert completed and completed[0] == "completed", completed
                    detail = (await second.get("/api/chats/private-alex")).json()
                    calls = [
                        json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()
                    ]
                    assert calls and all(call["role"] == "worker" for call in calls), calls
                    outputs = [
                        item
                        for item in detail["messages"]
                        if item.get("request_id") == queued["id"]
                    ]
                    assert len(outputs) == 1
                    print(
                        "PASS independent worker executes queued request and PG output projects once"
                    )
                    stop_input = "SLOW_CANCEL " + uuid4().hex
                    slow_id = uuid4().hex
                    await ws_a.send(
                        json.dumps(
                            {
                                "v": 1,
                                "type": "command",
                                "command": "send",
                                "chat_id": "private-alex",
                                "command_id": slow_id,
                                "payload": {"body": stop_input},
                            }
                        )
                    )
                    slow_ack = await receive(ws_a, lambda event: event.get("command_id") == slow_id)
                    assert slow_ack.get("accepted") == "business_persisted", slow_ack
                    async with await AsyncConnection.connect(database.url) as oracle:
                        for _ in range(100):
                            cursor = await oracle.execute(
                                "SELECT r.id,r.status FROM tt_reply_requests r JOIN tt_messages m ON m.id=r.source_message_id WHERE m.body=%s",
                                (stop_input,),
                            )
                            slow_request = await cursor.fetchone()
                            if slow_request and slow_request[1] == "running":
                                break
                            await asyncio.sleep(0.05)
                        assert slow_request and slow_request[1] == "running", slow_request
                        stop_id = uuid4().hex
                        await ws_b.send(
                            json.dumps(
                                {
                                    "v": 1,
                                    "type": "command",
                                    "command": "stop",
                                    "chat_id": "private-alex",
                                    "command_id": stop_id,
                                    "payload": {"request_id": slow_request[0]},
                                }
                            )
                        )
                        stopped_ack = await receive(
                            ws_b, lambda event: event.get("command_id") == stop_id
                        )
                        assert stopped_ack.get("accepted") == "business_persisted", stopped_ack
                        for _ in range(100):
                            cursor = await oracle.execute(
                                "SELECT status FROM tt_reply_requests WHERE id=%s",
                                (slow_request[0],),
                            )
                            stopped_state = (await cursor.fetchone())[0]
                            if stopped_state == "cancelled":
                                break
                            await asyncio.sleep(0.05)
                        assert stopped_state == "cancelled", stopped_state
                    print(
                        "PASS second API WS stop cancels the independent worker and persists cancelled state"
                    )
                    # Suppress only disposable immediate hints, then kill the accepting API.
                    # The worker's independent two-second outbox compensation must deliver it.
                    drop_control = root / "drop-hints"
                    drop_control.touch()
                    lost_id = uuid4().hex
                    await ws_a.send(
                        json.dumps(
                            {
                                "v": 1,
                                "type": "command",
                                "command": "send",
                                "chat_id": "private-alex",
                                "command_id": lost_id,
                                "payload": {"body": "Commit-before-publish " + lost_id},
                            }
                        )
                    )
                    lost_ack = await receive(ws_a, lambda event: event.get("command_id") == lost_id)
                    assert lost_ack.get("accepted") == "business_persisted", lost_ack
                    persisted = (await second.get("/api/chats/private-alex")).json()
                    lost_message = next(
                        item
                        for item in persisted["messages"]
                        if item["body"] == "Commit-before-publish " + lost_id
                    )
                    os.killpg(api_a.pid, signal.SIGKILL)
                    await asyncio.to_thread(api_a.wait, 5)
                    async with await AsyncConnection.connect(database.url) as oracle:
                        cursor = await oracle.execute(
                            "SELECT id FROM tt_reply_requests WHERE source_message_id = %s",
                            (lost_message["id"],),
                        )
                        lost_request_id = (await cursor.fetchone())[0]
                        deadline = time.monotonic() + 10
                        while time.monotonic() < deadline:
                            await receive(
                                ws_b,
                                lambda event: event.get("type") == "changed",
                                timeout=deadline - time.monotonic(),
                            )
                            cursor = await oracle.execute(
                                "SELECT status FROM tt_reply_requests WHERE id = %s",
                                (lost_request_id,),
                            )
                            if (await cursor.fetchone())[0] == "completed":
                                break
                        else:
                            raise AssertionError(
                                "Committed request was not recovered by outbox compensation"
                            )
                    drop_control.unlink()
                    persisted = (await second.get("/api/chats/private-alex")).json()
                    assert (
                        len(
                            [
                                item
                                for item in persisted["messages"]
                                if item.get("request_id") == lost_request_id
                            ]
                        )
                        == 1
                    )
                    api_a = spawn("api", port_a)
                    await ready(first)
                    print(
                        "PASS accepting API SIGKILL after PG ACK + suppressed publish: outbox compensation restores one complete output"
                    )
                    # Cross-origin and anonymous sockets cannot subscribe.
                    from websockets.exceptions import InvalidStatus

                    for origin, headers in [
                        ("https://untrusted.invalid", {"Cookie": cookie}),
                        (f"http://127.0.0.1:{port_a}", {}),
                    ]:
                        try:
                            async with connect(
                                f"ws://127.0.0.1:{port_a}/api/ws",
                                origin=origin,
                                additional_headers=headers,
                            ) as rejected:
                                await rejected.send(
                                    json.dumps(
                                        {"v": 1, "type": "subscribe", "chat_id": "private-alex"}
                                    )
                                )
                                from websockets.exceptions import ConnectionClosed

                                try:
                                    frame = await receive(
                                        rejected,
                                        lambda event: event.get("type") == "error",
                                        timeout=2,
                                    )
                                    assert frame.get("type") == "error"
                                except ConnectionClosed:
                                    pass
                        except InvalidStatus:
                            pass
                    print("PASS untrusted Origin and unauthenticated subscriptions rejected")
                    assert api_a.poll() is None and api_b.poll() is None and worker.poll() is None
                os.killpg(worker.pid, signal.SIGTERM)
                await asyncio.to_thread(worker.wait, 5)
                unreachable = f"redis://127.0.0.1:{free_port()}/0"
                worker = spawn(
                    "worker",
                    overrides={"TWINTEAMS_REDIS_URL": unreachable, "REDIS_URL": unreachable},
                )
                await asyncio.sleep(0.5)
                disconnected_id = uuid4().hex
                disconnected = await first.post(
                    "/api/chats/private-alex/messages",
                    json={
                        "body": "Worker Redis unreachable " + disconnected_id,
                        "client_request_key": disconnected_id,
                    },
                )
                disconnected.raise_for_status()
                disconnected_request = disconnected.json()["request"]["id"]
                async with await AsyncConnection.connect(database.url) as oracle:
                    for _ in range(100):
                        cursor = await oracle.execute(
                            "SELECT status FROM tt_reply_requests WHERE id = %s",
                            (disconnected_request,),
                        )
                        state = (await cursor.fetchone())[0]
                        if state == "completed":
                            break
                        await asyncio.sleep(0.1)
                    assert state == "completed", state
                persisted = (await second.get("/api/chats/private-alex")).json()
                assert (
                    len(
                        [
                            item
                            for item in persisted["messages"]
                            if item.get("request_id") == disconnected_request
                        ]
                    )
                    == 1
                )
                print(
                    "PASS worker-only Redis connection refusal: two-second PG compensation completes accepted work; shared Redis stayed running"
                )
            print("PASS distributed acceptance complete; no live model calls")
        except BaseException:
            from psycopg import AsyncConnection

            async with await AsyncConnection.connect(database.url) as oracle:
                rows = await oracle.execute(
                    "SELECT status, count(*) FROM tt_reply_requests GROUP BY status"
                )
                print("Fixture PG request states:", await rows.fetchall(), file=sys.stderr)
            calls_file = root / "calls.jsonl"
            if calls_file.exists():
                print("Fixture provider calls:", calls_file.read_text(), file=sys.stderr)
            # Logs are controlled offline test logs and contain no real provider key.
            for path in root.glob("*.log"):
                diagnostic = path.read_text()[-5000:]
                for name, value in env.items():
                    if value and ("URL" in name or "KEY" in name or "PASSWORD" in name):
                        diagnostic = diagnostic.replace(value, "<private-config>")
                print(f"Fixture diagnostic {path.name}:\n{diagnostic}", file=sys.stderr)
            raise
        finally:
            for process in processes:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
            for process in processes:
                try:
                    await asyncio.to_thread(process.wait, 5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    await asyncio.to_thread(process.wait, 5)
            for handle in handles:
                handle.close()
            from redis.asyncio import Redis

            redis = Redis.from_url(env["TWINTEAMS_REDIS_URL"])
            try:
                keys = [key async for key in redis.scan_iter(match=namespace + ":*", count=100)]
                if keys:
                    await redis.unlink(*keys)
            finally:
                await redis.aclose()
            database.close()


if __name__ == "__main__":
    asyncio.run(main())
