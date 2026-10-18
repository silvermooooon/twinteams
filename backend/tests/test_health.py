import sys

from fastapi.testclient import TestClient

from app.main import app


def test_python_version() -> None:
    assert sys.version_info[:2] == (3, 13)


def test_health() -> None:
    from app.service import service
    service.initialize(create_schema=True)
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["development_mode"] is True
