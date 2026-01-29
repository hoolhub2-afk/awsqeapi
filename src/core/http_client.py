import os
import httpx
import logging
import asyncio
from typing import Optional, Dict

# 确保 .env 已加载
from src.core.env import env_loaded  # noqa: F401

logger = logging.getLogger(__name__)

GLOBAL_CLIENT: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()  # Major Fix: 添加锁保护初始化

def get_proxies() -> Optional[Dict[str, str]]:
    """获取代理配置，验证 URL 格式"""
    proxy = os.getenv("HTTP_PROXY", "").strip()
    if not proxy:
        return None

    # 验证代理 URL 格式
    if not (proxy.startswith("http://") or proxy.startswith("https://")):
        logger.warning(f"Invalid proxy URL format: {proxy}")
        return None

    return {"http": proxy, "https": proxy}

async def init_global_client():
    """
    Initialize global HTTP client (idempotent).

    Major Fix: 添加锁保护和幂等性检查，防止连接池泄漏
    """
    global GLOBAL_CLIENT

    async with _client_lock:
        # 幂等性检查：如果已经初始化，先关闭旧的
        if GLOBAL_CLIENT:
            logger.warning("⚠️ Global HTTP client already exists, closing old client")
            try:
                await GLOBAL_CLIENT.aclose()
                logger.debug("Old HTTP client closed")
            except Exception as e:
                logger.error(f"❌ Error closing old HTTP client: {e}")
            GLOBAL_CLIENT = None

        # 创建新的客户端
        proxies = get_proxies()
        mounts = None
        if proxies:
            proxy_url = proxies.get("https") or proxies.get("http")
            logger.info(f"Using HTTP proxy: {proxy_url}")
            if proxy_url:
                mounts = {
                    "https://": httpx.AsyncHTTPTransport(proxy=proxy_url),
                    "http://": httpx.AsyncHTTPTransport(proxy=proxy_url),
                }
        else:
            logger.info("No HTTP proxy configured")

        limits = httpx.Limits(
            max_keepalive_connections=60,
            max_connections=100,
            keepalive_expiry=30.0
        )

        timeout = httpx.Timeout(
            connect=10.0,
            read=300.0,
            write=10.0,
            pool=10.0
        )

        GLOBAL_CLIENT = httpx.AsyncClient(
            mounts=mounts,
            timeout=timeout,
            limits=limits,
            follow_redirects=True,  # 添加重定向支持
            http2=False  # 暂不启用HTTP/2（可选）
        )

        logger.info("✅ Global HTTP client initialized successfully")


async def close_global_client():
    """Close global HTTP client with lock protection."""
    global GLOBAL_CLIENT

    async with _client_lock:
        if GLOBAL_CLIENT:
            try:
                await GLOBAL_CLIENT.aclose()
                logger.info("✅ Global HTTP client closed successfully")
            except Exception as e:
                logger.error(f"❌ Error closing HTTP client: {e}")
            finally:
                GLOBAL_CLIENT = None

def get_client() -> Optional[httpx.AsyncClient]:
    return GLOBAL_CLIENT


def create_proxied_client(timeout: float = 30.0) -> httpx.AsyncClient:
    """
    创建带代理配置的HTTP客户端（统一工厂函数）

    P0 Quick Fix: 提取重复的代理配置逻辑

    Args:
        timeout: 请求超时时间（秒）

    Returns:
        配置好的httpx.AsyncClient实例

    Note: 调用者负责关闭客户端（使用async with或手动close）
    """
    proxies = get_proxies()
    mounts = None

    if proxies:
        proxy_url = proxies.get("https") or proxies.get("http")
        if proxy_url:
            mounts = {
                "https://": httpx.AsyncHTTPTransport(proxy=proxy_url),
                "http://": httpx.AsyncHTTPTransport(proxy=proxy_url),
            }

    return httpx.AsyncClient(
        mounts=mounts,
        timeout=httpx.Timeout(timeout, connect=10.0, read=timeout, write=10.0),
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        follow_redirects=True
    )
