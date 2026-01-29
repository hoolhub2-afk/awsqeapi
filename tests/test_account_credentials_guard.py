import asyncio
import json

import pytest
from fastapi import HTTPException

from src.services import account_service


class DummyDB:
    def __init__(self):
        self.update_calls = []

    async def fetchall(self, *_args, **_kwargs):
        return [
            {
                "id": "valid-account",
                "clientId": "cid",
                "clientSecret": "secret",
                "refreshToken": "refresh",
                "enabled": 1,
            },
            {
                "id": "kiro-builder-id-account",
                "clientId": "kiro-client-id",
                "clientSecret": "kiro-client-secret",
                "refreshToken": "kiro-refresh",
                "other": json.dumps({"provider": "kiro", "authMethod": "builder-id", "idcRegion": "us-east-1"}),
                "enabled": 1,
            },
            {
                "id": "broken-account",
                "clientId": "",
                "clientSecret": "secret",
                "refreshToken": None,
                "enabled": 1,
            },
        ]

    async def execute(self, query, params):
        self.update_calls.append((query, params))
        return 1


class DummyCreateDB:
    def __init__(self):
        self.payload = None

    async def execute(self, _query, params):
        self.payload = params
        return 1

    async def fetchone(self, *_args, **_kwargs):
        return {
            "id": self.payload[0],
            "label": self.payload[1],
            "clientId": self.payload[2],
            "clientSecret": self.payload[3],
            "refreshToken": self.payload[4],
            "accessToken": self.payload[5],
            "expires_at": self.payload[6],
            "other": self.payload[7],
            "last_refresh_time": self.payload[8],
            "last_refresh_status": self.payload[9],
            "created_at": self.payload[10],
            "updated_at": self.payload[11],
            "enabled": self.payload[12],
        }


def test_list_enabled_accounts_skips_incomplete_accounts(monkeypatch):
    dummy = DummyDB()

    def fake_get_db():
        return dummy

    monkeypatch.setattr(account_service, "get_database_backend", fake_get_db)
    monkeypatch.setattr(account_service, "AUTO_DISABLE_INCOMPLETE_ACCOUNTS", False)

    accounts = asyncio.run(account_service.list_enabled_accounts())
    assert [acc["id"] for acc in accounts] == ["valid-account", "kiro-builder-id-account"]
    assert not dummy.update_calls, "auto-disable should not run when feature is off"


def test_list_enabled_accounts_auto_disables_when_enabled(monkeypatch):
    dummy = DummyDB()

    def fake_get_db():
        return dummy

    monkeypatch.setattr(account_service, "get_database_backend", fake_get_db)
    monkeypatch.setattr(account_service, "AUTO_DISABLE_INCOMPLETE_ACCOUNTS", True)

    accounts = asyncio.run(account_service.list_enabled_accounts())
    assert [acc["id"] for acc in accounts] == ["valid-account", "kiro-builder-id-account"]
    assert dummy.update_calls, "auto-disable should fire when feature flag is on"


def test_create_account_allows_missing_refresh_token(monkeypatch):
    dummy = DummyCreateDB()

    def fake_get_db():
        return dummy

    monkeypatch.setattr(account_service, "get_database_backend", fake_get_db)

    result = asyncio.run(
        account_service.create_account_from_tokens(
            client_id="cid",
            client_secret="secret",
            access_token="access",
            refresh_token=None,
            label=None,
            enabled=True,
        )
    )

    assert result["clientId"] == "cid"
    assert result["refreshToken"] is None
