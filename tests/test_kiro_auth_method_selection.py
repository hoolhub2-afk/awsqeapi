"""
Kiro 授权测试 - 仅支持 Builder ID
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import dependencies
from src.routers import kiro
from src.services import account_service


class _DummyDB:
    async def execute(self, *args, **kwargs):
        return None


def _setup_admin(monkeypatch):
    monkeypatch.setattr(dependencies, "ADMIN_API_KEY", "adminkey")
    monkeypatch.setattr(dependencies, "ADMIN_PASSWORD", "adminpass")
    monkeypatch.setattr(dependencies, "CONSOLE_ENABLED", True)
    monkeypatch.setattr(dependencies.ip_whitelist, "is_allowed", lambda _ip: True)


def test_kiro_auth_start_builder_id(monkeypatch):
    """测试 Kiro Builder ID 授权启动"""
    _setup_admin(monkeypatch)
    monkeypatch.setattr(account_service, "get_database_backend", lambda: _DummyDB())
    account_service.AUTH_SESSIONS.clear()

    async def fake_register_client_min():
        return "cid", "csec", "2026-12-31T23:59:59Z"

    async def fake_device_authorize(client_id: str, client_secret: str, start_url=None):
        return {
            "deviceCode": "dc",
            "interval": 1,
            "expiresIn": 600,
            "verificationUriComplete": "https://example.invalid/verify?c=dc",
            "userCode": "ABCD-EFGH",
        }

    async def fake_poll_task(auth_id: str, sess: dict):
        return None

    monkeypatch.setattr(kiro, "register_client_min", fake_register_client_min)
    monkeypatch.setattr(kiro, "device_authorize", fake_device_authorize)
    monkeypatch.setattr(kiro, "_poll_kiro_builder_id", fake_poll_task)

    app = FastAPI()
    app.include_router(kiro.router)
    client = TestClient(app)

    resp = client.post(
        "/v2/kiro/auth/start",
        json={"label": "test-account", "enabled": True},
        cookies={"admin_token": "adminkey"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "authId" in data
    assert data["authUrl"].startswith("https://")
    assert data["userCode"] == "ABCD-EFGH"
    assert data["interval"] == 1
    assert data["expiresIn"] == 600
