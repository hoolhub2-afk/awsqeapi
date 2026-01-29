import os
import uuid
import time
import httpx
import asyncio
import logging

from fastapi import APIRouter, HTTPException, Response, Request, Depends
from src.core.limiter import limiter
from src.api.schemas import AuthStartBody, PasswordVerify
from src.api.dependencies import ADMIN_API_KEY, DEBUG, require_admin, get_client_ip
from src.core.database import get_database_backend, row_to_dict
from src.services.account_service import (
    create_account_from_tokens,
    poll_token_device_code,
    register_client_min,
    device_authorize,
    save_auth_session,
    load_auth_session,
)
from src.security.auth import secure_error_detail, security_auditor, ip_whitelist
# Critical Fix: Blocker #1 - 使用 bcrypt 密码验证
from src.security.password import password_manager

logger = logging.getLogger(__name__)
router = APIRouter()

# Cookie security settings
_secure_cookie_env = os.getenv("SECURE_COOKIE", "auto").strip().lower()
if _secure_cookie_env in ("true", "1", "yes"):
    SECURE_COOKIE_DEFAULT = True
elif _secure_cookie_env in ("false", "0", "no"):
    SECURE_COOKIE_DEFAULT = False
else:
    SECURE_COOKIE_DEFAULT = None  # Auto-detect
COOKIE_SAMESITE = "lax"

async def _poll_device_flow_and_create_account(auth_id: str) -> None:
    sess = await load_auth_session(auth_id)
    if not sess or sess.get("status") != "pending":
        return
    try:
        toks = await poll_token_device_code(
            sess["clientId"],
            sess["clientSecret"],
            sess["deviceCode"],
            sess["interval"],
            sess["expiresIn"],
            max_timeout_sec=300,
        )
        latest = await load_auth_session(auth_id)
        if not latest or latest.get("status") != "pending":
            return
        access_token = toks.get("accessToken")
        refresh_token = toks.get("refreshToken")
        if not access_token:
            raise HTTPException(status_code=502, detail="No accessToken returned from OIDC")
        acc = await create_account_from_tokens(
            latest["clientId"],
            latest["clientSecret"],
            access_token,
            refresh_token,
            latest.get("label"),
            latest.get("enabled", True),
            expires_in=toks.get("expiresIn"),
        )
        latest["status"] = "completed"
        latest["accountId"] = acc.get("id")
        latest["error"] = None
        await save_auth_session(auth_id, latest)
    except TimeoutError:
        sess["status"] = "timeout"
        await save_auth_session(auth_id, sess)
    except httpx.HTTPError as e:
        sess["status"] = "error"
        sess["error"] = str(e) if DEBUG else "Authentication failed"
        await save_auth_session(auth_id, sess)
    except Exception as e:
        sess["status"] = "error"
        sess["error"] = str(e) if DEBUG else "Authentication failed"
        await save_auth_session(auth_id, sess)

def _should_use_secure_cookie(request: Request) -> bool:
    """Decide whether admin cookie should be marked secure."""
    if SECURE_COOKIE_DEFAULT is not None:
        return SECURE_COOKIE_DEFAULT
    # 检查多个可能的协议头
    proto = (
        request.headers.get("x-forwarded-proto") or
        request.headers.get("x-scheme") or
        request.url.scheme
    )
    return proto.lower() == "https"

@router.post("/v2/auth/start")
async def auth_start(body: AuthStartBody, _: bool = Depends(require_admin)):
    """
    Start device authorization and return verification URL for user login.
    """
    try:
        cid, csec, reg_expires_at = await register_client_min()
        dev = await device_authorize(cid, csec)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=secure_error_detail(e, "Authentication service error"))

    auth_id = str(uuid.uuid4())
    sess = {
        "clientId": cid,
        "clientSecret": csec,
        "deviceCode": dev.get("deviceCode"),
        "interval": int(dev.get("interval", 1)),
        "expiresIn": int(dev.get("expiresIn", 600)),
        "registrationExpiresAt": reg_expires_at,
        "verificationUriComplete": dev.get("verificationUriComplete"),
        "userCode": dev.get("userCode"),
        "startTime": int(time.time()),
        "label": body.label,
        "enabled": True if body.enabled is None else bool(body.enabled),
        "status": "pending",
        "error": None,
        "accountId": None,
    }
    await save_auth_session(auth_id, sess)
    asyncio.create_task(_poll_device_flow_and_create_account(auth_id))
    return {
        "authId": auth_id,
        "verificationUriComplete": sess["verificationUriComplete"],
        "userCode": sess["userCode"],
        "expiresIn": sess["expiresIn"],
        "interval": sess["interval"],
        "registrationExpiresAt": sess.get("registrationExpiresAt"),
    }

