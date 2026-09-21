"""文档上传、状态机与权限测试（docs/05 §4）。"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient

from app.core.config import settings
from tests.conftest import SAMPLE_MD, create_kb, run_worker, upload_md, unique


def test_upload_validation_and_idempotency(client: TestClient, user: dict, kb: dict) -> None:
    headers, kb_id = user["headers"], kb["id"]

    # 伪造扩展名（内容与扩展名不符）
    forged = client.post(
        f"/api/v1/kbs/{kb_id}/documents",
        headers=headers,
        files=[("files", ("fake.pdf", io.BytesIO(b"not a real pdf"), "application/pdf"))],
    )
    assert forged.json()["data"]["rejected"][0]["code"] == 41501

    # 不支持的扩展名
    unsupported = client.post(
        f"/api/v1/kbs/{kb_id}/documents",
        headers=headers,
        files=[("files", ("evil.exe", io.BytesIO(b"MZ"), "application/octet-stream"))],
    )
    assert unsupported.json()["data"]["rejected"][0]["code"] == 41501

    # 超出大小限制
    oversize = client.post(
        f"/api/v1/kbs/{kb_id}/documents",
        headers=headers,
        files=[
            ("files", ("big.md", io.BytesIO(b"a" * (settings.max_upload_size_bytes + 10)), "text/markdown"))
        ],
    )
    assert oversize.json()["data"]["rejected"][0]["code"] == 41301

    # 字段名兼容 files[]，并且同一 sha256 二次上传判重
    first = client.post(
        f"/api/v1/kbs/{kb_id}/documents",
        headers=headers,
        files=[("files[]", ("a.md", io.BytesIO(SAMPLE_MD.encode()), "text/markdown"))],
    )
    assert first.status_code == 202 and len(first.json()["data"]["accepted"]) == 1, first.text
    second = client.post(
        f"/api/v1/kbs/{kb_id}/documents",
        headers=headers,
        files=[("files", ("a-copy.md", io.BytesIO(SAMPLE_MD.encode()), "text/markdown"))],
    )
    assert second.json()["data"]["rejected"][0]["code"] == 40901

    # 单次数量上限
    too_many = [
        ("files", (f"m{i}.md", io.BytesIO(f"内容 {i}".encode()), "text/markdown"))
        for i in range(settings.max_upload_batch + 1)
    ]
    over = client.post(f"/api/v1/kbs/{kb_id}/documents", headers=headers, files=too_many)
    assert over.status_code == 400 and over.json()["code"] == 40001


def test_clean_markdown_for_index_strips_decoration_lines() -> None:
    """入库前清掉 Markdown 装饰线：`---` 会被 Dify 切成 3 字符碎片块（真题事故根因之一）。"""
    from app.services.document_service import clean_markdown_for_index

    raw = (
        "---\ntitle: 前言\n---\n\n"
        "# 二叉树遍历\n\n正文一。\n\n---\n\n正文二。\n\n***\n\n___\n\n"
        "| 列A | 列B |\n| --- | --- |\n| 1 | 2 |\n"
    )
    cleaned = clean_markdown_for_index(raw)

    frontmatter = "---\ntitle: 前言\n---"
    assert cleaned.startswith(frontmatter)  # frontmatter 原样保留
    body = cleaned[len(frontmatter) :]
    assert "\n---\n" not in body and "***" not in body and "\n___" not in body
    assert "正文一。" in body and "正文二。" in body
    assert "| --- | --- |" in body  # 表格分隔行不能被当成装饰线
    assert "\n\n\n" not in cleaned


def test_pipeline_indexes_content_without_decoration_chunks(
    client: TestClient, user: dict, kb: dict
) -> None:
    """含 `---` 的文档入库后不应出现「只有装饰线」的碎片块（否则会挤占检索 top_k）。"""
    headers, kb_id = user["headers"], kb["id"]
    text = SAMPLE_MD + "\n---\n\n补充：中序遍历二叉搜索树得到递增序列。\n\n---\n"
    doc_id = upload_md(client, headers, kb_id, text=text, filename="带装饰线.md")
    assert run_worker() >= 1

    chunks = client.get(f"/api/v1/documents/{doc_id}/chunks", headers=headers).json()["data"]
    contents = [str(item["content"]) for item in chunks["items"]]
    assert contents, chunks
    assert all(content.strip().strip("-*_").strip() for content in contents), contents
    assert any("递增序列" in content for content in contents)


def test_pipeline_preview_chunks_and_retry(client: TestClient, user: dict, kb: dict) -> None:
    headers, kb_id = user["headers"], kb["id"]
    doc_id = upload_md(client, headers, kb_id)

    pending = client.get(f"/api/v1/documents/{doc_id}/status", headers=headers).json()["data"]
    assert pending["status"] == "pending"

    assert run_worker() >= 1
    status = client.get(f"/api/v1/documents/{doc_id}/status", headers=headers).json()["data"]
    assert status["status"] == "ready"
    assert status["stage_progress"] == 100
    assert status["dify_indexing_status"] == "completed"

    detail = client.get(f"/api/v1/documents/{doc_id}", headers=headers).json()["data"]
    assert detail["parse_mode"] == "builtin"
    assert detail["audit_status"] == "pass"
    assert detail["chunk_count"] >= 1
    assert detail["dify_document_id"]

    preview = client.get(f"/api/v1/documents/{doc_id}/preview", headers=headers).json()["data"]
    assert "二叉树遍历" in preview["content"]
    assert preview["total_chars"] > 0

    chunks = client.get(f"/api/v1/documents/{doc_id}/chunks", headers=headers).json()["data"]
    assert chunks["total"] >= 1
    assert chunks["items"][0]["content"]

    listed = client.get(f"/api/v1/kbs/{kb_id}/documents?status=ready", headers=headers).json()["data"]
    assert listed["total"] == 1

    retried = client.post(f"/api/v1/documents/{doc_id}/retry", headers=headers, json={"stage": "index"})
    assert retried.status_code == 200, retried.text
    assert retried.json()["data"]["status"] == "pending"
    again = client.post(f"/api/v1/documents/{doc_id}/retry", headers=headers, json={})
    assert again.status_code == 409 and again.json()["code"] == 40901
    run_worker()
    assert (
        client.get(f"/api/v1/documents/{doc_id}/status", headers=headers).json()["data"]["status"] == "ready"
    )


def test_audit_blocked_document(client: TestClient, user: dict, kb: dict) -> None:
    """解析后送审命中 → 文档置 blocked，且不进入 Dify。

    注意：上传卡点只审「文件名 + 前 2000 字」，解析卡点审全文，
    因此这里把敏感词放在 2000 字之后，用来验证解析阶段的拦截。
    """
    headers, kb_id = user["headers"], kb["id"]
    padding = "正常的教学材料内容。" * 250  # ≈ 2500 字
    doc_id = upload_md(
        client,
        headers,
        kb_id,
        text=f"# 违规资料\n\n{padding}\n最后：这里教大家买卖答案和代考技巧。",
        filename="违规.md",
    )
    run_worker()
    status = client.get(f"/api/v1/documents/{doc_id}/status", headers=headers).json()["data"]
    assert status["status"] == "blocked"
    assert status["error_code"] == "50301"
    detail = client.get(f"/api/v1/documents/{doc_id}", headers=headers).json()["data"]
    assert detail["dify_document_id"] is None


def test_delete_and_not_found(client: TestClient, user: dict, kb: dict) -> None:
    headers, kb_id = user["headers"], kb["id"]
    doc_id = upload_md(client, headers, kb_id)
    assert client.delete(f"/api/v1/documents/{doc_id}", headers=headers).status_code == 200
    gone = client.get(f"/api/v1/documents/{doc_id}/status", headers=headers)
    assert gone.status_code == 404 and gone.json()["code"] == 40401
    unknown = client.get("/api/v1/documents/99999999-9999-9999-9999-999999999999", headers=headers)
    assert unknown.status_code == 404


def test_kb_crud_and_retrieval_test(client: TestClient, user: dict) -> None:
    headers = user["headers"]
    kb = create_kb(client, headers)
    kb_id = kb["id"]
    assert kb["dify_dataset_id"]

    dup = client.post("/api/v1/kbs", headers=headers, json={"name": kb["name"]})
    assert dup.status_code == 409 and dup.json()["code"] == 40901

    patched = client.patch(
        f"/api/v1/kbs/{kb_id}",
        headers=headers,
        json={"description": "改过的描述", "top_k": 8, "chunk_size": 400, "chunk_overlap": 40},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["top_k"] == 8

    assert client.patch(f"/api/v1/kbs/{kb_id}", headers=headers, json={"chunk_overlap": 999}).status_code == 400

    upload_md(client, headers, kb_id, filename=f"{unique('doc')}.md")
    run_worker()
    rt = client.post(
        f"/api/v1/kbs/{kb_id}/retrieval-test",
        headers=headers,
        json={"query": "中序遍历的顺序是什么", "top_k": 3},
    )
    assert rt.status_code == 200
    records = rt.json()["data"]["records"]
    assert records and records[0]["content"]

    members = client.get(f"/api/v1/kbs/{kb_id}/members", headers=headers).json()["data"]
    assert len(members["items"]) == 1 and members["items"][0]["role"] == "owner"

    listed = client.get("/api/v1/kbs", headers=headers).json()["data"]
    assert any(item["id"] == kb_id and item["role"] == "owner" for item in listed["items"])

    assert client.delete(f"/api/v1/kbs/{kb_id}", headers=headers).status_code == 200
    assert client.get(f"/api/v1/kbs/{kb_id}", headers=headers).status_code == 404
