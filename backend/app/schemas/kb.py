"""知识库与成员 Schema（docs/05 §3）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.core.config import settings
from app.core.constants import KbRole, RetrievalMode, Visibility
from app.schemas.common import ORMModel


class KbConfigIn(BaseModel):
    """检索与分块配置（默认值来自 .env，可按库覆盖）。"""

    chunk_size: int = Field(default=settings.default_chunk_size, ge=100, le=2000)
    chunk_overlap: int = Field(default=settings.default_chunk_overlap, ge=0, le=1000)
    top_k: int = Field(default=settings.default_top_k, ge=1, le=20)
    score_threshold: float = Field(default=settings.default_score_threshold, ge=0.0, le=1.0)
    retrieval_mode: RetrievalMode = RetrievalMode.HYBRID
    rerank_enabled: bool = True
    semantic_weight: float = Field(default=0.7, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_overlap(self) -> "KbConfigIn":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        return self


class KbCreateIn(KbConfigIn):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)
    visibility: Visibility = Visibility.PRIVATE


class KbUpdateIn(BaseModel):
    """部分更新：只传需要改的字段。"""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)
    visibility: Visibility | None = None
    chunk_size: int | None = Field(default=None, ge=100, le=2000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=1000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    retrieval_mode: RetrievalMode | None = None
    rerank_enabled: bool | None = None
    semantic_weight: float | None = Field(default=None, ge=0.0, le=1.0)


class KbOut(ORMModel):
    id: str
    tenant_id: str
    name: str
    description: str | None = None
    icon: str | None = None
    owner_id: str
    visibility: str
    dify_dataset_id: str | None = None
    chunk_size: int
    chunk_overlap: int
    top_k: int
    score_threshold: float
    retrieval_mode: str
    rerank_enabled: bool
    semantic_weight: float
    doc_count: int
    status: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    role: str | None = Field(default=None, description="当前用户在该库的角色")


class MemberAddIn(BaseModel):
    user_id: str
    role: KbRole = KbRole.VIEWER


class MemberUpdateIn(BaseModel):
    role: KbRole


class MemberUserOut(ORMModel):
    id: str
    email: str
    nickname: str | None = None


class MemberOut(ORMModel):
    id: str
    kb_id: str
    user_id: str
    role: str
    created_at: datetime | None = None
    user: MemberUserOut | None = None


class RetrievalTestIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int | None = Field(default=None, ge=1, le=20)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class RetrievalRecord(BaseModel):
    content: str
    score: float
    document_id: str | None = None
    document_name: str | None = None
    segment_id: str | None = None
    position: int | None = None


class RetrievalTestOut(BaseModel):
    query: str
    records: list[RetrievalRecord]
    total: int
