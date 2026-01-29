import json

from src.integrations import amazonq_client


def test_build_amazonq_request_maps_openai_tools_and_tool_results():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "get weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }
    ]

    messages = [
        {"role": "user", "content": "what is weather"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": json.dumps({"city": "sf"})},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
    ]

    body = amazonq_client.build_amazonq_request(messages, model="claude-opus-4-1-20250805", tools=tools)
    state = body["conversationState"]

    current = state["currentMessage"]["userInputMessage"]
    ctx = current["userInputMessageContext"]

    assert current["modelId"] == "claude-opus-4.5"
    assert ctx["tools"][0]["toolSpecification"]["name"] == "get_weather"
    assert ctx["toolResults"][0]["toolUseId"] == "call_1"
    assert ctx["toolResults"][0]["content"][0]["text"] == "sunny"

    history = state["history"]
    assert history[0]["userInputMessage"]["content"]
    assert history[1]["assistantResponseMessage"]["toolUses"][0]["toolUseId"] == "call_1"


def test_build_amazonq_request_tool_choice_none_removes_tools():
    tools = [
        {"type": "function", "function": {"name": "a", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "b", "parameters": {"type": "object"}}},
    ]
    messages = [{"role": "user", "content": "hi"}]
    body = amazonq_client.build_amazonq_request(messages, model="claude-sonnet-4.5", tools=tools, tool_choice="none")
    ctx = body["conversationState"]["currentMessage"]["userInputMessage"]["userInputMessageContext"]
    assert ctx.get("tools") == []


def test_build_amazonq_request_tool_choice_specific_function_filters_tools():
    tools = [
        {"type": "function", "function": {"name": "a", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "b", "parameters": {"type": "object"}}},
    ]
    messages = [{"role": "user", "content": "hi"}]
    tool_choice = {"type": "function", "function": {"name": "b"}}
    body = amazonq_client.build_amazonq_request(messages, model="claude-sonnet-4.5", tools=tools, tool_choice=tool_choice)
    ctx = body["conversationState"]["currentMessage"]["userInputMessage"]["userInputMessageContext"]
    assert ctx["tools"][0]["toolSpecification"]["name"] == "b"


def test_build_amazonq_request_tool_choice_auto_keeps_tools():
    tools = [
        {"type": "function", "function": {"name": "a", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "b", "parameters": {"type": "object"}}},
    ]
    messages = [{"role": "user", "content": "hi"}]
    body = amazonq_client.build_amazonq_request(messages, model="claude-sonnet-4.5", tools=tools, tool_choice="auto")
    ctx = body["conversationState"]["currentMessage"]["userInputMessage"]["userInputMessageContext"]
    assert {t["toolSpecification"]["name"] for t in ctx["tools"]} == {"a", "b"}


def test_build_amazonq_request_tool_choice_required_keeps_tools():
    tools = [
        {"type": "function", "function": {"name": "a", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "b", "parameters": {"type": "object"}}},
    ]
    messages = [{"role": "user", "content": "hi"}]
    body = amazonq_client.build_amazonq_request(messages, model="claude-sonnet-4.5", tools=tools, tool_choice="required")
    ctx = body["conversationState"]["currentMessage"]["userInputMessage"]["userInputMessageContext"]
    assert {t["toolSpecification"]["name"] for t in ctx["tools"]} == {"a", "b"}
