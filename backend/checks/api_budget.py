"""Optional real-ASGI timing/coverage for existing PostgreSQL tests.

Run pytest -p checks.api_budget --api-budget-report /tmp/duet-api-budget.json.
Only requests reaching the product's own FastAPI routes are recorded. Provider
MockTransport calls and synthetic HTTP endpoints do not count as API coverage.
"""
from __future__ import annotations

import ast
import json
import time
from pathlib import Path

import httpx
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.engine import Engine
from starlette.testclient import TestClient

APP = Path(__file__).resolve().parents[1] / "app"
AI_ROUTES = {
    ("POST", "/api/local-provider-config/verify"),
    ("POST", "/api/local-provider-config/verify-saved"),
}
TRANSFER_ROUTES = {
    ("GET", "/{path:path}"),
    ("POST", "/api/me/twin/avatar"),
    ("POST", "/api/me/twin/skills/upload"),
    ("POST", "/api/chats/{chat_id}/attachments"),
    ("POST", "/api/chats/{chat_id}/workspace/files"),
    ("GET", "/api/actors/{actor_id}/avatar"),
    ("GET", "/api/chats/{chat_id}/attachments/{attachment_id}"),
    ("GET", "/api/chats/{chat_id}/attachments/{attachment_id}/preview"),
    ("GET", "/api/chats/{chat_id}/workspace/files/{node_id}"),
    ("GET", "/api/chats/{chat_id}/workspace/files/{node_id}/preview"),
}


def route_inventory():
    """Read declarations without initializing the app or a configured database."""
    result = []
    for path in sorted(APP.glob("*.py")):
        tree = ast.parse(path.read_text())
        prefixes = {"app": ""}
        for node in tree.body:
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == "APIRouter"):
                prefix = next((item.value.value for item in node.value.keywords
                               if item.arg == "prefix" and isinstance(item.value, ast.Constant)), "")
                prefixes.update({target.id: prefix for target in node.targets
                                 if isinstance(target, ast.Name)})
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for item in node.decorator_list:
                if (not isinstance(item, ast.Call) or not isinstance(item.func, ast.Attribute)
                        or item.func.attr not in {"get", "post", "put", "patch", "delete"}
                        or not isinstance(item.func.value, ast.Name) or not item.args
                        or not isinstance(item.args[0], ast.Constant)):
                    continue
                method = item.func.attr.upper()
                route = prefixes[item.func.value.id] + item.args[0].value
                key = method, route
                result.append({"method": method, "path": route, "source": path.name,
                               "line": node.lineno,
                               "kind": "ai" if key in AI_ROUTES else
                               "transfer" if key in TRANSFER_ROUTES else "json"})
    return result


def product_route(client, request):
    transport = client._transport
    if isinstance(client, TestClient):
        app = client.app
    elif isinstance(transport, httpx.ASGITransport):
        app = transport.app
    else:
        return None
    if not isinstance(app, FastAPI):
        return None
    candidates = []
    for route in app.routes:
        if hasattr(route, "effective_route_contexts"):
            candidates.extend(route.effective_route_contexts())
        else:
            candidates.append(route)
    for route in candidates:
        endpoint = getattr(route, "original_route", route)
        if (request.method in (getattr(route, "methods", None) or ())
                and route.path_regex.fullmatch(request.url.path)
                and getattr(endpoint.endpoint, "__module__", "").startswith("app.")):
            return request.method, route.path
    return None


class ApiBudget:
    def __init__(self, config):
        self.path = Path(config.getoption("api_budget_report"))
        self.budget = config.getoption("api_budget_ms")
        self.current_test = ""
        self.rows = []
        self.sql = 0
        self.sync_send, self.async_send = httpx.Client.send, httpx.AsyncClient.send
        self.test_send = TestClient.send

    def before_sql(self, connection, *_args):
        if connection.dialect.name == "postgresql":
            self.sql += 1

    def install(self):
        collector = self

        def sync_send(client, request, *args, **kwargs):
            original = collector.test_send if isinstance(client, TestClient) else collector.sync_send
            route = product_route(client, request)
            if route is None:
                return original(client, request, *args, **kwargs)
            started, sql = time.perf_counter_ns(), collector.sql
            response = original(client, request, *args, **kwargs)
            collector.record(route, response.status_code, started, sql)
            return response

        async def async_send(client, request, *args, **kwargs):
            route = product_route(client, request)
            if route is None:
                return await collector.async_send(client, request, *args, **kwargs)
            started, sql = time.perf_counter_ns(), collector.sql
            response = await collector.async_send(client, request, *args, **kwargs)
            collector.record(route, response.status_code, started, sql)
            return response

        httpx.Client.send, httpx.AsyncClient.send = sync_send, async_send
        TestClient.send = sync_send
        event.listen(Engine, "before_cursor_execute", self.before_sql)

    def record(self, route, status, started, sql):
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        self.rows.append({"method": route[0], "path": route[1], "status": status,
                          "elapsed_ms": round(elapsed, 3), "test": self.current_test,
                          "observed_pg_statements": self.sql - sql})
        if 200 <= status < 300 and route not in AI_ROUTES:
            assert elapsed <= self.budget, (
                f"API budget exceeded: {route[0]} {route[1]} "
                f"returned {status} in {elapsed:.1f} ms (budget {self.budget:g} ms)")

    def finish(self):
        httpx.Client.send, httpx.AsyncClient.send = self.sync_send, self.async_send
        TestClient.send = self.test_send
        event.remove(Engine, "before_cursor_execute", self.before_sql)
        inventory = route_inventory()
        for route in inventory:
            samples = [row for row in self.rows if row["method"] == route["method"]
                       and row["path"] == route["path"] and 200 <= row["status"] < 300]
            route.update(successful_calls=len(samples),
                         max_ms=max((row["elapsed_ms"] for row in samples), default=None),
                         tests=sorted({row["test"] for row in samples}))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "budget_ms": self.budget, "provider_calls_measured": False,
            "notes": ["Only product FastAPI routes using TestClient/ASGITransport count.",
                      "MockTransport/provider stubs are excluded; runtime fixtures may be offline.",
                      "Statement counts observe SQLAlchemy PostgreSQL; SDK psycopg queries are not intercepted.",
                      "PG counters can overlap during concurrent requests; they are evidence, not per-request attribution.",
                      "File timings cover local fixture transfer, not arbitrary external network speed."],
            "routes": inventory, "requests": self.rows,
            "uncovered_non_ai": [route for route in inventory
                                 if route["kind"] != "ai" and not route["successful_calls"]],
        }, indent=2))


def pytest_addoption(parser):
    parser.addoption("--api-budget-report", help="Write real product API timing and route coverage")
    parser.addoption("--api-budget-ms", type=float, default=3000,
                     help="Maximum successful non-AI fixture response time (default: 3000 ms)")


def pytest_configure(config):
    if config.getoption("api_budget_report"):
        config._api_budget = ApiBudget(config)
        config._api_budget.install()


def pytest_runtest_setup(item):
    collector = getattr(item.config, "_api_budget", None)
    if collector:
        collector.current_test = item.nodeid


def pytest_sessionfinish(session):
    collector = getattr(session.config, "_api_budget", None)
    if collector:
        collector.finish()
