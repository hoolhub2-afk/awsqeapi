from src.integrations.claude.converter import merge_user_messages


def test_merge_user_messages_preserves_tool_results():
    messages = [
        {
            "content": "first",
            "userInputMessageContext": {
                "envState": {"operatingSystem": "macos"},
                "toolResults": [
                    {
                        "toolUseId": "tool-1",
                        "content": [{"text": "ok-1"}],
                        "status": "success",
                    }
                ],
            },
            "origin": "CLI",
            "modelId": "claude-sonnet-4",
        },
        {
            "content": "second",
            "userInputMessageContext": {
                "toolResults": [
                    {
                        "toolUseId": "tool-2",
                        "content": [{"text": "ok-2"}],
                        "status": "success",
                    }
                ]
            },
            "origin": "CLI",
            "modelId": "claude-sonnet-4",
        },
    ]

    merged = merge_user_messages(messages)

    assert merged["content"] == "first\n\nsecond"
    assert merged["userInputMessageContext"]["envState"] == {"operatingSystem": "macos"}
    assert [r["toolUseId"] for r in merged["userInputMessageContext"]["toolResults"]] == [
        "tool-1",
        "tool-2",
    ]


def test_merge_user_messages_merges_duplicate_tool_results_by_tool_use_id():
    messages = [
        {
            "content": "first",
            "userInputMessageContext": {
                "toolResults": [
                    {
                        "toolUseId": "tool-1",
                        "content": [{"text": "part-1"}],
                        "status": "success",
                    }
                ]
            },
            "origin": "CLI",
        },
        {
            "content": "second",
            "userInputMessageContext": {
                "toolResults": [
                    {
                        "toolUseId": "tool-1",
                        "content": [{"text": "part-2"}],
                        "status": "success",
                    }
                ]
            },
            "origin": "CLI",
        },
    ]

    merged = merge_user_messages(messages)
    tool_results = merged["userInputMessageContext"]["toolResults"]

    assert [r["toolUseId"] for r in tool_results] == ["tool-1"]
    assert tool_results[0]["content"] == [{"text": "part-1"}, {"text": "part-2"}]


def test_merge_user_messages_does_not_emit_null_model_id():
    messages = [
        {"content": "first", "userInputMessageContext": {"envState": {}}, "origin": "CLI"},
        {"content": "second", "userInputMessageContext": {"envState": {}}, "origin": "CLI"},
    ]

    merged = merge_user_messages(messages)
    assert "modelId" not in merged
