#!/usr/bin/env python
"""
统一启动入口 - 自动从 .env 读取配置启动 uvicorn

用法:
    python run.py                    # 生产模式
    python run.py --reload           # 开发模式（热重载）
    python run.py --workers 4        # 多进程模式
"""

import sys
import logging
from pathlib import Path

# 确保 .env 已加载
from src.core.env import env_loaded, get_env, get_env_int, get_env_bool
from src.core.logging_setup import configure_logging

def main():
    import uvicorn
    
    # 从 .env 读取配置
    host = get_env("HOST", "0.0.0.0")
    port = get_env_int("PORT", 8000)
    log_level = get_env("LOG_LEVEL", "INFO").lower()
    debug = get_env_bool("DEBUG", False)
    configure_logging(Path(__file__).resolve().parent)
    logger = logging.getLogger("q2api.startup")
    
    # 检查命令行参数
    reload_mode = "--reload" in sys.argv or debug
    workers = 1
    
    for i, arg in enumerate(sys.argv):
        if arg == "--workers" and i + 1 < len(sys.argv):
            try:
                workers = int(sys.argv[i + 1])
            except ValueError:
                logger.warning("[Startup] Invalid --workers value: %r, using default %s", sys.argv[i + 1], workers)
    
    logger.info("[Startup]")
    logger.info("  - Host: %s", host)
    logger.info("  - Port: %s", port)
    logger.info("  - Log level: %s", log_level.upper())
    logger.info("  - 热重载: %s", "是" if reload_mode else "否")
    logger.info("  - 工作进程: %s", workers)
    
    uvicorn.run(
        "app:app",
        host=host,
        port=port,
        reload=reload_mode,
        workers=1 if reload_mode else workers,  # reload 模式不支持多 workers
        log_level=log_level,
        access_log=log_level != "error",  # ERROR 级别时关闭访问日志
    )


if __name__ == "__main__":
    main()
