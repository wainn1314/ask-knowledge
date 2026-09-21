"""文档服务：上传校验、状态查询、预览、分块代理、重试、删除（docs/05 §4）。"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import KbContext
from app.core.config import settings
from app.core.constants import AuditScene, DocStatus, TaskType
from app.core.errors import bad_request, conflict, not_found
from app.core.logging import get_logger
from app.integrations.storage import get_storage, sniff_ext
from app.models.documents import Document
from app.services import task_service
from app.services.dify import get_dify_client
from app.services.moderation import get_moderator
from app.services.support import paginate

logger = get_logger(__name__)

_PREVIEW_CHARS = 4000  # 单页预览字符数
_TEXT_EXT = {"md", "txt"}
_IMAGE_EXT = {"png", "jpg", "jpeg"}

#: Markdown 独立装饰线：`---` / `***` / `___`（表格分隔行 `| --- | --- |` 不受影响）
_HR_LINE = re.compile(r"^\s{0,3}(?:[-*_]\s*){3,}$")
#: 文档开头的 YAML frontmatter（原样保留，不做装饰线清洗）
_FRONTMATTER = re.compile(r"\A---\r?\n.*?\r?\n---[ \t]*(?:\r?\n|$)", re.DOTALL)


def clean_markdown_for_index(markdown: str) -> str:
    """入库前清洗 Markdown 装饰线（`---` / `***` / `___`）。

    Dify 按 `separator="\\n\\n"` 切块，`---` 会单独成为 3 字符的碎片块：既占满检索 top_k，
    又可能因相似度评分挤掉真正的正文——真机上就是这样导致「出题拿到的资料为空、只能按
    知识库名编题」。这些行没有信息量，去掉后相邻段落会归并回正文块。
    """
    frontmatter = ""
    match = _FRONTMATTER.match(markdown or "")
    body = markdown or ""
    if match:
        frontmatter, body = match.group(0), body[match.end() :]
    kept = [line for line in body.splitlines() if not _HR_LINE.match(line)]
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    text = f"{frontmatter}{cleaned}\n" if cleaned else frontmatter
    if len(text) < len(markdown or ""):
        logger.info("入库前清洗装饰线：%s → %s 字符", len(markdown or ""), len(text))
    return text


def upload_documents(
    db: Session, ctx: KbContext, files: list[tuple[str, bytes]]
) -> dict[str, Any]:
    """批量上传：硬校验（数量/大小/类型/内容/审核）+ 幂等（kb_id + sha256）。"""
    if not files:
        raise bad_request("未收到任何文件")
    if len(files) > settings.max_upload_batch:
        raise bad_request(f"单次最多上传 {settings.max_upload_batch} 个文件")

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for filename, content in files:
        rejected_item = _validate(filename, content)
        if rejected_item is not None:
            rejected.append(rejected_item)
            continue

        digest = hashlib.sha256(content).hexdigest()
        duplicated = db.scalar(
            select(Document).where(
                Document.kb_id == ctx.kb_id,
                Document.sha256 == digest,
                Document.is_deleted.is_(False),
            )
        )
        if duplicated is not None:
            rejected.append(
                {
                    "filename": filename,
                    "code": 40901,
                    "message": f"重复上传（已存在：{duplicated.filename}）",
                }
            )
            continue

        ext = sniff_ext(content, filename) or filename.rsplit(".", 1)[-1].lower()
        verdict = _audit_upload(ctx, filename, content, ext)
        if verdict.get("result") == "block":
            rejected.append(
                {
                    "filename": filename,
                    "code": 50301,
                    "message": f"文件未通过内容审核（{verdict.get('label')}）",
                }
            )
            continue

        stored = get_storage().save_bytes(
            rel_dir=f"uploads/{ctx.kb.tenant_id}/{ctx.kb_id}", filename=filename, content=content
        )
        doc = Document(
            tenant_id=ctx.kb.tenant_id,
            kb_id=ctx.kb_id,
            uploader_id=ctx.user.id,
            filename=filename,
            file_ext=ext,
            file_size=stored.size,
            sha256=stored.sha256,
            storage_path=stored.rel_path,
            status=DocStatus.PENDING.value,
            stage_progress=0,
            audit_status=str(verdict.get("audit_status") or "pass"),
        )
        db.add(doc)
        db.flush()
        task = task_service.enqueue(
            db,
            tenant_id=ctx.kb.tenant_id,
            document_id=doc.id,
            task_type=TaskType.PARSE.value,
            stage="parse",
        )
        accepted.append(
            {
                "document_id": str(doc.id),
                "task_id": str(task.id),
                "filename": doc.filename,
                "status": doc.status,
            }
        )

    if accepted:
        ctx.kb.doc_count = int(ctx.kb.doc_count or 0) + len(accepted)
        db.flush()
    logger.info(
        "上传完成 kb=%s accepted=%s rejected=%s", ctx.kb_id, len(accepted), len(rejected)
    )
    return {"accepted": accepted, "rejected": rejected}


def _validate(filename: str, content: bytes) -> dict[str, Any] | None:
    """返回拒绝项（None 表示通过）。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in settings.allowed_ext_set:
        return {"filename": filename, "code": 41501, "message": f"不支持的文件类型：.{ext or '未知'}"}
    if len(content) > settings.max_upload_size_bytes:
        return {
            "filename": filename,
            "code": 41301,
            "message": f"文件超出大小限制（{settings.max_upload_size_mb}MB）",
        }
    if not content:
        return {"filename": filename, "code": 40001, "message": "文件内容为空"}
    if sniff_ext(content, filename) is None:
        return {"filename": filename, "code": 41501, "message": "文件内容与扩展名不符，疑似伪造"}
    return None


