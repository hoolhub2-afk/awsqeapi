from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.routers.openai import router


def test_v1_models_is_public():
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)
    resp = client.get("/v1/models")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["object"] == "list"
    ids = {item["id"] for item in payload["data"]}
    assert "auto" in ids
    assert "claude-sonnet-4" in ids
    assert "claude-sonnet-4.5" in ids
    assert "claude-haiku-4.5" in ids
    assert "claude-opus-4.5" in ids
    assert "claude-opus-4-1-20250805" not in ids
    assert "claude-opus-4-5-20251101" not in ids
    assert "claude-opus-4-20250514" not in ids
    assert "claude-sonnet-4-5-20250929" not in ids
    assert "claude-sonnet-4-20250514" not in ids
    assert "claude-haiku-4-5-20251001" not in ids
    assert "claude-3-5-haiku-20241022" not in ids


def test_v1_models_ignores_invalid_authorization_header():
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)
    resp = client.get("/v1/models", headers={"Authorization": "Bearer not-a-sk-key"})

    assert resp.status_code == 200
