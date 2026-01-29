import uuid
import time
import json
import httpx
import os
import logging
from typing import Dict, Any, Optional, List, AsyncGenerator, Tuple, Set, Callable, TypeVar
from dataclasses import dataclass

# 确保 .env 已加载
from src.core.env import env_loaded  # noqa: F401
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, field_validator

from src.core.tokenizer import count_tokens
from src.core.http_client import get_client
import src.core.request_dedupe as request_dedupe
from src.core.context_compressor import compress_messages
from src.core.retry import retry_with_account_fallback
from src.integrations.claude.model_config import MODEL_CONFIGS
from src.core.model_mapping import map_model_to_amazonq
from src.core.config import AMAZON_Q_DEFAULT_MODEL
from src.services.account_service import (
    disable_account,
    refresh_access_token_in_db,
    is_access_token_expired,
    update_account_stats,
    list_enabled_accounts
)
from src.services.quota_service import QuotaService
from src.services.session_service import SessionService
from src.api.dependencies import require_account
from src.integrations.amazonq_client import (
    build_amazonq_request,
    send_chat_request,
    QuotaExhaustedException,
    AccountSuspendedException,
    AccountUnauthorizedException,
    ChatResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Constants
def _parse_max_tokens() -> int:
    """安全解析 MAX_TOKENS_PER_REQUEST 环境变量"""
    default = 1000000
    try:
        return int(os.getenv("MAX_TOKENS_PER_REQUEST", str(default)))
    except ValueError:
        logger.warning("Invalid MAX_TOKENS_PER_REQUEST value, using default: %d", default)
        return default

def _parse_compress_threshold() -> int:
    """安全解析 TOKEN_COMPRESS_THRESHOLD 环境变量"""
    default = 950000
    try:
        return int(os.getenv("TOKEN_COMPRESS_THRESHOLD", str(default)))
    except ValueError:
        logger.warning("Invalid TOKEN_COMPRESS_THRESHOLD value, using default: %d", default)
        return default

MAX_TOKENS_PER_REQUEST = _parse_max_tokens()
TOKEN_COMPRESS_THRESHOLD = _parse_compress_threshold()

class ChatMessage(BaseModel):
    role: str
    content: Any
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    attachments: Optional[List[Dict[str, Any]]] = None

    @field_validator('role')
    @classmethod
    def validate_role(cls, v):
        allowed_roles = ['user', 'assistant', 'system', 'tool']
        if v not in allowed_roles:
            raise ValueError(f'role must be one of: {allowed_roles}')
        return v

class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: List[ChatMessage]
    stream: Optional[bool] = False
    user: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Any] = None
    functions: Optional[List[Dict[str, Any]]] = None
    function_call: Optional[Any] = None

    @field_validator('messages')
    @classmethod
    def validate_messages(cls, v):
        if not v:
            raise ValueError('messages list cannot be empty')
        # 计算总 token 数
        total_tokens = 0
        for msg in v:
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            total_tokens += count_tokens(content)

        # 硬限制: 超过最大限制直接拒绝
        if total_tokens > MAX_TOKENS_PER_REQUEST:
            raise ValueError(f'请求超过限制: {total_tokens} tokens (最大 {MAX_TOKENS_PER_REQUEST})')

        return v


def _maybe_compress_messages(messages: List[ChatMessage]) -> List[ChatMessage]:
    """如果消息超过压缩阈值则进行压缩, 工具模式下不压缩"""
    total_tokens = 0
    msg_dicts = []
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        total_tokens += count_tokens(content)
        msg_dicts.append({'role': msg.role, 'content': msg.content})

    if total_tokens <= TOKEN_COMPRESS_THRESHOLD:
        return messages

    has_tooling = any((m.role == "tool") or bool(getattr(m, "tool_calls", None)) for m in messages)
    if has_tooling:
        raise HTTPException(status_code=400, detail='messages too large for tool mode compression')

    compressed = compress_messages(msg_dicts, max_tokens=TOKEN_COMPRESS_THRESHOLD)
    return [ChatMessage(role=m['role'], content=m['content']) for m in compressed]

THINKING_START_TAG = "<thinking>"
THINKING_END_TAG = "</thinking>"

