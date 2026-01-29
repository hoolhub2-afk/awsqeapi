import time
import uuid
import json
import logging
import hashlib
from typing import Dict, Any, List, Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from src.core.limiter import limiter
from src.core.validators import validate_uuid, validate_uuid_list, validate_label, validate_batch_size
from src.core.security_utils import mask_account_info
from src.services.account_service import (
    get_account,
    delete_account,
    update_account,
    refresh_access_token_in_db,
    list_enabled_accounts,
    verify_and_enable_accounts,
)
from src.core.database import get_database_backend, row_to_dict
from src.api.schemas import (
    AccountCreate, BatchAccountCreate, AccountUpdate
)
from src.api.dependencies import require_admin
from src.security.manager import advanced_key_manager, SECURITY_LEVEL
from src.security.advanced import KeyStatus
from src.security.auth import log_security_event
from slowapi.util import get_remote_address
import asyncio

router = APIRouter()
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------------------
# 账号去重辅助函数
# ------------------------------------------------------------------------------

def _hash_token(token: str) -> str:
    """计算 token 的 SHA256 哈希值用于去重比较"""
    return hashlib.sha256(token.encode()).hexdigest()


def _get_token_fingerprint(refresh_token: Optional[str]) -> Optional[str]:
    """获取 refreshToken 的指纹用于去重

    使用 SHA256 哈希值作为指纹, 避免直接比较原始 token
    """
    if not refresh_token or not refresh_token.strip():
        return None
    return _hash_token(refresh_token.strip())


def extract_email_from_token(access_token: Optional[str]) -> Optional[str]:
    """从 accessToken (JWT) 中提取用户邮箱作为唯一标识

    安全说明: 此处故意不验证 JWT 签名 (verify_signature=False)
    原因: 该函数仅用于提取邮箱进行账号去重，不用于认证授权
    - 提取的邮箱仅作为去重标识，不授予任何权限
    - 即使 JWT 被伪造，最多导致误判重复，不会造成安全风险
    - 我们没有上游服务的公钥，无法验证签名
    """
    if not access_token:
        return None
    try:
        # 不验证签名 - 见上方安全说明
        decoded = jwt.decode(access_token, options={"verify_signature": False})
        email = decoded.get("email")
        if email:
            return email.lower().strip()
        identity_hash = decoded.get("identityHash")
        if identity_hash:
            return identity_hash
        return decoded.get("sub")
    except Exception as exc:
        logger.debug("JWT decode failed: %s", exc)
        return None


