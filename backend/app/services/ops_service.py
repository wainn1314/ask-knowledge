"""运营服务：审核日志与用量统计（docs/05 §9）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.core.errors import not_found
from app.models.ops import AuditLog, UsageRecord
from app.services.support import paginate


def list_audit_logs(
    db: Session,
    user: CurrentUser,
    *,
    scene: str | None = None,
    result: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[AuditLog], int]:
    stmt = select(AuditLog).where(AuditLog.tenant_id == user.tenant_id)
    if scene:
        stmt = stmt.where(AuditLog.scene == scene)
    if result:
        stmt = stmt.where(AuditLog.result == result)
    if start is not None:
        stmt = stmt.where(AuditLog.created_at >= start)
    if end is not None:
        stmt = stmt.where(AuditLog.created_at <= end)
    stmt = stmt.order_by(AuditLog.created_at.desc())
    return paginate(db, stmt, page=page, page_size=page_size)


def get_audit_log(db: Session, user: CurrentUser, log_id: str) -> AuditLog:
    row = db.scalar(
        select(AuditLog).where(AuditLog.id == log_id, AuditLog.tenant_id == user.tenant_id)
    )
    if row is None:
        raise not_found("审核记录不存在")
    return row


def usage_stats(
    db: Session,
    user: CurrentUser,
    *,
    scene: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    group_by: str = "day",
) -> dict[str, Any]:
    """用量聚合：`group_by=day`（按日）或 `user`（按用户）。"""
    if group_by not in {"day", "user"}:
        group_by = "day"
    key_expr = (
        func.date(UsageRecord.created_at) if group_by == "day" else UsageRecord.user_id
    )
    stmt = select(
        key_expr.label("key"),
        func.count(UsageRecord.id),
        func.coalesce(func.sum(UsageRecord.prompt_tokens), 0),
        func.coalesce(func.sum(UsageRecord.completion_tokens), 0),
        func.coalesce(func.sum(UsageRecord.total_tokens), 0),
        func.coalesce(func.sum(UsageRecord.cost), 0),
        func.coalesce(func.avg(UsageRecord.latency_ms), 0),
    ).where(UsageRecord.tenant_id == user.tenant_id)
    if scene:
        stmt = stmt.where(UsageRecord.scene == scene)
    if start is not None:
        stmt = stmt.where(UsageRecord.created_at >= start)
    if end is not None:
        stmt = stmt.where(UsageRecord.created_at <= end)
    stmt = stmt.group_by(key_expr).order_by(key_expr.desc()).limit(200)

    items: list[dict[str, Any]] = []
    total_tokens = 0
    total_cost = 0.0
    for key, requests, prompt, completion, total, cost, latency in db.execute(stmt).all():
        total_tokens += int(total or 0)
        total_cost += float(cost or 0)
        items.append(
            {
                "key": str(key),
                "scene": scene,
                "requests": int(requests or 0),
                "prompt_tokens": int(prompt or 0),
                "completion_tokens": int(completion or 0),
                "total_tokens": int(total or 0),
                "cost": round(float(cost or 0), 6),
                "avg_latency_ms": round(float(latency or 0), 2),
            }
        )
    return {
        "group_by": group_by,
        "items": items,
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 6),
    }
