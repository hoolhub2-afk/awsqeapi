"""上游服务 URL 配置

基础配置 (host, port, debug 等) 在 settings.py 中。
此模块仅包含上游服务 URL 配置 (OIDC, Kiro Builder ID, Amazon Q)。

注意: 仅支持 Builder ID 授权方式（与参考项目 AIClient-2-API 一致）
"""

import os

# 导入环境加载模块，确保 .env 在任何配置读取之前加载
from src.core.env import env_loaded  # noqa: F401


# ============ OIDC 认证服务 (Builder ID) ============
OIDC_BASE = os.getenv("OIDC_BASE_URL", "https://oidc.us-east-1.amazonaws.com")
OIDC_REGISTER_URL = f"{OIDC_BASE}/client/register"
OIDC_DEVICE_AUTH_URL = f"{OIDC_BASE}/device_authorization"
OIDC_TOKEN_URL = f"{OIDC_BASE}/token"
OIDC_START_URL = os.getenv("OIDC_START_URL", "https://view.awsapps.com/start")


# ============ Kiro Builder ID Token 刷新 (AWS OIDC) ============
# 参考: AIClient-2-API/src/auth/kiro-oauth.js REFRESH_IDC_URL
# 参考: AIClient-2-API/src/scripts/kiro-idc-token-refresh.js
KIRO_BUILDER_ID_TOKEN_URL_TEMPLATE = os.getenv(
    "KIRO_BUILDER_ID_TOKEN_URL_TEMPLATE",
    "https://oidc.{region}.amazonaws.com/token",
).strip() or "https://oidc.{region}.amazonaws.com/token"
KIRO_BUILDER_ID_DEFAULT_REGION = os.getenv(
    "KIRO_BUILDER_ID_DEFAULT_REGION",
    "us-east-1",
).strip() or "us-east-1"


# ============ Amazon Q 服务 ============
AMAZON_Q_BASE_URL = os.getenv("AMAZON_Q_BASE_URL", "https://q.us-east-1.amazonaws.com").rstrip("/")
AMAZON_Q_PATH = os.getenv("AMAZON_Q_PATH", "/")
if not AMAZON_Q_PATH.startswith("/"):
    AMAZON_Q_PATH = f"/{AMAZON_Q_PATH}"
AMAZON_Q_ENDPOINT = f"{AMAZON_Q_BASE_URL}{AMAZON_Q_PATH}"

# Amazon Q 请求头配置
AMAZON_Q_TARGET = os.getenv(
    "AMAZON_Q_TARGET",
    "AmazonCodeWhispererStreamingService.GenerateAssistantResponse"
)
# Kiro/Amazon Q User-Agent (KiroIDE 风格，与参考项目一致)
AMAZON_Q_USER_AGENT = os.getenv(
    "AMAZON_Q_USER_AGENT",
    "aws-sdk-js/1.0.0 ua/2.1 os/linux lang/js md/nodejs#20.0.0 api/codewhispererruntime#1.0.0 m/E KiroIDE"
)
AMAZON_Q_X_AMZ_USER_AGENT = os.getenv(
    "AMAZON_Q_X_AMZ_USER_AGENT",
    "aws-sdk-js/1.0.0 KiroIDE"
)
AMAZON_Q_OPTOUT = os.getenv("AMAZON_Q_OPTOUT", "false")
AMAZON_Q_DEFAULT_MODEL = os.getenv("AMAZON_Q_DEFAULT_MODEL", "claude-sonnet-4").strip()

# Amazon Q 客户端环境状态
AMAZON_Q_CLIENT_OS = os.getenv("AMAZON_Q_CLIENT_OS", "linux")
AMAZON_Q_CLIENT_CWD = os.getenv("AMAZON_Q_CLIENT_CWD", "/")


# ============ 外部服务 (可选) ============
class ExternalConfig:
    def __init__(self) -> None:
        # 号池服务地址, 用于快速添加账号跳转
        self.pool_service_url = os.getenv("POOL_SERVICE_URL", "").strip()
