"""MinerU 解析客户端：按 `PARSER_MODE` 切换 builtin / mineru_local / mineru_api。

契约（三种实现一致）：`parse(filename=..., content=..., ext=...) -> ParseResult`。
- `builtin`      ：零依赖（md/txt 直读；docx/pptx 用 zipfile+正则抽文本；pdf 需可选依赖 pypdf；图片不支持）
- `mineru_local` ：本地 MinerU 服务 `POST {MINERU_BASE_URL}/file_parse`
- `mineru_api`   ：MinerU 官方 API（申请上传链接 → 上传 → 轮询结果 → 下载 Markdown）

⚠️ `mineru_api` 需官方 Token，本机无凭据未验证；`mineru_local` 需先部署 MinerU 服务。
   两者失败统一映射为错误码 50202（解析失败），前端「重试解析」即可。

💡 本地解析器啃不动的类型（PDF 扫描件、作品集、图片）不必在这里硬扛：把扩展名加进
   `DIFY_PARSE_EXTENSIONS`（如 `pdf`），这类文件会在阶段 1 直接跳过本地解析，由 Dify 侧
   解析（`create-by-file` → 自带提取器 / 知识库流水线的解析分支）——见 workers/handlers.py。
"""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.core.config import settings
from app.core.errors import parse_error
from app.core.logging import get_logger

logger = get_logger(__name__)

_DOCX_TEXT = re.compile(r"<w:t[^>]*>(.*?)</w:t>", re.DOTALL)
_PPTX_TEXT = re.compile(r"<a:t>(.*?)</a:t>", re.DOTALL)
_PDF_TEXT = re.compile(rb"\((.*?)\)\s*Tj", re.DOTALL)


@dataclass(slots=True)
class ParseResult:
    markdown: str
    mode: str
    page_count: int | None = None
    images: dict[str, bytes] = field(default_factory=dict)


class Parser(Protocol):
    mode: str

    def parse(self, *, filename: str, content: bytes, ext: str) -> ParseResult: ...


# ── 公共小工具 ─────────────────────────────────────────────────────────
def _unescape(value: str) -> str:
    return (
        value.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    )


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return {"raw": resp.text[:500]}
    return data if isinstance(data, dict) else {"data": data}


def _read_markdown_from_zip(content: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in zf.namelist():
                if name.endswith("full.md") or name.endswith(".md"):
                    return zf.read(name).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError):
        return ""
    return ""


