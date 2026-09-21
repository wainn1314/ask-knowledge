"""认证与用户 Schema（docs/05 §2）。

注：不使用 pydantic `EmailStr`（需额外安装 email-validator），改用正则校验邮箱，
保持与 `pyproject.toml` 现有依赖一致。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.common import ORMModel

EMAIL_PATTERN = r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"
Email = Annotated[str, StringConstraints(pattern=EMAIL_PATTERN, max_length=255)]
Password = Annotated[str, StringConstraints(min_length=6, max_length=64)]


class RegisterIn(BaseModel):
    email: Email
    password: Password
    nickname: str | None = Field(default=None, max_length=64)


class LoginIn(BaseModel):
    email: Email
    password: Password


class RefreshIn(BaseModel):
    refresh_token: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int
    user: "MeOut"


class TenantOut(ORMModel):
    id: str
    name: str
    code: str


class UserOut(ORMModel):
    id: str
    email: str
    nickname: str | None = None
    role: str
    tenant_id: str


class MeOut(UserOut):
    tenant: TenantOut | None = None


class UserUpdateIn(BaseModel):
    """改昵称 / 改密码（改密码必须带旧密码）。"""

    nickname: str | None = Field(default=None, max_length=64)
    old_password: Password | None = None
    new_password: Password | None = None


TokenOut.model_rebuild()
