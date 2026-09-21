"""审核统一入口：策略路由 + 结果缓存 + audit_log 落库。

调用方式（服务层/回调接口统一走这里）：

    verdict = moderator.check_text(text, AuditScene.UPLOAD, tenant_id=..., session=db, object_type="document")
    verdict.raise_if_blocked()      # 命中或 fail-closed 异常时抛 50301 / 50203

- provider=local ：内置词库规则（零依赖，默认）
- provider=aliyun：阿里云内容安全；SDK 缺失/凭据缺失时自动回退 local（记为 fallback）
- 缓存：同文本 + 同场景在 `AUDIT_CACHE_MINUTES` 内复用结论，避免重复送审
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import AuditResult, AuditScene
from app.core.errors import audit_blocked, audit_error
from app.core.logging import get_logger
from app.models.ops import AuditLog
from app.services.moderation import rules
from app.services.moderation.aliyun_green import get_aliyun_green

logger = get_logger(__name__)

_CACHE_MAX = 2000


@dataclass(slots=True)
class Verdict:
    """一次审核结论。"""

    result: str  # pass / block / review / error
    label: str = rules.LABEL_SAFE
    words: list[str] = field(default_factory=list)
    provider: str = "local"
    scene: str = ""
    request_id: str | None = None
    excerpt: str | None = None
    cached: bool = False
    raw: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        return self.result == AuditResult.PASS.value

    @property
    def blocked(self) -> bool:
        return self.result == AuditResult.BLOCK.value

    @property
    def needs_review(self) -> bool:
        return self.result == AuditResult.REVIEW.value

    @property
    def audit_status(self) -> str:
        """映射到 document.audit_status / message.audit_status。"""
        return {
            AuditResult.PASS.value: "pass",
            AuditResult.BLOCK.value: "block",
            AuditResult.REVIEW.value: "review",
        }.get(self.result, "review")

    def raise_if_blocked(self, message: str = "") -> None:
        if self.blocked:
            raise audit_blocked(
                message or f"内容未通过审核（{self.label}）",
                label=self.label,
                words=self.words,
                provider=self.provider,
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "label": self.label,
            "words": self.words,
            "provider": self.provider,
            "scene": self.scene,
            "cached": self.cached,
        }


class Moderator:
    """审核执行器（单例，线程内共享缓存）。"""

    def __init__(
        self,
        *,
        provider: str | None = None,
        enabled: bool | None = None,
        cache_minutes: int | None = None,
    ) -> None:
        self.provider = (provider or settings.audit_provider or "local").lower()
        self.enabled = settings.audit_enabled if enabled is None else enabled
        self.cache_minutes = settings.audit_cache_minutes if cache_minutes is None else cache_minutes
        self._cache: dict[str, tuple[float, Verdict]] = {}

    # ── 对外入口 ──────────────────────────────────────────────────────
    def check_text(
        self,
        text: str,
        scene: str,
        *,
        tenant_id: str | None = None,
        object_type: str = "text",
        object_id: str | None = None,
        session: Session | None = None,
        strict: bool | None = None,
    ) -> Verdict:
        if not self.enabled:
            return Verdict(result=AuditResult.PASS.value, provider="disabled", scene=scene)

        key = self._cache_key(text, scene)
        cached = self._cache.get(key)
        if cached and time.monotonic() < cached[0]:
            verdict = cached[1]
            verdict.cached = True
            return verdict

        verdict = self._run(text, scene, strict=strict)
        self._remember(key, verdict, text)
        self._persist(session, verdict, tenant_id=tenant_id, object_type=object_type, object_id=object_id)
        return verdict

    def check_image(
        self,
        image_url: str,
        scene: str = AuditScene.UPLOAD.value,
        *,
        tenant_id: str | None = None,
        object_type: str = "image",
        object_id: str | None = None,
        session: Session | None = None,
    ) -> Verdict:
        """图片审核：provider=aliyun 走图片接口；provider=local 时仅记录待复核（无法识别像素）。"""
        if not self.enabled:
            return Verdict(result=AuditResult.PASS.value, provider="disabled", scene=scene)
        if self.provider != "aliyun":
            verdict = Verdict(
                result=AuditResult.REVIEW.value,
                label="image-review",
                provider="local",
                scene=scene,
                excerpt=image_url,
                raw={"note": "本地规则无法审核图片内容，建议配置 AUDIT_PROVIDER=aliyun"},
            )
        else:
            try:
                payload = get_aliyun_green().check_image(image_url, scene)
                verdict = self._from_payload(payload, scene)
            except Exception as exc:
                verdict = self._handle_error(exc, scene, strict=True)
        self._persist(session, verdict, tenant_id=tenant_id, object_type=object_type, object_id=object_id)
        return verdict

    # ── 内部实现 ──────────────────────────────────────────────────────
    def _run(self, text: str, scene: str, *, strict: bool | None = None) -> Verdict:
        if self.provider == "aliyun":
            client = get_aliyun_green()
            if not client.available:
                logger.warning("阿里云 SDK 不可用，回退本地规则审核（scene=%s）", scene)
                return self._run_local(text, scene, provider="local(fallback)")
            try:
                return self._from_payload(client.check_text(text, scene), scene)
            except Exception as exc:
                policy = rules.get_policy(scene)
                fail_closed = (
                    policy.error_action == AuditResult.BLOCK.value if strict is None else strict
                )
                return self._handle_error(exc, scene, strict=fail_closed)
        return self._run_local(text, scene, provider="local")

    def _run_local(self, text: str, scene: str, *, provider: str) -> Verdict:
        label, words = rules.scan(text or "")
        action = rules.decide(scene, label)
        return Verdict(
            result=action,
            label=label,
            words=words,
            provider=provider,
            scene=scene,
            excerpt=rules.excerpt(text or "", words) if words else (text or "")[:120],
            raw={"policy": rules.get_policy(scene).description, "hit": bool(words)},
        )

    def _from_payload(self, payload: dict[str, Any], scene: str) -> Verdict:
        result = str(payload.get("result") or AuditResult.PASS.value)
        policy = rules.get_policy(scene)
        # 命中风险但场景策略为 review 时降级为复核；block 策略保持拦截
        if result == AuditResult.BLOCK.value and policy.risk_action == AuditResult.REVIEW.value:
            result = AuditResult.REVIEW.value
        words = [str(w) for w in (payload.get("words") or [])]
        label = str(payload.get("label") or rules.LABEL_SAFE)
        return Verdict(
            result=result,
            label=label,
            words=words,
            provider=self.provider,
            scene=scene,
            request_id=payload.get("request_id"),
            excerpt=str(payload.get("excerpt") or "")[:200] or None,
            raw=payload.get("raw") if isinstance(payload.get("raw"), dict) else None,
        )

    def _handle_error(self, exc: Exception, scene: str, *, strict: bool) -> Verdict:
        """审核服务异常：strict=True 时 fail-closed（抛 50203），否则放行并记 error。"""
        logger.warning("审核服务异常 scene=%s strict=%s err=%s", scene, strict, exc)
        if strict:
            raise audit_error(f"内容审核服务不可用: {exc}", scene=scene) from exc
        return Verdict(
            result=AuditResult.ERROR.value,
            label="audit-error",
            provider=self.provider,
            scene=scene,
            raw={"error": str(exc)[:300]},
        )

    def _cache_key(self, text: str, scene: str) -> str:
        return hashlib.sha256(f"{self.provider}:{scene}:{text or ''}".encode()).hexdigest()

    def _remember(self, key: str, verdict: Verdict, text: str) -> None:
        if self.cache_minutes <= 0:
            return
        if len(self._cache) >= _CACHE_MAX:
            self._cache.clear()
        self._cache[key] = (time.monotonic() + self.cache_minutes * 60, verdict)

    def _persist(
        self,
        session: Session | None,
        verdict: Verdict,
        *,
        tenant_id: str | None,
        object_type: str,
        object_id: str | None,
    ) -> None:
        """写入 audit_log（无 session / tenant_id 时跳过，例如审核回调里另行落库）。"""
        logger.info(
            "审核结论 scene=%s object=%s/%s result=%s label=%s provider=%s cached=%s",
            verdict.scene,
            object_type,
            object_id,
            verdict.result,
            verdict.label,
            verdict.provider,
            verdict.cached,
        )
        if session is None or tenant_id is None:
            return
        session.add(
            AuditLog(
                tenant_id=tenant_id,
                scene=verdict.scene or AuditScene.QUERY.value,
                object_type=object_type,
                object_id=object_id,
                provider=verdict.provider,
                result=verdict.result,
                label=verdict.label,
                risk_words=verdict.words or None,
                request_id=verdict.request_id,
                raw_response=verdict.raw,
                content_excerpt=verdict.excerpt,
            )
        )
        session.flush()


_moderator: Moderator | None = None


def get_moderator() -> Moderator:
    global _moderator
    if _moderator is None:
        _moderator = Moderator()
    return _moderator


def set_moderator(moderator: Moderator | None) -> None:
    """测试用：替换全局审核器（传 None 复位）。"""
    global _moderator
    _moderator = moderator
