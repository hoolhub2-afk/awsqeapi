from src.integrations import amazonq_client


def test_openai_message_attachments_image_base64_is_mapped_to_amazonq_images():
    messages = [
        {
            "role": "user",
            "content": "hi",
            "attachments": [
                {"mime_type": "image/png", "data": "Zm9vYmFy"},
            ],
        }
    ]

    body = amazonq_client.build_amazonq_request(messages, model="claude-sonnet-4.5")
    current = body["conversationState"]["currentMessage"]["userInputMessage"]

    assert current["images"][0]["format"] == "png"
    assert current["images"][0]["source"]["bytes"] == "Zm9vYmFy"


def test_openai_message_attachments_image_data_url_is_mapped_to_amazonq_images():
    url = "data:image/jpeg;base64,QUJD"
    messages = [
        {
            "role": "user",
            "content": "hi",
            "attachments": [
                {"url": url},
            ],
        }
    ]

    body = amazonq_client.build_amazonq_request(messages, model="claude-sonnet-4.5")
    current = body["conversationState"]["currentMessage"]["userInputMessage"]

    assert current["images"][0]["format"] == "jpeg"
    assert current["images"][0]["source"]["bytes"] == "QUJD"
