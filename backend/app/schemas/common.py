"""通用 Schema：统一响应体、分页、ID 与时间基类（docs/05 §1）。"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    """可直接从 SQLAlchemy 模型构造。"""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class Resp(BaseModel, Generic[T]):
    """统一响应体：{code, message, data, request_id}。"""

    code: int = 0
    message: str = "ok"
    data: T | None = None
    request_id: str | None = None


class PageQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class TimestampedModel(ORMModel):
    id: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
