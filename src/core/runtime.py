import os
from typing import List, Optional
from urllib.parse import urlparse

# 确保 .env 已加载
from src.core.env import env_loaded  # noqa: F401


LOCAL_DEV_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8000",
]
LOCAL_DEV_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

_PRODUCTION_ALIASES = {"production", "prod"}


def _is_production_env() -> bool:
    env_value = os.getenv("APP_ENV", "development").strip().lower()
    return env_value in _PRODUCTION_ALIASES


def ensure_admin_credentials(console_enabled: bool) -> None:
    """Ensure admin credentials exist when console is enabled."""
    if not console_enabled:
        return
    admin_key = os.getenv("ADMIN_API_KEY", "").strip()
    admin_password = os.getenv("ADMIN_PASSWORD", "").strip()
    if not admin_key or not admin_password:
        raise RuntimeError("ADMIN_API_KEY and ADMIN_PASSWORD must be set when ENABLE_CONSOLE is true")


def parse_trusted_hosts(raw_hosts: str, debug_mode: bool, *, production_mode: Optional[bool] = None) -> List[str]:
    """Parse TRUSTED_HOSTS configuration."""
    strict_mode = production_mode if production_mode is not None else _is_production_env()
    hosts_env = (raw_hosts or "").strip()
    if hosts_env:
        hosts = []
        wildcard_requested = False
        for host in (entry.strip() for entry in hosts_env.split(",") if entry.strip()):
            normalized = _normalize_host_entry(host)
            if not normalized:
                continue
            if normalized == "*":
                wildcard_requested = True
                continue
            hosts.append(normalized)
        if wildcard_requested:
            if strict_mode:
                raise ValueError("Wildcard trusted hosts are not allowed when APP_ENV=production")
            return ["*"]
        if not hosts:
            raise ValueError("TRUSTED_HOSTS provided but empty after parsing")
        return hosts
    if debug_mode or not strict_mode:
        defaults = LOCAL_DEV_HOSTS.copy()
        if "*" not in defaults:
            defaults.append("*")
        return defaults
    raise ValueError("TRUSTED_HOSTS must be configured when APP_ENV=production")


def parse_cors_origins(raw_origins: str, debug_mode: bool, *, production_mode: Optional[bool] = None) -> List[str]:
    """Parse CORS_ORIGINS configuration."""
    strict_mode = production_mode if production_mode is not None else _is_production_env()
    origins_env = (raw_origins or "").strip()
    if origins_env:
        origins = []
        for entry in origins_env.split(","):
            origin = entry.strip()
            if not origin:
                continue
            if "*" in origin:
                raise ValueError("Wildcard CORS origins are not allowed")
            if "://" not in origin:
                origin = f"https://{origin}"
            origins.append(origin.rstrip("/"))
        if not origins:
            raise ValueError("CORS_ORIGINS provided but empty after parsing")
        return origins
    if debug_mode or not strict_mode:
        return LOCAL_DEV_ORIGINS
    raise ValueError("CORS_ORIGINS must be configured when APP_ENV=production")


def _normalize_host_entry(host: str) -> str:
    value = (host or "").strip()
    if not value:
        return ""
    if value == "*":
        return "*"
    candidate = value
    if "://" in candidate:
        parsed = urlparse(candidate)
        candidate = parsed.hostname or ""
    else:
        candidate = candidate.split("/", 1)[0]
    if candidate.startswith("[") and candidate.endswith("]"):
        return candidate.lower()
    if ":" in candidate:
        candidate = candidate.split(":", 1)[0]
    return candidate.lower()
