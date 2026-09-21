"""Dify 问答（App A：chat-qa）相关接口 —— 真实 REST 调用 + SSE 解析。

两种编排方式由 `DIFY_CHAT_MODE` 决定：

- `app`（默认）：把 `kb_ids`（各知识库自己的 Dify dataset id）作为 inputs 交给 Dify 应用，
  由应用内的知识检索节点召回；要求应用侧把检索节点绑定到该变量。
- `rag`：后端先调 `POST /datasets/{id}/retrieve` 在**所选知识库自己的 dataset** 内召回，
  再把命中资料注入查询交给应用生成答案，引用来源直接用后端检索结果。
  实测 Dify `advanced-chat` 应用会忽略未知的 `inputs.kb_ids`（既不报错也不检索），
  这类应用必须用 `rag` 模式才能保证「按知识库检索」真实生效。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from app.core.config import settings
from app.core.constants import DifyApp
from app.core.errors import BizError
from app.services.dify.client import RetrievedSegment
from app.services.dify.mapping import (
    format_material,
    segments_from_retriever_resources,
    to_sources,
)

#: `rag` 模式注入模板（占位符：{style} 回答风格 / {context} 资料 / {query} 用户问题）
RAG_PROMPT_TEMPLATE = """你是学习伙伴。请严格依据下面的【资料】回答【问题】。
要求：
1. 只使用资料中的信息，不要编造资料之外的内容；
2. 引用资料时在句末标注编号，例如 [1]；
3. 资料不足以回答时，直接说明「资料中没有相关内容」，不要强行作答。
回答风格：{style}

【资料】
{context}

