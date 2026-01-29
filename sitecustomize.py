"""Global logging filter to hide absolute paths in uvicorn reload output."""

from __future__ import annotations

import logging
from pathlib import Path


class HideAbsolutePathFilter(logging.Filter):
    """Replace absolute paths with relative ones to avoid leaking local paths."""

    def __init__(self) -> None:
        super().__init__()
        self._cwd = Path.cwd().resolve()

    def filter(self, record: logging.LogRecord) -> bool:
        if (
            record.msg.startswith("Will watch for changes in these directories:")
            and record.args
            and isinstance(record.args[0], list)
        ):
            record.args = (self._sanitize_paths(record.args[0]),)
        return True

    def _sanitize_paths(self, raw_paths: list[str]) -> list[str]:
        sanitized: list[str] = []
        for raw in raw_paths:
            try:
                rel = Path(raw).resolve().relative_to(self._cwd)
                sanitized.append(rel.as_posix() or ".")
            except ValueError:
                sanitized.append(Path(raw).name or raw)
        return sanitized


def _inject_uvicorn_filter() -> None:
    try:
        from uvicorn import config as uvicorn_config
    except Exception:  # pragma: no cover - uvicorn may not be installed
        return

    log_config = uvicorn_config.LOGGING_CONFIG
    log_config.setdefault("filters", {})
    filter_key = "hide_abs_path"

    if filter_key not in log_config["filters"]:
        log_config["filters"][filter_key] = {"()": "sitecustomize.HideAbsolutePathFilter"}

    default_handler = log_config.get("handlers", {}).get("default")
    if default_handler is not None:
        default_handler.setdefault("filters", [])
        if filter_key not in default_handler["filters"]:
            default_handler["filters"].append(filter_key)

    for logger_name in ("uvicorn", "uvicorn.error"):
        logger_config = log_config.get("loggers", {}).get(logger_name)
        if logger_config is None:
            continue
        logger_filters = logger_config.setdefault("filters", [])
        if filter_key not in logger_filters:
            logger_filters.append(filter_key)


_inject_uvicorn_filter()
