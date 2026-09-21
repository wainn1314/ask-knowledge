"""Dify「知识库」类接口的配额治理：滑动窗口限流 + 短 TTL 缓存。

真实故障（用户可见报错）：出题时前端弹出 **「Dify 返回 403」**。

抓包 + 实测（本仓库当前 Dify 订阅）确认：Dify 把**订阅侧的知识库接口频率上限**
返回成 403，第 11 次/分钟即触发：

    POST /datasets/{id}/retrieve -> 403
    {"code":"forbidden","message":"Sorry, you have reached the knowledge base request
     rate limit of your subscription.","status":403}

Dify 侧实现见 `api/controllers/service_api/wraps.py::cloud_edition_billing_rate_limit_check`，
它挂在**所有知识库类接口**上（retrieve / segments / documents / indexing-status / 建删改）。
而本后端有多处会高频打这些接口：

1. worker 入库轮询：每 1 秒查一次 `indexing-status`（一份 PDF 解析几分钟 = 数百次请求）；
2. 前端知识库详情页：有文档在处理中就每 2 秒拉一次（文档列表/分片预览）；
3. 出题前的多路检索：每个 query 一次 retrieve，命中不到还会放宽阈值重跑；
4. 问答（RAG 模式）与检索测试。

于是「上传 PDF → 轮询把配额吃干 → 出题检索 403」。这里提供三件基础设施把它压住：

- `KnowledgeQuota`：滚动窗口令牌，保证本进程发往 Dify 知识库接口的请求数不超过配置上限
  （拿不到令牌时最多等 `wait_seconds` 秒——等待通常比直接失败体验好）；
- `TTLCache`：同样的查询在 TTL 内只打一次 Dify（前端轮询会被自然合并）；
- 超限兜底由调用方决定：有缓存就返回「稍微过期的缓存」，没有才报 42901。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from app.core.config import settings

#: 便于测试注入的时钟 / 睡眠（生产用默认值）
DEFAULT_CLOCK: Callable[[], float] = time.monotonic
DEFAULT_SLEEPER: Callable[[float], None] = time.sleep


class KnowledgeQuota:
    """滚动窗口限流器：`limit_per_minute` 次 / `window_seconds` 秒。"""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = DEFAULT_CLOCK,
        sleeper: Callable[[float], None] = DEFAULT_SLEEPER,
    ) -> None:
        self._clock = clock
        self._sleep = sleeper
        self._lock = threading.Lock()
        self._stamps: deque[float] = deque()
        self._limit = 0
        self._window = 60.0
        self._wait = 0.0

    # ── 配置 ────────────────────────────────────────────────────────────
    def configure(
        self, *, limit_per_minute: int, wait_seconds: float, window_seconds: float = 60.0
    ) -> None:
        self._limit = max(0, int(limit_per_minute or 0))
        self._wait = max(0.0, float(wait_seconds or 0.0))
        self._window = max(1.0, float(window_seconds or 60.0))

    @property
    def enabled(self) -> bool:
        return self._limit > 0

    @property
    def limit_per_minute(self) -> int:
        return self._limit

    # ── 取号 ────────────────────────────────────────────────────────────
    def _forget(self, now: float) -> None:
        """丢掉滑出窗口的时间戳（调用时必须已持锁）。"""
        while self._stamps and now - self._stamps[0] >= self._window:
            self._stamps.popleft()

    def acquire(self) -> bool:
        """占一个名额：有空位立即返回 True；否则最多等 `wait_seconds` 秒。"""
        if not self.enabled:
            return True
        deadline = self._clock() + self._wait
        while True:
            now = self._clock()
            with self._lock:
                self._forget(now)
                if len(self._stamps) < self._limit:
                    self._stamps.append(now)
                    return True
                wait_for = self._window - (now - self._stamps[0])
            if wait_for > deadline - now:
                return False
            # 让出锁再睡：其它线程可以并行排队，而不是被一个 sleep 卡住
            self._sleep(max(wait_for, 0.01))

    def snapshot(self) -> dict[str, Any]:
        """当前窗口占用情况（日志 / 健康检查用）。"""
        with self._lock:
            self._forget(self._clock())
            return {
                "limit_per_minute": self._limit,
                "window_seconds": self._window,
                "in_window": len(self._stamps),
            }

    def reset(self) -> None:
        with self._lock:
            self._stamps.clear()


class TTLCache:
    """极简进程内 TTL 缓存；`get(allow_stale=True)` 可取「已过期但还有值」的旧数据。"""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = DEFAULT_CLOCK,
        max_items: int = 512,
    ) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._data: dict[str, tuple[float, Any]] = {}
        self._max_items = max(16, int(max_items))

    def get(self, key: str, *, allow_stale: bool = False) -> Any | None:
        with self._lock:
            item = self._data.get(key)
        if item is None:
            return None
        expire_at, value = item
        if allow_stale or self._clock() < expire_at:
            return value
        return None

    def set(self, key: str, value: Any, *, ttl: float) -> None:
        if ttl <= 0:
            return
        with self._lock:
            if len(self._data) >= self._max_items:
                now = self._clock()
                dead = [k for k, (expire_at, _) in self._data.items() if expire_at <= now]
                for victim in dead or [min(self._data, key=lambda k: self._data[k][0])]:
                    self._data.pop(victim, None)
            self._data[key] = (self._clock() + float(ttl), value)

    def invalidate(self, prefix: str = "") -> int:
        with self._lock:
            victims = [k for k in self._data if k.startswith(prefix)]
            for key in victims:
                self._data.pop(key, None)
            return len(victims)


# ── 全局单例 ────────────────────────────────────────────────────────────
#: 知识库类接口（`/datasets/*`）的每分钟闸门
knowledge_quota = KnowledgeQuota()
#: 检索结果缓存（出题 / 问答 / 检索测试共用，key 含 dataset + query + 参数）
retrieval_cache = TTLCache()
#: 分块列表缓存（预览 / 分块数回填）
segments_cache = TTLCache()
#: 文档列表缓存（Dify 侧文档列表）
documents_cache = TTLCache()
#: 「入库状态查询走哪个 path」的记忆（新版按 batch、老版按 document_id）
status_path_cache: dict[str, str] = {}


def sync_quota_from_settings() -> KnowledgeQuota:
    """把 `.env` 里的配额配置同步到单例（每次取号前刷一次，改动即时生效）。"""
    knowledge_quota.configure(
        limit_per_minute=settings.dify_knowledge_rate_limit_per_minute,
        wait_seconds=settings.dify_knowledge_wait_seconds,
    )
    return knowledge_quota


def reset_all() -> None:
    """清空闸门与全部缓存（测试 / 切换 Dify 工作区时调用）。"""
    knowledge_quota.reset()
    for cache in (retrieval_cache, segments_cache, documents_cache):
        cache.invalidate("")
    status_path_cache.clear()


__all__ = [
    "KnowledgeQuota",
    "TTLCache",
    "documents_cache",
    "knowledge_quota",
    "reset_all",
    "retrieval_cache",
    "segments_cache",
    "status_path_cache",
    "sync_quota_from_settings",
]
