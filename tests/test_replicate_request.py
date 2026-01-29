from src.integrations import replicate


def test_build_amazonq_request_uses_runtime_messages():
    messages = [
        {"role": "system", "content": "stay safe"},
        {"role": "assistant", "content": "hello from history"},
        {"role": "user", "content": "ping"},
    ]

    body = replicate.build_amazonq_request(messages, model="claude-sonnet-4.5")
    state = body["conversationState"]

    assert state["conversationId"]
    assert state["history"][0]["assistantResponseMessage"]["content"] == "hello from history"

    current = state["currentMessage"]["userInputMessage"]["content"]
    assert "stay safe" in current
    assert "ping" in current
    assert "你好，你必须讲个故事" not in current


def test_headers_do_not_include_static_placeholders():
    headers = replicate._build_amazonq_headers("token-123")
    assert headers["Authorization"] == "Bearer token-123"
    combined = " ".join(headers.values())
    assert "<redacted>" not in combined


def test_build_amazonq_request_maps_canonical_model_id():
    messages = [
        {"role": "user", "content": "ping"},
    ]

    body = replicate.build_amazonq_request(messages, model="claude-opus-4-5-20251101")
    state = body["conversationState"]
    current = state["currentMessage"]["userInputMessage"]

    assert current["modelId"] == "claude-opus-4.5"
