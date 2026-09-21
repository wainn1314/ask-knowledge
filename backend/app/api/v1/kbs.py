"""知识库路由（docs/05 §3）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, KbContext, get_current_user, kb_guard, resolve_kb_role
from app.core.config import settings
from app.core.constants import KbRole
from app.core.errors import envelope
from app.db.session import get_db
from app.schemas.kb import (
    KbCreateIn,
    KbOut,
    KbUpdateIn,
    MemberAddIn,
    MemberOut,
    MemberUpdateIn,
    RetrievalTestIn,
)
from app.services import kb_service

router = APIRouter(tags=["kbs"])


@router.get("/kbs", summary="我的知识库列表")
def list_kbs(
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    items, total = kb_service.list_kbs(
        db, user, keyword=keyword, page=page, page_size=page_size
    )
    return envelope(
        0,
        "ok",
        {
            "items": [KbOut.model_validate(kb).model_dump() for kb in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


@router.post("/kbs", status_code=201, summary="创建知识库")
def create_kb(
    payload: KbCreateIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    kb = kb_service.create_kb(db, user, payload)
    return envelope(0, "created", KbOut.model_validate(kb).model_dump())


@router.get("/kbs/default", summary="默认（唯一）知识库")
def get_default_kb(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """单知识库模式：返回开关状态 + 那个「唯一知识库」，前端据此免选库直接上传。

    未开启单知识库模式时返回 `{"single_kb_mode": false, "kb": null}`，前端走多库交互。
    ⚠️ 本路由必须注册在 `/kbs/{kb_id}` 之前，否则 "default" 会被当成 kb_id 解析成 404。
    """
    if not settings.single_kb_mode:
        return envelope(0, "ok", {"single_kb_mode": False, "kb": None})
    kb = kb_service.get_default_kb(db, user)
    data = KbOut.model_validate(kb).model_dump()
    data["role"] = resolve_kb_role(db, kb, user) or KbRole.EDITOR.value
    return envelope(0, "ok", {"single_kb_mode": True, "kb": data})


@router.get("/kbs/{kb_id}", summary="知识库详情")
def get_kb(ctx: KbContext = Depends(kb_guard(KbRole.VIEWER))) -> dict:
    data = KbOut.model_validate(ctx.kb).model_dump()
    data["role"] = ctx.role
    return envelope(0, "ok", data)


@router.patch("/kbs/{kb_id}", summary="修改知识库")
def update_kb(
    payload: KbUpdateIn,
    ctx: KbContext = Depends(kb_guard(KbRole.OWNER)),
    db: Session = Depends(get_db),
) -> dict:
    kb = kb_service.update_kb(db, ctx, payload)
    return envelope(0, "ok", KbOut.model_validate(kb).model_dump())


@router.delete("/kbs/{kb_id}", summary="删除知识库（软删）")
def delete_kb(
    ctx: KbContext = Depends(kb_guard(KbRole.OWNER)), db: Session = Depends(get_db)
) -> dict:
    kb_service.delete_kb(db, ctx)
    return envelope(0, "ok", {"id": ctx.kb_id})


@router.get("/kbs/{kb_id}/members", summary="成员列表")
def list_members(
    ctx: KbContext = Depends(kb_guard(KbRole.VIEWER)), db: Session = Depends(get_db)
) -> dict:
    return envelope(0, "ok", {"items": kb_service.list_members(db, ctx)})


@router.post("/kbs/{kb_id}/members", status_code=201, summary="添加成员")
def add_member(
    payload: MemberAddIn,
    ctx: KbContext = Depends(kb_guard(KbRole.OWNER)),
    db: Session = Depends(get_db),
) -> dict:
    return envelope(0, "created", MemberOut.model_validate(kb_service.add_member(db, ctx, payload)).model_dump())


@router.patch("/kbs/{kb_id}/members/{user_id}", summary="修改成员角色")
def update_member(
    user_id: str,
    payload: MemberUpdateIn,
    ctx: KbContext = Depends(kb_guard(KbRole.OWNER)),
    db: Session = Depends(get_db),
) -> dict:
    return envelope(0, "ok", kb_service.update_member(db, ctx, user_id, payload))


@router.delete("/kbs/{kb_id}/members/{user_id}", summary="移除成员")
def remove_member(
    user_id: str,
    ctx: KbContext = Depends(kb_guard(KbRole.OWNER)),
    db: Session = Depends(get_db),
) -> dict:
    kb_service.remove_member(db, ctx, user_id)
    return envelope(0, "ok", {"user_id": user_id})


@router.post("/kbs/{kb_id}/retrieval-test", summary="检索测试")
def retrieval_test(
    payload: RetrievalTestIn,
    ctx: KbContext = Depends(kb_guard(KbRole.EDITOR)),
    db: Session = Depends(get_db),
) -> dict:
    return envelope(0, "ok", kb_service.retrieval_test(db, ctx, payload))
