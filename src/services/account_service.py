import os
import time
import calendar
import uuid
import json
import logging
import traceback
import httpx
import asyncio
import weakref
from collections import OrderedDict
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

# 确保 .env 已加载
from src.core.env import env_loaded  # noqa: F401
from fastapi import HTTPException
from src.core.database import get_database_backend, row_to_dict
from src.core.http_client import get_client
from src.api.oidc_flow import poll_token_device_code, register_client_min, device_authorize
from src.integrations.amazonq_client import send_chat_request
from src.services.quota_service import QuotaService
from src.services.kiro_service import refresh_kiro_builder_id_token, normalize_region
# Critical Fix: Blocker #4 - 使用分布式文件锁替代内存锁
from src.core.distributed_lock import get_lock_manager
# Critical Fix: Blocker #6 - 使用智能错误检测器
from src.core.error_detector import AccountErrorDetector, AccountErrorType

logger = logging.getLogger(__name__)

from src.core.config import OIDC_TOKEN_URL as TOKEN_URL

# Token 刷新锁 - 已替换为分布式文件锁（见 distributed_lock.py）
# 旧的内存锁实现已移除，避免内存泄漏和竞态条件
_REFRESH_LOCK_EXPIRE_SECONDS = 600  # 锁过期时间 10 分钟（已移至分布式锁配置）
MAX_ERROR_COUNT = int(os.getenv("MAX_ERROR_COUNT", "100"))
AUTO_DISABLE_INCOMPLETE_ACCOUNTS = os.getenv("AUTO_DISABLE_INCOMPLETE_ACCOUNTS", "false").strip().lower() in (
    "true",
    "1",
    "yes",
)

# In-memory auth sessions (ephemeral) - 使用 OrderedDict 实现 LRU + TTL
# Major Fix: 降低默认容量并添加TTL机制防止内存泄漏

@dataclass
class TimedAuthSession:
    """带时间戳的会话数据"""
    data: Dict[str, Any]
    created_at: float  # Unix timestamp
    last_accessed: float  # Unix timestamp

    def is_expired(self, ttl_seconds: int = 600) -> bool:
        """检查会话是否过期（默认10分钟）"""
        return time.time() - self.created_at > ttl_seconds

    def touch(self) -> None:
        """更新最后访问时间"""
        self.last_accessed = time.time()


AUTH_SESSIONS: OrderedDict[str, TimedAuthSession] = OrderedDict()
DEFAULT_MAX_AUTH_SESSIONS = 1000  # 降低从10000到1000，减少内存占用
SESSION_TTL_SECONDS = 600  # 10分钟TTL
_auth_sessions_lock = asyncio.Lock()


def _get_max_auth_sessions() -> int:
    raw = os.getenv("MAX_AUTH_SESSIONS", str(DEFAULT_MAX_AUTH_SESSIONS)).strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_AUTH_SESSIONS
    return value if value > 0 else DEFAULT_MAX_AUTH_SESSIONS


def _cleanup_expired_sessions_sync() -> int:
    """清理过期会话（同步版本）"""
    expired_ids = [
        sid for sid, sess in list(AUTH_SESSIONS.items())
        if sess.is_expired(SESSION_TTL_SECONDS)
    ]
    for sid in expired_ids:
        AUTH_SESSIONS.pop(sid, None)
    return len(expired_ids)


def _auth_sessions_put_sync(auth_id: str, session: Dict[str, Any]) -> None:
    """
    同步版本，供内部使用

    Major Fix: 每次添加时清理过期会话，防止内存泄漏
    """
    # 清理过期会话（每次put时触发）
    expired_count = _cleanup_expired_sessions_sync()
    if expired_count > 0:
        logger.debug(f"清理了 {expired_count} 个过期会话")

    # 添加/更新会话
    if auth_id in AUTH_SESSIONS:
        # 更新现有会话
        AUTH_SESSIONS[auth_id].data = session
        AUTH_SESSIONS[auth_id].touch()
        AUTH_SESSIONS.move_to_end(auth_id)
    else:
        # 创建新会话
        AUTH_SESSIONS[auth_id] = TimedAuthSession(
            data=session,
            created_at=time.time(),
            last_accessed=time.time()
        )

    # LRU淘汰（在TTL清理后仍超限时触发）
    max_sessions = _get_max_auth_sessions()
    while len(AUTH_SESSIONS) > max_sessions:
        oldest_id, _ = AUTH_SESSIONS.popitem(last=False)
        logger.debug(f"LRU淘汰会话: {oldest_id[:8]}***")

    # 定期日志（每次put时检查）
    if len(AUTH_SESSIONS) > max_sessions * 0.8:  # 超过80%容量时警告
        logger.warning(
            f"⚠️ AUTH_SESSIONS接近容量上限: {len(AUTH_SESSIONS)}/{max_sessions} "
            f"(建议增加MAX_AUTH_SESSIONS或降低SESSION_TTL_SECONDS)"
        )


