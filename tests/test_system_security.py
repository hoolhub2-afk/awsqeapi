import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.routers import system
from src.api import dependencies


@pytest.fixture
def secured_app(monkeypatch):
    monkeypatch.setattr(dependencies, "ADMIN_API_KEY", "adminkey")
    monkeypatch.setattr(dependencies, "ADMIN_PASSWORD", "adminpass")
    monkeypatch.setattr(dependencies, "CONSOLE_ENABLED", True)

    app = FastAPI()
    app.include_router(system.router)
    return app


def test_health_requires_admin_token(secured_app):
    client = TestClient(secured_app)
    resp = client.get("/health")
    assert resp.status_code == 401


def test_metrics_requires_admin_token(secured_app):
    client = TestClient(secured_app)
    resp = client.get("/metrics")
    assert resp.status_code == 401
