"""声明基类与公共列类型。

方言兼容策略（见 README「与 docs/03 的差异说明」）：
- 主键：`Uuid(as_uuid=False)` —— PostgreSQL 上是原生 UUID，SQLite 上是 CHAR(32)，Python 侧统一为 str；
- JSON：`JSON` 在 PostgreSQL 上以 `JSONB` 变体渲染（docs/03 的 JSONB 字段）；
- 部分索引：同时提供 `postgresql_where` 与 `sqlite_where`，两种方言都能建。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, Uuid

#: 逻辑 JSON 字段：PostgreSQL 上落 JSONB，其余方言落 JSON
JsonType = JSON().with_variant(JSONB(), "postgresql")

#: 带时区时间戳
TZDateTime = DateTime(timezone=True)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """所有模型的基类。"""


def uuid_pk() -> Mapped[str]:
    """36 位 UUID 主键（字符串形态）。"""
    return mapped_column(Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))


def uuid_col(*, nullable: bool = False, index: bool = False) -> Mapped[str]:
    """UUID 关联列（无物理外键，按 docs/03 §1 设计原则）。"""
    return mapped_column(Uuid(as_uuid=False), nullable=nullable, index=index)



class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, nullable=False, index=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False, index=False
    )


class SoftDeleteMixin:
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(TZDateTime, default=None)

    def mark_deleted(self) -> None:
        self.is_deleted = True
        self.deleted_at = utcnow()


NOT_DELETED_PG = text("is_deleted = FALSE")
NOT_DELETED_SQLITE = text("is_deleted = 0")


def partial_index(
    name: str,
    *columns: str,
    where_pg: str = "is_deleted = FALSE",
    where_sqlite: str = "is_deleted = 0",
    unique: bool = False,
) -> Index:
    """部分索引：同一索引名在 PG / SQLite 下都能创建。"""
    return Index(
        name,
        *columns,
        unique=unique,
        postgresql_where=text(where_pg),
        sqlite_where=text(where_sqlite),
    )


__all__ = [
    "Base",
    "JsonType",
    "Text",
    "TZDateTime",
    "TimestampMixin",
    "SoftDeleteMixin",
    "partial_index",
    "utcnow",
    "uuid_pk",
]
