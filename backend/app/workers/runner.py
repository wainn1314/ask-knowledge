"""任务消费者：轮询 parse_task 并执行（前台线程 / 独立进程两种启动方式）。

`WORKER_INLINE=true` 时由 `main.py` 启动守护线程（开发与单机部署够用）；
生产建议 `WORKER_INLINE=false` + `python -m app.workers.runner` 独立进程（可多副本）。
"""

from __future__ import annotations

import signal
import threading
import time
import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.models.documents import ParseTask
from app.services import task_service
from app.workers import handlers

logger = get_logger(__name__)

_stop_event = threading.Event()
_thread: threading.Thread | None = None


def process_one(db: Session, worker_id: str) -> ParseTask | None:
    """领取并执行一个任务；无任务返回 None。"""
    task = task_service.claim_next(db, worker_id)
    if task is None:
        return None
    logger.info("开始处理任务 task=%s doc=%s stage=%s", task.id, task.document_id, task.stage)
    handlers.handle(db, task)
    return task


def run_forever(worker_id: str | None = None, poll_interval: float | None = None) -> None:
    """循环消费，直到 stop_event 被置位。"""
    name = worker_id or f"worker-{uuid.uuid4().hex[:6]}"
    interval = poll_interval or settings.worker_poll_interval_seconds
    logger.info("worker 启动 id=%s poll=%ss", name, interval)
    while not _stop_event.is_set():
        try:
            with session_scope() as db:
                task = process_one(db, name)
        except Exception:  # 单任务异常不能拖垮循环
            logger.exception("worker 循环异常 id=%s", name)
            task = None
        if task is None:
            _stop_event.wait(interval)
    logger.info("worker 退出 id=%s", name)


def stop() -> None:
    _stop_event.set()


def start_inline_worker() -> threading.Thread | None:
    """启动内联守护线程（幂等）。SQLite 强制单线程，避免并发写锁冲突。"""
    global _thread
    if _thread is not None and _thread.is_alive():
        return _thread
    concurrency = 1 if settings.is_sqlite else max(1, settings.worker_concurrency)
    _stop_event.clear()
    threads: list[threading.Thread] = []
    for idx in range(concurrency):
        thread = threading.Thread(
            target=run_forever, kwargs={"worker_id": f"inline-{idx}"}, daemon=True, name=f"worker-{idx}"
        )
        thread.start()
        threads.append(thread)
    _thread = threads[0]
    logger.info("内联 worker 已启动 threads=%s sqlite=%s", concurrency, settings.is_sqlite)
    return _thread


def _install_signal_handlers() -> None:
    def _handler(signum: int, _frame: object) -> None:
        logger.info("收到信号 %s，准备退出 worker", signum)
        stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):  # 非主线程 / 平台不支持
            pass


def main() -> None:
    """独立进程启动入口：`python -m app.workers.runner`。"""
    _install_signal_handlers()
    run_forever(worker_id="standalone")


if __name__ == "__main__":
    main()
