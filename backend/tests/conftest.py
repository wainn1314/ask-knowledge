"""pytest 公共夹具：临时 SQLite + 临时存储目录 + mock Dify + TestClient。

⚠️ 环境变量必须在导入任何 `app.*` 模块之前设置（config 是模块级单例）。
"""

from __future__ import annotations

import io
import os
import pathlib
import tempfile
import uuid
from collections.abc import Iterator
from typing import Any

TMP = pathlib.Path(tempfile.mkdtemp(prefix="ask-kb-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(TMP / 'test.db').as_posix()}"
os.environ["STORAGE_LOCAL_ROOT"] = str(TMP / "data")
os.environ["DIFY_MODE"] = "mock"
os.environ["WORKER_INLINE"] = "false"
os.environ["AUDIT_PROVIDER"] = "local"
os.environ["PARSER_MODE"] = "builtin"
os.environ["APP_SECRET_KEY"] = "test-secret-key-change-me-32bytes-minimum"
os.environ["MODERATION_CALLBACK_TOKEN"] = "test-moderation-token"
os.environ["AUDIT_ENABLED"] = "true"
# 单知识库模式默认关闭：既有用例都走「先建库再上传」的多库语义，
# 单知识库模式由 tests/test_single_kb.py 显式打开并还原。
os.environ["SINGLE_KB_MODE"] = "false"
# Dify 侧解析默认关闭：既有用例都断言本地解析（PARSER_MODE=builtin）的结果，
# 「直传原文件给 Dify」通道由 tests/test_dify_native_parse.py 显式打开并还原。
os.environ["DIFY_PARSE_EXTENSIONS"] = ""
# 日志不落盘：pytest 会跑几百次请求，写进 backend/logs/backend.log 只会把真实排障信息淹掉
os.environ["LOG_FILE"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.workers.runner import process_one  # noqa: E402

SAMPLE_MD = """# 二叉树遍历

二叉树遍历是指按某种顺序访问树中每个节点一次且仅一次。常见有四种遍历方式：

1. 前序遍历：根 → 左 → 右
2. 中序遍历：左 → 根 → 右
3. 后序遍历：左 → 右 → 根
4. 层序遍历：按层从左到右依次访问

中序遍历二叉搜索树可以得到递增序列，这是重要性质。
"""


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _dify_knowledge_isolation(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """关掉「Dify 知识库接口闸门」并清空读缓存，保证用例之间互不污染。

    闸门与缓存是给真实 Dify 订阅配额用的（见 `app/services/dify/quota.py`）：
    - 闸门开着时，单测在一个进程里连续打知识库接口会把名额用光 → 后面用例莫名 42901；
    - 缓存是进程级单例，不清空会出现「上个用例的检索/状态结果串到下个用例」。
    需要它们的用例（tests/test_dify_knowledge_quota.py）自行调 `configure()` / 构造实例。
    """
    from app.core.config import settings  # noqa: PLC0415 - 必须在环境变量设置之后导入
    from app.services.dify import quota as quota_module  # noqa: PLC0415

    monkeypatch.setattr(settings, "dify_knowledge_rate_limit_per_minute", 0, raising=False)
    quota_module.reset_all()
    yield
    quota_module.reset_all()


def unique(prefix: str = "u") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def register(client: TestClient, email: str | None = None) -> dict[str, Any]:
    """注册并返回 {headers, user_id, email, data}。"""
    email = email or f"{unique()}@example.com"
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "secret123", "nickname": email.split("@")[0]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    return {
        "email": email,
        "headers": {"Authorization": f"Bearer {data['access_token']}"},
        "user_id": data["user"]["id"],
        "data": data,
    }


def create_kb(client: TestClient, headers: dict[str, str], name: str | None = None) -> dict[str, Any]:
    resp = client.post(
        "/api/v1/kbs",
        headers=headers,
        json={"name": name or f"测试知识库-{unique('kb')}", "visibility": "private"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def upload_md(
    client: TestClient,
    headers: dict[str, str],
    kb_id: str,
    text: str = SAMPLE_MD,
    filename: str = "二叉树遍历.md",
) -> str:
    resp = client.post(
        f"/api/v1/kbs/{kb_id}/documents",
        headers=headers,
        files=[("files", (filename, io.BytesIO(text.encode("utf-8")), "text/markdown"))],
    )
    assert resp.status_code == 202, resp.text
    accepted = resp.json()["data"]["accepted"]
    assert accepted, resp.text
    return accepted[0]["document_id"]


def run_worker(times: int | None = None) -> int:
    """手动驱动 worker（测试里不开后台线程，保证可预测）。

    默认「排空队列」：避免上一个用例遗留的任务把当前用例的任务挤到后面。
    """
    limit = times or 50
    done = 0
    for _ in range(limit):
        with session_scope() as db:
            task = process_one(db, "test-worker")
        if task is None:
            break
        done += 1
    return done


@pytest.fixture
def user(client: TestClient) -> dict[str, Any]:
    return register(client)


@pytest.fixture
def kb(client: TestClient, user: dict[str, Any]) -> dict[str, Any]:
    return create_kb(client, user["headers"])


@pytest.fixture
def ready_doc(client: TestClient, user: dict[str, Any], kb: dict[str, Any]) -> dict[str, Any]:
    """已上传且完成「解析 → 审核 → 入库」的文档。"""
    doc_id = upload_md(client, user["headers"], kb["id"])
    assert run_worker() >= 1
    resp = client.get(f"/api/v1/documents/{doc_id}/status", headers=user["headers"])
    assert resp.json()["data"]["status"] == "ready", resp.text
    return {"id": doc_id, "kb_id": kb["id"]}
