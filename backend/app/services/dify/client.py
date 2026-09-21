"""Dify 客户端抽象：统一契约（Protocol）+ 工厂（http / mock 双实现）。

混合架构定位（见 docs/01 §1、docs/04）：
- **Dify 负责**：分块/嵌入/向量与关键词混合检索/Rerank/LLM 生成/出题与判分 Workflow；
- **本后端负责**：多租户与权限、上传与审核卡点、解析（MinerU）、调用编排、SSE 转发、
  归档（会话/消息/题目/错题本）、用量与审核日志。

因此这里定义的是「后端需要 Dify 做什么」的最小契约，两种实现必须一致：
1. `HttpDifyClient` —— 调真实 Dify REST（`http` 模式）；
2. `MockDifyClient` —— 离线内存实现（`mock` 模式），保证无 Dify 环境也能完整演示与单测。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Dify 分块分隔符（docs/04 §1：separator = "\n\n"）
CHUNK_SEPARATOR = "\n\n"
#: 引用片段下发前截断长度（docs/05 §5.2：截断 300 字）
SOURCE_SNIPPET_CHARS = 300


@dataclass(slots=True)
class RetrievedSegment:
    """检索命中的一段内容（Dify `retriever_resources` / `retrieval-test` 的归一化形态）。"""

    content: str
    score: float = 0.0
    document_id: str | None = None
    document_name: str | None = None
    segment_id: str | None = None
    position: int | None = None
    dataset_id: str | None = None

    def to_source(self, index: int, local_document_id: str | None = None) -> dict[str, Any]:
        """转本地 `sources` 事件结构（docs/05 §5.2 字段映射）。"""
        content = self.content or ""
        if len(content) > SOURCE_SNIPPET_CHARS:
            content = content[:SOURCE_SNIPPET_CHARS] + "…"
        return {
            "index": index,
            "dataset_id": self.dataset_id,
            "document_id": local_document_id or self.document_id,
            "dify_document_id": self.document_id,
            "document_name": self.document_name,
            "segment_id": self.segment_id,
            "segment_position": self.position,
            "score": round(float(self.score or 0), 4),
            "content": content,
        }


# ── 流式事件契约（chat_stream 逐条 yield） ────────────────────────────────
# {"type": "meta",    "conversation_id": str, "message_id": str}
# {"type": "sources", "items": [source, ...]}                  # 生成开始前下发
# {"type": "delta",   "text": str}                             # 增量文本
# {"type": "usage",   "prompt_tokens": int, "completion_tokens": int,
#                     "total_tokens": int, "latency_ms": int}
# {"type": "end",     "conversation_id": str, "message_id": str, "finish_reason": str}
# {"type": "error",   "code": int, "message": str}


@runtime_checkable
class DifyClient(Protocol):
    """Dify 能力契约。所有方法失败时抛 `app.core.errors.BizError(50201)`。"""

    mode: str

    # ── 健康检查 ──────────────────────────────────────────────────────
    def health(self) -> dict[str, Any]: ...

    # ── 知识库（dataset） ─────────────────────────────────────────────
    def create_dataset(self, *, name: str, description: str = "") -> dict[str, Any]: ...
    def delete_dataset(self, *, dataset_id: str) -> None: ...
    def create_document_by_text(
        self,
        *,
        dataset_id: str,
        name: str,
        text: str,
        chunk_size: int,
        chunk_overlap: int,
        separator: str = CHUNK_SEPARATOR,
    ) -> dict[str, Any]: ...
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
    ) -> dict[str, Any]: ...
    def get_indexing_status(
        self, *, dataset_id: str, document_id: str, batch: str | None = None
    ) -> str: ...
    def delete_document(self, *, dataset_id: str, document_id: str) -> None: ...
    def list_documents(self, *, dataset_id: str, page: int = 1, limit: int = 20) -> dict[str, Any]: ...
    def list_segments(
        self, *, dataset_id: str, document_id: str, page: int = 1, limit: int = 20
    ) -> dict[str, Any]: ...
    def retrieval_test(
        self,
        *,
        dataset_id: str,
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.5,
        retrieval_mode: str = "hybrid",
        rerank_enabled: bool = True,
    ) -> list[RetrievedSegment]: ...

    # ── 问答（App A：chat-qa） ────────────────────────────────────────
    def chat_stream(
        self,
        *,
        query: str,
        dataset_ids: list[str],
        user: str,
        conversation_id: str | None = None,
        top_k: int = 5,
        score_threshold: float = 0.5,
        answer_style: str = "教学式，简洁分点",
        retrieval_mode: str = "hybrid",
        rerank_enabled: bool = True,
    ) -> Iterator[dict[str, Any]]: ...

    def list_conversations(self, *, user: str, limit: int = 20) -> list[dict[str, Any]]: ...
    def delete_conversation(self, *, conversation_id: str, user: str) -> None: ...
    def list_messages(self, *, conversation_id: str, user: str, limit: int = 20) -> list[dict[str, Any]]: ...
    def upload_file(
        self, *, filename: str, content: bytes, mime_type: str, user: str
    ) -> dict[str, Any]: ...

    # ── Workflow（App B / C） ─────────────────────────────────────────
    def run_workflow(
        self, *, app: str, inputs: dict[str, Any], user: str, timeout: float | None = None
    ) -> dict[str, Any]: ...


# ── 工厂 ────────────────────────────────────────────────────────────────
_client: DifyClient | None = None


def build_dify_client(mode: str | None = None) -> DifyClient:
    resolved = (mode or settings.dify_mode or "mock").lower()
    if resolved == "http":
        from app.services.dify.http_client import HttpDifyClient

        return HttpDifyClient()
    if resolved == "mock":
        from app.services.dify.mock_client import MockDifyClient

        return MockDifyClient()
    raise ValueError(f"未知的 DIFY_MODE: {resolved}（应为 http 或 mock）")


def get_dify_client() -> DifyClient:
    """进程内单例。测试可用 `set_dify_client` 注入。"""
    global _client
    if _client is None:
        _client = build_dify_client()
        logger.info("Dify 客户端已初始化: mode=%s", _client.mode)
    return _client


def set_dify_client(client: DifyClient | None) -> None:
    global _client
    _client = client


def reset_dify_client() -> None:
    set_dify_client(None)
