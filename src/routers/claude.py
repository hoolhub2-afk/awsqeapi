import json
import uuid
import random
import logging
import httpx
from typing import Dict, Any, Optional, Tuple, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse

from src.core.tokenizer import count_tokens
from src.core.http_client import get_client
import src.core.request_dedupe as request_dedupe
from src.services.account_service import (
    disable_account,
    refresh_access_token_in_db,
    is_access_token_expired,
    update_account_stats,
    list_enabled_accounts
)
from src.api.dependencies import require_account
from src.integrations.amazonq_client import (
    send_chat_request,
    QuotaExhaustedException,
    AccountSuspendedException,
    AccountUnauthorizedException,
    ChatResponse,
)
from src.integrations.claude.types import ClaudeRequest
from src.integrations.claude.converter import convert_claude_to_amazonq_request
from src.integrations.claude.stream import ClaudeStreamHandler
from src.core.model_mapping import map_model_to_amazonq
from src.core.config import AMAZON_Q_DEFAULT_MODEL
from src.security.auth import secure_error_detail

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/v1/messages")
async def claude_messages(req: ClaudeRequest, request: Request, account: Dict[str, Any] = Depends(require_account)):
    """
    Claude-compatible messages endpoint.

    Note: upstream is always called in streaming mode to preserve full event structure
    (tool use, thinking deltas, etc). For non-streaming requests, the response is
    built by aggregating the SSE events.
    """
    requested_model = (req.model or "").strip() or None
    model = map_model_to_amazonq(requested_model, default_model=AMAZON_Q_DEFAULT_MODEL)

    header_conversation_id = request.headers.get("x-conversation-id")
    conversation_id = (header_conversation_id or req.conversation_id or "").strip() or str(uuid.uuid4())

    if request_dedupe.trace_enabled() or request_dedupe.dedupe_enabled():
        body = req.model_dump(exclude_none=True)
        fp = request_dedupe.fingerprint(body)
        sig = request_dedupe.fingerprint_drop(body, ("model",))
        if request_dedupe.trace_enabled():
            logger.info(request_dedupe.trace_line(request, "/v1/messages", model, req.stream, fp) + f" sig={sig[:12]}")
        key_model = "*" if request_dedupe.ignore_model() else model
        key_fp = sig if request_dedupe.ignore_model() else fp
        key = request_dedupe.make_key(request, "/v1/messages", key_model, key_fp)
        dup, retry_after_ms = request_dedupe.should_block(request, key)
        if dup:
            logger.warning("duplicate blocked path=/v1/messages model=%s fp=%s retry_after_ms=%s", model, fp[:12], retry_after_ms)
            retry_after_s = max(1, int(((retry_after_ms or 0) + 999) / 1000))
            return JSONResponse(
                status_code=429,
                content={"message": "Duplicate request blocked", "retry_after_ms": retry_after_ms, "fp": fp[:12]},
                headers={"Retry-After": str(retry_after_s)},
            )
    # 记录请求信息
    logger.info(f"Claude请求 | 模型={model} | 流式={'是' if req.stream else '否'} | 账号={account['id'][:8]}")

    # 1. Convert request
    try:
        aq_request = convert_claude_to_amazonq_request(req, conversation_id=conversation_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 2. Send upstream
    async def _send_upstream_raw() -> Tuple[Optional[str], Optional[AsyncGenerator[str, None]], Any, Optional[AsyncGenerator[Any, None]]]:
        access = account.get("accessToken")
        if is_access_token_expired(account):
            refreshed = await refresh_access_token_in_db(account["id"])
            access = refreshed.get("accessToken")
            if not access:
                raise HTTPException(status_code=502, detail="Access token unavailable after refresh")
        
        client = get_client()
        return await send_chat_request(
            access_token=access,
            messages=[], # Not used when raw_payload is present
            model=model,
            stream=True,
            client=client,
            raw_payload=aq_request
        )

    # Retry logic
    max_retries = 3
    last_error = None
    tried_accounts = {account["id"]}

    tracker = None
    event_stream = None
    first_event: Optional[Tuple[str, Any]] = None

    for attempt in range(max_retries):
        try:
            resp = await _send_upstream_raw()
            event_stream = resp.event_stream
            tracker = resp.tracker
            if not event_stream:
                raise HTTPException(status_code=502, detail="No event stream returned")

            try:
                first_event = await event_stream.__anext__()
            except StopAsyncIteration as e:
                raise HTTPException(status_code=502, detail="Empty response from upstream") from e
            except Exception as e:
                if hasattr(event_stream, "aclose"):
                    try:
                        await event_stream.aclose()
                    except Exception as close_exc:
                        logger.debug("关闭上游流失败: %s", close_exc)
                raise HTTPException(
                    status_code=502,
                    detail=secure_error_detail(e, default_message="Upstream error"),
                ) from e
            break
        except QuotaExhaustedException as e:
            await update_account_stats(account["id"], False, is_throttled=True, quota_exhausted=True)
            last_error = e

            # 配额耗尽时尝试切换账号
            if attempt < max_retries - 1:
                candidates = await list_enabled_accounts()
                available = [acc for acc in candidates if acc["id"] not in tried_accounts]

                if available:
                    account = random.choice(available)
                    tried_accounts.add(account["id"])
                    continue

            # 所有账号都配额耗尽，返回402
            raise HTTPException(status_code=402, detail="所有账号配额已耗尽，请稍后重试")
        except AccountSuspendedException as e:
            # 账号被封禁,自动禁用并尝试切换
            await disable_account(account["id"], "suspended")
            last_error = e

            if attempt < max_retries - 1:
                candidates = await list_enabled_accounts()
                available = [acc for acc in candidates if acc["id"] not in tried_accounts]

                if available:
                    account = random.choice(available)
                    tried_accounts.add(account["id"])
                    logger.info(f"账号被封禁,切换到账号 {account['id'][:8]}")
                    continue

            # 所有账号都被封禁
            raise HTTPException(status_code=403, detail="所有账号均已被封禁,请联系管理员")
        except AccountUnauthorizedException as e:
            await disable_account(account["id"], "unauthorized")
            last_error = e

            if attempt < max_retries - 1:
                candidates = await list_enabled_accounts()
                available = [acc for acc in candidates if acc["id"] not in tried_accounts]

                if available:
                    account = random.choice(available)
                    tried_accounts.add(account["id"])
                    logger.info(f"账号认证失败/Token过期,切换到账号 {account['id'][:8]}")
                    continue

            raise HTTPException(status_code=403, detail="所有账号认证失败/Token过期,请联系管理员")
        except Exception as e:
            await update_account_stats(account["id"], False)
            last_error = e
            logger.error(f"Claude请求失败 账号={account['id'][:8]} | 错误={type(e).__name__}: {str(e)[:200]}")

            # 检查是否是客户端错误(400系列)，直接返回给用户
            if isinstance(e, httpx.HTTPError) and hasattr(e, 'status_code'):
                if 400 <= e.status_code < 500:
                    raise HTTPException(status_code=e.status_code, detail=str(e))

            # 处理网络连接错误，立即重试
            if "ConnectError" in str(type(e)) and attempt < max_retries - 1:
                logger.warning(f"网络连接失败，正在重试... (尝试 {attempt + 1}/{max_retries})")
                continue

            if "429" in str(e) and attempt < max_retries - 1:
                candidates = await list_enabled_accounts()
                available = [acc for acc in candidates if acc["id"] not in tried_accounts]

                if available:
                    # Update local account variable for next iteration
                    # Note: 'account' is a local var here, safe to update
                    account = random.choice(available)
                    tried_accounts.add(account["id"])
                    continue

            # 如果不是429错误或已无可用账号,立即抛出
            if "429" not in str(e):
                raise

    if not event_stream:
        if last_error:
            raise last_error
        raise HTTPException(status_code=502, detail="No event stream returned")
    if first_event is None:
        raise HTTPException(status_code=502, detail="Empty response from upstream")

    # Calculate input tokens
    text_to_count = ""
    if req.system:
        if isinstance(req.system, str):
            text_to_count += req.system
        elif isinstance(req.system, list):
            for item in req.system:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_to_count += item.get("text", "")

    for msg in req.messages:
        if isinstance(msg.content, str):
            text_to_count += msg.content
        elif isinstance(msg.content, list):
            for item in msg.content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_to_count += item.get("text", "")

    input_tokens = count_tokens(text_to_count, apply_multiplier=True)
    handler = ClaudeStreamHandler(model=model, input_tokens=input_tokens)

    async def event_generator():
        event_count = 0
        try:
            if first_event is not None:
                event_type, payload = first_event
                async for sse in handler.handle_event(event_type, payload):
                    event_count += 1
                    yield sse
            async for event_type, payload in event_stream:
                async for sse in handler.handle_event(event_type, payload):
                    event_count += 1
                    yield sse
            async for sse in handler.finish():
                event_count += 1
                yield sse
            logger.info(f"Claude响应完成 账号={account['id'][:8]} | 模型={model} | 状态=成功 | 事件数={event_count}")
            await update_account_stats(account["id"], True)
        except GeneratorExit:
            logger.warning(f"客户端断开连接: 账号={account['id'][:8]}, 事件数={event_count}")
            await update_account_stats(account["id"], tracker.has_content if tracker else False)
        except Exception as e:
            # 检查是否是连接中断错误且已经有内容输出
            is_connection_error = any(keyword in str(e).lower() for keyword in [
                'peer closed connection', 'incomplete chunked read', 'remoteprotocolerror'
            ])

            if is_connection_error and event_count > 0:
                logger.warning(f"流式响应连接中断但已有内容: 账号={account['id'][:8]}, 事件数={event_count}, 错误={e}")
                await update_account_stats(account["id"], True)  # 标记为成功，因为已经有内容输出
            else:
                logger.error(f"流式响应错误: 账号={account['id'][:8]}, 事件数={event_count}, 错误={e}", exc_info=True)
                await update_account_stats(account["id"], tracker.has_content if tracker else False)
        finally:
            if hasattr(event_stream, "aclose"):
                try:
                    await event_stream.aclose()
                except Exception as close_exc:
                    logger.debug("关闭上游流失败: %s", close_exc)

    if req.stream:
        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"X-Conversation-Id": conversation_id, "X-ConversationId": conversation_id},
        )
    else:
        # Accumulate for non-streaming
        final_content = []
        usage = {"input_tokens": 0, "output_tokens": 0}
        stop_reason = None

        async for sse_line in event_generator():
            for line in sse_line.splitlines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    continue
                try:
                    data = json.loads(data_str)
                except ValueError:
                    continue
                dtype = data.get("type")
                if dtype == "content_block_start":
                    idx = data.get("index", 0)
                    while len(final_content) <= idx:
                        final_content.append(None)
                    final_content[idx] = data.get("content_block")
                    if final_content[idx] and final_content[idx].get("type") == "thinking" and "thinking" not in final_content[idx]:
                        final_content[idx]["thinking"] = ""
                elif dtype == "content_block_delta":
                    idx = data.get("index", 0)
                    delta = data.get("delta", {})
                    while len(final_content) <= idx:
                        final_content.append(None)
                    if final_content[idx]:
                        if delta.get("type") == "text_delta":
                            final_content[idx]["text"] += delta.get("text", "")
                        elif delta.get("type") == "thinking_delta":
                            if "thinking" not in final_content[idx]:
                                final_content[idx]["thinking"] = ""
                            final_content[idx]["thinking"] += delta.get("thinking", "")
                        elif delta.get("type") == "input_json_delta":
                            if "partial_json" not in final_content[idx]:
                                final_content[idx]["partial_json"] = ""
                            final_content[idx]["partial_json"] += delta.get("partial_json", "")
                elif dtype == "content_block_stop":
                    idx = data.get("index", 0)
                    while len(final_content) <= idx:
                        final_content.append(None)
                    if final_content[idx] and final_content[idx]["type"] == "tool_use":
                        if "partial_json" in final_content[idx]:
                            try:
                                final_content[idx]["input"] = json.loads(final_content[idx]["partial_json"])
                            except Exception as exc:
                                logger.debug("工具输入 JSON 解析失败: %s", exc)
                            del final_content[idx]["partial_json"]
                elif dtype == "message_delta":
                    usage = data.get("usage", usage)
                    stop_reason = data.get("delta", {}).get("stop_reason")

        response_body = {
            "id": f"msg_{uuid.uuid4()}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "conversation_id": conversation_id,
            "conversationId": conversation_id,
            "content": [c for c in final_content if c is not None],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": usage
        }
        return JSONResponse(
            content=response_body,
            headers={"X-Conversation-Id": conversation_id, "X-ConversationId": conversation_id},
        )

