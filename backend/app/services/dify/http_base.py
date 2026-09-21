"""Dify HTTP 基础设施：鉴权头、请求封装、错误统一映射为 50201。

另外承担「知识库接口配额闸门」（详见 `services/dify/quota.py`）：Dify 对
`/datasets/*` 有每分钟请求上限，超限时会返回 **403**（不是 429），用户看到的是
「Dify 返回 403」。这里在发请求**之前**取号，并把 Dify 的 403 限流归一成 42901，
让上层能给出「稍后重试」这种可读提示、并用缓存兜底。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

import httpx

from app.core.config import settings
from app.core.constants import DifyApp
from app.core.errors import dify_error, too_many_requests
from app.core.logging import get_logger
from app.services.dify.quota import sync_quota_from_settings

logger = get_logger(__name__)

#: 走「知识库配额」的应用标识（Dify 侧 dataset API）
_KNOWLEDGE_APP = "dataset"
#: Dify 限流 body 的关键字（`... reached the knowledge base request rate limit ...`）
_RATE_LIMIT_HINTS = ("rate limit", "too many requests")


def _is_rate_limit_body(body: str) -> bool:
    text = (body or "").lower()
    return any(hint in text for hint in _RATE_LIMIT_HINTS)


#: Dify 错误 body 里的 pydantic 文档链接（对用户没用，去掉）
_URL_RE = re.compile(r"https?://\S+")
#: 用户可见的「Dify 原因」最大长度
_REASON_CHARS = 200


def _dify_reason(body: str) -> str:
    """从 Dify 的错误 body 里抠出 `message`，压成一行并去掉 pydantic 文档链接。

    Dify 的 400/422 形如::

        {"code":"invalid_param","message":"2 validation errors for HitTestingPayload ...","status":400}

    只回一句「Dify 返回 400」用户无从下手，这里把原因一起带出来。
    """
    text = body or ""
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        text = str(payload.get("message") or payload.get("error") or text)
    text = re.sub(r"\s+", " ", _URL_RE.sub(" ", text)).strip()
    return text[:_REASON_CHARS]


def _describe_error(status: int, method: str, path: str, body: str) -> str:
    """把「Dify 返回 400」补成「Dify 返回 400（POST /datasets/xx）：<Dify 的原因>」。"""
    head = f"Dify 返回 {status}（{method} {path}）"
    reason = _dify_reason(body)
    return f"{head}：{reason}" if reason else head


def _acquire_knowledge_slot(path: str) -> None:
    """取「知识库接口」名额；超过 ``dify_knowledge_wait_seconds`` 仍无空位则报 42901。"""
    quota = sync_quota_from_settings()
    if not quota.enabled or quota.acquire():
        return
    limit = quota.limit_per_minute
    raise too_many_requests(
        f"Dify 知识库接口调用已达频率上限（{limit} 次/分钟），请稍后重试",
        path=path,
        limit_per_minute=limit,
    )


class HttpDifyBase:
    """真实 Dify REST 客户端基类（httpx 同步客户端，与 SQLAlchemy 同步会话栈一致）。"""

    mode = "http"

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self._base_url = (base_url or settings.dify_base_url).rstrip("/")
        self._default_timeout = timeout or settings.dify_timeout_seconds
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=self._default_timeout,
            headers={"Accept": "application/json"},
        )

    # ── 鉴权 ────────────────────────────────────────────────────────────
    @staticmethod
    def app_key(app: str) -> str:
        mapping = {
            DifyApp.CHAT.value: settings.dify_app_api_key,
            DifyApp.QUIZ.value: settings.dify_quiz_workflow_api_key,
            DifyApp.QUIZ_GRADE.value: settings.dify_grade_workflow_api_key,
            "dataset": settings.dify_dataset_api_key,
        }
        key = mapping.get(app, "")
        if not key:
            raise dify_error(f"Dify {app} 的 API Key 未配置（.env）", app=app)
        return key

    def _headers(self, app: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.app_key(app)}"}
        if extra:
            headers.update(extra)
        return headers

    # ── 请求 ────────────────────────────────────────────────────────────
    def request(
        self,
        method: str,
        path: str,
        *,
        app: str,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        files: Any = None,
        data: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        if app == _KNOWLEDGE_APP:
            # 先取「知识库接口」名额：宁可在这里排队/明确报错，也不要把 Dify 配额打爆
            _acquire_knowledge_slot(path)
        try:
            resp = self._client.request(
                method,
                path,
                headers=self._headers(app),
                json=json_body,
                params=params,
                files=files,
                data=data,
                timeout=timeout or self._default_timeout,
            )
        except httpx.TimeoutException as exc:
            raise dify_error(f"Dify 请求超时: {method} {path}") from exc
        except httpx.HTTPError as exc:
            raise dify_error(f"Dify 网络异常: {method} {path} -> {exc}") from exc

        if resp.status_code >= 400:
            raw = resp.text or ""
            body = raw[:500]
            # 落盘日志放宽到 2000 字：Dify 的 400 是 pydantic 校验错误，前 500 字常常刚好被截断
            logger.warning("Dify %s %s -> %s %s", method, path, resp.status_code, raw[:2000])
            if resp.status_code == 403 and _is_rate_limit_body(body):
                # Dify 把「订阅侧知识库请求频率超限」也返回成 403：
                # 归一成 42901，让上层能提示「稍后重试」并用缓存兜底，
                # 而不是给用户一句没法处理的「Dify 返回 403」。
                raise too_many_requests(
                    "Dify 知识库接口已触发订阅频率上限，请约 1 分钟后重试"
                    + (
                        f"（本服务已把并发限制在 {limit} 次/分钟）"
                        if (limit := int(settings.dify_knowledge_rate_limit_per_minute or 0))
                        else ""
                    ),
                    path=path,
                    status=resp.status_code,
                    body=body,
                )
            # 400/422 等参数类错误：把 Dify 给的原因一起带出来，否则用户只看到「Dify 返回 400」
            raise dify_error(
                _describe_error(resp.status_code, method, path, raw),
                path=path,
                status=resp.status_code,
                body=body,
            )
        return resp

    def request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        resp = self.request(method, path, **kwargs)
        if not resp.content:
            return {}
        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise dify_error(f"Dify 响应不是合法 JSON: {path}") from exc

    # ── SSE ─────────────────────────────────────────────────────────────
    def iter_sse(
        self,
        path: str,
        *,
        app: str,
        json_body: dict[str, Any],
        timeout: float | None = None,
    ) -> Iterator[dict[str, Any]]:
        """逐行解析 Dify SSE（`data: {...}`），不缓冲整段。"""
        try:
            with self._client.stream(
                "POST",
                path,
                headers=self._headers(app, {"Accept": "text/event-stream"}),
                json=json_body,
                timeout=timeout or self._default_timeout,
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    raise dify_error(
                        f"Dify 流式接口返回 {resp.status_code}",
                        path=path,
                        body=resp.text[:500],
                    )
                for line in resp.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        payload = line[5:].strip()
                        if not payload or payload == "[DONE]":
                            continue
                        try:
                            yield json.loads(payload)
                        except json.JSONDecodeError:
                            logger.warning("忽略非 JSON 的 SSE 数据: %s", payload[:200])
        except httpx.TimeoutException as exc:
            raise dify_error(f"Dify 流式请求超时: {path}") from exc
        except httpx.HTTPError as exc:
            raise dify_error(f"Dify 流式网络异常: {path} -> {exc}") from exc

    def close(self) -> None:
        self._client.close()
