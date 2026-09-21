"""租户与用户模型（对应 docs/03 §2）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import Role
from app.db.base import Base, TZDateTime, TimestampMixin, uuid_col, uuid_pk


class Tenant(Base, TimestampMixin):
    """租户（组织/客户）。MVP 只有一个，字段从 Day 1 建好。"""

    __tablename__ = "tenant"

    id: Mapped[str] = uuid_pk()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="租户短码，Dify user 前缀")
    status: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False, comment="1 启用/0 停用")

    __table_args__ = (Index("uk_tenant_code", "code", unique=True),)


class AppUser(Base, TimestampMixin):
    """用户。"""

    __tablename__ = "app_user"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = uuid_col()
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), default=Role.MEMBER.value, nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    __table_args__ = (
        Index("uk_user_tenant_email", "tenant_id", text("lower(email)"), unique=True),
        Index("idx_user_tenant", "tenant_id"),
    )

    @property
    def is_admin(self) -> bool:
        return self.role in (Role.OWNER.value, Role.ADMIN.value)
