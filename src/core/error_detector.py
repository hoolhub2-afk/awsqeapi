"""
账户错误检测器
Critical Fix: Blocker #6 - 完善AWS和API错误检测

智能分类错误类型，自动禁用暂停/封禁的账户
"""

import re
import logging
from enum import Enum
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class AccountErrorType(Enum):
    """账户错误类型分类"""
    SUSPENDED = "suspended"          # 永久暂停/封禁
    RATE_LIMITED = "rate_limited"    # 临时限速
    AUTH_ERROR = "auth_error"        # 凭证问题（可恢复）
    QUOTA_EXCEEDED = "quota_exceeded" # 配额耗尽（可能恢复）
    NETWORK_ERROR = "network_error"  # 网络错误（临时）
    CONFLICT = "conflict"            # 冲突错误
    UNKNOWN = "unknown"              # 未知错误


class AccountErrorDetector:
    """
    集中式账户错误检测器
    Critical Fix: Blocker #6 - 防止已暂停账户继续被使用

    参考AIClient-2-API (KIRO)的错误检测模式
    """

    # AWS错误代码 - 表示账户暂停/禁用
    AWS_SUSPENSION_CODES = {
        'ResourceNotFoundException',
        'InvalidAccessKeyId',
        'SignatureDoesNotMatch',
        'AccessDenied',
        'AccessDeniedException',
        'UnauthorizedException',
        'ForbiddenException',
        'AccountSuspended',
        'AccountDisabled',
        'ConflictException',
        'ValidationException',  # 有时表示账户状态无效
        'InvalidIdentityPoolConfigurationException',
        'NotAuthorizedException',
        'UserNotFoundException',
        'UserPoolTaggingException',
    }

    # Amazon Q特定错误码
    AMAZON_Q_SUSPENSION_CODES = {
        'ThrottlingException',  # 可能是永久限速
        'ServiceQuotaExceededException',
        'ResourceLimitExceededException',
        'InternalServerException',  # 某些情况下表示账户问题
    }

    # 错误消息模式 - 表示账户暂停
    SUSPENSION_PATTERNS = [
        # 账户状态（允许中间有词）
        r'account\s+.*\s*(suspended|banned|disabled|closed|terminated|deactivated)',
        r'account[_\s]+(suspended|banned|disabled|closed|terminated|deactivated)',
        r'access\s+.*\s*(revoked|denied|removed|blocked|restricted)',
        r'access[_\s]+(revoked|denied|removed|blocked|restricted)',
        r'subscription\s+.*\s*(expired|cancelled|terminated|suspended)',
        r'subscription[_\s]+(expired|cancelled|terminated|suspended)',
        r'service[_\s]+(disabled|suspended|unavailable)',

        # 权限错误
        r'permission[_\s]+(denied|revoked)',
        r'not[_\s]+authorized',
        r'unauthorized[_\s]+access',
        r'invalid[_\s]+credentials',
        r'authentication[_\s]+failed',
        r'credentials[_\s]+(expired|invalid|revoked)',

        # 资源错误
        r'resource[_\s]+not[_\s]+found',
        r'organization[_\s]+(deleted|disabled|suspended)',
        r'workspace[_\s]+(disabled|archived|deleted)',
        r'project[_\s]+(archived|deleted|suspended)',

        # 冲突错误
        r'concurrent[_\s]+access[_\s]+violation',
        r'session[_\s]+(expired|invalid|terminated)',
        r'token[_\s]+(revoked|invalid|expired)',

        # 配额错误（永久）
        r'(daily|monthly|annual)[_\s]+quota[_\s]+exceeded',
        r'(daily|monthly|annual)[_\s]+limit[_\s]+(reached|exceeded)',
        r'upgrade[_\s]+required',
        r'billing[_\s]+required',
        r'payment[_\s]+(required|failed)',
        r'trial[_\s]+(ended|expired)',

        # Amazon Q特定
        r'user[_\s]+not[_\s]+found',
        r'identity[_\s]+pool[_\s]+configuration',
        r'invalid[_\s]+identity',
    ]

    # 临时限速模式
    RATE_LIMIT_PATTERNS = [
        r'rate[_\s]+limit[_\s]+(exceeded|reached)',
        r'too[_\s]+many[_\s]+requests',
        r'throttl(ed|ing)',
        r'slow[_\s]+down',
        r'retry[_\s]+after',
        r'please[_\s]+wait',
    ]

    # 网络错误模式（临时）
    NETWORK_ERROR_PATTERNS = [
        r'connection[_\s]+(timeout|refused|reset|aborted)',
        r'network[_\s]+(error|timeout|unreachable)',
        r'dns[_\s]+(resolution|lookup)[_\s]+failed',
        r'ssl[_\s]+(error|handshake[_\s]+failed)',
        r'socket[_\s]+(timeout|error)',
        r'service[_\s]+temporarily[_\s]+unavailable',
    ]

    # 编译正则表达式（性能优化）
    _suspension_patterns = [re.compile(p, re.IGNORECASE) for p in SUSPENSION_PATTERNS]
    _rate_limit_patterns = [re.compile(p, re.IGNORECASE) for p in RATE_LIMIT_PATTERNS]
    _network_patterns = [re.compile(p, re.IGNORECASE) for p in NETWORK_ERROR_PATTERNS]

    @classmethod
    def detect_error_type(
        cls,
        exception: Exception,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None
    ) -> Tuple[AccountErrorType, str]:
        """
        检测错误类型

        Args:
            exception: 异常对象
            status_code: HTTP状态码（可选）
            error_code: 错误代码（可选，如AWS错误代码）

        Returns:
            (错误类型, 原因描述)

        Example:
            >>> error = Exception("Account suspended due to violation")
            >>> error_type, reason = AccountErrorDetector.detect_error_type(error)
            >>> error_type == AccountErrorType.SUSPENDED
            True
        """
        error_message = str(exception).lower()

        # 1. 优先检查AWS错误代码
        if error_code:
            if error_code in cls.AWS_SUSPENSION_CODES:
                return AccountErrorType.SUSPENDED, f"AWS error code: {error_code}"

            if error_code in cls.AMAZON_Q_SUSPENSION_CODES:
                # Amazon Q的ThrottlingException可能是永久的
                if "throttling" in error_code.lower() and cls._is_permanent_throttle(error_message):
                    return AccountErrorType.SUSPENDED, f"Permanent throttling: {error_code}"
                return AccountErrorType.RATE_LIMITED, f"Amazon Q error: {error_code}"

        # 2. 检查HTTP状态码
        if status_code == 401:
            return AccountErrorType.AUTH_ERROR, "HTTP 401 Unauthorized"
        elif status_code == 403:
            return AccountErrorType.SUSPENDED, "HTTP 403 Forbidden"
        elif status_code == 429:
            # 判断是临时还是永久限速
            if cls._is_permanent_rate_limit(error_message):
                return AccountErrorType.SUSPENDED, "Permanent rate limit exceeded"
            return AccountErrorType.RATE_LIMITED, "Temporary rate limit"
        elif status_code == 409:
            return AccountErrorType.CONFLICT, "HTTP 409 Conflict"

        # 3. 检查错误消息模式 - 暂停
        for pattern in cls._suspension_patterns:
            if pattern.search(error_message):
                return AccountErrorType.SUSPENDED, f"Pattern match: {pattern.pattern}"

        # 4. 检查错误消息模式 - 限速
        for pattern in cls._rate_limit_patterns:
            if pattern.search(error_message):
                if cls._is_permanent_rate_limit(error_message):
                    return AccountErrorType.SUSPENDED, f"Permanent rate limit"
                return AccountErrorType.RATE_LIMITED, f"Temporary rate limit"

        # 5. 检查网络错误
        for pattern in cls._network_patterns:
            if pattern.search(error_message):
                return AccountErrorType.NETWORK_ERROR, f"Network error: {pattern.pattern}"

        # 6. 检查配额错误
        if cls._is_quota_exceeded(error_message):
            return AccountErrorType.QUOTA_EXCEEDED, "Quota exceeded"

        # 7. 未知错误
        return AccountErrorType.UNKNOWN, error_message[:100]

    @classmethod
    def _is_permanent_rate_limit(cls, message: str) -> bool:
        """判断是否为永久性限速（配额耗尽）"""
        permanent_indicators = [
            'daily quota exceeded',
            'monthly limit reached',
            'monthly quota exceeded',
            'annual limit',
            'upgrade required',
            'billing required',
            'payment required',
            'trial ended',
            'trial expired',
            'subscription expired',
        ]
        message_lower = message.lower()
        return any(indicator in message_lower for indicator in permanent_indicators)

    @classmethod
    def _is_permanent_throttle(cls, message: str) -> bool:
        """判断Amazon Q的throttling是否为永久性"""
        # Amazon Q的throttling通常是临时的，除非明确提到quota
        return 'quota' in message.lower() or 'limit exceeded' in message.lower()

    @classmethod
    def _is_quota_exceeded(cls, message: str) -> bool:
        """检查是否为配额耗尽"""
        quota_indicators = [
            'quota exceeded',
            'quota limit',
            'request limit',
            'usage limit',
            'service quota',
        ]
        message_lower = message.lower()
        return any(indicator in message_lower for indicator in quota_indicators)

    @classmethod
    def should_disable_account(cls, error_type: AccountErrorType) -> bool:
        """
        判断是否应该禁用账户

        Args:
            error_type: 错误类型

        Returns:
            True如果应该禁用账户，False否则
        """
        return error_type in (
            AccountErrorType.SUSPENDED,
            AccountErrorType.QUOTA_EXCEEDED,  # 配额耗尽也应暂时禁用
        )

    @classmethod
    def should_mark_rate_limited(cls, error_type: AccountErrorType) -> bool:
        """
        判断是否应该标记为临时限速

        Args:
            error_type: 错误类型

        Returns:
            True如果应该标记为限速，False否则
        """
        return error_type == AccountErrorType.RATE_LIMITED

    @classmethod
    def get_retry_delay(cls, error_type: AccountErrorType) -> Optional[int]:
        """
        获取建议的重试延迟（秒）

        Args:
            error_type: 错误类型

        Returns:
            重试延迟秒数，None表示不应重试
        """
        retry_delays = {
            AccountErrorType.RATE_LIMITED: 60,      # 1分钟后重试
            AccountErrorType.NETWORK_ERROR: 5,      # 5秒后重试
            AccountErrorType.CONFLICT: 10,          # 10秒后重试
            AccountErrorType.AUTH_ERROR: 300,       # 5分钟后尝试刷新token
            AccountErrorType.QUOTA_EXCEEDED: 3600,  # 1小时后重试
            AccountErrorType.SUSPENDED: None,       # 不重试
            AccountErrorType.UNKNOWN: 30,           # 30秒后重试
        }
        return retry_delays.get(error_type)
