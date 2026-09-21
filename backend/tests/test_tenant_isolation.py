"""跨租户 / 跨用户隔离测试（越权统一 40401，docs/05 §1.1）。"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.constants import KbRole, Role
from app.core.security import create_access_token, hash_password
from app.db.session import session_scope
from app.models.org import AppUser, Tenant
from tests.conftest import SAMPLE_MD, register, run_worker, unique, upload_md


def _other_tenant_headers() -> dict[str, str]:
    """直接造第二个租户 + 用户（注册接口只进 default 租户，这里模拟多租户）。"""
    with session_scope() as db:
        tenant = Tenant(name=f"第二租户-{unique('t')}", code=unique("tcode"), status=1)
        db.add(tenant)
        db.flush()
        user = AppUser(
            tenant_id=str(tenant.id),
            email=f"{unique()}@other.com",
            password_hash=hash_password("secret123"),
            nickname="other",
            role=Role.OWNER.value,
            status=1,
        )
        db.add(user)
        db.flush()
        tenant_id, user_id = str(tenant.id), str(user.id)
    token = create_access_token(
        user_id=user_id, tenant_id=tenant_id, tenant_code="other", role=Role.OWNER.value
    )
    return {"Authorization": f"Bearer {token}"}


def test_cross_tenant_isolation(client: TestClient, user: dict, kb: dict) -> None:
    other_headers = _other_tenant_headers()
    doc_id = upload_md(client, user["headers"], kb["id"])

    assert client.get("/api/v1/kbs", headers=other_headers).json()["data"]["total"] == 0
    assert client.get(f"/api/v1/kbs/{kb['id']}", headers=other_headers).status_code == 404
    assert client.get(f"/api/v1/documents/{doc_id}/status", headers=other_headers).status_code == 404
    assert (
        client.post(
            f"/api/v1/kbs/{kb['id']}/retrieval-test",
            headers=other_headers,
            json={"query": "任意问题"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/kbs/{kb['id']}/documents",
            headers=other_headers,
            files=[("files", ("x.md", io.BytesIO(SAMPLE_MD.encode()), "text/markdown"))],
        ).status_code
        == 404
    )


def test_private_kb_invisible_to_same_tenant_member(client: TestClient, user: dict, kb: dict) -> None:
    """同租户非成员：private 库不可见（40401），且看不到他人会话。"""
    mate = register(client)
    assert client.get(f"/api/v1/kbs/{kb['id']}", headers=mate["headers"]).status_code == 404
    assert client.get("/api/v1/kbs", headers=mate["headers"]).json()["data"]["total"] == 0

    doc_id = upload_md(client, user["headers"], kb["id"])
    run_worker()
    with client.stream(
        "POST",
        f"/api/v1/kbs/{kb['id']}/chat/stream",
        headers=user["headers"],
        json={"query": "二叉树遍历方式有哪些"},
    ) as resp:
        raw = "".join(resp.iter_text())
    conv_id = ""
    for line in raw.splitlines():
        if line.startswith("data: ") and '"conversation_id"' in line:
            import json

            conv_id = json.loads(line[6:])["conversation_id"]
            break
    assert conv_id, raw[:200]
    assert client.get(f"/api/v1/conversations/{conv_id}", headers=mate["headers"]).status_code == 404
    assert client.get(f"/api/v1/documents/{doc_id}", headers=mate["headers"]).status_code == 404


def test_member_roles_guard(client: TestClient, user: dict, kb: dict) -> None:
    """成员角色：viewer 只读、editor 可上传、仅 owner 可改库与授权。"""
    mate = register(client)
    headers, kb_id = user["headers"], kb["id"]

    added = client.post(
        f"/api/v1/kbs/{kb_id}/members",
        headers=headers,
        json={"user_id": mate["user_id"], "role": KbRole.VIEWER.value},
    )
    assert added.status_code == 201, added.text

    assert client.get(f"/api/v1/kbs/{kb_id}", headers=mate["headers"]).status_code == 200
    blocked = client.post(
        f"/api/v1/kbs/{kb_id}/documents",
        headers=mate["headers"],
        files=[("files", ("v.md", io.BytesIO(SAMPLE_MD.encode()), "text/markdown"))],
    )
    assert blocked.status_code == 403 and blocked.json()["code"] == 40301
    assert (
        client.patch(f"/api/v1/kbs/{kb_id}", headers=mate["headers"], json={"name": "hack"}).status_code
        == 403
    )
    assert client.get(f"/api/v1/kbs/{kb_id}/members", headers=mate["headers"]).status_code == 200

    # 升为 editor → 可上传，但仍不能改库/授权
    assert (
        client.patch(
            f"/api/v1/kbs/{kb_id}/members/{mate['user_id']}",
            headers=headers,
            json={"role": KbRole.EDITOR.value},
        ).status_code
        == 200
    )
    ok_upload = client.post(
        f"/api/v1/kbs/{kb_id}/documents",
        headers=mate["headers"],
        files=[("files", ("e.md", io.BytesIO(SAMPLE_MD.encode()), "text/markdown"))],
    )
    assert ok_upload.status_code == 202, ok_upload.text
    assert (
        client.post(
            f"/api/v1/kbs/{kb_id}/members",
            headers=mate["headers"],
            json={"user_id": mate["user_id"], "role": KbRole.VIEWER.value},
        ).status_code
        == 403
    )

    # owner 不能被移除 / 不能改角色
    assert (
        client.delete(f"/api/v1/kbs/{kb_id}/members/{user['user_id']}", headers=headers).status_code == 400
    )
    assert (
        client.patch(
            f"/api/v1/kbs/{kb_id}/members/{user['user_id']}",
            headers=headers,
            json={"role": KbRole.VIEWER.value},
        ).status_code
        == 400
    )

    # 移除成员后不可见
    assert (
        client.delete(f"/api/v1/kbs/{kb_id}/members/{mate['user_id']}", headers=headers).status_code == 200
    )
    assert client.get(f"/api/v1/kbs/{kb_id}", headers=mate["headers"]).status_code == 404


def test_tenant_visibility_kb_readable_by_teammate(client: TestClient, user: dict) -> None:
    """visibility=tenant：同租户普通成员只读可见。"""
    headers = user["headers"]
    created = client.post(
        "/api/v1/kbs",
        headers=headers,
        json={"name": unique("共享库"), "visibility": "tenant"},
    )
    assert created.status_code == 201
    kb_id = created.json()["data"]["id"]
    mate = register(client)
    assert client.get(f"/api/v1/kbs/{kb_id}", headers=mate["headers"]).status_code == 200
    assert (
        client.patch(f"/api/v1/kbs/{kb_id}", headers=mate["headers"], json={"name": "改名"}).status_code
        == 403
    )
    with session_scope() as db:
        row = db.scalar(select(AppUser).where(AppUser.id == mate["user_id"]))
        assert row is not None and row.role == Role.MEMBER.value