def _audit_upload(ctx: KbContext, filename: str, content: bytes, ext: str) -> dict[str, Any]:
    """上传卡点审核：图片走图片审核；md/txt 附带文本内容；其余仅审文件名。"""
    moderator = get_moderator()
    if ext in _IMAGE_EXT:
        verdict = moderator.check_image(
            f"local://{ctx.kb_id}/{filename}",
            AuditScene.UPLOAD.value,
            tenant_id=ctx.kb.tenant_id,
            object_type="document",
        )
    else:
        text = filename
        if ext in _TEXT_EXT:
            text = f"{filename}\n{content.decode('utf-8', errors='replace')[:2000]}"
        verdict = moderator.check_text(
            text,
            AuditScene.UPLOAD.value,
            tenant_id=ctx.kb.tenant_id,
            object_type="document",
        )
    return {
        "result": verdict.result,
        "label": verdict.label,
        "audit_status": verdict.audit_status,
    }


# ── 查询与操作 ──────────────────────────────────────────────────────────
def list_documents(
    db: Session,
    ctx: KbContext,
    *,
    status: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Document], int]:
    stmt = select(Document).where(
        Document.kb_id == ctx.kb_id, Document.is_deleted.is_(False)
    )
    if status:
        stmt = stmt.where(Document.status == status)
    if keyword:
        stmt = stmt.where(Document.filename.contains(keyword))
    stmt = stmt.order_by(Document.created_at.desc())
    return paginate(db, stmt, page=page, page_size=page_size)


def get_document(db: Session, ctx: KbContext, document_id: str) -> Document:
    doc = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.kb_id == ctx.kb_id,
            Document.is_deleted.is_(False),
        )
    )
    if doc is None:
        raise not_found("文档不存在")
    return doc


def document_status(doc: Document) -> dict[str, Any]:
    return {
        "document_id": str(doc.id),
        "status": doc.status,
        "stage_progress": int(doc.stage_progress or 0),
        "audit_status": doc.audit_status,
        "dify_indexing_status": doc.dify_indexing_status,
        "error_code": doc.error_code,
        "error_message": doc.error_message,
        "retry_count": int(doc.retry_count or 0),
    }


def _segments_preview_text(ctx: KbContext, doc: Document, *, limit: int = 100) -> str:
    """把 Dify 分块内容拼成预览文本。

    「Dify 侧解析」的文档（`parse_mode=dify`）本地没有 Markdown，正文只存在于 Dify 的分块里。
    拉取失败返回空串——预览不该因为 Dify 抖动而变成 502。
    """
    if not doc.dify_document_id:
        return ""
    try:
        payload = get_dify_client().list_segments(
            dataset_id=ctx.dataset_id, document_id=str(doc.dify_document_id), page=1, limit=limit
        )
    except Exception as exc:  # noqa: BLE001 - 预览是只读增值功能，失败降级
        logger.warning("预览：拉取 Dify 分块失败 doc=%s err=%s", doc.id, exc)
        return ""
    parts = [str(seg.get("content") or "") for seg in payload.get("items") or []]
    return "\n\n".join(part for part in parts if part)


