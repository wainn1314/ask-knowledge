"""认证与用户服务（docs/05 §2）。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import Role
from app.core.errors import bad_request, conflict, not_found, unauthorized
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.org import AppUser, Tenant
from app.schemas.auth import LoginIn, RegisterIn, UserUpdateIn

DEFAULT_TENANT_CODE = "default"


def _default_tenant(db: Session) -> Tenant:
    """MVP 单租户：注册用户统一进 `default` 租户（含自动创建，便于零配置启动）。"""
    tenant = db.scalar(select(Tenant).where(Tenant.code == DEFAULT_TENANT_CODE))
    if tenant is None:
        tenant = Tenant(name="默认租户", code=DEFAULT_TENANT_CODE, status=1)
        db.add(tenant)
        db.flush()
    return tenant


def issue_tokens(user: AppUser, tenant: Tenant) -> dict:
    """签发 access / refresh 与用户信息。"""
    return {
        "access_token": create_access_token(
            user_id=str(user.id), tenant_id=str(tenant.id), tenant_code=tenant.code, role=user.role
        ),
        "refresh_token": create_refresh_token(user_id=str(user.id), tenant_id=str(tenant.id)),
        "token_type": "bearer",
        "expires_in": settings.access_token_expire_minutes * 60,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "nickname": user.nickname,
            "role": user.role,
            "tenant_id": str(tenant.id),
            "tenant": {"id": str(tenant.id), "name": tenant.name, "code": tenant.code},
        },
    }


def register(db: Session, payload: RegisterIn) -> dict:
    """注册（MVP 默认注册即登录）。"""
    tenant = _default_tenant(db)
    exists = db.scalar(
        select(AppUser).where(
            AppUser.tenant_id == tenant.id, func.lower(AppUser.email) == payload.email.lower()
        )
    )
    if exists is not None:
        raise conflict("该邮箱已注册")
    user = AppUser(
        tenant_id=str(tenant.id),
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        nickname=payload.nickname or payload.email.split("@")[0],
        role=Role.MEMBER.value,
        status=1,
    )
    db.add(user)
    db.flush()
    return issue_tokens(user, tenant)


def login(db: Session, payload: LoginIn) -> dict:
    user = db.scalar(select(AppUser).where(func.lower(AppUser.email) == payload.email.lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise unauthorized("邮箱或密码错误")
    if user.status != 1:
        raise unauthorized("账号已停用")
    tenant = db.get(Tenant, user.tenant_id)
    if tenant is None or tenant.status != 1:
        raise unauthorized("租户不存在或已停用")
    from app.db.base import utcnow

    user.last_login_at = utcnow()
    db.flush()
    return issue_tokens(user, tenant)


def refresh(db: Session, refresh_token: str) -> dict:
    payload = decode_token(refresh_token, "refresh")
    user = db.get(AppUser, str(payload.get("sub") or ""))
    if user is None or user.status != 1 or str(user.tenant_id) != str(payload.get("tid") or ""):
        raise unauthorized("刷新凭证无效")
    tenant = db.get(Tenant, user.tenant_id)
    if tenant is None or tenant.status != 1:
        raise unauthorized("租户不存在或已停用")
    return issue_tokens(user, tenant)


def get_me(db: Session, user_id: str) -> dict:
    user = db.get(AppUser, user_id)
    if user is None:
        raise not_found("用户不存在")
    tenant = db.get(Tenant, user.tenant_id)
    return {
        "id": str(user.id),
        "email": user.email,
        "nickname": user.nickname,
        "role": user.role,
        "tenant_id": str(user.tenant_id),
        "tenant": (
            {"id": str(tenant.id), "name": tenant.name, "code": tenant.code} if tenant else None
        ),
    }


def update_me(db: Session, user_id: str, payload: UserUpdateIn) -> dict:
    user = db.get(AppUser, user_id)
    if user is None:
        raise not_found("用户不存在")
    if payload.nickname is not None:
        user.nickname = payload.nickname
    if payload.new_password:
        if not payload.old_password:
            raise bad_request("修改密码需提供 old_password")
        if not verify_password(payload.old_password, user.password_hash):
            raise bad_request("原密码不正确")
        user.password_hash = hash_password(payload.new_password)
    db.flush()
    return get_me(db, user_id)
