"""服务层公共工具：分页、Dify ↔ 本地 ID 反查、用量落库。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.constants import UsageScene
from app.models.documents import Document
from app.models.knowledge import Kb
from app.models.ops import UsageRecord


def paginate(db: Session, stmt: Select, *, page: int, page_size: int) -> tuple[list[Any], int]:
    """统一分页：返回 (items, total)。"""
    total = int(db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
    items = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all())
    return items, total


def dify_document_map(db: Session, dataset_id: str, dify_document_ids: list[str]) -> dict[str, str]:
    """Dify document_id → 本地 document.id（用于 sources 溯源补齐）。"""
    ids = [i for i in dify_document_ids if i]
    if not ids:
        return {}
    rows = db.execute(
        select(Document.dify_document_id, Document.id).where(
            Document.dify_document_id.in_(ids),
            Document.is_deleted.is_(False),
        )
    ).all()
    return {str(dify_id): str(local_id) for dify_id, local_id in rows}


def kb_name_map(db: Session, kb_ids: list[str]) -> dict[str, str]:
    ids = [i for i in kb_ids if i]
    if not ids:
        return {}
    rows = db.execute(select(Kb.id, Kb.name).where(Kb.id.in_(ids))).all()
    return {str(kb_id): str(name) for kb_id, name in rows}


def record_usage(
    db: Session,
    *,
    tenant_id: str,
    user_id: str | None,
    scene: str,
    ref_id: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost: float = 0.0,
    latency_ms: int | None = None,
) -> UsageRecord:
    """写用量记录（chat / quiz / quiz_grade / moderation / parse）。"""
    total = int(prompt_tokens or 0) + int(completion_tokens or 0)
    row = UsageRecord(
        tenant_id=tenant_id,
        user_id=user_id,
        scene=scene if scene in {s.value for s in UsageScene} else UsageScene.CHAT.value,
        ref_id=ref_id,
        prompt_tokens=int(prompt_tokens or 0),
        completion_tokens=int(completion_tokens or 0),
        total_tokens=total,
        cost=cost or 0.0,
        latency_ms=latency_ms,
    )
    db.add(row)
    return row


def clamp_text(text: str | None, limit: int) -> str:
    value = text or ""
    return value if len(value) <= limit else value[:limit] + "…"