def _pending_tag_suffix(buffer: str, tag: str) -> int:
    max_len = min(len(buffer), len(tag) - 1)
    for length in range(max_len, 0, -1):
        if buffer[-length:] == tag[:length]:
            return length
    return 0

class _ThinkingStripper:
    def __init__(self):
        self._buf = ""
        self._in = False

    def feed(self, text: str) -> str:
        if not text:
            return ""
        self._buf += text
        out: List[str] = []
        while self._buf:
            if not self._in:
                start = self._buf.find(THINKING_START_TAG)
                if start == -1:
                    pending = _pending_tag_suffix(self._buf, THINKING_START_TAG)
                    out.append(self._buf[:-pending] if pending else self._buf)
                    self._buf = self._buf[-pending:] if pending else ""
                    break
                out.append(self._buf[:start])
                self._buf = self._buf[start + len(THINKING_START_TAG):]
                self._in = True
                continue
            end = self._buf.find(THINKING_END_TAG)
            if end == -1:
                pending = _pending_tag_suffix(self._buf, THINKING_END_TAG)
                self._buf = self._buf[-pending:] if pending else ""
                break
            self._buf = self._buf[end + len(THINKING_END_TAG):]
            self._in = False
        return "".join(out)

def _list_local_models() -> List[Dict[str, Any]]:
    models: List[Dict[str, Any]] = []
    for model_id in sorted(MODEL_CONFIGS.keys()):
        cfg = MODEL_CONFIGS.get(model_id, {})
        models.append({
            "id": model_id,
            "object": "model",
            "owned_by": "amazonq",
            "metadata": {
                "max_tokens": cfg.get("max_tokens"),
                "context_window": cfg.get("context_window"),
            },
        })
    return models

