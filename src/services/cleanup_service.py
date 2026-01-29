"""
自动清理日志和缓存文件服务
"""
import os
import time
import asyncio
import logging
from pathlib import Path

# 确保 .env 已加载
from src.core.env import env_loaded  # noqa: F401

logger = logging.getLogger(__name__)

# 配置
LOG_MAX_AGE_DAYS = int(os.getenv("LOG_MAX_AGE_DAYS", "7"))  # 日志保留天数
CACHE_MAX_AGE_DAYS = int(os.getenv("CACHE_MAX_AGE_DAYS", "1"))  # 缓存保留天数
CLEANUP_INTERVAL_HOURS = int(os.getenv("CLEANUP_INTERVAL_HOURS", "24"))  # 清理间隔

async def cleanup_old_files(directory: Path, max_age_days: int, pattern: str = "*") -> int:
    """删除超过指定天数的文件"""
    if not directory.exists():
        return 0

    now = time.time()
    max_age_seconds = max_age_days * 86400
    deleted_count = 0

    try:
        for file_path in directory.glob(pattern):
            if file_path.is_file():
                file_age = now - file_path.stat().st_mtime
                if file_age > max_age_seconds:
                    try:
                        file_path.unlink()
                        deleted_count += 1
                        logger.info(f"已删除过期文件: {file_path}")
                    except Exception as e:
                        logger.warning(f"删除文件失败 {file_path}: {e}")
    except Exception as e:
        logger.error(f"清理目录失败 {directory}: {e}")

    return deleted_count

async def cleanup_files_loop():
    """定期清理文件的后台任务"""
    logger.info(f"启动文件清理服务，间隔: {CLEANUP_INTERVAL_HOURS}小时")

    while True:
        try:
            # 清理日志文件
            log_dir = Path("logs")
            log_deleted = await cleanup_old_files(log_dir, LOG_MAX_AGE_DAYS, "*.log*")

            # 清理缓存文件
            cache_dir = Path("data/cache")
            cache_deleted = await cleanup_old_files(cache_dir, CACHE_MAX_AGE_DAYS)

            if log_deleted > 0 or cache_deleted > 0:
                logger.info(f"清理完成: 日志文件 {log_deleted} 个, 缓存文件 {cache_deleted} 个")

        except Exception as e:
            logger.error(f"文件清理任务异常: {e}")

        # 等待下次清理
        await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)