【问题】
{query}"""

#: 检索为空时的标准回答（与 MockDifyClient 行为一致）
NO_CONTEXT_ANSWER = "知识库中未找到与问题相关的内容，请换一种问法或补充资料。"
#: 无资料回答的分片长度（保持前端逐字渲染体验）
_STREAM_PIECE_CHARS = 24


def _stream_pieces(text: str, size: int = _STREAM_PIECE_CHARS) -> list[str]:
    """把整段文本切成小片，模拟流式下发。"""
    return [text[index : index + size] for index in range(0, len(text), size)]


def _format_context(sources: list[dict[str, Any]]) -> str:
    """把检索结果拼成注入大模型的资料块（带 [n] 编号，便于模型引用）。"""
    return format_material(sources)


class ChatClientMixin:
    """Mixin：依赖 HttpDifyBase 提供的 request / request_json / iter_sse。"""

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
    ) -> Iterator[dict[str, Any]]:
        """`POST /chat-messages`（streaming）→ 归一化事件流。

        归一化事件契约见 `client.py` 顶部注释。`dataset_ids` 由后端按权限计算后传入
        （docs/04 §6），前端永不接触 dataset_id。
        """
        dataset_ids = [dataset_id for dataset_id in dataset_ids if dataset_id]
        rag = (settings.dify_chat_mode or "app").strip().lower() == "rag"
        prompt_query = query
        emit_dify_sources = True

        if rag:
            # Dify 应用不接受按库检索参数：先按所选知识库检索，再把资料注入查询
            try:
                segments = self._retrieve_by_datasets(
                    query=query,
                    dataset_ids=dataset_ids,
                    top_k=top_k,
                    score_threshold=score_threshold,
                    retrieval_mode=retrieval_mode,
                    rerank_enabled=rerank_enabled,
                )
            except BizError as exc:
                yield {"type": "error", "code": exc.code, "message": f"知识库检索失败：{exc.message}"}
                return
            if not segments:
                # 未召回任何资料：不调用大模型，直接给标准提示（避免无依据作答）
                for piece in _stream_pieces(NO_CONTEXT_ANSWER):
                    yield {"type": "delta", "text": piece}
                yield {
                    "type": "usage",
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "latency_ms": 0,
                }
                yield {
                    "type": "end",
                    "conversation_id": conversation_id,
                    "message_id": None,
                    "finish_reason": "no_context",
                }
                return
            sources = to_sources(segments)
            yield {"type": "sources", "items": sources, "prefetch": True}
            prompt_query = RAG_PROMPT_TEMPLATE.format(
                style=answer_style,
                context=_format_context(sources),
                query=query,
            )
            emit_dify_sources = False  # 引用来源已用后端检索结果下发

        body: dict[str, Any] = {
            "inputs": {
                "kb_ids": list(dataset_ids),
                "top_k": top_k,
                "score_threshold": score_threshold,
                "answer_style": answer_style,
            },
            "query": prompt_query,
            "response_mode": "streaming",
            "user": user,
            "files": [],
            "auto_generate_name": False,
        }
        if conversation_id:
            body["conversation_id"] = conversation_id

        started = time.perf_counter()
        conv_id = conversation_id
        message_id: str | None = None
        finish_reason = "stop"
        meta_sent = False

        for evt in self.iter_sse("/chat-messages", app=DifyApp.CHAT.value, json_body=body):
            event = evt.get("event")
            if event in ("message", "agent_message"):
                conv_id = evt.get("conversation_id") or conv_id
                message_id = evt.get("message_id") or message_id
                if not meta_sent and conv_id:
                    # 与 MockDifyClient 保持同一契约：首帧 meta
                    yield {"type": "meta", "conversation_id": conv_id, "message_id": message_id}
                    meta_sent = True
                delta = evt.get("answer") or ""
                if delta:
                    yield {"type": "delta", "text": delta}
            elif event == "message_end":
                conv_id = evt.get("conversation_id") or conv_id
                message_id = evt.get("message_id") or message_id
                metadata = evt.get("metadata") or {}
                usage = metadata.get("usage") or {}
                segments: list[RetrievedSegment] = segments_from_retriever_resources(
                    evt.get("retriever_resources")
                )
                if segments and emit_dify_sources:
                    yield {"type": "sources", "items": to_sources(segments), "prefetch": False}
                yield {
                    "type": "usage",
                    "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0),
                    "total_tokens": int(
                        usage.get("total_tokens")
                        or (int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0))
                    ),
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                }
            elif event == "error":
                yield {
                    "type": "error",
                    "code": 50201,
                    "message": str(evt.get("message") or "Dify 问答失败"),
                }
                return
            elif event == "message_replace":
                # 内容审核触发的整体替换（Dify 会在审核命中时下发）
                yield {"type": "replace", "text": str(evt.get("answer") or "")}
            elif event == "workflow_finished":
                data = evt.get("data") or {}
                finish_reason = str(data.get("status") or "stop")

        yield {
            "type": "end",
            "conversation_id": conv_id,
            "message_id": message_id,
            "finish_reason": finish_reason,
        }

    def _retrieve_by_datasets(
        self,
        *,
        query: str,
        dataset_ids: list[str],
        top_k: int,
        score_threshold: float,
        retrieval_mode: str = "hybrid",
        rerank_enabled: bool = True,
    ) -> list[RetrievedSegment]:
        """在多个 dataset 内检索并合并排序，取相似度最高的 top_k 条（`rag` 模式用）。"""
        merged: list[RetrievedSegment] = []
        for dataset_id in dataset_ids:
            if not dataset_id:
                continue
            merged.extend(
                self.retrieval_test(
                    dataset_id=dataset_id,
                    query=query,
                    top_k=top_k,
                    score_threshold=score_threshold,
                    retrieval_mode=retrieval_mode,
                    rerank_enabled=rerank_enabled,
                )
            )
        merged.sort(key=lambda segment: float(segment.score or 0), reverse=True)
        return merged[: max(1, int(top_k or 5))]

    def list_conversations(self, *, user: str, limit: int = 20) -> list[dict[str, Any]]:
        data = self.request_json(
            "GET", "/conversations", app=DifyApp.CHAT.value, params={"user": user, "limit": limit}
        )
        return [
            {
                "dify_conversation_id": c.get("id"),
                "name": c.get("name"),
                "created_at": c.get("created_at"),
                "updated_at": c.get("updated_at"),
            }
            for c in (data.get("data") or [])
        ]

    def delete_conversation(self, *, conversation_id: str, user: str) -> None:
        self.request(
            "DELETE",
            f"/conversations/{conversation_id}",
            app=DifyApp.CHAT.value,
            json_body={"user": user},
        )

    def list_messages(
        self, *, conversation_id: str, user: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        data = self.request_json(
            "GET",
            "/messages",
            app=DifyApp.CHAT.value,
            params={"conversation_id": conversation_id, "user": user, "limit": limit},
        )
        return [
            {
                "dify_message_id": m.get("id"),
                "role": "user" if m.get("query") else "assistant",
                "content": m.get("answer") or m.get("query") or "",
                "sources": m.get("retriever_resources") or [],
                "created_at": m.get("created_at"),
                "error": m.get("error"),
            }
            for m in (data.get("data") or [])
        ]

    def upload_file(
        self, *, filename: str, content: bytes, mime_type: str, user: str
    ) -> dict[str, Any]:
        """备用：需要 file 变量时使用（docs/05 §10 最后一行）。"""
        data = self.request_json(
            "POST",
            "/files/upload",
            app=DifyApp.CHAT.value,
            data={"user": user},
            files={"file": (filename, content, mime_type)},
        )
        return {"id": data.get("id"), "name": data.get("name"), "size": data.get("size")}
