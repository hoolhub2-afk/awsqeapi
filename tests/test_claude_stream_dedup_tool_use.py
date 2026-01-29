from src.integrations.claude.stream import ClaudeStreamHandler


def test_stream_ignores_duplicate_tool_use_id_start():
    import asyncio

    async def run():
        handler = ClaudeStreamHandler(model="claude-sonnet-4.5", input_tokens=0)

        events = []
        async for e in handler.handle_event("toolUseEvent", {"toolUseId": "tool-1", "name": "t", "input": {}}):
            events.append(e)
        async for e in handler.handle_event("toolUseEvent", {"toolUseId": "tool-1", "name": "t", "input": {}, "stop": True}):
            events.append(e)

        start_events = [e for e in events if "content_block_start" in e and "\"tool_use\"" in e]
        assert len(start_events) == 1

        events2 = []
        async for e in handler.handle_event("toolUseEvent", {"toolUseId": "tool-1", "name": "t", "input": {}}):
            events2.append(e)
        assert events2 == []

    asyncio.run(run())
