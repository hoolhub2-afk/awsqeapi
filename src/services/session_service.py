"""
会话粘性管理服务
"""
import hashlib
import time
from typing import Dict, Optional
from src.core.database import get_database_backend

class SessionService:
    """会话粘性管理"""

    @staticmethod
    def generate_session_key(messages: list, user_id: Optional[str] = None) -> str:
        """基于消息内容生成会话键"""
        # 使用前几条消息生成会话标识
        session_content = ""
        for i, msg in enumerate(messages[:3]):  # 只用前3条消息
            if isinstance(msg, dict):
                session_content += msg.get("content", "")
            else:
                session_content += getattr(msg, "content", "")

        if user_id:
            session_content = f"{user_id}:{session_content}"

        return hashlib.md5(session_content.encode()).hexdigest()[:16]

    @staticmethod
    async def get_session_account(session_key: str) -> Optional[str]:
        """获取会话绑定的账号ID"""
        db = get_database_backend()
        result = await db.fetchone(
            "SELECT account_id FROM session_accounts WHERE session_key = ? AND expires_at > ?",
            (session_key, int(time.time()))
        )
        return result.get("account_id") if result else None

    @staticmethod
    async def bind_session_account(session_key: str, account_id: str, ttl: int = 3600):
        """绑定会话到账号"""
        db = get_database_backend()
        expires_at = int(time.time()) + ttl

        await db.execute("""
            INSERT OR REPLACE INTO session_accounts (session_key, account_id, expires_at, created_at)
            VALUES (?, ?, ?, ?)
        """, (session_key, account_id, expires_at, int(time.time())))

    @staticmethod
    async def cleanup_expired_sessions():
        """清理过期会话"""
        db = get_database_backend()
        await db.execute(
            "DELETE FROM session_accounts WHERE expires_at <= ?",
            (int(time.time()),)
        )

    @staticmethod
    async def initialize_session_table():
        """初始化会话表"""
        db = get_database_backend()
        await db.execute("""
            CREATE TABLE IF NOT EXISTS session_accounts (
                session_key TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at INTEGER DEFAULT (strftime('%s', 'now'))
            )
        """)

        # 创建索引
        await db.execute("CREATE INDEX IF NOT EXISTS idx_session_expires ON session_accounts(expires_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_session_account ON session_accounts(account_id)")