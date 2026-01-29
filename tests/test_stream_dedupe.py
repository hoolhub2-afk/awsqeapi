import pytest

from src.integrations import amazonq_client


class TestDeltaByPrefix:
    """Test _delta_by_prefix function edge cases"""

    def test_empty_previous(self):
        """When previous is empty, return current and current"""
        result = amazonq_client._delta_by_prefix("", "Hello")
        assert result == ("Hello", "Hello")

    def test_empty_current(self):
        """When current is empty, return previous and empty"""
        result = amazonq_client._delta_by_prefix("Hello", "")
        assert result == ("Hello", "")

    def test_simple_increment(self):
        """Simple increment case"""
        result = amazonq_client._delta_by_prefix("Hello", "Hello world")
        assert result == ("Hello world", " world")

    def test_complete_repeat(self):
        """Complete repeat - no new content"""
        result = amazonq_client._delta_by_prefix("Hello", "Hello")
        assert result == ("Hello", "")

    def test_substring_in_middle(self):
        """Substring appears in the middle"""
        result = amazonq_client._delta_by_prefix("Hello wor", "Hello world")
        assert result == ("Hello world", "ld")

    def test_no_overlap(self):
        """No overlap between strings"""
        result = amazonq_client._delta_by_prefix("Foo", "Bar")
        assert result == ("FooBar", "Bar")

    def test_partial_overlap_end(self):
        """Partial overlap at the end - in actual streaming, this rarely happens"""
        result = amazonq_client._delta_by_prefix("Hello wor", "wor ld")
        # When upstream sends overlapping complete content, we concatenate
        # This is the expected behavior for edge cases
        assert result == ("Hello wor ld", " ld")

    def test_longer_previous_no_overlap(self):
        """Previous is longer with no overlap"""
        result = amazonq_client._delta_by_prefix("Hello world!", "Hi")
        assert result == ("Hello world!Hi", "Hi")

    def test_exact_match_then_new_content(self):
        """Exact match then new content"""
        result = amazonq_client._delta_by_prefix("Hello", "Hello there")
        assert result == ("Hello there", " there")

    def test_small_fragment_no_overlap(self):
        """Small fragment with no overlap"""
        result = amazonq_client._delta_by_prefix("ABC", "DEF")
        assert result == ("ABCDEF", "DEF")


@pytest.mark.asyncio
async def test_dedupe_assistant_response_event_prefix_accumulation():
    """
    测试去重逻辑 - 严格遵循 AIClient-2-API 的实现
    只跳过连续完全相同的 content 事件，保持完整内容不变
    """
    async def events():
        yield ("initial-response", {"conversationId": "cid"})
        yield ("assistantResponseEvent", {"content": "Hello"})
        yield ("assistantResponseEvent", {"content": "Hello world"})
        yield ("assistantResponseEvent", {"content": "Hello world"})  # 重复，应跳过
        yield ("assistantResponseEvent", {"content": "Hello world!"})
        yield ("assistantResponseEnd", {})

    seen = []
    async for event_type, payload in amazonq_client._dedupe_assistant_content_events(events()):
        seen.append((event_type, payload))

    contents = [p["content"] for t, p in seen if t == "assistantResponseEvent"]
    # 新逻辑：保持完整内容，只跳过连续重复的 "Hello world"
    assert contents == ["Hello", "Hello world", "Hello world!"]

@pytest.mark.asyncio
async def test_dedupe_assistant_response_event_overlapping_delta_duplicates():
    big_a = "A" * 40
    big_b = "B" * 40

    async def events():
        yield ("assistantResponseEvent", {"content": big_a})
        yield ("assistantResponseEvent", {"content": big_b})
        yield ("assistantResponseEvent", {"content": big_b})

    contents = []
    async for event_type, payload in amazonq_client._dedupe_assistant_content_events(events()):
        if event_type == "assistantResponseEvent":
            contents.append(payload["content"])

    assert contents == [big_a, big_b]


@pytest.mark.asyncio
async def test_initial_response_is_injected_with_conversation_id_when_missing():
    async def events():
        yield ("initial-response", {"x": 1})
        yield ("assistantResponseEnd", {})

    seen = []
    async for event_type, payload in amazonq_client._ensure_initial_response_has_conversation_id(events(), "cid-123"):
        seen.append((event_type, payload))

    assert seen[0][0] == "initial-response"
    assert seen[0][1]["conversationId"] == "cid-123"
