"""
异步文件操作工具
Major Fix #8: 转换为异步文件I/O，提升并发性能

提供异步文件读写操作，避免阻塞事件循环
"""

import aiofiles
import aiofiles.os
import json
import logging
import os
import sys
import asyncio
from pathlib import Path
from typing import Any, Dict, Optional
import tempfile

logger = logging.getLogger(__name__)


class AsyncFileManager:
    """
    异步文件管理器
    Major Fix #8: 提供非阻塞的文件操作

    参考AIClient-2-API的文件操作模式：
    - 异步读写避免阻塞事件循环
    - 原子性写入（临时文件+重命名）
    - 适当的错误处理和日志记录
    """

    @staticmethod
    async def read_json(file_path: Path) -> Dict[str, Any]:
        """
        异步读取JSON文件

        Args:
            file_path: 文件路径

        Returns:
            解析后的JSON数据

        Raises:
            FileNotFoundError: 文件不存在
            json.JSONDecodeError: JSON格式错误
        """
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                return json.loads(content)
        except FileNotFoundError:
            logger.debug(f"File not found: {file_path}")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {file_path}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error reading JSON file {file_path}: {e}")
            raise

    @staticmethod
    async def write_json(
        file_path: Path,
        data: Dict[str, Any],
        atomic: bool = True,
        indent: int = 2
    ):
        """
        异步写入JSON文件

        Args:
            file_path: 目标文件路径
            data: 要写入的数据
            atomic: 是否使用原子写入（默认True，推荐）
            indent: JSON缩进（默认2）

        原子写入：先写临时文件，然后原子性重命名
        这确保即使写入过程中崩溃，也不会损坏原文件
        """
        content = json.dumps(data, indent=indent, ensure_ascii=False)

        if atomic:
            # 原子写入：写到临时文件，然后重命名
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # 在同一目录创建临时文件
            temp_fd, temp_path_str = tempfile.mkstemp(
                dir=file_path.parent,
                prefix=f".{file_path.name}.",
                suffix=".tmp",
                text=False
            )
            # 立即关闭文件描述符（Windows需要）
            os.close(temp_fd)
            temp_path = Path(temp_path_str)

            try:
                # 写入临时文件
                async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
                    await f.write(content)

                # 原子性重命名（跨平台兼容）
                if sys.platform == "win32":
                    # Windows: 使用同步os.replace在executor中执行
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, os.replace, str(temp_path), str(file_path))
                else:
                    # Unix: 使用异步rename
                    await aiofiles.os.replace(temp_path, file_path)

                logger.debug(f"Atomically wrote JSON to {file_path}")

            except Exception as e:
                # 清理临时文件
                try:
                    if sys.platform == "win32":
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, lambda: temp_path.unlink(missing_ok=True))
                    else:
                        await aiofiles.os.unlink(temp_path)
                except:
                    pass
                logger.error(f"Error writing JSON file {file_path}: {e}")
                raise

        else:
            # 直接写入（不推荐）
            file_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                await f.write(content)
            logger.debug(f"Wrote JSON to {file_path}")

    @staticmethod
    async def read_text(file_path: Path) -> str:
        """
        异步读取文本文件

        Args:
            file_path: 文件路径

        Returns:
            文件内容

        Raises:
            FileNotFoundError: 文件不存在
        """
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                return await f.read()
        except FileNotFoundError:
            logger.debug(f"File not found: {file_path}")
            raise
        except Exception as e:
            logger.error(f"Error reading text file {file_path}: {e}")
            raise

    @staticmethod
    async def write_text(file_path: Path, content: str, atomic: bool = True):
        """
        异步写入文本文件

        Args:
            file_path: 目标文件路径
            content: 要写入的内容
            atomic: 是否使用原子写入（默认True）
        """
        if atomic:
            file_path.parent.mkdir(parents=True, exist_ok=True)

            temp_fd, temp_path_str = tempfile.mkstemp(
                dir=file_path.parent,
                prefix=f".{file_path.name}.",
                suffix=".tmp",
                text=False
            )
            # 立即关闭文件描述符（Windows需要）
            os.close(temp_fd)
            temp_path = Path(temp_path_str)

            try:
                async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
                    await f.write(content)

                # 原子性重命名（跨平台兼容）
                if sys.platform == "win32":
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, os.replace, str(temp_path), str(file_path))
                else:
                    await aiofiles.os.replace(temp_path, file_path)

                logger.debug(f"Atomically wrote text to {file_path}")

            except Exception as e:
                try:
                    if sys.platform == "win32":
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, lambda: temp_path.unlink(missing_ok=True))
                    else:
                        await aiofiles.os.unlink(temp_path)
                except:
                    pass
                logger.error(f"Error writing text file {file_path}: {e}")
                raise

        else:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                await f.write(content)

    @staticmethod
    async def exists(file_path: Path) -> bool:
        """
        异步检查文件是否存在

        Args:
            file_path: 文件路径

        Returns:
            True如果文件存在，False否则
        """
        try:
            await aiofiles.os.stat(file_path)
            return True
        except FileNotFoundError:
            return False
        except Exception as e:
            logger.warning(f"Error checking file existence {file_path}: {e}")
            return False

    @staticmethod
    async def remove(file_path: Path, missing_ok: bool = True):
        """
        异步删除文件

        Args:
            file_path: 文件路径
            missing_ok: 如果文件不存在是否忽略错误（默认True）
        """
        try:
            await aiofiles.os.unlink(file_path)
            logger.debug(f"Removed file: {file_path}")
        except FileNotFoundError:
            if not missing_ok:
                raise
        except Exception as e:
            logger.error(f"Error removing file {file_path}: {e}")
            raise

    @staticmethod
    async def get_file_size(file_path: Path) -> Optional[int]:
        """
        异步获取文件大小

        Args:
            file_path: 文件路径

        Returns:
            文件大小（字节），None如果文件不存在
        """
        try:
            stat = await aiofiles.os.stat(file_path)
            return stat.st_size
        except FileNotFoundError:
            return None
        except Exception as e:
            logger.warning(f"Error getting file size {file_path}: {e}")
            return None

    @staticmethod
    async def list_files(directory: Path, pattern: str = "*") -> list:
        """
        异步列出目录中的文件

        Args:
            directory: 目录路径
            pattern: 文件匹配模式（默认"*"）

        Returns:
            文件路径列表
        """
        try:
            # glob操作在executor中执行
            import asyncio
            loop = asyncio.get_event_loop()
            files = await loop.run_in_executor(
                None,
                lambda: list(directory.glob(pattern))
            )
            return files
        except Exception as e:
            logger.error(f"Error listing files in {directory}: {e}")
            return []


# 便捷函数
async def read_json(file_path: Path) -> Dict[str, Any]:
    """便捷函数：异步读取JSON"""
    return await AsyncFileManager.read_json(file_path)


async def write_json(file_path: Path, data: Dict[str, Any], atomic: bool = True):
    """便捷函数：异步写入JSON"""
    await AsyncFileManager.write_json(file_path, data, atomic=atomic)


async def read_text(file_path: Path) -> str:
    """便捷函数：异步读取文本"""
    return await AsyncFileManager.read_text(file_path)


async def write_text(file_path: Path, content: str, atomic: bool = True):
    """便捷函数：异步写入文本"""
    await AsyncFileManager.write_text(file_path, content, atomic=atomic)
