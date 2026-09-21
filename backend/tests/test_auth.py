"""认证与用户接口测试（docs/05 §2）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import register, unique


def test_register_login_me_and_update(client: TestClient) -> None:
    email = f"{unique()}@example.com"
    data = register(client, email)["data"]
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0
    assert data["user"]["email"] == email
    assert data["user"]["tenant"]["code"] == "default"

    login = client.post("/api/v1/auth/login", json={"email": email, "password": "secret123"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    me = client.get("/api/v1/users/me", headers=headers)
    assert me.status_code == 200
    body = me.json()["data"]
    assert body["email"] == email
    assert body["tenant"]["name"] == "默认租户"

    patched = client.patch("/api/v1/users/me", headers=headers, json={"nickname": "新昵称"})
    assert patched.status_code == 200
    assert patched.json()["data"]["nickname"] == "新昵称"

    # 改密码需带旧密码
    bad = client.patch("/api/v1/users/me", headers=headers, json={"new_password": "newpass123"})
    assert bad.status_code == 400 and bad.json()["code"] == 40001
    wrong_old = client.patch(
        "/api/v1/users/me",
        headers=headers,
        json={"old_password": "wrongpass", "new_password": "newpass123"},
    )
    assert wrong_old.status_code == 400
    ok = client.patch(
        "/api/v1/users/me",
        headers=headers,
        json={"old_password": "secret123", "new_password": "newpass123"},
    )
    assert ok.status_code == 200
    assert client.post(
        "/api/v1/auth/login", json={"email": email, "password": "newpass123"}
    ).status_code == 200


def test_refresh_token(client: TestClient) -> None:
    data = register(client)["data"]
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": data["refresh_token"]})
    assert resp.status_code == 200
    assert resp.json()["data"]["access_token"]

    # 用 access_token 冒充 refresh_token
    bad = client.post("/api/v1/auth/refresh", json={"refresh_token": data["access_token"]})
    assert bad.status_code == 401 and bad.json()["code"] == 40101


def test_errors_and_validation(client: TestClient) -> None:
    registered = register(client)
    email = registered["email"]

    dup = client.post(
        "/api/v1/auth/register", json={"email": email, "password": "secret123"}
    )
    assert dup.status_code == 409 and dup.json()["code"] == 40901

    bad_password = client.post("/api/v1/auth/login", json={"email": email, "password": "wrongpass"})
    assert bad_password.status_code == 401

    invalid_email = client.post(
        "/api/v1/auth/register", json={"email": "not-an-email", "password": "secret123"}
    )
    assert invalid_email.status_code == 400 and invalid_email.json()["code"] == 40001

    short_password = client.post(
        "/api/v1/auth/register", json={"email": f"{unique()}@example.com", "password": "123"}
    )
    assert short_password.status_code == 400

    anonymous = client.get("/api/v1/kbs")
    assert anonymous.status_code == 401 and anonymous.json()["code"] == 40101

    broken = client.get("/api/v1/kbs", headers={"Authorization": "Bearer not-a-token"})
    assert broken.status_code == 401 and broken.json()["code"] == 40101

    # 统一响应体包含 request_id
    assert "request_id" in anonymous.json()
    assert anonymous.headers.get("X-Request-Id")
