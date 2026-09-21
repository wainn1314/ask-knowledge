"""Dify 侧解析通道：命中 `DIFY_PARSE_EXTENSIONS` 的文件不在本地解析，直传原文件给 Dify。

背景（真实故障）：本地 builtin 解析器没装 pypdf 时抽不出 PDF 文本 —— 上传作品集类 PDF 会报
「内置解析器抽不出这个 PDF 的文本（多为扫描件/图片型 PDF）」。而 Dify 侧能解析：自带提取器，
或知识库流水线里的解析分支（如「else → parse file」）。
开关 = `DIFY_PARSE_EXTENSIONS`（生产 `.env` 里 = pdf），本文件覆盖三条不变量：
1. 命中开关 → 本地解析器一次都不调用，走 `create-by-file`，文档照样 ready；
2. 开关为空 → 一切照旧走本地解析 + `create-by-text`（默认行为，零回归）；
3. 预览：本地没 Markdown 时用 Dify 分块兜底。
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.constants import ParseMode
from app.db.session import session_scope
from app.integrations.mineru_client import ParseResult
from app.models.documents import Document
from app.services.dify import get_dify_client
from app.workers import handlers
from tests.conftest import SAMPLE_MD, run_worker


def _pdf_bytes(text: str = "DIFY NATIVE PARSE PROBE alpha beta gamma delta") -> bytes:
    """未压缩的最小 PDF（含文本层）；文件头 `%PDF` 能通过上传内容校验。"""
    stream = f"BT /F1 14 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for idx, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{idx} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _native_uploads() -> int:
    """mock Dify 记录的「直传原文件」次数（http 模式没有该计数，按 0 处理）。"""
    return int(getattr(get_dify_client(), "native_file_uploads", 0))


def _upload(
    client: TestClient, headers: dict[str, str], kb_id: str, filename: str, content: bytes, mime: str
) -> str:
    resp = client.post(
        f"/api/v1/kbs/{kb_id}/documents",
        headers=headers,
        files=[("files", (filename, io.BytesIO(content), mime))],
    )
    assert resp.status_code == 202, resp.text
    accepted = resp.json()["data"]["accepted"]
    assert accepted, resp.text
    return accepted[0]["document_id"]


def _detail(client: TestClient, headers: dict[str, str], doc_id: str) -> dict:
    resp = client.get(f"/api/v1/documents/{doc_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


@pytest.fixture
def dify_parse_pdf() -> SimpleNamespace:
    """临时把 pdf 划入「Dify 侧解析」，并记录直传次数基线（用例结束还原）。"""
    baseline = _native_uploads()
    original = settings.dify_parse_extensions
    settings.dify_parse_extensions = "pdf"
    try:
        yield SimpleNamespace(baseline=baseline)
    finally:
        settings.dify_parse_extensions = original


def _forbid_local_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    """本地解析一旦被调用就判失败：命中开关的文档必须整段跳过本地解析。"""

    def _boom(**_: object) -> ParseResult:
        raise AssertionError("命中 DIFY_PARSE_EXTENSIONS 的文档不应调用本地解析器")

    monkeypatch.setattr(handlers, "get_parser", lambda: SimpleNamespace(parse=_boom))


def test_pdf_is_sent_raw_to_dify_and_becomes_ready(
    client: TestClient,
    user: dict,
    kb: dict,
    dify_parse_pdf: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_local_parser(monkeypatch)
    doc_id = _upload(
        client, user["headers"], kb["id"], "作品集.pdf", _pdf_bytes(), "application/pdf"
    )
    assert run_worker() >= 1

    detail = _detail(client, user["headers"], doc_id)
    assert detail["status"] == "ready", detail
    assert detail["parse_mode"] == ParseMode.DIFY.value
    assert detail["dify_document_id"]
    # 本地没有 Markdown（正文只存在于 Dify 侧）
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        assert doc is not None and doc.parsed_path is None
    # 走的确实是 create-by-file，而不是 create-by-text
    assert _native_uploads() == dify_parse_pdf.baseline + 1


def test_preview_falls_back_to_dify_segments(
    client: TestClient,
    user: dict,
    kb: dict,
    dify_parse_pdf: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_local_parser(monkeypatch)
    doc_id = _upload(
        client, user["headers"], kb["id"], "作品集预览.pdf", _pdf_bytes(), "application/pdf"
    )
    assert run_worker() >= 1

    resp = client.get(f"/api/v1/documents/{doc_id}/preview", headers=user["headers"])
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["mode"] == ParseMode.DIFY.value
    assert data["total_chars"] > 0 and data["content"]


def test_pdf_stays_local_when_switch_is_off(
    client: TestClient, user: dict, kb: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """默认（`DIFY_PARSE_EXTENSIONS` 为空）：PDF 仍由本地解析器处理，行为与改动前一致。"""
    baseline = _native_uploads()
    calls: list[str] = []

    def _fake_parse(*, filename: str, content: bytes, ext: str) -> ParseResult:
        calls.append(ext)
        return ParseResult(markdown="# 本地解析\n\n这份 PDF 由本地解析器处理。\n", mode="builtin")

    monkeypatch.setattr(handlers, "get_parser", lambda: SimpleNamespace(parse=_fake_parse))
    doc_id = _upload(
        client, user["headers"], kb["id"], "本地解析.pdf", _pdf_bytes(), "application/pdf"
    )
    assert run_worker() >= 1

    detail = _detail(client, user["headers"], doc_id)
    assert calls == ["pdf"]
    assert detail["status"] == "ready", detail
    assert detail["parse_mode"] == "builtin"
    assert _native_uploads() == baseline


def test_markdown_upload_ignores_the_switch(
    client: TestClient, user: dict, kb: dict, dify_parse_pdf: SimpleNamespace
) -> None:
    """开关只对列出的扩展名生效：md 依旧本地解析 + create-by-text。"""
    doc_id = _upload(
        client, user["headers"], kb["id"], "本地语义.md", SAMPLE_MD.encode("utf-8"), "text/markdown"
    )
    assert run_worker() >= 1

    detail = _detail(client, user["headers"], doc_id)
    assert detail["status"] == "ready", detail
    assert detail["parse_mode"] == "builtin"
    assert _native_uploads() == dify_parse_pdf.baseline


def test_extension_list_parsing() -> None:
    """`DIFY_PARSE_EXTENSIONS` 容错：大小写、点号、空白、空项都不影响解析结果。"""
    original = settings.dify_parse_extensions
    try:
        settings.dify_parse_extensions = " PDF , .Png ,, "
        assert settings.dify_parse_ext_set == {"pdf", "png"}
        settings.dify_parse_extensions = ""
        assert settings.dify_parse_ext_set == set()
    finally:
        settings.dify_parse_extensions = original
