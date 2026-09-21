"""任务队列服务：入队 / 领取 / 完成 / 失败重试（docs/03 §4 parse_task）。"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import TaskStage, TaskStatus, TaskType
from app.core.logging import get_logger
from app.db.base import utcnow
from app.models.documents import ParseTask

logger = get_logger(__name__)

#: 失败重试退避（秒）：第 1/2/3 次失败分别等待 5 / 20 / 60 秒
RETRY_DELAYS = (5, 20, 60)


def enqueue(
    db: Session,
    *,
    tenant_id: str,
    document_id: str,
    task_type: str = TaskType.PARSE.value,
    stage: str | None = TaskStage.PARSE.value,
    priority: int = 100,
    max_attempt: int | None = None,
    delay_seconds: int = 0,
) -> ParseTask:
    """入队一个文档任务。"""
    task = ParseTask(
        tenant_id=tenant_id,
        document_id=document_id,
        task_type=task_type,
        stage=stage,
        status=TaskStatus.QUEUED.value,
        priority=priority,
        attempt=0,
        max_attempt=max_attempt or settings.task_max_attempt,
        next_run_at=utcnow() + timedelta(seconds=delay_seconds),
    )
    db.add(task)
    db.flush()
    logger.info("任务入队 task=%s doc=%s type=%s", task.id, document_id, task_type)
    return task


def claim_next(db: Session, worker_id: str) -> ParseTask | None:
    """领取一个待执行任务（PG 用 SKIP LOCKED，SQLite 串行安全）。"""
    now = utcnow()
    stmt = (
        select(ParseTask)
        .where(
            ParseTask.status.in_([TaskStatus.QUEUED.value, TaskStatus.FAILED.value]),
            ParseTask.next_run_at <= now,
        )
        .order_by(ParseTask.priority.asc(), ParseTask.next_run_at.asc())
        .limit(1)
    )
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    task = db.scalar(stmt)
    if task is None:
        return None
    task.status = TaskStatus.RUNNING.value
    task.locked_by = worker_id
    task.locked_at = now
    task.attempt = int(task.attempt or 0) + 1
    db.flush()
    return task


def mark_success(db: Session, task: ParseTask) -> None:
    task.status = TaskStatus.SUCCESS.value
    task.finished_at = utcnow()
    task.error_message = None
    task.locked_by = None
    task.locked_at = None
    db.flush()


def mark_failed(db: Session, task: ParseTask, error: str) -> str:
    """失败处理：未超次数则退避重试（failed），超限则置 dead。返回最终状态。"""
    task.error_message = (error or "")[:2000]
    task.locked_by = None
    task.locked_at = None
    if int(task.attempt or 0) >= int(task.max_attempt or 1):
        task.status = TaskStatus.DEAD.value
        task.finished_at = utcnow()
        logger.error("任务失败（已超最大重试） task=%s err=%s", task.id, error)
    else:
        delay = RETRY_DELAYS[min(int(task.attempt or 1) - 1, len(RETRY_DELAYS) - 1)]
        task.status = TaskStatus.FAILED.value
        task.next_run_at = utcnow() + timedelta(seconds=delay)
        logger.warning("任务失败将重试 task=%s attempt=%s delay=%ss", task.id, task.attempt, delay)
    db.flush()
    return task.status


def pending_task_for(db: Session, document_id: str) -> ParseTask | None:
    """取某文档最近一次未完成任务（用于「重试」时去重）。"""
    return db.scalar(
        select(ParseTask)
        .where(
            ParseTask.document_id == document_id,
            ParseTask.status.in_(
                [TaskStatus.QUEUED.value, TaskStatus.RUNNING.value, TaskStatus.FAILED.value]
            ),
        )
        .order_by(ParseTask.created_at.desc())
        .limit(1)
    )
