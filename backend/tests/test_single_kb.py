"""单知识库模式：用户不能建库，上传的文档统一进「既有」Dify 知识库。

对应需求：「用户并不能直接创建知识库，而是上传文档，把文档导入我自己创建的知识库」。
开关 = `SINGLE_KB_MODE` / `DEFAULT_KB_DATASET_ID`（生产 .env 里指向 Dify 的「Untitled 1」）。
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.db.session import session_scope
from app.models.knowledge import Kb, KbMember
from app.services.dify import get_dify_client
from tests.conftest import SAMPLE_MD, register, run_worker, unique


def _drop_default_kb(dataset_id: str) -> None:
    """软删本用例产生的唯一知识库记录，避免污染同一测试会话的其它用例文件。"""
    with session_scope() as db:
        for kb in db.scalars(select(Kb).where(Kb.dify_dataset_id == dataset_id)).all():
            for member in db.scalars(select(KbMember).where(KbMember.kb_id == kb.id)).all():
                db.delete(member)
            kb.mark_deleted()
            kb.status = 0


@pytest.fixture
def single_kb() -> SimpleNamespace:
    """临时打开单知识库模式，并指向 mock Dify 里一个**已存在**的数据集。

    模拟真实场景：知识库（Untitled 1）早已在 Dify 建好，应用只负责往里传文档；
    因此 `dataset_total` 在上传前后的变化必须为 0。
    """
    dify = get_dify_client()
    dataset = dify.create_dataset(name="Untitled 1")  # 既有数据集，不由上传流程创建
    dataset_id = str(dataset["id"])
    original = (settings.single_kb_mode, settings.default_kb_name, settings.default_kb_dataset_id)
    settings.single_kb_mode = True
    settings.default_kb_name = "Untitled 1"
    settings.default_kb_dataset_id = dataset_id
    try:
        yield SimpleNamespace(dataset_id=dataset_id, dataset_total=dify.health()["datasets"])
    finally:
        settings.single_kb_mode, settings.default_kb_name, settings.default_kb_dataset_id = original
        _drop_default_kb(dataset_id)



def _default_kb(client: TestClient, headers: dict[str, str]) -> dict:
    resp = client.get("/api/v1/kbs/default", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _upload(
    client: TestClient,
    headers: dict[str, str],
    kb_id: str,
    filename: str,
    text: str = SAMPLE_MD,
) -> dict:
    resp = client.post(
        f"/api/v1/kbs/{kb_id}/documents",
        headers=headers,
        files=[("files", (filename, io.BytesIO(text.encode("utf-8")), "text/markdown"))],
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["data"]


def test_create_kb_is_forbidden_in_single_mode(
    client: TestClient, user: dict, single_kb: SimpleNamespace
) -> None:
    """用户不能建库：POST /kbs 直接 40301，并提示直接上传文档。"""
    resp = client.post(
        "/api/v1/kbs",
        headers=user["headers"],
        json={"name": unique("kb"), "visibility": "private"},
    )
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["code"] == 40301
    assert "单知识库模式" in body["message"]


def test_default_kb_endpoint_and_list_only_expose_the_unique_kb(
    client: TestClient, user: dict, single_kb: SimpleNamespace
) -> None:
    data = _default_kb(client, user["headers"])
    assert data["single_kb_mode"] is True
    assert data["kb"]["dify_dataset_id"] == single_kb.dataset_id
    assert data["kb"]["name"] == "Untitled 1"

    # 幂等：重复调用不会产生第二条本地记录
    assert _default_kb(client, user["headers"])["kb"]["id"] == data["kb"]["id"]

    listed = client.get("/api/v1/kbs?page_size=50", headers=user["headers"]).json()["data"]
    assert listed["total"] == 1
    assert [item["id"] for item in listed["items"]] == [data["kb"]["id"]]


def test_default_endpoint_is_noop_when_mode_disabled(client: TestClient, user: dict) -> None:
    """开关关闭时前端走多库交互（kb=null），不影响既有语义。"""
    data = _default_kb(client, user["headers"])
    assert data["single_kb_mode"] is False
    assert data["kb"] is None


def test_upload_lands_in_existing_dataset_without_creating_a_new_one(
    client: TestClient, user: dict, single_kb: SimpleNamespace
) -> None:
    kb_id = _default_kb(client, user["headers"])["kb"]["id"]
    accepted = _upload(client, user["headers"], kb_id, "单库上传.md")
    assert accepted["accepted"], accepted
    assert run_worker() >= 1

    doc_id = accepted["accepted"][0]["document_id"]
    status = client.get(f"/api/v1/documents/{doc_id}/status", headers=user["headers"])
    assert status.json()["data"]["status"] == "ready", status.text

    dify = get_dify_client()
    # ① 没有新建 Dify 数据集（原 bug 的判定点）
    assert dify.health()["datasets"] == single_kb.dataset_total
    # ② 文档确实写进了那个既有数据集
    docs = dify.list_documents(dataset_id=single_kb.dataset_id)
    landed = {d["name"]: d for d in docs["items"]}
    assert "单库上传.md" in landed
    assert landed["单库上传.md"]["indexing_status"] == "completed"
    assert landed["单库上传.md"]["segment_count"] >= 1


def test_tenant_member_can_upload_without_creating_a_kb(
    client: TestClient, user: dict, single_kb: SimpleNamespace
) -> None:
    """普通成员（非创建者）无需建库也能上传：自动获得唯一知识库的 editor 权限。"""
    member = register(client)
    kb_id = _default_kb(client, member["headers"])["kb"]["id"]
    accepted = _upload(
        client,
        member["headers"],
        kb_id,
        "同事上传.md",
        SAMPLE_MD + "\n补充：这是同事的版本，用于验证成员也能入库。\n",
    )
    assert accepted["accepted"], accepted
    assert run_worker() >= 1

    dify = get_dify_client()
    assert dify.health()["datasets"] == single_kb.dataset_total
    names = {d["name"] for d in dify.list_documents(dataset_id=single_kb.dataset_id)["items"]}
    assert "同事上传.md" in names


def test_direct_upload_by_fresh_member_is_auto_granted(
    client: TestClient, user: dict, single_kb: SimpleNamespace
) -> None:
    """兜底分支：成员没先访问 /kbs/default 就直连上传接口，也应被自动授予 editor。"""
    kb_id = _default_kb(client, user["headers"])["kb"]["id"]
    stranger = register(client)
    # 刻意不调 /kbs/default，直接拿唯一知识库 id 上传
    accepted = _upload(
        client,
        stranger["headers"],
        kb_id,
        "直连上传.md",
        SAMPLE_MD + "\n补充：调用方未先查询唯一知识库，直接上传。\n",
    )
    assert accepted["accepted"], accepted
    assert run_worker() >= 1

    dify = get_dify_client()
    assert dify.health()["datasets"] == single_kb.dataset_total
    names = {d["name"] for d in dify.list_documents(dataset_id=single_kb.dataset_id)["items"]}
    assert "直连上传.md" in names


def test_unique_kb_cannot_be_deleted(
    client: TestClient, user: dict, single_kb: SimpleNamespace
) -> None:
    """唯一知识库禁止删除（否则没有上传去处，还会连带删掉 Dify 侧数据集）。"""
    kb_id = _default_kb(client, user["headers"])["kb"]["id"]
    resp = client.delete(f"/api/v1/kbs/{kb_id}", headers=user["headers"])
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == 40301
    assert get_dify_client().health()["datasets"] == single_kb.dataset_total
