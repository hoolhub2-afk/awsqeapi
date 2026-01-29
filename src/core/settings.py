"""
应用配置模块 - 使用 Pydantic 进行环境变量验证
统一管理所有环境变量并提供类型安全的访问
"""

import os
from typing import Optional, List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """应用配置类"""
    
    # 数据库配置
    database_url: str = Field(default="", description="数据库连接 URL,留空使用 SQLite")
    
    # API 密钥配置
    openai_keys: str = Field(default="", description="OpenAI API Keys 白名单,逗号分隔")
    
    # Token 配置
    token_count_multiplier: float = Field(default=1.0, description="Token 计数倍率")
    
    # 安全配置
    master_key: str = Field(default="", description="主加密密钥 (base64/hex,至少 32 字节)")
    master_key_path: str = Field(default="master.key", description="主密钥文件路径")
    
    # 账号管理
    max_error_count: int = Field(default=100, description="账号错误次数阈值")
    auto_disable_incomplete_accounts: bool = Field(default=False, description="自动禁用不完整账号")
    
    # 网络配置
    http_proxy: str = Field(default="", description="HTTP 代理地址")
    
    # 控制台配置
    enable_console: bool = Field(default=True, description="启用 Web 管理控制台")
    admin_api_key: Optional[str] = Field(default=None, description="管理员 API Key")
    admin_password: Optional[str] = Field(default=None, description="管理员密码")
    
    # 服务配置
    port: int = Field(default=8000, description="服务端口")
    host: str = Field(default="0.0.0.0", description="服务监听地址")
    
    # 安全配置
    debug: bool = Field(default=False, description="调试模式")
    log_level: str = Field(default="INFO", description="日志级别")
    trusted_hosts: str = Field(default="", description="信任的 Host 列表,逗号分隔")
    cors_origins: str = Field(default="", description="CORS 允许的来源,逗号分隔")
    
    # 速率限制
    rate_limit_per_minute: int = Field(default=300, description="每分钟速率限制")
    rate_limit_ip_whitelist: str = Field(default="", description="速率限制 IP 白名单,逗号分隔")
    
    # JWT 配置
    jwt_secret: str = Field(default="", description="JWT 密钥")
    session_timeout_minutes: int = Field(default=30, description="会话超时时间(分钟)")
    
    # IP 白名单
    admin_ip_whitelist: str = Field(default="", description="管理员 IP 白名单,逗号分隔")
    admin_ip_auto_whitelist_ttl: int = Field(default=86400, description="管理员 IP 自动白名单 TTL(秒)")
    
    # 请求配置
    max_request_size_mb: int = Field(default=10, description="最大请求大小(MB)")
    enable_request_logging: bool = Field(default=True, description="启用请求日志")
    
    # 安全级别
    security_level: str = Field(default="production", description="安全级别: development/production/military")
    
    @field_validator("token_count_multiplier")
    @classmethod
    def validate_multiplier(cls, v: float) -> float:
        """验证 Token 计数倍率"""
        if v <= 0 or v > 10:
            raise ValueError("token_count_multiplier must be between 0 and 10")
        return v

    @field_validator("max_error_count")
    @classmethod
    def validate_max_error_count(cls, v: int) -> int:
        """验证最大错误次数"""
        if v < 1:
            raise ValueError("max_error_count must be >= 1")
        return v

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        """验证端口号"""
        if v < 1 or v > 65535:
            raise ValueError("port must be between 1 and 65535")
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """验证日志级别"""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(f"log_level must be one of {valid_levels}")
        return v_upper

    @field_validator("security_level")
    @classmethod
    def validate_security_level(cls, v: str) -> str:
        """验证安全级别"""
        valid_levels = ["development", "production", "military"]
        v_lower = v.lower()
        if v_lower not in valid_levels:
            raise ValueError(f"security_level must be one of {valid_levels}")
        return v_lower

    @field_validator("session_timeout_minutes")
    @classmethod
    def validate_session_timeout(cls, v: int) -> int:
        """验证会话超时"""
        if v < 1 or v > 1440:  # 1 分钟到 24 小时
            raise ValueError("session_timeout_minutes must be between 1 and 1440")
        return v

    @field_validator("rate_limit_per_minute")
    @classmethod
    def validate_rate_limit(cls, v: int) -> int:
        """验证速率限制"""
        if v < 1:
            raise ValueError("rate_limit_per_minute must be >= 1")
        return v

    @field_validator("max_request_size_mb")
    @classmethod
    def validate_max_request_size(cls, v: int) -> int:
        """验证最大请求大小"""
        if v < 1 or v > 100:
            raise ValueError("max_request_size_mb must be between 1 and 100")
        return v
    
    def get_openai_keys_list(self) -> List[str]:
        """获取 OpenAI Keys 列表"""
        if not self.openai_keys:
            return []
        return [key.strip() for key in self.openai_keys.split(",") if key.strip()]
    
    def get_cors_origins_list(self) -> List[str]:
        """获取 CORS 来源列表"""
        if not self.cors_origins:
            return []
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
    
    def get_trusted_hosts_list(self) -> List[str]:
        """获取信任的 Host 列表"""
        if not self.trusted_hosts:
            return []
        return [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]
    
    def is_development_mode(self) -> bool:
        """是否为开发模式"""
        return self.debug or self.security_level == "development" or not self.openai_keys

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # 忽略 .env 中未定义的额外字段
    )


# 全局配置实例
_settings: Optional[AppSettings] = None


def get_settings() -> AppSettings:
    """获取配置实例 (单例模式)"""
    global _settings
    if _settings is None:
        try:
            _settings = AppSettings()
        except Exception as e:
            raise RuntimeError(f"Failed to load application settings: {e}")
    return _settings


# 便捷访问
settings = get_settings()
