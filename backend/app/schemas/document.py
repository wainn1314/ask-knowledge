"""文档 Schema（docs/05 §4）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class DocumentOut(ORMModel):
    id: str
    tenant_id: str
    kb_id: str
    uploader_id: str
    filename: str
    file_ext: str
    file_size: int
    sha256: str
    status: str
    stage_progress: int
    audit_status: str
    parse_mode: str | None = None
    parse_duration_ms: int | None = None
    page_count: int | None = None
    chunk_count: int | None = None
    dify_document_id: str | None = None
    dify_indexing_status: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DocumentStatusOut(BaseModel):
    """前端 2s 轮询用的轻量状态。"""

    document_id: str
    status: str
    stage_progress: int
    audit_status: str
    dify_indexing_status: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int = 0


class DocumentPreviewOut(BaseModel):
    document_id: str
    filename: str
    mode: str | None = None
    page: int
    page_size: int
    total_chars: int
    truncated: bool
    content: str


class SegmentOut(BaseModel):
    segment_id: str
    position: int
    chars: int
    content: str


class ChunkListOut(BaseModel):
    document_id: str
    total: int
    items: list[SegmentOut]


class UploadAccepted(BaseModel):
    document_id: str
    task_id: str | None = None
    filename: str
    status: str


class UploadRejected(BaseModel):
    filename: str
    code: int
    message: str


class UploadResult(BaseModel):
    accepted: list[UploadAccepted]
    rejected: list[UploadRejected] = Field(default_factory=list)


class DocumentRetryIn(BaseModel):
    """重试阶段：不传则从解析开始整条链路重跑。"""

    stage: Literal["parse", "audit", "index"] | None = None
