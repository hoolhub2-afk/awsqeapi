import json
import time
import uuid
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from src.core.config import (
    AMAZON_Q_BASE_URL,
    AMAZON_Q_PATH,
    AMAZON_Q_USER_AGENT,
    AMAZON_Q_X_AMZ_USER_AGENT,
    AMAZON_Q_OPTOUT,
)
from src.core.database import get_database_backend, row_to_dict
from src.core.http_client import get_client, create_proxied_client
from src.services.account_service import get_account, is_access_token_expired, refresh_access_token_in_db

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _usage_path(path: str) -> str:
    clean = (path or "").strip("/")
    if not clean:
        return "getUsageLimits"
    if "generateAssistantResponse" in clean:
        return clean.replace("generateAssistantResponse", "getUsageLimits")
    return "getUsageLimits"


def _usage_url() -> str:
    base = AMAZON_Q_BASE_URL.rstrip("/")
    return f"{base}/{_usage_path(AMAZON_Q_PATH)}"


def _usage_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "user-agent": AMAZON_Q_USER_AGENT,
        "x-amz-user-agent": AMAZON_Q_X_AMZ_USER_AGENT,
        "x-amzn-codewhisperer-optout": AMAZON_Q_OPTOUT,
        "amz-sdk-request": "attempt=1; max=1",
        "amz-sdk-invocation-id": str(uuid.uuid4()),
    }


def _usage_params(profile_arn: Optional[str]) -> Dict[str, str]:
    params = {"isEmailRequired": "true", "origin": "AI_EDITOR", "resourceType": "AGENTIC_REQUEST"}
    if profile_arn:
        params["profileArn"] = profile_arn
    return params


def _parse_other(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _profile_arn(account: Dict[str, Any]) -> Optional[str]:
    return _parse_other(account.get("other")).get("profileArn")


def _usage_item(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for item in items:
        if item.get("resourceType") == "AGENTIC_REQUEST":
            return item
    return items[0] if items else None


def _usage_value(item: Optional[Dict[str, Any]], keys: Tuple[str, str]) -> Optional[float]:
    if not item:
        return None
    for key in keys:
        if item.get(key) is not None:
            return item.get(key)
    return None


def _extract_counters(data: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    used = data.get("usedCount")
    limit = data.get("limitCount")
    if used is not None and limit is not None:
        return used, limit
    item = _usage_item(data.get("usageBreakdownList") or [])
    used = _usage_value(item, ("currentUsageWithPrecision", "currentUsage"))
    limit = _usage_value(item, ("usageLimitWithPrecision", "usageLimit"))
    return used, limit


def _is_quota_exhausted(used: Optional[float], limit: Optional[float]) -> bool:
    if used is None or limit is None:
        return False
    return used >= limit


async def _mark_quota_exhausted(account_id: str) -> None:
    db = get_database_backend()
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    await db.execute(
        "UPDATE accounts SET quota_exhausted=1, enabled=0, last_refresh_status=?, updated_at=? WHERE id=?",
        ("quota_exhausted", now, account_id),
    )


async def _fetch_usage_data(token: str, profile_arn: Optional[str]) -> Dict[str, Any]:
    client = get_client()
    local_client = False
    if client is None:
        client = create_proxied_client(timeout=30.0)
        local_client = True
    try:
        resp = await client.get(_usage_url(), headers=_usage_headers(token), params=_usage_params(profile_arn))
        resp.raise_for_status()
        return resp.json()
    finally:
        if local_client:
            await client.aclose()


def _usage_base(account: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "accountId": account.get("id"),
        "label": account.get("label") or "",
        "enabled": bool(account.get("enabled")),
    }


def _usage_error(base: Dict[str, Any], message: str) -> Dict[str, Any]:
    return {**base, "status": "error", "error": message, "usageBreakdown": []}


def _usage_ok(base: Dict[str, Any], data: Dict[str, Any], used: Optional[float], limit: Optional[float], exhausted: bool) -> Dict[str, Any]:
    return {
        **base,
        "status": "exhausted" if exhausted else "ok",
        "usedCount": used,
        "limitCount": limit,
        "quotaExhausted": exhausted,
        "usageBreakdown": data.get("usageBreakdownList") or [],
    }


def _needs_refresh(account: Dict[str, Any], refresh: bool) -> bool:
    if refresh:
        return True
    if not account.get("accessToken"):
        return True
    try:
        return is_access_token_expired(account)
    except Exception:
        return True


async def _ensure_account(account: Dict[str, Any], refresh: bool) -> Tuple[Dict[str, Any], Optional[str]]:
    if not _needs_refresh(account, refresh):
        return account, None
    try:
        return await refresh_access_token_in_db(account["id"]), None
    except Exception as exc:
        return account, str(exc)


async def _prepare_account(account: Dict[str, Any], refresh: bool) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[str]]:
    base = _usage_base(account)
    updated, err = await _ensure_account(account, refresh)
    if err:
        return base, None, f"refresh failed: {err}"
    if not updated.get("accessToken"):
        return base, None, "missing accessToken"
    return base, updated, None


async def _fetch_usage_for_account(account: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        data = await _fetch_usage_data(account["accessToken"], _profile_arn(account))
        return data, None
    except Exception as exc:
        return None, str(exc)


async def _finalize_usage(base: Dict[str, Any], account: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    used, limit = _extract_counters(data)
    exhausted = _is_quota_exhausted(used, limit)
    if exhausted:
        await _mark_quota_exhausted(account["id"])
    return _usage_ok(base, data, used, limit, exhausted)


async def _usage_for_account(account: Dict[str, Any], refresh: bool) -> Dict[str, Any]:
    base, updated, err = await _prepare_account(account, refresh)
    if err:
        return _usage_error(base, err)
    data, err = await _fetch_usage_for_account(updated)
    if err:
        return _usage_error(base, err)
    return await _finalize_usage(base, updated, data)


async def _list_all_accounts() -> List[Dict[str, Any]]:
    db = get_database_backend()
    rows = await db.fetchall("SELECT * FROM accounts ORDER BY created_at DESC")
    return [row_to_dict(r) for r in rows]


async def _collect_usage(accounts: List[Dict[str, Any]], refresh: bool) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for account in accounts:
        results.append(await _usage_for_account(account, refresh))
    return results


async def get_all_usage(refresh: bool = False) -> Dict[str, Any]:
    accounts = await _list_all_accounts()
    results = await _collect_usage(accounts, refresh)
    return {"timestamp": _now_iso(), "accounts": results}


async def get_account_usage(account_id: str, refresh: bool = False) -> Dict[str, Any]:
    account = await get_account(account_id)
    result = await _usage_for_account(account, refresh)
    return {"timestamp": _now_iso(), "account": result}
