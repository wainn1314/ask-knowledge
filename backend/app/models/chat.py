"""对话与消息模型（对应 docs/03 §6：历史归档）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    Base,
    JsonType,
    SoftDeleteMixin,
    TZDateTime,
    TimestampMixin,
    partial_index,
    uuid_col,
    uuid_pk,
)


class Conversation(Base, TimestampMixin, SoftDeleteMixin):
    """会话。Dify 侧 dify_conversation_id 是续聊真相源。"""

    __tablename__ = "conversation"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = uuid_col()
    user_id: Mapped[str] = uuid_col()
    kb_id: Mapped[str] = uuid_col()
    title: Mapped[str | None] = mapped_column(String(255))
    dify_conversation_id: Mapped[str | None] = mapped_column(String(64))
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    __table_args__ = (
        partial_index("idx_conv_user", "tenant_id", "user_id", "last_message_at"),
        Index(
            "uk_conv_dify",
            "dify_conversation_id",
            unique=True,
            postgresql_where=text("dify_conversation_id IS NOT NULL"),
            sqlite_where=text("dify_conversation_id IS NOT NULL"),
        ),
    )


class Message(Base, TimestampMixin):
    """消息归档（含引用来源快照）。"""

    __tablename__ = "message"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = uuid_col()
    conversation_id: Mapped[str] = uuid_col()
    dify_message_id: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="user | assistant")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list | None] = mapped_column(JsonType, comment="retriever_resources 归一化快照")
    audit_status: Mapped[str] = mapped_column(String(16), default="pass", nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    finish_reason: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        Index("idx_message_conv", "conversation_id", "created_at"),
        Index("idx_message_dify", "dify_message_id"),
    )