async def check_duplicate_account(
    db,
    refresh_token: Optional[str],
    access_token: Optional[str],
    exclude_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    检查账号是否重复（基于 refreshToken 哈希或邮箱）
    返回重复的账号信息，如果没有重复返回 None
    """
    if not refresh_token and not access_token:
        return None

    # 使用哈希值比较 refreshToken, 避免直接比较原始 token
    new_token_fp = _get_token_fingerprint(refresh_token)
    email = extract_email_from_token(access_token)
    rows = await db.fetchall("SELECT * FROM accounts")

    for row in rows:
        acc = row_to_dict(row)
        if exclude_id and acc.get("id") == exclude_id:
            continue

        # 检查 refreshToken 哈希重复
        if new_token_fp:
            existing_token_fp = _get_token_fingerprint(acc.get("refreshToken"))
            if existing_token_fp and existing_token_fp == new_token_fp:
                return acc

        # 检查邮箱重复
        if email:
            existing_email = None
            other = acc.get("other")
            if other:
                try:
                    other_dict = json.loads(other) if isinstance(other, str) else other
                    existing_email = other_dict.get("email") or other_dict.get("userId")
                    if existing_email:
                        existing_email = existing_email.lower().strip()
                except Exception as exc:
                    logger.debug("Account 'other' field parse failed: account_id=%s, error=%s", acc.get("id"), exc)

            if not existing_email and acc.get("accessToken"):
                existing_email = extract_email_from_token(acc.get("accessToken"))

            if existing_email and existing_email == email:
                return acc

    return None

# ------------------------------------------------------------------------------
# Authentication Check

@router.get("/v2/auth/check")
async def check_auth(_: bool = Depends(require_admin)):
    """专门的认证检查接口"""
    return {"authenticated": True, "timestamp": int(time.time())}

# ------------------------------------------------------------------------------
# Account Status Check

@router.get("/v2/accounts/status")
async def check_accounts_status(_: bool = Depends(require_admin)):
    """检查所有账号的基本状态"""
    db = get_database_backend()

    # 获取所有账号
    accounts = await db.fetchall("SELECT * FROM accounts ORDER BY created_at DESC")

    account_statuses = []

    for account in accounts:
        status_info = {
            "id": account["id"],
            "label": account["label"],
            "enabled": bool(account["enabled"]),
            "error_count": account["error_count"],
            "success_count": account["success_count"],
            "last_refresh_time": account["last_refresh_time"],
            "last_refresh_status": account["last_refresh_status"],
            "status": "unknown",
            "status_message": "未检查"
        }

        # 检查账号基本状态（优先使用检测返回的真实状态）
        last_status = account["last_refresh_status"]
        
        # 优先处理检测返回的严重状态
        if last_status == "suspended":
            status_info["status"] = "suspended"
            status_info["status_message"] = "账号已被封禁"
        elif last_status == "quota_exhausted":
            status_info["status"] = "quota_exhausted"
            status_info["status_message"] = "配额已耗尽"
        elif last_status == "unauthorized":
            status_info["status"] = "unauthorized"
            status_info["status_message"] = "认证失败/Token过期"
        elif not account["accessToken"]:
            status_info["status"] = "no_token"
            status_info["status_message"] = "缺少访问令牌"
        elif not account["enabled"]:
            status_info["status"] = "disabled"
            status_info["status_message"] = "账号已禁用"
        elif account["error_count"] >= 100:  # MAX_ERROR_COUNT
            status_info["status"] = "error_limit"
            status_info["status_message"] = f"错误次数过多 ({account['error_count']})"
        elif last_status == "timeout":
            status_info["status"] = "timeout"
            status_info["status_message"] = "请求超时"
        elif last_status == "network_error":
            status_info["status"] = "network_error"
            status_info["status_message"] = "网络连接错误"
        elif last_status == "success":
            status_info["status"] = "active"
            status_info["status_message"] = "正常"
        elif last_status == "failed":
            status_info["status"] = "refresh_failed"
            status_info["status_message"] = "令牌刷新失败"
        elif account["accessToken"] and account["success_count"] > 0:
            status_info["status"] = "active"
            status_info["status_message"] = "正常使用中"
        else:
            status_info["status"] = "unknown"
            status_info["status_message"] = "未验证"

        account_statuses.append(status_info)

    return {
        "accounts": account_statuses,
        "total": len(account_statuses),
        "summary": {
            "active": len([a for a in account_statuses if a["status"] == "active"]),
            "disabled": len([a for a in account_statuses if a["status"] == "disabled"]),
            "error": len([a for a in account_statuses if a["status"] in ["error_limit", "refresh_failed", "check_error", "quota_exhausted", "suspended", "unauthorized"]]),
            "unknown": len([a for a in account_statuses if a["status"] in ["unknown", "rate_limited", "timeout"]])
        }
    }

@router.post("/v2/security/validate-key")
async def validate_api_key(request: Request, _: bool = Depends(require_admin)):
    """验证API密钥是否有效 - 直接使用密钥管理器验证，避免 SSRF 风险"""
    try:
        body = await request.json()
        api_key = body.get("api_key", "").strip()

        if not api_key:
            raise HTTPException(status_code=400, detail="API密钥不能为空")

        if not api_key.startswith("sk-"):
            return {
                "valid": False,
                "status": "invalid_format",
                "message": "密钥格式无效，必须以 sk- 开头"
            }

        # 直接使用密钥管理器验证，避免 SSRF 风险
        key_info = await advanced_key_manager.verify_key(api_key, None, None)
        if key_info:
            return {
                "valid": True,
                "status": "success",
                "message": "密钥验证成功",
                "key_id": key_info.key_id,
                "expires_at": key_info.expires_at.isoformat() if key_info.expires_at else None
            }
        return {
            "valid": False,
            "status": "unauthorized",
            "message": "密钥无效或已过期"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Key verification failed: {e}", exc_info=True)
        return {
            "valid": False,
            "status": "error",
            "message": f"验证过程出错: {str(e)}"
        }

# ------------------------------------------------------------------------------
# Accounts Management
# ------------------------------------------------------------------------------

@router.post("/v2/accounts", dependencies=[Depends(require_admin)])
async def create_account(body: AccountCreate):
    db = get_database_backend()
    
    # 验证和清理输入
    label = validate_label(body.label) if body.label else None

    # 去重检查
    dup = await check_duplicate_account(db, body.refreshToken, body.accessToken)
    if dup:
        email = extract_email_from_token(body.accessToken)
        raise HTTPException(
            status_code=409,
            detail=f"账号已存在（ID: {dup.get('id')}, Label: {dup.get('label')}, Email: {email or 'N/A'}），不可重复添加"
        )

    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    acc_id = str(uuid.uuid4())

    # 自动提取邮箱并存入 other 字段
    other_dict = body.other or {}
    email = extract_email_from_token(body.accessToken)
    if email and "email" not in other_dict:
        other_dict["email"] = email
    other_str = json.dumps(other_dict, ensure_ascii=False) if other_dict else None

    enabled_val = 1 if (body.enabled is None or body.enabled) else 0
    await db.execute(
        """
        INSERT INTO accounts (id, label, clientId, clientSecret, refreshToken, accessToken, other, last_refresh_time, last_refresh_status, created_at, updated_at, enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            acc_id,
            body.label,
            body.clientId,
            body.clientSecret,
            body.refreshToken,
            body.accessToken,
            other_str,
            None,
            "never",
            now,
            now,
            enabled_val,
        ),
    )
    row = await db.fetchone("SELECT * FROM accounts WHERE id=?", (acc_id,))
    # 过滤敏感字段后返回
    return filter_sensitive_fields(row_to_dict(row))

