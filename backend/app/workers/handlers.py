"""异步任务处理器：解析 → 审核 → 入库（Dify）三阶段流水线（docs/04 §5）。"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import (
    AuditScene,
    DifyIndexingStatus,
    DocStatus,
    ParseMode,
    TaskStatus,
)
from app.core.errors import TOO_MANY_REQUESTS, BizError, dify_error, parse_error
from app.core.logging import get_logger
from app.integrations.mineru_client import get_parser
from app.integrations.storage import get_storage, mime_for_ext
from app.models.documents import Document, ParseTask
from app.models.knowledge import Kb
from app.services import task_service
from app.services.dify import get_dify_client
from app.services.document_service import clean_markdown_for_index
from app.services.moderation import get_moderator

logger = get_logger(__name__)

#: 入库轮询间隔（秒）。真正生效值取 `settings.indexing_poll_interval_seconds`。
#: 真实教训：Dify 的 indexing-status 也吃「知识库接口」每分钟配额（当前订阅 10 次/分钟），
#: 原来 1 秒一次的轮询会在几十秒内把配额吃光 —— 之后用户点「出题」时，检索接口直接 403
#: （前端就显示「Dify 返回 403」）。所以这里默认放慢到 5 秒，并由 `quota.py` 的闸门兜底。
POLL_INTERVAL_SECONDS = 5.0


def _poll_interval_seconds() -> float:
    """本次轮询的等待间隔（`.env` 可调；不允许 0 造成忙等）。"""
    return max(0.5, float(settings.indexing_poll_interval_seconds or POLL_INTERVAL_SECONDS))
#: 送审文本上限（避免超大文档把审核服务打爆）
AUDIT_TEXT_LIMIT = 100_000


class StageSkipped(Exception):
    """当前阶段不满足执行条件（例如重复委托），跳过后续阶段但不算失败。"""


def handle(db: Session, task: ParseTask) -> None:
    """任务入口：按 task.stage 顺序推进 解析 → 审核 → 入库。"""
    doc = db.get(Document, task.document_id)
    if doc is None or doc.is_deleted:
        logger.warning("任务对应文档不存在，直接结束 task=%s doc=%s", task.id, task.document_id)
        task_service.mark_success(db, task)
        return
    stage = task.stage or "parse"
    try:
        if stage == "parse":
            _run_parse(db, task, doc)
            stage = "audit"
        if stage == "audit":
            if not _run_audit(db, task, doc):
                task_service.mark_success(db, task)
                return
            stage = "index"
        if stage == "index":
            _run_index(db, task, doc)
        task_service.mark_success(db, task)
        logger.info("文档处理完成 doc=%s status=%s", doc.id, doc.status)
    except StageSkipped as exc:
        logger.info("任务阶段跳过 task=%s reason=%s", task.id, exc)
        task_service.mark_success(db, task)
    except BizError as exc:
        _fail(db, task, doc, exc.code, exc.message)
    except Exception as exc:  # 未知异常：交给重试机制，避免线程挂掉
        logger.exception("文档处理异常 doc=%s", doc.id)
        _fail(db, task, doc, 50001, str(exc)[:500])


def _fail(db: Session, task: ParseTask, doc: Document, code: int, message: str) -> None:
    """标记文档失败 + 任务重试/终止；重试时文档回到 pending 等待下一轮。"""
    status = task_service.mark_failed(db, task, message)
    doc.error_code = str(code)
    doc.error_message = message[:1000]
    doc.status = DocStatus.FAILED.value if status == TaskStatus.DEAD.value else DocStatus.PENDING.value
    db.flush()


# ── 阶段 1：解析 ────────────────────────────────────────────────────────
def _defer_to_dify(doc: Document) -> bool:
    """该文档是否「不在本地解析、直传原文件给 Dify」（`DIFY_PARSE_EXTENSIONS`）。

    本地解析器啃不动的类型（典型是 PDF：未装 pypdf 时抽不出文本，扫描件/作品集更无能为力）
    直接交给 Dify —— 自带提取器，或知识库流水线里的解析分支（如 else → parse file）。
    """
    return (doc.file_ext or "").lower() in settings.dify_parse_ext_set


def _run_parse(db: Session, task: ParseTask, doc: Document) -> None:
    task.stage = "parse"
    doc.status = DocStatus.PARSING.value
    doc.stage_progress = 10
    db.flush()

    storage = get_storage()
    if not storage.exists(doc.storage_path):
        raise parse_error("源文件不存在（可能已被清理），请重新上传")
    if _defer_to_dify(doc):
        # 本地不产出 Markdown：解析与分块都交给 Dify（阶段 3 用 create-by-file 直传原文件）
        doc.parsed_path = None
        doc.parse_mode = ParseMode.DIFY.value
        doc.parse_duration_ms = 0
        doc.page_count = None
        doc.stage_progress = 30
        db.flush()
        logger.info(
            "跳过本地解析（命中 DIFY_PARSE_EXTENSIONS）doc=%s ext=%s", doc.id, doc.file_ext
        )
        return
    content = storage.read_bytes(doc.storage_path)
    started = time.perf_counter()
    result = get_parser().parse(filename=doc.filename, content=content, ext=doc.file_ext)
    duration_ms = int((time.perf_counter() - started) * 1000)
    if not result.markdown.strip():
        raise parse_error("解析结果为空，请检查文件内容")

    saved = storage.save_text(
        rel_dir=f"parsed/{doc.tenant_id}/{doc.kb_id}/{doc.id}",
        filename="markdown.md",
        text=result.markdown,
    )
    doc.parsed_path = saved.rel_path
    doc.parse_mode = result.mode
    doc.parse_duration_ms = duration_ms
    doc.page_count = result.page_count
    doc.stage_progress = 30
    db.flush()
    logger.info(
        "解析完成 doc=%s mode=%s chars=%s duration=%sms",
        doc.id,
        result.mode,
        len(result.markdown),
        duration_ms,
    )


# ── 阶段 2：审核 ────────────────────────────────────────────────────────
def _run_audit(db: Session, task: ParseTask, doc: Document) -> bool:
    """返回 True 表示通过（继续入库）；False 表示被拦截（文档置 blocked）。"""
    task.stage = "audit"
    doc.status = DocStatus.AUDITING.value
    doc.stage_progress = 60
    db.flush()

    text = ""
    if doc.parsed_path and get_storage().exists(doc.parsed_path):
        text = get_storage().read_text(doc.parsed_path)[:AUDIT_TEXT_LIMIT]
    verdict = get_moderator().check_text(
        f"{doc.filename}\n{text}",
        AuditScene.PARSE.value,
        tenant_id=str(doc.tenant_id),
        object_type="document",
        object_id=str(doc.id),
        session=db,
    )
    doc.audit_status = verdict.audit_status
    if verdict.blocked:
        doc.status = DocStatus.BLOCKED.value
        doc.stage_progress = 100
        doc.error_code = "50301"
        doc.error_message = f"内容未通过审核（{verdict.label}）"
        db.flush()
        logger.warning("文档被审核拦截 doc=%s label=%s", doc.id, verdict.label)
        return False
    if verdict.result in {"pass", "review"}:
        doc.stage_progress = 70
    db.flush()
    return True


# ── 阶段 3：入库（Dify） ────────────────────────────────────────────────
def _run_index(db: Session, task: ParseTask, doc: Document, *, allow_recreate: bool = True) -> None:
    task.stage = "index"
    kb = db.get(Kb, doc.kb_id)
    if kb is None or not kb.dify_dataset_id:
        raise dify_error("知识库未关联 Dify dataset，无法入库")
    doc.status = DocStatus.INDEXING.value
    doc.stage_progress = 80
    db.flush()

    #: 新版 Dify 的入库状态要按「批次」查询，batch 由 create-* 返回（不落库，仅本次调用用）
    batch: str | None = None
    if _defer_to_dify(doc):
        # Dify 侧解析通道：原文件直传，解析/分块由 Dify（或知识库流水线）负责
        batch = _send_file_to_dify(db, doc, kb)
    else:
        markdown = _load_index_markdown(db, doc, kb)
        if not doc.dify_document_id:
            batch = _send_text_to_dify(db, doc, kb, markdown)

    try:
        status = _wait_indexing(db, doc, kb.dify_dataset_id, batch=batch)
    except BizError as exc:
        # Dify 侧文档被删/知识库被重置（mock 重启也会遇到）：清空 ID 后重新入库一次
        if allow_recreate and doc.dify_document_id:
            logger.warning("Dify 侧文档已失效，重新入库 doc=%s err=%s", doc.id, exc)
            doc.dify_document_id = None
            doc.dify_indexing_status = None
            db.flush()
            _run_index(db, task, doc, allow_recreate=False)
            return
        raise

    if status != DifyIndexingStatus.COMPLETED.value:
        raise dify_error(
            f"Dify 入库未完成（indexing_status={status}）", document_id=doc.dify_document_id
        )

    doc.status = DocStatus.READY.value
    doc.stage_progress = 100
    doc.error_code = None
    doc.error_message = None
    db.flush()
    _sync_chunk_count(db, doc, kb.dify_dataset_id)
    logger.info(
        "入库完成 doc=%s dify_doc=%s chunks=%s",
        doc.id,
        doc.dify_document_id,
        doc.chunk_count,
    )


# ── 入库辅助：本地解析通道 vs Dify 侧解析通道 ───────────────────────────
def _load_index_markdown(db: Session, doc: Document, kb: Kb) -> str:
    """本地解析通道：读取 Markdown 并清洗（去掉 `---` 这类碎片来源）。

    清洗后正文与 Dify 里的旧版本不一致时删掉旧文档重建，否则索引里留着的还是带装饰线的旧碎片。
    """
    if not doc.parsed_path or not get_storage().exists(doc.parsed_path):
        raise parse_error("解析结果缺失，无法入库")
    markdown = get_storage().read_text(doc.parsed_path)
    cleaned = clean_markdown_for_index(markdown)
    if cleaned != markdown and doc.dify_document_id:
        logger.info(
            "正文清洗后有变化，重建 Dify 文档 doc=%s dify_doc=%s", doc.id, doc.dify_document_id
        )
        try:
            get_dify_client().delete_document(
                dataset_id=str(kb.dify_dataset_id), document_id=str(doc.dify_document_id)
            )
        except BizError as exc:  # 已被 Dify 侧删掉 / 知识库被重置：继续重建
            logger.warning("删除 Dify 侧文档失败，直接重建 doc=%s err=%s", doc.id, exc)
        doc.dify_document_id = None
        doc.dify_indexing_status = None
        db.flush()
    return cleaned


def _send_text_to_dify(db: Session, doc: Document, kb: Kb, markdown: str) -> str | None:
    """本地解析通道：Markdown 走 `create-by-text`，返回 batch（轮询入库状态用）。"""
    created = get_dify_client().create_document_by_text(
        dataset_id=str(kb.dify_dataset_id),
        name=doc.filename,
        text=markdown,
        chunk_size=int(kb.chunk_size or settings.default_chunk_size),
        chunk_overlap=int(kb.chunk_overlap or settings.default_chunk_overlap),
    )
    _save_remote_document(db, doc, created)
    return str(created.get("batch") or "") or None


def _send_file_to_dify(db: Session, doc: Document, kb: Kb) -> str | None:
    """Dify 侧解析通道：原文件走 `create-by-file`，返回 batch（轮询入库状态用）。

    重新入库（已有 Dify 文档）时先删旧文档：本地通道能靠 Markdown 比对决定是否重建，
    这条通道没有可比对的文本，统一重建，避免索引里留着上一版内容。
    """
    storage = get_storage()
    if not storage.exists(doc.storage_path):
        raise parse_error("源文件不存在（可能已被清理），请重新上传")
    client = get_dify_client()
    if doc.dify_document_id:
        try:
            client.delete_document(
                dataset_id=str(kb.dify_dataset_id), document_id=str(doc.dify_document_id)
            )
        except BizError as exc:
            logger.warning("删除 Dify 侧文档失败，直接重建 doc=%s err=%s", doc.id, exc)
        doc.dify_document_id = None
        doc.dify_indexing_status = None
        db.flush()
    created = client.create_document_by_file(
        dataset_id=str(kb.dify_dataset_id),
        name=doc.filename,
        filename=doc.filename,
        content=storage.read_bytes(doc.storage_path),
        mime_type=mime_for_ext(doc.file_ext, doc.filename),
        chunk_size=int(kb.chunk_size or settings.default_chunk_size),
        chunk_overlap=int(kb.chunk_overlap or settings.default_chunk_overlap),
    )
    logger.info(
        "原文件直传 Dify（Dify 侧解析）doc=%s dify_doc=%s batch=%s",
        doc.id,
        created.get("document_id"),
        created.get("batch"),
    )
    _save_remote_document(db, doc, created)
    return str(created.get("batch") or "") or None


def _save_remote_document(db: Session, doc: Document, created: dict[str, Any]) -> None:
    """记录 Dify 返回的 document_id 与初始入库状态（两条通道共用）。"""
    dify_document_id = str(created.get("document_id") or "")
    if not dify_document_id:
        raise dify_error("Dify 未返回 document_id", payload=str(created)[:200])
    doc.dify_document_id = dify_document_id
    doc.dify_indexing_status = str(
        created.get("indexing_status") or DifyIndexingStatus.WAITING.value
    )
    db.flush()


def _wait_indexing(db: Session, doc: Document, dataset_id: str, batch: str | None = None) -> str:
    """轮询 Dify 入库状态直到 completed / error / 超时（超时按失败重试）。

    被 Dify 频控挡下（42901）只当「暂时问不到」：继续等下一轮，**绝不**把文档判失败
    ——配额会在 1 分钟内回补，而文档已经交给 Dify 解析了，判失败会白跑一遍。
    """
    client = get_dify_client()
    timeout = float(settings.dify_indexing_timeout_seconds or 600.0)
    deadline = time.monotonic() + timeout
    while True:
        try:
            status = client.get_indexing_status(
                dataset_id=dataset_id, document_id=str(doc.dify_document_id), batch=batch
            )
        except BizError as exc:
            if exc.code != TOO_MANY_REQUESTS:
                raise
            logger.info("入库状态查询被 Dify 频控挡下，稍后重试 doc=%s", doc.id)
            if time.monotonic() >= deadline:
                logger.warning(
                    "入库轮询超时 doc=%s（频控中，最后状态=%s）",
                    doc.id,
                    doc.dify_indexing_status,
                )
                return str(doc.dify_indexing_status or DifyIndexingStatus.WAITING.value)
            time.sleep(_poll_interval_seconds())
            continue
        doc.dify_indexing_status = status
        db.flush()
        if status in {DifyIndexingStatus.COMPLETED.value, DifyIndexingStatus.ERROR.value}:
            return status
        if time.monotonic() >= deadline:
            logger.warning("入库轮询超时 doc=%s status=%s", doc.id, status)
            return status
        time.sleep(_poll_interval_seconds())


def _sync_chunk_count(db: Session, doc: Document, dataset_id: str) -> None:
    """回填分块数（前端文档详情页展示，失败不影响主流程）。"""
    try:
        payload = get_dify_client().list_segments(
            dataset_id=dataset_id, document_id=str(doc.dify_document_id), page=1, limit=1
        )
        doc.chunk_count = int(payload.get("total") or 0)
        db.flush()
    except Exception as exc:
        logger.debug("回填 chunk_count 失败 doc=%s err=%s", doc.id, exc)
