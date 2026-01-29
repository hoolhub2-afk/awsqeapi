import logging

from src.core.logging_setup import configure_logging


def _close_root_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        try:
            handler.flush()
        except Exception:
            pass
        try:
            handler.close()
        except Exception:
            pass
        root.removeHandler(handler)


def test_configure_logging_writes_error_log(tmp_path, monkeypatch):
    monkeypatch.delenv("LOG_FILE", raising=False)
    monkeypatch.delenv("LOG_FILE_PATH", raising=False)
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    log_file, error_file = configure_logging(tmp_path)
    logging.getLogger("t").info("hello")
    logging.getLogger("t").error("boom")
    _close_root_handlers()
    assert "hello" in log_file.read_text(encoding="utf-8")
    assert "boom" in error_file.read_text(encoding="utf-8")