async def _auth_sessions_put(auth_id: str, session: Dict[str, Any]) -> None:
    """异步版本，带锁保护"""
    async with _auth_sessions_lock:
        _auth_sessions_put_sync(auth_id, session)


def _value_present(value: Optional[str]) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _has_full_credentials(account: Dict[str, Any]) -> bool:
    return all(_value_present(account.get(field)) for field in ("clientId", "clientSecret", "refreshToken"))


def _parse_other_field(other: Any) -> Dict[str, Any]:
    if not other:
        return {}
    if isinstance(other, dict):
        return other
    if isinstance(other, str):
        try:
            data = json.loads(other)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _parse_utc_iso_ts(value: Any) -> Optional[int]:
    if not value or not isinstance(value, str):
        return None
    try:
        return calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def _calc_expires_at(expires_in: Any) -> Optional[str]:
    if expires_in is None:
        return None
    try:
        seconds = int(expires_in)
    except Exception:
        return None
    if seconds <= 0:
        return None
    return time.strftime(
        "%Y-%m-%dT%H:%M:%S",
        time.gmtime(time.time() + seconds),
    )


def get_account_expires_at(account: Dict[str, Any]) -> Optional[str]:
    expires_at = account.get("expires_at")
    if isinstance(expires_at, str) and expires_at.strip():
        return expires_at.strip()
    other = _parse_other_field(account.get("other"))
    other_expires = other.get("expiresAt")
    if isinstance(other_expires, str) and other_expires.strip():
        return other_expires.strip()
    return None


def is_access_token_expired(account: Dict[str, Any], *, leeway_sec: int = 0) -> bool:
    access = account.get("accessToken")
    if not access:
        return True
    expires_at = get_account_expires_at(account)
    exp_ts = _parse_utc_iso_ts(expires_at) if expires_at else None
    if exp_ts is None:
        # 兼容迁移/旧数据：如果该账号记录已经包含 expires_at 列但值为空，触发一次刷新以写回 expires_at。
        # 若账号对象本身不带 expires_at（例如某些测试/调用方手工构造），则不强制刷新。
        return "expires_at" in account
    return time.time() >= (exp_ts - max(0, int(leeway_sec)))


def _is_kiro_account(account: Dict[str, Any]) -> bool:
    other = _parse_other_field(account.get("other"))
    provider = str(other.get("provider") or "").strip().lower()
    return provider == "kiro"


def _has_refresh_credentials(account: Dict[str, Any]) -> bool:
    if _is_kiro_account(account):
        return _value_present(account.get("refreshToken"))
    return _has_full_credentials(account)


async def save_auth_session(auth_id: str, session: Dict[str, Any]) -> None:
    """Persist auth session for multi-worker access with LRU eviction."""
    # 内存中保存，实现 LRU 淘汰
    await _auth_sessions_put(auth_id, session)
    
    # 持久化到数据库
    db = get_database_backend()
    payload = json.dumps(session, ensure_ascii=False)
    now = int(time.time())
    try:
        await db.execute("DELETE FROM auth_sessions WHERE auth_id=?", (auth_id,))
        await db.execute(
            "INSERT INTO auth_sessions (auth_id, payload, created_at) VALUES (?, ?, ?)",
            (auth_id, payload, now),
        )
    except (OSError, IOError) as e:
        logger.error(f"数据库 I/O 错误,无法保存认证会话 {auth_id}: {e}")
    except Exception as e:
        logger.error(f"保存认证会话失败 {auth_id}: {e}", exc_info=True)


