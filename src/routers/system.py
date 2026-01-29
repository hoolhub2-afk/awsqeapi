import time
import logging
from typing import Dict, Any

from fastapi import APIRouter, Response, Depends
from src.core.database import get_database_backend, row_to_dict
from src.core.http_client import get_client
from src.services.account_service import list_enabled_accounts, list_disabled_accounts
from src.api.dependencies import require_admin, ADMIN_API_KEY, ADMIN_PASSWORD
from src.security.auth import security_config

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/healthz")
async def health():
    """
    基础健康检查 - 快速响应,适用于负载均衡器探针
    检查数据库连接是否正常
    """
    try:
        # 快速数据库连接检查
        db = get_database_backend()
        if db:
            await db.fetchone("SELECT 1")
            return {
                "status": "healthy",
                "timestamp": time.time(),
                "service": "q2api"
            }
        else:
            # 数据库未初始化
            return Response(
                content='{"status":"unhealthy","reason":"database_not_initialized"}',
                status_code=503,
                media_type="application/json"
            )
    except Exception as e:
        logger.error(f"健康检查失败: {e}", exc_info=True)
        return Response(
            content=f'{{"status":"unhealthy","reason":"database_connection_failed","error":"{str(e)[:100]}"}}',
            status_code=503,
            media_type="application/json"
        )

@router.get("/health", dependencies=[Depends(require_admin)])
async def detailed_health():
    """Detailed health check"""
    health_status = {
        "status": "healthy",
        "timestamp": time.time(),
        "service": "q2api",
        "version": "2.0.0",
        "checks": {}
    }

    # Database - 增强检查
    try:
        db = get_database_backend()
        if db:
            # 测试实际查询
            start_time = time.time()
            await db.fetchone("SELECT 1")
            query_time = (time.time() - start_time) * 1000  # ms
            
            # 检查是否有启用的账号
            try:
                enabled_accounts = await list_enabled_accounts()
                account_count = len(enabled_accounts)
            except Exception as exc:
                logger.debug("health check 账号统计失败: %s", exc)
                account_count = 0
            
            health_status["checks"]["database"] = {
                "status": "healthy",
                "query_time_ms": round(query_time, 2),
                "enabled_accounts": account_count
            }
        else:
            health_status["checks"]["database"] = {"status": "uninitialized"}
            health_status["status"] = "degraded"
    except Exception as e:
        health_status["checks"]["database"] = {"status": "unhealthy", "error": str(e)[:200]}
        health_status["status"] = "unhealthy"

    # HTTP Client
    try:
        if get_client():
            health_status["checks"]["http_client"] = {"status": "healthy"}
        else:
            health_status["checks"]["http_client"] = {"status": "not_initialized"}
    except Exception as e:
        health_status["checks"]["http_client"] = {"status": "unhealthy", "error": str(e)}
        health_status["status"] = "unhealthy"

    # Accounts
    try:
        if get_database_backend():
            enabled_accounts = await list_enabled_accounts()
            health_status["checks"]["accounts"] = {
                "status": "healthy",
                "enabled_count": len(enabled_accounts)
            }
        else:
            health_status["checks"]["accounts"] = {"status": "database_not_available"}
    except Exception as e:
        health_status["checks"]["accounts"] = {"status": "unhealthy", "error": str(e)}

    # Memory
    try:
        import psutil
        process = psutil.Process()
        memory_info = process.memory_info()
        health_status["checks"]["memory"] = {
            "status": "healthy",
            "rss_mb": round(memory_info.rss / 1024 / 1024, 2),
            "vms_mb": round(memory_info.vms / 1024 / 1024, 2)
        }
    except ImportError:
        health_status["checks"]["memory"] = {"status": "monitoring_not_available"}
    except Exception as e:
        health_status["checks"]["memory"] = {"status": "unhealthy", "error": str(e)}

    return health_status

@router.get("/metrics", dependencies=[Depends(require_admin)])
async def metrics():
    """Prometheus metrics"""
    try:
        import psutil
        process = psutil.Process()

        enabled_count = 0
        disabled_count = 0
        try:
            enabled_accounts = await list_enabled_accounts()
            disabled_accounts = await list_disabled_accounts()
            enabled_count = len(enabled_accounts)
            disabled_count = len(disabled_accounts)
        except Exception as exc:
            logger.debug("metrics 账号统计失败: %s", exc)

        metrics_text = f"""
# HELP q2api_enabled_accounts_total Number of enabled accounts
# TYPE q2api_enabled_accounts_total gauge
q2api_enabled_accounts_total {enabled_count}

# HELP q2api_disabled_accounts_total Number of disabled accounts
# TYPE q2api_disabled_accounts_total gauge
q2api_disabled_accounts_total {disabled_count}

# HELP q2api_memory_bytes Memory usage in bytes
# TYPE q2api_memory_bytes gauge
q2api_memory_bytes{{type="rss"}} {process.memory_info().rss}
q2api_memory_bytes{{type="vms"}} {process.memory_info().vms}

# HELP q2api_cpu_percent CPU usage percentage
# TYPE q2api_cpu_percent gauge
q2api_cpu_percent {process.cpu_percent()}

# HELP q2api_uptime_seconds Service uptime in seconds
# TYPE q2api_uptime_seconds counter
q2api_uptime_seconds {time.time()}

# HELP q2api_security_config_status Security configuration status (1=healthy, 0=warning)
# TYPE q2api_security_config_status gauge
q2api_security_config_status {1 if not security_config.debug_mode and ADMIN_API_KEY and ADMIN_PASSWORD else 0}
"""
        return Response(metrics_text, media_type="text/plain")
    except ImportError:
        return Response("# HELP q2api_monitoring_not_available Monitoring dependencies not installed", media_type="text/plain")