@router.post("/v1/messages/count_tokens")
async def count_tokens_endpoint(req: ClaudeRequest, request: Request):
    """
    Count tokens in a message without sending it.
    """
    text_to_count = ""
    
    if req.system:
        if isinstance(req.system, str):
            text_to_count += req.system
        elif isinstance(req.system, list):
            for item in req.system:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_to_count += item.get("text", "")
    
    for msg in req.messages:
        if isinstance(msg.content, str):
            text_to_count += msg.content
        elif isinstance(msg.content, list):
            for item in msg.content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_to_count += item.get("text", "")
    
    if req.tools:
        text_to_count += json.dumps([tool.model_dump() for tool in req.tools], ensure_ascii=False)

    if request_dedupe.trace_enabled() or request_dedupe.dedupe_enabled():
        body = req.model_dump(exclude_none=True)
        fp = request_dedupe.fingerprint(body)
        sig = request_dedupe.fingerprint_drop(body, ("model",))
        if request_dedupe.trace_enabled():
            logger.info(request_dedupe.trace_line(request, "/v1/messages/count_tokens", req.model, req.stream, fp) + f" sig={sig[:12]}")
        key_model = "*" if request_dedupe.ignore_model() else req.model
        key_fp = sig if request_dedupe.ignore_model() else fp
        key = request_dedupe.make_key(request, "/v1/messages/count_tokens", key_model, key_fp)
        dup, retry_after_ms = request_dedupe.should_block(request, key)
        if dup:
            logger.warning("duplicate blocked path=/v1/messages/count_tokens model=%s fp=%s retry_after_ms=%s", req.model, fp[:12], retry_after_ms)
            retry_after_s = max(1, int(((retry_after_ms or 0) + 999) / 1000))
            return JSONResponse(
                status_code=429,
                content={"message": "Duplicate request blocked", "retry_after_ms": retry_after_ms, "fp": fp[:12]},
                headers={"Retry-After": str(retry_after_s)},
            )
    
    input_tokens = count_tokens(text_to_count, apply_multiplier=True)
    
    return {"input_tokens": input_tokens}
