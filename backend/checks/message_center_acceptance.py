"""Real Message Center + Duet API/worker acceptance on a disposable PG schema."""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_stack import environment
from postgres import TestDatabase

BACKEND = Path(__file__).resolve().parents[1]
CENTER = BACKEND.parent.parent / "message-center" / "backend"
FIXTURE = BACKEND / "checks" / "message_center_fixture.py"


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(label, callback, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = callback()
            if value:
                return value
        except (httpx.HTTPError, psycopg.Error, KeyError, IndexError):
            pass
        time.sleep(.2)
    raise AssertionError(f"Timed out waiting for {label}")


def main():
    private = environment("worker")
    os.environ["DATABASE_URL"] = private["DATABASE_URL"]
    database = TestDatabase()
    procs, handles = [], []
    with tempfile.TemporaryDirectory(prefix="message-center-acceptance-") as directory:
        root = Path(directory)
        duet_port, center_port = port(), port()
        token = "acceptance-" + uuid4().hex
        env = {**private, "DATABASE_URL": database.url, "AGENT_DB_URL": database.url,
            "TWINTEAMS_ENV": "local", "TWINTEAMS_DEVELOPMENT_MODE": "true",
            "AGENT_API": "center-offline", "AGENT_PROVIDER": "openai", "AGENT_MODEL": "gpt-6-luna",
            "OPENAI_API_KEY": "offline-acceptance", "MESSAGE_CENTER_ACCEPTANCE": "true",
            "TWINTEAMS_MESSAGE_CENTER_URL": f"http://127.0.0.1:{center_port}",
            "TWINTEAMS_MESSAGE_CENTER_TOKEN": token, "MESSAGE_CENTER_SERVICE_TOKEN": token,
            "DUET_BASE_URL": f"http://127.0.0.1:{duet_port}", "MESSAGE_CENTER_TEST_FIXTURE": "true",
            "TWINTEAMS_REDIS_PREFIX": "center-acceptance:" + uuid4().hex,
            "TWINTEAMS_COMPENSATION_SECONDS": "2", "TWINTEAMS_ONBOARDING_ENABLED": "false",
            "TWINTEAMS_WORKSPACE_ROOT": str(root / "workspaces"), "AGENT_MEMORY_DIR": str(root / "memory")}

        def spawn(name, command, cwd, child_env=env):
            log = (root / (name + ".log")).open("a")
            handles.append(log)
            process = subprocess.Popen(command, cwd=cwd, env=child_env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT)
            procs.append(process)
            return process

        def stop(process):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)

        def sql(query, params=()):
            with psycopg.connect(database.url) as connection, connection.cursor() as cursor:
                cursor.execute(query, params)
                return cursor.fetchall()

        def check(response):
            response.raise_for_status()
            return response.json()

        def publish_from_owner(client, title):
            return check(client.post("/api/chats/private-alex/messages", json={"body": "CENTER PUBLISH " + title,
                "client_request_key": uuid4().hex}))["request"]["id"]

        try:
            init = spawn("duet-init", [sys.executable, str(FIXTURE), "init"], BACKEND)
            if init.wait(timeout=30):
                raise RuntimeError("Duet isolated provisioning failed")
            center_python = str(CENTER / ".venv" / "bin" / "python")
            center_env = {**env, "REDIS_URL": ""}
            center_init = spawn("center-init", [center_python, "-m", "app.init_db"], CENTER, center_env)
            if center_init.wait(timeout=30):
                raise RuntimeError("Message Center isolated provisioning failed")
            center_api = spawn("center-api", [center_python, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(center_port), "--log-level", "warning"], CENTER, center_env)
            spawn("duet-api", [sys.executable, str(FIXTURE), "api", "--port", str(duet_port)], BACKEND)
            duet_worker = spawn("duet-worker", [sys.executable, str(FIXTURE), "worker"], BACKEND)
            center_worker = spawn("center-worker", [center_python, "-m", "app.worker"], CENTER, center_env)
            with httpx.Client(base_url=f"http://127.0.0.1:{duet_port}", timeout=5) as alex, \
                 httpx.Client(base_url=f"http://127.0.0.1:{duet_port}", timeout=5) as maya, \
                 httpx.Client(base_url=f"http://127.0.0.1:{center_port}", timeout=5) as mc:
                wait_for("Duet API", lambda: alex.get("/health").status_code == 200)
                wait_for("center API", lambda: mc.get("/health").status_code == 200)
                check(alex.post("/api/auth/login", json={"username": "DEV-AAA"}))
                check(maya.post("/api/auth/login", json={"username": "DEV-BBB"}))
                check(mc.post("/api/auth/login", json={"username": "DEV-AAA"}))
                publish_from_owner(alex, "report")
                wait_for("report published", lambda: any(row["title"] == "report" for row in check(mc.get("/api/messages"))))
                stop(center_worker)
                center_worker = spawn("center-worker-report", [center_python, "-m", "app.worker"], CENTER, center_env)
                report = wait_for("report processed", lambda: next((row for row in check(mc.get("/api/deliveries")) if row["status"] == "processed"), None))
                report_message = check(mc.get("/api/messages/" + report["message_id"]))
                assert report_message["publisher"] == "owner-alex" and report["notified"] and not report["human_read"]
                assert sql("SELECT body FROM tt_messages WHERE body = %s", ("A useful briefing arrived.",))
                # Repeat the identical transport payload: no additional request or model run.
                payload = {"delivery_id": report["id"], "message_id": report["message_id"], "version": report["version"],
                    "owner_id": report["owner_id"], "mode": report["mode"], "message": report_message}
                before = sql("SELECT count(*) FROM tt_reply_requests WHERE source='message_center'")[0][0]
                headers = {"X-Message-Center-Token": token}
                check(alex.post("/api/message-center/deliveries", headers=headers, json=payload))
                check(alex.post("/api/message-center/deliveries", headers=headers, json=payload))
                assert sql("SELECT count(*) FROM tt_reply_requests WHERE source='message_center'")[0][0] == before
                publish_from_owner(alex, "silent")
                wait_for("silent published", lambda: any(row["title"] == "silent" for row in check(mc.get("/api/messages"))))
                stop(center_worker)
                center_worker = spawn("center-worker-silent", [center_python, "-m", "app.worker"], CENTER, center_env)
                silent = wait_for("silent processed", lambda: next((row for row in check(mc.get("/api/deliveries")) if row["message_id"] != report["message_id"] and row["status"] == "processed"), None))
                assert not silent["notified"] and not silent["human_read"]
                check(maya.post("/api/chats/private-maya/messages", json={"body": "CENTER ATTEND " + report["message_id"], "client_request_key": uuid4().hex}))
                wait_for("owner attendance", lambda: any(row["owner_id"] == "owner-maya" and row["response"] == "attending" for row in check(mc.get("/api/messages/" + report["message_id"]))["responses"]))
                check(mc.post("/api/subscriptions", json={"owner_id": "owner-maya", "topic": "digest-topic", "mode": "digest"}))
                digest = check(mc.post("/api/messages", json={"title": "digest", "body": "Digest body", "topic": "digest-topic"}))
                stop(center_worker)
                center_worker = spawn("center-worker-digest", [center_python, "-m", "app.worker"], CENTER, center_env)
                receipt = wait_for("digest receipt", lambda: next((row for row in check(mc.get("/api/deliveries")) if row["message_id"] == digest["id"] and row["status"] == "accepted"), None))
                assert receipt["mode"] == "digest" and receipt["request_id"] is None
                assert not sql("SELECT id FROM tt_reply_requests WHERE input_body=%s", (receipt["id"],))
                tasks = check(maya.get("/api/me/twin/scheduled-tasks"))["items"]
                system = next(row for row in tasks if row["kind"] == "system_review")
                check(maya.post("/api/me/twin/scheduled-tasks/" + system["id"] + "/run-now", json={"idempotency_key": uuid4().hex}))
                wait_for("digest processed", lambda: next((row for row in check(mc.get("/api/deliveries")) if row["id"] == receipt["id"] and row["status"] == "processed"), None))
                assert sql("SELECT body FROM tt_messages WHERE body=%s", ("A digest briefing arrived.",))
                # The delivery is durable while the executor or center is offline.
                stop(duet_worker)
                outage = check(mc.post("/api/messages", json={"title": "outage", "body": "Offline", "recipients": ["owner-maya"]}))
                stop(center_worker)
                center_worker = spawn("center-worker-outage", [center_python, "-m", "app.worker"], CENTER, center_env)
                receipt = wait_for("outage accepted", lambda: next((row for row in check(mc.get("/api/deliveries")) if row["message_id"] == outage["id"] and row["status"] == "accepted"), None))
                stop(center_api)
                duet_worker = spawn("duet-worker-restart", [sys.executable, str(FIXTURE), "worker"], BACKEND)
                time.sleep(3)
                assert sql("SELECT status FROM tt_reply_requests WHERE id=%s", (receipt["request_id"],))[0][0] in {"queued", "running"}
                center_api = spawn("center-api-restart", [center_python, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(center_port), "--log-level", "warning"], CENTER, center_env)
                wait_for("center restart", lambda: mc.get("/health").status_code == 200)
                wait_for("outage recovery", lambda: next((row for row in check(mc.get("/api/deliveries")) if row["id"] == receipt["id"] and row["status"] == "processed"), None))
                print("PASS: real SDK publish, immediate report/silence, digest, owner attendance, duplicate delivery and outage recovery")
        except Exception:
            for handle in handles:
                handle.flush()
            for path in root.glob("*.log"):
                print(f"--- {path.name} ---", file=sys.stderr)
                print(path.read_text()[-6000:], file=sys.stderr)
            raise
        finally:
            for process in reversed(procs):
                stop(process)
            for handle in handles:
                handle.close()
            database.close()


if __name__ == "__main__":
    main()
