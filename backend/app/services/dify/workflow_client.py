"""Dify Workflow（App B 出题 / App C 判分）接口 —— 真实 REST 调用。"""

from __future__ import annotations

import time
from typing import Any

from app.core.config import settings
from app.core.constants import DifyApp, QuestionType
from app.core.errors import dify_error
from app.core.logging import get_logger
from app.services.dify.mapping import extract_workflow_outputs, parse_grade_result, parse_workflow_questions

logger = get_logger(__name__)

_WORKFLOW_APPS = {DifyApp.QUIZ.value, DifyApp.QUIZ_GRADE.value}

#: 内部题型 → 提示词里的中文名（真实 App B 的 `type` 是自由文本段落，见 docs/04 §3）
_QUIZ_TYPE_LABELS = {
    QuestionType.SINGLE.value: "单选题",
    QuestionType.MULTI.value: "多选题",
    QuestionType.JUDGE.value: "判断题",
    QuestionType.SHORT.value: "简答题",
}

#: `/parameters` 输入变量缓存：(base_url, app) -> (过期时间, {变量名: 变量类型})
_INPUT_FORM_CACHE: dict[tuple[str, str], tuple[float, dict[str, str]]] = {}
_INPUT_FORM_TTL_SECONDS = 300.0
_INPUT_FORM_TIMEOUT_SECONDS = 10.0
#: 变量类型里含这些关键字视为数组
_ARRAY_TYPE_HINTS = ("array", "list")

#: 出题入参的语义别名：内部语义 -> 可能出现在 Workflow Start 节点的变量名
_QUIZ_INPUT_NAMES: dict[str, tuple[str, ...]] = {
    "datasets": ("kb_ids", "dataset_ids", "dataset_id"),
    "types": ("types", "type"),
    "count": ("count", "question_count"),
    "difficulty": ("difficulty",),
    "topic": ("topic", "subject"),
    # 后端按所选知识库检索出的【资料】原文（docs/04 §3）：App B 的「知识检索」节点只能静态
    # 勾选某个库，所以「题必须出自文档」靠后端下发这份资料保证。
    "material": ("context", "material", "reference_material", "knowledge"),
}

#: 下发的【资料】长度上限（字符）：够覆盖多个知识点，又不至于挤爆模型上下文
_MATERIAL_LIMIT_CHARS = 6000
#: 退化路径（App B 未声明资料变量）把资料并入 topic 时更保守，避免检索节点的查询词过长
_MATERIAL_IN_TOPIC_LIMIT_CHARS = 3000


def _merge_material_into_topic(topic_text: str, material_text: str) -> str:
    """把【资料】并入 topic —— 老版本 App B 没有资料变量时的退化路径。

    此时模型仍能从 topic 里读到原文（标题写明「出题唯一依据」），题目不再只靠主题词编。
    """
    return (
        "【资料·出题唯一依据】\n"
        f"{material_text[:_MATERIAL_IN_TOPIC_LIMIT_CHARS]}\n"
        f"【出题重点】{topic_text}"
    )


def describe_quiz_types(types: list[str]) -> str:
    """`["single", "short"]` -> `"单选题、简答题"`（无法识别时保留原值）。"""
    labels = [_QUIZ_TYPE_LABELS.get(str(t).strip().lower(), str(t).strip()) for t in types]
    return "、".join(label for label in labels if label) or _QUIZ_TYPE_LABELS[QuestionType.SINGLE.value]


def _is_array_type(variable_type: str) -> bool:
    lowered = variable_type.lower()
    return any(hint in lowered for hint in _ARRAY_TYPE_HINTS)


def _self_reported_error(outputs: dict[str, Any]) -> str:
    """Workflow 自报失败信息（真实 App B 输出 `{"success": "False", "error": "..."}`）。"""
    error = str(outputs.get("error") or "").strip()
    reported = str(outputs.get("success", "true")).strip().lower()
    if not error and reported in {"false", "0", "no", "否", "失败"}:
        error = "success=false"
    return f"（Workflow 自报：{error}）" if error else ""


def reset_input_form_cache() -> None:
    """清空 Start 节点变量缓存（测试或切换 Dify 工作区后调用）。"""
    _INPUT_FORM_CACHE.clear()


