from src.integrations.claude.stream import ClaudeStreamHandler


def test_stream_emits_thinking_blocks_from_tags():
    import asyncio

    async def run():
        handler = ClaudeStreamHandler(model="claude-sonnet-4.5", input_tokens=0)
        out = []
        async for sse in handler.handle_event("assistantResponseEvent", {"content": "a<thinking>t</thinking>b"}):
            out.append(sse)
        text = "\n".join(out)
        assert "\"type\": \"thinking\"" in text
        assert "\"type\": \"thinking_delta\"" in text
        assert "\"thinking\": \"t\"" in text

    asyncio.run(run())


def test_stream_handles_split_thinking_tags_across_events():
    import asyncio

    async def run():
        handler = ClaudeStreamHandler(model="claude-sonnet-4.5", input_tokens=0)
        out = []
        async for sse in handler.handle_event("assistantResponseEvent", {"content": "a<thin"}):
            out.append(sse)
        async for sse in handler.handle_event("assistantResponseEvent", {"content": "king>t</thinking>b"}):
            out.append(sse)
        text = "\n".join(out)
        assert "\"type\": \"thinking_delta\"" in text
        assert "\"thinking\": \"t\"" in text

    asyncio.run(run())

