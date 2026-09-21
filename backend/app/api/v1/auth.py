"""认证与用户路由（docs/05 §2）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.core.errors import envelope
from app.db.session import get_db
from app.schemas.auth import LoginIn, RefreshIn, RegisterIn, UserUpdateIn
from app.services import auth_service

router = APIRouter(tags=["auth"])


@router.post("/auth/register", summary="注册")
def register(payload: RegisterIn, db: Session = Depends(get_db)) -> dict:
    return envelope(0, "ok", auth_service.register(db, payload))


@router.post("/auth/login", summary="登录")
def login(payload: LoginIn, db: Session = Depends(get_db)) -> dict:
    return envelope(0, "ok", auth_service.login(db, payload))


@router.post("/auth/refresh", summary="刷新 Token")
def refresh(payload: RefreshIn, db: Session = Depends(get_db)) -> dict:
    return envelope(0, "ok", auth_service.refresh(db, payload.refresh_token))


@router.get("/users/me", summary="当前用户")
def me(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return envelope(0, "ok", auth_service.get_me(db, user.id))


@router.patch("/users/me", summary="修改昵称 / 密码")
def update_me(
    payload: UserUpdateIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return envelope(0, "ok", auth_service.update_me(db, user.id, payload))
