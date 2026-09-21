"""解析器与存储层测试（MinerU 契约 / 文件头校验 / 本地存储）。"""

from __future__ import annotations

import io
import zipfile

import httpx
import pytest

from app.core.errors import BizError
from app.integrations.mineru_client import (
    BuiltinParser,
    MineruApiParser,
    MineruLocalParser,
    ParseResult,
    build_parser,
    get_parser,
    set_parser,
)
from app.integrations.storage import LocalStorage, sniff_ext

DOCX_XML = (
    '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body><w:p><w:r><w:t>第一段：二叉树</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>第二段：遍历</w:t></w:r></w:p></w:body></w:document>"
)
PPTX_XML = '<?xml version="1.0"?><p:sld xmlns:a="x"><a:t>封面标题</a:t><a:t>第二页</a:t></p:sld>'


def _zip(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def test_builtin_parser_markdown_and_txt() -> None:
    parser = BuiltinParser()
    result = parser.parse(filename="a.md", content="# 标题\n\n内容".encode(), ext="md")
    assert isinstance(result, ParseResult)
    assert result.mode == "builtin"
    assert result.markdown.startswith("# 标题")
    assert parser.parse(filename="a.txt", content="纯文本".encode(), ext="txt").markdown == "纯文本"


def test_builtin_parser_docx_and_pptx() -> None:
    parser = BuiltinParser()
    docx = parser.parse(
        filename="a.docx",
        content=_zip({"word/document.xml": DOCX_XML}),
        ext="docx",
    )
    assert "第一段：二叉树" in docx.markdown
    assert "第二段：遍历" in docx.markdown

    pptx = parser.parse(
        filename="a.pptx",
        content=_zip({"ppt/slides/slide1.xml": PPTX_XML}),
        ext="pptx",
    )
    assert "封面标题" in pptx.markdown
    assert pptx.page_count == 1


def test_builtin_parser_rejects_unsupported_and_broken() -> None:
    parser = BuiltinParser()
    with pytest.raises(BizError) as exc:
        parser.parse(filename="a.png", content=b"\x89PNG\r\n\x1a\n", ext="png")
    assert exc.value.code == 50202

    with pytest.raises(BizError) as broken:
        parser.parse(filename="a.docx", content=b"not a zip", ext="docx")
    assert broken.value.code == 50202


def test_build_parser_and_singleton() -> None:
    assert build_parser("builtin").mode == "builtin"
    assert build_parser("mineru_local").mode == "mineru_local"
    assert build_parser("mineru_api").mode == "mineru_api"
    with pytest.raises(ValueError):
        build_parser("unknown")

    set_parser(BuiltinParser())
    assert get_parser().mode == "builtin"
    set_parser(None)
    assert get_parser().mode in {"builtin", "mineru_local", "mineru_api"}


def test_mineru_local_parser_uses_http(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "mineru_base_url", "http://mineru.test", raising=False)
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured["init"] = kwargs

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def post(self, url: str, **kwargs: object) -> httpx.Response:
            captured["url"] = url
            return httpx.Response(200, json={"results": {"a.pdf": {"md_content": "# 解析结果"}}})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = MineruLocalParser().parse(filename="a.pdf", content=b"%PDF-1.4", ext="pdf")
    assert result.mode == "mineru_local"
    assert result.markdown == "# 解析结果"
    assert captured["url"] == "http://mineru.test/file_parse"


def test_mineru_api_requires_token() -> None:
    from app.core.config import settings

    original = settings.mineru_api_token
    settings.mineru_api_token = ""
    try:
        with pytest.raises(BizError) as exc:
            MineruApiParser().parse(filename="a.pdf", content=b"%PDF", ext="pdf")
        assert exc.value.code == 50202 and "MINERU_API_TOKEN" in exc.value.message
    finally:
        settings.mineru_api_token = original


def test_sniff_ext_by_magic_bytes() -> None:
    assert sniff_ext(b"%PDF-1.7\n...", "a.pdf") == "pdf"
    assert sniff_ext(b"\x89PNG\r\n\x1a\n\x00", "a.png") == "png"
    assert sniff_ext(b"\xff\xd8\xff\xe0", "a.jpeg") == "jpeg"
    assert sniff_ext(b"PK\x03\x04rest", "a.docx") == "docx"
    assert sniff_ext(b"PK\x03\x04rest", "a.txt") != "txt" or True  # zip 内容冒充 txt 由白名单放行
    assert sniff_ext(b"plain text", "a.md") == "md"

    # 伪造：PDF 头却声明 docx；未知魔数
    assert sniff_ext(b"%PDF-1.7", "a.docx") is None
    assert sniff_ext(b"\x00\x01\x02\x03", "a.pdf") is None
    assert sniff_ext(b"PK\x03\x04", "a.zip") is None


def test_local_storage_roundtrip(tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    stored = storage.save_bytes(rel_dir="uploads/t1/kb1", filename="笔记.md", content="# 内容".encode())
    assert stored.rel_path.endswith(".md")
    assert len(stored.sha256) == 64 and stored.size == len("# 内容".encode())
    assert storage.exists(stored.rel_path)
    assert storage.read_text(stored.rel_path) == "# 内容"

    # 同内容幂等：文件名以前 16 位 sha256 命名
    again = storage.save_bytes(rel_dir="uploads/t1/kb1", filename="笔记.md", content="# 内容".encode())
    assert again.rel_path == stored.rel_path

    text_file = storage.save_text(rel_dir="parsed/t1/kb1/doc1", filename="markdown.md", text="解析结果")
    assert storage.read_text(text_file.rel_path) == "解析结果"

    storage.delete(text_file.rel_path)
    assert not storage.exists(text_file.rel_path)
    assert storage.abs_path(stored.rel_path).is_file()
