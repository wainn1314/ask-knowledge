"""阿里云内容安全（green-cip / green20220302）适配。

SDK 为可选依赖：`pip install alibabacloud_green20220302`。
未安装或调用失败时由 `moderator` 按场景策略决定回退（本地规则）或 fail-closed（错误码 50203）。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.core.errors import audit_error
from app.core.logging import get_logger

logger = get_logger(__name__)

# 场景 → 阿里云服务码（教育场景：文档 / 对话 / 生成内容）
SERVICE_BY_SCENE: dict[str, str] = {
    "upload": "document_detection_pro",
    "parse": "document_detection_pro",
    "query": "chat_detection_pro",
    "answer": "pgc_detection_pro",
}
DEFAULT_SERVICE = "comment_detection_pro"

_RISK_MAP = {"none": "pass", "low": "pass", "medium": "review", "high": "block"}


class AliyunGreenClient:
    """文本 / 图片审核；返回归一化结构 {result, label, words, risk_level, request_id, raw}。"""

    provider = "aliyun"

    def __init__(self) -> None:
        self._client: Any = None
        self._models: Any = None

    @property
    def available(self) -> bool:
        """SDK 是否可用（不校验凭据）。"""
        try:
            self._ensure()
            return True
        except ImportError:
            return False

    def _ensure(self) -> None:
        if self._client is not None:
            return
        if not (settings.aliyun_access_key_id and settings.aliyun_access_key_secret):
            raise audit_error("阿里云 AccessKey 未配置（ALIYUN_ACCESS_KEY_ID/SECRET）")
        from alibabacloud_green20220302 import models as green_models  # type: ignore
        from alibabacloud_green20220302.client import Client  # type: ignore
        from alibabacloud_tea_openapi import models as open_models  # type: ignore

        config = open_models.Config(
            access_key_id=settings.aliyun_access_key_id,
            access_key_secret=settings.aliyun_access_key_secret,
        )
        config.endpoint = settings.aliyun_green_endpoint
        config.connect_timeout = 3000
        config.read_timeout = 6000
        self._client = Client(config)
        self._models = green_models

    # ── 文本审核 ──────────────────────────────────────────────────────
    def check_text(self, text: str, scene: str) -> dict[str, Any]:
        self._ensure()
        service = SERVICE_BY_SCENE.get(scene, DEFAULT_SERVICE)
        request = self._models.TextModerationPlusRequest(
            service=service,
            service_parameters=json.dumps({"content": text}, ensure_ascii=False),
        )
        try:
            response = self._client.text_moderation_plus(request)
        except Exception as exc:  # SDK 异常类型多样，统一收敛为「审核服务异常」
            raise audit_error(f"阿里云文本审核调用失败: {exc}") from exc
        body = getattr(response, "body", None)
        if body is None or getattr(body, "code", 200) not in (200, None):
            raise audit_error(f"阿里云文本审核返回异常: {getattr(body, 'code', None)}")
        raw = _to_dict(getattr(body, "data", None))
        risk_level = str(raw.get("risk_level") or raw.get("riskLevel") or "none").lower()
        label, words = _extract_labels(raw)
        result = _RISK_MAP.get(risk_level, "review")
        if result == "pass" and words:
            result = "review"
        return {
            "result": result,
            "label": label,
            "words": words,
            "risk_level": risk_level,
            "request_id": getattr(body, "request_id", None),
            "raw": raw,
        }

    # ── 图片审核 ──────────────────────────────────────────────────────
    def check_image(self, image_url: str, scene: str) -> dict[str, Any]:
        self._ensure()
        request = self._models.ImageModerationRequest(
            service="baselineCheck",
            service_parameters=json.dumps({"imageUrl": image_url}, ensure_ascii=False),
        )
        try:
            response = self._client.image_moderation(request)
        except Exception as exc:
            raise audit_error(f"阿里云图片审核调用失败: {exc}") from exc
        body = getattr(response, "body", None)
        if body is None or getattr(body, "code", 200) not in (200, None):
            raise audit_error(f"阿里云图片审核返回异常: {getattr(body, 'code', None)}")
        raw = _to_dict(getattr(body, "data", None))
        items = raw.get("result") or []
        labels: list[str] = []
        words: list[str] = []
        risk = "none"
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label") or "")
                if label and label not in {"nonLabel", "normal"}:
                    labels.append(label)
                level = str(item.get("riskLevel") or item.get("risk_level") or "none").lower()
                if _risk_rank(level) > _risk_rank(risk):
                    risk = level
                words += [str(w) for w in (item.get("riskWords") or []) if w]
        return {
            "result": _RISK_MAP.get(risk, "review") if labels else "pass",
            "label": labels[0] if labels else "safe",
            "words": sorted(set(words)),
            "risk_level": risk,
            "request_id": getattr(body, "request_id", None),
            "raw": raw,
        }


def _risk_rank(level: str) -> int:
    return {"none": 0, "low": 1, "medium": 2, "high": 3}.get(level, 0)


def _to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "to_map"):
        return dict(value.to_map() or {})
    if hasattr(value, "__dict__"):
        return {k: v for k, v in vars(value).items() if not k.startswith("_")}
    return {"value": str(value)}


def _extract_labels(raw: dict[str, Any]) -> tuple[str, list[str]]:
    """抽取 label 与风险词（兼容 result / labels 两种返回结构）。"""
    words: list[str] = []
    label = ""
    items = raw.get("result") or []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            if not label:
                label = str(item.get("label") or "")
            words += [str(w) for w in (item.get("risk_words") or item.get("riskWords") or []) if w]
    if not label and raw.get("labels"):
        labels = raw["labels"]
        label = labels.split(",")[0] if isinstance(labels, str) else str(labels)
    return (label or "safe"), sorted(set(words))


_green: AliyunGreenClient | None = None


def get_aliyun_green() -> AliyunGreenClient:
    global _green
    if _green is None:
        _green = AliyunGreenClient()
    return _green