# ══ 1. 内置解析（零依赖） ═════════════════════════════════════════════════
class BuiltinParser:
    """零依赖解析：md/txt 直读；docx/pptx 解包抽文本；pdf 尽力抽取；图片不支持。"""

    mode = "builtin"

    def parse(self, *, filename: str, content: bytes, ext: str) -> ParseResult:
        ext = (ext or "").lower()
        if ext in {"md", "txt"}:
            text = content.decode("utf-8", errors="replace")
            return ParseResult(markdown=_normalize(text), mode=self.mode)
        if ext in {"docx", "pptx"}:
            return self._parse_office(filename, content, ext)
        if ext == "pdf":
            return self._parse_pdf(content)
        raise parse_error(
            f"内置解析器不支持 .{ext}：扫描件/图片请把 PARSER_MODE 切到 mineru_local 或 mineru_api",
            ext=ext,
        )

    def _parse_office(self, filename: str, content: bytes, ext: str) -> ParseResult:
        """docx/pptx 本质是 zip；按段落拼接可见文本（无版式还原，但足够入库检索）。"""
        pattern = _DOCX_TEXT if ext == "docx" else _PPTX_TEXT
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                names = sorted(
                    n
                    for n in zf.namelist()
                    if n.endswith(".xml")
                    and (n == "word/document.xml" or n.startswith("ppt/slides/slide"))
                )
                if ext == "docx" and "word/document.xml" not in names:
                    names = [n for n in names if n.startswith("word/")]
                parts: list[str] = []
                for name in names:
                    xml = zf.read(name).decode("utf-8", errors="replace")
                    texts = [_unescape(t).strip() for t in pattern.findall(xml)]
                    joined = "".join(t for t in texts if t)
                    if joined:
                        parts.append(joined)
        except zipfile.BadZipFile as exc:
            raise parse_error(f"{filename} 不是有效的 {ext} 文件", ext=ext) from exc
        if not parts:
            raise parse_error(f"{filename} 中未提取到文本内容", ext=ext)
        return ParseResult(
            markdown=_normalize("\n\n".join(parts)),
            mode=self.mode,
            page_count=len(parts) if ext == "pptx" else None,
        )

    def _parse_pdf(self, content: bytes) -> ParseResult:
        """优先使用可选依赖 pypdf；缺失时退化为未压缩文本流提取（质量有限）。"""
        try:
            from pypdf import PdfReader  # type: ignore[import-not-found]

            reader = PdfReader(io.BytesIO(content))
            pages = [(page.extract_text() or "").strip() for page in reader.pages]
            text = _normalize("\n\n".join(p for p in pages if p))
            if not text:
                raise parse_error("PDF 未提取到文本（可能是扫描件），请使用 MinerU 解析")
            return ParseResult(markdown=text, mode=self.mode, page_count=len(pages))
        except ImportError:
            logger.warning("未安装 pypdf，PDF 退化为原始流提取（建议 pip install pypdf 或使用 MinerU）")
        chunks = [m.group(1).decode("latin-1", errors="ignore") for m in _PDF_TEXT.finditer(content)]
        text = _normalize(" ".join(chunks))
        if len(text) < 20:
            raise parse_error(
                "内置解析器抽不出这个 PDF 的文本（多为扫描件/图片型 PDF）："
                "请在 DIFY_PARSE_EXTENSIONS 里加上 pdf（交给 Dify 侧解析），"
                "或安装 pypdf / 把 PARSER_MODE 切到 mineru_local",
                chars=len(text),
            )
        return ParseResult(markdown=text, mode=self.mode)


# ══ 2. 本地 MinerU 服务 ══════════════════════════════════════════════════
class MineruLocalParser:
    """`POST {MINERU_BASE_URL}/file_parse`（MinerU 2.x 本地服务 / mineru-server）。"""

    mode = "mineru_local"

    def parse(self, *, filename: str, content: bytes, ext: str) -> ParseResult:
        url = f"{settings.mineru_base_url.rstrip('/')}/file_parse"
        data = {
            "lang_list": ["ch"],
            "backend": "pipeline",
            "parse_method": "auto",
            "return_content_list": "false",
            "return_images": "false",
            "enable_table": "true",
            "enable_formula": "true",
        }
        files = {"files": (filename, content, "application/octet-stream")}
        headers: dict[str, str] = {}
        if settings.mineru_api_token:
            headers["Authorization"] = f"Bearer {settings.mineru_api_token}"
        try:
            with httpx.Client(timeout=settings.mineru_timeout_seconds) as client:
                resp = client.post(url, data=data, files=files, headers=headers)
        except httpx.HTTPError as exc:
            raise parse_error(f"MinerU 服务不可达: {url}（{exc}）", url=url) from exc
        if resp.status_code >= 400:
            raise parse_error(f"MinerU 返回 {resp.status_code}", url=url, body=resp.text[:300])
        payload = _safe_json(resp)
        markdown = _extract_markdown(payload, filename)
        if not markdown:
            raise parse_error("MinerU 未返回 Markdown 内容", url=url, keys=list(payload)[:10])
        return ParseResult(markdown=_normalize(markdown), mode=self.mode)


