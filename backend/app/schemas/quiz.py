"""出题 / 判分 Schema（docs/05 §7）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.constants import Difficulty, QuestionType, QuizStatus
from app.schemas.chat import SourceItem
from app.schemas.common import ORMModel

AnswerValue = str | list[str] | bool | None


class QuizCreateIn(BaseModel):
    kb_id: str
    types: list[QuestionType] = Field(min_length=1)
    count: int = Field(default=10, ge=1, le=30)
    difficulty: Difficulty = Difficulty.MEDIUM
    topic: str | None = Field(default=None, max_length=200)

    @field_validator("types")
    @classmethod
    def _dedup_types(cls, value: list[QuestionType]) -> list[QuestionType]:
        seen: list[QuestionType] = []
        for item in value:
            if item not in seen:
                seen.append(item)
        if not seen:
            raise ValueError("types 至少需要 1 项")
        return seen


class QuestionOption(BaseModel):
    key: str
    text: str


class QuestionOut(BaseModel):
    """题目输出；`include_answer=false` 时 answer/analysis/grading_points 不下发。"""

    id: str
    seq: int
    type: str
    difficulty: str
    stem: str
    options: list[QuestionOption] | None = None
    answer: Any = None
    analysis: str | None = None
    grading_points: list[str] | None = None
    sources: list[SourceItem] | None = None


class QuizOut(BaseModel):
    quiz_id: str
    status: str
    question_count: int
    title: str | None = None
    questions: list[QuestionOut] = Field(default_factory=list)


class QuizBriefOut(ORMModel):
    id: str
    kb_id: str
    title: str | None = None
    config: dict[str, Any] | None = None
    question_count: int
    status: str
    created_at: datetime | None = None
    kb_name: str | None = None


class QuizDetailOut(QuizBriefOut):
    questions: list[QuestionOut] = Field(default_factory=list)


class AnswerIn(BaseModel):
    question_id: str
    answer: AnswerValue = None


class AnswerResultItem(BaseModel):
    question_id: str
    is_correct: bool | None = None
    score: float | None = None
    correct_answer: Any = None
    user_answer: AnswerValue = None
    feedback: str | None = None
    analysis: str | None = None
    type: str | None = None


class QuizResultOut(BaseModel):
    quiz_id: str
    total: int
    correct: int
    score: float
    items: list[AnswerResultItem]
    wrong_question_ids: list[str] = Field(default_factory=list)


__all__ = [
    "AnswerIn",
    "AnswerResultItem",
    "AnswerValue",
    "QuestionOption",
    "QuestionOut",
    "QuizBriefOut",
    "QuizCreateIn",
    "QuizDetailOut",
    "QuizOut",
    "QuizResultOut",
    "QuizStatus",
]
