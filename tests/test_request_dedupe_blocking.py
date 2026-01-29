from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.core.request_dedupe as request_dedupe
import src.routers.claude as claude_router


def _app():
    app = FastAPI()
    app.include_router(claude_router.router)
    return app


def test_count_tokens_duplicate_is_blocked(monkeypatch):
    monkeypatch.setenv("REQUEST_DEDUPE_WINDOW_MS", "10000")
    monkeypatch.setenv("REQUEST_DEDUPE_IGNORE_MODEL", "false")
    request_dedupe.reset_state()

    client = TestClient(_app())
    payload = {"model": "claude-sonnet-4", "stream": False, "messages": [{"role": "user", "content": "hi"}]}

    r1 = client.post("/v1/messages/count_tokens?beta=true", json=payload)
    r2 = client.post("/v1/messages/count_tokens?beta=true", json=payload)

    assert r1.status_code == 200
    assert r2.status_code == 429


def test_count_tokens_duplicate_can_be_bypassed(monkeypatch):
    monkeypatch.setenv("REQUEST_DEDUPE_WINDOW_MS", "10000")
    monkeypatch.setenv("REQUEST_DEDUPE_IGNORE_MODEL", "false")
    request_dedupe.reset_state()

    client = TestClient(_app())
    payload = {"model": "claude-sonnet-4", "stream": False, "messages": [{"role": "user", "content": "hi"}]}

    r1 = client.post("/v1/messages/count_tokens?beta=true", json=payload)
    r2 = client.post(
        "/v1/messages/count_tokens?beta=true",
        json=payload,
        headers={"x-dedupe-bypass": "1"},
    )

    assert r1.status_code == 200
    assert r2.status_code == 200


def test_count_tokens_duplicate_is_scoped_by_end_user_id(monkeypatch):
    monkeypatch.setenv("REQUEST_DEDUPE_WINDOW_MS", "10000")
    monkeypatch.setenv("REQUEST_DEDUPE_IGNORE_MODEL", "false")
    request_dedupe.reset_state()

    client = TestClient(_app())
    payload = {"model": "claude-sonnet-4", "stream": False, "messages": [{"role": "user", "content": "hi"}]}

    r1 = client.post(
        "/v1/messages/count_tokens?beta=true",
        json=payload,
        headers={"x-end-user-id": "user-a"},
    )
    r2 = client.post(
        "/v1/messages/count_tokens?beta=true",
        json=payload,
        headers={"x-end-user-id": "user-b"},
    )

    assert r1.status_code == 200
    assert r2.status_code == 200


def test_count_tokens_duplicate_same_end_user_id_is_blocked(monkeypatch):
    monkeypatch.setenv("REQUEST_DEDUPE_WINDOW_MS", "10000")
    monkeypatch.setenv("REQUEST_DEDUPE_IGNORE_MODEL", "false")
    request_dedupe.reset_state()

    client = TestClient(_app())
    payload = {"model": "claude-sonnet-4", "stream": False, "messages": [{"role": "user", "content": "hi"}]}

    r1 = client.post(
        "/v1/messages/count_tokens?beta=true",
        json=payload,
        headers={"x-end-user-id": "user-a"},
    )
    r2 = client.post(
        "/v1/messages/count_tokens?beta=true",
        json=payload,
        headers={"x-end-user-id": "user-a"},
    )

    assert r1.status_code == 200
    assert r2.status_code == 429
