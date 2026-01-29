"""
配额监控和预警服务
"""
import os
import time
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

# 确保 .env 已加载
from src.core.env import env_loaded  # noqa: F401
from src.core.database import get_database_backend

logger = logging.getLogger(__name__)

# 配额预警阈值配置
QUOTA_WARNING_THRESHOLD = float(os.getenv("QUOTA_WARNING_THRESHOLD", "0.8"))  # 80%
QUOTA_CRITICAL_THRESHOLD = float(os.getenv("QUOTA_CRITICAL_THRESHOLD", "0.95"))  # 95%

class QuotaService:
    """配额监控服务"""

    @staticmethod
    async def initialize_quota_table():
        """初始化配额统计表"""
        db = get_database_backend()
        await db.execute("""
            CREATE TABLE IF NOT EXISTS quota_stats (
                account_id TEXT PRIMARY KEY,
                month_key TEXT NOT NULL,
                request_count INTEGER DEFAULT 0,
                throttle_count INTEGER DEFAULT 0,
                last_throttle_time INTEGER,
                quota_status TEXT DEFAULT 'normal',
                created_at INTEGER DEFAULT (strftime('%s', 'now')),
                updated_at INTEGER DEFAULT (strftime('%s', 'now'))
            )
        """)

        # 创建索引
        await db.execute("CREATE INDEX IF NOT EXISTS idx_quota_month ON quota_stats(month_key)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_quota_status ON quota_stats(quota_status)")

    @staticmethod
    def get_month_key() -> str:
        """获取当前月份键值"""
        return datetime.now(timezone.utc).strftime("%Y-%m")

    @staticmethod
    async def record_request(account_id: str, is_throttled: bool = False):
        """记录请求统计"""
        db = get_database_backend()
        month_key = QuotaService.get_month_key()
        now = int(time.time())

        # 更新或插入统计记录
        if is_throttled:
            await db.execute("""
                INSERT INTO quota_stats (account_id, month_key, request_count, throttle_count, last_throttle_time, updated_at)
                VALUES (?, ?, 1, 1, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    request_count = request_count + 1,
                    throttle_count = throttle_count + 1,
                    last_throttle_time = ?,
                    updated_at = ?
            """, (account_id, month_key, now, now, now, now))
        else:
            await db.execute("""
                INSERT INTO quota_stats (account_id, month_key, request_count, updated_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    request_count = request_count + 1,
                    updated_at = ?
            """, (account_id, month_key, now, now))

    @staticmethod
    async def update_quota_status(account_id: str):
        """更新配额状态"""
        db = get_database_backend()
        month_key = QuotaService.get_month_key()

        # 获取当前统计
        result = await db.fetchone(
            "SELECT request_count, throttle_count FROM quota_stats WHERE account_id = ? AND month_key = ?",
            (account_id, month_key)
        )

        if not result:
            return

        request_count = result.get("request_count")
        throttle_count = result.get("throttle_count")

        # 计算配额状态
        if throttle_count > 0:
            status = "exhausted"
        elif request_count > 0:
            # 基于请求频率估算状态
            throttle_ratio = throttle_count / request_count if request_count > 0 else 0
            if throttle_ratio >= QUOTA_CRITICAL_THRESHOLD:
                status = "critical"
            elif throttle_ratio >= QUOTA_WARNING_THRESHOLD:
                status = "warning"
            else:
                status = "normal"
        else:
            status = "normal"

        # 更新状态
        await db.execute(
            "UPDATE quota_stats SET quota_status = ?, updated_at = ? WHERE account_id = ? AND month_key = ?",
            (status, int(time.time()), account_id, month_key)
        )

        return status

    @staticmethod
    async def get_quota_stats(account_id: str) -> Optional[Dict[str, Any]]:
        """获取账号配额统计"""
        db = get_database_backend()
        month_key = QuotaService.get_month_key()

        result = await db.fetchone("""
            SELECT request_count, throttle_count, last_throttle_time, quota_status, updated_at
            FROM quota_stats
            WHERE account_id = ? AND month_key = ?
        """, (account_id, month_key))

        if not result:
            return None

        return {
            "account_id": account_id,
            "month": month_key,
            "request_count": result.get("request_count"),
            "throttle_count": result.get("throttle_count"),
            "last_throttle_time": result.get("last_throttle_time"),
            "quota_status": result.get("quota_status"),
            "updated_at": result.get("updated_at")
        }

    @staticmethod
    async def get_all_quota_stats() -> List[Dict[str, Any]]:
        """获取所有账号的配额统计"""
        db = get_database_backend()
        month_key = QuotaService.get_month_key()

        results = await db.fetchall("""
            SELECT account_id, request_count, throttle_count, last_throttle_time, quota_status, updated_at
            FROM quota_stats
            WHERE month_key = ?
            ORDER BY throttle_count DESC, request_count DESC
        """, (month_key,))

        return [
            {
                "account_id": row.get("account_id"),
                "month": month_key,
                "request_count": row.get("request_count"),
                "throttle_count": row.get("throttle_count"),
                "last_throttle_time": row.get("last_throttle_time"),
                "quota_status": row.get("quota_status"),
                "updated_at": row.get("updated_at")
            }
            for row in results
        ]

    @staticmethod
    async def check_quota_alerts() -> List[Dict[str, Any]]:
        """检查需要预警的账号"""
        db = get_database_backend()
        month_key = QuotaService.get_month_key()

        results = await db.fetchall("""
            SELECT account_id, request_count, throttle_count, quota_status
            FROM quota_stats
            WHERE month_key = ? AND quota_status IN ('warning', 'critical', 'exhausted')
        """, (month_key,))

        alerts = []
        for row in results:
            alerts.append({
                "account_id": row.get("account_id"),
                "request_count": row.get("request_count"),
                "throttle_count": row.get("throttle_count"),
                "status": row.get("quota_status"),
                "message": QuotaService._get_alert_message(row.get("quota_status"), row.get("request_count"), row.get("throttle_count"))
            })

        return alerts

    @staticmethod
    def _get_alert_message(status: str, request_count: int, throttle_count: int) -> str:
        """生成预警消息"""
        if status == "exhausted":
            return f"账号配额已耗尽，本月已被限流 {throttle_count} 次"
        elif status == "critical":
            return f"账号配额即将耗尽，请求 {request_count} 次，限流 {throttle_count} 次"
        elif status == "warning":
            return f"账号配额使用率较高，请求 {request_count} 次，限流 {throttle_count} 次"
        return "配额状态正常"