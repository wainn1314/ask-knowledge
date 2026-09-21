"""FastAPI 应用入口：中间件、异常处理、路由挂载、内联 worker 启动。

启动：`uvicorn app.main:app --reload --port 8000`（工作目录 = backend/）
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import settings
from app.core.context import new_request_id, set_request_id
from app.core.errors import envelope, register_exception_handlers
from app.core.logging import get_logger
from app.db.engine import init_db
from app.services.dify import get_dify_client
from app.workers.runner import start_inline_worker, stop as stop_workers

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """启动：建表 + （可选）内联 worker；关闭：停止 worker。"""
    init_db()
    logger.info(
        "应用启动 env=%s dify_mode=%s parser_mode=%s audit=%s",
        settings.app_env,
        settings.dify_mode,
        settings.parser_mode,
        settings.audit_provider,
    )
    if settings.worker_inline:
        start_inline_worker()
    try:
        yield
    finally:
        stop_workers()
        logger.info("应用已关闭")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Ask Knowledge AI Learning Partner —— 混合架构后端（Dify 做 RAG/LLM 引擎）",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # MVP 放开；生产改成前端域名白名单
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )

    @app.middleware("http")
    async def _request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-Id") or new_request_id()
        set_request_id(request_id)
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/health", tags=["ops"], summary="健康检查")
    def health() -> dict:
        """探活 + 关键依赖状态（Dify 不可达时 status=degraded，不阻塞探活）。"""
        dify_info: dict | None = None
        status = "ok"
        try:
            dify_info = get_dify_client().health()
            if not dify_info.get("healthy", True):
                status = "degraded"
        except Exception as exc:  # 50201 等
            status = "degraded"
            dify_info = {"healthy": False, "error": str(exc)[:200]}
        return envelope(
            0,
            status,
            {
                "status": status,
                "app_env": settings.app_env,
                "dify_mode": settings.dify_mode,
                "dify": dify_info,
                "parser_mode": settings.parser_mode,
                "dify_parse_extensions": sorted(settings.dify_parse_ext_set),
                "audit_provider": settings.audit_provider,
                "worker_inline": settings.worker_inline,
                "single_kb_mode": settings.single_kb_mode,
                "default_kb_dataset_id": settings.default_kb_dataset_id or None,
            },
        )

    return app


app = create_app()
