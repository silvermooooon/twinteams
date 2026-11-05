"""The opt-in budget measures product ASGI requests, never a mocked provider."""
import asyncio
import json
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import router
from checks.api_budget import ApiBudget, product_route, route_inventory


def test_inventory_includes_prefixes_and_explicit_ai_exceptions():
    routes = {(item["method"], item["path"]): item for item in route_inventory()}
    assert routes["GET", "/api/me/twin/contacts"]["kind"] == "json"
    assert routes["GET", "/api/chats/{chat_id}"]["kind"] == "json"
    assert routes["POST", "/api/local-provider-config/verify"]["kind"] == "ai"
    assert routes["GET", "/api/chats/{chat_id}/attachments/{attachment_id}"]["kind"] == "transfer"


def test_only_real_product_routes_count():
    app = FastAPI()
    app.include_router(router)
    request = httpx.Request("GET", "http://testserver/api/config?not-recorded=anything")
    with TestClient(app) as client:
        assert client.get("/api/config").status_code == 200
        assert product_route(client, request) == ("GET", "/api/config")
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
        assert product_route(client, request) is None
    synthetic = FastAPI()
    synthetic.get("/api/config")(lambda: {"synthetic": True})
    with TestClient(synthetic) as client:
        assert product_route(client, request) is None


def test_slow_success_is_a_budget_failure(tmp_path):
    class Config:
        def getoption(self, name):
            return str(tmp_path / "report.json") if name == "api_budget_report" else 3000

    collector = ApiBudget(Config())
    with pytest.raises(AssertionError, match="API budget exceeded"):
        collector.record(("GET", "/api/bootstrap"), 200,
                         time.perf_counter_ns() - 3_100_000_000, 0)
    assert collector.rows[0]["elapsed_ms"] >= 3100
    assert "not-recorded" not in str(collector.rows)


def test_plugin_records_real_sync_and_async_http(tmp_path):
    class Config:
        def getoption(self, name):
            return str(tmp_path / "report.json") if name == "api_budget_report" else 3000

    app = FastAPI()
    app.include_router(router)
    collector = ApiBudget(Config())
    collector.install()
    try:
        with TestClient(app) as client:
            assert client.get("/api/config").status_code == 200

        async def request():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                assert (await client.get("/api/config")).status_code == 200

        asyncio.run(request())
    finally:
        collector.finish()
    report = json.loads((tmp_path / "report.json").read_text())
    config = next(item for item in report["routes"] if item["path"] == "/api/config")
    assert config["successful_calls"] == 2
    assert all(item["status"] == 200 for item in report["requests"])
