from src.integrations.claude.converter import convert_claude_to_amazonq_request
from src.integrations.claude.types import ClaudeRequest


def test_current_message_tool_result_only_includes_system_prompt():
    req = ClaudeRequest(
        model="claude-sonnet-4",
        system="sys",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": "ok",
                        "status": "success",
                    }
                ],
            }
        ],
    )

    body = convert_claude_to_amazonq_request(req, conversation_id="cid-1")
    msg = body["conversationState"]["currentMessage"]["userInputMessage"]

    assert msg["userInputMessageContext"]["toolResults"][0]["toolUseId"] == "tool-1"
    assert "SYSTEM PROMPT BEGIN" in msg["content"]
    assert "sys" in msg["content"]


def test_current_message_tool_result_only_keeps_empty_content_when_no_system_or_tools():
    req = ClaudeRequest(
        model="claude-sonnet-4",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": "ok",
                        "status": "success",
                    }
                ],
            }
        ],
    )

    body = convert_claude_to_amazonq_request(req, conversation_id="cid-1")
    msg = body["conversationState"]["currentMessage"]["userInputMessage"]
    assert msg["content"] == ""


def test_current_message_tool_result_only_with_long_tool_description_includes_docs_and_context_entry():
    req = ClaudeRequest(
        model="claude-sonnet-4",
        tools=[
            {
                "name": "t",
                "description": "x" * 10241,
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": "ok",
                        "status": "success",
                    }
                ],
            }
        ],
    )

    body = convert_claude_to_amazonq_request(req, conversation_id="cid-1")
    msg = body["conversationState"]["currentMessage"]["userInputMessage"]
    assert "TOOL DOCUMENTATION BEGIN" in msg["content"]
    assert "CONTEXT ENTRY BEGIN" in msg["content"]

