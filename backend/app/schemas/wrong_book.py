"""错题本 Schema（docs/05 §8）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.constants import WrongStatus
from app.schemas.chat import SourceItem
from app.schemas.common import ORMModel
from app.schemas.quiz import QuestionOption


class WrongBookUpdateIn(BaseModel):
    status: WrongStatus | None = None
    remark: str | None = Field(default=None, max_length=1000)


class WrongBookItemOut(BaseModel):
    id: str
    question_id: str
    kb_id: str
    kb_name: str | None = None
    type: str
    stem: str
    wrong_count: int
    status: str
    last_wrong_at: datetime | None = None
    remark: str | None = None


class WrongBookDetailOut(WrongBookItemOut):
    options: list[QuestionOption] | None = None
    answer: Any = None
    analysis: str | None = None
    grading_points: list[str] | None = None
    sources: list[SourceItem] | None = None
    last_feedback: str | None = None
    last_user_answer: Any = None


class WrongBookBriefOut(ORMModel):
    """重做接口返回（不含答案）。"""

    id: str
    question_id: str
    type: str
    stem: str
    options: list[QuestionOption] | None = None
    status: str
    wrong_count: int
