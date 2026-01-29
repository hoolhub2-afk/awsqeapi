import tiktoken
import os

# 确保 .env 已加载
from src.core.env import env_loaded  # noqa: F401

try:
    # cl100k_base is used by gpt-4, gpt-3.5-turbo, text-embedding-ada-002
    ENCODING = tiktoken.get_encoding("cl100k_base")
except Exception as exc:
    ENCODING = None

TOKEN_COUNT_MULTIPLIER: float = float(os.getenv("TOKEN_COUNT_MULTIPLIER", "1.0"))

def count_tokens(text: str, apply_multiplier: bool = False) -> int:
    """Counts tokens with tiktoken."""
    if not text or not ENCODING:
        return 0
    token_count = len(ENCODING.encode(text))
    if apply_multiplier:
        token_count = int(token_count * TOKEN_COUNT_MULTIPLIER)
    return token_count
