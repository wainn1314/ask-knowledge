"""问答服务：会话生命周期 + SSE 事件流（docs/05 §5-§6）。

⚠️ 注意：FastAPI 的 `Depends(get_db)` 会在**响应开始前**关闭会话，
因此流式接口内部自行用 `session_scope()` 持有事务（长连接，直到流结束/客户端断开）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_kb_context
from app.core.constants import KbRole, UsageScene
from app.core.errors import bad_request, not_found
from app.core.logging import get_logger
from app.db.session import session_scope
from app.models.chat import Conversation, Message
from app.services.dify import get_dify_client
from app.services.moderation import get_moderator
from app.services.support import dify_document_map, kb_name_map, paginate, record_usage

logger = get_logger(__name__)

SSE_MEDIA_TYPE = "text/event-stream"


def sse(event: str, data: dict[str, Any]) -> str:
    """SSE 帧：`event: x\\ndata: {...}\\n\\n`（不引入额外依赖）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── 会话 ────────────────────────────────────────────────────────────────
def resolve_conversation(
    db: Session,
    *,
    tenant_id: str,
    user: CurrentUser,
    kb_id: str,
    conversation_id: str | None,
    title_seed: str,
) -> Conversation:
    """续聊校验 / 新建会话。"""
    if conversation_id:
        conv = db.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user.id,
                Conversation.is_deleted.is_(False),
            )
        )
        if conv is None:
            raise not_found("会话不存在")
        if str(conv.kb_id) != kb_id:
            raise bad_request("会话不属于该知识库")
        return conv
    conv = Conversation(
        tenant_id=tenant_id,
        user_id=user.id,
        kb_id=kb_id,
        title=(title_seed or "新会话")[:50],
        message_count=0,
    )
    db.add(conv)
    db.flush()
    return conv


def add_message(
    db: Session,
    conv: Conversation,
    *,
    role: str,
    content: str,
    dify_message_id: str | None = None,
    sources: list[dict[str, Any]] | None = None,
    audit_status: str = "pass",
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    latency_ms: int | None = None,
    finish_reason: str | None = None,
) -> Message:
    """归档消息并刷新会话最后活跃时间。"""
    from app.db.base import utcnow

    msg = Message(
        tenant_id=conv.tenant_id,
        conversation_id=conv.id,
        dify_message_id=dify_message_id,
        role=role,
        content=content,
        sources=sources,
        audit_status=audit_status,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        finish_reason=finish_reason,
    )
    db.add(msg)
    conv.message_count = int(conv.message_count or 0) + 1
    conv.last_message_at = utcnow()
    if role == "user" and (not conv.title or conv.title == "新会话"):
        conv.title = (content or "新会话")[:50]
    db.flush()
    return msg


def _conv_dict(conv: Conversation, kb_name: str | None = None) -> dict[str, Any]:
    return {
        "id": str(conv.id),
        "kb_id": str(conv.kb_id),
        "title": conv.title,
        "message_count": int(conv.message_count or 0),
        "last_message_at": conv.last_message_at,
        "dify_conversation_id": conv.dify_conversation_id,
        "created_at": conv.created_at,
        "kb_name": kb_name,
    }


def list_conversations(
    db: Session,
    user: CurrentUser,
    *,
    kb_id: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    stmt = select(Conversation).where(
        Conversation.tenant_id == user.tenant_id,
        Conversation.user_id == user.id,
        Conversation.is_deleted.is_(False),
    )
    if kb_id:
        stmt = stmt.where(Conversation.kb_id == kb_id)
    if keyword:
        stmt = stmt.where(Conversation.title.contains(keyword))
    stmt = stmt.order_by(Conversation.last_message_at.desc().nullslast())
    rows, total = paginate(db, stmt, page=page, page_size=page_size)
    names = kb_name_map(db, [str(c.kb_id) for c in rows])
    return [_conv_dict(conv, names.get(str(conv.kb_id))) for conv in rows], total


def get_conversation_detail(db: Session, user: CurrentUser, conversation_id: str) -> dict[str, Any]:
    """详情：`dify_available=false` 时前端隐藏「继续追问」（Dify 侧会话已失联）。"""
    conv = _user_conversation(db, user, conversation_id)
    available = False
    if conv.dify_conversation_id:
        try:
            conversations = get_dify_client().list_conversations(user=user.dify_user, limit=100)
            available = any(
                str(c.get("id") or c.get("dify_conversation_id")) == conv.dify_conversation_id
                for c in conversations
            )
        except Exception as exc:
            logger.warning("校验 Dify 会话失败 conv=%s err=%s", conv.id, exc)
    names = kb_name_map(db, [str(conv.kb_id)])
    return {**_conv_dict(conv, names.get(str(conv.kb_id))), "dify_available": available}


def list_messages(
    db: Session, user: CurrentUser, conversation_id: str, *, page: int, page_size: int
) -> tuple[list[Message], int]:
    """消息列表：倒序分页（前端反转后正序渲染）。"""
    conv = _user_conversation(db, user, conversation_id)
    stmt = (
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc(), Message.id.desc())
    )
    return paginate(db, stmt, page=page, page_size=page_size)


