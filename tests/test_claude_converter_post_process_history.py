from src.integrations.claude.converter import convert_claude_to_amazonq_request
from src.integrations.claude.types import ClaudeRequest


def test_post_process_merges_consecutive_user_messages_in_history():
    req = ClaudeRequest(
        model="claude-sonnet-4",
        messages=[
            {"role": "user", "content": "M"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tool-1", "name": "t", "input": {"x": 1}}],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "ok", "status": "success"}],
            },
            {"role": "user", "content": "follow"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "current"},
        ],
    )

    body = convert_claude_to_amazonq_request(req, conversation_id="cid-1")
    history = body["conversationState"]["history"]

    assert len(history) == 4
    assert "userInputMessage" in history[0]
    assert "assistantResponseMessage" in history[1]
    assert "userInputMessage" in history[2]
    assert "assistantResponseMessage" in history[3]

    merged_user = history[2]["userInputMessage"]
    assert "follow" in merged_user["content"]
    tool_results = merged_user["userInputMessageContext"]["toolResults"]
    assert [r["toolUseId"] for r in tool_results] == ["tool-1"]

