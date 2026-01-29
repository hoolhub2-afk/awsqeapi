"""上下文自动压缩模块"""
from typing import List, Dict, Any, Union
from src.core.tokenizer import count_tokens


def _budget_tokens_estimate(text: str) -> int:
    # tiktoken 在超大文本上会非常慢；这里采用保守估算以决定是否需要压缩
    if not text:
        return 0
    if len(text) >= 20000:
        return len(text)
    return count_tokens(text)


def compress_messages(
    messages: List[Dict[str, Any]],
    max_tokens: int = 950000,
    keep_recent: int = 5
) -> List[Dict[str, Any]]:
    """
    当消息超过 token 限制时自动压缩

    策略：
    1. 保留最近 N 条消息（默认 5 条）
    2. 将旧消息压缩为摘要
    3. 始终保留 system 消息
    """
    if not messages:
        return messages

    # 计算总 token（超过上限则提前停止，避免大文本全量计数导致卡顿）
    total_tokens = 0
    for msg in messages:
        total_tokens += _budget_tokens_estimate(_extract_text(msg.get('content', '')))
        if total_tokens > max_tokens:
            break

    if total_tokens <= max_tokens:
        return messages

    # 分离 system 和其他消息
    system_msgs = [m for m in messages if m.get('role') == 'system']
    other_msgs = [m for m in messages if m.get('role') != 'system']

    if len(other_msgs) <= keep_recent:
        return messages

    # 保留最近的消息
    recent_msgs = other_msgs[-keep_recent:]
    old_msgs = other_msgs[:-keep_recent]

    # 压缩旧消息为摘要（作为 system 消息传递上下文）
    summary = _create_summary(old_msgs)
    summary_msg = {
        'role': 'system',
        'content': summary
    }

    return system_msgs + [summary_msg] + recent_msgs


def _extract_text(content: Union[str, List[Dict[str, Any]]]) -> str:
    """提取文本内容"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'text':
                texts.append(block.get('text', ''))
        return '\n'.join(texts)
    return str(content)


def _create_summary(messages: List[Dict[str, Any]]) -> str:
    """创建消息摘要 - 使用 Amazon Q 格式"""
    summary_parts = []
    for msg in messages:
        role = msg.get('role', 'unknown')
        content = _extract_text(msg.get('content', ''))
        # 截取前 150 字符作为摘要
        preview = content[:150] + ('...' if len(content) > 150 else '')
        summary_parts.append(f"- {role}: {preview}")

    summary_text = '\n'.join(summary_parts)
    return (
        f"以下是之前的对话摘要（已压缩 {len(messages)} 条消息以节省上下文空间）：\n\n"
        f"{summary_text}\n\n"
        f"请基于以上摘要继续对话。"
    )
