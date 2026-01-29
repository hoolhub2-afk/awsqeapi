"""
环境变量统一加载模块

这是项目中环境变量的唯一入口点。
任何需要读取环境变量的模块都应该在顶部导入此模块：
    from src.core.env import env_loaded  # 确保 .env 已加载

这样无论模块导入顺序如何，.env 文件都会被正确加载。
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 计算项目根目录和 .env 文件路径
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

# 加载 .env 文件
# override=False: 不覆盖已存在的环境变量（系统环境变量优先）
_loaded = load_dotenv(_ENV_FILE, override=False)

# 导出标志，供其他模块确认环境已加载
env_loaded = True
env_file_path = str(_ENV_FILE)
env_file_exists = _ENV_FILE.exists()


def get_env(key: str, default: str = "") -> str:
    """安全获取环境变量，确保 .env 已加载"""
    return os.getenv(key, default)


def get_env_bool(key: str, default: bool = False) -> bool:
    """获取布尔类型环境变量"""
    value = os.getenv(key, "").strip().lower()
    if not value:
        return default
    return value in ("true", "1", "yes", "on", "enabled")


def get_env_int(key: str, default: int = 0) -> int:
    """获取整数类型环境变量"""
    value = os.getenv(key, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def get_env_float(key: str, default: float = 0.0) -> float:
    """获取浮点数类型环境变量"""
    value = os.getenv(key, "").strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def get_env_list(key: str, default: list = None, separator: str = ",") -> list:
    """获取列表类型环境变量"""
    if default is None:
        default = []
    value = os.getenv(key, "").strip()
    if not value:
        return default
    return [item.strip() for item in value.split(separator) if item.strip()]
