from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.openai_errors import register_openai_error_handlers
import src.routers.openai as openai_router


def test_openai_chat_errors_use_error_object(monkeypatch):
    app = FastAPI()
    app.include_router(openai_router.router)
    register_openai_error_handlers(app)

    account = {"id": "acc1", "accessToken": "t1"}
    app.dependency_overrides[openai_router.require_account] = lambda: account

    # Mock update_account_stats to avoid database access
    async def _noop_async(*args, **kwargs):
        return None

    monkeypatch.setattr(openai_router, "update_account_stats", _noop_async)

    client = TestClient(app)
    payload = {
        "model": "claude-sonnet-4.5",
        "stream": False,
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ],
    }
    resp = client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body
    assert "detail" not in body
    assert body["error"]["type"] == "invalid_request_error"


def test_openai_invalid_api_key_uses_error_object():
    app = FastAPI()
    app.include_router(openai_router.router)
    register_openai_error_handlers(app)

    client = TestClient(app)
    payload = {"model": "claude-sonnet-4.5", "stream": False, "messages": [{"role": "user", "content": "hi"}]}
    resp = client.post("/v1/chat/completions", json=payload, headers={"Authorization": "Bearer not-a-sk-key"})
    assert resp.status_code == 401
    body = resp.json()
    assert "error" in body
    assert body["error"]["type"] == "invalid_api_key"

