"""Dify 知识库（dataset）相关接口 —— 真实 REST 调用。

对应 docs/05 §10 调用对照表：建库 / 删库 / create-by-text / indexing-status /
文档列表与删除 / segments / retrieve。（文件分两段写入，下段为查询类接口。）
"""

from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.core.constants import DifyIndexingStatus
from app.core.errors import TOO_MANY_REQUESTS, BizError
from app.core.logging import get_logger
from app.services.dify.client import CHUNK_SEPARATOR, RetrievedSegment
from app.services.dify.mapping import segments_from_retrieval_records
from app.services.dify.quota import (
    documents_cache,
    retrieval_cache,
    segments_cache,
    status_path_cache,
)

logger = get_logger(__name__)

SEARCH_METHODS = {
    "semantic": "semantic_search",
    "full_text": "full_text_search",
    "hybrid": "hybrid_search",
}


def _extract_indexing_status(payload: dict[str, Any], document_id: str) -> str:
    """从响应里取出目标文档的入库状态。

    兼容两种 Dify 响应：老版 `data` 是对象；新版 `/documents/{batch}/indexing-status`
    的 `data` 是「该批次内文档状态数组」。文档详情接口则直接把文档对象放在顶层。
    """
    data = payload.get("data")
    if isinstance(data, dict):
        records = [data]
    elif isinstance(data, list):
        records = [item for item in data if isinstance(item, dict)]
    elif payload.get("indexing_status") is not None or payload.get("id") is not None:
        records = [payload]
    else:
        records = []
    if not records:
        return ""
    for record in records:
        if str(record.get("id") or "") == str(document_id):
            return str(record.get("indexing_status") or "")
    # 批次接口只回一条时（常见情况）直接采用，避免因字段差异误判为未找到
    if len(records) == 1:
        return str(records[0].get("indexing_status") or "")
    return ""


def _is_empty_collection_error(exc: BizError) -> bool:
    """识别「向量集合尚未建立」：空知识库第一次检索时 Dify 会返回该错误。

    这种情况等价于「没有召回到任何内容」，应按空结果处理而不是 50201。
    """
    body = str((exc.detail or {}).get("body") or "")
    text = f"{body} {exc.message}".lower()
    return "collection not found" in text


def _is_quota_error(exc: BizError) -> bool:
    """上游频率受限（本服务的闸门 42901，或 Dify 的 403 限流已被归一为 42901）。"""
    return exc.code == TOO_MANY_REQUESTS


def _cached_json(
    cache: Any,
    key: str,
    ttl: float,
    fetch: Any,
    *,
    label: str,
) -> Any:
    """读接口统一套路：TTL 内直接用缓存；被限流时退回旧缓存（stale-while-error）。

    配额只有「每分钟 N 次」这一点额度，任何重复查询都应命中缓存；真被限流时
    用上一次的结果兜底，比直接给用户报错更有用（数据最多旧几十秒）。
    """
    hit = cache.get(key)
    if hit is not None:
        logger.debug("%s 命中缓存 key=%s", label, key[:80])
        return hit
    try:
        value = fetch()
    except BizError as exc:
        if not _is_quota_error(exc):
            raise
        stale = cache.get(key, allow_stale=True)
        if stale is None:
            raise
        logger.warning("%s 被 Dify 频控挡下，退回上一次结果 key=%s", label, key[:80])
        return stale
    cache.set(key, value, ttl=ttl)
    return value


