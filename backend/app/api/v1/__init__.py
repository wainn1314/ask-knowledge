"""v1 路由聚合（前缀 `/api/v1`）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, chat, documents, internal, kbs, moderation, quizzes, wrong_book

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(kbs.router)
api_router.include_router(documents.router)
api_router.include_router(chat.router)
api_router.include_router(quizzes.router)
api_router.include_router(wrong_book.router)
api_router.include_router(moderation.router)
api_router.include_router(internal.router)

__all__ = ["api_router"]
