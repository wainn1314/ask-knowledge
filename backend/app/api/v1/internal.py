"""内部接口：Dify 处理完成回调（docs/05 §9）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_internal_token
from app.core.constants import DifyIndexingStatus, DocStatus
from app.core.errors import bad_request, envelope, not_found
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.documents import Document
from app.models.knowledge import Kb
from app.services.dify import get_dify_client

logger = get_logger(__name__)

router = APIRouter(tags=["internal"], dependencies=[Depends(require_internal_token)])


@router.get("/internal/documents/{document_id}/callback", summary="同步 Dify 入库状态（内部）")
def document_callback(document_id: str, db: Session = Depends(get_db)) -> dict:
    """Dify 处理完成/补偿用：拉取 indexing-status 并同步本地文档状态。"""
    doc = db.get(Document, document_id)
    if doc is None or doc.is_deleted:
        raise not_found("文档不存在")
    if not doc.dify_document_id:
        raise bad_request("文档尚未提交到 Dify")
    kb = db.get(Kb, doc.kb_id)
    if kb is None or not kb.dify_dataset_id:
        raise bad_request("知识库未关联 Dify dataset")

    status = get_dify_client().get_indexing_status(
        dataset_id=kb.dify_dataset_id, document_id=str(doc.dify_document_id)
    )
    doc.dify_indexing_status = status
    if status == DifyIndexingStatus.COMPLETED.value and doc.status != DocStatus.READY.value:
        doc.status = DocStatus.READY.value
        doc.stage_progress = 100
        doc.error_code = None
        doc.error_message = None
    elif status == DifyIndexingStatus.ERROR.value:
        doc.status = DocStatus.FAILED.value
        doc.error_code = "50201"
        doc.error_message = "Dify 入库失败"
    db.flush()
    logger.info("内部回调同步 doc=%s status=%s", doc.id, status)
    return envelope(0, "ok", {"document_id": str(doc.id), "status": doc.status, "dify_indexing_status": status})
