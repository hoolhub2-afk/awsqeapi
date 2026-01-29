# Claude 模型配置
from typing import Dict

# 模型上下文配置
# 使用 KIRO_CLI origin 时可用 Sonnet/Haiku/Opus 4.5 系列
MODEL_CONFIGS: Dict[str, Dict[str, int]] = {
    "auto": {
        "max_tokens": 8192,
        "context_window": 1000000  # 1M 上下文
    },
    "claude-sonnet-4": {
        "max_tokens": 8192,
        "context_window": 1000000  # 1M 上下文
    },
    "claude-sonnet-4.5": {
        "max_tokens": 8192,
        "context_window": 1000000  # 1M 上下文
    },
    "claude-haiku-4.5": {
        "max_tokens": 8192,
        "context_window": 1000000  # 1M 上下文
    },
    "claude-opus-4.5": {
        "max_tokens": 8192,
        "context_window": 1000000  # 1M 上下文
    },
    # 默认配置，用于未知模型
    "_default": {
        "max_tokens": 8192,
        "context_window": 1000000
    }
}

def get_model_max_tokens(model_id: str) -> int:
    """获取模型的最大 token 限制"""
    return MODEL_CONFIGS.get(model_id, MODEL_CONFIGS["_default"])["max_tokens"]

def get_model_context_window(model_id: str) -> int:
    """获取模型的上下文窗口大小"""
    return MODEL_CONFIGS.get(model_id, MODEL_CONFIGS["_default"])["context_window"]
