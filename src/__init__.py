
# Q2API - 主要包初始化
__version__ = "1.0.0"
__author__ = "Q2API Team"

# 导出主要组件
from .core.database import init_db, close_db
from .security.auth import security_config
from .security.advanced import create_key_manager

__all__ = [
    "init_db", "close_db", "security_config", "create_key_manager"
]
