import logging
import os
from pathlib import Path

from src.core.security_utils import resolve_log_file_path


def _log_level_int() -> int:
    value = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, value, logging.INFO)


def _file_handler(path: Path, level: int) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(level)
    return handler


def configure_logging(base_dir: Path) -> tuple[Path, Path]:
    level = _log_level_int()
    log_file = resolve_log_file_path(base_dir)
    error_file = log_file.parent / "error.log"
    console = logging.StreamHandler()
    console.setLevel(level)
    handlers = [console, _file_handler(log_file, level), _file_handler(error_file, logging.ERROR)]
    logging.basicConfig(level=level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", handlers=handlers, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.setLevel(level)
        uv_logger.propagate = True
    return log_file, error_file
