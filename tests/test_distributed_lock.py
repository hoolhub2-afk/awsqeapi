"""
测试分布式文件锁
验证 Blocker #4 修复
"""

import os
import pytest
import asyncio
import time
from pathlib import Path
import tempfile
import shutil

from src.core.distributed_lock import DistributedLock


@pytest.fixture
def temp_lock_dir():
    """创建临时锁目录"""
    temp_dir = Path(tempfile.mkdtemp(prefix="test_locks_"))
    yield temp_dir
    # 清理
    try:
        shutil.rmtree(temp_dir)
    except:
        pass


@pytest.fixture
def lock_manager(temp_lock_dir):
    """创建锁管理器实例"""
    return DistributedLock(
        lock_dir=temp_lock_dir,
        timeout=5.0,
        stale_timeout=10.0
    )


class TestBasicLocking:
    """测试基本锁功能"""

    @pytest.mark.asyncio
    async def test_can_acquire_and_release_lock(self, lock_manager):
        """测试：可以获取和释放锁"""
        resource_id = "test-resource"

        async with lock_manager.acquire(resource_id):
            # 锁已获取
            pass

        # 锁已释放，应该能再次获取
        async with lock_manager.acquire(resource_id):
            pass

    @pytest.mark.asyncio
    async def test_lock_prevents_concurrent_access(self, lock_manager):
        """测试：锁防止并发访问"""
        resource_id = "test-resource"
        counter = {"value": 0}

        async def increment_with_lock():
            async with lock_manager.acquire(resource_id):
                # 读取当前值
                current = counter["value"]
                # 模拟一些处理时间
                await asyncio.sleep(0.05)
                # 写入新值
                counter["value"] = current + 1

        # 启动10个并发任务
        tasks = [increment_with_lock() for _ in range(10)]
        await asyncio.gather(*tasks)

        # 由于锁保护，计数器应该正确增加到10
        assert counter["value"] == 10

    @pytest.mark.asyncio
    async def test_lock_timeout(self, lock_manager):
        """测试：锁获取超时"""
        resource_id = "test-resource"

        # 第一个任务持有锁
        async def hold_lock_long():
            async with lock_manager.acquire(resource_id):
                await asyncio.sleep(10)  # 持有锁10秒

        # 启动第一个任务
        task1 = asyncio.create_task(hold_lock_long())

        # 等待确保锁已获取
        await asyncio.sleep(0.1)

        # 第二个任务应该超时
        with pytest.raises(TimeoutError, match="Failed to acquire lock"):
            async with lock_manager.acquire(resource_id):
                pass

        # 取消第一个任务并清理
        task1.cancel()
        try:
            await task1
        except asyncio.CancelledError:
            pass


class TestStaleLockCleanup:
    """测试过期锁清理"""

    @pytest.mark.asyncio
    async def test_detects_stale_locks(self, lock_manager, temp_lock_dir):
        """测试：检测过期的锁"""
        # 创建一个旧的锁文件
        lock_path = temp_lock_dir / "stale-resource.lock"
        lock_path.write_text(str(time.time()))

        # 修改文件时间为10分钟前
        old_time = time.time() - 600
        os.utime(lock_path, (old_time, old_time))

        # 检测是否为过期锁
        assert lock_manager._is_lock_stale(lock_path) is True

    @pytest.mark.asyncio
    async def test_cleanup_removes_stale_locks(self, lock_manager, temp_lock_dir):
        """测试：清理删除过期的锁"""
        # 创建几个过期的锁文件
        for i in range(3):
            lock_path = temp_lock_dir / f"stale-{i}.lock"
            lock_path.write_text(str(time.time()))

            # 修改为过期时间
            old_time = time.time() - 600
            os.utime(lock_path, (old_time, old_time))

        # 执行清理
        cleaned = await lock_manager.cleanup_stale_locks()

        assert cleaned == 3
        # 验证文件已删除
        remaining = list(temp_lock_dir.glob("*.lock"))
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_can_acquire_after_stale_removal(self, lock_manager, temp_lock_dir):
        """测试：清理过期锁后可以重新获取"""
        resource_id = "test-resource"
        lock_path = lock_manager._get_lock_path(resource_id)

        # 创建过期锁
        lock_path.write_text(str(time.time()))
        old_time = time.time() - 600
        os.utime(lock_path, (old_time, old_time))

        # 应该能成功获取锁（会自动清理过期锁）
        async with lock_manager.acquire(resource_id):
            pass


class TestLockStats:
    """测试锁统计"""

    @pytest.mark.asyncio
    async def test_get_lock_stats(self, lock_manager):
        """测试：获取锁统计信息"""
        # 获取一个锁
        async with lock_manager.acquire("resource-1"):
            stats = lock_manager.get_lock_stats()

            assert stats["total_locks"] == 1
            assert stats["active_locks"] >= 0
            assert "lock_dir" in stats


class TestConcurrentRefresh:
    """测试并发token刷新场景"""

    @pytest.mark.asyncio
    async def test_prevents_concurrent_token_refresh(self, lock_manager):
        """测试：防止同一账户的并发token刷新"""
        account_id = "test-account"
        refresh_count = {"value": 0}

        async def simulate_token_refresh():
            """模拟token刷新过程"""
            async with lock_manager.acquire(f"token_refresh_{account_id}"):
                # 读取token（模拟）
                current_count = refresh_count["value"]

                # 模拟刷新过程
                await asyncio.sleep(0.1)

                # 增加计数
                refresh_count["value"] = current_count + 1

        # 启动20个并发刷新请求
        tasks = [simulate_token_refresh() for _ in range(20)]
        await asyncio.gather(*tasks)

        # 由于锁保护，刷新应该按顺序执行
        assert refresh_count["value"] == 20


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
