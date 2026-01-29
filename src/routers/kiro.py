"""
Kiro 授权路由模块

支持的授权方式（与参考项目 AIClient-2-API 一致）:
1. AWS Builder ID - 设备码授权
2. 批量导入 refreshToken
3. 导入 AWS 账号 (从 AWS SSO cache)
"""
import time
import uuid
import asyncio
import logging
from typing import Any, Dict, Optional, List

import httpx
from fastapi import APIRouter, Depends, HTTPException

from src.api.schemas import KiroAuthStartBody, KiroImportRefreshTokens, KiroImportAwsCredentials
from src.api.dependencies import require_admin, DEBUG
from src.core.database import get_database_backend, row_to_dict
from src.core.security_utils import mask_account_info
from src.security.auth import secure_error_detail
from src.services.account_service import (
    save_auth_session,
    load_auth_session,
    create_kiro_builder_id_account_from_tokens,
)
from src.api.oidc_flow import register_client_min, device_authorize, poll_token_device_code
from src.services.kiro_service import (
    refresh_kiro_builder_id_token,
    normalize_region,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _mask_account(account: Dict[str, Any]) -> Dict[str, Any]:
    return mask_account_info(account)


# ============ AWS Builder ID 授权 ============

async def _start_kiro_builder_id(body: KiroAuthStartBody) -> Dict[str, Any]:
    """启动 AWS Builder ID 设备码授权流程"""
    # 获取自定义 startUrl 和 region
    start_url = body.startUrl  # 支持 AWS IAM Identity Center
    region = normalize_region(body.region)

    try:
        cid, csec, reg_expires_at = await register_client_min()
        dev = await device_authorize(cid, csec, start_url=start_url)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=secure_error_detail(e, "Kiro Builder ID error"))

    auth_id = str(uuid.uuid4())
    sess = {
        "type": "kiro_builder_id",
        "clientId": cid,
        "clientSecret": csec,
        "registrationExpiresAt": reg_expires_at,
        "startUrl": start_url or "https://view.awsapps.com/start",
        "region": region,
        "deviceCode": dev.get("deviceCode"),
        "interval": int(dev.get("interval", 1)),
        "expiresIn": int(dev.get("expiresIn", 600)),
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

    async def _safe_poll_wrapper() -> None:
        """包装轮询任务，确保异常被记录"""
        try:
            await _poll_kiro_builder_id(auth_id, dict(sess))
        except Exception as e:
            logger.error(f"Background polling task failed for {auth_id[:8]}***: {e}")

    asyncio.create_task(_safe_poll_wrapper())

    return {
        "authId": auth_id,
        "authUrl": sess["verificationUriComplete"],
        "userCode": sess["userCode"],
        "expiresIn": sess["expiresIn"],
        "interval": sess["interval"],
    }


async def _poll_kiro_builder_id(auth_id: str, sess: Dict[str, Any]) -> None:
    """轮询 Builder ID 设备码授权状态"""
    try:
        toks = await poll_token_device_code(
            sess["clientId"],
            sess["clientSecret"],
            sess["deviceCode"],
            sess["interval"],
            sess["expiresIn"],
            max_timeout_sec=300,
        )
        access_token = toks.get("accessToken")
        refresh_token = toks.get("refreshToken")
        if not access_token or not refresh_token:
            raise ValueError("OIDC token response missing accessToken/refreshToken")

        other = {
            "provider": "kiro",
            "authMethod": "builder-id",
            "idcRegion": sess.get("region") or normalize_region(None),
            "source": "kiro_builder_id",
            "startUrl": sess.get("startUrl") or "https://view.awsapps.com/start",
        }
        # 添加可选字段
        if sess.get("registrationExpiresAt"):
            other["registrationExpiresAt"] = sess["registrationExpiresAt"]
        acc = await create_kiro_builder_id_account_from_tokens(
            client_id=sess["clientId"],
            client_secret=sess["clientSecret"],
            access_token=access_token,
            refresh_token=refresh_token,
            label=sess.get("label") or "Kiro(Builder ID)",
            enabled=bool(sess.get("enabled", True)),
            other=other,
            expires_in=toks.get("expiresIn"),
        )
        sess["status"] = "completed"
        sess["accountId"] = acc.get("id")
        sess["error"] = None
        await save_auth_session(auth_id, sess)
    except TimeoutError:
        sess["status"] = "timeout"
        sess["error"] = "Kiro Builder ID timeout"
        await save_auth_session(auth_id, sess)
    except httpx.HTTPError as e:
        sess["status"] = "error"
        sess["error"] = str(e) if DEBUG else "Kiro Builder ID failed"
        await save_auth_session(auth_id, sess)
    except Exception as e:
        sess["status"] = "error"
        sess["error"] = str(e) if DEBUG else "Kiro Builder ID failed"
        await save_auth_session(auth_id, sess)


@router.post("/v2/kiro/auth/start", dependencies=[Depends(require_admin)])
async def kiro_auth_start(body: KiroAuthStartBody):
    """
    启动 Kiro 授权流程

    仅支持 Builder ID 方式 (method="builder-id")
    """
    return await _start_kiro_builder_id(body)


@router.get("/v2/kiro/auth/status/{auth_id}", dependencies=[Depends(require_admin)])
async def kiro_auth_status(auth_id: str):
    """查询授权状态"""
    sess = await load_auth_session(auth_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Auth session not found")
    now_ts = int(time.time())
    deadline = int(sess.get("startTime", now_ts)) + min(int(sess.get("expiresIn", 600)), 300)
    remaining = max(0, deadline - now_ts)
    return {
        "status": sess.get("status"),
        "remaining": remaining,
        "error": sess.get("error"),
        "accountId": sess.get("accountId"),
    }


@router.post("/v2/kiro/auth/claim/{auth_id}", dependencies=[Depends(require_admin)])
async def kiro_auth_claim(auth_id: str):
    """认领已完成授权的账号"""
    sess = await load_auth_session(auth_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Auth session not found")
    if sess.get("status") != "completed" or not sess.get("accountId"):
        return {
            "status": sess.get("status"),
            "accountId": sess.get("accountId"),
            "error": sess.get("error"),
        }

    db = get_database_backend()
    row = await db.fetchone("SELECT * FROM accounts WHERE id=?", (sess["accountId"],))
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"status": "completed", "account": _mask_account(row_to_dict(row))}


# ============ 批量导入 refreshToken ============

@router.post("/v2/kiro/import/refresh-tokens", dependencies=[Depends(require_admin)])
async def kiro_import_refresh_tokens(body: KiroImportRefreshTokens):
    """
    批量导入 refreshToken

    需要提供 clientId 和 clientSecret 用于 Builder ID 刷新
    """
    region = normalize_region(body.region)
    tokens = body.refreshTokens
    client_id = body.clientId
    client_secret = body.clientSecret

    db = get_database_backend()
    existing: set[str] = set()
    if not body.skipDuplicateCheck:
        placeholders = ", ".join("?" for _ in tokens)
        rows = await db.fetchall(
            f"SELECT refreshToken FROM accounts WHERE refreshToken IN ({placeholders})",
            tuple(tokens),
        )
        for r in rows:
            rt = (r.get("refreshToken") if isinstance(r, dict) else None) or (r["refreshToken"] if r else None)
            if rt:
                existing.add(str(rt))

    seen: set[str] = set()
    created: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    for idx, rt in enumerate(tokens, start=1):
        if rt in seen:
            skipped.append({"index": idx, "reason": "duplicate in request"})
            continue
        seen.add(rt)

        if (not body.skipDuplicateCheck) and rt in existing:
            skipped.append({"index": idx, "reason": "already exists"})
            continue

        try:
            # Builder ID 方式刷新
            data = await refresh_kiro_builder_id_token(
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=rt,
                region=region,
                client=None,
            )
            other = {
                "provider": "kiro",
                "authMethod": "builder-id",
                "idcRegion": region,
                "source": "kiro_import",
                "startUrl": "https://view.awsapps.com/start",
            }
            label = (body.labelPrefix or "Kiro") + f" #{idx}"
            acc = await create_kiro_builder_id_account_from_tokens(
                client_id=client_id,
                client_secret=client_secret,
                access_token=data["accessToken"],
                refresh_token=data.get("refreshToken", rt),
                label=label,
                enabled=bool(body.enabled if body.enabled is not None else True),
                other=other,
                expires_in=data.get("expiresIn"),
            )
            created.append(_mask_account(acc))
        except Exception as e:
            failed.append({"index": idx, "reason": str(e) if DEBUG else "import_failed"})

    return {
        "region": region,
        "created": created,
        "created_count": len(created),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "failed": failed,
        "failed_count": len(failed),
    }


# ============ 导入 AWS 账号 ============

@router.post("/v2/kiro/import/aws-credentials", dependencies=[Depends(require_admin)])
async def kiro_import_aws_credentials(body: KiroImportAwsCredentials):
    """
    导入 AWS SSO 凭据用于 Kiro (Builder ID 模式)

    参考: AIClient-2-API/src/auth/kiro-oauth.js importAwsCredentials

    必需字段:
    - clientId
    - clientSecret
    - refreshToken
    """
    creds = body.credentials or {}
    rt = str(creds.get("refreshToken") or "").strip()
    client_id = str(creds.get("clientId") or "").strip()
    client_secret = str(creds.get("clientSecret") or "").strip()

    # 验证必需字段
    if not client_id:
        raise HTTPException(status_code=400, detail="missing clientId")
    if not client_secret:
        raise HTTPException(status_code=400, detail="missing clientSecret")
    if not rt:
        raise HTTPException(status_code=400, detail="missing refreshToken")

    # 确定 region
    idc_region = str(creds.get("idcRegion") or "").strip()
    region = normalize_region(body.region or creds.get("region") or idc_region)

    db = get_database_backend()
    if not body.skipDuplicateCheck:
        row = await db.fetchone("SELECT id FROM accounts WHERE refreshToken=?", (rt,))
        if row:
            raise HTTPException(status_code=409, detail="account already exists")

    refreshed = False
    token_data: Optional[Dict[str, Any]] = None

    try:
        # Builder ID 方式刷新 - 使用 AWS OIDC 端点
        token_data = await refresh_kiro_builder_id_token(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=rt,
            region=region,
            client=None,
        )
        refreshed = True
    except Exception as e:
        logger.warning(f"Token refresh failed during AWS credentials import: {e}")
        token_data = None

    access_token = (token_data or {}).get("accessToken") or creds.get("accessToken")
    refresh_token = (token_data or {}).get("refreshToken") or rt
    expires_in = (token_data or {}).get("expiresIn")

    if not access_token:
        raise HTTPException(status_code=400, detail="missing accessToken and refresh failed")

    other: Dict[str, Any] = {
        "provider": "kiro",
        "authMethod": "builder-id",
        "idcRegion": region,
        "source": "kiro_aws_import",
        "startUrl": creds.get("startUrl") or "https://view.awsapps.com/start",
    }
    # 添加可选字段
    if creds.get("registrationExpiresAt"):
        other["registrationExpiresAt"] = creds["registrationExpiresAt"]

    label = body.label or "Kiro(Builder ID)"

    acc = await create_kiro_builder_id_account_from_tokens(
        client_id=client_id,
        client_secret=client_secret,
        access_token=str(access_token),
        refresh_token=str(refresh_token),
        label=label,
        enabled=bool(body.enabled if body.enabled is not None else True),
        other=other,
        expires_in=expires_in,
    )

    return {
        "region": region,
        "authMethod": "builder-id",
        "refreshed": refreshed,
        "account": _mask_account(acc),
    }
