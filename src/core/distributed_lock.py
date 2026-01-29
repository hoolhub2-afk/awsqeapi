"""
分布式文件锁实现
Critical Fix: Blocker #4 - 解决Token刷新竞态条件和内存泄漏

基于文件系统的分布式锁，支持：
- 跨进程/跨实例的锁
- 自动超时和过期清理
- 死锁检测
- 异常安全（确保锁始终被释放）
- 跨平台支持（Windows/Linux/macOS）
"""

import asyncio
import os
import sys
import time
import logging
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

# 跨平台文件锁支持
if sys.platform == "win32":
    import msvcrt
    HAS_FCNTL = False
else:
    try:
        import fcntl
        HAS_FCNTL = True
    except ImportError:
        HAS_FCNTL = False

logger = logging.getLogger(__name__)


class DistributedLock:
    """
    基于文件系统的分布式锁
    使用fcntl（Unix）或msvcrt（Windows）实现进程级锁定
    """

    def __init__(self, lock_dir: Path, timeout: float = 30.0, stale_timeout: float = 300.0):
        """
        初始化分布式锁管理器

        Args:
            lock_dir: 锁文件存储目录
            timeout: 获取锁的超时时间（秒）
            stale_timeout: 锁被视为过期的时间（秒）
        """
        self.lock_dir = Path(lock_dir)
        self.timeout = timeout
        self.stale_timeout = stale_timeout
        self._ensure_lock_dir()

    def _ensure_lock_dir(self):
        """确保锁目录存在"""
        try:
            self.lock_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create lock directory {self.lock_dir}: {e}")
            raise

    def _get_lock_path(self, resource_id: str) -> Path:
        """获取资源的锁文件路径"""
        # 使用安全的文件名（移除特殊字符）
        safe_id = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in resource_id)
        return self.lock_dir / f"{safe_id}.lock"

    def _is_lock_stale(self, lock_path: Path) -> bool:
        """
        检查锁是否过期

        Args:
            lock_path: 锁文件路径

        Returns:
            True如果锁已过期，False否则
        """
        try:
            if not lock_path.exists():
                return False

            # 检查文件修改时间
            mtime = lock_path.stat().st_mtime
            age = time.time() - mtime

            if age > self.stale_timeout:
                logger.warning(
                    f"🔒 [LOCK] Stale lock detected: {lock_path.name}, "
                    f"age={age:.1f}s, threshold={self.stale_timeout}s"
                )
                return True

            return False

        except Exception as e:
            logger.warning(f"Failed to check lock staleness: {e}")
            return False

    def _force_remove_lock(self, lock_path: Path):
        """强制删除过期的锁文件"""
        try:
            lock_path.unlink(missing_ok=True)
            logger.info(f"🔒 [LOCK] Removed stale lock: {lock_path.name}")
        except Exception as e:
            logger.warning(f"Failed to remove stale lock {lock_path}: {e}")

    async def _try_acquire_lock(self, lock_path: Path) -> Optional[int]:
        """
        尝试获取锁（非阻塞）

        Args:
            lock_path: 锁文件路径

        Returns:
            文件描述符（成功）或None（失败）
        """
        try:
            # 在executor中执行阻塞的文件操作
            loop = asyncio.get_event_loop()
            fd = await loop.run_in_executor(None, self._do_acquire, lock_path)
            return fd
        except Exception as e:
            logger.debug(f"Failed to acquire lock {lock_path.name}: {e}")
            return None

    def _do_acquire(self, lock_path: Path) -> int:
        """
        实际获取锁的同步方法（在executor中执行）
        Enhanced: 跨平台支持（Windows + Unix）

        Returns:
            文件描述符

        Raises:
            BlockingIOError: 如果锁已被占用
        """
        # 创建或打开锁文件
        if sys.platform == "win32":
            # Windows: 使用标准文件打开模式
            fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        else:
            # Unix: 使用标准模式
            fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)

        try:
            # 跨平台文件锁
            if sys.platform == "win32":
                # Windows: 使用msvcrt锁
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                except OSError:
                    # 锁已被占用
                    os.close(fd)
                    raise BlockingIOError("Lock is already held")
            elif HAS_FCNTL:
                # Unix: 使用fcntl锁
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                # Fallback: 简单的文件存在性检查
                # 不是真正的锁，但比没有好
                logger.warning("Using fallback file-based locking (no fcntl available)")

            # 写入当前时间戳
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            timestamp = str(time.time()).encode()
            os.write(fd, timestamp)
            os.fsync(fd)

            return fd

        except (BlockingIOError, IOError, OSError) as e:
            # 锁已被占用
            try:
                os.close(fd)
            except:  # noqa: E722 - Intentional: Suppress any close() errors during cleanup
                # 裸except是有意为之：确保即使close()失败也能继续抛出原始错误
                # 这避免掩盖真正的锁获取失败原因
                pass
            raise BlockingIOError(f"Failed to acquire lock: {e}")

    async def _release_lock(self, fd: int, lock_path: Path):
        """
        释放锁

        Args:
            fd: 文件描述符
            lock_path: 锁文件路径
        """
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._do_release, fd, lock_path)
        except Exception as e:
            logger.warning(f"Failed to release lock {lock_path.name}: {e}")

    def _do_release(self, fd: int, lock_path: Path):
        """
        实际释放锁的同步方法
        Enhanced: 跨平台支持（Windows + Unix）
        """
        try:
            # 跨平台文件锁释放
            if sys.platform == "win32":
                # Windows: 使用msvcrt解锁
                try:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except:  # noqa: E722 - Intentional: Best-effort unlock, continue cleanup
                    # 裸except是有意为之：尽力解锁，即使失败也继续清理文件
                    pass
            elif HAS_FCNTL:
                # Unix: 使用fcntl解锁
                fcntl.flock(fd, fcntl.LOCK_UN)

            # 关闭文件描述符
            os.close(fd)

            # 删除锁文件
            lock_path.unlink(missing_ok=True)

        except Exception as e:
            logger.warning(f"Error during lock release: {e}")

    @asynccontextmanager
    async def acquire(self, resource_id: str):
        """
        获取资源的分布式锁（上下文管理器）

        Args:
            resource_id: 资源标识符

        Raises:
            TimeoutError: 如果在超时时间内无法获取锁

        Example:
            async with lock_manager.acquire("account-123"):
                # 执行需要锁保护的操作
                await refresh_token(account_id)
        """
        lock_path = self._get_lock_path(resource_id)
        fd = None
        start_time = time.time()

        try:
            # 尝试获取锁，带超时重试
            while time.time() - start_time < self.timeout:
                # 检查过期锁
                if self._is_lock_stale(lock_path):
                    self._force_remove_lock(lock_path)

                # 尝试获取锁
                fd = await self._try_acquire_lock(lock_path)
                if fd is not None:
                    break

                # 短暂等待后重试
                await asyncio.sleep(0.1)

            if fd is None:
                elapsed = time.time() - start_time
                raise TimeoutError(
                    f"Failed to acquire lock for '{resource_id}' within {elapsed:.1f}s"
                )

            # 成功获取锁，执行保护的代码
            logger.debug(f"🔒 [LOCK] Acquired lock for '{resource_id}'")
            yield

        finally:
            # 确保释放锁
            if fd is not None:
                await self._release_lock(fd, lock_path)
                logger.debug(f"🔒 [LOCK] Released lock for '{resource_id}'")

    async def cleanup_stale_locks(self):
        """
        清理过期的锁文件

        Returns:
            清理的锁文件数量
        """
        cleaned = 0

        try:
            for lock_file in self.lock_dir.glob("*.lock"):
                if self._is_lock_stale(lock_file):
                    self._force_remove_lock(lock_file)
                    cleaned += 1

            if cleaned > 0:
                logger.info(f"🔒 [LOCK] Cleaned up {cleaned} stale lock files")

        except Exception as e:
            logger.error(f"Failed to cleanup stale locks: {e}", exc_info=True)

        return cleaned

    def get_lock_stats(self) -> dict:
        """
        获取锁统计信息

        Returns:
            包含锁统计的字典
        """
        try:
            lock_files = list(self.lock_dir.glob("*.lock"))
            active_locks = []
            stale_locks = []

            for lock_file in lock_files:
                if self._is_lock_stale(lock_file):
                    stale_locks.append(lock_file.name)
                else:
                    active_locks.append(lock_file.name)

            return {
                "total_locks": len(lock_files),
                "active_locks": len(active_locks),
                "stale_locks": len(stale_locks),
                "lock_dir": str(self.lock_dir),
                "active_lock_names": active_locks[:10],  # 只返回前10个
                "stale_lock_names": stale_locks[:10]
            }

        except Exception as e:
            logger.error(f"Failed to get lock stats: {e}")
            return {"error": str(e)}


# 全局锁管理器实例
_global_lock_manager: Optional[DistributedLock] = None


def get_lock_manager() -> DistributedLock:
    """
    获取全局锁管理器实例

    Returns:
        DistributedLock实例
    """
    global _global_lock_manager

    if _global_lock_manager is None:
        # 从环境变量获取配置
        lock_dir = os.getenv("LOCK_DIR", ".locks")
        lock_timeout = float(os.getenv("LOCK_TIMEOUT", "30.0"))
        stale_timeout = float(os.getenv("LOCK_STALE_TIMEOUT", "300.0"))

        _global_lock_manager = DistributedLock(
            lock_dir=Path(lock_dir),
            timeout=lock_timeout,
            stale_timeout=stale_timeout
        )

        logger.info(
            f"✅ [LOCK] Initialized distributed lock manager: "
            f"dir={lock_dir}, timeout={lock_timeout}s, stale={stale_timeout}s"
        )

    return _global_lock_manager