@router.get("/v2/auth/status/{auth_id}")
async def auth_status(auth_id: str, _: bool = Depends(require_admin)):
    sess = await load_auth_session(auth_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Auth session not found")
    now_ts = int(time.time())
    deadline = sess["startTime"] + min(int(sess.get("expiresIn", 600)), 300)
    remaining = max(0, deadline - now_ts)
    return {
        "status": sess.get("status"),
        "remaining": remaining,
        "error": sess.get("error"),
        "accountId": sess.get("accountId"),
    }

@router.post("/v2/auth/claim/{auth_id}")
async def auth_claim(auth_id: str, _: bool = Depends(require_admin)):
    """
    Return account once background device flow finishes.
    """
    sess = await load_auth_session(auth_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Auth session not found")
    if sess.get("status") == "completed" and sess.get("accountId"):
        db = get_database_backend()
        row = await db.fetchone("SELECT * FROM accounts WHERE id=?", (sess["accountId"],))
        if not row:
            raise HTTPException(status_code=404, detail="Account not found")
        return {"status": "completed", "account": row_to_dict(row)}
    if sess.get("status") in ("timeout", "error"):
        return {"status": sess.get("status"), "accountId": sess.get("accountId"), "error": sess.get("error")}
    return {"status": sess.get("status"), "accountId": sess.get("accountId"), "error": sess.get("error")}

@router.post("/v2/auth/verify")
@limiter.limit("5/minute")  # Critical Fix: 防止暴力破解（每分钟最多5次）
async def verify_password(request: Request, body: PasswordVerify, response: Response):
    """
    验证管理员密码并返回安全令牌

    Critical Fix: Blocker #1 - 使用 bcrypt 哈希验证，防止时序攻击
    """
    client_ip_full = await get_client_ip(request)
    if client_ip_full and client_ip_full != "unknown" and not ip_whitelist.is_allowed(client_ip_full):
        security_auditor.record_suspicious_activity(
            "unauthorized_ip_access",
            client_ip_full,
            f"IP {client_ip_full} attempted admin login but not whitelisted",
            user_agent=request.headers.get("User-Agent", ""),
        )
        raise HTTPException(status_code=403, detail="IP not authorized")

    # 检查 IP 是否被阻止（20次失败/小时）
    client_ip = request.client.host if request.client else "unknown"
    if security_auditor.is_ip_blocked(client_ip):
        logger.warning(f"🚫 [Auth] IP {client_ip} 已被阻止（失败次数过多）")
        await asyncio.sleep(2)  # 额外延迟惩罚
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Please try again later."
        )

    # 检查密码管理器是否已配置
    if not password_manager.is_configured() or not ADMIN_API_KEY:
        raise HTTPException(status_code=503, detail="Authentication not configured")

    # ✅ 使用 bcrypt 验证密码（防时序攻击，自动常量时间比较）
    is_valid = password_manager.verify_password(body.password)

    if not is_valid:
        security_auditor.record_failed_login(client_ip, request.headers.get("User-Agent", ""))
        # 添加固定延迟防止暴力破解和时序攻击
        await asyncio.sleep(0.5)
        raise HTTPException(status_code=401, detail="Invalid password")

    # 密码验证成功，添加 IP 到动态白名单
    if client_ip_full and client_ip_full != "unknown":
        ip_whitelist.allow_ip(client_ip_full)

    secure_cookie = _should_use_secure_cookie(request)
    logger.info(f"✅ [Auth] Login success, Cookie: secure={secure_cookie}, IP: {client_ip}")

    response.set_cookie(
        key="admin_token",
        value=ADMIN_API_KEY,
        httponly=True,
        secure=secure_cookie,
        samesite=COOKIE_SAMESITE,
        path="/",
        max_age=86400  # 1天有效期，平衡安全性和用户体验
    )
    return {"success": True}

@router.post("/v2/auth/logout")
async def logout(request: Request, response: Response):
    """安全退出登录 - 清除服务端 cookie 并记录日志"""
    client_ip = await get_client_ip(request)
    user_agent = request.headers.get("User-Agent", "")[:200]
    
    # 清除 cookie
    response.delete_cookie(
        key="admin_token",
        path="/",
        secure=_should_use_secure_cookie(request),
        samesite=COOKIE_SAMESITE
    )
    
    # 记录安全事件
    from src.security.auth import log_security_event
    log_security_event("admin_logout", {
        "client_ip": client_ip,
        "user_agent": user_agent
    })
    
    logger.info(f"[Auth] Logout success, IP: {client_ip}")
    return {"success": True, "message": "已安全退出登录"}

# 向后兼容的路由别名（无 /v2 前缀）
@router.post("/auth/start")
async def auth_start_legacy(body: AuthStartBody, _: bool = Depends(require_admin)):
    return await auth_start(body, _)

@router.get("/auth/status/{auth_id}")
async def auth_status_legacy(auth_id: str, _: bool = Depends(require_admin)):
    return await auth_status(auth_id, _)

@router.post("/auth/claim/{auth_id}")
async def auth_claim_legacy(auth_id: str, _: bool = Depends(require_admin)):
    return await auth_claim(auth_id, _)

@router.post("/auth/verify")
async def verify_password_legacy(request: Request, body: PasswordVerify, response: Response):
    return await verify_password(request, body, response)

@router.post("/v2/auth/quick-add")
async def quick_add_account(body: AuthStartBody, _: bool = Depends(require_admin)):
    """快速添加账号 - 跳转到号池网站"""
    from src.core.config import ExternalConfig
    external_config = ExternalConfig()
    if not external_config.pool_service_url:
        raise HTTPException(status_code=503, detail="POOL_SERVICE_URL is not configured")

    return {
        "authId": "redirect",
        "verificationUriComplete": external_config.pool_service_url,
        "userCode": "REDIRECT",
        "expiresIn": 0,
        "interval": 0,
        "message": "正在跳转到号池网站..."
    }
