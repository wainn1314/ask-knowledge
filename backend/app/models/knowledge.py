"""知识库与成员权限模型（对应 docs/03 §3）。"""

from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, Numeric, SmallInteger, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.constants import RetrievalMode, Visibility
from app.db.base import Base, SoftDeleteMixin, TimestampMixin, partial_index, uuid_col, uuid_pk


class Kb(Base, TimestampMixin, SoftDeleteMixin):
    """知识库（与 Dify dataset 一对一）。"""

    __tablename__ = "kb"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = uuid_col()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    icon: Mapped[str | None] = mapped_column(String(255))
    owner_id: Mapped[str] = uuid_col()
    visibility: Mapped[str] = mapped_column(
        String(16), default=Visibility.PRIVATE.value, nullable=False
    )

    # ── Dify 映射 ────────────────────────────────────────────────────
    dify_dataset_id: Mapped[str | None] = mapped_column(String(64))
    embedding_model: Mapped[str | None] = mapped_column(String(128))

    # ── 检索与分块配置（默认值来自 .env，可被 KB 覆盖） ────────────────
    chunk_size: Mapped[int] = mapped_column(
        Integer,
        default=settings.default_chunk_size,
        nullable=False,
        comment="分块长度，单位：字符（Dify 语义，见 docs/04 §8）",
    )
    chunk_overlap: Mapped[int] = mapped_column(
        Integer, default=settings.default_chunk_overlap, nullable=False
    )
    top_k: Mapped[int] = mapped_column(Integer, default=settings.default_top_k, nullable=False)
    score_threshold: Mapped[float] = mapped_column(
        Numeric(4, 3), default=settings.default_score_threshold, nullable=False
    )
    retrieval_mode: Mapped[str] = mapped_column(
        String(16), default=RetrievalMode.HYBRID.value, nullable=False
    )
    rerank_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    semantic_weight: Mapped[float] = mapped_column(Numeric(4, 3), default=0.700, nullable=False)
    doc_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    status: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)

    __table_args__ = (
        partial_index("uk_kb_tenant_name", "tenant_id", text("lower(name)"), unique=True),
        partial_index("idx_kb_tenant", "tenant_id", "created_at"),
    )


class KbMember(Base, TimestampMixin):
    """知识库成员权限：owner（管理+授权）/ editor（上传+问答）/ viewer（仅问答）。"""

    __tablename__ = "kb_member"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = uuid_col()
    kb_id: Mapped[str] = uuid_col()
    user_id: Mapped[str] = uuid_col()
    role: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        Index("uk_kb_member", "kb_id", "user_id", unique=True),
        Index("idx_kb_member_user", "tenant_id", "user_id"),
    )
