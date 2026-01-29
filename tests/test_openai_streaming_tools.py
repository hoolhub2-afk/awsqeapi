import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.routers.openai as openai_router


def _app_with_account(account):
    app = FastAPI()
    app.include_router(openai_router.router)
    app.dependency_overrides[openai_router.require_account] = lambda: account
    return app


def _sse_json_lines(resp):
    for raw in resp.iter_lines():
        line = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        if not line.startswith("data: "):
            continue
        data = line[6:].strip()
        if not data or data == "[DONE]":
            continue
        yield json.loads(data)


def test_openai_stream_tool_calls_emitted_and_thinking_stripped(monkeypatch):
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
            yield ("toolUseEvent", {"toolUseId": "call_1", "input": {"unit": "c"}})
            yield ("toolUseEvent", {"toolUseId": "call_1", "stop": True})
            yield ("assistantResponseEnd", {})

        tracker = StreamTracker()
        # Fix: Return ChatResponse object instead of tuple
        return ChatResponse(text=None, text_stream=None, tracker=tracker, event_stream=gen())

    monkeypatch.setattr(openai_router, "send_chat_request", fake_send_chat_request)

    client = TestClient(_app_with_account(account))
    payload = {
        "model": "claude-opus-4-1-20250805",
        "stream": True,
        "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}],
        "messages": [{"role": "user", "content": "hi"}],
    }
    resp = client.post("/v1/chat/completions", json=payload)
    events = list(_sse_json_lines(resp))
    assert events[0]["model"] == "claude-opus-4.5"
    assert "thinking" not in events[1]["choices"][0]["delta"].get("content", "")
    tool_chunks = [e for e in events if e["choices"][0]["delta"].get("tool_calls")]
    assert tool_chunks[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "get_weather"


def test_openai_stream_tools_falls_back_to_next_account(monkeypatch):
    acc1 = {"id": "acc1", "accessToken": "t1"}
    acc2 = {"id": "acc2", "accessToken": "t2"}
    used = []

    async def _noop_async(*args, **kwargs):
        return None

    async def _prepare(a, m):
        return a

    async def _list_accounts():
        return [acc2]

    monkeypatch.setattr(openai_router, "_prepare_account_with_session", _prepare)
    monkeypatch.setattr(openai_router, "update_account_stats", _noop_async)
    monkeypatch.setattr(openai_router, "list_enabled_accounts", _list_accounts)
    # 新的账号选择逻辑使用 _select_fallback_account，需要 monkeypatch _select_best_account
    from src.api import dependencies as deps_module
    monkeypatch.setattr(deps_module, "_select_best_account", lambda xs: xs[0])

    async def fake_send_chat_request(access_token, *args, **kwargs):
        # Import ChatResponse from amazonq_client
        from src.integrations.amazonq_client import ChatResponse, StreamTracker

        used.append(access_token)
        if access_token == "t1":
            raise openai_router.QuotaExhaustedException("MONTHLY_REQUEST_COUNT")

        async def gen():
            tracker.has_content = True
            yield ("initial-response", {"conversationId": "cid"})
            yield ("assistantResponseEvent", {"content": "ok"})
            yield ("assistantResponseEnd", {})

        tracker = StreamTracker()
        # Fix: Return ChatResponse object instead of tuple
        return ChatResponse(text=None, text_stream=None, tracker=tracker, event_stream=gen())

    monkeypatch.setattr(openai_router, "send_chat_request", fake_send_chat_request)

    client = TestClient(_app_with_account(acc1))
    payload = {
        "model": "claude-sonnet-4-20250514",
        "stream": True,
        "tools": [{"type": "function", "function": {"name": "x", "parameters": {"type": "object"}}}],
        "messages": [{"role": "user", "content": "hi"}],
    }
    resp = client.post("/v1/chat/completions", json=payload)
    events = list(_sse_json_lines(resp))
    assert used == ["t1", "t2"]
    assert events[0]["model"] == "claude-sonnet-4"
