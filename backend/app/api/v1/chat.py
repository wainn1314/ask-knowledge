"""问答 SSE 路由与对话历史（docs/05 §5-§6）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, get_kb_context
from app.core.constants import KbRole
from app.core.errors import envelope, not_found
from app.db.session import get_db
from app.models.chat import Conversation
from app.schemas.chat import (
    ChatStreamIn,
    ConversationDetailOut,
    ConversationOut,
    ConversationUpdateIn,
    MessageOut,
    SourceItem,
)
from app.services import chat_service

router = APIRouter(tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # Nginx：关闭缓冲，避免流式被攒批
}


def _sse_response(kb_id: str, user: CurrentUser, payload: ChatStreamIn) -> StreamingResponse:
    return StreamingResponse(
        chat_service.stream_chat(kb_id=kb_id, user=user, payload=payload),
        media_type=chat_service.SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )


@router.post("/kbs/{kb_id}/chat/stream", summary="问答（SSE 流式）")
def chat_stream(
    kb_id: str,
    payload: ChatStreamIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """先做一次权限校验（非 SSE 客户端也能拿到规范错误码），再返回事件流。"""
    get_kb_context(kb_id, db, user, KbRole.VIEWER.value)
    return _sse_response(kb_id, user, payload)


@router.get("/conversations", summary="会话列表")
def list_conversations(
    kb_id: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    items, total = chat_service.list_conversations(
        db, user, kb_id=kb_id, keyword=keyword, page=page, page_size=page_size
    )
    return envelope(
        0,
        "ok",
        {
            "items": [ConversationOut.model_validate(item).model_dump() for item in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


@router.get("/conversations/{conversation_id}", summary="会话详情")
def get_conversation(
    conversation_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    data = chat_service.get_conversation_detail(db, user, conversation_id)
    return envelope(0, "ok", ConversationDetailOut.model_validate(data).model_dump())


@router.get("/conversations/{conversation_id}/messages", summary="消息列表")
def list_messages(
    conversation_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    items, total = chat_service.list_messages(
        db, user, conversation_id, page=page, page_size=page_size
    )
    return envelope(
        0,
        "ok",
        {
            "items": [MessageOut.model_validate(msg).model_dump() for msg in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


@router.patch("/conversations/{conversation_id}", summary="重命名会话")
def rename_conversation(
    conversation_id: str,
    payload: ConversationUpdateIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return envelope(0, "ok", chat_service.rename_conversation(db, user, conversation_id, payload.title))


@router.delete("/conversations/{conversation_id}", summary="删除会话")
def delete_conversation(
    conversation_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    chat_service.delete_conversation(db, user, conversation_id)
    return envelope(0, "ok", {"id": conversation_id})


@router.post("/conversations/{conversation_id}/continue", summary="续聊（SSE）")
def continue_conversation(
    conversation_id: str,
    payload: ChatStreamIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
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
    kb_id = str(conv.kb_id)
    get_kb_context(kb_id, db, user, KbRole.VIEWER.value)
    payload.conversation_id = conversation_id
    return _sse_response(kb_id, user, payload)


__all__ = ["SourceItem", "router"]
