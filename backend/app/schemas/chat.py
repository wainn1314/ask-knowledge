"""问答 / 会话 Schema（docs/05 §5-§6）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ChatStreamIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, description="为空则新建会话")
    top_k: int | None = Field(default=None, ge=1, le=20)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    answer_style: str | None = Field(default=None, max_length=200)


class SourceItem(BaseModel):
    """引用来源（Dify retriever_resources 归一化，content 截断 300 字）。"""

    index: int
    dataset_id: str | None = None
    document_id: str | None = None
    document_name: str | None = None
    segment_id: str | None = None
    score: float = 0.0
    content: str = ""
    position: int | None = None


class ChatMetaEvent(BaseModel):
    conversation_id: str
    message_id: str
    kb_id: str


class ChatMessageEvent(BaseModel):
    delta: str


class ChatSourcesEvent(BaseModel):
    items: list[SourceItem]


class ChatUsageEvent(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0


class ChatModerationEvent(BaseModel):
    scene: str = "output"
    flagged: bool = True
    message: str = "内容已被审核拦截"


class ChatErrorEvent(BaseModel):
    code: int
    message: str


class ChatDoneEvent(BaseModel):
    message_id: str
    conversation_id: str
    sources_count: int = 0
    finish_reason: str = "stop"


class ConversationUpdateIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class ConversationOut(ORMModel):
    id: str
    kb_id: str
    title: str | None = None
    message_count: int
    last_message_at: datetime | None = None
    dify_conversation_id: str | None = None
    created_at: datetime | None = None
    kb_name: str | None = None


class ConversationDetailOut(ConversationOut):
    dify_available: bool = True


class MessageOut(ORMModel):
    id: str
    conversation_id: str
    role: str
    content: str
    sources: list[SourceItem] | None = None
    audit_status: str = "pass"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None
    finish_reason: str | None = None
    created_at: datetime | None = None
