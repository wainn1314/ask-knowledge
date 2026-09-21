"""FastAPI 依赖：JWT 鉴权、租户注入、KB 权限校验、内部/回调 Token（docs/05 §1）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import KbRole, Role, Visibility
from app.core.errors import forbidden, not_found, unauthorized
from app.core.security import decode_token
from app.db.session import get_db
from app.models.knowledge import Kb, KbMember
from app.models.org import AppUser, Tenant

bearer_scheme = HTTPBearer(auto_error=False, description="Bearer <JWT>")

#: KB 角色权重（owner > editor > viewer）
KB_RANK: dict[str, int] = {KbRole.OWNER.value: 3, KbRole.EDITOR.value: 2, KbRole.VIEWER.value: 1}


@dataclass(slots=True)
class CurrentUser:
    """鉴权后的当前用户（含租户上下文）。"""

    id: str
    tenant_id: str
    tenant_code: str
    role: str
    email: str
    nickname: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role in (Role.OWNER.value, Role.ADMIN.value)

    @property
    def dify_user(self) -> str:
        """传给 Dify 的 user 字段（跨租户隔离，见 docs/04 §6）。"""
        return f"{self.tenant_code}:{self.id}"


@dataclass(slots=True)
class KbContext:
    """KB 权限校验结果。"""

    kb: Kb
    role: str
    user: CurrentUser

    @property
    def kb_id(self) -> str:
        return self.kb.id

    @property
    def dataset_id(self) -> str:
        return self.kb.dify_dataset_id or ""

    def can_edit(self) -> bool:
        return KB_RANK[self.role] >= KB_RANK[KbRole.EDITOR.value]

    def can_manage(self) -> bool:
        return self.role == KbRole.OWNER.value


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> CurrentUser:
    """解析 Bearer JWT 并加载用户（含租户校验）。"""
    if credentials is None or not credentials.credentials:
        raise unauthorized("缺少登录凭证")
    payload = decode_token(credentials.credentials, "access")
    user_id = str(payload.get("sub") or "")
    tenant_id = str(payload.get("tid") or "")
    user = db.get(AppUser, user_id)
    if user is None or user.status != 1 or str(user.tenant_id) != tenant_id:
        raise unauthorized("用户不存在或已停用")
    tenant = db.get(Tenant, tenant_id)
    if tenant is None or tenant.status != 1:
        raise unauthorized("租户不存在或已停用")
    return CurrentUser(
        id=str(user.id),
        tenant_id=str(user.tenant_id),
        tenant_code=tenant.code,
        role=user.role,
        email=user.email,
        nickname=user.nickname,
    )


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """管理员依赖（owner / admin）。"""
    if not user.is_admin:
        raise forbidden("需要管理员权限")
    return user


def require_internal_token(x_internal_token: str = Header(default="")) -> None:
    """内部接口 Token（Dify → 后端回调）。"""
    if not settings.moderation_callback_token:
        return
    if x_internal_token != settings.moderation_callback_token:
        raise forbidden("内部 Token 校验失败")


def require_moderation_token(x_moderation_token: str = Header(default="")) -> None:
    """Dify 审核扩展点 Token。"""
    if not settings.moderation_callback_token:
        return
    if x_moderation_token != settings.moderation_callback_token:
        raise forbidden("审核回调 Token 校验失败")


def _is_default_kb(kb: Kb, user: CurrentUser) -> bool:
    """单知识库模式下的「唯一知识库」判定（按配置的数据集 id 绑定 + 同租户）。"""
    return bool(
        settings.single_kb_mode
        and settings.default_kb_dataset_id
        and kb.dify_dataset_id == settings.default_kb_dataset_id
        and str(kb.tenant_id) == user.tenant_id
    )


def resolve_kb_role(db: Session, kb: Kb, user: CurrentUser) -> str | None:
    """计算用户在某 KB 上的角色；无权限返回 None（调用方统一映射 40401）。"""
    member = db.scalar(
        select(KbMember).where(KbMember.kb_id == kb.id, KbMember.user_id == user.id)
    )
    if member is not None:
        return member.role
    if str(kb.owner_id) == user.id:
        return KbRole.OWNER.value
    if _is_default_kb(kb, user):
        # 单知识库模式兜底：唯一知识库对所有本租户登录用户开放上传。
        # 必须放在 tenant 可见性之前：否则普通成员只会拿到 viewer，上传仍是 403；
        # 也让「没先访问过 /kbs/default」的直连接口调用不会踩空。
        db.add(
            KbMember(
                tenant_id=str(kb.tenant_id),
                kb_id=kb.id,
                user_id=user.id,
                role=KbRole.EDITOR.value,
            )
        )
        db.flush()
        return KbRole.EDITOR.value
    if kb.visibility == Visibility.TENANT.value and str(kb.tenant_id) == user.tenant_id:
        return KbRole.EDITOR.value if user.is_admin else KbRole.VIEWER.value
    return None




def get_kb_context(
    kb_id: str,
    db: Session,
    user: CurrentUser,
    min_role: str = KbRole.VIEWER.value,
) -> KbContext:
    """业务层可复用：校验并返回 KB 上下文（越权/不存在统一 40401）。"""
    kb = db.scalar(
        select(Kb).where(
            Kb.id == kb_id, Kb.tenant_id == user.tenant_id, Kb.is_deleted.is_(False)
        )
    )
    if kb is None:
        raise not_found("知识库不存在")
    role = resolve_kb_role(db, kb, user)
    if role is None:
        raise not_found("知识库不存在")
    if KB_RANK.get(role, 0) < KB_RANK.get(min_role, 1):
        raise forbidden("当前角色无该操作权限")
    return KbContext(kb=kb, role=role, user=user)


def kb_guard(min_role: str = KbRole.VIEWER.value) -> Callable[..., KbContext]:
    """依赖工厂：`ctx: KbContext = Depends(kb_guard(KbRole.EDITOR))`。"""

    def dependency(
        kb_id: str,
        db: Session = Depends(get_db),
        user: CurrentUser = Depends(get_current_user),
    ) -> KbContext:
        return get_kb_context(kb_id, db, user, min_role)

    return dependency


def accessible_kb_ids(db: Session, user: CurrentUser) -> list[str]:
    """当前用户可访问的 KB 列表（用于「问答覆盖哪些库」时计算 Dify dataset_ids）。"""
    kbs = db.scalars(
        select(Kb).where(
            Kb.tenant_id == user.tenant_id, Kb.is_deleted.is_(False), Kb.status == 1
        )
    ).all()
    member_ids = {
        str(kb_id)
        for kb_id in db.scalars(select(KbMember.kb_id).where(KbMember.user_id == user.id)).all()
    }
    return [
        str(kb.id)
        for kb in kbs
        if str(kb.owner_id) == user.id
        or str(kb.id) in member_ids
        or kb.visibility == Visibility.TENANT.value
    ]
