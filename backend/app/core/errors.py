"""统一错误码、业务异常与 FastAPI 异常处理器（错误码表见 docs/05 §1.1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import get_request_id
from app.core.logging import get_logger

logger = get_logger(__name__)

# code -> (默认消息, HTTP 状态码)
ERROR_CODES: dict[int, tuple[str, int]] = {
    0: ("ok", 200),
    40001: ("参数校验失败", 400),
    40101: ("未登录或登录已过期", 401),
    40301: ("无该知识库权限", 403),
    40401: ("资源不存在", 404),
    40901: ("资源冲突", 409),
    41301: ("文件超出大小限制", 413),
    41501: ("不支持的文件类型", 415),
    42901: ("请求过于频繁", 429),
    50001: ("服务内部错误", 500),
    50201: ("Dify 调用失败", 502),
    50202: ("文档解析失败", 502),
    50203: ("内容审核服务异常", 502),
    50301: ("内容未通过审核", 503),
}

#: 上游频率受限（Dify 知识库订阅配额；构造用 `too_many_requests`）
TOO_MANY_REQUESTS = 42901


def envelope(code: int = 0, message: str = "ok", data: Any = None) -> dict[str, Any]:
    """统一响应体：{code, message, data, request_id}。"""
    return {"code": code, "message": message, "data": data, "request_id": get_request_id()}


@dataclass
class BizError(Exception):
    """业务异常。"""

    code: int
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.message:
            self.message = ERROR_CODES.get(self.code, ("服务异常", 500))[0]
        Exception.__init__(self, self.message)

    @property
    def http_status(self) -> int:
        return ERROR_CODES.get(self.code, ("", 500))[1]

    def to_response(self) -> JSONResponse:
        data = {"code": self.code, "message": self.message, "request_id": get_request_id()}
        if self.detail:
            data["detail"] = self.detail
        return JSONResponse(status_code=self.http_status, content=data)


# ── 便捷构造 ───────────────────────────────────────────────────────────
def bad_request(message: str = "", **detail: Any) -> BizError:
    return BizError(40001, message, detail)


def unauthorized(message: str = "") -> BizError:
    return BizError(40101, message)


def forbidden(message: str = "") -> BizError:
    return BizError(40301, message)


def not_found(message: str = "资源不存在") -> BizError:
    return BizError(40401, message)


def conflict(message: str = "") -> BizError:
    return BizError(40901, message)


def payload_too_large(message: str = "") -> BizError:
    return BizError(41301, message)


def unsupported_type(message: str = "") -> BizError:
    return BizError(41501, message)


def too_many_requests(message: str = "", **detail: Any) -> BizError:
    """上游频率受限（Dify 知识库订阅配额，见 `services/dify/quota.py`）。"""
    return BizError(TOO_MANY_REQUESTS, message, detail)


def dify_error(message: str = "", **detail: Any) -> BizError:
    return BizError(50201, message, detail)


def parse_error(message: str = "", **detail: Any) -> BizError:
    return BizError(50202, message, detail)


def audit_error(message: str = "", **detail: Any) -> BizError:
    return BizError(50203, message, detail)


def audit_blocked(message: str = "", **detail: Any) -> BizError:
    return BizError(50301, message, detail)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizError)
    async def _biz(_: Request, exc: BizError) -> JSONResponse:
        logger.warning("BizError code=%s msg=%s", exc.code, exc.message)
        return exc.to_response()

    @app.exception_handler(RequestValidationError)
    async def _validate(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "code": 40001,
                "message": "参数校验失败",
                "detail": {"errors": exc.errors()},
                "request_id": get_request_id(),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = 40401 if exc.status_code == 404 else (40101 if exc.status_code == 401 else 50001)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": code,
                "message": str(exc.detail),
                "request_id": get_request_id(),
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理异常: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "code": 50001,
                "message": "服务内部错误",
                "request_id": get_request_id(),
            },
        )
