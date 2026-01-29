import hashlib
import json
import os
import time
from typing import Any, Dict, Optional, Tuple


_SEEN: Dict[str, int] = {}


def reset_state() -> None:
    _SEEN.clear()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _enabled() -> bool:
    return _env_int("REQUEST_DEDUPE_WINDOW_MS", 0) > 0


def trace_enabled() -> bool:
    val = os.getenv("REQUEST_TRACE_ENABLED", "false").strip().lower()
    return val in ("1", "true", "yes", "on")


def dedupe_enabled() -> bool:
    return _enabled()

def ignore_model() -> bool:
    val = os.getenv("REQUEST_DEDUPE_IGNORE_MODEL", "false").strip().lower()
    return val in ("1", "true", "yes", "on")


def _window_ms() -> int:
    return max(0, _env_int("REQUEST_DEDUPE_WINDOW_MS", 0))


def _max_keys() -> int:
    return max(100, _env_int("REQUEST_DEDUPE_MAX_KEYS", 2000))


def _bypass(headers: Any) -> bool:
    try:
        return str(headers.get("x-dedupe-bypass", "")).strip() in ("1", "true", "yes")
    except (AttributeError, TypeError):
        return False


def _short_json(obj: Any, limit: int = 4096) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        s = str(obj)
    return s if len(s) <= limit else s[:limit]


def fingerprint(obj: Any) -> str:
    s = _short_json(obj)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def fingerprint_drop(obj: Any, drop_keys: Tuple[str, ...]) -> str:
    if not isinstance(obj, dict):
        return fingerprint(obj)
    drop = set(drop_keys)
    return fingerprint({k: v for k, v in obj.items() if k not in drop})


def _client_ip(request: Any) -> str:
    try:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    except (AttributeError, TypeError):
        pass
    try:
        return request.client.host or "unknown"
    except AttributeError:
        return "unknown"


def _user_agent(request: Any) -> str:
    try:
        return request.headers.get("user-agent", "-")
    except AttributeError:
        return "-"


def _header(request: Any, name: str) -> str:
    try:
        return str(request.headers.get(name, "")).strip()
    except Exception:
        return ""


def _end_user_id(request: Any) -> str:
    val = _header(request, "x-end-user-id") or _header(request, "x-user-id")
    if not val:
        return ""
    return val[:80]


def _auth_fingerprint(request: Any) -> str:
    raw = _header(request, "authorization")
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _client_id(request: Any) -> str:
    uid = _end_user_id(request)
    if uid:
        return f"u:{uid}"
    auth = _auth_fingerprint(request)
    if auth:
        return f"k:{auth}"
    return _client_ip(request)


def make_key(request: Any, path: str, model: str, fp: str) -> str:
    return f"{_client_id(request)}|{path}|{model}|{fp}"


def check_and_mark(key: str) -> Tuple[bool, int]:
    now = _now_ms()
    win = _window_ms()
    last = _SEEN.get(key)
    if last is not None and win > 0 and (now - last) < win:
        return True, max(1, win - (now - last))
    _SEEN[key] = now
    if len(_SEEN) > _max_keys():
        _SEEN.clear()
    return False, 0


def trace_line(request: Any, path: str, model: str, stream: Any, fp: str) -> str:
    ip = _client_ip(request)
    ua = _user_agent(request)
    try:
        rid = request.headers.get("x-request-id") or request.headers.get("x-amzn-trace-id") or "-"
    except AttributeError:
        rid = "-"
    return f"trace path={path} model={model} stream={stream} ip={ip} fp={fp[:12]} rid={rid} ua={ua}"


def should_block(request: Any, key: str) -> Tuple[bool, Optional[int]]:
    if not _enabled():
        return False, None
    if _bypass(getattr(request, "headers", {})):
        return False, None
    dup, retry_after_ms = check_and_mark(key)
    return dup, (retry_after_ms if dup else None)
