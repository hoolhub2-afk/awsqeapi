from src.integrations.claude.converter import convert_claude_to_amazonq_request
from src.integrations.claude.types import ClaudeMessage, ClaudeRequest


def _image_block(b64: str) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": b64},
    }


def test_prune_images_to_last_two_user_messages():
    req = ClaudeRequest(
        model="claude-sonnet-4-5-20250929",
        messages=[
            ClaudeMessage(role="user", content=[{"type": "text", "text": "u1"}, _image_block("b64_1")]),
            ClaudeMessage(role="assistant", content="a1"),
            ClaudeMessage(role="user", content=[{"type": "text", "text": "u2"}, _image_block("b64_2")]),
            ClaudeMessage(role="assistant", content="a2"),
            ClaudeMessage(role="user", content=[{"type": "text", "text": "u3"}, _image_block("b64_3")]),
        ],
    )

    body = convert_claude_to_amazonq_request(req)
    history = body["conversationState"]["history"]
    current = body["conversationState"]["currentMessage"]["userInputMessage"]

    assert "images" not in history[0]["userInputMessage"]
    assert history[2]["userInputMessage"]["images"][0]["source"]["bytes"] == "b64_2"
    assert current["images"][0]["source"]["bytes"] == "b64_3"

