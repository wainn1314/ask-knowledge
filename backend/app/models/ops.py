"""审核日志与用量模型（对应 docs/03 §7）。"""

from __future__ import annotations

from sqlalchemy import Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, JsonType, TimestampMixin, uuid_col, uuid_pk


class AuditLog(Base, TimestampMixin):
    """内容审核日志：upload / parse / query / answer 四个卡点。"""

    __tablename__ = "audit_log"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = uuid_col()
    scene: Mapped[str] = mapped_column(String(16), nullable=False)
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(32), default="local", nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False, comment="pass/block/review/error")
    label: Mapped[str | None] = mapped_column(String(64))
    risk_words: Mapped[list | None] = mapped_column(JsonType)
    request_id: Mapped[str | None] = mapped_column(String(64))
    raw_response: Mapped[dict | None] = mapped_column(JsonType)
    content_excerpt: Mapped[str | None] = mapped_column(Text, comment="命中内容摘要，便于复核")

    __table_args__ = (
        Index("idx_audit_scene", "tenant_id", "scene", "created_at"),
        Index("idx_audit_object", "object_type", "object_id"),
    )


class UsageRecord(Base, TimestampMixin):
    """用量与成本记录。"""

    __tablename__ = "usage_record"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = uuid_col()
    user_id: Mapped[str | None] = mapped_column(String(36))
    scene: Mapped[str] = mapped_column(String(24), nullable=False)
    ref_id: Mapped[str | None] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (Index("idx_usage_tenant_day", "tenant_id", "created_at"),)
