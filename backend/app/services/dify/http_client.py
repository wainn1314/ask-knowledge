"""`HttpDifyClient`：真实 Dify REST 客户端（DIFY_MODE=http）。

组合三个能力域 Mixin，共享同一 httpx 客户端与鉴权逻辑：
- DatasetClientMixin  知识库 / 文档入库 / 分块 / 检索
- ChatClientMixin     问答流式 / 会话 / 消息 / 文件上传
- WorkflowClientMixin 出题 / 简答判分
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.constants import DifyApp
from app.services.dify.chat_client import ChatClientMixin
from app.services.dify.dataset_client import DatasetClientMixin
from app.services.dify.http_base import HttpDifyBase
from app.services.dify.workflow_client import WorkflowClientMixin


class HttpDifyClient(DatasetClientMixin, ChatClientMixin, WorkflowClientMixin, HttpDifyBase):
    """真实 Dify 客户端。所有方法抛 BizError(50201) 表示 Dify 侧失败。"""

    mode = "http"

    def health(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "mode": self.mode,
            "base_url": self._base_url,
            "keys": {
                "chat": bool(settings.dify_app_api_key),
                "quiz": bool(settings.dify_quiz_workflow_api_key),
                "grade": bool(settings.dify_grade_workflow_api_key),
                "dataset": bool(settings.dify_dataset_api_key),
            },
            "reachable": False,
        }
        try:
            data = self.request_json(
                "GET", "/datasets", app="dataset", params={"page": 1, "limit": 1}, timeout=5.0
            )
            info["reachable"] = True
            info["dataset_total"] = data.get("total")
        except Exception as exc:  # noqa: BLE001 - 健康检查不应抛错
            info["error"] = str(exc)
        try:
            if settings.dify_app_api_key:
                self.request_json("GET", "/parameters", app=DifyApp.CHAT.value, timeout=5.0)
                info["chat_app_ok"] = True
        except Exception as exc:  # noqa: BLE001
            info["chat_app_ok"] = False
            info["chat_app_error"] = str(exc)
        return info
