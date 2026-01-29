import json
import uuid
import time
import logging
import os
from datetime import datetime
from typing import List, Dict, Any, Optional, Union

from src.integrations.claude.types import ClaudeRequest, ClaudeMessage, ClaudeTool
from src.core.model_mapping import map_model_to_amazonq
from src.core.config import AMAZON_Q_CLIENT_OS, AMAZON_Q_CLIENT_CWD

logger = logging.getLogger(__name__)

THINKING_HINT = "<antml:thinking_mode>interleaved</antml:thinking_mode><antml:max_thinking_length>16000</antml:max_thinking_length>"
THINKING_START_TAG = "<thinking>"
THINKING_END_TAG = "</thinking>"

def _wrap_thinking_content(thinking_text: str) -> str:
    return f"{THINKING_START_TAG}{thinking_text}{THINKING_END_TAG}"

def is_thinking_mode_enabled(thinking_cfg: Optional[Any]) -> bool:
    if thinking_cfg is None:
        return False
    if isinstance(thinking_cfg, bool):
        return thinking_cfg
    if isinstance(thinking_cfg, str):
        return thinking_cfg.strip().lower() == "enabled"
    if isinstance(thinking_cfg, dict):
        if str(thinking_cfg.get("type", "")).lower() == "enabled":
            return True
        enabled = thinking_cfg.get("enabled")
        if isinstance(enabled, bool):
            return enabled
        budget = thinking_cfg.get("budget_tokens")
        return isinstance(budget, (int, float)) and budget > 0
    return False

def _append_thinking_hint(text: str) -> str:
    if not text:
        return THINKING_HINT
    if THINKING_HINT in text:
        return text
    separator = "" if text.endswith(("\n", "\r")) else "\n"
    return f"{text}{separator}{THINKING_HINT}"

def _prune_images_to_last_two_user_messages(history: List[Dict[str, Any]], current_user: Dict[str, Any]) -> None:
    targets: List[Dict[str, Any]] = []
    for item in history:
        user = item.get("userInputMessage")
        if isinstance(user, dict) and user.get("images"):
            targets.append(user)
    if current_user.get("images"):
        targets.append(current_user)
    for user in targets[:-2]:
        user.pop("images", None)

def _debug_message_conversion_strict() -> bool:
    return os.getenv("DEBUG_MESSAGE_CONVERSION", "").strip().lower() in ("1", "true", "yes", "on")

def get_current_timestamp() -> str:
    """Get current timestamp in Amazon Q format."""
    now = datetime.now().astimezone()
    weekday = now.strftime("%A")
    iso_time = now.isoformat(timespec='milliseconds')
    return f"{weekday}, {iso_time}"

def map_model_name(claude_model: str) -> str:
    """Map Claude model name (canonical or short) to Amazon Q model ID."""
    from src.core.config import AMAZON_Q_DEFAULT_MODEL
    return map_model_to_amazonq(claude_model, default_model=AMAZON_Q_DEFAULT_MODEL)

def extract_text_from_content(content: Union[str, List[Dict[str, Any]]]) -> str:
    """Extract text from Claude content."""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type")
                if block_type == "text":
                    parts.append(block.get("text", ""))
                elif block_type == "thinking":
                    parts.append(_wrap_thinking_content(block.get("thinking", "")))
        return "\n".join(parts)
    return ""

