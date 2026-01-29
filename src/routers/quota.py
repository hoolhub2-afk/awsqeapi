"""
配额监控API路由
"""
import logging
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException
from src.services.quota_service import QuotaService

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/quota/stats", response_model=List[Dict[str, Any]])
async def get_quota_stats():
    """获取所有账号的配额统计"""
    try:
        stats = await QuotaService.get_all_quota_stats()
        return stats
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("获取配额统计失败")
        raise HTTPException(status_code=500, detail=f"获取配额统计失败: {str(e)}")

@router.get("/quota/stats/{account_id}", response_model=Dict[str, Any])
async def get_account_quota_stats(account_id: str):
    """获取指定账号的配额统计"""
    try:
        stats = await QuotaService.get_quota_stats(account_id)
        if not stats:
            raise HTTPException(status_code=404, detail="账号配额统计不存在")
        return stats
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取账号配额统计失败: {str(e)}")

@router.get("/quota/alerts", response_model=List[Dict[str, Any]])
async def get_quota_alerts():
    """获取配额预警信息"""
    try:
        alerts = await QuotaService.check_quota_alerts()
        return alerts
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("获取配额预警失败")
        raise HTTPException(status_code=500, detail=f"获取配额预警失败: {str(e)}")
