from src.integrations import amazonq_client


def test_build_amazonq_request_maps_openai_image_url_to_images():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "old"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,b2xk"}},
            ],
        },
        {"role": "assistant", "content": "ack"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "mid"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,bWlk"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
            ],
        }
    ]

    body = amazonq_client.build_amazonq_request(messages, model="claude-sonnet-4.5")
    current = body["conversationState"]["currentMessage"]["userInputMessage"]
    history = body["conversationState"]["history"]

    assert "hi" in current["content"]
    assert current["images"][0]["source"]["bytes"] == "bWlk"
    assert current["images"][1]["source"]["bytes"] == "aGVsbG8="
    assert "images" not in history[0]["userInputMessage"]
