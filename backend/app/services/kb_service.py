"""知识库服务：CRUD、成员权限、检索测试（docs/05 §3）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, KbContext, resolve_kb_role
from app.core.config import settings
from app.core.constants import KbRole, Visibility
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.logging import get_logger
from app.models.knowledge import Kb, KbMember
from app.models.org import AppUser
from app.schemas.kb import KbCreateIn, KbUpdateIn, MemberAddIn, MemberUpdateIn, RetrievalTestIn
from app.services.dify import get_dify_client
from app.services.support import paginate

logger = get_logger(__name__)

_UPDATABLE = (
    "name",
    "description",
    "visibility",
    "chunk_size",
    "chunk_overlap",
    "top_k",
    "score_threshold",
    "retrieval_mode",
    "rerank_enabled",
    "semantic_weight",
)


def get_default_kb(db: Session, user: CurrentUser) -> Kb:
    """单知识库模式：返回「唯一知识库」（本地无记录时按配置补一条本地记录）。

    关键点：这里**只把本地记录绑定到既有 Dify 数据集**（`settings.default_kb_dataset_id`），
    绝不调用 Dify `POST /datasets` —— 所以用户上传文档时不会再冒出新的知识库。
    非 owner 的普通用户会被自动补一条 editor 成员记录，保证「人人可上传」。
    """
    if not settings.default_kb_dataset_id:
        raise bad_request("未配置 DEFAULT_KB_DATASET_ID，无法定位唯一知识库")
    kb = _find_default_kb(db, user)
    if kb is None:
        kb = Kb(
            tenant_id=user.tenant_id,
            name=settings.default_kb_name,
            description="单知识库模式：所有用户上传的资料都进入这里",
            owner_id=user.id,
            visibility=Visibility.TENANT.value,
            dify_dataset_id=settings.default_kb_dataset_id,
            status=1,
        )
        db.add(kb)
        db.flush()
        logger.info(
            "单知识库模式：已绑定既有 Dify 数据集 kb=%s dataset=%s",
            kb.id,
            kb.dify_dataset_id,
        )
    else:
        # 复用时校正绑定：运维改了 DEFAULT_KB_DATASET_ID、或该库曾被软删，都要拉回唯一库状态
        if kb.dify_dataset_id != settings.default_kb_dataset_id:
            logger.warning(
                "单知识库模式：本地库 kb=%s 数据集 %s 重绑为 %s",
                kb.id,
                kb.dify_dataset_id,
                settings.default_kb_dataset_id,
            )
            kb.dify_dataset_id = settings.default_kb_dataset_id
        if kb.is_deleted or kb.status != 1:
            kb.is_deleted = False
            kb.deleted_at = None
            kb.status = 1
        db.flush()
    _ensure_default_member(db, kb, user)
    return kb


def _find_default_kb(db: Session, user: CurrentUser) -> Kb | None:
    """两级查找唯一知识库：先按数据集绑定，再按同名（大小写不敏感）。

    第二级是必须的：`kb` 表有唯一索引 `uk_kb_tenant_name(tenant_id, lower(name))`，
    若本地已存在同名记录却还去新建，会直接撞唯一约束（500）。
    """
    bound = db.scalar(
        select(Kb).where(
            Kb.tenant_id == user.tenant_id,
            Kb.dify_dataset_id == settings.default_kb_dataset_id,
            Kb.is_deleted.is_(False),
        )
    )
    if bound is not None:
        return bound
    name_filter = (
        Kb.tenant_id == user.tenant_id,
        func.lower(Kb.name) == settings.default_kb_name.lower(),
    )
    named = db.scalar(
        select(Kb).where(*name_filter, Kb.is_deleted.is_(False)).order_by(Kb.created_at.desc())
    )
    if named is not None:
        return named
    return db.scalar(select(Kb).where(*name_filter).order_by(Kb.created_at.desc()))



def _ensure_default_member(db: Session, kb: Kb, user: CurrentUser) -> None:
    """唯一知识库对所有登录用户开放可写：非 owner 且无成员记录时补一条 editor。"""
    if str(kb.owner_id) == user.id:
        return
    member = db.scalar(
        select(KbMember).where(KbMember.kb_id == kb.id, KbMember.user_id == user.id)
    )
    if member is None:
        db.add(
            KbMember(
                tenant_id=str(kb.tenant_id),
                kb_id=kb.id,
                user_id=user.id,
                role=KbRole.EDITOR.value,
            )
        )
        db.flush()


def list_kbs(
    db: Session,
    user: CurrentUser,
    *,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Kb], int]:
    """我的知识库列表（owner / 成员 / 租户可见）。

    单知识库模式下只有一个「唯一知识库」对外可见，其余历史库不再出现在列表里
    （本地记录保留，`SINGLE_KB_MODE=false` 即可恢复多库视图）。
    """
    if settings.single_kb_mode:
        kb = get_default_kb(db, user)
        setattr(kb, "role", resolve_kb_role(db, kb, user) or KbRole.EDITOR.value)
        return [kb], 1

    member_ids = select(KbMember.kb_id).where(KbMember.user_id == user.id)
    stmt = select(Kb).where(
        Kb.tenant_id == user.tenant_id,
        Kb.is_deleted.is_(False),
        or_(
            Kb.owner_id == user.id,
            Kb.id.in_(member_ids),
            Kb.visibility == Visibility.TENANT.value,
        ),
    )
    if keyword:
        stmt = stmt.where(func.lower(Kb.name).contains(keyword.lower()))
    stmt = stmt.order_by(Kb.updated_at.desc())
    items, total = paginate(db, stmt, page=page, page_size=page_size)
    for kb in items:
        setattr(kb, "role", resolve_kb_role(db, kb, user) or KbRole.VIEWER.value)
    return items, total


def create_kb(db: Session, user: CurrentUser, payload: KbCreateIn) -> Kb:
    """创建知识库：先建 Dify dataset，再落库（Dify 失败则整体回滚 → 50201）。

    单知识库模式下**禁止新建**：用户只需上传文档，文档会进 `DEFAULT_KB_DATASET_ID`
    指向的既有知识库（见 `get_default_kb`）。
    """
    if settings.single_kb_mode:
        raise forbidden(
            "当前为单知识库模式：不能新建知识库，"
            f"直接把文档上传到「{settings.default_kb_name}」即可"
        )
    dup = db.scalar(
        select(Kb).where(
            Kb.tenant_id == user.tenant_id,
            Kb.is_deleted.is_(False),
            func.lower(Kb.name) == payload.name.lower(),
        )
    )
    if dup is not None:
        raise conflict("同名知识库已存在")
    dataset = get_dify_client().create_dataset(
        name=payload.name, description=payload.description or ""
    )
    dataset_id = str(dataset.get("id") or dataset.get("dataset_id") or "")
    kb = Kb(
        tenant_id=user.tenant_id,
        name=payload.name,
        description=payload.description,
        owner_id=user.id,
        visibility=payload.visibility.value,
        dify_dataset_id=dataset_id or None,
        chunk_size=payload.chunk_size,
        chunk_overlap=payload.chunk_overlap,
        top_k=payload.top_k,
        score_threshold=payload.score_threshold,
        retrieval_mode=payload.retrieval_mode.value,
        rerank_enabled=payload.rerank_enabled,
        semantic_weight=payload.semantic_weight,
        status=1,
    )
    db.add(kb)
    db.flush()
    db.add(
        KbMember(tenant_id=user.tenant_id, kb_id=kb.id, user_id=user.id, role=KbRole.OWNER.value)
    )
    db.flush()
    logger.info("知识库创建成功 kb=%s dataset=%s", kb.id, dataset_id)
    setattr(kb, "role", KbRole.OWNER.value)
    return kb


def update_kb(db: Session, ctx: KbContext, payload: KbUpdateIn) -> Kb:
    kb = ctx.kb
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "name" in data and str(data["name"]).lower() != kb.name.lower():
        dup = db.scalar(
            select(Kb).where(
                Kb.tenant_id == kb.tenant_id,
                Kb.is_deleted.is_(False),
                func.lower(Kb.name) == str(data["name"]).lower(),
                Kb.id != kb.id,
            )
        )
        if dup is not None:
            raise conflict("同名知识库已存在")
    for key in _UPDATABLE:
        if key in data:
            value = data[key]
            setattr(kb, key, value.value if hasattr(value, "value") else value)
    if kb.chunk_overlap >= kb.chunk_size:
        raise bad_request("chunk_overlap 必须小于 chunk_size")
    db.flush()
    setattr(kb, "role", ctx.role)
    return kb


def delete_kb(db: Session, ctx: KbContext) -> None:
    """软删本地 + 同步删 Dify dataset（失败仅记日志，不阻塞用户操作）。

    单知识库模式下唯一知识库不可删除（删了就没地方上传，且会连带删掉 Dify 侧数据集）。
    """
    kb = ctx.kb
    if settings.single_kb_mode and kb.dify_dataset_id == settings.default_kb_dataset_id:
        raise forbidden(f"当前为单知识库模式：唯一知识库「{kb.name}」不可删除")
    kb.mark_deleted()
    kb.status = 0
    db.flush()
    if kb.dify_dataset_id:
        try:
            get_dify_client().delete_dataset(dataset_id=kb.dify_dataset_id)
        except Exception as exc:  # 50201 等；Dify 侧残留由运维脚本清理
            logger.warning(
                "删除 Dify dataset 失败 kb=%s dataset=%s err=%s", kb.id, kb.dify_dataset_id, exc
            )


def list_members(db: Session, ctx: KbContext) -> list[dict[str, Any]]:
    rows = db.execute(
        select(KbMember, AppUser)
        .join(AppUser, AppUser.id == KbMember.user_id)
        .where(KbMember.kb_id == ctx.kb_id)
        .order_by(KbMember.created_at)
    ).all()
    return [_member_dict(member, user) for member, user in rows]


def add_member(db: Session, ctx: KbContext, payload: MemberAddIn) -> dict[str, Any]:
    user = db.get(AppUser, payload.user_id)
    if user is None or str(user.tenant_id) != ctx.kb.tenant_id:
        raise not_found("用户不存在")
    exists = db.scalar(
        select(KbMember).where(KbMember.kb_id == ctx.kb_id, KbMember.user_id == user.id)
    )
    if exists is not None:
        raise conflict("该用户已是知识库成员")
    member = KbMember(
        tenant_id=ctx.kb.tenant_id, kb_id=ctx.kb_id, user_id=str(user.id), role=payload.role.value
    )
    db.add(member)
    db.flush()
    return _member_dict(member, user)


def update_member(
    db: Session, ctx: KbContext, user_id: str, payload: MemberUpdateIn
) -> dict[str, Any]:
    if user_id == str(ctx.kb.owner_id):
        raise bad_request("知识库所有者的角色不可修改")
    member = db.scalar(
        select(KbMember).where(KbMember.kb_id == ctx.kb_id, KbMember.user_id == user_id)
    )
    if member is None:
        raise not_found("成员不存在")
    member.role = payload.role.value
    db.flush()
    return {
        "id": str(member.id),
        "kb_id": str(member.kb_id),
        "user_id": str(member.user_id),
        "role": member.role,
    }


def remove_member(db: Session, ctx: KbContext, user_id: str) -> None:
    if user_id == str(ctx.kb.owner_id):
        raise bad_request("不能移除知识库所有者")
    member = db.scalar(
        select(KbMember).where(KbMember.kb_id == ctx.kb_id, KbMember.user_id == user_id)
    )
    if member is None:
        raise not_found("成员不存在")
    db.delete(member)
    db.flush()


def retrieval_test(db: Session, ctx: KbContext, payload: RetrievalTestIn) -> dict[str, Any]:
    """检索测试（代理 Dify `datasets/{id}/retrieve`），用于调参与排障。"""
    if not ctx.dataset_id:
        raise bad_request("该知识库尚未关联 Dify dataset，请稍后重试")
    top_k = payload.top_k or ctx.kb.top_k
    threshold = payload.score_threshold
    if threshold is None:
        threshold = float(ctx.kb.score_threshold or 0.0)
    segments = get_dify_client().retrieval_test(
        dataset_id=ctx.dataset_id, query=payload.query, top_k=top_k, score_threshold=threshold
    )
    return {
        "query": payload.query,
        "total": len(segments),
        "records": [
            {
                "content": seg.content,
                "score": round(float(seg.score or 0), 4),
                "document_id": seg.document_id,
                "document_name": seg.document_name,
                "segment_id": seg.segment_id,
                "position": seg.position,
            }
            for seg in segments
        ],
    }


def _member_dict(member: KbMember, user: AppUser) -> dict[str, Any]:
    return {
        "id": str(member.id),
        "kb_id": str(member.kb_id),
        "user_id": str(member.user_id),
        "role": member.role,
        "created_at": member.created_at,
        "user": {"id": str(user.id), "email": user.email, "nickname": user.nickname},
    }
