"""文档与异步任务模型（对应 docs/03 §4）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Index, Integer, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import DocStatus, TaskStatus, TaskType
from app.db.base import (
    Base,
    SoftDeleteMixin,
    TZDateTime,
    TimestampMixin,
    partial_index,
    uuid_col,
    uuid_pk,
)


class Document(Base, TimestampMixin, SoftDeleteMixin):
    """文档状态机：pending → parsing → auditing → indexing → ready / failed / blocked。"""

    __tablename__ = "document"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = uuid_col()
    kb_id: Mapped[str] = uuid_col()
    uploader_id: Mapped[str] = uuid_col()
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_ext: Mapped[str] = mapped_column(String(16), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    parsed_path: Mapped[str | None] = mapped_column(String(1024))
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(String(16), default=DocStatus.PENDING.value, nullable=False)
    stage_progress: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    audit_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    parse_mode: Mapped[str | None] = mapped_column(String(16))
    parse_duration_ms: Mapped[int | None] = mapped_column(Integer)

    dify_document_id: Mapped[str | None] = mapped_column(String(64))
    dify_indexing_status: Mapped[str | None] = mapped_column(String(16))
    retry_count: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)

    __table_args__ = (
        partial_index("uk_document_kb_sha", "kb_id", "sha256", unique=True),
        partial_index("idx_document_kb", "tenant_id", "kb_id", "created_at"),
        partial_index("idx_document_status", "tenant_id", "status"),
    )


class ParseTask(Base, TimestampMixin):
    """异步任务表：解析 / 审核 / 入库 / 删除索引；worker 轮询 + 重试。"""

    __tablename__ = "parse_task"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = uuid_col()
    document_id: Mapped[str] = uuid_col()
    task_type: Mapped[str] = mapped_column(
        String(16), default=TaskType.PARSE.value, nullable=False
    )
    stage: Mapped[str | None] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(
        String(16), default=TaskStatus.QUEUED.value, nullable=False
    )
    priority: Mapped[int] = mapped_column(SmallInteger, default=100, nullable=False)
    attempt: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    max_attempt: Mapped[int] = mapped_column(SmallInteger, default=3, nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    next_run_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    __table_args__ = (
        partial_index(
            "idx_task_pick",
            "next_run_at",
            "priority",
            where_pg="status IN ('queued', 'failed')",
            where_sqlite="status IN ('queued', 'failed')",
        ),
        Index("idx_task_document", "document_id"),
    )
