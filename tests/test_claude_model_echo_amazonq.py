import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.routers.claude as claude_router


def _app_with_account(account):
    app = FastAPI()
    app.include_router(claude_router.router)
    app.dependency_overrides[claude_router.require_account] = lambda: account
    return app


def test_claude_nonstreaming_model_is_amazonq(monkeypatch):
    account = {"id": "acc1", "accessToken": "t1"}

    async def _noop_async(*args, **kwargs):
        return None

    captured = {}

    def _convert(req, conversation_id=None):
        captured["conversation_id"] = conversation_id
        return {}

    monkeypatch.setattr(claude_router, "convert_claude_to_amazonq_request", _convert)
    monkeypatch.setattr(claude_router, "update_account_stats", _noop_async)

    async def fake_send_chat_request(*args, **kwargs):
        # Import ChatResponse from amazonq_client
        from src.integrations.amazonq_client import ChatResponse, StreamTracker

        async def gen():
            tracker.has_content = True
            yield ("initial-response", {"conversationId": "cid"})
            yield ("assistantResponseEvent", {"content": "hello"})
            yield ("assistantResponseEnd", {})

        tracker = StreamTracker()
        # Fix: Return ChatResponse object instead of tuple
        return ChatResponse(text=None, text_stream=None, tracker=tracker, event_stream=gen())

    monkeypatch.setattr(claude_router, "send_chat_request", fake_send_chat_request)

    client = TestClient(_app_with_account(account))
    payload = {
        "model": "claude-opus-4-1-20250805",
        "stream": False,
        "conversationId": "cid-123",
        "messages": [{"role": "user", "content": "hi"}],
    }
    resp = client.post("/v1/messages", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "claude-opus-4.5"
    assert data["conversationId"] == "cid-123"
    assert data["conversation_id"] == "cid-123"
    assert resp.headers.get("x-conversation-id") == "cid-123"
    assert resp.headers.get("x-conversationid") == "cid-123"
    assert captured.get("conversation_id") == "cid-123"


def test_claude_streaming_message_start_model_is_amazonq(monkeypatch):
    account = {"id": "acc1", "accessToken": "t1"}

    async def _noop_async(*args, **kwargs):
        return None

    captured = {}

    def _convert(req, conversation_id=None):
        captured["conversation_id"] = conversation_id
        return {}

    monkeypatch.setattr(claude_router, "convert_claude_to_amazonq_request", _convert)
    monkeypatch.setattr(claude_router, "update_account_stats", _noop_async)

    async def fake_send_chat_request(*args, **kwargs):
        # Import ChatResponse from amazonq_client
        from src.integrations.amazonq_client import ChatResponse, StreamTracker

        async def gen():
            tracker.has_content = True
            yield ("initial-response", {"conversationId": "cid"})
            yield ("assistantResponseEvent", {"content": "hello"})
            yield ("assistantResponseEnd", {})

        tracker = StreamTracker()
        # Fix: Return ChatResponse object instead of tuple
        return ChatResponse(text=None, text_stream=None, tracker=tracker, event_stream=gen())

    monkeypatch.setattr(claude_router, "send_chat_request", fake_send_chat_request)

    client = TestClient(_app_with_account(account))
    payload = {
        "model": "claude-opus-4-1-20250805",
        "stream": True,
        "conversationId": "cid-456",
        "messages": [{"role": "user", "content": "hi"}],
    }
    with client.stream("POST", "/v1/messages", json=payload) as resp:
        assert resp.status_code == 200
        assert resp.headers.get("x-conversation-id") == "cid-456"
        assert resp.headers.get("x-conversationid") == "cid-456"
        for line in resp.iter_lines():
            if not line:
                continue
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="ignore")
            if not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            if not data_str or data_str == "[DONE]":
                continue
            event = json.loads(data_str)
            if event.get("type") == "message_start":
                assert event["message"]["model"] == "claude-opus-4.5"
                break
        else:
            raise AssertionError("no message_start event received")
    assert captured.get("conversation_id") == "cid-456"
