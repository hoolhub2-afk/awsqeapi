from src.integrations.claude.converter import convert_claude_to_amazonq_request
from src.integrations.claude.types import ClaudeRequest


def test_boundary_merge_preserves_tools_and_tool_results():
    req = ClaudeRequest(
        model="claude-sonnet-4",
        tools=[{"name": "t", "description": "d", "input_schema": {"type": "object", "properties": {}}}],
        messages=[
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "ok", "status": "success"}],
            },
            {"role": "user", "content": "question"},
        ],
    )

    body = convert_claude_to_amazonq_request(req, conversation_id="cid-1")
    state = body["conversationState"]

    assert state["history"] == []
    current = state["currentMessage"]["userInputMessage"]
    ctx = current["userInputMessageContext"]

    assert ctx["tools"]
    assert [r["toolUseId"] for r in ctx["toolResults"]] == ["tool-1"]
    assert "question" in current["content"]

