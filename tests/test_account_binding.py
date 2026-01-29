import types

import pytest
from fastapi import Request, HTTPException

from src.api import dependencies


def build_request(headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "headers": [(key.encode("latin-1"), value.encode("latin-1")) for key, value in headers.items()],
        "client": ("127.0.0.1", 0),
        "method": "GET",
        "scheme": "http",
        "path": "/",
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_resolve_account_rejects_disallowed_accounts(monkeypatch):
    async def fake_verify_key(*_):
        return types.SimpleNamespace(allowed_account_ids=["acc-1"], default_account_id=None)

    monkeypatch.setattr(dependencies, "advanced_key_manager", types.SimpleNamespace(verify_key=fake_verify_key))

    async def fake_list_enabled_accounts():
        return [{"id": "acc-1"}, {"id": "acc-2"}]

    monkeypatch.setattr(dependencies, "list_enabled_accounts", fake_list_enabled_accounts)

    request = build_request({"x-account-id": "acc-2", "user-agent": "pytest"})
    with pytest.raises(HTTPException) as exc:
        await dependencies.resolve_account_for_key("sk-demo", request)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_resolve_account_honors_request_header(monkeypatch):
    async def fake_verify_key(*_):
        return types.SimpleNamespace(allowed_account_ids=["acc-1", "acc-2"], default_account_id="acc-2")

    monkeypatch.setattr(dependencies, "advanced_key_manager", types.SimpleNamespace(verify_key=fake_verify_key))

    async def fake_list_enabled_accounts():
        return [{"id": "acc-1"}, {"id": "acc-2"}]

    monkeypatch.setattr(dependencies, "list_enabled_accounts", fake_list_enabled_accounts)

    request = build_request({"x-account-id": "acc-1", "user-agent": "pytest"})
    account = await dependencies.resolve_account_for_key("sk-demo", request)
    assert account["id"] == "acc-1"