def preview(db: Session, ctx: KbContext, document_id: str, *, page: int, page_size: int) -> dict[str, Any]:
    """解析结果 Markdown 预览（分页返回，避免大文件拖垮浏览器）。"""
    doc = get_document(db, ctx, document_id)
    text = ""
    if doc.parsed_path and get_storage().exists(doc.parsed_path):
        text = get_storage().read_text(doc.parsed_path)
    elif doc.dify_document_id:
        # parse_mode=dify：正文在 Dify 侧，用分块拼预览
        text = _segments_preview_text(ctx, doc)
    elif doc.status != DocStatus.READY.value:
        raise bad_request("文档尚未解析完成，暂无预览内容")
    total = len(text)
    start = (page - 1) * page_size
    chunk = text[start : start + page_size]
    return {
        "document_id": str(doc.id),
        "filename": doc.filename,
        "mode": doc.parse_mode,
        "page": page,
        "page_size": page_size,
        "total_chars": total,
        "truncated": start + page_size < total,
        "content": chunk,
    }


def list_chunks(
    db: Session, ctx: KbContext, document_id: str, *, page: int, page_size: int
) -> dict[str, Any]:
    """分块查看：代理 Dify segments 接口（分块真相源在 Dify）。"""
    doc = get_document(db, ctx, document_id)
    if not doc.dify_document_id:
        raise bad_request("文档尚未入库到 Dify，暂无分块数据")
    payload = get_dify_client().list_segments(
        dataset_id=ctx.dataset_id, document_id=doc.dify_document_id, page=page, limit=page_size
    )
    items: list[dict[str, Any]] = []
    for seg in payload.get("items") or []:
        if not isinstance(seg, dict):
            continue
        content = str(seg.get("content") or "")
        items.append(
            {
                "segment_id": str(seg.get("segment_id") or ""),
                "position": int(seg.get("position") or 0),
                "chars": len(content),
                "content": content,
            }
        )
    return {
        "document_id": str(doc.id),
        "total": int(payload.get("total") or len(items)),
        "items": items,
    }


def retry_document(db: Session, ctx: KbContext, document_id: str, stage: str | None = None) -> dict[str, Any]:
    """重试：重置状态并重新入队（默认从解析阶段开始）。"""
    doc = get_document(db, ctx, document_id)
    if task_service.pending_task_for(db, doc.id) is not None:
        raise conflict("该文档已有进行中的任务，请稍后再试")
    doc.status = DocStatus.PENDING.value
    doc.stage_progress = 0
    doc.error_code = None
    doc.error_message = None
    doc.retry_count = int(doc.retry_count or 0) + 1
    task = task_service.enqueue(
        db,
        tenant_id=ctx.kb.tenant_id,
        document_id=doc.id,
        task_type=TaskType.REPARSE.value,
        stage=stage or "parse",
    )
    db.flush()
    return {"document_id": str(doc.id), "task_id": str(task.id), "status": doc.status}


def delete_document(db: Session, ctx: KbContext, document_id: str) -> None:
    """软删本地 + 删 Dify document（失败仅告警，本地状态先落地）。"""
    doc = get_document(db, ctx, document_id)
    doc.mark_deleted()
    doc.status = DocStatus.FAILED.value
    if ctx.kb.doc_count:
        ctx.kb.doc_count = max(0, int(ctx.kb.doc_count) - 1)
    db.flush()
    if doc.dify_document_id and ctx.dataset_id:
        try:
            get_dify_client().delete_document(
                dataset_id=ctx.dataset_id, document_id=doc.dify_document_id
            )
        except Exception as exc:
            logger.warning("删除 Dify document 失败 doc=%s err=%s", doc.id, exc)
    for rel_path in (doc.parsed_path, doc.storage_path):
        if rel_path:
            try:
                get_storage().delete(rel_path)
            except Exception as exc:  # 文件可能已被清理
                logger.debug("删除文件失败 %s: %s", rel_path, exc)
