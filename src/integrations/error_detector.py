"""
上游错误检测器 - 识别配额耗尽、账号封禁等错误
"""
import json
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def detect_upstream_error(error_text: str, status_code: int = 0) -> Tuple[Optional[str], Optional[str]]:
    """
    检测上游错误类型

    Returns:
        (error_type, error_message)
        error_type: 'quota_exhausted' | 'account_suspended' | 'access_denied' | None
    """
    if not error_text:
        return None, None

    # 配额耗尽检测
    if _is_quota_exhausted(error_text):
        return 'quota_exhausted', '账号月度配额已耗尽'

    # 账号封禁检测
    if _is_account_suspended(error_text):
        return 'account_suspended', '账号已被临时封禁'

    # 访问拒绝检测
    if _is_access_denied(error_text, status_code):
        return 'access_denied', '账号权限不足或已失效'

    return None, None


def _is_quota_exhausted(error_text: str) -> bool:
    """检测配额耗尽"""
    quota_keywords = [
        'MONTHLY_REQUEST_COUNT',
        'ThrottlingException',
        'quota exhausted',
        'rate limit exceeded',
        'too many requests'
    ]

    if any(kw in error_text for kw in quota_keywords):
        return True

    try:
        err_json = json.loads(error_text)
        if isinstance(err_json, dict):
            return (err_json.get('__type') == 'com.amazon.aws.codewhisperer#ThrottlingException' and
                    err_json.get('reason') == 'MONTHLY_REQUEST_COUNT')
    except Exception as exc:
        logger.debug("quota 错误 JSON 解析失败: %s", exc)

    return False


def _is_account_suspended(error_text: str) -> bool:
    """检测账号封禁"""
    suspend_keywords = [
        'TEMPORARILY_SUSPENDED',
        'account suspended',
        'account disabled',
        'account blocked'
    ]

    if any(kw in error_text for kw in suspend_keywords):
        return True

    try:
        err_json = json.loads(error_text)
        if isinstance(err_json, dict):
            reason = err_json.get('reason', '')
            return reason == 'TEMPORARILY_SUSPENDED'
    except Exception as exc:
        logger.debug("suspend 错误 JSON 解析失败: %s", exc)

    return False


def _is_access_denied(error_text: str, status_code: int) -> bool:
    """检测访问拒绝"""
    if status_code == 403:
        return True

    access_keywords = [
        'AccessDeniedException',
        'Forbidden',
        'access denied',
        'unauthorized',
        'invalid credentials'
    ]

    if any(kw in error_text for kw in access_keywords):
        return True

    try:
        err_json = json.loads(error_text)
        if isinstance(err_json, dict):
            error_type = err_json.get('__type', '')
            return 'AccessDeniedException' in error_type or 'Forbidden' in error_type
    except Exception as exc:
        logger.debug("access 错误 JSON 解析失败: %s", exc)

    return False
