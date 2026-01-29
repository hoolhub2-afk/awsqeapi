import os

# 确保 .env 已加载
from src.core.env import env_loaded  # noqa: F401
from src.security.advanced import create_key_manager, SecurityLevel

def _get_security_level() -> SecurityLevel:
    """根据环境变量获取安全级别"""
    level_str = os.getenv("SECURITY_LEVEL", "production").upper()
    if level_str == "MILITARY":
        return SecurityLevel.MILITARY
    elif level_str == "DEVELOPMENT":
        return SecurityLevel.DEVELOPMENT
    else:
        return SecurityLevel.PRODUCTION

SECURITY_LEVEL = _get_security_level()
advanced_key_manager = create_key_manager(SECURITY_LEVEL)
