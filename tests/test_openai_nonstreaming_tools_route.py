import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.routers.openai as openai_router


def _app_with_account(account):
    app = FastAPI()
    app.include_router(openai_router.router)
    app.dependency_overrides[openai_router.require_account] = lambda: account
    return app


def test_openai_nonstreaming_tool_calls_returned_and_model_is_amazonq(monkeypatch):
    account = {"id": "acc1", "accessToken": "t1"}

    async def _noop_async(*args, **kwargs):
        return None

    async def _prepare(a, m):
        return a

    monkeypatch.setattr(openai_router, "_prepare_account_with_session", _prepare)
    monkeypatch.setattr(openai_router, "update_account_stats", _noop_async)
    monkeypatch.setattr(openai_router.SessionService, "bind_session_account", _noop_async)

    async def fake_send_chat_request(*args, **kwargs):
        # Import ChatResponse from amazonq_client
        from src.integrations.amazonq_client import ChatResponse, StreamTracker

        async def gen():
            tracker.has_content = True
            yield ("initial-response", {"conversationId": "cid"})
            yield ("assistantResponseEvent", {"content": "hello <thinking>x</thinking>"})
            yield ("toolUseEvent", {"toolUseId": "call_1", "name": "get_weather", "input": {"city": "sf"}})
            yield ("assistantResponseEnd", {})

        tracker = StreamTracker()
        # Fix: Return ChatResponse object instead of tuple
        return ChatResponse(text=None, text_stream=None, tracker=tracker, event_stream=gen())

    monkeypatch.setattr(openai_router, "send_chat_request", fake_send_chat_request)

    client = TestClient(_app_with_account(account))
    payload = {
        "model": "claude-opus-4-1-20250805",
        "stream": False,
        "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}],
        "messages": [{"role": "user", "content": "hi"}],
    }

    resp = client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "claude-opus-4.5"
    msg = data["choices"][0]["message"]
    assert "<thinking>" not in (msg.get("content") or "")
    assert msg["tool_calls"][0]["id"] == "call_1"
    assert msg["tool_calls"][0]["function"]["name"] == "get_weather"


def test_openai_tool_validation_returns_400_instead_of_500(monkeypatch):
    account = {"id": "acc1", "accessToken": "t1"}

    async def _noop_async(*args, **kwargs):
        return None

    async def _prepare(a, m):
        return a

    monkeypatch.setattr(openai_router, "_prepare_account_with_session", _prepare)
    monkeypatch.setattr(openai_router, "update_account_stats", _noop_async)

    client = TestClient(_app_with_account(account))
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

