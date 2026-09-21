"""Dify 知识库接口配额治理：闸门 + TTL 缓存 + 403 归一化（真实故障回归）。

真实故障：用户点「出题」时前端弹出 **「Dify 返回 403」**。

根因（已实测复现）：Dify 把「订阅侧知识库接口频率上限」返回成 **403**，本仓库当前
订阅为 10 次/分钟（第 11 次触发）：

    POST /datasets/{id}/retrieve -> 403
    {"code":"forbidden","message":"Sorry, you have reached the knowledge base request rate
     limit of your subscription.","status":403}

而 worker 的入库轮询原来每 **1 秒**打一次 indexing-status（同一配额），一份 PDF 解析几分钟
就能打出几百次请求 —— 配额被吃光后，出题前的检索必然 403。

本文件锁住四条不变量：
1. 闸门：窗口内超过上限的调用拿不到名额（等待，或按等待预算明确报错）；
2. Dify 的 403 限流被归一成 42901 可读提示，不再显示裸的「Dify 返回 403」；
3. 同一检索在 TTL 内只打一次 Dify（出题多路检索 / 重复点击都省配额）；
4. 真被限流时用上一次的检索结果兜底，出题不中断；轮询撞上限流也不会把文档判失败。
"""

from __future__ import annotations

import time

import httpx
import pytest

from app.core.config import settings
from app.core.constants import DifyIndexingStatus
from app.core.errors import TOO_MANY_REQUESTS, BizError
from app.db.session import session_scope
from app.models.documents import Document
from app.services.dify import quota as quota_module
from app.services.dify.http_client import HttpDifyClient
from app.services.dify.quota import KnowledgeQuota, TTLCache
from app.workers import handlers
from tests.conftest import upload_md

RATE_LIMIT_BODY = (
    '{"code":"forbidden","message":"Sorry, you have reached the knowledge base request '
    'rate limit of your subscription.","status":403}'
)

RETRIEVE_OK = {
    "query": {"content": "二叉树"},
    "records": [
        {
            "score": 0.87,
            "segment": {
                "id": "seg-1",
                "position": 1,
                "content": "前序遍历：根 → 左 → 右；中序遍历二叉搜索树可得递增序列。",
                "document": {"id": "doc-1", "name": "二叉树遍历.md", "dataset_id": "ds-1"},
            },
        }
    ],
}


def _client_with(handler) -> HttpDifyClient:  # noqa: ANN001 - 测试内部小工具
    """把真实客户端接到 httpx MockTransport 上（不发真实请求）。"""
    client = HttpDifyClient()
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url=settings.dify_base_url
    )
    return client


@pytest.fixture
def dify_http_env() -> None:
    """真实 HTTP 客户端所需的最小环境：有 dataset key、闸门与缓存都干净。"""
    original_key = settings.dify_dataset_api_key
    original_limit = settings.dify_knowledge_rate_limit_per_minute
    original_ttl = settings.dify_retrieval_cache_seconds
    settings.dify_dataset_api_key = "dataset-test"
    settings.dify_knowledge_rate_limit_per_minute = 0  # 只有闸门用例才打开
    quota_module.reset_all()
    try:
        yield
    finally:
        settings.dify_dataset_api_key = original_key
        settings.dify_knowledge_rate_limit_per_minute = original_limit
        settings.dify_retrieval_cache_seconds = original_ttl
        quota_module.reset_all()


def test_quota_waits_for_window_to_slide() -> None:
    """没名额时按「最早的调用滑出 60s 窗口」所需时间等待，而不是直接失败。"""
    now = [1000.0]
    slept: list[float] = []

    def _sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    quota = KnowledgeQuota(clock=lambda: now[0], sleeper=_sleep)
    quota.configure(limit_per_minute=2, wait_seconds=90)
    assert [quota.acquire() for _ in range(2)] == [True, True]
    assert quota.acquire() is True  # 等到第 1 次滑出窗口后拿到名额
    assert slept and slept[0] >= 60
    assert quota.snapshot()["in_window"] == 1


def test_quota_gives_up_when_wait_budget_is_too_small() -> None:
    """等待预算不够时返回 False，交给上层用缓存兜底 / 报友好错误。"""
    quota = KnowledgeQuota(clock=lambda: 0.0, sleeper=lambda _seconds: None)
    quota.configure(limit_per_minute=1, wait_seconds=1.0)
    assert quota.acquire() is True
    assert quota.acquire() is False