@router.post("/v2/accounts/feed", dependencies=[Depends(require_admin)])
async def create_accounts_feed(request: BatchAccountCreate):
    """
    Bulk create accounts and verify in background.
    Includes duplicate check based on refreshToken hash and email.
    """
    db = get_database_backend()
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

    # 预先获取所有已有账号用于去重 (使用哈希值)
    existing_rows = await db.fetchall("SELECT * FROM accounts")
    existing_accounts = [row_to_dict(r) for r in existing_rows]
    existing_token_fps = {
        _get_token_fingerprint(acc.get("refreshToken"))
        for acc in existing_accounts
        if acc.get("refreshToken")
    }
    existing_token_fps.discard(None)
    existing_emails = set()
    for acc in existing_accounts:
        email = None
        other = acc.get("other")
        if other:
            try:
                other_dict = json.loads(other) if isinstance(other, str) else other
                email = other_dict.get("email") or other_dict.get("userId")
                if email:
                    email = email.lower().strip()
            except Exception as exc:
                logger.debug("Account 'other' field parse failed: account_id=%s, error=%s", acc.get("id"), exc)
        if not email and acc.get("accessToken"):
            email = extract_email_from_token(acc.get("accessToken"))
        if email:
            existing_emails.add(email)

    # 请求内去重 (使用哈希值)
    seen_token_fps = set()
    seen_emails = set()
    skipped = []
    new_account_ids = []

    for i, account_data in enumerate(request.accounts):
        rt = (account_data.refreshToken or "").strip()
        rt_fp = _get_token_fingerprint(rt) if rt else None
        email = extract_email_from_token(account_data.accessToken)

        # 检查请求内重复
        if rt_fp and rt_fp in seen_token_fps:
            skipped.append({"index": i + 1, "reason": "请求内 refreshToken 重复"})
            continue
        if email and email in seen_emails:
            skipped.append({"index": i + 1, "reason": f"请求内邮箱重复 ({email})"})
            continue

        # 检查与已有账号重复
        if rt_fp and rt_fp in existing_token_fps:
            skipped.append({"index": i + 1, "reason": "已存在相同 refreshToken"})
            continue
        if email and email in existing_emails:
            skipped.append({"index": i + 1, "reason": f"已存在相同邮箱 ({email})"})
            continue

        # 标记已见
        if rt_fp:
            seen_token_fps.add(rt_fp)
        if email:
            seen_emails.add(email)

        # 创建账号
        acc_id = str(uuid.uuid4())
        other_dict = account_data.other or {}
        other_dict['source'] = 'feed'
        if email:
            other_dict['email'] = email
        other_str = json.dumps(other_dict, ensure_ascii=False)

        await db.execute(
            """
            INSERT INTO accounts (id, label, clientId, clientSecret, refreshToken, accessToken, other, last_refresh_time, last_refresh_status, created_at, updated_at, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                acc_id,
                account_data.label or f"Bulk Account {i+1}",
                account_data.clientId,
                account_data.clientSecret,
                account_data.refreshToken,
                account_data.accessToken,
                other_str,
                None,
                "never",
                now,
                now,
                0,  # Initially disabled
            ),
        )
        new_account_ids.append(acc_id)

    if new_account_ids:
        asyncio.create_task(verify_and_enable_accounts(new_account_ids))

    return {
        "status": "processing",
        "message": f"{len(new_account_ids)} accounts received, {len(skipped)} skipped due to duplicates.",
        "account_ids": new_account_ids,
        "skipped": skipped
    }

# 敏感字段列表 - 这些字段需要脱敏处理
SENSITIVE_FIELDS = {'accessToken', 'refreshToken', 'clientSecret', 'clientId'}

def mask_sensitive_value(value: str, show_chars: int = 8) -> str:
    """脱敏敏感值，只显示前几位字符"""
    if not value:
        return ""
    if len(value) <= show_chars:
        return "*" * len(value)
    return value[:show_chars] + "****"

def filter_sensitive_fields(account: Dict[str, Any]) -> Dict[str, Any]:
    """脱敏账号中的敏感字段，保留字段存在性信息"""
    result = {}
    for k, v in account.items():
        if k in SENSITIVE_FIELDS:
            # 敏感字段脱敏显示（保留前8位+****）
            result[k] = mask_sensitive_value(v) if v else ""
        else:
            result[k] = v
    return result

@router.get("/v2/accounts", dependencies=[Depends(require_admin)])
async def list_accounts():
    db = get_database_backend()
    rows = await db.fetchall(
        "SELECT * FROM accounts ORDER BY created_at DESC"
    )
    # 过滤敏感字段后返回
    return [filter_sensitive_fields(row_to_dict(r)) for r in rows]

@router.get("/v2/accounts/{account_id}", dependencies=[Depends(require_admin)])
async def get_account_detail_endpoint(account_id: str):
    db = get_database_backend()
    row = await db.fetchone(
        "SELECT id, label, enabled, error_count, success_count, last_refresh_status, last_refresh_time, created_at, updated_at FROM accounts WHERE id=?",
        (account_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    return row_to_dict(row)

@router.delete("/v2/accounts/{account_id}", dependencies=[Depends(require_admin)])
async def delete_account_endpoint(account_id: str):
    # 验证 UUID 格式
    account_id = validate_uuid(account_id, "account_id")
    return await delete_account(account_id)

@router.post("/v2/accounts/delete-quota-exhausted", dependencies=[Depends(require_admin)])
async def delete_quota_exhausted_accounts(dry_run: bool = False):
    """删除所有配额耗尽的账号

    Args:
        dry_run: 如果为 True，只返回将被删除的账号列表，不实际删除
    """
    db = get_database_backend()
    rows = await db.fetchall("SELECT id, label FROM accounts WHERE quota_exhausted=1")
    accounts_to_delete = [{"id": row_to_dict(r)["id"], "label": row_to_dict(r).get("label")} for r in rows]

    if dry_run:
        return {
            "dry_run": True,
            "would_delete_count": len(accounts_to_delete),
            "accounts": accounts_to_delete
        }

    deleted_ids = [acc["id"] for acc in accounts_to_delete]
    if deleted_ids:
        validate_batch_size(len(deleted_ids), "delete quota exhausted accounts")
        placeholders = ", ".join("?" for _ in deleted_ids)
        await db.execute(f"DELETE FROM accounts WHERE id IN ({placeholders})", tuple(deleted_ids))

    return {"deleted_count": len(deleted_ids), "deleted_ids": deleted_ids}

@router.post("/v2/accounts/delete-banned", dependencies=[Depends(require_admin)])
async def delete_banned_accounts(dry_run: bool = False):
    """删除所有封禁/禁用的账号(enabled=0 或 last_refresh_status='suspended')

    Args:
        dry_run: 如果为 True，只返回将被删除的账号列表，不实际删除
    """
    db = get_database_backend()
    rows = await db.fetchall(
        "SELECT id, label FROM accounts WHERE enabled=0 OR last_refresh_status='suspended'"
    )
    accounts_to_delete = [{"id": r["id"], "label": r.get("label")} for r in rows]

    if dry_run:
        return {
            "dry_run": True,
            "would_delete_count": len(accounts_to_delete),
            "accounts": accounts_to_delete
        }

    deleted_ids = [acc["id"] for acc in accounts_to_delete]
    if deleted_ids:
        placeholders = ", ".join("?" for _ in deleted_ids)
        await db.execute(f"DELETE FROM accounts WHERE id IN ({placeholders})", tuple(deleted_ids))

    return {"deleted_count": len(deleted_ids), "deleted_ids": deleted_ids}

@router.patch("/v2/accounts/{account_id}", dependencies=[Depends(require_admin)])
async def update_account_endpoint(account_id: str, body: AccountUpdate):
    # 验证 UUID 格式
    account_id = validate_uuid(account_id, "account_id")
    
    # 验证 label
    updates = body.dict(exclude_unset=True)
    if 'label' in updates and updates['label']:
        updates['label'] = validate_label(updates['label'])
    
    return await update_account(account_id, updates)

@router.post("/v2/accounts/{account_id}/refresh", dependencies=[Depends(require_admin)])
async def manual_refresh(account_id: str):
    # 验证 UUID 格式
    account_id = validate_uuid(account_id, "account_id")
    return await refresh_access_token_in_db(account_id)


@router.post("/v2/accounts/{account_id}/check", dependencies=[Depends(require_admin)])
async def check_account_real_status(account_id: str, request: Request):
    """
    检测账号真实状态 - 通过发送实际请求验证账号可用性
    
    返回:
    - status: success | token_error | quota_exhausted | suspended | unauthorized | timeout | network_error | unknown
    - latency_ms: 响应时间（毫秒）
    - message: 状态描述
    - detail: 详细错误信息（如果有）
    """
    import httpx
    from src.integrations.amazonq_client import send_chat_request
    
    db = get_database_backend()
    row = await db.fetchone("SELECT * FROM accounts WHERE id=?", (account_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    
    account = row_to_dict(row)
    result = {
        "account_id": account_id,
        "label": account.get("label"),
        "status": "unknown",
        "message": "未知状态",
        "detail": None,
        "latency_ms": 0,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    }
    
    # 检查基本条件
    if not account.get("accessToken"):
        # 尝试刷新 token
        if account.get("refreshToken"):
            try:
                account = await refresh_access_token_in_db(account_id)
            except Exception as e:
                result["status"] = "token_error"
                result["message"] = "Token 刷新失败"
                result["detail"] = str(e)
                return result
        else:
            result["status"] = "token_error"
            result["message"] = "缺少访问令牌且无法刷新"
            result["detail"] = "账号缺少 refreshToken"
            return result
    
    # 发送测试请求
    start_time = time.time()
    try:
        # 使用简单的测试消息
        test_messages = [{"role": "user", "content": "hi"}]

        resp = await send_chat_request(
            access_token=account["accessToken"],
            messages=test_messages,
            model="claude-sonnet-4",
            stream=True
        )

        # 消费事件流，检查是否有响应
        response_received = False
        error_message = None

        if resp.event_stream:
            async for event_type, payload in resp.event_stream:
                if event_type in ("messageStartEvent", "textEvent"):
                    response_received = True
                elif event_type == "error":
                    error_message = payload.get("message", "Unknown error")
                    break
        elif resp.text_stream:
            async for chunk in resp.text_stream:
                if chunk:
                    response_received = True
                    break
        else:
            # 非流式模式下尝试直接读取文本
            if resp.text and str(resp.text).strip():
                response_received = True
            else:
                raise Exception("Failed to establish connection to service")
        
        latency_ms = int((time.time() - start_time) * 1000)
        result["latency_ms"] = latency_ms
        
        if response_received:
            result["status"] = "success"
            result["message"] = f"账号正常 (响应时间: {latency_ms}ms)"
            # 更新账号统计
            from src.services.account_service import update_account_stats
            await update_account_stats(account_id, True)
            # 更新 last_refresh_status 为检测成功
            now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            await db.execute(
                "UPDATE accounts SET last_refresh_status=?, last_refresh_time=?, updated_at=? WHERE id=?",
                ("success", now, now, account_id)
            )
        elif error_message:
            # 分析错误类型（支持中英文关键词）
            error_lower = error_message.lower()
            if "quota" in error_lower or "throttl" in error_lower or "rate" in error_lower or "配额" in error_message:
                result["status"] = "quota_exhausted"
                result["message"] = "账号配额已耗尽,请联系管理员"
            elif "suspend" in error_lower or "banned" in error_lower or "disabled" in error_lower or "封禁" in error_message:
                result["status"] = "suspended"
                result["message"] = "账号已被封禁,请联系管理员"
            elif "unauthorized" in error_lower or "401" in error_lower or "403" in error_lower or "认证" in error_message or "授权" in error_message:
                result["status"] = "unauthorized"
                result["message"] = "认证失败,Token已过期"
            elif "timeout" in error_lower or "timed out" in error_lower or "超时" in error_message:
                result["status"] = "timeout"
                result["message"] = "请求超时,请稍后重试"
            elif "connect" in error_lower or "network" in error_lower or "网络" in error_message or "连接" in error_message:
                result["status"] = "network_error"
                result["message"] = "网络连接错误,请检查网络"
            elif "http 5" in error_lower or "http 500" in error_lower or "上游服务错误" in error_message:
                result["status"] = "network_error"
                result["message"] = "上游服务异常,请稍后重试"
            else:
                result["status"] = "unknown"
                result["message"] = "检测失败,请稍后重试"
            result["detail"] = error_message
            # 仅在明确属于账号自身的问题时, 才更新统计/状态, 避免上游异常污染账号状态.
            if result["status"] in ("quota_exhausted", "suspended", "unauthorized"):
                from src.services.account_service import update_account_stats
                is_quota = result["status"] == "quota_exhausted"
                await update_account_stats(account_id, False, is_throttled=is_quota, quota_exhausted=is_quota)
                now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                await db.execute(
                    "UPDATE accounts SET last_refresh_status=?, last_refresh_time=?, updated_at=? WHERE id=?",
                    (result["status"], now, now, account_id)
                )
        else:
            result["status"] = "unknown"
            result["message"] = "未收到响应"
            
    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        result["latency_ms"] = latency_ms
        error_str = str(e).lower()
        error_detail = str(e)
        status_code = getattr(e, "status_code", None)
        if isinstance(status_code, int) and status_code >= 500:
            result["status"] = "network_error"
            result["message"] = "上游服务异常,请稍后重试"
            detail = getattr(e, "detail", None)
            result["detail"] = f"{status_code}: {detail}" if detail is not None else error_detail
            logger.error("[AccountCheck] Upstream error %s for account %s: %s", status_code, account_id, detail or error_detail)
            return result
        
        # 解析错误类型（支持中英文关键词）
        if "quota" in error_str or "throttl" in error_str or "rate" in error_str or "配额" in error_detail:
            result["status"] = "quota_exhausted"
            result["message"] = "账号配额已耗尽,请联系管理员"
        elif "suspend" in error_str or "banned" in error_str or "disabled" in error_str or "封禁" in error_detail:
            result["status"] = "suspended"
            result["message"] = "账号已被封禁,请联系管理员"
        elif "unauthorized" in error_str or "401" in error_str or "403" in error_str or "认证" in error_detail or "授权" in error_detail:
            result["status"] = "unauthorized"
            result["message"] = "认证失败,Token已过期"
        elif "timeout" in error_str or "timed out" in error_str or "超时" in error_detail:
            result["status"] = "timeout"
            result["message"] = "请求超时,请稍后重试"
        elif "connect" in error_str or "network" in error_str or "网络" in error_detail or "连接" in error_detail:
            result["status"] = "network_error"
            result["message"] = "网络连接错误,请检查网络"
        else:
            result["status"] = "unknown"
            result["message"] = "检测失败,请稍后重试"
        
        result["detail"] = error_detail
        
        # 仅在明确属于账号自身的问题时, 才更新统计/状态, 避免上游异常污染账号状态.
        if result["status"] in ("quota_exhausted", "suspended", "unauthorized"):
            from src.services.account_service import update_account_stats
            is_quota = result["status"] == "quota_exhausted"
            await update_account_stats(account_id, False, is_throttled=is_quota, quota_exhausted=is_quota)
            now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            await db.execute(
                "UPDATE accounts SET last_refresh_status=?, last_refresh_time=?, updated_at=? WHERE id=?",
                (result["status"], now, now, account_id)
            )
    
    return result

# ------------------------------------------------------------------------------
# Key Management
# ------------------------------------------------------------------------------

@router.post("/v2/security/keys/generate", dependencies=[Depends(require_admin)])
async def generate_secure_key(request: Request, body: Dict[str, Any]):
    """Generate new secure key"""
    try:
        expires_in_days = body.get("expires_in_days")
        max_uses = body.get("max_uses")
        allowed_ips = body.get("allowed_ips", [])
        allowed_user_agents = body.get("allowed_user_agents", [])
        rate_limit = body.get("rate_limit")
        metadata = body.get("metadata", {})
        allowed_account_ids = body.get("allowed_account_ids")
        default_account_id = body.get("default_account_id")

        if expires_in_days is not None and (not isinstance(expires_in_days, int) or expires_in_days < 1 or expires_in_days > 365):
            raise HTTPException(status_code=400, detail="expires_in_days must be 1-365")

        if max_uses is not None and (not isinstance(max_uses, int) or max_uses < 1):
            raise HTTPException(status_code=400, detail="max_uses must be positive")

        if rate_limit is not None and (not isinstance(rate_limit, int) or rate_limit < 1 or rate_limit > 1000):
            raise HTTPException(status_code=400, detail="rate_limit must be 1-1000")

        if allowed_account_ids is not None:
            if not isinstance(allowed_account_ids, list) or not all(isinstance(acc, str) and acc.strip() for acc in allowed_account_ids):
                raise HTTPException(status_code=400, detail="allowed_account_ids must be an array of account IDs")
        else:
            allowed_account_ids = []

        if default_account_id is not None:
            if not isinstance(default_account_id, str) or not default_account_id.strip():
                raise HTTPException(status_code=400, detail="default_account_id must be a non-empty string")
            default_account_id = default_account_id.strip()

        if default_account_id and default_account_id not in allowed_account_ids:
            allowed_account_ids.append(default_account_id)

        key_id, api_key = advanced_key_manager.generate_secure_key(
            expires_in_days=expires_in_days,
            max_uses=max_uses,
            allowed_ips=allowed_ips,
            allowed_user_agents=allowed_user_agents,
            rate_limit=rate_limit,
            metadata=metadata,
            allowed_account_ids=allowed_account_ids,
            default_account_id=default_account_id
        )

        await advanced_key_manager.save_key_to_db(key_id)

        log_security_event("secure_key_generated", {
            "key_id": key_id,
            "expires_in_days": expires_in_days,
            "max_uses": max_uses,
            "allowed_ips": len(allowed_ips),
            "rate_limit": rate_limit,
            "client_ip": get_remote_address(request)
        })

        return {
            "key_id": key_id,
            "api_key": api_key,
            "message": "Key generated successfully. Save it now.",
            "security_level": SECURITY_LEVEL.value,
            "expires_at": advanced_key_manager.get_key_stats(key_id)["expires_at"] if expires_in_days else None,
            "allowed_account_ids": allowed_account_ids,
            "default_account_id": default_account_id
        }

    except HTTPException:
        raise
    except Exception as e:
        log_security_event("key_generation_error", {"error": str(e)})
        raise HTTPException(status_code=500, detail="Key generation failed")

@router.get("/v2/security/keys", dependencies=[Depends(require_admin)])
async def list_secure_keys():
    """直接从数据库读取密钥列表，确保数据一致性"""
    try:
        db = get_database_backend()
        rows = await db.fetchall("""
            SELECT key_id, created_at, expires_at, last_used, usage_count, max_uses,
                   rate_limit_per_minute, status, security_level, allowed_accounts, default_account_id
            FROM secure_keys
            WHERE status = 'active'
            ORDER BY created_at DESC
        """)

        keys_info = []
        for row in rows:
            row_dict = row_to_dict(row)
            keys_info.append({
                "key_id": row_dict["key_id"],
                "status": row_dict["status"],
                "created_at": row_dict["created_at"],
                "expires_at": row_dict["expires_at"],
                "last_used": row_dict["last_used"],
                "usage_count": row_dict["usage_count"] or 0,
                "max_uses": row_dict["max_uses"],
                "security_level": row_dict["security_level"],
                "rate_limit_per_minute": row_dict["rate_limit_per_minute"] or advanced_key_manager.default_rate_limit,
                "allowed_account_ids": json.loads(row_dict["allowed_accounts"]) if row_dict.get("allowed_accounts") else [],
                "default_account_id": row_dict.get("default_account_id")
            })

        return {
            "total_keys": len(keys_info),
            "security_level": SECURITY_LEVEL.value,
            "default_rate_limit": advanced_key_manager.default_rate_limit,
            "keys": keys_info
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list keys: {str(e)}")

@router.get("/v2/security/keys/{key_id}", dependencies=[Depends(require_admin)])
async def get_key_details(key_id: str):
    try:
        stats = advanced_key_manager.get_key_stats(key_id)
        if not stats:
            raise HTTPException(status_code=404, detail="Key not found")

        key_info = advanced_key_manager.keys.get(key_id)
        if not key_info:
            raise HTTPException(status_code=404, detail="Key not found")

        return {
            **stats,
            "allowed_ips": key_info.allowed_ips,
            "allowed_user_agents": key_info.allowed_user_agents,
            "allowed_account_ids": key_info.allowed_account_ids,
            "default_account_id": key_info.default_account_id,
            "metadata": key_info.metadata
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get key details")

@router.get("/v2/security/keys/{key_id}/decrypt", dependencies=[Depends(require_admin)])
async def get_decrypted_key(key_id: str, request: Request):
    try:
        api_key = advanced_key_manager.get_decrypted_key(key_id)
        if not api_key:
            raise HTTPException(status_code=404, detail="Key not found or cannot decrypt")

        log_security_event("key_decrypted", {
            "key_id": key_id,
            "client_ip": get_remote_address(request)
        })

        return {"key_id": key_id, "api_key": api_key}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to decrypt key")

@router.post("/v2/security/keys/{key_id}/revoke", dependencies=[Depends(require_admin)])
async def revoke_key(key_id: str, request: Request, body: Dict[str, str] = None):
    """销毁密钥 - 直接从数据库删除，确保完全移除"""
    try:
        reason = body.get("reason", "Admin revoked") if body else "Admin revoked"
        db = get_database_backend()

        # 先检查数据库中是否存在该密钥
        existing = await db.fetchone("SELECT key_id FROM secure_keys WHERE key_id = ?", (key_id,))
        if not existing:
            raise HTTPException(status_code=404, detail="Key not found in database")

        # 直接从数据库删除密钥
        await db.execute("DELETE FROM secure_keys WHERE key_id = ?", (key_id,))

        # 验证删除成功
        verify = await db.fetchone("SELECT key_id FROM secure_keys WHERE key_id = ?", (key_id,))
        if verify:
            raise HTTPException(status_code=500, detail="Failed to delete key from database")

        # 数据库删除成功后，同步清理内存
        if key_id in advanced_key_manager.keys:
            key_info = advanced_key_manager.keys[key_id]
            # 清理 key_lookup 索引
            if key_info.encrypted_key:
                decrypted = advanced_key_manager._decrypt_key(key_info.encrypted_key)
                if decrypted:
                    lookup_hash = advanced_key_manager._calculate_lookup_hash(decrypted)
                    if lookup_hash in advanced_key_manager.key_lookup:
                        del advanced_key_manager.key_lookup[lookup_hash]
            del advanced_key_manager.keys[key_id]

        log_security_event("key_revoked_and_deleted", {
            "key_id": key_id,
            "reason": reason,
            "client_ip": get_remote_address(request)
        })

        return {"message": f"Key {key_id} permanently deleted from database", "reason": reason}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Revocation failed: {str(e)}")

@router.post("/v2/security/keys/{key_id}/rotate", dependencies=[Depends(require_admin)])
async def rotate_key(key_id: str, request: Request):
    try:
        result = advanced_key_manager.rotate_key(key_id)
        if not result:
            raise HTTPException(status_code=404, detail="Key not found or cannot rotate")

        new_key_id, new_api_key = result

        await advanced_key_manager.update_key_status_in_db(key_id, KeyStatus.INACTIVE)
        await advanced_key_manager.save_key_to_db(new_key_id)

        log_security_event("key_rotated", {
            "old_key_id": key_id,
            "new_key_id": new_key_id,
            "client_ip": get_remote_address(request)
        })

        return {
            "message": "Key rotated successfully",
            "old_key_id": key_id,
            "new_key_id": new_key_id,
            "new_api_key": new_api_key,
            "warning": "Old key revoked immediately."
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Rotation failed")

@router.post("/v2/security/cleanup", dependencies=[Depends(require_admin)])
async def cleanup_expired_keys(request: Request):
    try:
        advanced_key_manager.cleanup_expired_keys()
        log_security_event("expired_keys_cleanup", {
            "client_ip": get_remote_address(request)
        })
        return {"message": "Cleanup completed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Cleanup failed")

@router.get("/v2/security/report", dependencies=[Depends(require_admin)])
async def get_security_report():
    try:
        report = advanced_key_manager.export_security_report()
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail="Report generation failed")