async def load_auth_session(auth_id: str) -> Optional[Dict[str, Any]]:
    """
    Load auth session from memory or database.

    Major Fix: 检查会话过期并更新访问时间
    """
    if auth_id in AUTH_SESSIONS:
        timed_session = AUTH_SESSIONS[auth_id]

        # 检查会话是否过期
        if timed_session.is_expired(SESSION_TTL_SECONDS):
            logger.debug(f"会话已过期: {auth_id[:8]}***")
            AUTH_SESSIONS.pop(auth_id, None)
            return None

        # 更新访问时间
        timed_session.touch()
        return timed_session.data

    # 从数据库加载
    db = get_database_backend()
    try:
        row = await db.fetchone("SELECT payload FROM auth_sessions WHERE auth_id=?", (auth_id,))
        if not row:
            return None
        data = json.loads(row.get("payload", "{}"))
        if data:
            await _auth_sessions_put(auth_id, data)
        return data
    except json.JSONDecodeError as e:
        logger.error(f"认证会话数据损坏 {auth_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"加载认证会话失败 {auth_id}: {e}", exc_info=True)
        return None


async def delete_auth_session(auth_id: str) -> None:
    AUTH_SESSIONS.pop(auth_id, None)
    db = get_database_backend()
    try:
        await db.execute("DELETE FROM auth_sessions WHERE auth_id=?", (auth_id,))
    except Exception as e:
        logger.error(f"删除认证会话失败 {auth_id}: {e}", exc_info=True)

def _oidc_headers() -> Dict[str, str]:
    return {
        "content-type": "application/json",
        "user-agent": "aws-sdk-rust/1.3.9 os/macos lang/rust/1.87.0 exec-env/CLI md/appVersion-1.19.7",
        "x-amz-user-agent": "aws-sdk-rust/1.3.9 ua/2.1 api/ssooidc/1.88.0 os/macos lang/rust/1.87.0 exec-env/CLI m/E md/appVersion-1.19.7 app/AmazonQ-For-CLI",
        "amz-sdk-request": "attempt=1; max=3",
        "amz-sdk-invocation-id": str(uuid.uuid4()),
    }


async def handle_account_error(
    account_id: str,
    exception: Exception,
    status_code: Optional[int] = None,
    error_code: Optional[str] = None
) -> None:
    """
    处理账户错误，智能分类并采取相应行动
    Critical Fix: Blocker #6 - 使用智能错误检测器自动禁用暂停的账户

    Args:
        account_id: 账户ID
        exception: 异常对象
        status_code: HTTP状态码（可选）
        error_code: AWS/API错误代码（可选）
    """
    # 使用智能错误检测器分析错误
    error_type, reason = AccountErrorDetector.detect_error_type(
        exception,
        status_code=status_code,
        error_code=error_code
    )

    logger.warning(
        f"🔴 [ACCOUNT ERROR] Account {account_id[:8]} - "
        f"Type: {error_type.value}, Reason: {reason[:100]}"
    )

    # 根据错误类型采取行动
    if AccountErrorDetector.should_disable_account(error_type):
        # 永久暂停或配额耗尽 - 禁用账户
        await disable_account(account_id, f"{error_type.value}: {reason[:200]}")
        logger.error(
            f"🚫 [ACCOUNT DISABLED] Account {account_id[:8]} has been disabled "
            f"due to {error_type.value}: {reason[:100]}"
        )

    elif AccountErrorDetector.should_mark_rate_limited(error_type):
        # 临时限速 - 标记但不禁用
        await update_account_stats(account_id, success=False, is_throttled=True)
        retry_delay = AccountErrorDetector.get_retry_delay(error_type)
        logger.warning(
            f"⏱️  [RATE LIMITED] Account {account_id[:8]} is rate limited. "
            f"Will retry after {retry_delay}s"
        )

    elif error_type == AccountErrorType.AUTH_ERROR:
        # 凭证错误 - 增加错误计数，可能需要刷新token
        await update_account_stats(account_id, success=False)
        logger.warning(
            f"🔑 [AUTH ERROR] Account {account_id[:8]} has authentication error. "
            f"May need token refresh."
        )

    elif error_type == AccountErrorType.NETWORK_ERROR:
        # 网络错误 - 不增加错误计数（临时问题）
        logger.info(
            f"🌐 [NETWORK ERROR] Account {account_id[:8]} encountered network error. "
            f"Will retry automatically."
        )

    else:
        # 未知错误 - 增加错误计数
        await update_account_stats(account_id, success=False)
        logger.warning(
            f"❓ [UNKNOWN ERROR] Account {account_id[:8]} encountered unknown error: "
            f"{reason[:100]}"
        )


async def disable_account(account_id: str, reason: str = "suspended") -> None:
    """禁用账号"""
    db = get_database_backend()
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    await db.execute(
        "UPDATE accounts SET enabled=0, last_refresh_status=?, updated_at=? WHERE id=?",
        (reason, now, account_id)
    )
    logger.warning(f"账号 {account_id[:8]} 已被禁用 | 原因={reason}")
async def update_account_stats(account_id: str, success: bool, is_throttled: bool = False, quota_exhausted: bool = False) -> None:
    """Update account statistics atomically to prevent race conditions."""
    db = get_database_backend()
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

    # 记录配额使用情况
    await QuotaService.record_request(account_id, is_throttled)

    if success:
        await db.execute(
            "UPDATE accounts SET success_count=success_count+1, error_count=0, quota_exhausted=0, updated_at=? WHERE id=?",
            (now, account_id)
        )
    else:
        if quota_exhausted:
            # 配额耗尽时标记并禁用
            await db.execute(
                "UPDATE accounts SET quota_exhausted=1, enabled=0, updated_at=? WHERE id=?",
                (now, account_id)
            )
        else:
            await db.execute(
                """
                UPDATE accounts
                SET error_count=error_count+1,
                    enabled=CASE WHEN error_count+1 >= ? THEN 0 ELSE enabled END,
                    updated_at=?
                WHERE id=?
                """,
                (MAX_ERROR_COUNT, now, account_id)
            )

    # 更新配额状态
    if is_throttled:
        await QuotaService.update_quota_status(account_id)

async def refresh_access_token_in_db(account_id: str) -> Dict[str, Any]:
    """
    刷新账户的访问令牌
    Critical Fix: Blocker #4 - 使用分布式文件锁防止竞态条件和内存泄漏
    """
    # 使用分布式文件锁，支持跨进程/跨实例的锁定
    lock_manager = get_lock_manager()
    async with lock_manager.acquire(f"token_refresh_{account_id}"):
        db = get_database_backend()
        row = await db.fetchone("SELECT * FROM accounts WHERE id=?", (account_id,))
        if not row:
            raise HTTPException(status_code=404, detail="Account not found")
        acc = row_to_dict(row)

        # 如果已知 expires_at 且 token 未过期，直接返回（对齐上游：仅在过期时刷新）
        try:
            expires_at = get_account_expires_at(acc)
            if expires_at and (not is_access_token_expired(acc)):
                return acc
        except Exception as exc:
            logger.debug("账号 expires_at 检查失败，将继续刷新: %s", exc)
        
        # 双重检查: 锁内再次验证是否需要刷新
        # Major Fix: 改为1分钟窗口（更严格），并重新读取最新数据
        if acc.get("last_refresh_time"):
            try:
                last_time = _parse_utc_iso_ts(acc["last_refresh_time"])
                if time.time() - last_time < 60:  # 改为1分钟（更严格）
                    # 重要：重新从数据库读取最新数据，避免返回过期token
                    logger.debug(f"账号 {account_id[:8]}*** 最近已刷新，重新读取最新数据")
                    row2 = await db.fetchone("SELECT * FROM accounts WHERE id=?", (account_id,))
                    if row2:
                        fresh_acc = row_to_dict(row2)
                        logger.debug(f"返回最新刷新数据: {account_id[:8]}***")
                        return fresh_acc
                    else:
                        logger.warning(f"重新读取账号失败: {account_id[:8]}***，使用锁内数据")
                        return acc
            except Exception as exc:
                logger.debug("账号 last_refresh_time 解析失败: %s", exc)
        
        other = _parse_other_field(acc.get("other"))
        if _is_kiro_account(acc):
            if not acc.get("refreshToken"):
                raise HTTPException(status_code=400, detail="Account missing refreshToken for Kiro refresh")

            # 仅支持 Builder ID 刷新方式
            # 参考: AIClient-2-API/src/auth/kiro-oauth.js
            if not acc.get("clientId") or not acc.get("clientSecret"):
                raise HTTPException(
                    status_code=400,
                    detail="Kiro account missing clientId/clientSecret for Builder ID refresh"
                )

            try:
                client = get_client()

                # Builder ID: 使用 AWS OIDC 端点
                idc_region = other.get("idcRegion") or other.get("region")
                data = await refresh_kiro_builder_id_token(
                    client_id=acc["clientId"],
                    client_secret=acc["clientSecret"],
                    refresh_token=acc["refreshToken"],
                    region=idc_region,
                    client=client,
                )
                updated_other = dict(other)
                updated_other["provider"] = "kiro"
                updated_other["authMethod"] = "builder-id"
                if idc_region:
                    updated_other["idcRegion"] = idc_region

                new_access = data.get("accessToken")
                new_refresh = data.get("refreshToken", acc.get("refreshToken"))
                now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                status = "success"

                if data.get("expiresIn"):
                    expires_at = _calc_expires_at(data.get("expiresIn"))
                    if expires_at:
                        updated_other["expiresAt"] = expires_at
            except httpx.HTTPStatusError as e:
                now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                status = "failed"
                error_detail = f"HTTP {e.response.status_code}"
                await db.execute(
                    """
                    UPDATE accounts
                    SET last_refresh_time=?, last_refresh_status=?, updated_at=?
                    WHERE id=?
                    """,
                    (now, status, now, account_id),
                )
                await update_account_stats(account_id, False)
                logger.error(f"Kiro token refresh failed for account {account_id[:8]}***: HTTP {e.response.status_code}")
                raise HTTPException(status_code=502, detail=f"Token refresh failed: {error_detail}")
            except httpx.HTTPError as e:
                now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                status = "failed"
                error_detail = f"{type(e).__name__}"
                await db.execute(
                    """
                    UPDATE accounts
                    SET last_refresh_time=?, last_refresh_status=?, updated_at=?
                    WHERE id=?
                    """,
                    (now, status, now, account_id),
                )
                await update_account_stats(account_id, False)
                logger.error(f"Kiro token refresh failed for account {account_id[:8]}***: {error_detail}")
                raise HTTPException(status_code=502, detail=f"Token refresh failed: {error_detail}")
            except Exception as e:
                now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                status = "failed"
                error_detail = f"{type(e).__name__}"
                await db.execute(
                    """
                    UPDATE accounts
                    SET last_refresh_time=?, last_refresh_status=?, updated_at=?
                    WHERE id=?
                    """,
                    (now, status, now, account_id),
                )
                await update_account_stats(account_id, False)
                logger.error(f"Unexpected error during Kiro token refresh for account {account_id[:8]}***: {error_detail}")
                raise HTTPException(status_code=502, detail=f"Token refresh failed: {error_detail}")

            await db.execute(
                """
                UPDATE accounts
                SET accessToken=?, refreshToken=?, expires_at=?, other=?, last_refresh_time=?, last_refresh_status=?, updated_at=?
                WHERE id=?
                """,
                (
                    new_access,
                    new_refresh,
                    updated_other.get("expiresAt"),
                    json.dumps(updated_other, ensure_ascii=False) if updated_other else None,
                    now,
                    status,
                    now,
                    account_id,
                ),
            )

            row2 = await db.fetchone("SELECT * FROM accounts WHERE id=?", (account_id,))
            return row_to_dict(row2)

        if not acc.get("clientId") or not acc.get("clientSecret") or not acc.get("refreshToken"):
            raise HTTPException(status_code=400, detail="Account missing clientId/clientSecret/refreshToken for refresh")

        payload = {
            "grantType": "refresh_token",
            "clientId": acc["clientId"],
            "clientSecret": acc["clientSecret"],
            "refreshToken": acc["refreshToken"],
        }

        try:
            client = get_client()
            if not client:
                async with httpx.AsyncClient(timeout=60.0) as temp_client:
                    r = await temp_client.post(TOKEN_URL, headers=_oidc_headers(), json=payload)
                    r.raise_for_status()
                    data = r.json()
            else:
                r = await client.post(TOKEN_URL, headers=_oidc_headers(), json=payload)
                r.raise_for_status()
                data = r.json()

            new_access = data.get("accessToken")
            new_refresh = data.get("refreshToken", acc.get("refreshToken"))
            expires_at = _calc_expires_at(data.get("expiresIn"))
            now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            status = "success"
        except httpx.HTTPStatusError as e:
            # HTTP 状态错误，记录但不泄露敏感信息
            now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            status = "failed"
            # 仅记录状态码，避免泄露 Token 等敏感信息
            error_detail = f"HTTP {e.response.status_code}"
            
            await db.execute(
                """
                UPDATE accounts
                SET last_refresh_time=?, last_refresh_status=?, updated_at=?
                WHERE id=?
                """,
                (now, status, now, account_id),
            )
            await update_account_stats(account_id, False)
            # 日志中也不包含响应体，避免泄露敏感信息
            logger.error(f"Token refresh failed for account {account_id[:8]}***: HTTP {e.response.status_code}")
            raise HTTPException(status_code=502, detail=f"Token refresh failed: {error_detail}")
        except httpx.HTTPError as e:
            # 其他 HTTP 错误（连接、超时等）
            now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            status = "failed"
            error_detail = f"{type(e).__name__}"
            await db.execute(
                """
                UPDATE accounts
                SET last_refresh_time=?, last_refresh_status=?, updated_at=?
                WHERE id=?
                """,
                (now, status, now, account_id),
            )
            await update_account_stats(account_id, False)
            logger.error(f"Token refresh failed for account {account_id[:8]}***: {error_detail}")
            raise HTTPException(status_code=502, detail=f"Token refresh failed: {error_detail}")
        except Exception as e:
            # 其他未知错误
            now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            status = "failed"
            error_detail = f"{type(e).__name__}"
            await db.execute(
                """
                UPDATE accounts
                SET last_refresh_time=?, last_refresh_status=?, updated_at=?
                WHERE id=?
                """,
                (now, status, now, account_id),
            )
            await update_account_stats(account_id, False)
            logger.error(f"Unexpected error during token refresh for account {account_id[:8]}***: {error_detail}")
            raise HTTPException(status_code=502, detail=f"Token refresh failed: {error_detail}")

        await db.execute(
            """
            UPDATE accounts
            SET accessToken=?, refreshToken=?, expires_at=?, last_refresh_time=?, last_refresh_status=?, updated_at=?
            WHERE id=?
            """,
            (new_access, new_refresh, expires_at, now, status, now, account_id),
        )

        row2 = await db.fetchone("SELECT * FROM accounts WHERE id=?", (account_id,))
        return row_to_dict(row2)

async def get_account(account_id: str) -> Dict[str, Any]:
    db = get_database_backend()
    row = await db.fetchone("SELECT * FROM accounts WHERE id=?", (account_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    return row_to_dict(row)

async def list_enabled_accounts() -> List[Dict[str, Any]]:
    db = get_database_backend()
    rows = await db.fetchall("SELECT * FROM accounts WHERE enabled=1 ORDER BY created_at DESC")
    valid_accounts: List[Dict[str, Any]] = []
    invalid_ids: List[str] = []
    for row in rows:
        acc = row_to_dict(row)
        if _has_refresh_credentials(acc):
            valid_accounts.append(acc)
        else:
            acc_id = acc.get("id")
            if acc_id:
                invalid_ids.append(acc_id)
            logger.warning(
                "[Accounts] Skipping enabled account %s because credentials are incomplete",
                acc_id or "<unknown>"
            )

    if invalid_ids and AUTO_DISABLE_INCOMPLETE_ACCOUNTS:
        # Critical Fix: 使用SecurityValidator进行严格验证，避免SQL注入
        from src.core.security_utils import SecurityValidator

        # 限制批量操作大小防止 DoS
        MAX_BATCH_SIZE = 100
        if len(invalid_ids) > MAX_BATCH_SIZE:
            logger.warning(f"批量禁用账号数量超限: {len(invalid_ids)},仅处理前 {MAX_BATCH_SIZE} 个")
            invalid_ids = invalid_ids[:MAX_BATCH_SIZE]

        # ✅ 使用SecurityValidator严格验证每个ID
        validated_ids = []
        for account_id in invalid_ids:
            try:
                validated_id = SecurityValidator.validate_account_id(account_id)
                validated_ids.append(validated_id)
            except ValueError as e:
                logger.warning(f"❌ Invalid account ID format skipped: {account_id}, error: {e}")

        if not validated_ids:
            logger.debug("没有有效的账号ID需要禁用")
            return valid_accounts

        # ✅ 使用单独UPDATE而非IN子句（更安全，避免字符串拼接）
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        disabled_count = 0
        failed_count = 0

        # 分批处理，每批最多20个（避免数据库压力）
        BATCH_SIZE = 20
        for i in range(0, len(validated_ids), BATCH_SIZE):
            batch = validated_ids[i:i+BATCH_SIZE]

            for account_id in batch:
                try:
                    # 使用参数化查询，完全避免SQL注入
                    await db.execute(
                        "UPDATE accounts SET enabled=0, last_refresh_status=?, updated_at=? WHERE id=?",
                        ("missing_credentials", now, account_id)
                    )
                    disabled_count += 1
                except Exception as e:
                    logger.error(f"❌ 禁用账号失败 {account_id[:8]}***: {e}")
                    failed_count += 1

        if disabled_count > 0:
            logger.info(f"✅ 批量禁用不完整账号: 成功 {disabled_count}, 失败 {failed_count}")
        if failed_count > 0:
            logger.warning(f"⚠️ 部分账号禁用失败: {failed_count} 个")

    return valid_accounts

async def list_disabled_accounts() -> List[Dict[str, Any]]:
    db = get_database_backend()
    rows = await db.fetchall("SELECT * FROM accounts WHERE enabled=0 ORDER BY created_at DESC")
    return [row_to_dict(r) for r in rows]

async def create_account_from_tokens(
    client_id: str,
    client_secret: str,
    access_token: str,
    refresh_token: Optional[str],
    label: Optional[str],
    enabled: bool,
    expires_in: Optional[int] = None,
) -> Dict[str, Any]:
    if not _has_full_credentials(
        {"clientId": client_id, "clientSecret": client_secret, "refreshToken": refresh_token}
    ):
        logger.warning(
            "[Accounts] Creating account without full credentials (label=%s); it will remain unusable until completed.",
            label or "<unnamed>",
        )

    db = get_database_backend()
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    acc_id = str(uuid.uuid4())
    expires_at = _calc_expires_at(expires_in)
    await db.execute(
        """
        INSERT INTO accounts (id, label, clientId, clientSecret, refreshToken, accessToken, expires_at, other, last_refresh_time, last_refresh_status, created_at, updated_at, enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            acc_id,
            label,
            client_id,
            client_secret,
            refresh_token,
            access_token,
            expires_at,
            None,
            now,
            "success",
            now,
            now,
            1 if enabled else 0,
        ),
    )
    row = await db.fetchone("SELECT * FROM accounts WHERE id=?", (acc_id,))
    return row_to_dict(row)


async def create_kiro_builder_id_account_from_tokens(
    *,
    client_id: str,
    client_secret: str,
    access_token: str,
    refresh_token: str,
    label: Optional[str],
    enabled: bool,
    other: Optional[Dict[str, Any]] = None,
    expires_in: Optional[int] = None,
) -> Dict[str, Any]:
    db = get_database_backend()
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    acc_id = str(uuid.uuid4())
    expires_at = _calc_expires_at(expires_in)
    other_str = json.dumps(other or {}, ensure_ascii=False) if other else None
    await db.execute(
        "INSERT INTO accounts (id, label, clientId, clientSecret, refreshToken, accessToken, expires_at, other, last_refresh_time, last_refresh_status, created_at, updated_at, enabled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (acc_id, label, client_id, client_secret, refresh_token, access_token, expires_at, other_str, now, "success", now, now, 1 if enabled else 0),
    )
    row = await db.fetchone("SELECT * FROM accounts WHERE id=?", (acc_id,))
    return row_to_dict(row)

async def delete_account(account_id: str) -> Dict[str, Any]:
    db = get_database_backend()
    rowcount = await db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
    if rowcount == 0:
        raise HTTPException(status_code=404, detail="Account not found")
    # 注意：分布式文件锁会自动过期，无需手动清理
    return {"deleted": account_id}

async def update_account(account_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    db = get_database_backend()
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    fields = []
    values: List[Any] = []

    # Map supported fields
    # label, clientId, clientSecret, refreshToken, accessToken, other (dict->json), enabled (bool->int)
    if "label" in updates and updates["label"] is not None:
        fields.append("label=?"); values.append(updates["label"])
    if "clientId" in updates and updates["clientId"] is not None:
        fields.append("clientId=?"); values.append(updates["clientId"])
    if "clientSecret" in updates and updates["clientSecret"] is not None:
        fields.append("clientSecret=?"); values.append(updates["clientSecret"])
    if "refreshToken" in updates and updates["refreshToken"] is not None:
        fields.append("refreshToken=?"); values.append(updates["refreshToken"])
    if "accessToken" in updates and updates["accessToken"] is not None:
        fields.append("accessToken=?"); values.append(updates["accessToken"])
    if "other" in updates and updates["other"] is not None:
        fields.append("other=?"); values.append(json.dumps(updates["other"], ensure_ascii=False))
    if "enabled" in updates and updates["enabled"] is not None:
        fields.append("enabled=?"); values.append(1 if updates["enabled"] else 0)

    if not fields:
        return await get_account(account_id)

    fields.append("updated_at=?"); values.append(now)
    values.append(account_id)

    rowcount = await db.execute(f"UPDATE accounts SET {', '.join(fields)} WHERE id=?", tuple(values))
    if rowcount == 0:
        raise HTTPException(status_code=404, detail="Account not found")
    
    return await get_account(account_id)

async def verify_account(account: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Verify account usability."""
    try:
        # Refresh token first
        try:
            account = await refresh_access_token_in_db(account['id'])
        except Exception as refresh_err:
            logger.error(f"❌ [Account] Token refresh failed for {account['id'][:8] if len(account['id']) > 8 else account['id']}***: {refresh_err}")
            return False, f"Token refresh failed: {str(refresh_err)}"

        test_request = {
            "conversationState": {
                "currentMessage": {"userInputMessage": {"content": "hello"}},
                "chatTriggerType": "MANUAL"
            }
        }
        _, _, tracker, event_gen = await send_chat_request(
            access_token=account['accessToken'],
            messages=[],
            stream=True,
            raw_payload=test_request,
            client=get_client()
        )
        if event_gen:
            async for _ in event_gen:
                break
        return True, None
    except Exception as e:
        if "AccessDenied" in str(e) or "403" in str(e):
            return False, "AccessDenied"
        return False, str(e)

async def verify_and_enable_accounts(account_ids: List[str]):
    """Background task to verify and enable accounts."""
    db = get_database_backend()
    for acc_id in account_ids:
        try:
            account = await get_account(acc_id)
            verify_success, fail_reason = await verify_account(account)
            now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

            if verify_success:
                await db.execute("UPDATE accounts SET enabled=1, updated_at=? WHERE id=?", (now, acc_id))
            elif fail_reason:
                other_dict = account.get("other", {}) or {}
                other_dict['failedReason'] = fail_reason
                await db.execute("UPDATE accounts SET other=?, updated_at=? WHERE id=?", (json.dumps(other_dict, ensure_ascii=False), now, acc_id))
        except Exception as e:
            logger.error(f"❌ [Account] Error verifying account {acc_id}: {e}", exc_info=True)

async def refresh_stale_tokens_loop():
    """Background task to refresh tokens."""
    while True:
        try:
            await asyncio.sleep(300)  # 5 minutes
            db = get_database_backend()
            if not db:
                logger.error("❌ [Database] Database not initialized, skipping token refresh cycle.")
                continue
            now = time.time()
            rows = await db.fetchall("SELECT id, accessToken, expires_at, other, last_refresh_time FROM accounts WHERE enabled=1")
            for row in rows:
                acc_id = row.get('id')
                if not acc_id:
                    continue
                acc = row_to_dict(row)
                last_refresh = acc.get('last_refresh_time')
                should_refresh = False

                expires_at = get_account_expires_at(acc)
                exp_ts = _parse_utc_iso_ts(expires_at) if expires_at else None

                if not acc.get("accessToken"):
                    should_refresh = True
                elif exp_ts is not None:
                    should_refresh = now >= exp_ts
                elif not last_refresh or last_refresh == "never":
                    should_refresh = True
                else:
                    try:
                        last_time = _parse_utc_iso_ts(last_refresh)
                        if not last_time or now - last_time > 1500:  # 25 minutes
                            should_refresh = True
                    except (TypeError, ValueError):
                        should_refresh = True

                if should_refresh:
                    try:
                        await refresh_access_token_in_db(acc_id)
                    except HTTPException as exc:
                        detail_text = str(exc.detail)
                        if exc.status_code == 400 and "Account missing clientId/clientSecret/refreshToken" in detail_text:
                            logger.warning(
                                "[Accounts] Disabling account %s because credentials are incomplete; skipping refresh.",
                                acc_id
                            )
                            now_str = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                            try:
                                await db.execute(
                                    """
                                    UPDATE accounts
                                    SET enabled=0, last_refresh_status=?, updated_at=?
                                    WHERE id=?
                                    """,
                                    ("missing_credentials", now_str, acc_id)
                                )
                            except Exception as e:
                                logger.error(f"更新账号状态失败 {acc_id}: {e}", exc_info=True)
                        else:
                            logger.error(f"Token 刷新失败 {acc_id}: {detail_text}", exc_info=True)
                    except Exception as e:
                        logger.error(f"Token 刷新异常 {acc_id}: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Token 刷新循环异常: {e}", exc_info=True)

async def cleanup_auth_sessions_loop():
    """Clean up expired auth sessions every 10 minutes."""
    while True:
        try:
            await asyncio.sleep(600)  # 10 minutes
            now = int(time.time())
            to_delete = []
            for auth_id, sess in list(AUTH_SESSIONS.items()):
                if sess.is_expired(SESSION_TTL_SECONDS):
                    to_delete.append(auth_id)
            if to_delete:
                for auth_id in to_delete:
                    await delete_auth_session(auth_id)
                logger.info(f"🔐 [Auth] Cleaned up {len(to_delete)} expired auth sessions (memory)")

            try:
                db = get_database_backend()
                cutoff = now - 600
                rows = await db.fetchall("SELECT auth_id FROM auth_sessions WHERE created_at<?", (cutoff,))
                expired_ids = [row.get("auth_id") for row in rows if row.get("auth_id")]
                if expired_ids:
                    # 限制批量删除大小
                    MAX_DELETE_BATCH = 100
                    if len(expired_ids) > MAX_DELETE_BATCH:
                        logger.warning(f"过期会话数量超限: {len(expired_ids)},分批处理")
                        expired_ids = expired_ids[:MAX_DELETE_BATCH]
                    placeholders = ", ".join("?" for _ in expired_ids)
                    await db.execute(f"DELETE FROM auth_sessions WHERE auth_id IN ({placeholders})", tuple(expired_ids))
                    logger.info(f"🔐 [Auth] Cleaned up {len(expired_ids)} expired auth sessions (db)")
            except Exception as e:
                logger.error(f"数据库清理过期会话失败: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"❌ [Auth] Session cleanup failed: {e}", exc_info=True)


async def cleanup_expired_data_loop():
    """Background task to clean up expired data from all tables every hour."""
    while True:
        try:
            await asyncio.sleep(3600)  # 1 hour
            db = get_database_backend()
            if not db:
                logger.error("❌ [Database] Database not initialized, skipping cleanup cycle.")
                continue
            results = await db.cleanup_expired_data()
            if results:
                total = sum(results.values())
                if total > 0:
                    logger.info(f"🧹 [Cleanup] Expired data cleanup completed: {results}")
        except Exception as e:
            logger.error(f"❌ [Cleanup] Expired data cleanup failed: {e}", exc_info=True)


async def cleanup_expired_refresh_locks_loop():
    """
    清理过期的刷新锁文件（每5分钟）
    Critical Fix: Blocker #4 - 使用分布式锁的自动清理机制
    """
    lock_manager = get_lock_manager()

    while True:
        try:
            await asyncio.sleep(300)  # 5 minutes

            # 使用分布式锁管理器的清理功能
            cleaned = await lock_manager.cleanup_stale_locks()

            if cleaned > 0:
                logger.info(f"🔒 [Locks] Cleaned up {cleaned} stale distributed lock files")

            # 记录锁统计信息
            stats = lock_manager.get_lock_stats()
            if stats.get("total_locks", 0) > 0:
                logger.debug(f"🔒 [Locks] Lock stats: {stats}")

        except Exception as e:
            logger.error(f"❌ [Locks] Distributed lock cleanup failed: {e}", exc_info=True)


async def count_enabled_accounts() -> int:
    """
    Count enabled accounts with valid credentials.
    Added for health check endpoint (Major Fix #7)
    """
    try:
        accounts = await list_enabled_accounts()
        return len(accounts)
    except Exception as e:
        logger.error(f"Failed to count enabled accounts: {e}")
        return 0