def test_ttl_cache_expires_but_keeps_stale_value() -> None:
    now = [0.0]
    cache = TTLCache(clock=lambda: now[0])
    cache.set("k", [1, 2], ttl=10)
    assert cache.get("k") == [1, 2]

    now[0] = 11.0
    assert cache.get("k") is None  # 新鲜值没了
    assert cache.get("k", allow_stale=True) == [1, 2]  # 降级兜底还拿得到


def test_dify_403_rate_limit_becomes_readable_error(dify_http_env: None) -> None:
    """Dify 的 403 限流 → 42901 可读提示（不再把裸状态码丢给用户）。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=RATE_LIMIT_BODY)

    client = _client_with(handler)
    with pytest.raises(BizError) as err:
        client.retrieval_test(dataset_id="ds-1", query="二叉树遍历")
    assert err.value.code == TOO_MANY_REQUESTS
    assert err.value.http_status == 429
    assert "频率" in err.value.message
    assert "Dify 返回" not in err.value.message


def test_other_403_is_still_reported_as_dify_error(dify_http_env: None) -> None:
    """非限流的 403（如 Key 越权）不能被误判成「稍后重试」。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text='{"code":"forbidden","message":"Access denied."}')

    client = _client_with(handler)
    with pytest.raises(BizError) as err:
        client.retrieval_test(dataset_id="ds-1", query="二叉树遍历")
    assert err.value.code == 50201
    assert "403" in err.value.message


def test_retrieval_result_is_cached_within_ttl(dify_http_env: None) -> None:
    """同一检索在 TTL 内只打一次 Dify（出题的多路检索/重复点击都被合并）。"""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=RETRIEVE_OK)

    client = _client_with(handler)
    first = client.retrieval_test(dataset_id="ds-1", query="二叉树遍历")
    second = client.retrieval_test(dataset_id="ds-1", query="二叉树遍历")
    client.retrieval_test(dataset_id="ds-1", query="其他主题")

    assert len(calls) == 2  # 相同 query 命中缓存；换 query 才再打 Dify
    assert [s.segment_id for s in first] == [s.segment_id for s in second] == ["seg-1"]
    assert first[0].score == pytest.approx(0.87)
    assert first[0].document_name == "二叉树遍历.md"


def test_retrieval_falls_back_to_last_result_when_throttled(dify_http_env: None) -> None:
    """真被限流时用上一次的检索结果兜底：出题不中断。"""
    calls: list[httpx.Request] = []
    throttled = [False]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if throttled[0]:
            return httpx.Response(403, text=RATE_LIMIT_BODY)
        return httpx.Response(200, json=RETRIEVE_OK)

    settings.dify_retrieval_cache_seconds = 0.05  # 让「新鲜缓存」很快过期
    client = _client_with(handler)
    warm = client.retrieval_test(dataset_id="ds-1", query="二叉树遍历")
    assert warm and len(calls) == 1

    throttled[0] = True
    time.sleep(0.08)
    again = client.retrieval_test(dataset_id="ds-1", query="二叉树遍历")
    assert [s.segment_id for s in again] == ["seg-1"]
    assert len(calls) == 2


def test_indexing_poll_survives_quota_error(
    client,  # noqa: ANN001 - conftest 夹具
    user: dict,
    kb: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """入库轮询撞上配额上限：等下一轮再问，绝不把文档判失败。"""

    class _StubClient:
        def __init__(self) -> None:
            self.calls = 0

        def get_indexing_status(self, **_kwargs: object) -> str:
            self.calls += 1
            if self.calls <= 2:
                raise BizError(TOO_MANY_REQUESTS, "Dify 知识库接口调用已达频率上限")
            return DifyIndexingStatus.COMPLETED.value

    doc_id = upload_md(client, user["headers"], kb["id"], filename="配额轮询.md")
    stub = _StubClient()
    monkeypatch.setattr(handlers, "get_dify_client", lambda: stub)
    monkeypatch.setattr(settings, "indexing_poll_interval_seconds", 0.01)

    with session_scope() as db:
        doc = db.get(Document, doc_id)
        assert doc is not None
        status = handlers._wait_indexing(db, doc, kb["dify_dataset_id"])

    assert status == DifyIndexingStatus.COMPLETED.value
    assert stub.calls == 3  # 两次被限流后继续问，第三次正常返回
