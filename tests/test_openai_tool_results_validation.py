import pytest

from src.integrations import amazonq_client


def test_tool_results_without_prior_tool_uses_rejected():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
    ]

    with pytest.raises(ValueError):
        amazonq_client.build_amazonq_request(messages, model="claude-sonnet-4.5")


def test_tool_results_must_match_prior_tool_uses():
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_2", "content": "ok"},
    ]

    with pytest.raises(ValueError):
        amazonq_client.build_amazonq_request(
            messages,
            model="claude-sonnet-4.5",
            tools=[{"type": "function", "function": {"name": "a", "parameters": {"type": "object"}}}],
        )
