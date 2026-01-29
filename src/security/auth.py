"""Security utilities and helpers."""

import os
import hashlib
import hmac
import time
import logging
import secrets
import ipaddress
from typing import Optional, List, Dict, Any
from functools import wraps
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, Request

# 确保 .env 已加载
from src.core.env import env_loaded  # noqa: F401

security_logger = logging.getLogger("security")


def _truncate(value: Optional[str], limit: int = 120) -> str:
    if not value:
        return ""
    if len(value) <= limit:
        return value
    return f"{value[: max(0, limit - 3)]}..."


class SecurityConfig:
    """Load security-related settings from environment."""

    def __init__(self):
        self.jwt_secret = os.getenv("JWT_SECRET", "").encode()
        self.session_timeout = int(os.getenv("SESSION_TIMEOUT_MINUTES", "30"))
        self.rate_limit_per_minute = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
        self.max_request_size = int(os.getenv("MAX_REQUEST_SIZE_MB", "10")) * 1024 * 1024
        self.enable_request_logging = (
            os.getenv("ENABLE_REQUEST_LOGGING", "true").lower() == "true"
        )
        self.admin_ip_whitelist = self._parse_ip_whitelist()
        self.rate_limit_ip_whitelist = self._parse_rate_limit_ip_whitelist()
        self.debug_mode = os.getenv("DEBUG", "false").lower() == "true"
        self.admin_ip_auto_whitelist_ttl = int(
            os.getenv("ADMIN_IP_AUTO_WHITELIST_TTL", "86400")
        )

    def _parse_ip_whitelist(self) -> List[str]:
        whitelist = os.getenv("ADMIN_IP_WHITELIST", "").strip()
        if not whitelist:
            return []
        return [ip.strip() for ip in whitelist.split(",") if ip.strip()]

    def _parse_rate_limit_ip_whitelist(self) -> List[str]:
        whitelist = os.getenv("RATE_LIMIT_IP_WHITELIST", "").strip()
        if not whitelist:
            return []
        return [ip.strip() for ip in whitelist.split(",") if ip.strip()]


class TokenManager:
    """Simple HMAC-based token generation and validation."""

    def __init__(self, secret: bytes):
        self.secret = secret or secrets.token_bytes(64)

    def generate_token(self, user_id: str, expires_in_minutes: int = 30) -> str:
        expires_at = int(time.time()) + expires_in_minutes * 60
        payload = f"{user_id}:{expires_at}"
        signature = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}:{signature}"

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        try:
            parts = token.split(":")
            if len(parts) != 3:
                return None

            user_id, expires_at, signature = parts
            expected_payload = f"{user_id}:{expires_at}"
            expected_signature = hmac.new(
                self.secret, expected_payload.encode(), hashlib.sha256
            ).hexdigest()

            if not hmac.compare_digest(signature, expected_signature):
                return None

            if int(expires_at) < time.time():
                return None

            return {"user_id": user_id, "expires_at": int(expires_at)}
        except Exception as e:  # pragma: no cover - defensive
            security_logger.warning(f"Token verification failed: {e}")
            return None


