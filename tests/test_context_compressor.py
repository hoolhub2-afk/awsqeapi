"""测试上下文自动压缩功能"""
import pytest
from src.core.context_compressor import compress_messages


def test_no_compression_needed():
    """小于限制时不压缩"""
    messages = [
        {'role': 'user', 'content': '你好'},
        {'role': 'assistant', 'content': '你好！'}
    ]
    result = compress_messages(messages, max_tokens=1000000)
    assert len(result) == 2


def test_compression_triggered():
    """超过限制时自动压缩"""
    # 创建大量消息
    messages = [{'role': 'user', 'content': 'x' * 100000} for _ in range(20)]
    result = compress_messages(messages, max_tokens=100000, keep_recent=3)

    # 应该保留最近 3 条 + 1 条摘要
    assert len(result) == 4
    assert '以下是之前的对话摘要' in result[0]['content']
    assert result[0]['role'] == 'system'


def test_preserve_system_messages():
    """始终保留 system 消息"""
    messages = [
        {'role': 'system', 'content': '你是助手'},
        {'role': 'user', 'content': 'x' * 100000},
        {'role': 'user', 'content': 'x' * 100000},
        {'role': 'user', 'content': '最新消息'}
    ]
    result = compress_messages(messages, max_tokens=100000, keep_recent=1)

    # system + 摘要 + 最新消息
    assert result[0]['role'] == 'system'
    assert result[-1]['content'] == '最新消息'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
