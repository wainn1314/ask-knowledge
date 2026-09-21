"""审核回调与运营接口（docs/05 §9）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, require_admin, require_moderation_token
from app.core.config import settings
from app.core.constants import AuditScene
from app.core.errors import envelope
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.org import Tenant
from app.schemas.moderation import (
    AuditLogDetailOut,
    AuditLogOut,
    ModerationCallbackIn,
    UsageOut,
)
from app.services import ops_service
from app.services.moderation import LABEL_SAFE, get_moderator

logger = get_logger(__name__)

router = APIRouter(tags=["moderation"])

_BLOCK_MESSAGE = "内容包含违规信息，已被拦截，请调整后重试。"


@router.post("/moderation", summary="Dify 内容审核扩展点回调", dependencies=[Depends(require_moderation_token)])
def moderation_callback(payload: ModerationCallbackIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Dify 会在输入/输出两个卡点回调这里，返回 `{flagged, action, preset_response}`。

    ⚠️ 响应体不能包 `envelope`（Dify 只认这三个字段）；且必须在 1s 内返回。
    """
    params = payload.params or {}
    if settings.dify_app_id and str(params.get("app_id") or "") not in {"", settings.dify_app_id}:
        logger.warning("审核回调 app_id 不匹配: %s", params.get("app_id"))
        return {"flagged": False, "action": "direct_output", "preset_response": "ok"}
    if payload.point == "ping":
        return {"flagged": False, "action": "direct_output", "preset_response": "ok"}

    if payload.point.endswith("input"):
        scene = AuditScene.QUERY.value
        text = str(params.get("query") or "")
    else:
        scene = AuditScene.ANSWER.value
        text = str(params.get("answer") or params.get("llm_output") or params.get("text") or "")
    if not text:
        return {"flagged": False, "action": "direct_output", "preset_response": "ok"}

    tenant_id, user_id = _resolve_identity(db, str(params.get("user_id") or ""))
    verdict = get_moderator().check_text(
        text,
        scene,
        tenant_id=tenant_id,
        object_type="moderation",
        object_id=user_id,
        session=db if tenant_id else None,
    )
    db.commit()
    # 注意：Dify 扩展点是「用户看到之前」的最后一道闸，与 SSE 路径不同（那里无法回撤已流出的文本），
    # 因此只要命中风险词就拦截，不区分 block / review 策略。
    flagged = verdict.result in {"block", "error"} or verdict.label not in {"safe", LABEL_SAFE}
    if flagged:
        return {"flagged": True, "action": "direct_output", "preset_response": _BLOCK_MESSAGE}
    return {"flagged": False, "action": "direct_output", "preset_response": "ok"}


def _resolve_identity(db: Session, user_key: str) -> tuple[str | None, str | None]:
    """Dify 传回的 user 形如 `{tenant_code}:{user_id}`；据此定位租户用于写审核日志。"""
    if not user_key:
        return None, None
    code, sep, user_id = user_key.partition(":")
    lookup = code if sep else user_key
    if not lookup:
        return None, user_id or None
    tenant = db.scalar(select(Tenant).where(Tenant.code == lookup))
    if tenant is None:
        return None, user_id or None
    return str(tenant.id), user_id or None


@router.get("/moderation/audit-logs", summary="审核日志（管理员）")
def list_audit_logs(
    scene: str | None = Query(default=None),
    result: str | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    items, total = ops_service.list_audit_logs(
        db, user, scene=scene, result=result, start=start, end=end, page=page, page_size=page_size
    )
    return envelope(
        0,
        "ok",
        {
            "items": [AuditLogOut.model_validate(row).model_dump() for row in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


@router.get("/moderation/audit-logs/{log_id}", summary="审核日志详情（管理员）")
def get_audit_log(
    log_id: str,
    user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    row = ops_service.get_audit_log(db, user, log_id)
    return envelope(0, "ok", AuditLogDetailOut.model_validate(row).model_dump())


@router.get("/usage", summary="用量统计（管理员）")
def usage(
    scene: str | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    group_by: str = Query(default="day", pattern="^(day|user)$"),
    user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    data = ops_service.usage_stats(
        db, user, scene=scene, start=start, end=end, group_by=group_by
    )
    return envelope(0, "ok", UsageOut.model_validate(data).model_dump())
