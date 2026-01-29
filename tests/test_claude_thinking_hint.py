from src.integrations.claude.converter import THINKING_HINT, convert_claude_to_amazonq_request
from src.integrations.claude.types import ClaudeMessage, ClaudeRequest


def test_thinking_hint_appended_only_when_enabled_bool():
    req = ClaudeRequest(
        model="claude-sonnet-4-5-20250929",
        thinking=True,
        messages=[ClaudeMessage(role="user", content="hi")],
    )
    body = convert_claude_to_amazonq_request(req)
    content = body["conversationState"]["currentMessage"]["userInputMessage"]["content"]
    assert THINKING_HINT in content
    assert content.count(THINKING_HINT) == 1


def test_thinking_hint_not_appended_when_disabled_dict():
    req = ClaudeRequest(
        model="claude-sonnet-4-5-20250929",
        thinking={"type": "disabled"},
        messages=[ClaudeMessage(role="user", content="hi")],
    )
    body = convert_claude_to_amazonq_request(req)
    content = body["conversationState"]["currentMessage"]["userInputMessage"]["content"]
    assert THINKING_HINT not in content

