"""Independent disposable-PG, dual-API/WS naming acceptance; no Worker/provider.

Run from backend: .venv/bin/python -m checks.chat_naming_acceptance
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

FIXTURE = """
from app.service import service
from app.models import Chat,ChatSetting
service.initialize(create_schema=True)
with service.transaction() as db:
    row=service.settings_row(db,"owner-alex",db.get(Chat,"private-alex"))
    row.reply_for_me_enabled=True
"""


async def verify(urls, database):
    count = 0

    def check(value, message):
        nonlocal count
        assert value, message
        count += 1

    async with (
        httpx.AsyncClient(base_url=urls[0], trust_env=False) as aaa,
        httpx.AsyncClient(base_url=urls[1], trust_env=False) as bbb,
        httpx.AsyncClient(base_url=urls[1], trust_env=False) as ccc,
    ):
        for client, username in ((aaa, "DEV-AAA"), (bbb, "DEV-BBB"), (ccc, "DEV-CCC")):
            check(
                (await client.post("/api/auth/login", json={"username": username})).status_code
                == 200,
                "Login",
            )
        async with httpx.AsyncClient(base_url=urls[0], trust_env=False) as anon:
            check(
                (await anon.patch("/api/chats/group-team", json={"title": "Denied"})).status_code
                == 401,
                "Anonymous rename denied",
            )
        for client, other in ((aaa, "BBB"), (bbb, "AAA")):
            for path in ("/api/chats/group-alex-maya", "/api/chats/group-alex-maya/summary"):
                result = await client.get(path)
                row = result.json().get("chat", result.json())
                check(
                    result.status_code == 200
                    and row["title"] == other
                    and row["kind"] == "group"
                    and row["can_rename"] is False,
                    "Pair viewer title and unchanged kind",
                )
            boot = (await client.get("/api/bootstrap")).json()
            check(
                next(c for c in boot["chats"] if c["id"] == "group-alex-maya")["title"] == other,
                "Bootstrap viewer title",
            )
        check(
            (await aaa.patch("/api/chats/group-alex-maya", json={"title": "Forbidden"})).status_code
            == 409,
            "Pair rename denied",
        )
        check(
            (await ccc.patch("/api/chats/group-alex-maya", json={"title": "Forbidden"})).status_code
            == 403,
            "Nonmember denied before rename",
        )
        check(
            (await aaa.patch("/api/chats/private-alex", json={"title": "Forbidden"})).status_code
            == 409,
            "Private rename denied",
        )
        for title in ("", "   ", "\n\t"):
            check(
                (await aaa.patch("/api/chats/group-team", json={"title": title})).status_code
                in (400, 422),
                "Blank title denied",
            )
        check(
            (await aaa.patch("/api/chats/group-team", json={"title": "x" * 257})).status_code
            in (400, 422),
            "Oversized title denied",
        )
        check(
            (await aaa.patch("/api/chats/group-team", json={"title": "x" * 256})).status_code
            == 200,
            "256 character title accepted",
        )
        check(
            (
                await aaa.patch(
                    "/api/chats/group-team", json={"title": "Name", "owner_id": "owner-maya"}
                )
            ).status_code
            == 422,
            "Owner spoof rejected",
        )
        first = (await bbb.get("/api/chats/group-team")).json()["chat"]
        cookie = "; ".join(f"{key}={value}" for key, value in bbb.cookies.items())
        async with websockets.connect(
            urls[1].replace("http://", "ws://") + "/api/ws",
            origin=urls[1],
            proxy=None,
            additional_headers={"Cookie": cookie},
        ) as socket:
            await socket.send(json.dumps({"v": 1, "type": "subscribe", "chat_id": "group-team"}))
            await asyncio.wait_for(socket.recv(), 3)
            renamed = await aaa.patch("/api/chats/group-team", json={"title": "  Review launch  "})
            check(
                renamed.status_code == 200
                and renamed.json()["title"] == "Review launch"
                and renamed.json()["can_rename"] is True,
                "Trimmed name stored",
            )
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                event = json.loads(await asyncio.wait_for(socket.recv(), 4))
                if (
                    event.get("chat_id") == "group-team"
                    and event.get("revision", 0) > first["revision"]
                ):
                    check(event["type"] == "changed", "Cross API WS invalidation")
                    check(
                        "Review launch" not in json.dumps(event), "WS hint carries no title content"
                    )
                    break
            else:
                raise AssertionError("No cross API rename notification")
        for client in (aaa, bbb, ccc):
            row = (await client.get("/api/chats/group-team")).json()["chat"]
            check(
                row["title"] == "Review launch" and row["can_rename"] is True,
                "Shared name persisted across APIs",
            )
        created = await aaa.post(
            "/api/chats",
            json={
                "owner_ids": ["owner-maya"],
                "title": "CUSTOM TITLE MUST BE IGNORED",
                "history_policy": "all",
            },
        )
        check(
            created.status_code == 200
            and created.json()["title"] == "BBB"
            and created.json()["can_rename"] is False,
            "Pair creation ignores custom title",
        )
        pair_id = created.json()["id"]
        repeated = await aaa.post(
            "/api/chats", json={"owner_ids": ["owner-maya", "owner-alex", "owner-maya"]}
        )
        reverse = await bbb.post("/api/chats", json={"owner_ids": ["owner-alex"]})
        check(
            repeated.status_code == reverse.status_code == 200
            and repeated.json()["id"] == reverse.json()["id"] == pair_id,
            "Same and reverse pair creation return canonical existing chat",
        )
        # No seeded BBB/CCC pair: both APIs race through the insert path.
        concurrent = await asyncio.gather(
            *[
                (bbb if index % 2 == 0 else ccc).post(
                    "/api/chats",
                    json={"owner_ids": ["owner-jordan" if index % 2 == 0 else "owner-maya"]},
                )
                for index in range(12)
            ]
        )
        check(
            all(response.status_code == 200 for response in concurrent),
            "Concurrent create does not leak unique errors",
        )
        concurrent_ids = {response.json()["id"] for response in concurrent}
        check(
            len(concurrent_ids) == 1,
            "Twelve opposite-direction creates on two APIs return one chat",
        )
        bc_pair = concurrent_ids.pop()
        check(
            all(
                response.json()["title"] == ("CCC" if index % 2 == 0 else "BBB")
                for index, response in enumerate(concurrent)
            ),
            "Concurrent responses preserve viewer-specific peer title",
        )
        await bbb.patch(
            f"/api/chats/{bc_pair}/settings",
            json={
                "prompt_override": "Preserve existing private preference",
                "reply_for_me_enabled": True,
            },
        )
        with psycopg.connect(database.url) as db:
            db.execute(
                "UPDATE tt_memberships SET history_policy='no_prior',lower_seq=7 WHERE chat_id=%s AND owner_id='owner-maya'",
                [bc_pair],
            )
            db.commit()
        reopen = await bbb.post(
            "/api/chats", json={"owner_ids": ["owner-jordan"], "history_policy": "all"}
        )
        check(
            reopen.status_code == 200 and reopen.json()["id"] == bc_pair,
            "Reopen uses existing pair despite a different history payload",
        )
        with psycopg.connect(database.url) as db:
            check(
                db.execute(
                    "SELECT history_policy,lower_seq FROM tt_memberships WHERE chat_id=%s AND owner_id='owner-maya'",
                    [bc_pair],
                ).fetchone()
                == ("no_prior", 7),
                "Existing history grant and floor are never widened on reopen",
            )
        reused_settings = (await bbb.get(f"/api/chats/{bc_pair}")).json()["settings"]
        check(
            reused_settings["prompt_override"] == "Preserve existing private preference"
            and reused_settings["reply_for_me_enabled"] is True,
            "Reopen preserves existing per-owner settings",
        )
        # Either ordering is valid; after upgrade commits it cannot be reused as a pair.
        upgraded, raced_create = await asyncio.gather(
            bbb.post(
                f"/api/chats/{bc_pair}/members",
                json={"owner_ids": ["owner-alex"], "history_policy": "all"},
            ),
            ccc.post("/api/chats", json={"owner_ids": ["owner-maya"]}),
        )
        check(
            upgraded.status_code == raced_create.status_code == 200,
            "Pair upgrade versus create completes without deadlock",
        )
        replacement = await bbb.post("/api/chats", json={"owner_ids": ["owner-jordan"]})
        check(
            replacement.status_code == 200
            and replacement.json()["id"] != bc_pair
            and len(replacement.json()["member_owner_ids"]) == 2,
            "Upgraded multi-owner group is never reused as direct pair",
        )
        reverse_replacement = await ccc.post("/api/chats", json={"owner_ids": ["owner-maya"]})
        check(
            reverse_replacement.status_code == 200
            and reverse_replacement.json()["id"] == replacement.json()["id"],
            "Replacement pair is shared by both directions",
        )
        check(
            len((await bbb.get(f"/api/chats/{bc_pair}")).json()["chat"]["member_owner_ids"]) == 3,
            "Upgraded group remains intact",
        )
        check(
            (await bbb.get("/api/chats/" + pair_id)).json()["chat"]["title"] == "AAA",
            "Created pair other viewer name",
        )
        check(
            (await ccc.get("/api/chats/" + pair_id)).status_code == 403,
            "Created pair membership enforced",
        )
        check(
            (await aaa.patch("/api/chats/" + pair_id, json={"title": "Not allowed"})).status_code
            == 409,
            "Created pair rename denied",
        )
        expanded = await aaa.post(
            "/api/chats/" + pair_id + "/members",
            json={"owner_ids": ["owner-jordan"], "history_policy": "all"},
        )
        check(
            expanded.status_code == 200 and expanded.json()["can_rename"] is True,
            "Adding third owner enables rename",
        )
        check(
            (await ccc.patch("/api/chats/" + pair_id, json={"title": "New trio"})).status_code
            == 200,
            "New third member may rename",
        )
        fresh_ab = await aaa.post("/api/chats", json={"owner_ids": ["owner-maya"]})
        check(
            fresh_ab.status_code == 200
            and fresh_ab.json()["id"] != pair_id
            and len(fresh_ab.json()["member_owner_ids"]) == 2,
            "Adding third owner releases original pair identity",
        )
        group_setting = await aaa.patch(
            "/api/chats/group-team/settings", json={"reply_for_me_enabled": True}
        )
        check(
            group_setting.status_code == 200
            and group_setting.json()["reply_for_me_enabled"] is True,
            "Group flag remains enabled",
        )
        sent = await aaa.post(
            "/api/chats/group-team/messages",
            json={
                "body": "Synthetic rewrite input",
                "reply_for_me": True,
                "client_request_key": uuid4().hex,
            },
        )
        check(
            sent.status_code == 200
            and sent.json()["message"] is None
            and sent.json()["request"]["source"] == "reply_for_me",
            "Group reply for me preserved",
        )
        with psycopg.connect(database.url) as db:
            check(
                db.execute("SELECT title FROM tt_chats WHERE id='group-team'").fetchone()[0]
                == "Review launch",
                "Title durable in PG",
            )
            check(
                db.execute("SELECT count(*) FROM agent_model_usage").fetchone()[0] == 0,
                "No model usage",
            )
            db.execute(
                "UPDATE tt_memberships SET left_at=now() WHERE chat_id=%s AND owner_id='owner-jordan'",
                [pair_id],
            )
            db.commit()
        check(
            (await ccc.patch("/api/chats/" + pair_id, json={"title": "Revoked"})).status_code
            == 403,
            "Departed member cannot rename",
        )
        for client in (aaa, bbb, ccc):
            await client.post("/api/auth/logout")
    return count


def main():
    os.environ["TWINTEAMS_TEST_DB_URL"] = read_env(BACKEND / ".env")["DATABASE_URL"]
    database, children = TestDatabase(), []
    try:
        with tempfile.TemporaryDirectory(prefix="tt-naming-http-") as temp:
            root = Path(temp)
            env = environment("api")
            env.update(
                DATABASE_URL=database.url,
                AGENT_DB_URL=database.url,
                TWINTEAMS_REDIS_PREFIX="tt-naming-" + uuid4().hex,
                AGENT_MEMORY_DIR=str(root / "memory"),
                AGENT_SECRETS_DIR=str(root / "secrets"),
                TWINTEAMS_WORKSPACE_ROOT=str(root / "workspace"),
            )
            for name in ("OPENAI_API_KEY", "AGENT_SECRETS_KEY"):
                env.pop(name, None)
            subprocess.run(
                [sys.executable, "-c", FIXTURE],
                cwd=BACKEND,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
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
                    raise RuntimeError("Disposable naming API startup failed")
            count = asyncio.run(verify(urls, database))
            print(
                f"PASS: {count} isolated PG/dual API/WS naming assertions; no Worker/LLM; temporary state removed"
            )
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
        database.close()


if __name__ == "__main__":
    main()