class RateLimiter:
    """In-memory rate limiter with optional IP whitelist."""

    def __init__(
        self, max_requests_per_minute: int = 60, ip_whitelist: Optional[List[str]] = None
    ):
        self.max_requests = max_requests_per_minute
        self.requests: Dict[str, List[int]] = {}
        self.ip_whitelist = self._parse_whitelist(ip_whitelist or [])

    def _parse_whitelist(self, whitelist: List[str]) -> List:
        allowed_networks = []
        for ip_str in whitelist:
            try:
                if "/" in ip_str:
                    allowed_networks.append(ipaddress.ip_network(ip_str))
                else:
                    allowed_networks.append(ipaddress.ip_address(ip_str))
            except ValueError as e:
                security_logger.error(f"Invalid whitelist IP: {ip_str}, error: {e}")
        return allowed_networks

    def _is_ip_whitelisted(self, client_ip: str) -> bool:
        if not self.ip_whitelist:
            return False

        try:
            ip = ipaddress.ip_address(client_ip)
            for network in self.ip_whitelist:
                if isinstance(network, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
                    if ip in network:
                        return True
                elif ip == network:
                    return True
        except ValueError as e:
            security_logger.error(f"Invalid client IP: {client_ip}, error: {e}")
        return False

    def is_allowed(self, identifier: str) -> bool:
        client_ip = None
        if ":" in identifier:
            parts = identifier.split(":", 1)
            if len(parts) == 2:
                client_ip = parts[1]
        else:
            client_ip = identifier

        if client_ip and self._is_ip_whitelisted(client_ip):
            security_logger.debug(
                f"IP {client_ip} in whitelist, bypassing rate limit for {identifier}"
            )
            return True

        now = int(time.time())
        minute_start = now - (now % 60)

        if identifier not in self.requests:
            self.requests[identifier] = []

        self.requests[identifier] = [
            ts for ts in self.requests[identifier] if ts >= minute_start
        ]

        if len(self.requests[identifier]) >= self.max_requests:
            security_logger.warning(f"Rate limit hit for {identifier}")
            return False

        self.requests[identifier].append(now)
        return True


class IPWhitelist:
    """CIDR-aware whitelist checker with dynamic session allowances."""

    def __init__(self, whitelist: List[str], auto_ttl: int = 0, default_allow_all_when_empty: bool = True):
        self.allowed_networks = []
        self.dynamic_ips: Dict[str, float] = {}
        self.auto_ttl = max(0, auto_ttl)
        self.default_allow_all_when_empty = bool(default_allow_all_when_empty)
        for ip_str in whitelist:
            try:
                if "/" in ip_str:
                    self.allowed_networks.append(ipaddress.ip_network(ip_str))
                else:
                    self.allowed_networks.append(ipaddress.ip_address(ip_str))
            except ValueError as e:
                security_logger.error(f"Invalid whitelist IP: {ip_str}, error: {e}")

    def allow_ip(self, client_ip: str, ttl: Optional[int] = None) -> None:
        effective_ttl = self.auto_ttl if ttl is None else ttl
        if effective_ttl <= 0:
            return
        try:
            ip = ipaddress.ip_address(client_ip)
        except ValueError as e:
            security_logger.error(f"Invalid client IP for dynamic whitelist: {client_ip}, error: {e}")
            return
        expiry = time.time() + effective_ttl
        self.dynamic_ips[str(ip)] = expiry
        security_logger.debug(f"Added {ip} to dynamic whitelist for {effective_ttl}s")

    def _cleanup_dynamic(self) -> None:
        if not self.dynamic_ips:
            return
        now = time.time()
        expired = [ip for ip, expiry in self.dynamic_ips.items() if expiry <= now]
        for ip in expired:
            self.dynamic_ips.pop(ip, None)

    def is_allowed(self, client_ip: str) -> bool:
        self._cleanup_dynamic()

        # 本地IP始终允许
        if client_ip in ['127.0.0.1', '::1', 'localhost']:
            return True

        if not self.allowed_networks and not self.dynamic_ips:
            return self.default_allow_all_when_empty

        try:
            ip = ipaddress.ip_address(client_ip)

            # 检查动态白名单
            if str(ip) in self.dynamic_ips:
                return True

            # 检查静态白名单
            for network in self.allowed_networks:
                if isinstance(network, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
                    if ip in network:
                        return True
                else:
                    if ip == network:
                        return True

            return False
        except ValueError as e:
            security_logger.error(f"Invalid client IP: {client_ip}, error: {e}")
            return False


class InputValidator:
    """Basic payload validators."""

    @staticmethod
    def validate_account_id(account_id: str) -> bool:
        if not account_id or not isinstance(account_id, str):
            return False
        try:
            import uuid

            uuid.UUID(account_id)
            return True
        except ValueError:
            return False

    @staticmethod
    def validate_json_input(data: Any, max_size: int = 1024 * 1024) -> bool:
        import json

        if isinstance(data, str):
            if len(data.encode("utf-8")) > max_size:
                return False
            try:
                json.loads(data)
                return True
            except json.JSONDecodeError:
                return False
        if isinstance(data, dict):
            return len(str(data).encode("utf-8")) <= max_size
        return False

    @staticmethod
    def sanitize_string(text: str, max_length: int = 1000) -> str:
        if not text:
            return ""
        text = text[:max_length]
        dangerous_chars = ["<", ">", "&", '"', "'", "\x00"]
        for char in dangerous_chars:
            text = text.replace(char, "")
        return text.strip()


class SecurityAuditor:
    """Tracks suspicious activity and failed logins."""

    def __init__(self):
        self.failed_logins: Dict[str, List[float]] = {}
        self.suspicious_activities: List[Dict[str, Any]] = []

    def record_failed_login(self, client_ip: str, user_agent: str = ""):
        now = time.time()
        self.failed_logins.setdefault(client_ip, [])
        self.failed_logins[client_ip] = [ts for ts in self.failed_logins[client_ip] if now - ts < 24 * 3600]
        self.failed_logins[client_ip].append(now)
        if len(self.failed_logins[client_ip]) > 10:
            self.record_suspicious_activity(
                "multiple_failed_logins",
                client_ip,
                f"More than 10 failed logins in 24h ({len(self.failed_logins[client_ip])})",
                user_agent=user_agent,
            )

    def record_suspicious_activity(
        self, activity_type: str, client_ip: str, details: str, user_agent: str = ""
    ):
        activity = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": activity_type,
            "client_ip": client_ip,
            "details": details,
            "user_agent": user_agent,
        }
        self.suspicious_activities.append(activity)
        summary = _truncate(details, 160) or "no details"
        ua = _truncate(user_agent, 80) or "n/a"
        security_logger.warning(
            "Suspicious activity [%s] from %s - %s (UA=%s)",
            activity_type,
            client_ip or "unknown",
            summary,
            ua,
        )
        if len(self.suspicious_activities) > 1000:
            self.suspicious_activities = self.suspicious_activities[-1000:]

    def is_ip_blocked(self, client_ip: str) -> bool:
        if client_ip not in self.failed_logins:
            return False
        now = time.time()
        recent_failures = [ts for ts in self.failed_logins[client_ip] if now - ts < 3600]
        return len(recent_failures) > 20


security_config = SecurityConfig()
token_manager = TokenManager(security_config.jwt_secret)
rate_limiter = RateLimiter(
    security_config.rate_limit_per_minute, security_config.rate_limit_ip_whitelist
)

_SECURITY_LEVEL = os.getenv("SECURITY_LEVEL", "production").strip().lower()
_STRICT_ADMIN_IP_WHITELIST = _SECURITY_LEVEL in ("production", "military")
_CONSOLE_ENABLED = os.getenv("ENABLE_CONSOLE", "true").strip().lower() not in (
    "false",
    "0",
    "no",
    "disabled",
)
ip_whitelist = IPWhitelist(
    security_config.admin_ip_whitelist,
    security_config.admin_ip_auto_whitelist_ttl,
    default_allow_all_when_empty=not _STRICT_ADMIN_IP_WHITELIST,
)

# 启动时检查白名单配置
if _CONSOLE_ENABLED and _STRICT_ADMIN_IP_WHITELIST and not security_config.admin_ip_whitelist:
    security_logger.warning(
        "ADMIN_IP_WHITELIST not configured, admin interface will only allow localhost access. "
        "Configure ADMIN_IP_WHITELIST for remote admin access in production."
    )

input_validator = InputValidator()
security_auditor = SecurityAuditor()


def secure_error_detail(error: Exception, default_message: str = "operation failed") -> str:
    """Return a safe error message (detailed only in debug)."""
    security_logger.error(f"{default_message}: {error}", exc_info=True)
    if security_config.debug_mode:
        return f"{default_message}: {error}"
    return default_message


def _find_request(args: tuple, kwargs: Dict[str, Any]) -> Optional[Request]:
    request = kwargs.get("request")
    if isinstance(request, Request):
        return request
    for arg in args:
        if isinstance(arg, Request):
            return arg
    return None


def _is_https_request(request: Request) -> bool:
    scheme = (request.url.scheme or "").lower()
    if scheme == "https":
        return True
    header = (request.headers.get("x-forwarded-proto") or request.headers.get("x-forwarded-scheme") or "")
    proto = header.split(",")[0].strip().lower()
    if proto == "https":
        return True
    if (request.headers.get("x-forwarded-ssl") or "").lower() == "on":
        return True
    return (request.headers.get("front-end-https") or "").lower() == "on"


def require_https(func):
    """Enforce HTTPS unless debug is enabled."""

    @wraps(func)
    async def wrapper(*args, **kwargs):
        if security_config.debug_mode:
            return await func(*args, **kwargs)
        request = _find_request(args, kwargs)
        if request is None:
            raise HTTPException(status_code=500, detail="HTTPS enforcement requires Request parameter")
        if not _is_https_request(request):
            raise HTTPException(status_code=400, detail="HTTPS is required")
        return await func(*args, **kwargs)

    return wrapper


def log_security_event(event_type: str, details: Dict[str, Any]):
    client_ip = details.get("client_ip", "unknown")
    user_agent = _truncate(details.get("user_agent"), 80) or "n/a"
    extras = {
        key: value
        for key, value in details.items()
        if key not in {"client_ip", "user_agent"}
    }
    extras_str = ", ".join(f"{k}={v}" for k, v in extras.items())
    extras_summary = _truncate(extras_str, 160) if extras_str else "no extra details"
    security_logger.info(
        "Security event %s from %s - %s (UA=%s)",
        event_type,
        client_ip,
        extras_summary,
        user_agent,
    )


def generate_secure_random_string(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    iterations = 100000
    hashed = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    )
    return hashed.hex(), salt


def verify_password(password: str, hashed: str, salt: str) -> bool:
    expected_hash, _ = hash_password(password, salt)
    return hmac.compare_digest(expected_hash, hashed)
