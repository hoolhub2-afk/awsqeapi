"""
输入验证工具模块
提供统一的输入验证和安全检查功能
"""

import uuid
import re
from typing import Optional, List
from fastapi import HTTPException

# 常量定义
MAX_BATCH_SIZE = 100
MAX_STRING_LENGTH = 1000
MAX_LABEL_LENGTH = 200


def validate_uuid(value: str, field_name: str = "id") -> str:
    """
    验证 UUID 格式
    
    Args:
        value: 待验证的字符串
        field_name: 字段名称(用于错误消息)
    
    Returns:
        验证通过的 UUID 字符串
    
    Raises:
        HTTPException: UUID 格式无效
    """
    if not value or not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field_name} cannot be empty")
    
    try:
        # 验证并规范化 UUID
        uuid_obj = uuid.UUID(value)
        return str(uuid_obj)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name} format: must be a valid UUID")


def validate_uuid_list(values: List[str], field_name: str = "ids", max_count: Optional[int] = None) -> List[str]:
    """
    验证 UUID 列表
    
    Args:
        values: UUID 字符串列表
        field_name: 字段名称
        max_count: 最大数量限制
    
    Returns:
        验证通过的 UUID 列表
    
    Raises:
        HTTPException: 格式无效或数量超限
    """
    if not values:
        raise HTTPException(status_code=400, detail=f"{field_name} cannot be empty")
    
    if not isinstance(values, list):
        raise HTTPException(status_code=400, detail=f"{field_name} must be a list")
    
    # 检查数量限制
    max_limit = max_count or MAX_BATCH_SIZE
    if len(values) > max_limit:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} count exceeds limit: {len(values)} > {max_limit}"
        )
    
    # 验证每个 UUID
    validated = []
    for idx, value in enumerate(values):
        try:
            validated.append(validate_uuid(value, f"{field_name}[{idx}]"))
        except HTTPException as e:
            raise HTTPException(status_code=400, detail=f"{field_name}[{idx}]: {e.detail}")
    
    return validated


def validate_string(value: str, field_name: str, max_length: int = MAX_STRING_LENGTH, allow_empty: bool = False) -> str:
    """
    验证字符串长度和内容
    
    Args:
        value: 待验证的字符串
        field_name: 字段名称
        max_length: 最大长度
        allow_empty: 是否允许空字符串
    
    Returns:
        验证通过的字符串
    
    Raises:
        HTTPException: 验证失败
    """
    if value is None:
        if allow_empty:
            return ""
        raise HTTPException(status_code=400, detail=f"{field_name} cannot be null")
    
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field_name} must be a string")
    
    if not allow_empty and not value.strip():
        raise HTTPException(status_code=400, detail=f"{field_name} cannot be empty")
    
    if len(value) > max_length:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} exceeds maximum length: {len(value)} > {max_length}"
        )
    
    return value


def validate_label(value: Optional[str]) -> Optional[str]:
    """
    验证账号标签
    
    Args:
        value: 标签值
    
    Returns:
        验证通过的标签
    """
    if value is None:
        return None
    
    return validate_string(value, "label", max_length=MAX_LABEL_LENGTH, allow_empty=True)


def validate_api_key_format(api_key: str) -> bool:
    """
    验证 API Key 格式 (sk-开头 + 48位字母数字)
    
    Args:
        api_key: API 密钥
    
    Returns:
        是否符合格式
    """
    if not api_key or not isinstance(api_key, str):
        return False
    
    # sk- + 48位字母数字 = 总长度 51
    pattern = r'^sk-[A-Za-z0-9]{48}$'
    return bool(re.match(pattern, api_key))


def sanitize_sql_like_pattern(pattern: str) -> str:
    """
    清理 SQL LIKE 模式,防止注入
    
    Args:
        pattern: 用户输入的模式
    
    Returns:
        清理后的模式
    """
    if not pattern:
        return ""
    
    # 转义特殊字符
    escaped = pattern.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return escaped


def validate_batch_size(size: int, operation: str = "batch operation") -> None:
    """
    验证批量操作大小
    
    Args:
        size: 操作数量
        operation: 操作名称
    
    Raises:
        HTTPException: 数量超限
    """
    if size <= 0:
        raise HTTPException(status_code=400, detail=f"{operation} size must be positive")
    
    if size > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"{operation} size exceeds limit: {size} > {MAX_BATCH_SIZE}"
        )


def validate_pagination(offset: int, limit: int) -> tuple[int, int]:
    """
    验证分页参数
    
    Args:
        offset: 偏移量
        limit: 每页数量
    
    Returns:
        (offset, limit) 验证后的值
    
    Raises:
        HTTPException: 参数无效
    """
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be non-negative")
    
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be positive")
    
    if limit > 1000:
        raise HTTPException(status_code=400, detail="limit exceeds maximum: 1000")
    
    return offset, limit


def validate_account_credentials(client_id: Optional[str], client_secret: Optional[str], refresh_token: Optional[str]) -> None:
    """
    验证账号凭证完整性
    
    Args:
        client_id: 客户端 ID
        client_secret: 客户端密钥
        refresh_token: 刷新令牌
    
    Raises:
        HTTPException: 凭证不完整
    """
    missing = []
    if not client_id or not client_id.strip():
        missing.append("clientId")
    if not client_secret or not client_secret.strip():
        missing.append("clientSecret")
    if not refresh_token or not refresh_token.strip():
        missing.append("refreshToken")
    
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing required credentials: {', '.join(missing)}"
        )
