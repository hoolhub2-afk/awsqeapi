import json
import uuid
import os
import asyncio
import logging
from contextlib import aclosing
from typing import Dict, Optional, Tuple, List, AsyncGenerator, Any
import struct
import httpx
import importlib.util
from pathlib import Path
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# 从统一配置中心导入上游 URL 配置
from src.core.config import (
    AMAZON_Q_ENDPOINT,
    AMAZON_Q_TARGET,
    AMAZON_Q_USER_AGENT,
    AMAZON_Q_X_AMZ_USER_AGENT,
    AMAZON_Q_OPTOUT,
    AMAZON_Q_DEFAULT_MODEL,
    AMAZON_Q_CLIENT_OS,
    AMAZON_Q_CLIENT_CWD,
)
from src.core.model_mapping import map_model_to_amazonq


class QuotaExhaustedException(Exception):
    """配额耗尽异常"""
    pass

class AccountSuspendedException(Exception):
    """账号被封禁异常"""
    pass

def _load_claude_parser():
    """Dynamically load claude_parser module."""
    base_dir = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("v2_claude_parser", str(base_dir / "claude" / "parser.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

try:
    _parser = _load_claude_parser()
    EventStreamParser = _parser.EventStreamParser
    extract_event_info = _parser.extract_event_info
except (OSError, ImportError, AttributeError, TypeError, ValueError, SyntaxError) as exc:
    logger.warning("Failed to load claude_parser module: %s", exc, exc_info=True)
    EventStreamParser = None
    extract_event_info = None

class StreamTracker:
    def __init__(self):
        self.has_content = False
    
    async def track(self, gen: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
        async for item in gen:
            if item:
                self.has_content = True
            yield item

def _get_proxies() -> Optional[Dict[str, str]]:
    proxy = os.getenv("HTTP_PROXY", "").strip()
    if proxy:
        return {"http": proxy, "https": proxy}
    return None

# 使用统一配置
DEFAULT_MODEL = AMAZON_Q_DEFAULT_MODEL
DEFAULT_ENV_STATE = {
    "operatingSystem": AMAZON_Q_CLIENT_OS,
    "currentWorkingDirectory": AMAZON_Q_CLIENT_CWD
}

def _is_quota_exhausted_error(error_text: str) -> bool:
    """检测是否是配额耗尽错误"""
    if not error_text:
        return False

    lowered = error_text.lower()
    if "rate limit exceeded" in lowered:
        return True
    if "MONTHLY_REQUEST_COUNT" in error_text:
        return True

    try:
        if "ThrottlingException" in error_text and "MONTHLY_REQUEST_COUNT" in error_text:
            return True
        err_json = json.loads(error_text)
        if isinstance(err_json, dict):
            if err_json.get("reason") == "MONTHLY_REQUEST_COUNT":
                return True
            type_val = err_json.get("__type") or ""
            return ("ThrottlingException" in type_val and err_json.get("reason") == "MONTHLY_REQUEST_COUNT")
    except ValueError as exc:
        logger.debug("quota 错误 JSON 解析失败: %s", exc)
    return False

def _is_account_suspended_error(error_text: str) -> bool:
    """检测账号是否被封禁(只匹配明确封禁原因, 避免将普通 403/AccessDenied 误判为封禁)."""
    if not error_text:
        return False
    if "TEMPORARILY_SUSPENDED" in error_text:
        return True
    lowered = error_text.lower()
    if any(k in lowered for k in ("account suspended", "account disabled", "account blocked")):
        return True
    try:
        err_json = json.loads(error_text)
    except ValueError as exc:
        logger.debug("suspend 错误 JSON 解析失败: %s", exc)
        return False

    return isinstance(err_json, dict) and err_json.get("reason") == "TEMPORARILY_SUSPENDED"

def _convert_openai_tool_to_amazonq(tool: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(tool, dict) or tool.get("type") != "function":
        return None
    fn = tool.get("function") or {}
    name = fn.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    desc = fn.get("description") or ""
    if isinstance(desc, str) and len(desc) > 10240:
        desc = desc[:10100] + "\n\n...(truncated)"
    schema = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {}
    return {"toolSpecification": {"name": name, "description": desc or "", "inputSchema": {"json": schema}}}

def _apply_openai_tool_choice(tools: List[Dict[str, Any]], tool_choice: Any) -> List[Dict[str, Any]]:
    if tool_choice is None or tool_choice == "auto" or tool_choice == "required":
        return tools
    if tool_choice == "none":
        return []
    if isinstance(tool_choice, dict):
        fn = (tool_choice.get("function") or {}).get("name")
        if isinstance(fn, str) and fn.strip():
            return [t for t in tools if ((t.get("function") or {}).get("name") == fn)]
    return tools

def _openai_tool_results_from_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        tool_id = msg.get("tool_call_id") or msg.get("toolCallId")
        if not isinstance(tool_id, str) or not tool_id.strip():
            continue
        text = _normalize_openai_content(msg.get("content", ""))
        results.append({"toolUseId": tool_id, "content": [{"text": text}], "status": "success"})
    return results

def _merge_tool_results_by_tool_use_id(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in results:
        tid = item.get("toolUseId")
        if not isinstance(tid, str) or not tid:
            merged.append(item)
            continue
        existing = by_id.get(tid)
        if existing is None:
            copied = dict(item)
            by_id[tid] = copied
            merged.append(copied)
            continue
        existing_content = existing.get("content")
        if not isinstance(existing_content, list):
            existing["content"] = [] if existing_content is None else [existing_content]
        incoming = item.get("content") or []
        if isinstance(incoming, list):
            existing["content"].extend(incoming)
        else:
            existing["content"].append(incoming)
    return merged

def _amazonq_tool_uses_from_openai_assistant(msg: Dict[str, Any]) -> List[Dict[str, Any]]:
    tool_calls = msg.get("tool_calls") or msg.get("toolCalls") or []
    if not isinstance(tool_calls, list):
        return []
    uses: List[Dict[str, Any]] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        tool_id = call.get("id")
        fn = call.get("function") or {}
        name = fn.get("name")
        args = fn.get("arguments", "")
        if not isinstance(tool_id, str) or not isinstance(name, str) or not name:
            continue
        parsed: Any = args
        if isinstance(args, str) and args.strip().startswith("{"):
            try:
                parsed = json.loads(args)
            except ValueError:
                parsed = args
        uses.append({"toolUseId": tool_id, "name": name, "input": parsed if parsed is not None else {}})
    return uses

def _extract_tool_use_ids_from_item(item: Dict[str, Any]) -> List[str]:
    if "assistantResponseMessage" in item:
        tool_uses = (item.get("assistantResponseMessage") or {}).get("toolUses") or []
        return [u.get("toolUseId") for u in tool_uses if isinstance(u, dict) and u.get("toolUseId")]
    if "userInputMessage" in item:
        ctx = (item.get("userInputMessage") or {}).get("userInputMessageContext") or {}
        tool_results = ctx.get("toolResults") or []
        return [r.get("toolUseId") for r in tool_results if isinstance(r, dict) and r.get("toolUseId")]
    return []

def _validate_tool_results_follow_tool_uses(history: List[Dict[str, Any]]) -> None:
    last_tool_use_ids: List[str] = []
    has_assistant = False
    for idx, item in enumerate(history):
        if "assistantResponseMessage" in item:
            has_assistant = True
            last_tool_use_ids = _extract_tool_use_ids_from_item(item)
            continue
        if "userInputMessage" not in item:
            continue
        tool_result_ids = _extract_tool_use_ids_from_item(item)
        if not tool_result_ids:
            continue
        ok = has_assistant and last_tool_use_ids and all(tid in last_tool_use_ids for tid in tool_result_ids)
        if not ok:
            raise ValueError(f"toolResults must follow assistant toolUses: idx={idx}, toolUseIds={tool_result_ids}, prev_toolUses={last_tool_use_ids}")

def _validate_history_alternation(history: List[Dict[str, Any]]) -> None:
    last_role = None
    for idx, item in enumerate(history):
        role = "assistant" if "assistantResponseMessage" in item else "user" if "userInputMessage" in item else None
        if not role:
            continue
        if last_role == role:
            raise ValueError(f"history alternation violated: idx={idx}, role={role}")
        last_role = role


async def check_account_status(access_token: str, client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    """
    检查账号状态的轻量级函数
    返回账号的真实状态信息
    """
    # 构建一个最小的测试请求，使用正确的格式
    test_payload = {
        "conversationState": {
            "conversationId": str(uuid.uuid4()),
            "history": [],
            "currentMessage": {
                "userInputMessage": {
                    "content": "test",
                    "userInputMessageContext": {
                        "envState": dict(DEFAULT_ENV_STATE),
                        "tools": []
                    },
                    "origin": "KIRO_CLI"
                }
            },
            "chatTriggerType": "MANUAL"
        }
    }

    headers = _build_amazonq_headers(access_token)
    payload_str = json.dumps(test_payload, ensure_ascii=False)

    local_client = False
    if client is None:
        local_client = True
        proxies = _get_proxies()
        mounts = None
        if proxies:
            proxy_url = proxies.get("https") or proxies.get("http")
            if proxy_url:
                mounts = {
                    "https://": httpx.AsyncHTTPTransport(proxy=proxy_url),
                    "http://": httpx.AsyncHTTPTransport(proxy=proxy_url),
                }
        client = httpx.AsyncClient(
            mounts=mounts,
            timeout=httpx.Timeout(10.0, read=30.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
        )

    try:
        response = await client.post(AMAZON_Q_ENDPOINT, headers=headers, content=payload_str)

        if response.status_code == 200:
            return {
                "status": "active",
                "message": "账号正常",
                "quota_available": True,
                "response_code": 200
            }
        elif response.status_code == 429:
            # 检查是否是配额耗尽
            error_text = response.text
            if _is_quota_exhausted_error(error_text):
                return {
                    "status": "quota_exhausted",
                    "message": "配额已耗尽",
                    "quota_available": False,
                    "response_code": 429
                }
            else:
                return {
                    "status": "rate_limited",
                    "message": "请求频率限制",
                    "quota_available": True,
                    "response_code": 429
                }
        elif response.status_code in [401, 403]:
            # 检查是否是账号被封禁
            error_text = response.text
            if _is_account_suspended_error(error_text):
                return {
                    "status": "suspended",
                    "message": "账号被封禁",
                    "quota_available": False,
                    "response_code": response.status_code
                }
            else:
                return {
                    "status": "unauthorized",
                    "message": "认证失败",
                    "quota_available": False,
                    "response_code": response.status_code
                }
        else:
            return {
                "status": "error",
                "message": f"未知错误 (HTTP {response.status_code})",
                "quota_available": None,
                "response_code": response.status_code
            }

    except httpx.TimeoutException:
        return {
            "status": "timeout",
            "message": "请求超时",
            "quota_available": None,
            "response_code": None
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"检查失败: {str(e)}",
            "quota_available": None,
            "response_code": None
        }
    finally:
        if local_client and client:
            await client.aclose()


def _build_amazonq_headers(bearer_token: str) -> Dict[str, str]:
    return {
        "content-type": "application/x-amz-json-1.0",
        "x-amz-target": AMAZON_Q_TARGET,
        "user-agent": AMAZON_Q_USER_AGENT,
        "x-amz-user-agent": AMAZON_Q_X_AMZ_USER_AGENT,
        "x-amzn-codewhisperer-optout": AMAZON_Q_OPTOUT,
        "amz-sdk-request": "attempt=1; max=3",
        "amz-sdk-invocation-id": str(uuid.uuid4()),
        "Authorization": f"Bearer {bearer_token}",
    }

def _parse_event_headers(raw: bytes) -> Dict[str, object]:
    headers: Dict[str, object] = {}
    i = 0
    n = len(raw)
    while i < n:
        if i + 1 > n:
            break
        name_len = raw[i]
        i += 1
        if i + name_len + 1 > n:
            break
        name = raw[i : i + name_len].decode("utf-8", errors="ignore")
        i += name_len
        htype = raw[i]
        i += 1
        if htype == 0:
            val = True
        elif htype == 1:
            val = False
        elif htype == 2:
            if i + 1 > n: break
            val = raw[i]; i += 1
        elif htype == 3:
            if i + 2 > n: break
            val = int.from_bytes(raw[i:i+2],"big",signed=True); i += 2
        elif htype == 4:
            if i + 4 > n: break
            val = int.from_bytes(raw[i:i+4],"big",signed=True); i += 4
        elif htype == 5:
            if i + 8 > n: break
            val = int.from_bytes(raw[i:i+8],"big",signed=True); i += 8
        elif htype == 6:
            if i + 2 > n: break
            l = int.from_bytes(raw[i:i+2],"big"); i += 2
            if i + l > n: break
            val = raw[i:i+l]; i += l
        elif htype == 7:
            if i + 2 > n: break
            l = int.from_bytes(raw[i:i+2],"big"); i += 2
            if i + l > n: break
            val = raw[i:i+l].decode("utf-8", errors="ignore"); i += l
        elif htype == 8:
            if i + 8 > n: break
            val = int.from_bytes(raw[i:i+8],"big",signed=False); i += 8
        elif htype == 9:
            if i + 16 > n: break
            import uuid as _uuid
            val = str(_uuid.UUID(bytes=bytes(raw[i:i+16]))); i += 16
        else:
            break
        headers[name] = val
    return headers

class AwsEventStreamParser:
    def __init__(self):
        self._buf = bytearray()
    def feed(self, data: bytes) -> List[Tuple[Dict[str, object], bytes]]:
        if not data:
            return []
        self._buf.extend(data)
        out: List[Tuple[Dict[str, object], bytes]] = []
        while True:
            if len(self._buf) < 12:
                break
            total_len, headers_len, _prelude_crc = struct.unpack(">I I I", self._buf[:12])
            if total_len < 16 or headers_len > total_len:
                self._buf.pop(0)
                continue
            if len(self._buf) < total_len:
                break
            msg = bytes(self._buf[:total_len])
            del self._buf[:total_len]
            headers_raw = msg[12:12+headers_len]
            payload = msg[12+headers_len: total_len-4]
            headers = _parse_event_headers(headers_raw)
            out.append((headers, payload))
        return out

def _try_decode_event_payload(payload: bytes) -> Optional[dict]:
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None

def _extract_text_from_event(ev: dict) -> Optional[str]:
    for key in ("assistantResponseEvent","assistantMessage","message","delta","data"):
        if key in ev and isinstance(ev[key], dict):
            inner = ev[key]
            if isinstance(inner.get("content"), str) and inner.get("content"):
                return inner["content"]
    if isinstance(ev.get("content"), str) and ev.get("content"):
        return ev["content"]
    for list_key in ("chunks","content"):
        if isinstance(ev.get(list_key), list):
            buf = []
            for item in ev[list_key]:
                if isinstance(item, dict):
                    if isinstance(item.get("content"), str):
                        buf.append(item["content"])
                    elif isinstance(item.get("text"), str):
                        buf.append(item["text"])
                elif isinstance(item, str):
                    buf.append(item)
            if buf:
                return "".join(buf)
    for k in ("text","delta","payload"):
        v = ev.get(k)
        if isinstance(v, str) and v:
            return v
    return None

def _delta_by_prefix(previous: str, current: str) -> Tuple[str, str]:
    if not current:
        return previous, ""
    if not previous:
        return current, current
    if current.startswith(previous):
        return current, current[len(previous):]
    # If upstream is already sending deltas, keep small fragments as-is to avoid
    # accidentally removing intentional repetition.
    if len(current) < 32:
        return previous + current, current
    max_check = min(len(previous), len(current), 4096)
    for length in range(max_check, 0, -1):
        if previous.endswith(current[:length]):
            delta = current[length:]
            return previous + delta, delta
    return previous + current, current

async def _dedupe_assistant_content_events(
    events: AsyncGenerator[Tuple[str, Any], None],
) -> AsyncGenerator[Tuple[str, Any], None]:
    last_assistant_content = ""
    async for event_type, payload in events:
        if event_type == "assistantResponseEvent" and isinstance(payload, dict):
            content = payload.get("content")
            if isinstance(content, str) and content:
                last_assistant_content, delta = _delta_by_prefix(last_assistant_content, content)
                if not delta:
                    continue
                if delta != content:
                    payload = {**payload, "content": delta}
        yield event_type, payload

async def _ensure_initial_response_has_conversation_id(
    events: AsyncGenerator[Tuple[str, Any], None],
    conversation_id: Optional[str],
) -> AsyncGenerator[Tuple[str, Any], None]:
    async for event_type, payload in events:
        if event_type == "initial-response" and isinstance(payload, dict):
            if conversation_id and "conversationId" not in payload:
                payload = {**payload, "conversationId": conversation_id}
        yield event_type, payload

def _normalize_openai_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif "text" in block:
                    parts.append(str(block["text"]))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)

def _extract_openai_images(content: Any) -> List[Dict[str, Any]]:
    if not isinstance(content, list):
        return []

    images: List[Dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") not in ("image_url", "input_image", "image"):
            continue
        url = block.get("image_url") or block.get("url")
        if isinstance(url, dict):
            url = url.get("url")
        if not isinstance(url, str) or "base64," not in url:
            continue
        header, b64 = url.split("base64,", 1)
        media_type = "image/png"
        if header.startswith("data:"):
            media_type = header[5:].split(";", 1)[0] or media_type
        fmt = media_type.split("/", 1)[1] if "/" in media_type else "png"
        images.append({"format": fmt, "source": {"bytes": b64}})
    return images

def _data_url_to_image(url: str) -> Optional[Dict[str, Any]]:
    if not isinstance(url, str) or "base64," not in url or not url.startswith("data:image/"):
        return None
    header, b64 = url.split("base64,", 1)
    media_type = header[5:].split(";", 1)[0] if header.startswith("data:") else "image/png"
    fmt = media_type.split("/", 1)[1] if "/" in media_type else "png"
    return {"format": fmt, "source": {"bytes": b64}}

def _extract_openai_images_from_attachments(attachments: Any) -> List[Dict[str, Any]]:
    if not isinstance(attachments, list):
        return []
    images: List[Dict[str, Any]] = []
    for item in attachments:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("data_url")
        if isinstance(url, str):
            img = _data_url_to_image(url)
            if img:
                images.append(img)
                continue
        data = item.get("data") or item.get("base64") or item.get("bytes") or item.get("content")
        if isinstance(data, str) and data.startswith("data:image/"):
            img = _data_url_to_image(data)
            if img:
                images.append(img)
                continue
        mime = item.get("mime_type") or item.get("content_type") or item.get("media_type")
        if isinstance(data, str) and isinstance(mime, str) and mime.startswith("image/"):
            fmt = mime.split("/", 1)[1] if "/" in mime else "png"
            images.append({"format": fmt or "png", "source": {"bytes": data}})
    return images

def _extract_openai_images_from_message(msg: Dict[str, Any]) -> List[Dict[str, Any]]:
    content_images = _extract_openai_images(msg.get("content"))
    attachment_images = _extract_openai_images_from_attachments(msg.get("attachments"))
    return content_images + attachment_images

def _prune_images_to_last_two_user_messages(history_entries: List[Dict[str, Any]], current_payload: Dict[str, Any]) -> None:
    targets: List[Dict[str, Any]] = []
    for item in history_entries:
        user = item.get("userInputMessage")
        if isinstance(user, dict) and user.get("images"):
            targets.append(user)
    if current_payload.get("images"):
        targets.append(current_payload)
    for user in targets[:-2]:
        user.pop("images", None)

def _format_prompt(system_prompt: str, user_prompt: str) -> str:
    sections: List[str] = []
    if system_prompt.strip():
        sections.append(
            "--- SYSTEM PROMPT BEGIN ---\n"
            f"{system_prompt.strip()}\n"
            "--- SYSTEM PROMPT END ---"
        )
    sections.append(
        "--- USER MESSAGE BEGIN ---\n"
        f"{user_prompt.strip()}\n"
        "--- USER MESSAGE END ---"
    )
    return "\n\n".join(sections)

def _build_user_message(
    content: str,
    model: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    origin: str = "KIRO_CLI",
    images: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    ctx = {
        "envState": dict(DEFAULT_ENV_STATE),
        "tools": tools if tools is not None else []
    }
    message: Dict[str, Any] = {
        "content": content,
        "userInputMessageContext": ctx,
        "origin": origin
    }
    model_id = map_model_to_amazonq(model, default_model=DEFAULT_MODEL)
    if model_id:
        message["modelId"] = model_id
    if images:
        message["images"] = images
    return message

def _build_assistant_message(content: str) -> Dict[str, Any]:
    return {
        "assistantResponseMessage": {
            "messageId": str(uuid.uuid4()),
            "content": content
        }
    }

def _create_http_client(proxies: Optional[Dict[str, str]], timeout: Tuple[int, int]) -> httpx.AsyncClient:
    """创建 HTTP 客户端"""
    mounts = None
    if proxies:
        proxy_url = proxies.get("https") or proxies.get("http")
        if proxy_url:
            mounts = {
                "https://": httpx.AsyncHTTPTransport(proxy=proxy_url),
                "http://": httpx.AsyncHTTPTransport(proxy=proxy_url),
            }
    return httpx.AsyncClient(
        mounts=mounts,
        timeout=httpx.Timeout(timeout[0], read=timeout[1], connect=10.0),
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
    )

async def _send_request_with_retry(
    client: httpx.AsyncClient,
    request: httpx.Request,
    max_retries: int = 2
) -> httpx.Response:
    """发送请求，失败时重试"""
    for attempt in range(max_retries):
        try:
            resp = await client.send(request, stream=True)
            return resp
        except (httpx.RemoteProtocolError, httpx.ReadTimeout) as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(0.5)
                continue
            raise

async def _handle_error_response(
    resp: httpx.Response,
    local_client: bool,
    client: Optional[httpx.AsyncClient]
) -> None:
    """处理错误响应"""
    original_err = None
    try:
        raw = await resp.aread()
        err_text = raw.decode("utf-8", errors="ignore").strip()
        err = err_text or resp.text or f"HTTP {resp.status_code}"
        original_err = err
        
        try:
            err_json = json.loads(err)
            if isinstance(err_json, dict):
                err = json.dumps(err_json, ensure_ascii=False)
                reason = err_json.get("reason")
                if reason == "CONTENT_LENGTH_EXCEEDS_THRESHOLD":
                    err = "输入内容超过上游API限制,请减少对话历史或缩短消息内容"
                elif reason == "MONTHLY_REQUEST_COUNT":
                    err = "账号月度配额已耗尽,请等待下月重置或添加新账号"
                elif reason == "TEMPORARILY_SUSPENDED":
                    err = "账号已被临时封禁"
        except ValueError as exc:
            logger.debug("上游错误 JSON 解析失败: %s", exc)
    except httpx.HTTPError as exc:
        logger.debug("读取上游错误响应失败: %s", exc)
        err = f"HTTP {resp.status_code}"
        original_err = err
    
    await resp.aclose()
    if local_client and client:
        await client.aclose()
    
    check_err = original_err if original_err else err
    if _is_quota_exhausted_error(check_err):
        raise QuotaExhaustedException(err)
    if _is_account_suspended_error(check_err):
        raise AccountSuspendedException(err)
    raise HTTPException(status_code=resp.status_code, detail=err)

def build_amazonq_request(
    messages: List[Dict[str, Any]],
    model: Optional[str],
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Any = None,
) -> Dict[str, Any]:
    if not messages:
        raise ValueError("messages cannot be empty")

    system_prompts: List[str] = []
    conversation: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            system_prompts.append(_normalize_openai_content(msg.get("content", "")))
            continue
        conversation.append(msg)

    last_assistant_idx: Optional[int] = None
    for idx in range(len(conversation) - 1, -1, -1):
        if conversation[idx].get("role") == "assistant":
            last_assistant_idx = idx
            break
    prefix = conversation[: last_assistant_idx + 1] if last_assistant_idx is not None else []
    tail = conversation[last_assistant_idx + 1 :] if last_assistant_idx is not None else conversation

    if not any(m.get("role") == "user" for m in conversation):
        raise ValueError("at least one user message is required")

    openai_tools = tools or []
    if not isinstance(openai_tools, list):
        openai_tools = []
    openai_tools = _apply_openai_tool_choice(openai_tools, tool_choice)
    amazonq_tools = [t for t in (_convert_openai_tool_to_amazonq(x) for x in openai_tools) if t]

    image_contribs: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []

    history_entries: List[Dict[str, Any]] = []
    pending_user_messages: List[Dict[str, Any]] = []
    pending_tool_results: List[Dict[str, Any]] = []

    def _flush_pending_user() -> None:
        nonlocal pending_user_messages
        if not pending_user_messages:
            return
        merged_text = "\n\n".join([m.get("content", "") for m in pending_user_messages if m.get("content")])
        merged = _build_user_message(merged_text, model=model)
        history_entries.append({"userInputMessage": merged})
        pending_user_messages = []

    def _flush_pending_tool_results() -> None:
        nonlocal pending_tool_results
        if not pending_tool_results:
            return
        merged = _build_user_message("", model=model)
        merged["userInputMessageContext"]["toolResults"] = _merge_tool_results_by_tool_use_id(pending_tool_results)
        history_entries.append({"userInputMessage": merged})
        pending_tool_results = []

    for historic in prefix:
        role = historic.get("role")
        historic_content = historic.get("content", "")
        text = _normalize_openai_content(historic_content)
        images = _extract_openai_images_from_message(historic)
        if role == "assistant":
            _flush_pending_user()
            _flush_pending_tool_results()
            entry = _build_assistant_message(text)
            tool_uses = _amazonq_tool_uses_from_openai_assistant(historic)
            if tool_uses:
                entry["assistantResponseMessage"]["toolUses"] = tool_uses
            history_entries.append(entry)
        elif role == "user":
            _flush_pending_tool_results()
            user_msg = _build_user_message(text, model=model)
            pending_user_messages.append(user_msg)
            if images:
                image_contribs.append((user_msg, images))
        elif role == "tool":
            _flush_pending_user()
            pending_tool_results.extend(_openai_tool_results_from_messages([historic]))

    _flush_pending_user()
    _flush_pending_tool_results()

    tail_user_texts = []
    for msg in tail:
        if msg.get("role") != "user":
            continue
        c = msg.get("content", "")
        tail_user_texts.append(_normalize_openai_content(c))
        images = _extract_openai_images_from_message(msg)
        if images:
            # tail user messages are merged into current_payload; keep per-message ordering here
            image_contribs.append(({"__pending_current__": True}, images))

    tail_tool_results = _openai_tool_results_from_messages(tail)
    tail_tool_results = _merge_tool_results_by_tool_use_id(tail_tool_results)
    user_text = "\n".join([t for t in tail_user_texts if t]).strip()
    formatted = "" if (tail_tool_results and not user_text) else _format_prompt("\n".join([p for p in system_prompts if p]), user_text)
    current_payload = _build_user_message(formatted, model, tools=amazonq_tools or None)
    if tail_tool_results:
        current_payload["userInputMessageContext"]["toolResults"] = tail_tool_results

    # Re-bind tail image contributions to current payload, then prune globally to last 2 user messages with images.
    for idx, (target, imgs) in enumerate(list(image_contribs)):
        if "__pending_current__" in target:
            image_contribs[idx] = (current_payload, imgs)

    if image_contribs:
        for target, _ in image_contribs:
            target.pop("images", None)
        kept = image_contribs[-2:]
        for target, imgs in kept:
            target.setdefault("images", [])
            target["images"].extend(imgs)

    combined_history = list(history_entries)
    combined_history.append({"userInputMessage": current_payload})
    _validate_history_alternation(combined_history)
    _validate_tool_results_follow_tool_uses(combined_history)

    return {
        "conversationState": {
            "conversationId": str(uuid.uuid4()),
            "history": history_entries,
            "currentMessage": {
                "userInputMessage": current_payload
            },
            "chatTriggerType": "MANUAL"
        }
    }

async def send_chat_request(
    access_token: str,
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    stream: bool = False,
    timeout: Tuple[int,int] = (15,300),
    client: Optional[httpx.AsyncClient] = None,
    raw_payload: Optional[Dict[str, Any]] = None
) -> Tuple[Optional[str], Optional[AsyncGenerator[str, None]], StreamTracker, Optional[AsyncGenerator[Any, None]]]:
    """发送聊天请求到 Amazon Q
    
    重构说明: 拆分为多个子函数提高可维护性
    - _create_http_client: 创建 HTTP 客户端
    - _send_request_with_retry: 发送请求并重试
    - _handle_error_response: 处理错误响应
    - _create_safe_generators: 创建安全的生成器
    """
    # 1. 准备请求
    if raw_payload:
        body_json = raw_payload
        if "conversationState" in body_json and "conversationId" not in body_json["conversationState"]:
            body_json["conversationState"]["conversationId"] = str(uuid.uuid4())
    else:
        body_json = build_amazonq_request(messages, model)

    conversation_id = None
    try:
        cid = (body_json.get("conversationState") or {}).get("conversationId")
        if isinstance(cid, str) and cid.strip():
            conversation_id = cid
    except (AttributeError, TypeError):
        conversation_id = None

    payload_str = json.dumps(body_json, ensure_ascii=False)
    headers = _build_amazonq_headers(access_token)
    
    # 2. 准备客户端
    local_client = False
    if client is None:
        local_client = True
        client = _create_http_client(_get_proxies(), timeout)
    
    # 3. 发送请求
    req = client.build_request("POST", AMAZON_Q_ENDPOINT, headers=headers, content=payload_str)
    resp = await _send_request_with_retry(client, req)

    try:
        # 4. 处理错误响应
        if resp.status_code >= 400:
            await _handle_error_response(resp, local_client, client)
        
        parser = AwsEventStreamParser()
        tracker = StreamTracker()
        
        # Track if the response has been consumed to avoid double-close
        response_consumed = False
        
        async def _iter_events() -> AsyncGenerator[Any, None]:
            nonlocal response_consumed
            client_disconnected = False
            has_yielded = False
            try:
                if EventStreamParser and extract_event_info:
                    # Use proper EventStreamParser
                    bytes_received = 0
                    async def byte_gen():
                        nonlocal bytes_received
                        try:
                            async for chunk in resp.aiter_bytes():
                                if chunk:
                                    bytes_received += len(chunk)
                                    yield chunk
                        except (httpx.RemoteProtocolError, httpx.ReadError) as e:
                            # 服务端提前关闭连接，如果已经有内容则正常结束
                            if has_yielded:
                                return
                            else:
                                raise

                    message_count = 0
                    try:
                        async for message in EventStreamParser.parse_stream(byte_gen()):
                            message_count += 1
                            event_info = extract_event_info(message)
                            if event_info:
                                event_type = event_info.get('event_type')
                                payload = event_info.get('payload')
                                # 调试日志
                                if not event_type or not payload:
                                    logger.debug(f"消息缺少字段: event_type={event_type}, payload={payload}, headers={message.get('headers')}")
                                if event_type and payload:
                                    has_yielded = True
                                    yield (event_type, payload)
                    except (httpx.RemoteProtocolError, httpx.ReadError) as e:
                        # 如果已经产生了内容，则认为是正常的流结束
                        if has_yielded:
                            logger.debug("上游流异常但已有内容, 按正常结束处理: %s", e)
                        else:
                            raise
                else:
                    # Fallback to old parser
                    try:
                        async for chunk in resp.aiter_bytes():
                            if not chunk:
                                continue
                            events = parser.feed(chunk)
                            for ev_headers, payload in events:
                                parsed = _try_decode_event_payload(payload)
                                if parsed is not None:
                                    event_type = None
                                    if ":event-type" in ev_headers:
                                        event_type = ev_headers[":event-type"]
                                    has_yielded = True
                                    yield (event_type, parsed)
                    except (httpx.RemoteProtocolError, httpx.ReadError) as e:
                        # 如果已经产生了内容，则认为是正常的流结束
                        if has_yielded:
                            logger.debug("上游流异常但已有内容, 按正常结束处理: %s", e)
                        else:
                            raise

                # 如果没有产生任何事件,返回空占位,避免客户端因 502 断开
                if not has_yielded:
                    logger.warning("上游返回空响应流,返回空文本占位以保持连接")
                    has_yielded = True
                    yield ("textEvent", {"text": ""})
            except GeneratorExit:
                # Client disconnected - mark flag and re-raise to ensure proper cleanup
                client_disconnected = True
                raise
            except Exception as exc:
                if not tracker.has_content:
                    raise
                logger.debug("上游流异常但已有内容, 已忽略: %s", exc)
            finally:
                response_consumed = True
                try:
                    if resp and not resp.is_closed:
                        await resp.aclose()
                except Exception as e:
                    # Log but don't fail cleanup
                    logger.warning(f"Failed to close response: {e}")
                try:
                    if local_client and client:
                        await client.aclose()
                except Exception as e:
                    logger.warning(f"Failed to close client: {e}")

        async def _iter_text() -> AsyncGenerator[str, None]:
            last_text = ""
            async for event_type, parsed in _iter_events():
                text = _extract_text_from_event(parsed)
                if isinstance(text, str) and text:
                    last_text, delta = _delta_by_prefix(last_text, text)
                    if delta:
                        yield delta
        
        if stream:
            # If raw_payload is used, we might want the raw event stream
            if raw_payload:
                # Wrap event stream with aclosing to ensure cleanup
                async def _safe_event_gen():
                    try:
                        events = _dedupe_assistant_content_events(_iter_events())
                        events = _ensure_initial_response_has_conversation_id(events, conversation_id)
                        async for item in events:
                            tracker.has_content = True
                            yield item
                    finally:
                        # 确保资源清理
                        if not response_consumed:
                            try:
                                if resp and not resp.is_closed:
                                    await resp.aclose()
                                if local_client and client:
                                    await client.aclose()
                            except Exception as e:
                                logger.warning(f"Event generator cleanup failed: {e}")
                
                return None, None, tracker, _safe_event_gen()
            
            # Wrap text stream with aclosing to ensure cleanup
            async def _safe_text_gen():
                try:
                    async for item in tracker.track(_iter_text()):
                        yield item
                finally:
                    # 确保资源清理
                    if not response_consumed:
                        try:
                            if resp and not resp.is_closed:
                                await resp.aclose()
                            if local_client and client:
                                await client.aclose()
                        except Exception as e:
                            logger.warning(f"Text generator cleanup failed: {e}")
            
            return None, _safe_text_gen(), tracker, None
        else:
            buf = []
            try:
                async for t in tracker.track(_iter_text()):
                    buf.append(t)
            finally:
                # Ensure response is closed even if iteration is incomplete
                if not response_consumed and resp:
                    await resp.aclose()
                    if local_client:
                        await client.aclose()
            return "".join(buf), None, tracker, None

    except httpx.ConnectError as e:
        # 网络连接错误，提供更友好的错误信息
        if local_client and client:
            await client.aclose()
        raise httpx.ConnectError(f"无法连接到Amazon Q服务，请检查网络连接和代理设置: {str(e)}")
    except Exception as exc:
        # Critical: close response on any exception before generators are created
        if resp and not resp.is_closed:
            await resp.aclose()
        if local_client and client:
            await client.aclose()
        raise
