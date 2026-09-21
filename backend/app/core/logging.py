"""统一日志：控制台 + 可选落盘文件，带 request_id / 级别 / logger 名。

排障动因：上游报错（「Dify 返回 400/403」）的**原因只写在日志里**，而控制台一滚就
没了 —— 配一个 `LOG_FILE`（如 `logs/backend.log`）即可把同样的日志按 5MB × 3 轮转
落盘，事后能直接翻到 Dify 返回的原始 body。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.context import get_request_id

_CONFIGURED = False

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | rid=%(request_id)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()[:8]
        return True


def _formatter() -> logging.Formatter:
    return logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)


def _resolve_log_file(path: str) -> Path | None:
    """把 `LOG_FILE` 解析成绝对路径并建好目录；留空返回 None（= 只输出控制台）。"""
    name = (path or "").strip()
    if not name:
        return None
    from app.core.config import BACKEND_DIR  # 延迟导入：config 依赖本模块的 get_logger

    target = Path(name)
    if not target.is_absolute():
        target = BACKEND_DIR / target
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:  # 目录不可写时退回「只控制台」，绝不因日志把服务拖死
        return None
    return target


def _configured_level(settings: object) -> int:
    """`LOG_LEVEL` 文本 → logging 级别常量（写错就退回 INFO）。"""
    name = str(getattr(settings, "log_level", "INFO") or "INFO").upper()
    return getattr(logging, name, logging.INFO)


def setup_logging(level: int | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    from app.core.config import settings

    root = logging.getLogger()
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(_formatter())
    console.addFilter(_RequestIdFilter())
    root.addHandler(console)

    log_file = _resolve_log_file(getattr(settings, "log_file", ""))
    if log_file is not None:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max(1, int(getattr(settings, "log_file_max_mb", 5))) * 1024 * 1024,
            backupCount=max(0, int(getattr(settings, "log_file_backup_count", 3))),
            encoding="utf-8",
        )
        file_handler.setFormatter(_formatter())
        file_handler.addFilter(_RequestIdFilter())
        root.addHandler(file_handler)

    root.setLevel(level if level is not None else _configured_level(settings))
    # 降噪
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str = "ask_knowledge") -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
