"""统一的重试和账号故障转移逻辑"""
import random
import logging
from typing import TypeVar, Callable, List, Dict, Any, Set, Tuple, Awaitable

from src.integrations.amazonq_client import QuotaExhaustedException, AccountSuspendedException

logger = logging.getLogger(__name__)

T = TypeVar('T')

async def retry_with_account_fallback(
    initial_account: Dict[str, Any],
    operation: Callable[[Dict[str, Any]], Awaitable[T]],
    get_enabled_accounts: Callable[[], Awaitable[List[Dict[str, Any]]]],
    max_retries: int = 3,
    exception_types: Tuple = (QuotaExhaustedException, AccountSuspendedException)
) -> Tuple[T, Dict[str, Any]]:
    """统一的账号重试逻辑
    
    Args:
        initial_account: 初始账号
        operation: 要执行的操作，接收账号作为参数
        get_enabled_accounts: 获取可用账号列表的函数
        max_retries: 最大重试次数
        exception_types: 需要重试的异常类型
        
    Returns:
        (result, account_used): 操作结果和使用的账号
        
    Raises:
        HTTPException: 所有账号都失败时抛出
    """
    tried_accounts: Set[str] = {initial_account["id"]}
    account = initial_account
    
    for attempt in range(max_retries):
        try:
            result = await operation(account)
            return result, account
        except exception_types as e:
            error_type = type(e).__name__
            logger.warning(f"Account {account['id'][:8]}*** failed: {error_type}")
            
            if attempt < max_retries - 1:
                # 选择新账号
                candidates = await get_enabled_accounts()
                available = [acc for acc in candidates if acc["id"] not in tried_accounts]
                
                if available:
                    account = random.choice(available)
                    tried_accounts.add(account["id"])
                    logger.info(f"Switched to new account: {account['id'][:8]}***")
                    continue
            
            # 所有账号都失败
            from fastapi import HTTPException
            if isinstance(e, QuotaExhaustedException):
                raise HTTPException(status_code=402, detail="所有账号配额已耗尽，请稍后重试")
            elif isinstance(e, AccountSuspendedException):
                raise HTTPException(status_code=403, detail="所有账号均已被封禁,请联系管理员")
            raise
