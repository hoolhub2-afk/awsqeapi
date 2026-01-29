import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import dependencies
from src.routers import auth
from src.services import account_service


class _DummyDB:
    async def execute(self, *args, **kwargs):
        return None


@pytest.fixture
def _app(monkeypatch):
    monkeypatch.setattr(dependencies, "ADMIN_API_KEY", "adminkey")
    monkeypatch.setattr(dependencies, "ADMIN_PASSWORD", "adminpass")
    monkeypatch.setattr(dependencies, "CONSOLE_ENABLED", True)
    monkeypatch.setattr(dependencies.ip_whitelist, "is_allowed", lambda _ip: True)

    async def fake_register_client_min():
        return "cid", "csec"

    async def fake_device_authorize(client_id: str, client_secret: str):
        return {
            "deviceCode": "dc",
            "interval": 1,
            "expiresIn": 600,
            "verificationUriComplete": "https://example.invalid/verify?c=dc",
            "userCode": "ABCD-EFGH",
        }

    async def fake_poll_task(auth_id: str):
        return None

    monkeypatch.setattr(auth, "register_client_min", fake_register_client_min)
    monkeypatch.setattr(auth, "device_authorize", fake_device_authorize)
    monkeypatch.setattr(auth, "_poll_device_flow_and_create_account", fake_poll_task)
    monkeypatch.setattr(account_service, "get_database_backend", lambda: _DummyDB())

    account_service.AUTH_SESSIONS.clear()

    app = FastAPI()
    app.include_router(auth.router)
    return app


def test_auth_start_persists_session_without_corrupting_lru_store(_app):
    client = TestClient(_app)
    resp = client.post("/v2/auth/start", json={"label": "t", "enabled": True}, cookies={"admin_token": "adminkey"})
    assert resp.status_code == 200

    auth_id = resp.json()["authId"]
    assert isinstance(account_service.AUTH_SESSIONS[auth_id], account_service.TimedAuthSession)

    loaded = asyncio.run(account_service.load_auth_session(auth_id))
    assert loaded is not None
