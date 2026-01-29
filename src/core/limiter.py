import os
import warnings
from slowapi import Limiter
from slowapi.util import get_remote_address

# 确保 .env 已加载
from src.core.env import env_loaded  # noqa: F401

# 抑制 slowapi 关于不存在配置文件的警告
warnings.filterwarnings("ignore", message="Config file '__nonexistent__.env' not found.")

rate_limit = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10000"))
# 禁用自动加载 .env 文件,避免 Windows 系统下的编码问题
# 配置已在 app.py 中通过 load_dotenv 加载
# 传入不存在的文件名来阻止 slowapi 加载 .env
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{rate_limit}/minute"],
    config_filename="__nonexistent__.env"
)