def rename_conversation(
    db: Session, user: CurrentUser, conversation_id: str, title: str
) -> dict[str, Any]:
    conv = _user_conversation(db, user, conversation_id)
    conv.title = title
    db.flush()
    return {"id": str(conv.id), "title": conv.title}


def delete_conversation(db: Session, user: CurrentUser, conversation_id: str) -> None:
    """删除：先删 Dify 会话（失败不阻塞）再软删本地。"""
    conv = _user_conversation(db, user, conversation_id)
    if conv.dify_conversation_id:
        try:
            get_dify_client().delete_conversation(
                conversation_id=conv.dify_conversation_id, user=user.dify_user
            )
        except Exception as exc:
            logger.warning("删除 Dify 会话失败 conv=%s err=%s", conv.id, exc)
    conv.mark_deleted()
    db.flush()


def _user_conversation(db: Session, user: CurrentUser, conversation_id: str) -> Conversation:
    conv = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
            Conversation.tenant_id == user.tenant_id,
            Conversation.is_deleted.is_(False),
        )
    )
    if conv is None:
        raise not_found("会话不存在")
    return conv

def _localize_sources(db: Session, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 Dify 引用映射为本地 sources：补齐本地 document_id，且不下发任何 Dify ID。"""
    dify_ids = [str(i.get("document_id") or "") for i in items]
    mapping = dify_document_map(db, "", dify_ids)
    out: list[dict[str, Any]] = []
    for item in items:
        dify_doc_id = str(item.get("document_id") or "")
        out.append(
            {
                "index": int(item.get("index") or len(out) + 1),
                "document_id": mapping.get(dify_doc_id, dify_doc_id or None),
                "document_name": item.get("document_name"),
                "segment_id": item.get("segment_id"),
                "score": round(float(item.get("score") or 0.0), 4),
                "content": item.get("content") or "",
                "position": item.get("segment_position"),
            }
        )
    return out


def stream_chat(*, kb_id: str, user: CurrentUser, payload: Any) -> Iterator[str]:
    """问答 SSE 生成器入口：事件序列 meta → sources → message* → usage → done。

    - 输入审核命中：`moderation` + `done`（不调用 Dify）
    - 客户端中断：已生成内容落库，`finish_reason=client_abort`
    - 输出审核命中：流已结束，补发 `moderation`（前端提示，不重写已渲染文本）
    """
    with session_scope() as db:
        ctx = get_kb_context(kb_id, db, user, KbRole.VIEWER.value)
        yield from _stream_events(db, ctx, user, payload)


def _stream_events(db: Session, ctx: Any, user: CurrentUser, payload: Any) -> Iterator[str]:
    from app.core.constants import AuditScene

    if not ctx.dataset_id:
        raise bad_request("该知识库尚未关联 Dify dataset，无法问答")
    moderator = get_moderator()
    conv = resolve_conversation(
        db,
        tenant_id=ctx.kb.tenant_id,
        user=user,
        kb_id=ctx.kb_id,
        conversation_id=payload.conversation_id,
        title_seed=payload.query,
    )
    input_verdict = moderator.check_text(
        payload.query,
        AuditScene.QUERY.value,
        tenant_id=ctx.kb.tenant_id,
        object_type="message",
        session=db,
    )
    user_msg = add_message(
        db, conv, role="user", content=payload.query, audit_status=input_verdict.audit_status
    )
    if input_verdict.blocked:
        yield sse(
            "meta",
            {
                "conversation_id": str(conv.id),
                "message_id": str(user_msg.id),
                "kb_id": ctx.kb_id,
            },
        )
        yield sse(
            "moderation",
            {"scene": "input", "flagged": True, "message": "提问包含违规内容，已被拦截"},
        )
        yield sse(
            "done",
            {
                "message_id": str(user_msg.id),
                "conversation_id": str(conv.id),
                "sources_count": 0,
                "finish_reason": "moderation_blocked",
            },
        )
        return

    assistant = add_message(db, conv, role="assistant", content="")
    yield sse(
        "meta",
        {"conversation_id": str(conv.id), "message_id": str(assistant.id), "kb_id": ctx.kb_id},
    )

    buffer: list[str] = []
    sources: list[dict[str, Any]] = []
    usage: dict[str, Any] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "latency_ms": 0,
    }
    error_event: dict[str, Any] | None = None
    archived = False
    verdict: Any | None = None
    try:
        events = get_dify_client().chat_stream(
            query=payload.query,
            dataset_ids=[ctx.dataset_id],
            user=user.dify_user,
            conversation_id=conv.dify_conversation_id,
            top_k=payload.top_k or ctx.kb.top_k,
            score_threshold=(
                payload.score_threshold
                if payload.score_threshold is not None
                else float(ctx.kb.score_threshold or 0.0)
            ),
            answer_style=payload.answer_style or "教学式，简洁分点",
            retrieval_mode=ctx.kb.retrieval_mode or "hybrid",
            rerank_enabled=bool(ctx.kb.rerank_enabled),
        )
        for event in events:
            etype = event.get("type")
            if etype == "meta":
                conv.dify_conversation_id = (
                    event.get("conversation_id") or conv.dify_conversation_id
                )
                assistant.dify_message_id = event.get("message_id")
            elif etype == "sources":
                sources = _localize_sources(db, event.get("items") or [])
                if sources:
                    yield sse("sources", {"items": sources})
            elif etype in {"delta", "replace"}:
                text = str(event.get("text") or event.get("delta") or "")
                if text:
                    buffer.append(text)
                    yield sse("message", {"delta": text})
            elif etype == "usage":
                usage = {
                    "prompt_tokens": int(event.get("prompt_tokens") or 0),
                    "completion_tokens": int(event.get("completion_tokens") or 0),
                    "total_tokens": int(event.get("total_tokens") or 0),
                    "latency_ms": int(event.get("latency_ms") or 0),
                }
            elif etype == "error":
                error_event = {
                    "code": int(event.get("code") or 50201),
                    "message": str(event.get("message") or "Dify 调用失败"),
                }
                break
            elif etype == "end":
                conv.dify_conversation_id = event.get("conversation_id") or conv.dify_conversation_id
                assistant.dify_message_id = event.get("message_id") or assistant.dify_message_id
                assistant.finish_reason = str(event.get("finish_reason") or "stop")
                break
        verdict = _archive_answer(
            db, ctx, assistant, "".join(buffer), sources, usage, error_event, user=user
        )
        archived = True
    finally:
        if not archived:
            partial = "".join(buffer)
            assistant.content = partial
            assistant.finish_reason = assistant.finish_reason or "client_abort"
            db.flush()
            logger.info("问答流被中断 conv=%s chars=%s", conv.id, len(partial))

    if error_event is not None:
        yield sse("error", error_event)
        yield sse(
            "done",
            {
                "message_id": str(assistant.id),
                "conversation_id": str(conv.id),
                "sources_count": len(sources),
                "finish_reason": "error",
            },
        )
        return
    yield sse("usage", usage)
    if verdict is not None and verdict.result in {"block", "review"}:
        yield sse(
            "moderation",
            {
                "scene": "output",
                "flagged": True,
                "message": "本次回答触发内容审核，请谨慎参考",
            },
        )
    yield sse(
        "done",
        {
            "message_id": str(assistant.id),
            "conversation_id": str(conv.id),
            "sources_count": len(sources),
            "finish_reason": assistant.finish_reason or "stop",
        },
    )


def _archive_answer(
    db: Session,
    ctx: Any,
    assistant: Message,
    answer: str,
    sources: list[dict[str, Any]],
    usage: dict[str, Any],
    error_event: dict[str, Any] | None,
    *,
    user: CurrentUser,
) -> Any:
    """结束时归档回答：写内容/引用/用量 + 回答审核（scene=answer）；返回审核结论。"""
    from app.core.constants import AuditScene

    assistant.content = answer
    assistant.sources = sources or None
    assistant.prompt_tokens = int(usage.get("prompt_tokens") or 0)
    assistant.completion_tokens = int(usage.get("completion_tokens") or 0)
    assistant.latency_ms = int(usage.get("latency_ms") or 0)
    if error_event is not None:
        assistant.finish_reason = "error"
    verdict = get_moderator().check_text(
        answer,
        AuditScene.ANSWER.value,
        tenant_id=ctx.kb.tenant_id,
        object_type="message",
        object_id=str(assistant.id),
        session=db,
    )
    assistant.audit_status = verdict.audit_status
    db.flush()
    record_usage(
        db,
        tenant_id=ctx.kb.tenant_id,
        user_id=user.id,
        scene=UsageScene.CHAT.value,
        ref_id=str(assistant.id),
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        latency_ms=int(usage.get("latency_ms") or 0),
    )
    return verdict

