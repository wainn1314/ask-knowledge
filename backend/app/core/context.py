"""请求上下文：request_id / tenant_id / user_id（ContextVar，避免层层传参）。"""

from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4

_request_id: ContextVar[str] = ContextVar("request_id", default="")
_tenant_id: ContextVar[str | None] = ContextVar("tenant_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)
_tenant_code: ContextVar[str | None] = ContextVar("tenant_code", default=None)


def new_request_id() -> str:
    return uuid4().hex


def set_request_id(value: str) -> None:
    _request_id.set(value)


def get_request_id() -> str:
    rid = _request_id.get()
    if not rid:
        rid = new_request_id()
        _request_id.set(rid)
    return rid


def set_identity(tenant_id: str | None, user_id: str | None, tenant_code: str | None = None) -> None:
    _tenant_id.set(tenant_id)
    _user_id.set(user_id)
    _tenant_code.set(tenant_code)


def get_tenant_id() -> str | None:
    return _tenant_id.get()


def get_user_id() -> str | None:
    return _user_id.get()


def get_tenant_code() -> str | None:
    return _tenant_code.get()


def dify_user_key() -> str:
    """传给 Dify 的 user 字段：'{tenant_code}:{user_id}'（见 docs/04 §6）。"""
    return f"{_tenant_code.get() or 'default'}:{_user_id.get() or 'anonymous'}"
