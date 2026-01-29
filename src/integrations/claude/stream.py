import json
import logging
from typing import AsyncGenerator, Optional, Dict, Any, List, Set
import tiktoken

from src.integrations.claude import parser as _parser

logger = logging.getLogger(__name__)

THINKING_START_TAG = "<thinking>"
THINKING_END_TAG = "</thinking>"

def _pending_tag_suffix(buffer: str, tag: str) -> int:
    if not buffer or not tag:
        return 0
    max_len = min(len(buffer), len(tag) - 1)
    for length in range(max_len, 0, -1):
        if buffer[-length:] == tag[:length]:
            return length
    return 0

# ------------------------------------------------------------------------------
# Tokenizer
# ------------------------------------------------------------------------------

try:
    # cl100k_base is used by gpt-4, gpt-3.5-turbo, text-embedding-ada-002
    ENCODING = tiktoken.get_encoding("cl100k_base")
except Exception as exc:
    ENCODING = None

def count_tokens(text: str) -> int:
    """Counts tokens with tiktoken."""
    if not text or not ENCODING:
        return 0
    return len(ENCODING.encode(text))

# ------------------------------------------------------------------------------
# Static Loader
# ------------------------------------------------------------------------------

build_message_start = _parser.build_message_start
build_content_block_start = _parser.build_content_block_start
build_content_block_delta = _parser.build_content_block_delta
build_thinking_delta = _parser.build_thinking_delta
build_content_block_stop = _parser.build_content_block_stop
build_ping = _parser.build_ping
build_message_stop = _parser.build_message_stop
build_tool_use_start = _parser.build_tool_use_start
build_tool_use_input_delta = _parser.build_tool_use_input_delta

