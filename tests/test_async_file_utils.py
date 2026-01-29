"""
测试异步文件工具
验证 Major #8 修复
"""

import pytest
import json
import tempfile
import asyncio
import time
import sys
from pathlib import Path
import shutil

from src.core.async_file_utils import AsyncFileManager


@pytest.fixture
def temp_dir():
    """创建临时目录"""
    temp_path = Path(tempfile.mkdtemp(prefix="test_files_"))
    yield temp_path
    # 清理
    try:
        shutil.rmtree(temp_path)
    except:
        pass


class TestJSONOperations:
    """测试JSON文件操作"""

    @pytest.mark.asyncio
    async def test_write_and_read_json(self, temp_dir):
        """测试：写入和读取JSON文件"""
        file_path = temp_dir / "test.json"
        test_data = {
            "key1": "value1",
            "key2": 123,
            "key3": ["a", "b", "c"]
        }

        # 写入
        await AsyncFileManager.write_json(file_path, test_data)

        # 读取
        loaded_data = await AsyncFileManager.read_json(file_path)

        assert loaded_data == test_data

    @pytest.mark.asyncio
    async def test_atomic_write_json(self, temp_dir):
        """测试：原子性写入JSON（即使崩溃也不损坏原文件）"""
        file_path = temp_dir / "atomic_test.json"

        # 初始写入
        initial_data = {"version": 1, "data": "initial"}
        await AsyncFileManager.write_json(file_path, initial_data, atomic=True)

        # 验证文件存在且内容正确
        assert file_path.exists()
        loaded = await AsyncFileManager.read_json(file_path)
        assert loaded == initial_data

        # 更新数据
        updated_data = {"version": 2, "data": "updated"}
        await AsyncFileManager.write_json(file_path, updated_data, atomic=True)

        # 验证更新成功
        loaded = await AsyncFileManager.read_json(file_path)
        assert loaded == updated_data

        # 验证没有遗留临时文件
        temp_files = list(temp_dir.glob("*.tmp"))
        assert len(temp_files) == 0

    @pytest.mark.asyncio
    async def test_read_nonexistent_json_returns_empty_dict(self, temp_dir):
        """测试：读取不存在的JSON文件返回空字典"""
        file_path = temp_dir / "nonexistent.json"

        data = await AsyncFileManager.read_json(file_path)
        assert data == {}


class TestTextOperations:
    """测试文本文件操作"""

    @pytest.mark.asyncio
    async def test_write_and_read_text(self, temp_dir):
        """测试：写入和读取文本文件"""
        file_path = temp_dir / "test.txt"
        test_content = "Hello, World!\n这是测试内容。"

        # 写入
        await AsyncFileManager.write_text(file_path, test_content)

        # 读取
        loaded_content = await AsyncFileManager.read_text(file_path)

        assert loaded_content == test_content

    @pytest.mark.asyncio
    async def test_atomic_write_text(self, temp_dir):
        """测试：原子性写入文本"""
        file_path = temp_dir / "atomic.txt"

        # 写入
        await AsyncFileManager.write_text(file_path, "Initial content", atomic=True)

        # 更新
        await AsyncFileManager.write_text(file_path, "Updated content", atomic=True)

        # 验证
        content = await AsyncFileManager.read_text(file_path)
        assert content == "Updated content"

        # 验证没有遗留临时文件
        temp_files = list(temp_dir.glob("*.tmp"))
        assert len(temp_files) == 0


class TestFileChecks:
    """测试文件检查功能"""

    @pytest.mark.asyncio
    async def test_exists_check(self, temp_dir):
        """测试：文件存在性检查"""
        existing_file = temp_dir / "exists.txt"
        nonexistent_file = temp_dir / "not_exists.txt"

        # 创建文件
        await AsyncFileManager.write_text(existing_file, "content")

        # 检查
        assert await AsyncFileManager.exists(existing_file) is True
        assert await AsyncFileManager.exists(nonexistent_file) is False

    @pytest.mark.asyncio
    async def test_get_file_size(self, temp_dir):
        """测试：获取文件大小"""
        file_path = temp_dir / "size_test.txt"
        content = "a" * 1000  # 1000字节

        await AsyncFileManager.write_text(file_path, content)

        size = await AsyncFileManager.get_file_size(file_path)
        assert size == 1000

    @pytest.mark.asyncio
    async def test_get_nonexistent_file_size_returns_none(self, temp_dir):
        """测试：获取不存在文件的大小返回None"""
        file_path = temp_dir / "nonexistent.txt"

        size = await AsyncFileManager.get_file_size(file_path)
        assert size is None


class TestFileRemoval:
    """测试文件删除"""

    @pytest.mark.asyncio
    async def test_remove_file(self, temp_dir):
        """测试：删除文件"""
        file_path = temp_dir / "to_remove.txt"

        # 创建文件
        await AsyncFileManager.write_text(file_path, "content")
        assert file_path.exists()

        # 删除
        await AsyncFileManager.remove(file_path)
        assert not file_path.exists()

    @pytest.mark.asyncio
    async def test_remove_nonexistent_file_with_missing_ok(self, temp_dir):
        """测试：删除不存在的文件（missing_ok=True）"""
        file_path = temp_dir / "nonexistent.txt"

        # 不应该抛出异常
        await AsyncFileManager.remove(file_path, missing_ok=True)

    @pytest.mark.asyncio
    async def test_remove_nonexistent_file_raises(self, temp_dir):
        """测试：删除不存在的文件（missing_ok=False）抛出异常"""
        file_path = temp_dir / "nonexistent.txt"

        with pytest.raises(FileNotFoundError):
            await AsyncFileManager.remove(file_path, missing_ok=False)


class TestConcurrentFileOperations:
    """测试并发文件操作"""

    @pytest.mark.asyncio
    async def test_concurrent_reads(self, temp_dir):
        """测试：并发读取同一文件"""
        file_path = temp_dir / "concurrent_read.json"
        test_data = {"value": 42}

        # 写入
        await AsyncFileManager.write_json(file_path, test_data)

        # 并发读取
        tasks = [AsyncFileManager.read_json(file_path) for _ in range(20)]
        results = await asyncio.gather(*tasks)

        # 所有读取应该返回相同数据
        assert all(r == test_data for r in results)

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows has file locking limitations in concurrent atomic writes"
    )
    async def test_concurrent_writes_with_atomic(self, temp_dir):
        """测试：并发原子写入不损坏文件（Unix/Linux/macOS）"""
        file_path = temp_dir / "concurrent_write.json"

        # 并发写入不同数据
        async def write_data(value):
            await AsyncFileManager.write_json(
                file_path,
                {"value": value, "timestamp": time.time()},
                atomic=True
            )

        # 启动10个并发写入
        await asyncio.gather(*[write_data(i) for i in range(10)])

        # 文件应该包含有效JSON（不损坏）
        data = await AsyncFileManager.read_json(file_path)
        assert "value" in data
        assert "timestamp" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
