"""日志配置：`LOG_FILE` 落盘（排障用，见 docs/04 §1.3）。"""

from __future__ import annotations

import logging

from app.core import logging as app_logging
from app.core.config import BACKEND_DIR, settings


def test_resolve_log_file_blank_disables() -> None:
    assert app_logging._resolve_log_file("") is None
    assert app_logging._resolve_log_file("   ") is None


def test_resolve_log_file_relative_to_backend() -> None:
    """相对路径按 backend/ 解析，并自动建目录。"""
    target = app_logging._resolve_log_file("logs/unit-test-only.log")
    assert target is not None
    assert target == BACKEND_DIR / "logs" / "unit-test-only.log"
    assert target.parent.is_dir()


def test_setup_logging_writes_file(tmp_path, monkeypatch) -> None:
    """配了 LOG_FILE 就真的落盘：控制台一滚就没了，靠文件翻 Dify 的原始报错。"""
    log_path = tmp_path / "app.log"
    monkeypatch.setattr(settings, "log_file", str(log_path), raising=False)
    monkeypatch.setattr(settings, "log_level", "INFO", raising=False)
    monkeypatch.setattr(app_logging, "_CONFIGURED", False)
    try:
        app_logging.setup_logging()
        app_logging.get_logger("tests.logging").warning("hello-log-file")
        for handler in logging.getLogger().handlers:
            handler.flush()
        assert "hello-log-file" in log_path.read_text(encoding="utf-8")
    finally:
        # 还原全局 logging 配置，避免影响其它用例（pytest 也会捕获日志）
        monkeypatch.undo()
        app_logging._CONFIGURED = False
        root = logging.getLogger()
        for handler in list(root.handlers):
            handler.close()
        root.handlers.clear()
        app_logging.setup_logging()
