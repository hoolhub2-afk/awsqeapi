"""
Database abstraction layer supporting SQLite, PostgreSQL, and MySQL.
Backend selection is based on DATABASE_URL environment variable:
- postgres://... or postgresql://... -> PostgreSQL
- mysql://... -> MySQL
- Not set -> SQLite (default)

优化特性:
- SQLite 连接池复用
- 完整索引支持
- 自动清理过期数据
- WAL 模式和性能优化
"""

import os
import json

# 确保 .env 已加载（在读取 DATABASE_URL 之前）
from src.core.env import env_loaded  # noqa: F401
import time
import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager

import aiosqlite

logger = logging.getLogger(__name__)

# Optional imports for other backends
try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False

try:
    import aiomysql
    HAS_AIOMYSQL = True
except ImportError:
    HAS_AIOMYSQL = False


class DatabaseBackend(ABC):
    """Abstract base class for database backends."""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize connection and ensure schema exists."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close database connections."""
        pass

    @abstractmethod
    async def execute(self, query: str, params: tuple = ()) -> int:
        """Execute a query and return affected row count."""
        pass

    @abstractmethod
    async def fetchone(self, query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        """Fetch a single row as dict."""
        pass

    @abstractmethod
    async def fetchall(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Fetch all rows as list of dicts."""
        pass

    async def fetch_all(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Backward compatibility: alias for fetchall (older code may call fetch_all)."""
        return await self.fetchall(query, params)

    async def cleanup_expired_data(self) -> Dict[str, int]:
        """Clean up expired data from various tables. Returns count of deleted rows per table."""
        return {}


class SQLiteBackend(DatabaseBackend):
    """
    SQLite database backend with connection pooling and optimizations.
    Enhanced: Blocker #2 - Added timeout protection and connection limits
    """

    def __init__(self, db_path: Path, max_connections: int = 10, timeout: float = 30.0):
        self._db_path = db_path
        self._initialized = False
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        # Critical Fix: Blocker #2 - Add connection pool limits and timeouts
        self._max_connections = max_connections
        self._timeout = timeout
        self._semaphore = asyncio.Semaphore(max_connections)
        self._active_connections = 0
        self._wait_queue_size = 0

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # 创建持久连接
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row

        # SQLite 性能优化
        # 注意: synchronous=FULL 确保数据安全，避免系统崩溃时丢失数据
        # 生产环境中账号凭证数据的完整性比性能更重要
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA synchronous=FULL;")
        await self._conn.execute("PRAGMA cache_size=-64000;")  # 64MB cache
        await self._conn.execute("PRAGMA temp_store=MEMORY;")
        await self._conn.execute("PRAGMA mmap_size=268435456;")  # 256MB mmap

        # 创建 accounts 表
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                label TEXT,
                clientId TEXT,
                clientSecret TEXT,
                refreshToken TEXT,
                accessToken TEXT,
                expires_at TEXT,
                other TEXT,
                last_refresh_time TEXT,
                last_refresh_status TEXT,
                created_at TEXT,
                updated_at TEXT,
                enabled INTEGER DEFAULT 1,
                error_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                quota_exhausted INTEGER DEFAULT 0
            )
        """)

        # accounts 表索引优化
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_enabled ON accounts(enabled)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_quota ON accounts(quota_exhausted)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_enabled_quota ON accounts(enabled, quota_exhausted)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_error_count ON accounts(error_count)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_created ON accounts(created_at)")

        # 添加 quota_exhausted 字段(如果不存在)
        try:
            await self._conn.execute("ALTER TABLE accounts ADD COLUMN quota_exhausted INTEGER DEFAULT 0")
        except Exception as exc:
            logger.debug("[DB] Migration: quota_exhausted add skipped: %s", exc)

        # 添加 expires_at 字段(如果不存在)
        try:
            await self._conn.execute("ALTER TABLE accounts ADD COLUMN expires_at TEXT")
        except Exception as exc:
            logger.debug("[DB] Migration: expires_at add skipped: %s", exc)

        # auth_sessions 表
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS auth_sessions (
                auth_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at INTEGER
            )
        """)
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_created ON auth_sessions(created_at)")

        # secure_keys 表
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS secure_keys (
                key_id TEXT PRIMARY KEY,
                key_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                encrypted_key TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                last_used TEXT,
                usage_count INTEGER DEFAULT 0,
                max_uses INTEGER,
                allowed_ips TEXT,
                allowed_user_agents TEXT,
                allowed_accounts TEXT,
                default_account_id TEXT,
                rate_limit_per_minute INTEGER DEFAULT 100,
                status TEXT DEFAULT 'active',
                security_level TEXT DEFAULT 'production',
                metadata TEXT
            )
        """)
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_secure_keys_status ON secure_keys(status)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_secure_keys_expires ON secure_keys(expires_at)")

        # 迁移：添加缺失列
        try:
            async with self._conn.execute("PRAGMA table_info(secure_keys)") as cursor:
                rows = await cursor.fetchall()
                cols = [row[1] for row in rows]
                if "encrypted_key" not in cols:
                    await self._conn.execute("ALTER TABLE secure_keys ADD COLUMN encrypted_key TEXT")
                    logger.info("[DB] Migration: Added 'encrypted_key' column to secure_keys")
                if "allowed_accounts" not in cols:
                    await self._conn.execute("ALTER TABLE secure_keys ADD COLUMN allowed_accounts TEXT")
                    logger.info("[DB] Migration: Added 'allowed_accounts' column to secure_keys")
                if "default_account_id" not in cols:
                    await self._conn.execute("ALTER TABLE secure_keys ADD COLUMN default_account_id TEXT")
                    logger.info("[DB] Migration: Added 'default_account_id' column to secure_keys")
        except Exception as e:
            logger.warning(f"[DB] Warning: secure_keys migration check: {e}")

        # audit_logs 表
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                event_type TEXT,
                client_ip TEXT,
                details TEXT,
                user_agent TEXT
            )
        """)
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_event_type ON audit_logs(event_type)")

        # quota_stats 表
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS quota_stats (
                account_id TEXT PRIMARY KEY,
                month_key TEXT NOT NULL,
                request_count INTEGER DEFAULT 0,
                throttle_count INTEGER DEFAULT 0,
                last_throttle_time INTEGER,
                quota_status TEXT DEFAULT 'normal',
                created_at INTEGER DEFAULT (strftime('%s', 'now')),
                updated_at INTEGER DEFAULT (strftime('%s', 'now'))
            )
        """)
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_quota_month ON quota_stats(month_key)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_quota_status ON quota_stats(quota_status)")

        # session_accounts 表
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS session_accounts (
                session_key TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at INTEGER DEFAULT (strftime('%s', 'now'))
            )
        """)
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_session_expires ON session_accounts(expires_at)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_session_account ON session_accounts(account_id)")

        await self._conn.commit()

        # 迁移：accounts 表添加缺失列
        try:
            async with self._conn.execute("PRAGMA table_info(accounts)") as cursor:
                rows = await cursor.fetchall()
                cols = [row[1] for row in rows]
                if "enabled" not in cols:
                    await self._conn.execute("ALTER TABLE accounts ADD COLUMN enabled INTEGER DEFAULT 1")
                    logger.info("[DB] Migration: Added 'enabled' column")
                if "error_count" not in cols:
                    await self._conn.execute("ALTER TABLE accounts ADD COLUMN error_count INTEGER DEFAULT 0")
                    logger.info("[DB] Migration: Added 'error_count' column")
                if "success_count" not in cols:
                    await self._conn.execute("ALTER TABLE accounts ADD COLUMN success_count INTEGER DEFAULT 0")
                    logger.info("[DB] Migration: Added 'success_count' column")
                if "quota_exhausted" not in cols:
                    await self._conn.execute("ALTER TABLE accounts ADD COLUMN quota_exhausted INTEGER DEFAULT 0")
                    logger.info("[DB] Migration: Added 'quota_exhausted' column")
                if "expires_at" not in cols:
                    await self._conn.execute("ALTER TABLE accounts ADD COLUMN expires_at TEXT")
                    logger.info("[DB] Migration: Added 'expires_at' column")
        except Exception as e:
            logger.warning(f"[DB] Warning: Migration failed: {e}")

        await self._conn.commit()

        # 执行一次 VACUUM 优化（仅在数据库初始化时）
        try:
            await self._conn.execute("PRAGMA optimize;")
        except Exception as exc:
            logger.debug("[DB] PRAGMA optimize skipped: %s", exc)

        self._initialized = True
        logger.info("[DB] SQLite initialized with optimizations and indexes")

    @asynccontextmanager
    async def _acquire_connection(self):
        """
        Acquire a database connection with timeout protection.
        Critical Fix: Blocker #2 - Prevent resource exhaustion
        """
        acquired = False
        self._wait_queue_size += 1

        try:
            # Try to acquire semaphore with timeout
            try:
                acquired = await asyncio.wait_for(
                    self._semaphore.acquire(),
                    timeout=self._timeout
                )
            except asyncio.TimeoutError:
                logger.error(
                    f"🔴 [DATABASE] Connection acquisition timeout after {self._timeout}s "
                    f"(active: {self._active_connections}, waiting: {self._wait_queue_size})"
                )
                raise TimeoutError(
                    f"Failed to acquire database connection within {self._timeout}s. "
                    f"Active connections: {self._active_connections}, "
                    f"Max connections: {self._max_connections}"
                )

            self._active_connections += 1
            self._wait_queue_size -= 1

            # Yield the shared connection with lock
            async with self._lock:
                yield self._conn

        finally:
            if acquired:
                self._active_connections -= 1
                self._semaphore.release()
            else:
                self._wait_queue_size -= 1

    async def close(self) -> None:
        """Close database connection with cleanup"""
        if self._conn:
            try:
                await self._conn.close()
                logger.info("[DATABASE] SQLite connection closed")
            except Exception as e:
                logger.error(f"[DATABASE] Error closing SQLite connection: {e}")
            finally:
                self._conn = None
        self._initialized = False

    async def execute(self, query: str, params: tuple = ()) -> int:
        """Execute query with timeout protection"""
        async with self._acquire_connection() as conn:
            try:
                cursor = await asyncio.wait_for(
                    conn.execute(query, params),
                    timeout=self._timeout
                )
                await conn.commit()
                return cursor.rowcount
            except asyncio.TimeoutError:
                logger.error(f"🔴 [DATABASE] Query execution timeout: {query[:100]}")
                raise TimeoutError(f"Query execution timeout after {self._timeout}s")
            except Exception as e:
                logger.error(f"🔴 [DATABASE] Query execution error: {e}", exc_info=True)
                raise

    async def fetchone(self, query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        """Fetch one row with timeout protection"""
        async with self._acquire_connection() as conn:
            try:
                async with conn.execute(query, params) as cursor:
                    row = await asyncio.wait_for(
                        cursor.fetchone(),
                        timeout=self._timeout
                    )
                    return dict(row) if row else None
            except asyncio.TimeoutError:
                logger.error(f"🔴 [DATABASE] Query timeout: {query[:100]}")
                raise TimeoutError(f"Query timeout after {self._timeout}s")

    async def fetchall(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Fetch all rows with timeout protection"""
        async with self._acquire_connection() as conn:
            try:
                async with conn.execute(query, params) as cursor:
                    rows = await asyncio.wait_for(
                        cursor.fetchall(),
                        timeout=self._timeout
                    )
                    return [dict(row) for row in rows]
            except asyncio.TimeoutError:
                logger.error(f"🔴 [DATABASE] Query timeout: {query[:100]}")
                raise TimeoutError(f"Query timeout after {self._timeout}s")

    async def cleanup_expired_data(self) -> Dict[str, int]:
        """清理过期数据 - Enhanced with timeout protection"""
        results = {}
        now = int(time.time())

        async with self._acquire_connection() as conn:
            try:
                # 清理过期的 auth_sessions（超过10分钟）
                cursor = await conn.execute(
                    "DELETE FROM auth_sessions WHERE created_at < ?",
                    (now - 600,)
                )
                results["auth_sessions"] = cursor.rowcount

                # 清理过期的 session_accounts
                cursor = await conn.execute(
                    "DELETE FROM session_accounts WHERE expires_at < ?",
                    (now,)
                )
                results["session_accounts"] = cursor.rowcount

                # 清理旧的 audit_logs（保留30天）
                thirty_days_ago = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - 30 * 24 * 3600))
                cursor = await conn.execute(
                    "DELETE FROM audit_logs WHERE timestamp < ?",
                    (thirty_days_ago,)
                )
                results["audit_logs"] = cursor.rowcount

                # 清理旧月份的 quota_stats（保留当月和上月）
                current_month = time.strftime("%Y-%m", time.gmtime())
                last_month = time.strftime("%Y-%m", time.gmtime(now - 32 * 24 * 3600))
                cursor = await conn.execute(
                    "DELETE FROM quota_stats WHERE month_key < ? AND month_key != ?",
                    (last_month, current_month)
                )
                results["quota_stats"] = cursor.rowcount

                await conn.commit()

            except Exception as e:
                logger.error(f"[DB] Cleanup error: {e}", exc_info=True)
                raise

        total = sum(results.values())
        if total > 0:
            logger.info(f"[DB] Cleanup completed: {results}")

        return results


class PostgresBackend(DatabaseBackend):
    """
    PostgreSQL database backend using asyncpg.
    Enhanced: Blocker #2 - Added timeout protection
    """

    def __init__(self, dsn: str, timeout: float = 30.0):
        self._dsn = dsn
        self._pool: "Optional[asyncpg.pool.Pool]" = None
        self._initialized = False
        self._connection_errors = 0
        self._max_connection_errors = 3
        # Critical Fix: Blocker #2 - Add timeout configuration
        self._timeout = timeout
        self._command_timeout = timeout

    async def initialize(self) -> None:
        if not HAS_ASYNCPG:
            raise ImportError("asyncpg is required for PostgreSQL support. Install with: pip install asyncpg")

        # Critical Fix: Blocker #2 - Add timeout configuration to pool
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=1,
            max_size=20,
            command_timeout=self._command_timeout,  # Query timeout
            timeout=self._timeout,  # Connection acquisition timeout
            max_inactive_connection_lifetime=300.0  # Close idle connections after 5 min
        )

        async with self._pool.acquire() as conn:
            # 检查 accounts 表是否存在
            table_exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = 'accounts'
                )
            """)

            if not table_exists:
                # 如果表不存在，先删除可能存在的同名类型（PostgreSQL 创建表时会自动创建同名复合类型）
                try:
                    await conn.execute("DROP TYPE IF EXISTS accounts CASCADE")
                except Exception as exc:
                    logger.debug("[DB] Drop type accounts skipped: %s", exc)

                await conn.execute("""
                    CREATE TABLE accounts (
                        id TEXT PRIMARY KEY,
                        label TEXT,
                        clientId TEXT,
                        clientSecret TEXT,
                        refreshToken TEXT,
                        accessToken TEXT,
                        expires_at TEXT,
                        other TEXT,
                        last_refresh_time TEXT,
                        last_refresh_status TEXT,
                        created_at TEXT,
                        updated_at TEXT,
                        enabled INTEGER DEFAULT 1,
                        error_count INTEGER DEFAULT 0,
                        success_count INTEGER DEFAULT 0,
                        quota_exhausted INTEGER DEFAULT 0
                    )
                """)
                await conn.execute("""
                    CREATE TABLE auth_sessions (
                        auth_id TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        created_at BIGINT
                    )
                """)

            # accounts 表迁移：补齐缺失列（历史版本可能缺少 quota_exhausted / expires_at）
            col_exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = 'accounts'
                    AND column_name = 'quota_exhausted'
                )
            """)
            if not col_exists:
                await conn.execute("ALTER TABLE accounts ADD COLUMN quota_exhausted INTEGER DEFAULT 0")
                logger.info("[DB] Migration: Added 'quota_exhausted' column to accounts")

            col_exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = 'accounts'
                    AND column_name = 'expires_at'
                )
            """)
            if not col_exists:
                await conn.execute("ALTER TABLE accounts ADD COLUMN expires_at TEXT")
                logger.info("[DB] Migration: Added 'expires_at' column to accounts")

            # 检查 secure_keys 表是否存在
            secure_keys_exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = 'secure_keys'
                )
            """)

            if not secure_keys_exists:
                # 如果表不存在，先删除可能存在的同名类型
                try:
                    await conn.execute("DROP TYPE IF EXISTS secure_keys CASCADE")
                except Exception as exc:
                    logger.debug("[DB] Drop type secure_keys skipped: %s", exc)

                await conn.execute("""
                    CREATE TABLE secure_keys (
                        key_id TEXT PRIMARY KEY,
                        key_hash TEXT NOT NULL,
                        salt TEXT NOT NULL,
                        encrypted_key TEXT,
                        created_at TEXT NOT NULL,
                        expires_at TEXT,
                        last_used TEXT,
                        usage_count INTEGER DEFAULT 0,
                        max_uses INTEGER,
                        allowed_ips TEXT,
                        allowed_user_agents TEXT,
                        allowed_accounts TEXT,
                        default_account_id TEXT,
                        rate_limit_per_minute INTEGER DEFAULT 100,
                        status TEXT DEFAULT 'active',
                        security_level TEXT DEFAULT 'production',
                        metadata TEXT
                    )
                """)
            else:
                # 检查并添加 encrypted_key 列
                col_exists = await conn.fetchval("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.columns
                        WHERE table_schema = 'public'
                        AND table_name = 'secure_keys'
                        AND column_name = 'encrypted_key'
                    )
                """)
                if not col_exists:
                    await conn.execute("ALTER TABLE secure_keys ADD COLUMN encrypted_key TEXT")
                    logger.info("Migration: Added 'encrypted_key' column to secure_keys")
                col_exists = await conn.fetchval("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.columns
                        WHERE table_schema = 'public'
                        AND table_name = 'secure_keys'
                        AND column_name = 'allowed_accounts'
                    )
                """)
                if not col_exists:
                    await conn.execute("ALTER TABLE secure_keys ADD COLUMN allowed_accounts TEXT")
                    logger.info("Migration: Added 'allowed_accounts' column to secure_keys")

                col_exists = await conn.fetchval("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.columns
                        WHERE table_schema = 'public'
                        AND table_name = 'secure_keys'
                        AND column_name = 'default_account_id'
                    )
                """)
                if not col_exists:
                    await conn.execute("ALTER TABLE secure_keys ADD COLUMN default_account_id TEXT")
                    logger.info("Migration: Added 'default_account_id' column to secure_keys")
            
            # Rate Limits
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS rate_limits (
                    key_id TEXT PRIMARY KEY,
                    count INTEGER DEFAULT 0,
                    reset_at DOUBLE PRECISION
                )
            """)
            # Audit Logs
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id SERIAL PRIMARY KEY,
                    timestamp TEXT,
                    event_type TEXT,
                    client_ip TEXT,
                    details TEXT,
                    user_agent TEXT
                )
            """)
            # Quota Stats
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS quota_stats (
                    account_id TEXT PRIMARY KEY,
                    month_key TEXT NOT NULL,
                    request_count INTEGER DEFAULT 0,
                    throttle_count INTEGER DEFAULT 0,
                    last_throttle_time BIGINT,
                    quota_status TEXT DEFAULT 'normal',
                    created_at BIGINT DEFAULT EXTRACT(EPOCH FROM NOW()),
                    updated_at BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_quota_month ON quota_stats(month_key)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_quota_status ON quota_stats(quota_status)")

            # Session Accounts
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS session_accounts (
                    session_key TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    expires_at BIGINT NOT NULL,
                    created_at BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_session_expires ON session_accounts(expires_at)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_session_account ON session_accounts(account_id)")
        self._initialized = True

    async def _reinit_pool(self) -> None:
        """
        Reinitialize connection pool after errors with graceful shutdown.

        Critical Fix: 添加锁保护防止并发重连竞态条件
        """
        # 确保锁存在（第一次调用时初始化）
        if not hasattr(self, '_reinit_lock'):
            self._reinit_lock = asyncio.Lock()

        async with self._reinit_lock:
            # 双重检查：锁内再次验证是否需要重连
            if self._connection_errors < self._max_connection_errors:
                logger.debug("[DB] Connection errors below threshold, skip reinit")
                return

            old_pool = self._pool
            new_pool = None

            try:
                # 创建新连接池 - with timeout configuration
                logger.info("[DB] Creating new connection pool...")
                new_pool = await asyncpg.create_pool(
                    dsn=self._dsn,
                    min_size=1,
                    max_size=20,
                    command_timeout=self._command_timeout,
                    timeout=self._timeout,
                    max_inactive_connection_lifetime=300.0
                )

                # 成功后再替换池
                self._pool = new_pool
                self._connection_errors = 0
                logger.info("[DB] ✅ PostgreSQL connection pool reinitialized successfully")

            except Exception as e:
                # 清理新池（如果创建了但后续失败）
                if new_pool:
                    try:
                        await asyncio.wait_for(new_pool.close(), timeout=5.0)
                        logger.debug("[DB] Cleaned up failed new pool")
                    except Exception as cleanup_error:
                        logger.debug(f"[DB] Error cleaning up new pool: {cleanup_error}")

                # 保持旧池不变（不恢复，因为可能已经损坏）
                logger.error(f"[DB] ❌ Failed to reinitialize pool: {e}", exc_info=True)
                raise

            # 最后关闭旧池（使用超时保护）
            if old_pool and old_pool != self._pool:
                try:
                    await asyncio.wait_for(old_pool.close(), timeout=5.0)
                    logger.debug("[DB] Old connection pool closed successfully")
                except asyncio.TimeoutError:
                    logger.warning("[DB] ⚠️ Timeout closing old pool (5s)")
                except Exception as e:
                    logger.warning(f"[DB] Error closing old pool: {e}")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
            self._initialized = False

    def _convert_placeholders(self, query: str) -> str:
        """Convert ? placeholders to $1, $2, etc."""
        result = []
        param_num = 0
        i = 0
        while i < len(query):
            if query[i] == '?':
                param_num += 1
                result.append(f'${param_num}')
            else:
                result.append(query[i])
            i += 1
        return ''.join(result)

    async def execute(self, query: str, params: tuple = ()) -> int:
        pg_query = self._convert_placeholders(query)
        try:
            async with self._pool.acquire() as conn:
                # 连接健康检查
                try:
                    await conn.fetchval("SELECT 1")
                except Exception as exc:
                    logger.warning("[DB] Connection health check failed, reconnecting: %s", exc)
                    raise
                
                result = await conn.execute(pg_query, *params)
                # 成功后重置错误计数
                self._connection_errors = 0
                # asyncpg returns string like "UPDATE 1"
                try:
                    return int(result.split()[-1])
                except (ValueError, IndexError):
                    return 0
        except Exception as e:
            self._connection_errors += 1
            if self._connection_errors >= self._max_connection_errors:
                logger.error(f"[DB] Critical: Too many connection errors ({self._connection_errors}), reinitializing pool")
                await self._reinit_pool()
            raise

    async def fetchone(self, query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        pg_query = self._convert_placeholders(query)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(pg_query, *params)
            return dict(row) if row else None

    async def fetchall(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        pg_query = self._convert_placeholders(query)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(pg_query, *params)
            return [dict(row) for row in rows]

    async def cleanup_expired_data(self) -> Dict[str, int]:
        """清理过期数据"""
        results = {}
        now = int(time.time())

        async with self._pool.acquire() as conn:
            # 清理过期的 auth_sessions（超过10分钟）
            result = await conn.execute(
                "DELETE FROM auth_sessions WHERE created_at < $1",
                now - 600
            )
            results["auth_sessions"] = int(result.split()[-1]) if result else 0

            # 清理过期的 session_accounts
            result = await conn.execute(
                "DELETE FROM session_accounts WHERE expires_at < $1",
                now
            )
            results["session_accounts"] = int(result.split()[-1]) if result else 0

            # 清理旧的 audit_logs（保留30天）
            thirty_days_ago = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - 30 * 24 * 3600))
            result = await conn.execute(
                "DELETE FROM audit_logs WHERE timestamp < $1",
                thirty_days_ago
            )
            results["audit_logs"] = int(result.split()[-1]) if result else 0

            # 清理旧月份的 quota_stats（保留当月和上月）
            current_month = time.strftime("%Y-%m", time.gmtime())
            last_month = time.strftime("%Y-%m", time.gmtime(now - 32 * 24 * 3600))
            result = await conn.execute(
                "DELETE FROM quota_stats WHERE month_key < $1 AND month_key != $2",
                last_month, current_month
            )
            results["quota_stats"] = int(result.split()[-1]) if result else 0

        total = sum(results.values())
        if total > 0:
            logger.info(f"[DB] PostgreSQL cleanup completed: {results}")

        return results


class MySQLBackend(DatabaseBackend):
    """
    MySQL database backend using aiomysql.
    Enhanced: Blocker #2 - Added timeout protection
    """

    def __init__(self, dsn: str, timeout: float = 30.0):
        self._dsn = dsn
        self._pool = None
        self._initialized = False
        self._config = self._parse_dsn(dsn)
        # Critical Fix: Blocker #2 - Add timeout configuration
        self._timeout = timeout

    def _parse_dsn(self, dsn: str) -> Dict[str, Any]:
        """Parse MySQL DSN into connection parameters."""
        # mysql://user:password@host:port/database
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(dsn)
        config = {
            'host': parsed.hostname or 'localhost',
            'port': parsed.port or 3306,
            'user': parsed.username or 'root',
            'password': parsed.password or '',
            'db': parsed.path.lstrip('/') if parsed.path else 'test',
        }
        # Handle SSL
        query = parse_qs(parsed.query)
        if 'ssl' in query or 'sslmode' in query or 'ssl-mode' in query:
            config['ssl'] = True
        return config

    async def initialize(self) -> None:
        if not HAS_AIOMYSQL:
            raise ImportError("aiomysql is required for MySQL support. Install with: pip install aiomysql")

        # Critical Fix: Blocker #2 - Add timeout configuration to pool
        self._pool = await aiomysql.create_pool(
            host=self._config['host'],
            port=self._config['port'],
            user=self._config['user'],
            password=self._config['password'],
            db=self._config['db'],
            minsize=1,
            maxsize=20,
            autocommit=True,
            connect_timeout=self._timeout,  # Connection timeout
            pool_recycle=300  # Recycle connections after 5 minutes
        )

        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS accounts (
                        id VARCHAR(255) PRIMARY KEY,
                        label TEXT,
                        clientId TEXT,
                        clientSecret TEXT,
                        refreshToken TEXT,
                        accessToken TEXT,
                        expires_at TEXT,
                        other TEXT,
                        last_refresh_time TEXT,
                        last_refresh_status TEXT,
                        created_at TEXT,
                        updated_at TEXT,
                        enabled INT DEFAULT 1,
                        error_count INT DEFAULT 0,
                        success_count INT DEFAULT 0,
                        quota_exhausted INT DEFAULT 0
                    )
                """)

                try:
                    await cur.execute("ALTER TABLE accounts ADD COLUMN quota_exhausted INT DEFAULT 0")
                except Exception as exc:
                    logger.debug("[DB] MySQL add quota_exhausted skipped: %s", exc)
                try:
                    await cur.execute("ALTER TABLE accounts ADD COLUMN expires_at TEXT")
                except Exception as exc:
                    logger.debug("[DB] MySQL add expires_at skipped: %s", exc)
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS auth_sessions (
                        auth_id VARCHAR(255) PRIMARY KEY,
                        payload TEXT NOT NULL,
                        created_at BIGINT
                    )
                """)
                # 创建安全密钥表
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS secure_keys (
                        key_id VARCHAR(255) PRIMARY KEY,
                        key_hash TEXT NOT NULL,
                        salt TEXT NOT NULL,
                        encrypted_key TEXT,
                        created_at TEXT NOT NULL,
                        expires_at TEXT,
                        last_used TEXT,
                        usage_count INT DEFAULT 0,
                        max_uses INT,
                        allowed_ips TEXT,
                        allowed_user_agents TEXT,
                        allowed_accounts TEXT,
                        default_account_id TEXT,
                        rate_limit_per_minute INT DEFAULT 100,
                        status VARCHAR(50) DEFAULT 'active',
                        security_level VARCHAR(50) DEFAULT 'production',
                        metadata TEXT
                    )
                """)
                try:
                    await cur.execute("ALTER TABLE secure_keys ADD COLUMN allowed_accounts TEXT")
                except Exception as exc:
                    logger.debug("[DB] MySQL add allowed_accounts skipped: %s", exc)
                try:
                    await cur.execute("ALTER TABLE secure_keys ADD COLUMN default_account_id TEXT")
                except Exception as exc:
                    logger.debug("[DB] MySQL add default_account_id skipped: %s", exc)
                # Rate Limits table
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS rate_limits (
                        key_id VARCHAR(255) PRIMARY KEY,
                        count INT DEFAULT 0,
                        reset_at FLOAT
                    )
                """)
                # Audit Logs table
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS audit_logs (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        timestamp VARCHAR(50),
                        event_type VARCHAR(50),
                        client_ip VARCHAR(50),
                        details TEXT,
                        user_agent TEXT
                    )
                """)
                # Quota Stats table
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS quota_stats (
                        account_id VARCHAR(255) PRIMARY KEY,
                        month_key VARCHAR(10) NOT NULL,
                        request_count INT DEFAULT 0,
                        throttle_count INT DEFAULT 0,
                        last_throttle_time BIGINT,
                        quota_status VARCHAR(20) DEFAULT 'normal',
                        created_at BIGINT,
                        updated_at BIGINT,
                        INDEX idx_quota_month (month_key),
                        INDEX idx_quota_status (quota_status)
                    )
                """)
                # Session Accounts table
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS session_accounts (
                        session_key VARCHAR(255) PRIMARY KEY,
                        account_id VARCHAR(255) NOT NULL,
                        expires_at BIGINT NOT NULL,
                        created_at BIGINT,
                        INDEX idx_session_expires (expires_at),
                        INDEX idx_session_account (account_id)
                    )
                """)
        self._initialized = True

    async def close(self) -> None:
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
            self._initialized = False

    def _convert_placeholders(self, query: str) -> str:
        """Convert ? placeholders to %s for MySQL."""
        return query.replace('?', '%s')

    async def execute(self, query: str, params: tuple = ()) -> int:
        mysql_query = self._convert_placeholders(query)
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(mysql_query, params)
                return cur.rowcount

    async def fetchone(self, query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        mysql_query = self._convert_placeholders(query)
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(mysql_query, params)
                return await cur.fetchone()

    async def fetchall(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        mysql_query = self._convert_placeholders(query)
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(mysql_query, params)
                return await cur.fetchall()

    async def cleanup_expired_data(self) -> Dict[str, int]:
        """清理过期数据"""
        results = {}
        now = int(time.time())

        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 清理过期的 auth_sessions（超过10分钟）
                await cur.execute(
                    "DELETE FROM auth_sessions WHERE created_at < %s",
                    (now - 600,)
                )
                results["auth_sessions"] = cur.rowcount

                # 清理过期的 session_accounts
                await cur.execute(
                    "DELETE FROM session_accounts WHERE expires_at < %s",
                    (now,)
                )
                results["session_accounts"] = cur.rowcount

                # 清理旧的 audit_logs（保留30天）
                thirty_days_ago = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - 30 * 24 * 3600))
                await cur.execute(
                    "DELETE FROM audit_logs WHERE timestamp < %s",
                    (thirty_days_ago,)
                )
                results["audit_logs"] = cur.rowcount

                # 清理旧月份的 quota_stats（保留当月和上月）
                current_month = time.strftime("%Y-%m", time.gmtime())
                last_month = time.strftime("%Y-%m", time.gmtime(now - 32 * 24 * 3600))
                await cur.execute(
                    "DELETE FROM quota_stats WHERE month_key < %s AND month_key != %s",
                    (last_month, current_month)
                )
                results["quota_stats"] = cur.rowcount

        total = sum(results.values())
        if total > 0:
            logger.info(f"[DB] MySQL cleanup completed: {results}")

        return results


# Global database instance
_db: Optional[DatabaseBackend] = None


def get_database_backend() -> DatabaseBackend:
    """
    Get the configured database backend based on DATABASE_URL.
    Enhanced: Blocker #2 - All backends now have timeout protection
    Note: This returns the global instance. Connection validity is checked on use.
    """
    global _db
    if _db is not None:
        return _db

    # Get configurable timeout from environment (default 30s)
    db_timeout = float(os.getenv('DATABASE_TIMEOUT', '30.0'))
    database_url = os.getenv('DATABASE_URL', '').strip()

    if database_url.startswith(('postgres://', 'postgresql://')):
        # Fix common postgres:// to postgresql:// for asyncpg
        dsn = database_url.replace('postgres://', 'postgresql://', 1) if database_url.startswith('postgres://') else database_url
        _db = PostgresBackend(dsn, timeout=db_timeout)
        logger.info(f"Using PostgreSQL backend with {db_timeout}s timeout")
    elif database_url.startswith('mysql://'):
        _db = MySQLBackend(database_url, timeout=db_timeout)
        logger.info(f"Using MySQL backend with {db_timeout}s timeout")
    else:
        # Default to SQLite
        base_dir = Path(__file__).resolve().parent.parent.parent  # 回到项目根目录
        db_path = base_dir / "data" / "database" / "data.sqlite3"
        # Get connection pool settings from environment
        max_connections = int(os.getenv('SQLITE_MAX_CONNECTIONS', '10'))
        _db = SQLiteBackend(db_path, max_connections=max_connections, timeout=db_timeout)
        # 使用相对路径显示，避免泄露完整路径
        relative_path = db_path.relative_to(Path.cwd()) if db_path.is_absolute() else db_path
        logger.info(f"Using SQLite backend: {relative_path} (max_conn={max_connections}, timeout={db_timeout}s)")

    return _db


async def init_db() -> DatabaseBackend:
    """Initialize and return the database backend."""
    db = get_database_backend()
    await db.initialize()
    return db


async def close_db() -> None:
    """Close the database backend."""
    global _db
    if _db:
        await _db.close()
        _db = None


# Helper functions for common operations
def row_to_dict(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert a database row to dict with JSON parsing for 'other' field."""
    if row is None:
        return None
    d = dict(row)
    if d.get("other"):
        try:
            other = json.loads(d["other"])
            # Validate type - must be dict
            if not isinstance(other, dict):
                logger.warning(f"Invalid 'other' field type: {type(other)}, expected dict")
                d["other"] = {}
            else:
                d["other"] = other
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse 'other' field: {e}")
            d["other"] = {}
        except Exception as e:
            logger.warning(f"Unexpected error parsing 'other' field: {e}")
            d["other"] = {}
    # normalize enabled to bool
    if "enabled" in d and d["enabled"] is not None:
        try:
            d["enabled"] = bool(int(d["enabled"]))
        except (TypeError, ValueError):
            d["enabled"] = bool(d["enabled"])
    return d
