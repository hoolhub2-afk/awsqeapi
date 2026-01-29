"""
健康检查端点
Major Fix #7: 为负载均衡器和Kubernetes提供健康检查

提供三个端点：
- /health - 简单存活检查
- /ready - 就绪检查（包含依赖项检查）
- /status - 详细状态信息
"""

import asyncio
import logging
import time
from typing import Dict, Any
from fastapi import APIRouter, Response
import psutil

from src.core.database import get_database_backend
from src.services.account_service import count_enabled_accounts

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

# 启动时间
_START_TIME = time.time()


@router.get("/health")
async def liveness_probe():
    """
    存活探针 - 服务是否在运行？
    返回200表示服务存活

    用于Kubernetes liveness probe和负载均衡器健康检查
    """
    return {
        "status": "ok",
        "timestamp": time.time()
    }


@router.get("/ready")
async def readiness_probe(response: Response):
    """
    就绪探针 - 服务是否准备好接受流量？
    返回200表示就绪，503表示未就绪

    检查关键依赖项：
    - 数据库连接
    - 可用账户数量
    - 内存使用

    用于Kubernetes readiness probe
    """
    checks = {}
    all_healthy = True

    # 检查数据库
    try:
        db = get_database_backend()
        # 简单查询测试连接
        await db.fetchone("SELECT 1", ())
        checks["database"] = {"healthy": True, "message": "Database OK"}
    except Exception as e:
        checks["database"] = {"healthy": False, "message": str(e)}
        all_healthy = False
        logger.error(f"Health check - database failed: {e}")

    # 检查可用账户
    try:
        enabled_count = await count_enabled_accounts()
        healthy = enabled_count > 0
        checks["accounts"] = {
            "healthy": healthy,
            "enabled_accounts": enabled_count,
            "message": "OK" if healthy else "No enabled accounts"
        }
        if not healthy:
            all_healthy = False
    except Exception as e:
        checks["accounts"] = {"healthy": False, "message": str(e)}
        all_healthy = False
        logger.error(f"Health check - accounts failed: {e}")

    # 检查内存使用
    try:
        memory = psutil.virtual_memory()
        memory_ok = memory.percent < 90
        checks["memory"] = {
            "healthy": memory_ok,
            "percent": memory.percent,
            "message": "OK" if memory_ok else "High memory usage"
        }
        if not memory_ok:
            all_healthy = False
    except Exception as e:
        checks["memory"] = {"healthy": False, "message": str(e)}
        logger.error(f"Health check - memory failed: {e}")

    # 设置响应状态码
    response.status_code = 200 if all_healthy else 503

    return {
        "ready": all_healthy,
        "checks": checks,
        "timestamp": time.time()
    }


@router.get("/status")
async def detailed_status():
    """
    详细状态信息 - 用于监控和调试

    包含：
    - 版本信息
    - 运行时长
    - 资源使用
    - 依赖项状态
    """
    uptime_seconds = time.time() - _START_TIME

    # 系统资源
    try:
        process = psutil.Process()
        memory_info = process.memory_info()
        cpu_percent = process.cpu_percent(interval=0.1)

        resources = {
            "memory_rss_mb": memory_info.rss / 1024 / 1024,
            "memory_vms_mb": memory_info.vms / 1024 / 1024,
            "memory_percent": psutil.virtual_memory().percent,
            "cpu_percent": cpu_percent,
            "connections": len(process.connections()) if hasattr(process, 'connections') else 0
        }
    except Exception as e:
        logger.warning(f"Failed to get process info: {e}")
        resources = {"error": str(e)}

    # 数据库状态
    db_status = {}
    try:
        db = get_database_backend()
        await db.fetchone("SELECT 1", ())
        db_status = {"connected": True, "type": type(db).__name__}
    except Exception as e:
        db_status = {"connected": False, "error": str(e)}

    # 账户状态
    accounts_status = {}
    try:
        enabled_count = await count_enabled_accounts()
        accounts_status = {"enabled": enabled_count}
    except Exception as e:
        accounts_status = {"error": str(e)}

    return {
        "service": "q2api",
        "version": "1.0.0",  # 可以从环境变量或配置文件读取
        "uptime_seconds": uptime_seconds,
        "uptime_human": f"{int(uptime_seconds // 3600)}h {int((uptime_seconds % 3600) // 60)}m",
        "resources": resources,
        "database": db_status,
        "accounts": accounts_status,
        "timestamp": time.time()
    }
