import pytest

from src.integrations.claude.converter import process_history
from src.integrations.claude.types import ClaudeMessage


def test_process_history_does_not_merge_user_messages_with_tool_results():
    messages = [
        ClaudeMessage(role="user", content="hello"),
        ClaudeMessage(
            role="user",
            content=[
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "content": "ok",
                    "status": "success",
                }
            ],
        ),
        ClaudeMessage(role="assistant", content="done"),
    ]

    history = process_history(messages)

    assert len(history) == 3
    assert "userInputMessage" in history[0]
    assert history[0]["userInputMessage"]["content"] == "hello"

    assert "userInputMessage" in history[1]
    ctx = history[1]["userInputMessage"]["userInputMessageContext"]
    assert [r["toolUseId"] for r in ctx["toolResults"]] == ["tool-1"]

    assert "assistantResponseMessage" in history[2]


def test_process_history_strict_mode_raises_with_debug_context():
    import os

    old = os.environ.get("DEBUG_MESSAGE_CONVERSION"); os.environ["DEBUG_MESSAGE_CONVERSION"] = "1"
    try:
        messages = [ClaudeMessage(role="assistant", content=[{"type": "tool_use", "id": "tool-1", "name": "t", "input": {}}, {"type": "text", "text": "a"}]), ClaudeMessage(role="assistant", content="b")]
        with pytest.raises(ValueError) as exc: process_history(messages)
        s = str(exc.value); assert "prev_idx=0" in s and "idx=1" in s and "tool-1" in s
    finally:
        os.environ.pop("DEBUG_MESSAGE_CONVERSION", None) if old is None else os.environ.__setitem__("DEBUG_MESSAGE_CONVERSION", old)


def test_process_history_strict_mode_raises_on_tool_results_without_prior_tool_use():
    import os

    old = os.environ.get("DEBUG_MESSAGE_CONVERSION"); os.environ["DEBUG_MESSAGE_CONVERSION"] = "1"
    try:
        messages = [ClaudeMessage(role="user", content=[{"type": "tool_result", "tool_use_id": "tool-1", "content": "ok", "status": "success"}])]
        with pytest.raises(ValueError) as exc: process_history(messages)
        assert "toolResults order violated" in str(exc.value)
    finally:
        os.environ.pop("DEBUG_MESSAGE_CONVERSION", None) if old is None else os.environ.__setitem__("DEBUG_MESSAGE_CONVERSION", old)
