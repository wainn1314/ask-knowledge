"""文档路由（docs/05 §4）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, KbContext, get_current_user, get_kb_context, kb_guard
from app.core.constants import KbRole
from app.core.errors import envelope, not_found
from app.db.session import get_db
from app.models.documents import Document
from app.schemas.document import (
    ChunkListOut,
    DocumentOut,
    DocumentPreviewOut,
    DocumentRetryIn,
    DocumentStatusOut,
    UploadResult,
)
from app.services import document_service

router = APIRouter(tags=["documents"])


def _doc_ctx(
    document_id: str,
    db: Session,
    user: CurrentUser,
    min_role: str = KbRole.VIEWER.value,
) -> tuple[KbContext, Document]:
    """以 document_id 反查所属 KB 并复用 KB 权限校验（越权统一 40401）。"""
    doc = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == user.tenant_id,
            Document.is_deleted.is_(False),
        )
    )
    if doc is None:
        raise not_found("文档不存在")
    return get_kb_context(str(doc.kb_id), db, user, min_role), doc


@router.post("/kbs/{kb_id}/documents", status_code=202, summary="上传文档（批量）")
async def upload_documents(
    files: list[UploadFile] = File(default_factory=list),
    files_bracket: list[UploadFile] = File(default_factory=list, alias="files[]"),
    ctx: KbContext = Depends(kb_guard(KbRole.EDITOR)),
    db: Session = Depends(get_db),
) -> dict:
    """兼容字段名 `files` 与 `files[]`（前端/Postman 两种写法都能用）。"""
    uploads: list[tuple[str, bytes]] = []
    for item in [*files, *files_bracket]:
        if not item.filename:
            continue
        uploads.append((item.filename, await item.read()))
    result = document_service.upload_documents(db, ctx, uploads)
    return envelope(0, "accepted", UploadResult.model_validate(result).model_dump())


@router.get("/kbs/{kb_id}/documents", summary="文档列表")
def list_documents(
    status: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    ctx: KbContext = Depends(kb_guard(KbRole.VIEWER)),
    db: Session = Depends(get_db),
) -> dict:
    items, total = document_service.list_documents(
        db, ctx, status=status, keyword=keyword, page=page, page_size=page_size
    )
    return envelope(
        0,
        "ok",
        {
            "items": [DocumentOut.model_validate(doc).model_dump() for doc in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


@router.get("/documents/{document_id}", summary="文档详情")
def get_document(
    document_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _, doc = _doc_ctx(document_id, db, user)
    return envelope(0, "ok", DocumentOut.model_validate(doc).model_dump())


@router.get("/documents/{document_id}/status", summary="文档状态（2s 轮询）")
def document_status(
    document_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _, doc = _doc_ctx(document_id, db, user)
    return envelope(0, "ok", DocumentStatusOut.model_validate(document_service.document_status(doc)).model_dump())


@router.get("/documents/{document_id}/preview", summary="解析后 Markdown 预览")
def preview(
    document_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=4000, ge=200, le=20000),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    ctx, doc = _doc_ctx(document_id, db, user)
    data = document_service.preview(db, ctx, str(doc.id), page=page, page_size=page_size)
    return envelope(0, "ok", DocumentPreviewOut.model_validate(data).model_dump())


@router.get("/documents/{document_id}/chunks", summary="分块查看（代理 Dify segments）")
def list_chunks(
    document_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    ctx, doc = _doc_ctx(document_id, db, user)
    data = document_service.list_chunks(db, ctx, str(doc.id), page=page, page_size=page_size)
    return envelope(0, "ok", ChunkListOut.model_validate(data).model_dump())


@router.post("/documents/{document_id}/retry", summary="重新解析 / 入库")
def retry_document(
    document_id: str,
    payload: DocumentRetryIn | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    ctx, doc = _doc_ctx(document_id, db, user, KbRole.EDITOR.value)
    stage = payload.stage if payload else None
    return envelope(0, "ok", document_service.retry_document(db, ctx, str(doc.id), stage))


@router.delete("/documents/{document_id}", summary="删除文档（软删）")
def delete_document(
    document_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    ctx, doc = _doc_ctx(document_id, db, user, KbRole.EDITOR.value)
    document_service.delete_document(db, ctx, str(doc.id))
    return envelope(0, "ok", {"id": str(doc.id)})
