"""
安全工具模块
提供日志脱敏、敏感数据处理、输入验证等功能
统一的脱敏函数入口

Enhanced: Blocker #3 - 添加输入验证以防止注入攻击
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Union
from enum import Enum


# ============ 输入验证 (Critical Fix: Blocker #3) ============

class SecurityValidator:
    """
    集中式安全验证器
    Critical Fix: Blocker #3 - 防止SQL注入、XSS和其他注入攻击

    所有用户输入在进入数据库或敏感操作之前都必须通过这些验证
    """

    # API密钥格式：16-128字符，字母数字加-_
    API_KEY_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{16,128}$')

    # 账户ID格式：字母数字加-
    ACCOUNT_ID_PATTERN = re.compile(r'^[a-zA-Z0-9-]{1,64}$')

    # 模型名称格式：字母数字加-./
    MODEL_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9.\-/]{1,128}$')

    # 电子邮件格式（简化版）
    EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

    # 用户ID格式：字母数字加-_
    USER_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')

    @classmethod
    def validate_api_key(cls, key: Any) -> str:
        """
        验证API密钥格式

        Critical Fix: Blocker #3 - 防止SQL注入和目录遍历

        Args:
            key: 待验证的API密钥

        Returns:
            验证后的API密钥（去除首尾空格）

        Raises:
            ValueError: 如果密钥格式无效

        Examples:
            >>> SecurityValidator.validate_api_key("sk-1234567890abcdef")
            "sk-1234567890abcdef"
            >>> SecurityValidator.validate_api_key("invalid!@#$")
            ValueError: API key contains invalid characters
        """
        if not key or not isinstance(key, str):
            raise ValueError("API key must be a non-empty string")

        # 去除首尾空格
        key = key.strip()

        # 检查长度
        if len(key) < 16:
            raise ValueError(f"API key too short: minimum 16 characters, got {len(key)}")

        if len(key) > 128:
            raise ValueError(f"API key too long: maximum 128 characters, got {len(key)}")

        # 白名单验证 - 只允许安全字符
        if not cls.API_KEY_PATTERN.match(key):
            raise ValueError(
                "API key contains invalid characters. "
                "Only alphanumeric characters, hyphens, and underscores are allowed"
            )

        return key

    @classmethod
    def validate_account_id(cls, account_id: Any) -> str:
        """
        验证账户ID格式

        Args:
            account_id: 待验证的账户ID

        Returns:
            验证后的账户ID

        Raises:
            ValueError: 如果ID格式无效
        """
        if not account_id or not isinstance(account_id, str):
            raise ValueError("Account ID must be a non-empty string")

        account_id = account_id.strip()

        if len(account_id) < 1 or len(account_id) > 64:
            raise ValueError(f"Account ID length must be 1-64 characters, got {len(account_id)}")

        if not cls.ACCOUNT_ID_PATTERN.match(account_id):
            raise ValueError(
                "Account ID contains invalid characters. "
                "Only alphanumeric characters and hyphens are allowed"
            )

        return account_id

    @classmethod
    def validate_model_name(cls, model: Any) -> str:
        """
        验证模型名称格式

        Args:
            model: 待验证的模型名称

        Returns:
            验证后的模型名称

        Raises:
            ValueError: 如果模型名称无效
        """
        if not model or not isinstance(model, str):
            raise ValueError("Model name must be a non-empty string")

        model = model.strip()

        if len(model) < 1 or len(model) > 128:
            raise ValueError(f"Model name length must be 1-128 characters, got {len(model)}")

        if not cls.MODEL_NAME_PATTERN.match(model):
            raise ValueError(
                "Model name contains invalid characters. "
                "Only alphanumeric characters, hyphens, dots, and slashes are allowed"
            )

        return model

    @classmethod
    def validate_email(cls, email: Any) -> str:
        """
        验证电子邮件格式

        Args:
            email: 待验证的电子邮件地址

        Returns:
            验证后的电子邮件（小写）

        Raises:
            ValueError: 如果电子邮件格式无效
        """
        if not email or not isinstance(email, str):
            raise ValueError("Email must be a non-empty string")

        email = email.strip().lower()

        if len(email) > 254:  # RFC 5321
            raise ValueError(f"Email too long: maximum 254 characters, got {len(email)}")

        if not cls.EMAIL_PATTERN.match(email):
            raise ValueError("Invalid email format")

        return email

    @classmethod
    def validate_user_id(cls, user_id: Any) -> str:
        """
        验证用户ID格式

        Args:
            user_id: 待验证的用户ID

        Returns:
            验证后的用户ID

        Raises:
            ValueError: 如果ID格式无效
        """
        if not user_id or not isinstance(user_id, str):
            raise ValueError("User ID must be a non-empty string")

        user_id = user_id.strip()

        if len(user_id) < 1 or len(user_id) > 64:
            raise ValueError(f"User ID length must be 1-64 characters, got {len(user_id)}")

        if not cls.USER_ID_PATTERN.match(user_id):
            raise ValueError(
                "User ID contains invalid characters. "
                "Only alphanumeric characters, hyphens, and underscores are allowed"
            )

        return user_id

    @classmethod
    def sanitize_path(cls, path: str) -> str:
        """
        防止目录遍历攻击

        Args:
            path: 待验证的路径

        Returns:
            安全的路径

        Raises:
            ValueError: 如果路径包含危险模式
        """
        if not path or not isinstance(path, str):
            raise ValueError("Path must be a non-empty string")

        # 检测目录遍历模式
        dangerous_patterns = [
            '..',      # 父目录
            '~',       # 用户主目录
            '/etc',    # Unix系统目录
            '/proc',   # Unix进程信息
            '/sys',    # Unix系统信息
            'C:',      # Windows驱动器
            'D:',      # Windows驱动器
        ]

        path_lower = path.lower()
        for pattern in dangerous_patterns:
            if pattern in path_lower:
                raise ValueError(f"Path contains dangerous pattern: {pattern}")

        # 不允许绝对路径
        if path.startswith('/') or path.startswith('\\'):
            raise ValueError("Absolute paths are not allowed")

        return path

    @classmethod
    def validate_integer(cls, value: Any, min_val: Optional[int] = None, max_val: Optional[int] = None, name: str = "value") -> int:
        """
        验证整数值范围

        Args:
            value: 待验证的值
            min_val: 最小值（可选）
            max_val: 最大值（可选）
            name: 字段名称（用于错误消息）

        Returns:
            验证后的整数

        Raises:
            ValueError: 如果值无效或超出范围
        """
        try:
            int_val = int(value)
        except (TypeError, ValueError) as e:
            raise ValueError(f"{name} must be an integer, got {type(value).__name__}: {e}")

        if min_val is not None and int_val < min_val:
            raise ValueError(f"{name} must be >= {min_val}, got {int_val}")

        if max_val is not None and int_val > max_val:
            raise ValueError(f"{name} must be <= {max_val}, got {int_val}")

        return int_val

    @classmethod
    def validate_float(cls, value: Any, min_val: Optional[float] = None, max_val: Optional[float] = None, name: str = "value") -> float:
        """
        验证浮点数值范围

        Args:
            value: 待验证的值
            min_val: 最小值（可选）
            max_val: 最大值（可选）
            name: 字段名称（用于错误消息）

        Returns:
            验证后的浮点数

        Raises:
            ValueError: 如果值无效或超出范围
        """
        try:
            float_val = float(value)
        except (TypeError, ValueError) as e:
            raise ValueError(f"{name} must be a number, got {type(value).__name__}: {e}")

        if min_val is not None and float_val < min_val:
            raise ValueError(f"{name} must be >= {min_val}, got {float_val}")

        if max_val is not None and float_val > max_val:
            raise ValueError(f"{name} must be <= {max_val}, got {float_val}")

        return float_val


# ============ 日志脱敏函数 ============

def mask_token(token: Optional[str], visible_chars: int = 4) -> str:
    """
    掩码 Token (保留前后各 N 个字符)

    Args:
        token: 原始 Token
        visible_chars: 可见字符数量

    Returns:
        掩码后的 Token

    Examples:
        >>> mask_token("sk-abc123def456ghi789")
        "sk-a...i789"
        >>> mask_token("very_long_access_token_12345678")
        "very...5678"
    """
    if not token or not isinstance(token, str):
        return "***"

    if len(token) <= visible_chars * 2:
        return "***"

    return f"{token[:visible_chars]}...{token[-visible_chars:]}"


def mask_email(email: Optional[str]) -> str:
    """
    掩码邮箱地址

    Args:
        email: 邮箱地址

    Returns:
        掩码后的邮箱

    Examples:
        >>> mask_email("user@example.com")
        "u***@example.com"
    """
    if not email or not isinstance(email, str) or '@' not in email:
        return "***@***.***"

    parts = email.split('@')
    if len(parts) != 2:
        return "***@***.***"

    username, domain = parts
    if len(username) <= 1:
        masked_username = "*"
    else:
        masked_username = username[0] + "***"

    return f"{masked_username}@{domain}"


def mask_sensitive_dict(data: Dict[str, Any], sensitive_keys: Optional[list] = None) -> Dict[str, Any]:
    """
    掩码字典中的敏感字段

    Args:
        data: 原始字典
        sensitive_keys: 敏感键列表(默认包含常见敏感字段)

    Returns:
        掩码后的字典(新对象,不修改原字典)
    """
    if not data or not isinstance(data, dict):
        return {}

    # 默认敏感字段
    default_sensitive = {
        'password', 'secret', 'token', 'key', 'api_key', 'apiKey',
        'accessToken', 'refreshToken', 'clientSecret', 'authorization',
        'Bearer', 'x-api-key', 'masterKey', 'MASTER_KEY'
    }

    sensitive_set = set(sensitive_keys) if sensitive_keys else default_sensitive

    # 创建新字典
    masked = {}
    for key, value in data.items():
        key_lower = key.lower()

        # 检查是否为敏感字段
        is_sensitive = any(sens.lower() in key_lower for sens in sensitive_set)

        if is_sensitive:
            if isinstance(value, str):
                masked[key] = mask_token(value)
            else:
                masked[key] = "***"
        elif isinstance(value, dict):
            # 递归处理嵌套字典
            masked[key] = mask_sensitive_dict(value, sensitive_keys)
        elif isinstance(value, list):
            # 处理列表
            masked[key] = [
                mask_sensitive_dict(item, sensitive_keys) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            masked[key] = value

    return masked


def mask_url_params(url: str) -> str:
    """
    掩码 URL 中的敏感参数

    Args:
        url: 原始 URL

    Returns:
        掩码后的 URL

    Examples:
        >>> mask_url_params("https://api.example.com/v1/users?token=abc123&page=1")
        "https://api.example.com/v1/users?token=***&page=1"
    """
    if not url or not isinstance(url, str):
        return url

    # 敏感参数模式
    sensitive_params = ['token', 'key', 'secret', 'password', 'auth', 'api_key']

    for param in sensitive_params:
        # 匹配 ?param=value 或 &param=value
        pattern = rf'([?&]{param}=)[^&]*'
        url = re.sub(pattern, r'\1***', url, flags=re.IGNORECASE)

    return url


def mask_account_info(account: Dict[str, Any]) -> Dict[str, Any]:
    """
    掩码账号信息 (针对 accounts 表结构优化)

    Args:
        account: 账号字典

    Returns:
        掩码后的账号信息
    """
    if not account or not isinstance(account, dict):
        return {}

    masked = account.copy()

    # 掩码敏感字段
    if 'clientSecret' in masked and masked['clientSecret']:
        masked['clientSecret'] = mask_token(masked['clientSecret'], 6)

    if 'accessToken' in masked and masked['accessToken']:
        masked['accessToken'] = mask_token(masked['accessToken'], 8)

    if 'refreshToken' in masked and masked['refreshToken']:
        masked['refreshToken'] = mask_token(masked['refreshToken'], 6)

    # 保留非敏感字段
    # id, label, enabled, error_count, success_count, last_refresh_time, etc.

    return masked


def sanitize_log_message(message: str) -> str:
    """
    清理日志消息中的敏感信息

    Args:
        message: 原始日志消息

    Returns:
        清理后的消息
    """
    if not message or not isinstance(message, str):
        return message

    # 匹配常见 Token 格式并替换
    # Bearer tokens
    message = re.sub(r'Bearer\s+[A-Za-z0-9\-_.]+', 'Bearer ***', message, flags=re.IGNORECASE)

    # sk- 开头的 API Keys
    message = re.sub(r'sk-[A-Za-z0-9]{40,}', 'sk-***', message)

    # JWT tokens (三段 base64)
    message = re.sub(r'eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}', 'eyJ***.eyJ***.***', message)

    # UUID tokens (可能是 refresh token)
    message = re.sub(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', '****-****-****-****-************', message)

    # 邮箱地址
    message = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '***@***.***', message)

    return message


def safe_repr(obj: Any, max_length: int = 200) -> str:
    """
    安全的对象表示 (自动脱敏并限制长度)

    Args:
        obj: 任意对象
        max_length: 最大长度

    Returns:
        安全的字符串表示
    """
    if isinstance(obj, dict):
        masked = mask_sensitive_dict(obj)
        repr_str = repr(masked)
    elif isinstance(obj, str):
        repr_str = sanitize_log_message(obj)
    else:
        repr_str = repr(obj)

    if len(repr_str) > max_length:
        return repr_str[:max_length - 3] + "..."

    return repr_str


# ============ 从 logging_utils.py 合并的函数 ============

def sanitize_account_id(account_id: str) -> str:
    """脱敏账号 ID，仅显示前 8 位"""
    if not account_id or len(account_id) <= 8:
        return "***"
    return f"{account_id[:8]}***"


def sanitize_error_message(error: Exception) -> str:
    """脱敏错误信息，仅返回异常类型，不包含详细消息"""
    return type(error).__name__


def safe_log_error(logger: logging.Logger, message: str, error: Exception, *, include_traceback: bool = False) -> None:
    """安全记录错误日志，避免泄露敏感信息

    Args:
        logger: Logger 实例
        message: 日志消息
        error: 异常对象
        include_traceback: 是否包含完整堆栈跟踪（默认 False）
    """
    error_type = sanitize_error_message(error)
    full_message = f"{message}: {error_type}"

    if include_traceback:
        logger.error(full_message, exc_info=True)
    else:
        logger.error(full_message)


def safe_log_warning(logger: logging.Logger, message: str, error: Exception) -> None:
    """安全记录警告日志"""
    error_type = sanitize_error_message(error)
    logger.warning(f"{message}: {error_type}")


def resolve_log_file_path(base_dir: Path) -> Path:
    """解析日志文件路径"""
    value = (os.getenv("LOG_FILE_PATH") or os.getenv("LOG_FILE") or "").strip()
    path = Path(value) if value else (base_dir / "logs" / "app.log")
    if not path.is_absolute():
        path = base_dir / path
    return path


# 导出常用函数
__all__ = [
    # 输入验证 (NEW - Blocker #3)
    'SecurityValidator',
    # 原有函数
    'mask_token',
    'mask_email',
    'mask_sensitive_dict',
    'mask_url_params',
    'mask_account_info',
    'sanitize_log_message',
    'safe_repr',
    # 从 logging_utils 合并的函数
    'sanitize_account_id',
    'sanitize_error_message',
    'safe_log_error',
    'safe_log_warning',
    'resolve_log_file_path',
]
