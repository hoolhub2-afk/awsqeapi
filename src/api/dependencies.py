import os
import logging
from typing import Optional, Dict, Any, List
from fastapi import Request, Header, HTTPException, Cookie, Depends

# 确保 .env 已加载
from src.core.env import env_loaded  # noqa: F401
from slowapi.util import get_remote_address
from src.services.account_service import list_enabled_accounts
from src.security.manager import advanced_key_manager
from src.security.auth import (
    ip_whitelist, rate_limiter, security_auditor, log_security_event
)
# Critical Fix: Blocker #3 - 添加输入验证
from src.core.security_utils import SecurityValidator

logger = logging.getLogger(__name__)

CONSOLE_ENABLED: bool = os.getenv("ENABLE_CONSOLE", "true").strip().lower() not in ("false", "0", "no", "disabled")
ADMIN_API_KEY: Optional[str] = os.getenv("ADMIN_API_KEY", "").strip() or None
ADMIN_PASSWORD: Optional[str] = os.getenv("ADMIN_PASSWORD", "").strip() or None
DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"


def _select_best_account(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """选择最佳账号 - 基于加权最少使用策略

    选择策略:
    1. 优先选择错误次数少的账号
    2. 在错误次数相同时，选择成功次数少的账号（负载均衡）
    3. 考虑成功率作为权重因子

    这比简单的 random.choice() 更能实现负载均衡，避免某些账号被过度使用
    """
    if not candidates:
        raise ValueError("No candidates available")

    if len(candidates) == 1:
        return candidates[0]

    def account_score(acc: Dict[str, Any]) -> tuple:
        """计算账号评分，返回元组用于排序（越小越优先）"""
        error_count = acc.get("error_count", 0) or 0
        success_count = acc.get("success_count", 0) or 0

        # 计算成功率（避免除零）
        total = error_count + success_count
        error_rate = error_count / total if total > 0 else 0

        # 排序优先级：
        # 1. 错误率低的优先
        # 2. 使用次数少的优先（负载均衡）
        return (error_rate, success_count, error_count)

    # 按评分排序，选择最优账号
    sorted_candidates = sorted(candidates, key=account_score)
    return sorted_candidates[0]

def _extract_bearer(token_header: Optional[str]) -> Optional[str]:
    """
    Extract and validate Bearer token from Authorization header.
    Enhanced: Blocker #3 - Added format validation
    """
    if not token_header:
        return None

    # Extract token
    if token_header.startswith("Bearer "):
        raw_token = token_header.split(" ", 1)[1].strip()
    else:
        raw_token = token_header.strip()

    # Critical Fix: Blocker #3 - Validate token format before any use
    try:
        validated_token = SecurityValidator.validate_api_key(raw_token)
        return validated_token
    except ValueError as e:
        logger.warning(f"🔴 [SECURITY] Invalid API key format: {e}")
        # Return None to trigger authentication failure
        return None

async def get_client_ip(request: Request) -> str:
    """Get client real IP"""
    header_candidates = [
        "CF-Connecting-IP",
        "True-Client-IP",
        "X-Real-IP",
        "X-Forwarded-For",
    ]

    for header_name in header_candidates:
        raw_value = request.headers.get(header_name)
        if not raw_value:
            continue
        # 多值头取第一个(真实客户端IP),而不是最后一个
        candidate = raw_value.split(",", 1)[0].strip()
        if candidate:
            return candidate

    return request.client.host if request.client else "unknown"

async def resolve_account_for_key(bearer_key: Optional[str], request: Request = None) -> Dict[str, Any]:
    """
    Advanced security authorization system - supports only sk- format keys.
    Enhanced: Blocker #3 - Added input validation for account IDs
    """
    client_ip = get_remote_address(request) if request else None
    user_agent = request.headers.get("User-Agent", "") if request else ""

    if not bearer_key or not bearer_key.startswith("sk-"):
        raise HTTPException(status_code=401, detail="Invalid API key format")

    key_info = await advanced_key_manager.verify_key(bearer_key, client_ip, user_agent)
    if not key_info:
        raise HTTPException(status_code=401, detail="Invalid or compromised API key")

    # Critical Fix: Blocker #3 - Validate account ID format
    requested_account_id = request.headers.get("X-Account-Id") if request else None
    if requested_account_id:
        try:
            requested_account_id = SecurityValidator.validate_account_id(requested_account_id)
        except ValueError as e:
            logger.warning(f"🔴 [SECURITY] Invalid account ID format: {e}")
            raise HTTPException(status_code=400, detail="Invalid account ID format")

    allowed_accounts = set(key_info.allowed_account_ids or [])

    candidates = await list_enabled_accounts()
    if not candidates:
        raise HTTPException(status_code=401, detail="No enabled account available")

    if allowed_accounts:
        scoped_candidates = [acc for acc in candidates if acc["id"] in allowed_accounts]
    else:
        scoped_candidates = candidates

    if not scoped_candidates:
        raise HTTPException(status_code=403, detail="API key has no permitted accounts")

    if requested_account_id:
        match = next((acc for acc in scoped_candidates if acc["id"] == requested_account_id), None)
        if not match:
            raise HTTPException(status_code=403, detail="Account not allowed for this key")
        return match

    if key_info.default_account_id:
        preferred = next((acc for acc in scoped_candidates if acc["id"] == key_info.default_account_id), None)
        if preferred:
            return preferred

    # 使用加权最少使用策略选择账号，而非随机选择
    return _select_best_account(scoped_candidates)

async def require_account(authorization: Optional[str] = Header(default=None), request: Request = None) -> Dict[str, Any]:
    bearer = _extract_bearer(authorization)
    return await resolve_account_for_key(bearer, request)

async def require_admin(
    request: Request,
    x_admin_key: Optional[str] = Header(default=None),
    admin_token: Optional[str] = Cookie(default=None)
) -> bool:
    """Enhanced admin authentication with IP whitelist and auditing."""
    if not CONSOLE_ENABLED:
        raise HTTPException(status_code=503, detail="Management console is disabled")

    if not ADMIN_API_KEY or not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Management console not configured")

    client_ip = await get_client_ip(request)
    user_agent = request.headers.get("User-Agent", "")

    # IP Whitelist
    if not ip_whitelist.is_allowed(client_ip):
        security_auditor.record_suspicious_activity(
            "unauthorized_ip_access",
            client_ip,
            f"IP {client_ip} attempted admin access but not whitelisted",
            user_agent=user_agent
        )
        raise HTTPException(status_code=403, detail="IP not authorized")

    # Block check
    if security_auditor.is_ip_blocked(client_ip):
        raise HTTPException(status_code=429, detail="Too many requests, IP temporarily blocked")

    token = admin_token or x_admin_key

    if not token:
        security_auditor.record_failed_login(client_ip, user_agent)
        raise HTTPException(status_code=401, detail="Admin authentication required")

    if token != ADMIN_API_KEY:
        security_auditor.record_failed_login(client_ip, user_agent)
        log_security_event("admin_auth_failed", {
            "client_ip": client_ip,
            "user_agent": user_agent[:200]
        })
        raise HTTPException(status_code=403, detail="Authentication failed")

    log_security_event("admin_auth_success", {
        "client_ip": client_ip,
        "user_agent": user_agent[:200] if user_agent else "unknown"
    })

    return True