class DatasetClientMixin:
    """Mixin：依赖 HttpDifyBase 提供的 request / request_json。"""

    def create_dataset(self, *, name: str, description: str = "") -> dict[str, Any]:
        body = {
            "name": name,
            "description": description,
            "indexing_technique": "high_quality",  # 高质量索引才能向量检索
            "permission": "only_me",
        }
        data = self.request_json("POST", "/datasets", app="dataset", json_body=body)
        return {"id": data.get("id"), "name": data.get("name")}

    def delete_dataset(self, *, dataset_id: str) -> None:
        self.request("DELETE", f"/datasets/{dataset_id}", app="dataset")

    def create_document_by_text(
        self,
        *,
        dataset_id: str,
        name: str,
        text: str,
        chunk_size: int,
        chunk_overlap: int,
        separator: str = CHUNK_SEPARATOR,
    ) -> dict[str, Any]:
        """入库 Markdown 文本（审核 + MinerU 之后的产物），分块规则与 KB 配置一致。"""
        body = {
            "name": name,
            "text": text,
            "indexing_technique": "high_quality",
            "process_rule": {
                "mode": "custom",
                "rules": {
                    "pre_processing_rules": [
                        {"id": "remove_extra_spaces", "enabled": True},
                        {"id": "remove_urls_emails", "enabled": False},
                    ],
                    "segmentation": {
                        "separator": separator,
                        "max_tokens": chunk_size,
                        "chunk_overlap": chunk_overlap,
                    },
                },
            },
        }
        data = self.request_json(
            "POST",
            f"/datasets/{dataset_id}/document/create-by-text",
            app="dataset",
            json_body=body,
        )
        document = data.get("document") or {}
        return {
            "document_id": document.get("id"),
            "name": document.get("name"),
            "indexing_status": document.get("indexing_status"),
            "batch": data.get("batch"),
        }

    def create_document_by_file(
        self,
        *,
        dataset_id: str,
        name: str,
        filename: str,
        content: bytes,
        mime_type: str = "application/octet-stream",
        chunk_size: int = 512,
        chunk_overlap: int = 50,
        separator: str = CHUNK_SEPARATOR,
        doc_form: str = "text_model",
    ) -> dict[str, Any]:
        """直传原文件入库（`POST /datasets/{id}/document/create-by-file`）。

        解析交给 Dify：自带提取器，或知识库流水线（pipeline）里的解析分支 —— 所以本后端
        不需要在本机装 pypdf / MinerU 也能吃 PDF、扫描件。入库状态同样按 `batch` 轮询。

        ⚠️ 流水线型知识库（`GET /datasets/{id}` 返回的 `pipeline_id` 非空，如 Dify 里的
        「Untitled 1」）以流水线节点的分块配置为准，这里传的 `process_rule` 只对非流水线
        知识库生效。
        """
        body = {
            "indexing_technique": "high_quality",
            "doc_form": doc_form,
            "process_rule": json.dumps(
                {
                    "mode": "custom",
                    "rules": {
                        "pre_processing_rules": [
                            {"id": "remove_extra_spaces", "enabled": True},
                            {"id": "remove_urls_emails", "enabled": False},
                        ],
                        "segmentation": {
                            "separator": separator,
                            "max_tokens": chunk_size,
                            "chunk_overlap": chunk_overlap,
                        },
                    },
                },
                ensure_ascii=False,
            ),
        }
        data = self.request_json(
            "POST",
            f"/datasets/{dataset_id}/document/create-by-file",
            app="dataset",
            data=body,
            files={"file": (filename, content, mime_type)},
            # 上传大文件（最大 15MB）+ 服务端建文档比普通 JSON 调用慢，给足超时
            timeout=max(float(settings.dify_timeout_seconds or 60.0), 120.0),
        )
        document = data.get("document") or {}
        return {
            "document_id": document.get("id"),
            "name": document.get("name") or name,
            "indexing_status": document.get("indexing_status")
            or DifyIndexingStatus.WAITING.value,
            "batch": data.get("batch"),
        }

    def get_indexing_status(
        self, *, dataset_id: str, document_id: str, batch: str | None = None
    ) -> str:
        """文档入库状态。

        新版 Dify 把该接口改成了「按批次查询」：`GET /datasets/{id}/documents/{batch}/indexing-status`
        （`data` 为该批次内文档状态数组，`batch` 由 create-by-text 返回）；老版是
        `.../documents/{document_id}/indexing-status`。这里按 batch → document_id → 文档详情的
        顺序降级，两种版本都能用。
        """
        candidates: list[str] = []
        if batch:
            candidates.append(f"/datasets/{dataset_id}/documents/{batch}/indexing-status")
        candidates.append(f"/datasets/{dataset_id}/documents/{document_id}/indexing-status")
        candidates.append(f"/datasets/{dataset_id}/documents/{document_id}")

        # 「上次走通的 path」优先：轮询是高频调用，每次都试 3 条会白烧 3 倍配额
        hint_key = f"{dataset_id}:{batch or document_id}"
        hint = status_path_cache.get(hint_key)
        if hint and hint in candidates:
            candidates = [hint] + [path for path in candidates if path != hint]

        error: BizError | None = None
        for path in candidates:
            try:
                payload = self.request_json("GET", path, app="dataset")
            except BizError as exc:
                error = exc
                logger.debug("查入库状态失败 path=%s err=%s", path, exc.message)
                continue
            status = _extract_indexing_status(payload, document_id)
            if status:
                status_path_cache[hint_key] = path
                return status
        if error is not None:
            raise error
        return DifyIndexingStatus.WAITING.value

    def delete_document(self, *, dataset_id: str, document_id: str) -> None:
        self.request("DELETE", f"/datasets/{dataset_id}/documents/{document_id}", app="dataset")

    def list_documents(self, *, dataset_id: str, page: int = 1, limit: int = 20) -> dict[str, Any]:
        def _fetch() -> dict[str, Any]:
            data = self.request_json(
                "GET",
                f"/datasets/{dataset_id}/documents",
                app="dataset",
                params={"page": page, "limit": limit},
            )
            documents = data.get("data") or []
            return {
                "items": [
                    {
                        "dify_document_id": d.get("id"),
                        "name": d.get("name"),
                        "indexing_status": d.get("indexing_status"),
                        "word_count": d.get("word_count"),
                        "segment_count": d.get("segment_count"),
                    }
                    for d in documents
                ],
                "total": data.get("total", len(documents)),
                "page": page,
                "page_size": limit,
            }

        return _cached_json(
            documents_cache,
            f"documents:{dataset_id}:{page}:{limit}",
            float(settings.dify_documents_cache_seconds or 0),
            _fetch,
            label="文档列表",
        )

    def list_segments(
        self, *, dataset_id: str, document_id: str, page: int = 1, limit: int = 20
    ) -> dict[str, Any]:
        """分块查看（`GET /documents/{id}/chunks` 代理此接口）。"""

        def _fetch() -> dict[str, Any]:
            data = self.request_json(
                "GET",
                f"/datasets/{dataset_id}/documents/{document_id}/segments",
                app="dataset",
                params={"page": page, "limit": limit},
            )
            segments = data.get("data") or []
            return {
                "items": [
                    {
                        "segment_id": s.get("id"),
                        "position": s.get("position"),
                        "content": s.get("content"),
                        "word_count": s.get("word_count"),
                        "tokens": s.get("tokens"),
                    }
                    for s in segments
                ],
                "total": data.get("total", len(segments)),
                "page": page,
                "page_size": limit,
            }

        return _cached_json(
            segments_cache,
            f"segments:{dataset_id}:{document_id}:{page}:{limit}",
            float(settings.dify_segments_cache_seconds or 0),
            _fetch,
            label="分块列表",
        )

    def retrieval_test(
        self,
        *,
        dataset_id: str,
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.5,
        retrieval_mode: str = "hybrid",
        rerank_enabled: bool = True,
    ) -> list[RetrievedSegment]:
        """检索测试 / 引用来源预取 / 出题前的多路检索（`POST /datasets/{id}/retrieve`）。"""
        retrieval_model: dict[str, Any] = {
            "search_method": SEARCH_METHODS.get(retrieval_mode, "hybrid_search"),
            "reranking_enable": bool(rerank_enabled),
            "top_k": max(1, min(int(top_k or 5), 100)),
            "score_threshold_enabled": bool(score_threshold),
            "score_threshold": float(score_threshold or 0.0),
        }
        cache_key = (
            f"retrieve:{dataset_id}:{retrieval_model['search_method']}:"
            f"{retrieval_model['top_k']}:{retrieval_model['score_threshold']}:"
            f"{int(retrieval_model['reranking_enable'])}:{query}"
        )

        def _fetch() -> list[RetrievedSegment]:
            try:
                data = self.request_json(
                    "POST",
                    f"/datasets/{dataset_id}/retrieve",
                    app="dataset",
                    json_body={"query": query, "retrieval_model": retrieval_model},
                )
            except BizError as exc:
                # 新库还没有任何成功入库的文档时，Dify 的向量集合尚未建立，检索会报错；
                # 语义上等于「没有召回」，按空结果返回，避免问答/检索测试直接 50201。
                if _is_empty_collection_error(exc):
                    logger.info("Dify dataset %s 向量集合未建立，按空结果处理", dataset_id)
                    return []
                raise
            return segments_from_retrieval_records(data.get("records") or [])

        cached = _cached_json(
            retrieval_cache,
            cache_key,
            float(settings.dify_retrieval_cache_seconds or 0),
            _fetch,
            label="检索",
        )
        # 复制一份再返回：调用方会 sort/拼接，避免污染缓存里的对象
        return list(cached)

