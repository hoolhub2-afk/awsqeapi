"""日志工具模块 - 提供安全的日志记录函数，防止敏感信息泄露"""
import logging
import os
from pathlib import Path
from typing import Any

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
    value = (os.getenv("LOG_FILE_PATH") or os.getenv("LOG_FILE") or "").strip()
    path = Path(value) if value else (base_dir / "logs" / "app.log")
    if not path.is_absolute():
        path = base_dir / path
    return path
