"""审核与用量 Schema（docs/05 §9）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ModerationCallbackIn(BaseModel):
    """Dify 内容审核扩展点回调请求。"""

    point: str
    params: dict[str, Any] = Field(default_factory=dict)


class ModerationCallbackOut(BaseModel):
    flagged: bool
    action: str = "direct_output"
    preset_response: str = "ok"


class AuditLogOut(ORMModel):
    id: str
    scene: str
    object_type: str
    object_id: str | None = None
    provider: str
    result: str
    label: str | None = None
    risk_words: list[str] | None = None
    request_id: str | None = None
    content_excerpt: str | None = None
    created_at: datetime | None = None


class AuditLogDetailOut(AuditLogOut):
    raw_response: dict[str, Any] | None = None


class UsageBucketOut(BaseModel):
    key: str = Field(description="group_by=day 时为日期，group_by=user 时为 user_id")
    scene: str | None = None
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    avg_latency_ms: float | None = None


class UsageOut(BaseModel):
    group_by: str = "day"
    items: list[UsageBucketOut]
    total_tokens: int = 0
    total_cost: float = 0.0


class HealthOut(BaseModel):
    status: str = "ok"
    app_env: str
    dify_mode: str
    dify: dict[str, Any] | None = None
    parser_mode: str
    audit_provider: str
    worker_inline: bool