class WorkflowClientMixin:
    """Mixin：依赖 HttpDifyBase 提供的 request_json。两个 Workflow 各自独立 API Key。"""

    # ── Start 节点入参探针 ──────────────────────────────────────────────
    def _declared_inputs(self, app: str) -> dict[str, str]:
        """`GET /parameters` → `{变量名: 变量类型}`（缓存 5 分钟；失败返回 {} 走并集入参）。

        Workflow 的 Start 节点变量由用户自助配置，与 docs/04 §3 的设计契约可能不一致；
        先探测再按声明拼报文，可避免「字段名/类型对不上 → Dify 400」。
        """
        cache_key = (self._base_url, app)
        now = time.monotonic()
        cached = _INPUT_FORM_CACHE.get(cache_key)
        if cached is not None and cached[0] > now:
            return cached[1]

        variables: dict[str, str] = {}
        try:
            data = self.request_json(
                "GET",
                "/parameters",
                app=app,
                timeout=min(self._default_timeout, _INPUT_FORM_TIMEOUT_SECONDS),
            )
            for entry in data.get("user_input_form") or []:
                if not isinstance(entry, dict):
                    continue
                for spec in entry.values():
                    if isinstance(spec, dict) and spec.get("variable"):
                        variables[str(spec["variable"])] = str(spec.get("type") or "")
        except Exception as exc:  # noqa: BLE001 - 探针失败不应影响出题，回退并集入参
            logger.warning("读取 Dify %s 的 Start 变量失败，改用并集入参: %s", app, exc)
            variables = {}

        _INPUT_FORM_CACHE[cache_key] = (now + _INPUT_FORM_TTL_SECONDS, variables)
        return variables

    def _quiz_inputs(
        self,
        *,
        dataset_ids: list[str],
        types: list[str],
        count: int,
        difficulty: str,
        topic: str | None,
        material: str | None = None,
    ) -> dict[str, Any]:
        """按 Workflow 实际声明的变量拼出题入参；探不到声明时发「并集」兼容两种契约。

        `material` = 后端按**所选知识库**检索出的原文（docs/04 §3）。App B 的「知识检索」节点
        只能静态勾选某个库，所以「题必须出自文档」由后端下发这份资料保证：
        - 声明了 `context`（推荐）：直接下发，Prompt 里以【资料】为准；
        - 没声明（老版本 App B）：并入 `topic`，模型仍能读到原文。
        """
        labels = describe_quiz_types(types)
        topic_text = (topic or "核心知识点").strip() or "核心知识点"
        material_text = (material or "").strip()
        union: dict[str, Any] = {
            "kb_ids": list(dataset_ids),  # 设计契约（数组）
            "types": list(types),  # 设计契约（数组）
            "type": labels,  # 真实 App B（段落文本：题型描述）
            "count": count,
            "difficulty": difficulty,
            "topic": topic_text,
            "context": material_text[:_MATERIAL_LIMIT_CHARS],  # 设计契约：后端检索到的【资料】
        }
        declared = self._declared_inputs(DifyApp.QUIZ.value)
        if not declared:
            return union

        material_name = next((n for n in _QUIZ_INPUT_NAMES["material"] if n in declared), None)
        # 老版本 App B 没有资料变量：把【资料】并入 topic，保证「依据知识库原文出题」仍生效
        inline_material = bool(material_text) and material_name is None
        focus_text = (
            _merge_material_into_topic(topic_text, material_text) if inline_material else topic_text
        )

        inputs: dict[str, Any] = {}
        for semantic, names in _QUIZ_INPUT_NAMES.items():
            name = next((n for n in names if n in declared), None)
            if name is None:
                continue
            variable_type = declared[name]
            if semantic == "datasets":
                if name.endswith("s") or _is_array_type(variable_type):
                    inputs[name] = list(dataset_ids)
                else:  # dataset_id 单个：取第一个库
                    inputs[name] = dataset_ids[0] if dataset_ids else ""
            elif semantic == "types":
                if name == "types":  # 设计契约：数组（声明成文本时发描述串）
                    as_array = _is_array_type(variable_type) or not variable_type
                    inputs[name] = list(types) if as_array else labels
                elif _is_array_type(variable_type):  # type 声明成数组
                    inputs[name] = [
                        _QUIZ_TYPE_LABELS.get(str(t).strip().lower(), str(t).strip()) for t in types
                    ]
                else:  # 真实 App B：type 是段落文本
                    inputs[name] = labels
            elif semantic == "count":
                inputs[name] = count if variable_type == "number" else str(count)
            elif semantic == "difficulty":
                inputs[name] = difficulty
            elif semantic == "material":
                inputs[name] = material_text[:_MATERIAL_LIMIT_CHARS]
            else:  # topic
                inputs[name] = focus_text

        if not inputs:  # 声明的变量一个都不认识，退回并集
            return union

        # 真实 App B 未声明 topic：把主题（含资料）附在题型描述后，保证「按主题出题」仍生效
        if (
            "topic" not in declared
            and isinstance(inputs.get("type"), str)
            and (topic or inline_material)
        ):
            inputs["type"] = f"{inputs['type']}（主题：{focus_text}）"
            logger.info("出题 Workflow 未声明 topic 输入，已并入 type 提示: %s", inputs["type"][:120])
        if inline_material:
            logger.warning(
                "出题 Workflow 未声明资料变量，已把后端检索到的【资料】并入 topic"
                "（题目仍出自知识库原文；建议按 dify/README.md §4 给开始节点补 context 段落变量）"
            )
        return inputs

    def run_workflow(
        self, *, app: str, inputs: dict[str, Any], user: str, timeout: float | None = None
    ) -> dict[str, Any]:
        """`POST /workflows/run`（blocking，超时默认 120s）。"""
        if app not in _WORKFLOW_APPS:
            raise ValueError(f"未知的 Workflow 应用: {app}")
        body = {"inputs": inputs, "response_mode": "blocking", "user": user}
        data = self.request_json(
            "POST",
            "/workflows/run",
            app=app,
            json_body=body,
            timeout=timeout or settings.dify_workflow_timeout_seconds,
        )
        status = (data.get("data") or {}).get("status")
        if status and status != "succeeded":
            error = (data.get("data") or {}).get("error") or "Workflow 执行失败"
            raise dify_error(f"Workflow {app} {status}: {error}", app=app)
        return {
            "workflow_run_id": (data.get("workflow_run_id") or (data.get("data") or {}).get("id")),
            "outputs": extract_workflow_outputs(data),
            "elapsed_time": (data.get("data") or {}).get("elapsed_time"),
            "total_tokens": (data.get("data") or {}).get("total_tokens"),
            "raw": data,
        }

    # ── 语义化封装（业务层直接调用） ──────────────────────────────────
    def generate_questions(
        self,
        *,
        dataset_ids: list[str],
        types: list[str],
        count: int,
        difficulty: str,
        topic: str | None,
        user: str,
        material: str | None = None,
    ) -> dict[str, Any]:
        """App B：出题。返回已通过校验的题目列表（不合法题目已剔除）。

        `material` = 后端按所选知识库检索出的原文（唯一出题依据，见 `_quiz_inputs`）。
        """
        inputs = self._quiz_inputs(
            dataset_ids=dataset_ids,
            types=types,
            count=count,
            difficulty=difficulty,
            topic=topic,
            material=material,
        )
        result = self.run_workflow(app=DifyApp.QUIZ.value, inputs=inputs, user=user)
        questions = parse_workflow_questions(result["outputs"])
        if not questions:
            reason = _self_reported_error(result["outputs"])
            raise dify_error(
                f"出题 Workflow 未返回合法题目{reason}", count=count, types=types, inputs=inputs
            )
        return {
            "questions": questions,
            "workflow_run_id": result.get("workflow_run_id"),
            "total_tokens": result.get("total_tokens") or 0,
            "elapsed_time": result.get("elapsed_time"),
        }

    def grade_short_answer(
        self,
        *,
        stem: str,
        reference_answer: str,
        grading_points: list[str] | None,
        user_answer: str,
        user: str,
    ) -> dict[str, Any]:
        """App C：简答题判分。score>=60 视为正确（docs/04 §4）。"""
        inputs = {
            "stem": stem,
            "reference_answer": reference_answer,
            "grading_points": list(grading_points or []),
            "user_answer": user_answer,
        }
        declared = self._declared_inputs(DifyApp.QUIZ_GRADE.value)
        points_type = declared.get("grading_points")
        if points_type is not None and not _is_array_type(points_type):
            # App C 把采分点声明成文本变量时改发「；」拼接串（parse_grade_result 会再拆开）
            inputs["grading_points"] = "；".join(inputs["grading_points"])
        result = self.run_workflow(app=DifyApp.QUIZ_GRADE.value, inputs=inputs, user=user)
        return parse_grade_result(result["outputs"])