def extract_images_from_content(content: Union[str, List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """Extract images from Claude content and convert to Amazon Q format."""
    if not isinstance(content, list):
        return None
    
    images = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "image":
            source = block.get("source", {})
            if source.get("type") == "base64":
                media_type = source.get("media_type", "image/png")
                fmt = media_type.split("/")[-1] if "/" in media_type else "png"
                images.append({
                    "format": fmt,
                    "source": {
                        "bytes": source.get("data", "")
                    }
                })
    return images if images else None

def convert_tool(tool: ClaudeTool) -> Dict[str, Any]:
    """Convert Claude tool to Amazon Q tool."""
    desc = tool.description or ""
    if len(desc) > 10240:
        desc = desc[:10100] + "\n\n...(Full description provided in TOOL DOCUMENTATION section)"
    
    return {
        "toolSpecification": {
            "name": tool.name,
            "description": desc,
            "inputSchema": {"json": tool.input_schema}
        }
    }

def merge_user_messages(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge consecutive user messages, keeping only the last 2 messages' images."""
    if not messages:
        return {}
    
    all_contents = []
    base_context = None
    base_origin = None
    base_model = None
    all_images = []
    all_tool_results = []
    
    for msg in messages:
        content = msg.get("content", "")
        msg_ctx = msg.get("userInputMessageContext", {}) or {}
        if base_context is None:
            base_context = dict(msg_ctx)
            if "toolResults" in base_context:
                all_tool_results.extend(base_context.pop("toolResults") or [])
        else:
            if "toolResults" in msg_ctx:
                all_tool_results.extend(msg_ctx.get("toolResults") or [])
        if base_origin is None:
            base_origin = msg.get("origin", "KIRO_CLI")
        if base_model is None:
            base_model = msg.get("modelId")
        
        if content:
            all_contents.append(content)
        
        # Collect images from each message
        msg_images = msg.get("images")
        if msg_images:
            all_images.append(msg_images)
    
    result: Dict[str, Any] = {
        "content": "\n\n".join(all_contents),
        "userInputMessageContext": base_context or {},
        "origin": base_origin or "KIRO_CLI",
    }
    if base_model:
        result["modelId"] = base_model

    if all_tool_results:
        merged_tool_results = []
        tool_results_by_id = {}
        for tr in all_tool_results:
            tool_use_id = tr.get("toolUseId")
            if not tool_use_id:
                merged_tool_results.append(tr)
                continue

            existing = tool_results_by_id.get(tool_use_id)
            if existing is None:
                copied = dict(tr)
                tool_results_by_id[tool_use_id] = copied
                merged_tool_results.append(copied)
                continue

            existing_content = existing.get("content")
            if not isinstance(existing_content, list):
                existing["content"] = [] if existing_content is None else [existing_content]

            incoming_content = tr.get("content") or []
            if isinstance(incoming_content, list):
                existing["content"].extend(incoming_content)
            else:
                existing["content"].append(incoming_content)

            if "status" not in existing and "status" in tr:
                existing["status"] = tr.get("status")

        result["userInputMessageContext"]["toolResults"] = merged_tool_results
    
    # Only keep images from the last 2 messages that have images
    if all_images:
        kept_images = []
        for img_list in all_images[-2:]:  # Take last 2 messages' images
            kept_images.extend(img_list)
        if kept_images:
            result["images"] = kept_images
    
    return result

def _extract_tool_use_ids_from_history_item(item: Dict[str, Any]) -> List[str]:
    if "userInputMessage" in item:
        ctx = (item.get("userInputMessage") or {}).get("userInputMessageContext", {}) or {}
        tool_results = ctx.get("toolResults") or []
        return [r.get("toolUseId") for r in tool_results if isinstance(r, dict) and r.get("toolUseId")]
    if "assistantResponseMessage" in item:
        msg = item.get("assistantResponseMessage") or {}
        tool_uses = msg.get("toolUses") or []
        return [u.get("toolUseId") for u in tool_uses if isinstance(u, dict) and u.get("toolUseId")]
    return []

def _summarize_history_item(item: Dict[str, Any]) -> str:
    role = "user" if "userInputMessage" in item else "assistant" if "assistantResponseMessage" in item else "unknown"
    keys = sorted(list(item.keys()))
    tool_use_ids = _extract_tool_use_ids_from_history_item(item)
    if role == "user":
        content = (item.get("userInputMessage") or {}).get("content", "")
    else:
        content = (item.get("assistantResponseMessage") or {}).get("content", "")
    content_len = len(content) if isinstance(content, str) else 0
    return f"role={role}, keys={keys}, toolUseIds={tool_use_ids}, content_len={content_len}"

def _alternation_violation_message(
    prev_idx: int,
    prev_item: Dict[str, Any],
    idx: int,
    item: Dict[str, Any],
    role: str,
) -> str:
    return (
        f"history alternation violated: prev_idx={prev_idx}, idx={idx}, role={role}. "
        f"prev=({_summarize_history_item(prev_item)}), curr=({_summarize_history_item(item)})"
    )

def _validate_history_alternation(history: List[Dict[str, Any]]) -> None:
    strict = _debug_message_conversion_strict()
    last = None
    for idx, item in enumerate(history):
        role = "user" if "userInputMessage" in item else "assistant" if "assistantResponseMessage" in item else None
        if not role:
            continue
        if last and last[0] == role:
            msg = _alternation_violation_message(last[1], last[2], idx, item, role)
            if strict:
                raise ValueError(msg)
            logger.warning(msg)
        last = (role, idx, item)

def _tool_result_violation_message(
    prev_idx: Optional[int],
    prev_item: Optional[Dict[str, Any]],
    idx: int,
    item: Dict[str, Any],
) -> str:
    prev_summary = _summarize_history_item(prev_item or {})
    return (
        f"toolResults order violated: prev_idx={prev_idx}, idx={idx}. "
        f"prev=({prev_summary}), curr=({_summarize_history_item(item)})"
    )

def _tool_results_match_tool_uses(tool_result_ids: List[str], tool_use_ids: List[str]) -> bool:
    return bool(tool_result_ids) and all(tid in tool_use_ids for tid in tool_result_ids)

def _validate_tool_results_follow_tool_uses(history: List[Dict[str, Any]]) -> None:
    strict = _debug_message_conversion_strict()
    last_assistant = None
    last_tool_use_ids: List[str] = []
    for idx, item in enumerate(history):
        if "assistantResponseMessage" in item:
            last_assistant = (idx, item)
            last_tool_use_ids = _extract_tool_use_ids_from_history_item(item)
            continue
        if "userInputMessage" not in item:
            continue
        tool_result_ids = _extract_tool_use_ids_from_history_item(item)
        if not tool_result_ids:
            continue
        ok = bool(last_assistant) and _tool_results_match_tool_uses(tool_result_ids, last_tool_use_ids)
        if ok:
            continue
        msg = _tool_result_violation_message(last_assistant[0] if last_assistant else None, last_assistant[1] if last_assistant else None, idx, item)
        if strict:
            raise ValueError(msg)
        logger.warning(msg)

def _merge_user_messages_preserve_current_context(
    prev_user: Dict[str, Any],
    current_user: Dict[str, Any],
) -> Dict[str, Any]:
    merged = merge_user_messages([prev_user, current_user])
    tool_results = (merged.get("userInputMessageContext", {}) or {}).get("toolResults")
    merged["userInputMessageContext"] = dict(current_user.get("userInputMessageContext", {}) or {})
    if tool_results:
        merged["userInputMessageContext"]["toolResults"] = tool_results
    return merged

def _post_process_amazonq_history(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    processed = []
    pending: List[Dict[str, Any]] = []
    for item in history:
        if "userInputMessage" in item:
            pending.append(item["userInputMessage"])
            continue
        if pending:
            processed.append({"userInputMessage": merge_user_messages(pending)})
            pending = []
        processed.append(item)
    if pending:
        processed.append({"userInputMessage": merge_user_messages(pending)})
    _validate_history_alternation(processed)
    _validate_tool_results_follow_tool_uses(processed)
    return processed

def process_history(messages: List[ClaudeMessage]) -> List[Dict[str, Any]]:
    """Process history messages to match Amazon Q format (alternating user/assistant)."""
    history = []
    seen_tool_use_ids = set()
    
    raw_history = []
    
    # First pass: convert individual messages
    for msg in messages:
        if msg.role == "user":
            content = msg.content
            text_content = ""
            tool_results = None
            images = extract_images_from_content(content)
            
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        btype = block.get("type")
                        if btype == "text":
                            text_parts.append(block.get("text", ""))
                        elif btype == "tool_result":
                            if tool_results is None:
                                tool_results = []
                            
                            tool_use_id = block.get("tool_use_id")
                            raw_c = block.get("content", [])
                            
                            aq_content = []
                            if isinstance(raw_c, str):
                                aq_content = [{"text": raw_c}]
                            elif isinstance(raw_c, list):
                                for item in raw_c:
                                    if isinstance(item, dict):
                                        if item.get("type") == "text":
                                            aq_content.append({"text": item.get("text", "")})
                                        elif "text" in item:
                                            aq_content.append({"text": item["text"]})
                                    elif isinstance(item, str):
                                        aq_content.append({"text": item})
                            
                            if not any(i.get("text", "").strip() for i in aq_content):
                                aq_content = [{"text": "Tool use was cancelled by the user"}]
                                
                            # Merge if exists
                            existing = next((r for r in tool_results if r["toolUseId"] == tool_use_id), None)
                            if existing:
                                existing["content"].extend(aq_content)
                            else:
                                tool_results.append({
                                    "toolUseId": tool_use_id,
                                    "content": aq_content,
                                    "status": block.get("status", "success")
                                })
                text_content = "\n".join(text_parts)
            else:
                text_content = extract_text_from_content(content)
            
            user_ctx = {
                "envState": {
                    "operatingSystem": AMAZON_Q_CLIENT_OS,
                    "currentWorkingDirectory": AMAZON_Q_CLIENT_CWD,
                }
            }
            if tool_results:
                user_ctx["toolResults"] = tool_results
                
            u_msg = {
                "content": text_content,
                "userInputMessageContext": user_ctx,
                "origin": "KIRO_CLI"
            }
            if images:
                u_msg["images"] = images
                
            raw_history.append({"userInputMessage": u_msg})
            
        elif msg.role == "assistant":
            content = msg.content
            text_content = extract_text_from_content(content)
            
            entry = {
                "assistantResponseMessage": {
                    "messageId": str(uuid.uuid4()),
                    "content": text_content
                }
            }
            
            if isinstance(content, list):
                tool_uses = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tid = block.get("id")
                        if tid and tid not in seen_tool_use_ids:
                            seen_tool_use_ids.add(tid)
                            tool_uses.append({
                                "toolUseId": tid,
                                "name": block.get("name"),
                                "input": block.get("input", {})
                            })
                if tool_uses:
                    entry["assistantResponseMessage"]["toolUses"] = tool_uses
            
            raw_history.append(entry)

    # Second pass: merge consecutive user messages
    pending_user_msgs = []
    for item in raw_history:
        if "userInputMessage" in item:
            user_msg = item["userInputMessage"]
            msg_ctx = user_msg.get("userInputMessageContext", {}) or {}
            tool_results = msg_ctx.get("toolResults") or []
            has_tool_results = bool(tool_results)

            if has_tool_results:
                if pending_user_msgs:
                    merged = merge_user_messages(pending_user_msgs)
                    history.append({"userInputMessage": merged})
                    pending_user_msgs = []
                history.append(item)
            else:
                pending_user_msgs.append(user_msg)
        elif "assistantResponseMessage" in item:
            if pending_user_msgs:
                merged = merge_user_messages(pending_user_msgs)
                history.append({"userInputMessage": merged})
                pending_user_msgs = []
            history.append(item)
            
    if pending_user_msgs:
        merged = merge_user_messages(pending_user_msgs)
        history.append({"userInputMessage": merged})
        
    _validate_history_alternation(history)
    _validate_tool_results_follow_tool_uses(history)
    return history

def convert_claude_to_amazonq_request(req: ClaudeRequest, conversation_id: Optional[str] = None) -> Dict[str, Any]:
    """Convert ClaudeRequest to Amazon Q request body."""
    if conversation_id is None:
        conversation_id = str(uuid.uuid4())
        
    # 1. Tools
    aq_tools = []
    long_desc_tools = []
    if req.tools:
        for t in req.tools:
            if t.description and len(t.description) > 10240:
                long_desc_tools.append({"name": t.name, "full_description": t.description})
            aq_tools.append(convert_tool(t))
            
    # 2. Current Message (last user message)
    last_msg = req.messages[-1] if req.messages else None
    prompt_content = ""
    tool_results = None
    has_tool_result = False
    images = None
    
    if last_msg and last_msg.role == "user":
        content = last_msg.content
        images = extract_images_from_content(content)
        
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "thinking":
                        text_parts.append(_wrap_thinking_content(block.get("thinking", "")))
                    elif btype == "tool_result":
                        has_tool_result = True
                        if tool_results is None:
                            tool_results = []
                        
                        tid = block.get("tool_use_id")
                        raw_c = block.get("content", [])
                        
                        aq_content = []
                        if isinstance(raw_c, str):
                            aq_content = [{"text": raw_c}]
                        elif isinstance(raw_c, list):
                            for item in raw_c:
                                if isinstance(item, dict):
                                    if item.get("type") == "text":
                                        aq_content.append({"text": item.get("text", "")})
                                    elif "text" in item:
                                        aq_content.append({"text": item["text"]})
                                elif isinstance(item, str):
                                    aq_content.append({"text": item})
                                    
                        if not any(i.get("text", "").strip() for i in aq_content):
                            aq_content = [{"text": "Tool use was cancelled by the user"}]
                            
                        existing = next((r for r in tool_results if r["toolUseId"] == tid), None)
                        if existing:
                            existing["content"].extend(aq_content)
                        else:
                            tool_results.append({
                                "toolUseId": tid,
                                "content": aq_content,
                                "status": block.get("status", "success")
                            })
            prompt_content = "\n".join(text_parts)
        else:
            prompt_content = extract_text_from_content(content)
            
    prompt_content_text = prompt_content.strip()

    # 3. Context
    user_ctx = {
        "envState": {
            "operatingSystem": AMAZON_Q_CLIENT_OS,
            "currentWorkingDirectory": AMAZON_Q_CLIENT_CWD,
        }
    }
    if aq_tools:
        user_ctx["tools"] = aq_tools
    if tool_results:
        user_ctx["toolResults"] = tool_results
        
    # 4. Format Content
    formatted_content = ""
    now_stamp = get_current_timestamp()
    if has_tool_result and not prompt_content_text:
        formatted_content = ""
    else:
        formatted_content = (
            "--- CONTEXT ENTRY BEGIN ---\n"
            f"Current time: {now_stamp}\n"
            "--- CONTEXT ENTRY END ---\n\n"
            "--- USER MESSAGE BEGIN ---\n"
            f"{prompt_content_text}\n"
            "--- USER MESSAGE END ---"
        )
        
    if long_desc_tools:
        docs = []
        for info in long_desc_tools:
            docs.append(f"Tool: {info['name']}\nFull Description:\n{info['full_description']}\n")
        tool_docs = (
            "--- TOOL DOCUMENTATION BEGIN ---\n"
            f"{''.join(docs)}"
            "--- TOOL DOCUMENTATION END ---\n\n"
        )
        if formatted_content:
            formatted_content = f"{tool_docs}{formatted_content}"
        else:
            formatted_content = (
                f"{tool_docs}"
                "--- CONTEXT ENTRY BEGIN ---\n"
                f"Current time: {now_stamp}\n"
                "--- CONTEXT ENTRY END ---"
            )
        
    if req.system:
        sys_text = ""
        if isinstance(req.system, str):
            sys_text = req.system
        elif isinstance(req.system, list):
            parts = []
            for b in req.system:
                if isinstance(b, dict) and b.get("type") == "text":
                    parts.append(b.get("text", ""))
            sys_text = "\n".join(parts)
            
        if sys_text.strip():
            sys_text = sys_text.strip()
            formatted_content = (
                "--- SYSTEM PROMPT BEGIN ---\n"
                f"{sys_text}\n"
                "--- SYSTEM PROMPT END ---\n\n"
                f"{formatted_content}"
            )

    if is_thinking_mode_enabled(req.thinking) and formatted_content:
        formatted_content = _append_thinking_hint(formatted_content)
            
    # 5. Model
    model_id = map_model_name(req.model)
    logger.info(f"[CONVERTER] Mapped model '{req.model}' to '{model_id}'")

    # 6. User Input Message
    user_input_msg = {
        "content": formatted_content,
        "userInputMessageContext": user_ctx,
        "origin": "KIRO_CLI",
        "modelId": model_id
    }
    if images:
        user_input_msg["images"] = images
        
    # 7. History
    history_msgs = req.messages[:-1] if len(req.messages) > 1 else []
    aq_history = process_history(history_msgs)
    aq_history = _post_process_amazonq_history(aq_history)
    if aq_history and "userInputMessage" in aq_history[-1]:
        prev_user = aq_history[-1]["userInputMessage"]
        user_input_msg = _merge_user_messages_preserve_current_context(prev_user, user_input_msg)
        aq_history = aq_history[:-1]
    _prune_images_to_last_two_user_messages(aq_history, user_input_msg)
    
    # 8. Final Body
    return {
        "conversationState": {
            "conversationId": conversation_id,
            "history": aq_history,
            "currentMessage": {
                "userInputMessage": user_input_msg
            },
            "chatTriggerType": "MANUAL"
        }
    }
