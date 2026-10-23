"""Repeatable private local API/worker process management.

Run with backend/.venv/bin/python checks/local_stack.py start|status|stop.
Secrets are inherited by processes, never stored in the process manifest.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

BACKEND = Path(__file__).resolve().parents[1]
RUNTIME = BACKEND.parent.parent / "agent-runtime"
STATE = BACKEND / "data" / "local-stack"
MANIFEST = STATE / "processes.json"
DOCKER_ENV = BACKEND.parent.parent.parent / "2026-10-18" / "docker-pg-docker" / ".env"


def read_env(path):
    if not path.exists():
        return {}
    if path.is_symlink():
        raise RuntimeError("Refusing a symlink environment file")
    result = {}
    for line in path.read_text().splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        value = value.removeprefix("export ")
        if "=" not in value:
            continue
        name, raw = value.split("=", 1)
        fields = shlex.split(raw, comments=True)
        result[name.strip()] = " ".join(fields)
    return result


def environment(role):
    env = dict(os.environ)
    if role == "worker":
        env.update(read_env(RUNTIME / ".env"))
    env.update(read_env(BACKEND / ".env"))
    docker = read_env(DOCKER_ENV)
    if not (env.get("TWINTEAMS_REDIS_URL") or env.get("REDIS_URL")):
        password = docker.get("REDIS_PASSWORD")
        if not password:
            raise RuntimeError("Private local Redis credentials are unavailable")
        env["REDIS_URL"] = (
            "redis://default:"
            + quote(password, safe="")
            + "@127.0.0.1:"
            + docker.get("REDIS_PORT", "6379")
            + "/0"
        )
    env.setdefault("TWINTEAMS_REDIS_URL", env.get("REDIS_URL", ""))
    env.setdefault("REDIS_URL", env["TWINTEAMS_REDIS_URL"])
    for name in ("DATABASE_URL", "AGENT_DB_URL", "TWINTEAMS_REDIS_URL"):
        value = env.get(name) or (env.get("DATABASE_URL") if name == "AGENT_DB_URL" else "")
        if urlsplit(value).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError(
                "The local launcher requires loopback PostgreSQL and Redis addresses"
            )
    env.update(
        TWINTEAMS_ENV="local",
        TWINTEAMS_DEVELOPMENT_MODE="true",
        AGENT_PROVIDER="openai",
        AGENT_API="openai-responses",
        AGENT_MODEL="gpt-6-luna",
    )
    if role == "worker":
        if not env.get("OPENAI_API_KEY"):
            raise RuntimeError("The previously authorized private OpenAI key is unavailable")
    else:
        env.pop("OPENAI_API_KEY", None)
    return env


def owned_alive(item):
    result = subprocess.run(
        ["ps", "-p", str(item["pid"]), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    return (
        result.returncode == 0
        and item["signature"] in result.stdout
        and str(BACKEND / ".venv" / "bin" / "python") in result.stdout
    )


def read_manifest():
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []


def save_manifest(items):
    MANIFEST.write_text(json.dumps(items, indent=2) + "\n")
    MANIFEST.chmod(0o600)


def terminate(items):
    for item in items:
        if owned_alive(item):
            os.killpg(item["pid"], signal.SIGTERM)
    deadline = time.monotonic() + 8
    while any(owned_alive(item) for item in items) and time.monotonic() < deadline:
        time.sleep(0.1)
    for item in items:
        if owned_alive(item):
            os.killpg(item["pid"], signal.SIGKILL)


def status():
    items = read_manifest()
    for item in items:
        print(
            f"{item['role']}: pid={item['pid']} {'running' if owned_alive(item) else 'stopped'} log={item['log']}"
        )
    if not items:
        print("No managed local processes")
    return items


def start(args):
    existing = read_manifest()
    if any(owned_alive(item) for item in existing):
        raise RuntimeError("A managed stack is already running; use status or stop first")
    ports = [args.port] + ([args.second_port] if args.second_port else [])
    for port in ports:
        with socket.socket() as connection:
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(
                    f"Port {port} is already in use; no existing process was stopped"
                )
    STATE.mkdir(parents=True, exist_ok=True)
    STATE.chmod(0o700)
    python = str(BACKEND / ".venv" / "bin" / "python")
    specs = [("worker", [python, "-m", "app.worker"], "app.worker")]
    specs.extend(
        (
            f"api-{port}",
            [
                python,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--timeout-graceful-shutdown",
                "5",
                "--ws-max-size",
                "65536",
                "--ws-max-queue",
                "64",
            ],
            f"--port {port}",
        )
        for port in ports
    )
    items = []
    children = []
    try:
        for role, command, signature in specs:
            log = STATE / (role + ".log")
            with log.open("a") as target:
                child = subprocess.Popen(
                    command,
                    cwd=BACKEND,
                    env=environment("worker" if role == "worker" else "api"),
                    stdin=subprocess.DEVNULL,
                    stdout=target,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            log.chmod(0o600)
            children.append(child)
            items.append({"role": role, "pid": child.pid, "signature": signature, "log": str(log)})
            save_manifest(items)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if any(child.poll() is not None for child in children):
                raise RuntimeError("A local process exited during startup; inspect its private log")
            ready = True
            for port in ports:
                try:
                    with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                        ready = ready and response.status == 200
                except (OSError, ValueError):
                    ready = False
            if ready:
                status()
                print(f"UI: http://127.0.0.1:{args.port}")
                return
            time.sleep(0.2)
        raise RuntimeError("Local readiness timed out; inspect the private process logs")
    except BaseException:
        terminate(items)
        for child in children:
            child.wait(timeout=5)
        save_manifest(items)
        raise


def restart_worker():
    items = read_manifest()
    workers = [item for item in items if item["role"] == "worker"]
    if len(workers) != 1:
        raise RuntimeError("Start a managed local stack before restarting its worker")
    terminate(workers)
    log = STATE / "worker.log"
    with log.open("a") as target:
        process = subprocess.Popen(
            [str(BACKEND / ".venv" / "bin" / "python"), "-m", "app.worker"],
            cwd=BACKEND,
            env=environment("worker"),
            stdin=subprocess.DEVNULL,
            stdout=target,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    workers[0]["pid"] = process.pid
    save_manifest(items)
    for _ in range(10):
        if process.poll() is not None:
            raise RuntimeError("The replacement worker exited; inspect its private log")
        time.sleep(0.1)
    status()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "status", "stop", "migrate", "restart-worker"])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--second-port", type=int, default=8001)
    args = parser.parse_args()
    if args.action == "start":
        start(args)
    elif args.action == "restart-worker":
        restart_worker()
    elif args.action == "migrate":
        raise SystemExit(
            subprocess.call(
                [
                    str(BACKEND / ".venv" / "bin" / "python"),
                    "-m",
                    "app.init_db",
                    "--backfill-activity",
                ],
                cwd=BACKEND,
                env=environment("api"),
            )
        )
    elif args.action == "stop":
        terminate(read_manifest())
        status()
    else:
        status()


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