class ClaudeStreamHandler:
    def __init__(self, model: str, input_tokens: int = 0):
        self.model = model
        self.input_tokens = input_tokens
        self.response_buffer: List[str] = []
        self.content_block_index: int = -1
        self.content_block_started: bool = False
        self.content_block_start_sent: bool = False
        self.content_block_stop_sent: bool = False
        self.message_start_sent: bool = False
        self.conversation_id: Optional[str] = None

        # Tool use state
        self.current_tool_use: Optional[Dict[str, Any]] = None
        self.tool_input_buffer: List[str] = []
        self.tool_use_id: Optional[str] = None
        self.tool_name: Optional[str] = None
        self._processed_tool_use_ids: Set[str] = set()
        self.all_tool_inputs: List[str] = []

        self._open_block_type: Optional[str] = None
        self._text_buffer: List[str] = []
        self._thinking_buffer: List[str] = []
        self._think_buffer: str = ""
        self._in_think_block: bool = False

    async def _open_block(self, block_type: str) -> AsyncGenerator[str, None]:
        if self._open_block_type and not self.content_block_stop_sent:
            yield build_content_block_stop(self.content_block_index)
            self.content_block_stop_sent = True
        self.content_block_index += 1
        yield build_content_block_start(self.content_block_index, block_type)
        self._open_block_type = block_type
        self.content_block_start_sent = True
        self.content_block_stop_sent = False
        self.content_block_started = True

    async def _emit_text(self, text: str) -> AsyncGenerator[str, None]:
        if not text:
            return
        if self._open_block_type != "text":
            async for sse in self._open_block("text"):
                yield sse
        self._text_buffer.append(text)
        yield build_content_block_delta(self.content_block_index, text)

    async def _emit_thinking(self, thinking: str) -> AsyncGenerator[str, None]:
        if not thinking:
            return
        if self._open_block_type != "thinking":
            async for sse in self._open_block("thinking"):
                yield sse
        self._thinking_buffer.append(thinking)
        yield build_thinking_delta(self.content_block_index, thinking)

    async def _consume_think_buffer(self) -> AsyncGenerator[str, None]:
        while self._think_buffer:
            if not self._in_think_block:
                start = self._think_buffer.find(THINKING_START_TAG)
                if start == -1:
                    pending = _pending_tag_suffix(self._think_buffer, THINKING_START_TAG)
                    emit = self._think_buffer[:-pending] if pending else self._think_buffer
                    self._think_buffer = self._think_buffer[-pending:] if pending else ""
                    async for sse in self._emit_text(emit):
                        yield sse
                    if pending:
                        return
                    continue
                async for sse in self._emit_text(self._think_buffer[:start]):
                    yield sse
                self._think_buffer = self._think_buffer[start + len(THINKING_START_TAG):]
                self._in_think_block = True
                continue

            end = self._think_buffer.find(THINKING_END_TAG)
            if end == -1:
                pending = _pending_tag_suffix(self._think_buffer, THINKING_END_TAG)
                emit = self._think_buffer[:-pending] if pending else self._think_buffer
                self._think_buffer = self._think_buffer[-pending:] if pending else ""
                async for sse in self._emit_thinking(emit):
                    yield sse
                if pending:
                    return
                continue
            async for sse in self._emit_thinking(self._think_buffer[:end]):
                yield sse
            self._think_buffer = self._think_buffer[end + len(THINKING_END_TAG):]
            if self._open_block_type == "thinking" and not self.content_block_stop_sent:
                yield build_content_block_stop(self.content_block_index)
                self.content_block_stop_sent = True
                self._open_block_type = None
            self._in_think_block = False

    async def handle_event(self, event_type: str, payload: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """Process a single Amazon Q event and yield Claude SSE events."""
        
        # 1. Message Start (initial-response)
        if event_type == "initial-response":
            if not self.message_start_sent:
                conv_id = payload.get('conversationId', self.conversation_id or 'unknown')
                self.conversation_id = conv_id
                yield build_message_start(conv_id, self.model, self.input_tokens)
                self.message_start_sent = True
                yield build_ping()

        # 2. Content Block Delta (assistantResponseEvent)
        elif event_type == "assistantResponseEvent":
            content = payload.get("content", "")

            logger.debug("[StreamHandler] assistantResponseEvent: content_len=%d, content_preview=%s",
                        len(content) if content else 0,
                        str(content)[:80] if content else "")

            # 去重已在上游 _dedupe_assistant_content_events 中完成
            # 这里不再需要重复去重，避免逻辑冲突

            # Close any open tool use block
            if self.current_tool_use and not self.content_block_stop_sent:
                yield build_content_block_stop(self.content_block_index)
                self.content_block_stop_sent = True
                self.current_tool_use = None
                self._open_block_type = None

            if content:
                self._think_buffer += content
                async for sse in self._consume_think_buffer():
                    yield sse

        # 3. Tool Use (toolUseEvent)
        elif event_type == "toolUseEvent":
            tool_use_id = payload.get("toolUseId")
            tool_name = payload.get("name")
            tool_input = payload.get("input", {})
            is_stop = payload.get("stop", False)

            if (
                tool_use_id
                and tool_use_id in self._processed_tool_use_ids
                and not self.current_tool_use
            ):
                return

            # Start new tool use
            if tool_use_id and tool_name and not self.current_tool_use:
                # Close previous text block if open
                if self._open_block_type and not self.content_block_stop_sent:
                    yield build_content_block_stop(self.content_block_index)
                    self.content_block_stop_sent = True
                    self._open_block_type = None

                self._processed_tool_use_ids.add(tool_use_id)
                self.content_block_index += 1
                
                yield build_tool_use_start(self.content_block_index, tool_use_id, tool_name)
                
                self.content_block_started = True
                self.current_tool_use = {"toolUseId": tool_use_id, "name": tool_name}
                self.tool_use_id = tool_use_id
                self.tool_name = tool_name
                self.tool_input_buffer = []
                self.content_block_stop_sent = False
                self.content_block_start_sent = True
                self._open_block_type = "tool_use"

            # Accumulate input
            is_current_tool = (
                self.current_tool_use
                and (tool_use_id is None or tool_use_id == self.tool_use_id)
            )
            if is_current_tool and tool_input:
                fragment = ""
                if isinstance(tool_input, str):
                    fragment = tool_input
                else:
                    fragment = json.dumps(tool_input, ensure_ascii=False)
                
                self.tool_input_buffer.append(fragment)
                yield build_tool_use_input_delta(self.content_block_index, fragment)

            # Stop tool use
            if is_stop and is_current_tool:
                full_input = "".join(self.tool_input_buffer)
                self.all_tool_inputs.append(full_input)
                
                yield build_content_block_stop(self.content_block_index)
                self.content_block_stop_sent = True
                self.content_block_started = False
                self.current_tool_use = None
                self.tool_use_id = None
                self.tool_name = None
                self.tool_input_buffer = []
                self._open_block_type = None

        # 4. Assistant Response End (assistantResponseEnd)
        elif event_type == "assistantResponseEnd":
            # Close any open block
            if self._open_block_type and self.content_block_started and not self.content_block_stop_sent:
                yield build_content_block_stop(self.content_block_index)
                self.content_block_stop_sent = True
                self._open_block_type = None

    async def finish(self) -> AsyncGenerator[str, None]:
        """Send final events."""
        # Ensure last block is closed
        if self._open_block_type and self.content_block_started and not self.content_block_stop_sent:
            yield build_content_block_stop(self.content_block_index)
            self.content_block_stop_sent = True
            self._open_block_type = None

        # Calculate output tokens (approximate)
        full_text = "".join(self._text_buffer)
        full_thinking = "".join(self._thinking_buffer)
        full_tool_input = "".join(self.all_tool_inputs)
        # Simple approximation: 4 chars per token
        # output_tokens = max(1, (len(full_text) + len(full_tool_input)) // 4)
        output_tokens = count_tokens(full_text) + count_tokens(full_thinking) + count_tokens(full_tool_input)

        yield build_message_stop(self.input_tokens, output_tokens, "end_turn")
