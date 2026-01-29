"""
Kiro 服务模块

仅支持 Builder ID 授权方式（与参考项目 AIClient-2-API 一致）
"""
import os
from typing import Any, Dict, Optional

import httpx

from src.core.config import (
    KIRO_BUILDER_ID_TOKEN_URL_TEMPLATE,
    KIRO_BUILDER_ID_DEFAULT_REGION,
)


def normalize_region(region: Optional[str]) -> str:
    """标准化 region，默认使用 us-east-1"""
    r = (region or "").strip()
    return r or KIRO_BUILDER_ID_DEFAULT_REGION


def kiro_builder_id_token_url(region: Optional[str]) -> str:
    """生成 Builder ID Token 刷新 URL"""
    return KIRO_BUILDER_ID_TOKEN_URL_TEMPLATE.format(region=normalize_region(region))


async def refresh_kiro_builder_id_token(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    region: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """
    刷新 Kiro Builder ID Token (使用 AWS OIDC 端点)

    参考: AIClient-2-API/src/auth/kiro-oauth.js REFRESH_IDC_URL
    参考: AIClient-2-API/src/scripts/kiro-idc-token-refresh.js

    Args:
        client_id: AWS OIDC client ID
        client_secret: AWS OIDC client secret
        refresh_token: Kiro refresh token
        region: AWS region (默认 us-east-1)
        client: 可选的 httpx 客户端

    Returns:
        包含 accessToken, refreshToken, expiresIn 的字典
    """
    url = kiro_builder_id_token_url(region)
    payload = {
        "grantType": "refresh_token",
        "clientId": client_id,
        "clientSecret": client_secret,
        "refreshToken": refresh_token,
    }
    headers = {
        "content-type": "application/json",
        "user-agent": os.getenv("KIRO_USER_AGENT", "KiroIDE"),
    }

    if client is None:
        from src.core.http_client import create_proxied_client
        async with create_proxied_client(timeout=30.0) as temp:
            r = await temp.post(url, json=payload, headers=headers)
    else:
        r = await client.post(url, json=payload, headers=headers)

    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or not data.get("accessToken"):
        raise ValueError("Kiro Builder ID refresh response missing accessToken")
    return data
