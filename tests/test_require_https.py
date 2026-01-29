from starlette.requests import Request
import pytest
from fastapi import HTTPException

from src.security.auth import require_https, security_config


def _make_request(scheme="https", headers=None):
    headers = headers or {}
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "scheme": scheme,
        "method": "GET",
        "path": "/",
        "headers": raw_headers,
        "query_string": b"",
        "server": ("testserver", 443 if scheme == "https" else 80),
        "client": ("testclient", 123),
    }
    return Request(scope)


@require_https
async def _handler(request: Request):
    return "ok"


@pytest.mark.asyncio
async def test_require_https_allows_https(monkeypatch):
    monkeypatch.setattr(security_config, "debug_mode", False)
    result = await _handler(request=_make_request("https"))
    assert result == "ok"


@pytest.mark.asyncio
async def test_require_https_rejects_http(monkeypatch):
    monkeypatch.setattr(security_config, "debug_mode", False)
    with pytest.raises(HTTPException) as exc:
        await _handler(request=_make_request("http"))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_require_https_honors_forwarded_proto(monkeypatch):
    monkeypatch.setattr(security_config, "debug_mode", False)
    req = _make_request("http", {"x-forwarded-proto": "https"})
    result = await _handler(request=req)
    assert result == "ok"


@pytest.mark.asyncio
async def test_require_https_allows_http_in_debug(monkeypatch):
    monkeypatch.setattr(security_config, "debug_mode", True)
    result = await _handler(request=_make_request("http"))
    assert result == "ok"
