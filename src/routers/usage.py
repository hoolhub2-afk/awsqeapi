import logging

from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies import require_admin
from src.services.usage_service import get_all_usage, get_account_usage

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/v2/usage")
async def usage_all(refresh: bool = False, _: bool = Depends(require_admin)):
    try:
        return await get_all_usage(refresh=refresh)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("获取用量失败")
        raise HTTPException(status_code=500, detail=f"获取用量失败: {exc}")


@router.get("/v2/usage/{account_id}")
async def usage_one(account_id: str, refresh: bool = False, _: bool = Depends(require_admin)):
    try:
        return await get_account_usage(account_id, refresh=refresh)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("获取账号用量失败")
        raise HTTPException(status_code=500, detail=f"获取账号用量失败: {exc}")
