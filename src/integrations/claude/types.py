from typing import List, Optional, Union, Dict, Any, Literal
from pydantic import AliasChoices, BaseModel, Field, field_validator
from .model_config import get_model_max_tokens
from src.core.tokenizer import count_tokens
from src.core.context_compressor import compress_messages

class ClaudeMessage(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]]]

class ClaudeTool(BaseModel):
    name: str
    description: Optional[str] = ""
    input_schema: Dict[str, Any]

class ClaudeRequest(BaseModel):
    model: str
    messages: List[ClaudeMessage]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    tools: Optional[List[ClaudeTool]] = None
    stream: bool = False
    system: Optional[Union[str, List[Dict[str, Any]]]] = None
    thinking: Optional[Union[bool, Dict[str, Any]]] = None  # 是否启用思考流
    conversation_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("conversation_id", "conversationId"),
    )

    @field_validator('messages')
    @classmethod
    def validate_messages(cls, v):
        """验证并自动压缩消息"""
        if not v:
            raise ValueError('messages list cannot be empty')
        total_tokens = 0
        msg_dicts = []
        for msg in v:
            if isinstance(msg.content, str):
                total_tokens += count_tokens(msg.content)
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, dict) and 'text' in block:
                        total_tokens += count_tokens(block['text'])
            msg_dicts.append({'role': msg.role, 'content': msg.content})

        # 超过 950K 自动压缩
        if total_tokens > 950000:
            compressed = compress_messages(msg_dicts, max_tokens=950000)
            return [ClaudeMessage(role=m['role'], content=m['content']) for m in compressed]
        return v

    @field_validator('max_tokens', mode='before')
    @classmethod
    def set_max_tokens(cls, v, info):
        """根据模型自动设置 max_tokens"""
        if v is not None:
            return v
        model = info.data.get('model', 'claude-sonnet-4') if info.data else 'claude-sonnet-4'
        from .converter import map_model_name
        model_id = map_model_name(model)
        return get_model_max_tokens(model_id)
