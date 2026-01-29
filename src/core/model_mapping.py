import logging
from typing import Optional

logger = logging.getLogger(__name__)

# AWS CodeWhisperer API 实际支持的模型
# 使用 KIRO_CLI origin 时可用 Sonnet/Haiku/Opus 4.5 系列
VALID_AMAZONQ_MODELS = {
    "claude-sonnet-4",
    "claude-sonnet-4.5",
    "claude-haiku-4.5",
    "claude-opus-4.5",
}

CANONICAL_TO_SHORT_MODEL = {
    # Only map to models supported by AWS CodeWhisperer (KIRO_CLI origin)
    "claude-sonnet-4-20250514": "claude-sonnet-4",
    "claude-sonnet-4-5-20250929": "claude-sonnet-4.5",
    "claude-haiku-4-5-20251001": "claude-haiku-4.5",
    "claude-opus-4-5-20251101": "claude-opus-4.5",
    "claude-3-5-sonnet-20241022": "claude-sonnet-4.5",
    "claude-3-5-sonnet-20240620": "claude-sonnet-4.5",
    "claude-3-5-haiku-20241022": "claude-haiku-4.5",
}


def _normalize_model_id(model: Optional[str]) -> str:
    raw = (model or "").strip().lower()
    if not raw:
        return ""

    # Clients often send friendly labels like "opus (claude-opus-4-5-20251101)".
    # Retain only the substring that starts from the Claude prefix.
    claude_idx = raw.find("claude-")
    if claude_idx > 0:
        raw = raw[claude_idx:]

    # Remove stray wrapping characters, e.g. "claude-opus-4-5-20251101)".
    raw = raw.strip("()[]{} ")
    return raw


def _ensure_default_model(default_model: str) -> str:
    normalized = _normalize_model_id(default_model)
    mapped = _resolve_model_id(normalized)
    if mapped:
        return mapped
    return "claude-sonnet-4"


def _heuristic_model_map(model_lower: str) -> Optional[str]:
    """
    启发式模型映射 - 将各种模型名称映射到 AWS 支持的模型
    使用 KIRO_CLI origin 时可用 Sonnet/Haiku/Opus 4.5 系列
    """
    if model_lower.startswith("claude-sonnet-4-5") or model_lower.startswith("claude-sonnet-4.5"):
        return "claude-sonnet-4.5"
    if model_lower.startswith("claude-sonnet-4"):
        return "claude-sonnet-4"

    # Opus/Haiku 4.5
    if "opus-4-5" in model_lower or "opus-4.5" in model_lower:
        return "claude-opus-4.5"
    if "haiku-4-5" in model_lower or "haiku-4.5" in model_lower:
        return "claude-haiku-4.5"
    if "opus" in model_lower:
        return "claude-opus-4.5"
    if "haiku" in model_lower:
        return "claude-haiku-4.5"

    if "1m" in model_lower or "1000k" in model_lower:
        return "claude-sonnet-4.5"
    return None


def _resolve_model_id(model_lower: str) -> Optional[str]:
    """
    解析模型ID，进行智能映射
    注意：仅允许上游已知支持的模型, 避免触发上游 ValidationException
    """
    if not model_lower:
        return None

    # 如果已在已知模型列表中，直接返回
    if model_lower in VALID_AMAZONQ_MODELS:
        return model_lower

    # 尝试从规范名称映射
    if model_lower in CANONICAL_TO_SHORT_MODEL:
        return CANONICAL_TO_SHORT_MODEL[model_lower]

    # 尝试启发式映射
    heuristic = _heuristic_model_map(model_lower)
    if heuristic:
        return heuristic

    return None


def map_model_to_amazonq(model: Optional[str], default_model: str = "auto") -> str:
    """
    将模型ID映射到 Amazon Q 格式
    只允许上游已知支持的模型, 未知模型回退到默认值
    """
    default = _ensure_default_model(default_model)
    model_lower = _normalize_model_id(model)
    if not model_lower:
        return default
    if model_lower == "auto":
        return default

    resolved = _resolve_model_id(model_lower)
    if resolved and resolved in VALID_AMAZONQ_MODELS:
        if resolved != model_lower:
            logger.debug("Mapped model '%s' to '%s'", model, resolved)
        return resolved
    if resolved and resolved not in VALID_AMAZONQ_MODELS:
        logger.warning("Unsupported model '%s' mapped to '%s', using default '%s'", model, resolved, default)
        return default

    # 无法解析时返回默认模型
    logger.warning("Unable to normalize model '%s', using default '%s'", model, default)
    return default
