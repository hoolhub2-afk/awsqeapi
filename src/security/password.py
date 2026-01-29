"""
密码哈希和验证工具
使用 bcrypt 进行安全的密码存储

Critical Fix: Blocker #1 - 解决管理员密码明文存储问题
"""
import os
import bcrypt
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class PasswordManager:
    """管理员密码哈希和验证"""

    _password_hash: Optional[bytes] = None
    _initialized: bool = False

    @classmethod
    def initialize(cls) -> None:
        """
        初始化密码哈希
        在应用启动时调用一次
        """
        if cls._initialized:
            return

        admin_password = os.getenv("ADMIN_PASSWORD", "").strip()

        if not admin_password:
            logger.warning("⚠️ ADMIN_PASSWORD 未设置，管理控制台将无法使用")
            cls._password_hash = None
            cls._initialized = True
            return

        # 生成 bcrypt 哈希（自动包含盐值）
        # 工作因子 12 = 2^12 次迭代（平衡安全性和性能）
        cls._password_hash = bcrypt.hashpw(
            admin_password.encode('utf-8'),
            bcrypt.gensalt(rounds=12)
        )

        cls._initialized = True
        logger.info("✅ 管理员密码哈希已初始化（bcrypt，rounds=12）")

    @classmethod
    def verify_password(cls, password: str) -> bool:
        """
        验证密码

        Args:
            password: 用户输入的明文密码

        Returns:
            bool: 密码是否匹配
        """
        if not cls._initialized:
            cls.initialize()

        if cls._password_hash is None:
            logger.error("❌ 密码哈希未初始化")
            return False

        try:
            # bcrypt.checkpw 内部使用常量时间比较，防止时序攻击
            return bcrypt.checkpw(
                password.encode('utf-8'),
                cls._password_hash
            )
        except Exception as e:
            logger.error(f"密码验证失败: {e}")
            return False

    @classmethod
    def is_configured(cls) -> bool:
        """检查密码是否已配置"""
        if not cls._initialized:
            cls.initialize()
        return cls._password_hash is not None


# 全局实例
password_manager = PasswordManager()
