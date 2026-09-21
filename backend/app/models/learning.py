"""出题 / 作答 / 错题本模型（对应 docs/03 §5）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, Numeric, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import QuizStatus, WrongStatus
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


class Quiz(Base, TimestampMixin, SoftDeleteMixin):
    """一次出题记录。"""

    __tablename__ = "quiz"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = uuid_col()
    kb_id: Mapped[str] = uuid_col()
    creator_id: Mapped[str] = uuid_col()
    title: Mapped[str | None] = mapped_column(String(255))
    config: Mapped[dict] = mapped_column(JsonType, nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dify_workflow_run_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(16), default=QuizStatus.GENERATING.value, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (partial_index("idx_quiz_kb", "tenant_id", "kb_id", "created_at"),)


class Question(Base, TimestampMixin):
    """题目（含引用快照 source_segments，用于溯源）。"""

    __tablename__ = "question"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = uuid_col()
    quiz_id: Mapped[str] = uuid_col()
    kb_id: Mapped[str] = uuid_col()
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False, comment="single/multi/judge/short")
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list | None] = mapped_column(JsonType, comment='[{"key":"A","text":"..."}]')
    answer: Mapped[object] = mapped_column(JsonType, nullable=False, comment="str | list | bool | 文本")
    analysis: Mapped[str | None] = mapped_column(Text)
    difficulty: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    grading_points: Mapped[list | None] = mapped_column(JsonType, comment="简答题评分要点")
    source_segments: Mapped[list | None] = mapped_column(JsonType, comment="引用来源快照")

    __table_args__ = (
        Index("idx_question_quiz", "quiz_id", "seq"),
        Index("idx_question_kb", "tenant_id", "kb_id"),
    )


class AnswerRecord(Base, TimestampMixin):
    """一次作答（客观题规则判分，简答题 LLM 判分）。"""

    __tablename__ = "answer_record"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = uuid_col()
    user_id: Mapped[str] = uuid_col()
    quiz_id: Mapped[str] = uuid_col()
    question_id: Mapped[str] = uuid_col()
    user_answer: Mapped[object | None] = mapped_column(JsonType)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    feedback: Mapped[str | None] = mapped_column(Text)
    cost_ms: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("idx_answer_user", "tenant_id", "user_id", "created_at"),
        Index("idx_answer_question", "question_id"),
    )


class WrongBook(Base, TimestampMixin, SoftDeleteMixin):
    """错题本：unmastered / reviewing / mastered。"""

    __tablename__ = "wrong_book"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = uuid_col()
    user_id: Mapped[str] = uuid_col()
    kb_id: Mapped[str] = uuid_col()
    question_id: Mapped[str] = uuid_col()
    wrong_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_wrong_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=WrongStatus.UNMASTERED.value, nullable=False
    )
    mastered_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    remark: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        partial_index("uk_wrong_user_question", "user_id", "question_id", unique=True),
        partial_index("idx_wrong_user_status", "tenant_id", "user_id", "status"),
    )
