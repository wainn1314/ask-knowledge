"""认证与安全：密码哈希（stdlib pbkdf2-sha256）+ JWT 签发/校验。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt

from app.core.config import settings
from app.core.errors import unauthorized

_PBKDF2_ITERATIONS = 210_000
_SALT_BYTES = 16
_HASH_NAME = "sha256"


def hash_password(password: str) -> str:
    """返回 pbkdf2_sha256$iterations$salt_b64$hash_b64。"""
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(_HASH_NAME, password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return "$".join(
        [
            f"pbkdf2_{_HASH_NAME}",
            str(_PBKDF2_ITERATIONS),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations, salt_b64, hash_b64 = encoded.split("$")
        if not algo.startswith("pbkdf2_"):
            return False
        digest = hashlib.pbkdf2_hmac(
            algo.removeprefix("pbkdf2_"),
            password.encode("utf-8"),
            base64.b64decode(salt_b64),
            int(iterations),
        )
        return hmac.compare_digest(digest, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


def _encode(payload: dict[str, Any], expires_minutes: int, token_type: str) -> str:
    now = datetime.now(UTC)
    body = {
        **payload,
        "type": token_type,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(body, settings.app_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(*, user_id: str, tenant_id: str, tenant_code: str, role: str) -> str:
    return _encode(
        {"sub": str(user_id), "tid": str(tenant_id), "tcode": tenant_code, "role": role},
        settings.access_token_expire_minutes,
        "access",
    )


def create_refresh_token(*, user_id: str, tenant_id: str) -> str:
    return _encode(
        {"sub": str(user_id), "tid": str(tenant_id)},
        settings.refresh_token_expire_minutes,
        "refresh",
    )


def decode_token(token: str, expected_type: Literal["access", "refresh"] = "access") -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.app_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise unauthorized("登录已过期，请重新登录") from exc
    except jwt.PyJWTError as exc:
        raise unauthorized("无效的登录凭证") from exc
    if payload.get("type") != expected_type:
        raise unauthorized("凭证类型不正确")
    return payload