def _openai_non_streaming_response(
    text: str,
    model: Optional[str],
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    finish_reason: str = "stop",
) -> Dict[str, Any]:
    created = int(time.time())
    message: Dict[str, Any] = {
        "role": "assistant",
        "content": text,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
        if not text:
            message["content"] = None
    return {
        "id": f"chatcmpl-{uuid.uuid4()}",
        "object": "chat.completion",
        "created": created,
        "model": model or "unknown",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }

def _sse_format(obj: Dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

@router.get("/v1/models")
async def list_upstream_models():
    """
    Public models list endpoint.

    Many clients call this endpoint without an API key, so it must be public.
    """
    return {
        "object": "list",
        "data": _list_local_models(),
        "source": "local"
    }

async def _prepare_account_with_session(
    account: Dict[str, Any],
    messages: List[ChatMessage],
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """准备账号，优先使用会话绑定的账号"""
    session_key = SessionService.generate_session_key([m.model_dump() for m in messages], user_id=user_id)
    
    try:
        session_account_id = await SessionService.get_session_account(session_key)
        if session_account_id:
            candidates = await list_enabled_accounts()
            session_account = next((acc for acc in candidates if acc["id"] == session_account_id), None)
            if session_account:
                quota_stats = await QuotaService.get_quota_stats(session_account_id)
                if not quota_stats or quota_stats.get("quota_status") != "exhausted":
                    return session_account
    except Exception as exc:
        logger.debug("会话绑定账号查询失败, 回退默认账号: %s", exc)
    
    return account

async def _execute_chat_request(
    account: Dict[str, Any],
    messages: List[ChatMessage],
    model: Optional[str],
    stream: bool
) -> Tuple[Optional[str], Optional[AsyncGenerator[str, None]], Any]:
    """执行聊天请求的核心逻辑"""
    access = account.get("accessToken")
    if is_access_token_expired(account):
        refreshed = await refresh_access_token_in_db(account["id"])
        access = refreshed.get("accessToken")
        if not access:
            raise HTTPException(status_code=502, detail="Access token unavailable after refresh")

    client = get_client()
    try:
        result = await send_chat_request(
            access_token=access,
            messages=[m.dict() for m in messages],
            model=model,
            stream=stream,
            client=client
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return result.text, result.text_stream, result.tracker

async def _handle_account_errors(account_id: str, error: Exception) -> None:
    """统一处理账号相关错误"""
    if isinstance(error, QuotaExhaustedException):
        await update_account_stats(account_id, False, is_throttled=True, quota_exhausted=True)
    elif isinstance(error, AccountSuspendedException):
        await disable_account(account_id, "suspended")
    elif isinstance(error, AccountUnauthorizedException):
        await disable_account(account_id, "unauthorized")
    else:
        await update_account_stats(account_id, False)


# ==============================================================================
# 重试逻辑辅助函数 - 提取重复代码
# ==============================================================================

@dataclass
class RetryContext:
    """重试上下文，跟踪重试状态"""
    account: Dict[str, Any]
    tried_accounts: Set[str]
    max_retries: int = 3

    @classmethod
    def create(cls, account: Dict[str, Any], max_retries: int = 3) -> "RetryContext":
        return cls(account=account, tried_accounts={account["id"]}, max_retries=max_retries)


async def _select_fallback_account(ctx: RetryContext) -> Optional[Dict[str, Any]]:
    """选择备用账号，返回 None 表示没有可用账号"""
    candidates = await list_enabled_accounts()
    available = [acc for acc in candidates if acc["id"] not in ctx.tried_accounts]
    if not available:
        return None
    # 使用加权最少使用策略选择账号
    from src.api.dependencies import _select_best_account
    selected = _select_best_account(available)
    ctx.tried_accounts.add(selected["id"])
    ctx.account = selected
    return selected


async def _handle_retry_error(
    ctx: RetryContext,
    error: Exception,
    attempt: int,
    logger: logging.Logger
) -> bool:
    """
    处理重试错误，返回是否应该继续重试

    Returns:
        True: 应该继续重试
        False: 不应该重试，应该抛出异常
    """
    account_id = ctx.account["id"]

    if isinstance(error, QuotaExhaustedException):
        await update_account_stats(account_id, False, is_throttled=True, quota_exhausted=True)
        logger.warning(f"账号配额耗尽，已禁用 | 账号={account_id[:8]}")

        if attempt < ctx.max_retries - 1:
            new_account = await _select_fallback_account(ctx)
            if new_account:
                logger.info(f"切换到新账号 | 账号={new_account['id'][:8]}")
                return True
        return False

    elif isinstance(error, AccountSuspendedException):
        await disable_account(account_id, "suspended")
        logger.warning(f"账号被封禁，已禁用 | 账号={account_id[:8]}")

        if attempt < ctx.max_retries - 1:
            new_account = await _select_fallback_account(ctx)
            if new_account:
                logger.info(f"切换到新账号 | 账号={new_account['id'][:8]}")
                return True
        return False

    elif isinstance(error, AccountUnauthorizedException):
        await disable_account(account_id, "unauthorized")
        logger.warning(f"账号认证失败/Token过期，已禁用 | 账号={account_id[:8]}")

        if attempt < ctx.max_retries - 1:
            new_account = await _select_fallback_account(ctx)
            if new_account:
                logger.info(f"切换到新账号 | 账号={new_account['id'][:8]}")
                return True
        return False

    elif isinstance(error, httpx.HTTPError):
        await update_account_stats(account_id, False)
        return False

    elif "429" in str(error):
        await update_account_stats(account_id, False)
        if attempt < ctx.max_retries - 1:
            new_account = await _select_fallback_account(ctx)
            if new_account:
                logger.info(f"429错误，切换到新账号 | 账号={new_account['id'][:8]}")
                return True
        return False

    else:
        await update_account_stats(account_id, False)
        return False


def _raise_final_error(error: Exception) -> None:
    """根据错误类型抛出最终的 HTTP 异常"""
    if isinstance(error, QuotaExhaustedException):
        raise HTTPException(status_code=402, detail="所有账号配额已耗尽，请稍后重试")
    elif isinstance(error, AccountSuspendedException):
        raise HTTPException(status_code=403, detail="所有账号均已被封禁,请联系管理员")
    elif isinstance(error, AccountUnauthorizedException):
        raise HTTPException(status_code=403, detail="所有账号认证失败/Token过期,请联系管理员")
    elif isinstance(error, httpx.HTTPError):
        status_code = getattr(error, 'status_code', 502)
        raise HTTPException(status_code=status_code, detail=str(error))
    elif isinstance(error, HTTPException):
        raise error
    else:
        raise error


# ==============================================================================
# 非流式请求处理
# ==============================================================================

async def _handle_non_streaming_tooling_request(
    ctx: RetryContext,
    req: "ChatCompletionRequest",
    model: str,
    session_key: str,
    send_upstream_events: Callable,
    logger: logging.Logger
) -> JSONResponse:
    """处理非流式工具调用请求"""
    last_error: Optional[Exception] = None

    for attempt in range(ctx.max_retries):
        try:
            prompt_text = "".join([m.content for m in req.messages if isinstance(m.content, str)])
            prompt_tokens = count_tokens(prompt_text)

            tracker, event_stream = await send_upstream_events()

            stripper = _ThinkingStripper()
            completion_text_parts: List[str] = []
            tool_order: List[str] = []
            tool_state: Dict[str, Dict[str, Any]] = {}
            tool_names: Dict[str, str] = {}

            async for event_type, payload in event_stream:
                if event_type == "assistantResponseEvent":
                    content = payload.get("content", "")
                    if isinstance(content, str) and content:
                        completion_text_parts.append(stripper.feed(content))
                elif event_type == "toolUseEvent":
                    tool_use_id = payload.get("toolUseId")
                    tool_name = payload.get("name")
                    tool_input = payload.get("input")
                    if not isinstance(tool_use_id, str):
                        continue
                    if isinstance(tool_name, str) and tool_name:
                        tool_names[tool_use_id] = tool_name
                    resolved_name = tool_names.get(tool_use_id)
                    if not resolved_name:
                        continue
                    if tool_use_id not in tool_order:
                        tool_order.append(tool_use_id)
                    idx = tool_order.index(tool_use_id)
                    state = tool_state.setdefault(tool_use_id, {"index": idx, "name": resolved_name, "args": ""})
                    fragment = tool_input if isinstance(tool_input, str) else json.dumps(tool_input or {}, ensure_ascii=False)
                    state["args"] += fragment
                elif event_type == "assistantResponseEnd":
                    break

            completion_text = "".join([t for t in completion_text_parts if t])
            tool_calls = []
            for tool_use_id in tool_order:
                st = tool_state.get(tool_use_id) or {}
                tool_calls.append({
                    "id": tool_use_id,
                    "type": "function",
                    "function": {"name": st.get("name"), "arguments": st.get("args", "")},
                })

            completion_tokens = count_tokens(completion_text) + count_tokens("".join([c["function"]["arguments"] for c in tool_calls]))
            finish_reason = "tool_calls" if tool_calls else "stop"
            await update_account_stats(ctx.account["id"], bool(completion_text) or bool(tool_calls))

            if tracker and getattr(tracker, "has_content", False):
                try:
                    await SessionService.bind_session_account(session_key, ctx.account["id"])
                except Exception as exc:
                    logger.debug("会话绑定失败, 已忽略: %s", exc)

            return JSONResponse(content=_openai_non_streaming_response(
                completion_text,
                model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                tool_calls=tool_calls or None,
                finish_reason=finish_reason,
            ))
        except (QuotaExhaustedException, AccountSuspendedException, AccountUnauthorizedException, httpx.HTTPError) as e:
            last_error = e
            should_retry = await _handle_retry_error(ctx, e, attempt, logger)
            if should_retry:
                continue
            _raise_final_error(e)
        except Exception as e:
            last_error = e
            should_retry = await _handle_retry_error(ctx, e, attempt, logger)
            if should_retry:
                continue
            raise

    if last_error:
        _raise_final_error(last_error)
    raise HTTPException(status_code=500, detail="Unexpected error in retry loop")


async def _handle_non_streaming_request(
    ctx: RetryContext,
    req: "ChatCompletionRequest",
    model: str,
    session_key: str,
    send_upstream: Callable,
    logger: logging.Logger
) -> JSONResponse:
    """处理非流式普通请求"""
    last_error: Optional[Exception] = None

    for attempt in range(ctx.max_retries):
        try:
            prompt_text = "".join([m.content for m in req.messages if isinstance(m.content, str)])
            prompt_tokens = count_tokens(prompt_text)

            text, _, tracker = await send_upstream(stream=False)
            stripper = _ThinkingStripper()
            text = stripper.feed(text or "")
            await update_account_stats(ctx.account["id"], bool(text))

            # 成功后绑定会话到账号
            if text:
                try:
                    await SessionService.bind_session_account(session_key, ctx.account["id"])
                except Exception as exc:
                    logger.debug("会话绑定失败, 已忽略: %s", exc)

            completion_tokens = count_tokens(text or "")
            logger.info(f"OpenAI响应完成 账号={ctx.account['id'][:8]} | 模型={req.model or 'default'} | 状态=成功 | tokens={completion_tokens}")

            return JSONResponse(content=_openai_non_streaming_response(
                text or "",
                model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens
            ))
        except (QuotaExhaustedException, AccountSuspendedException, AccountUnauthorizedException, httpx.HTTPError) as e:
            last_error = e
            should_retry = await _handle_retry_error(ctx, e, attempt, logger)
            if should_retry:
                continue
            _raise_final_error(e)
        except Exception as e:
            last_error = e
            should_retry = await _handle_retry_error(ctx, e, attempt, logger)
            if should_retry:
                continue
            raise

    if last_error:
        _raise_final_error(last_error)
    raise HTTPException(status_code=500, detail="Unexpected error in retry loop")


# ==============================================================================
# 流式请求处理
# ==============================================================================

async def _prepare_streaming_resources(
    ctx: RetryContext,
    req: "ChatCompletionRequest",
    use_tooling: bool,
    send_upstream: Callable,
    send_upstream_events: Callable,
    logger: logging.Logger
) -> Tuple[Optional[AsyncGenerator], Optional[Any], Optional[Any], int]:
    """准备流式请求所需的资源，处理重试逻辑"""
    it = None
    tracker = None
    event_stream = None
    prompt_tokens = 0
    last_error: Optional[Exception] = None

    for attempt in range(ctx.max_retries):
        try:
            prompt_text = "".join([m.content for m in req.messages if isinstance(m.content, str)])
            prompt_tokens = count_tokens(prompt_text)

            if use_tooling:
                tracker, event_stream = await send_upstream_events()
            else:
                _, it, tracker = await send_upstream(stream=True)
                assert it is not None
            break
        except (QuotaExhaustedException, AccountSuspendedException, AccountUnauthorizedException, httpx.HTTPError) as e:
            last_error = e
            should_retry = await _handle_retry_error(ctx, e, attempt, logger)
            if should_retry:
                continue
            _raise_final_error(e)
        except Exception as e:
            last_error = e
            should_retry = await _handle_retry_error(ctx, e, attempt, logger)
            if should_retry:
                continue
            raise

    return it, tracker, event_stream, prompt_tokens

@router.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest, request: Request, account: Dict[str, Any] = Depends(require_account)):
    """
    OpenAI-compatible chat endpoint.

    重构说明: 使用统一的重试逻辑模块减少重复代码
    - RetryContext: 跟踪重试状态和已尝试的账号
    - _handle_retry_error: 统一处理重试错误
    - _handle_non_streaming_request: 处理非流式请求
    - _handle_non_streaming_tooling_request: 处理非流式工具调用
    - _prepare_streaming_resources: 准备流式请求资源
    """
    logger.info(f"OpenAI请求 | 模型={req.model or 'default'} | 流式={'是' if req.stream else '否'} | 账号={account['id'][:8]}***")

    # 在端点处理函数中进行消息压缩
    messages = _maybe_compress_messages(req.messages)

    end_user_id = (req.user or request.headers.get("x-end-user-id") or request.headers.get("x-user-id") or "").strip() or None

    # 准备账号（会话粘性）
    account = await _prepare_account_with_session(account, messages)

    # Normalize model name
    requested_model = (req.model or "").strip() or None
    model = map_model_to_amazonq(requested_model, default_model=AMAZON_Q_DEFAULT_MODEL)
    do_stream = bool(req.stream)
    session_key = SessionService.generate_session_key([m.model_dump() for m in messages], user_id=end_user_id)

    # 请求去重检查
    if request_dedupe.trace_enabled() or request_dedupe.dedupe_enabled():
        body = req.model_dump(exclude_none=True)
        fp = request_dedupe.fingerprint(body)
        sig = request_dedupe.fingerprint_drop(body, ("model",))
        if request_dedupe.trace_enabled():
            logger.info(request_dedupe.trace_line(request, "/v1/chat/completions", model, do_stream, fp) + f" sig={sig[:12]}")
        key_model = "*" if request_dedupe.ignore_model() else model
        key_fp = sig if request_dedupe.ignore_model() else fp
        key = request_dedupe.make_key(request, "/v1/chat/completions", key_model, key_fp)
        dup, retry_after_ms = request_dedupe.should_block(request, key)
        if dup:
            logger.warning("duplicate blocked path=/v1/chat/completions model=%s fp=%s retry_after_ms=%s", model, fp[:12], retry_after_ms)
            retry_after_s = max(1, int(((retry_after_ms or 0) + 999) / 1000))
            raise HTTPException(
                status_code=429,
                detail={"message": "Duplicate request blocked", "retry_after_ms": retry_after_ms, "fp": fp[:12]},
                headers={"Retry-After": str(retry_after_s)},
            )

    use_tooling = bool(req.tools or req.functions) or any((m.role == "tool") or bool(m.tool_calls) for m in messages)

    # 工具规范化辅助函数
    def _normalize_tool_choice(choice: Any) -> Any:
        if isinstance(choice, dict) and "name" in choice and "function" not in choice:
            name = choice.get("name")
            if isinstance(name, str) and name.strip():
                return {"type": "function", "function": {"name": name}}
        return choice

    def _normalize_openai_tools() -> Tuple[Optional[List[Dict[str, Any]]], Any]:
        tools = req.tools
        tool_choice = req.tool_choice
        if not tools and req.functions:
            tools = [{"type": "function", "function": f} for f in req.functions if isinstance(f, dict)]
            tool_choice = tool_choice if tool_choice is not None else req.function_call
        return tools, _normalize_tool_choice(tool_choice)

    def _build_raw_payload() -> Dict[str, Any]:
        msgs = [m.model_dump(exclude_none=True) for m in messages]
        tools, tool_choice = _normalize_openai_tools()
        try:
            return build_amazonq_request(msgs, model=requested_model, tools=tools, tool_choice=tool_choice)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    # 创建重试上下文
    ctx = RetryContext.create(account)

    # 上游请求函数（闭包捕获 ctx.account）
    async def _send_upstream_events() -> Tuple[Any, Any]:
        access = ctx.account.get("accessToken")
        if is_access_token_expired(ctx.account):
            refreshed = await refresh_access_token_in_db(ctx.account["id"])
            access = refreshed.get("accessToken")
            if not access:
                raise HTTPException(status_code=502, detail="Access token unavailable after refresh")
        client = get_client()
        resp = await send_chat_request(
            access_token=access,
            messages=[],
            model=model,
            stream=True,
            client=client,
            raw_payload=_build_raw_payload(),
        )
        if resp.event_stream is None:
            raise HTTPException(status_code=502, detail="No event stream returned")
        return resp.tracker, resp.event_stream

    async def _send_upstream(stream: bool):
        return await _execute_chat_request(ctx.account, messages, model, stream)

    # ==== 非流式请求处理 ====
    if not do_stream:
        if use_tooling:
            return await _handle_non_streaming_tooling_request(
                ctx, req, model, session_key, _send_upstream_events, logger
            )
        else:
            return await _handle_non_streaming_request(
                ctx, req, model, session_key, _send_upstream, logger
            )

    # ==== 流式请求处理 ====
    created = int(time.time())
    stream_id = f"chatcmpl-{uuid.uuid4()}"
    model_used = model or "unknown"

    # 准备流式资源
    it, tracker, event_stream, prompt_tokens = await _prepare_streaming_resources(
        ctx, req, use_tooling, _send_upstream, _send_upstream_events, logger
    )

    try:
        async def event_gen() -> AsyncGenerator[str, None]:
            completion_text = ""
            try:
                yield _sse_format({
                    "id": stream_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_used,
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                })

                tool_order: List[str] = []
                tool_index: Dict[str, int] = {}
                tool_args: Dict[str, str] = {}
                tool_names: Dict[str, str] = {}
                saw_tool = False

                if use_tooling:
                    assert event_stream is not None
                    stripper = _ThinkingStripper()
                    async for event_type, payload in event_stream:
                        if event_type == "assistantResponseEvent":
                            content = payload.get("content", "")
                            if isinstance(content, str) and content:
                                piece = stripper.feed(content)
                                if piece:
                                    completion_text += piece
                                    yield _sse_format({
                                        "id": stream_id,
                                        "object": "chat.completion.chunk",
                                        "created": created,
                                        "model": model_used,
                                        "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                                    })
                        elif event_type == "toolUseEvent":
                            tool_use_id = payload.get("toolUseId")
                            tool_name = payload.get("name")
                            tool_input = payload.get("input")
                            is_stop = payload.get("stop", False)
                            if not isinstance(tool_use_id, str):
                                continue
                            if isinstance(tool_name, str) and tool_name:
                                tool_names[tool_use_id] = tool_name
                            resolved_name = tool_names.get(tool_use_id)
                            if not resolved_name:
                                continue
                            idx = tool_index.get(tool_use_id)
                            if idx is None:
                                idx = len(tool_order)
                                tool_order.append(tool_use_id)
                                tool_index[tool_use_id] = idx
                                tool_args[tool_use_id] = ""
                                saw_tool = True
                                yield _sse_format({
                                    "id": stream_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": model_used,
                                    "choices": [{
                                        "index": 0,
                                        "delta": {
                                            "tool_calls": [{
                                                "index": idx,
                                                "id": tool_use_id,
                                                "type": "function",
                                                "function": {"name": resolved_name, "arguments": ""},
                                            }]
                                        },
                                        "finish_reason": None,
                                    }],
                                })
                            fragment = tool_input if isinstance(tool_input, str) else json.dumps(tool_input or {}, ensure_ascii=False)
                            if fragment:
                                tool_args[tool_use_id] = tool_args.get(tool_use_id, "") + fragment
                                yield _sse_format({
                                    "id": stream_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": model_used,
                                    "choices": [{
                                        "index": 0,
                                        "delta": {
                                            "tool_calls": [{
                                                "index": idx,
                                                "function": {"arguments": fragment},
                                            }]
                                        },
                                        "finish_reason": None,
                                    }],
                                })
                            if is_stop:
                                continue
                        elif event_type == "assistantResponseEnd":
                            break
                else:
                    assert it is not None
                    stripper = _ThinkingStripper()
                    async for piece in it:
                        if piece:
                            cleaned = stripper.feed(piece)
                            if cleaned:
                                completion_text += cleaned
                                yield _sse_format({
                                    "id": stream_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": model_used,
                                    "choices": [{"index": 0, "delta": {"content": cleaned}, "finish_reason": None}],
                                })

                completion_tokens = count_tokens(completion_text)
                if saw_tool:
                    completion_tokens += count_tokens("".join(tool_args.values()))
                finish_reason = "tool_calls" if saw_tool else "stop"
                yield _sse_format({
                    "id": stream_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_used,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": prompt_tokens + completion_tokens,
                    }
                })

                yield "data: [DONE]\n\n"
                await update_account_stats(ctx.account["id"], bool(completion_text) or bool(saw_tool))
                try:
                    await SessionService.bind_session_account(session_key, ctx.account["id"])
                except Exception as exc:
                    logger.debug("会话绑定失败, 已忽略: %s", exc)
            except GeneratorExit:
                await update_account_stats(ctx.account["id"], tracker.has_content if tracker else False)
            except Exception as exc:
                await update_account_stats(ctx.account["id"], tracker.has_content if tracker else False)
                raise

        return StreamingResponse(event_gen(), media_type="text/event-stream")
    except Exception as e:
        try:
            if it and hasattr(it, "aclose"):
                await it.aclose()
        except Exception as exc:
            logger.debug("关闭上游迭代器失败: %s", exc)
        await update_account_stats(ctx.account["id"], False)
        raise