def _extract_markdown(payload: dict, filename: str) -> str:
    """兼容 MinerU 本地服务多种返回结构：{md_content|md|content|markdown}、{results:{file:{...}}}。"""
    for key in ("md_content", "md", "content", "markdown"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    results = payload.get("results")
    if not isinstance(results, dict):
        return ""
    stem = filename.rsplit(".", 1)[0] if filename else ""
    ordered = sorted(results.items(), key=lambda kv: 0 if stem and stem in kv[0] else 1)
    for _, value in ordered:
        if not isinstance(value, dict):
            continue
        for key in ("md_content", "md", "content", "markdown"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text
    return ""


# ══ 3. MinerU 官方 API ═══════════════════════════════════════════════════
class MineruApiParser:
    """官方 API（mineru.net）：申请上传链接 → PUT 上传 → 轮询结果 → 下载 Markdown。

    需配置 `MINERU_API_TOKEN`。失败统一映射 50202。
    """

    mode = "mineru_api"
    _BASE = "https://mineru.net/api/v4"

    def parse(self, *, filename: str, content: bytes, ext: str) -> ParseResult:
        if not settings.mineru_api_token:
            raise parse_error("MINERU_API_TOKEN 未配置，无法调用 MinerU 官方 API")
        headers = {"Authorization": f"Bearer {settings.mineru_api_token}"}
        with httpx.Client(timeout=settings.mineru_timeout_seconds, base_url=self._BASE) as client:
            created = client.post(
                "/file-urls/batch",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "enable_formula": True,
                    "enable_table": True,
                    "language": "ch",
                    "model_version": "pipeline",
                    "files": [{"name": filename, "is_ocr": False}],
                },
            )
            payload = _safe_json(created)
            if created.status_code >= 400 or payload.get("code") not in (0, None):
                raise parse_error(f"MinerU API 申请上传链接失败: {str(payload)[:200]}")
            data = payload.get("data") or {}
            url_items = data.get("file_urls") or []
            if not url_items:
                raise parse_error("MinerU API 未返回上传链接")
            upload_url = url_items[0].get("url", "")
            batch_id = data.get("batch_id", "")
            put = client.put(upload_url, content=content)
            if put.status_code >= 400:
                raise parse_error(f"MinerU 文件上传失败: {put.status_code}")

            result = self._poll(client, headers, batch_id)
            markdown = self._fetch_markdown(client, result)
        if not markdown:
            raise parse_error("MinerU 返回结果中没有 Markdown")
        return ParseResult(markdown=_normalize(markdown), mode=self.mode)

    def _poll(self, client: httpx.Client, headers: dict[str, str], batch_id: str) -> dict:
        deadline = time.time() + settings.mineru_timeout_seconds
        while time.time() < deadline:
            poll = client.get(f"/extract-results/batch/{batch_id}", headers=headers)
            data = _safe_json(poll).get("data") or {}
            items = data.get("extract_result") or []
            if items:
                state = items[0].get("state")
                if state == "done":
                    return items[0]
                if state == "failed":
                    raise parse_error(f"MinerU 解析失败: {items[0].get('err_msg')}")
            time.sleep(5)
        raise parse_error("MinerU 解析超时（请检查文件大小或稍后重试）")

    @staticmethod
    def _fetch_markdown(client: httpx.Client, result: dict) -> str:
        zip_url = result.get("full_zip_url")
        if zip_url:
            zipped = client.get(zip_url)
            if zipped.status_code < 400:
                text = _read_markdown_from_zip(zipped.content)
                if text:
                    return text
        return result.get("md_content") or ""


# ══ 工厂 ═════════════════════════════════════════════════════════════════
def build_parser(mode: str | None = None) -> Parser:
    resolved = (mode or settings.parser_mode or "builtin").lower()
    if resolved == "mineru_api":
        return MineruApiParser()
    if resolved == "mineru_local":
        return MineruLocalParser()
    if resolved == "builtin":
        return BuiltinParser()
    raise ValueError(f"未知的 PARSER_MODE: {resolved}")


_parser: Parser | None = None


def get_parser() -> Parser:
    global _parser
    if _parser is None:
        _parser = build_parser()
        logger.info("解析器已初始化: mode=%s", _parser.mode)
    return _parser


def set_parser(parser: Parser | None) -> None:
    """测试用：覆盖全局解析器（传 None 复位）。"""
    global _parser
    _parser = parser
