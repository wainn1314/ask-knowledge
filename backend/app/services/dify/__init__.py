"""Dify 集成层（混合架构：Dify 做 RAG/LLM 引擎，后端做业务编排）。"""

from app.services.dify.client import (
    CHUNK_SEPARATOR,
    DifyClient,
    RetrievedSegment,
    build_dify_client,
    get_dify_client,
    reset_dify_client,
    set_dify_client,
)

__all__ = [
    "CHUNK_SEPARATOR",
    "DifyClient",
    "RetrievedSegment",
    "build_dify_client",
    "get_dify_client",
    "reset_dify_client",
    "set_dify_client",
]
