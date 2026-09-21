"""Dify 报文 ↔ 本地结构映射与校验（docs/04 §3 Code 节点校验规则的 Python 版）。

拆分为两段写入，本文件保持单一职责：检索结果 ↔ sources；Workflow JSON ↔ 题目/判分。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.constants import Difficulty, QuestionType
from app.core.logging import get_logger
from app.services.dify.client import RetrievedSegment

logger = get_logger(__name__)

_OPTION_KEYS = ["A", "B", "C", "D", "E"]
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_JSON_ARRAY_BLOCK = re.compile(r"\[.*\]", re.DOTALL)
#: 选项文本里的 "A. xxx" / "A、xxx" / "A）xxx" 前缀（仅认标点分隔，避免误伤 "Apple is red" 这类正文）
_OPTION_PREFIX = re.compile(r"^\s*([A-Ea-e])\s*[.．、,，:：)）]\s*")

#: Workflow/LLM 输出的题型别名 → 内部枚举值（真实 App B 返回中文题型名，如「单选题」「填空题」）。
#: 按顺序匹配，多选 / 判断 / 简答 必须先于笼统的「选择题」。
_TYPE_ALIASES: tuple[tuple[str, str], ...] = (
    ("multiple_choice", QuestionType.MULTI.value),
    ("multiple", QuestionType.MULTI.value),
    ("multi", QuestionType.MULTI.value),
    ("多项", QuestionType.MULTI.value),
    ("多选", QuestionType.MULTI.value),
    ("true_false", QuestionType.JUDGE.value),
    ("judge", QuestionType.JUDGE.value),
    ("判断", QuestionType.JUDGE.value),
    ("short_answer", QuestionType.SHORT.value),
    ("short", QuestionType.SHORT.value),
    ("填空", QuestionType.SHORT.value),
    ("简答", QuestionType.SHORT.value),
    ("问答", QuestionType.SHORT.value),
    ("single_choice", QuestionType.SINGLE.value),
    ("single", QuestionType.SINGLE.value),
    ("单选", QuestionType.SINGLE.value),
    ("单项", QuestionType.SINGLE.value),
    ("选择", QuestionType.SINGLE.value),
)
_TRUE_WORDS = {"true", "t", "yes", "y", "对", "正确", "是", "√"}
_FALSE_WORDS = {"false", "f", "no", "n", "错", "错误", "否", "×", "x"}
#: App C（判分）End 节点应输出的变量名；缺失时尝试从单个 JSON 字符串里展开
_GRADE_KEYS = ("score", "hit_points", "miss_points", "comment")
_GRADE_WRAPPERS = ("result", "output", "text", "data", "answer", "json")


# ── 检索结果 ────────────────────────────────────────────────────────────
def segments_from_retriever_resources(resources: list[dict] | None) -> list[RetrievedSegment]:
    """Dify `message_end.retriever_resources` → `RetrievedSegment`。"""
    out: list[RetrievedSegment] = []
    for r in resources or []:
        if not isinstance(r, dict):
            continue
        out.append(
            RetrievedSegment(
                content=str(r.get("content") or ""),
                score=float(r.get("score") or 0.0),
                document_id=r.get("document_id"),
                document_name=r.get("document_name"),
                segment_id=r.get("segment_id") or r.get("id"),
                position=_to_int(r.get("position")),
                dataset_id=r.get("dataset_id"),
            )
        )
    return out


def segments_from_retrieval_records(records: list[dict] | None) -> list[RetrievedSegment]:
    """Dify `datasets/{id}/retrieve` 的 records（字段嵌在 segment/document 里）。"""
    out: list[RetrievedSegment] = []
    for r in records or []:
        if not isinstance(r, dict):
            continue
        seg = r.get("segment") or {}
        doc = seg.get("document") or {}
        out.append(
            RetrievedSegment(
                content=str(seg.get("content") or ""),
                score=float(r.get("score") or 0.0),
                document_id=doc.get("id") or seg.get("document_id"),
                document_name=doc.get("name"),
                segment_id=seg.get("id"),
                position=_to_int(seg.get("position")),
                dataset_id=doc.get("dataset_id"),
            )
        )
    return out


def to_sources(
    segments: list[RetrievedSegment], document_id_map: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """按顺序编号 1..n（答案中的 [1] 对应 sources[0]）。"""
    id_map = document_id_map or {}
    return [
        seg.to_source(idx, local_document_id=id_map.get(seg.document_id or ""))
        for idx, seg in enumerate(segments, start=1)
    ]


def format_material(sources: list[dict[str, Any]], *, max_chars: int = 0) -> str:
    """把检索结果拼成注入大模型的资料块（带 [n] 编号与来源，便于模型引用）。

    问答（`chat_client` 的 rag 模式）与出题（`workflow_client` 的 App B 入参）共用同一份格式。
    `max_chars > 0` 时按块截断：出题的资料要能覆盖多个知识点，又不能撑爆模型上下文。
    """
    blocks: list[str] = []
    used = 0
    for item in sources:
        name = item.get("document_name") or item.get("section") or "片段"
        block = f"[{item.get('index')}]（{name}）\n{item.get('content') or ''}"
        if max_chars > 0:
            remain = max_chars - used
            if remain <= 0:
                break
            block = block[:remain]
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


# ── Workflow 输出 ───────────────────────────────────────────────────────
def extract_workflow_outputs(payload: dict[str, Any]) -> dict[str, Any]:
    """`POST /workflows/run`(blocking) → data.outputs。"""
    data = payload.get("data") or payload
    outputs = data.get("outputs")
    if isinstance(outputs, dict):
        return outputs
    if isinstance(outputs, str):
        parsed = loads_lenient(outputs)
        if isinstance(parsed, dict):
            return parsed
    return {}


def loads_lenient(value: str) -> Any:
    """容错解析 LLM 输出：允许 ```json 包裹或前后夹带解释文字（对象或数组均可）。"""
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for pattern in (_JSON_BLOCK, _JSON_ARRAY_BLOCK):
            m = pattern.search(text)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    continue
    logger.warning("Workflow 输出无法解析为 JSON: %s", text[:200])
    return None


def parse_workflow_questions(outputs: dict[str, Any]) -> list[dict[str, Any]]:
    """校验并修复出题 Workflow 输出，剔除不合规题目（docs/04 §3）。

    兼容两类输出：
    - 设计契约：`{"questions": [{seq,type,stem,options:[{key,text}],answer,analysis}]}`
    - 真实 App B：`{"success":"True","questions":"[{id,question,type,options,answer,explanation}]",`
      `"error":""}`（题型为中文名，选项形如 `"A. xxx"`，解析时统一归一为内部结构）
    """
    raw_questions = outputs.get("questions")
    if isinstance(raw_questions, str):
        parsed = loads_lenient(raw_questions)
        raw_questions = parsed
    if isinstance(raw_questions, dict):
        raw_questions = (
            raw_questions.get("questions") or raw_questions.get("data") or raw_questions
        )
    if isinstance(raw_questions, dict):  # 只有单题时也接受
        raw_questions = [raw_questions]
    if not isinstance(raw_questions, list):
        return []

    valid: list[dict[str, Any]] = []
    for raw in raw_questions:
        if not isinstance(raw, dict):
            continue
        item = normalize_question(raw, seq=len(valid) + 1)
        if item is not None:
            valid.append(item)
    return valid


def normalize_question_type(value: Any) -> str | None:
    """题型别名 → 内部枚举值（single/multi/judge/short）；无法识别返回 None。"""
    text = str(value or "").strip().lower()
    if not text:
        return None
    for alias, target in _TYPE_ALIASES:
        if alias in text:
            return target
    return None


def _normalize_bool(value: Any) -> bool | None:
    """判断题答案容错：bool / "正确" / "对" / "T" / "√" 等。"""
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    return None


def normalize_question(raw: dict[str, Any], *, seq: int) -> dict[str, Any] | None:
    """单题校验：类型合法、选项数、答案落在选项内、简答题必须有参考答案。

    字段名做同义兼容：`stem|question|title`、`analysis|explanation`、题型中英文别名。
    """
    qtype = normalize_question_type(raw.get("type"))
    stem = str(raw.get("stem") or raw.get("question") or raw.get("title") or "").strip()
    if qtype is None or not stem:
        return None

    options = _normalize_options(raw.get("options"))
    answer = raw.get("answer")
    if answer is None:
        answer = raw.get("correct_answer") or raw.get("参考答案")
    grading_points = raw.get("grading_points") or raw.get("grading_points_list")

    if qtype == QuestionType.SINGLE.value:
        if len(options) != 4:
            return None
        key = _normalize_answer_key(answer, options)
        if key is None:
            return None
        answer = key
    elif qtype == QuestionType.MULTI.value:
        if not 4 <= len(options) <= 5:
            return None
        keys = answer if isinstance(answer, list) else [answer]
        normalized = sorted({k for k in (_normalize_answer_key(a, options) for a in keys) if k})
        if len(normalized) < 2:
            return None
        answer = normalized
    elif qtype == QuestionType.JUDGE.value:
        options = None
        flag = _normalize_bool(answer)
        if flag is None:
            return None
        answer = flag
    else:  # short
        options = None
        answer = str(answer or "").strip()
        if not answer:
            return None
        grading_points = (
            [str(p) for p in grading_points if str(p).strip()][:8]
            if isinstance(grading_points, list)
            else None
        )

    difficulty = str(raw.get("difficulty") or "").strip().lower()
    if difficulty not in {d.value for d in Difficulty}:
        difficulty = Difficulty.MEDIUM.value

    source_index = raw.get("source_index")
    if isinstance(source_index, str):
        source_index = [s.strip() for s in source_index.split(",") if s.strip()]
    if not isinstance(source_index, list):
        source_index = []
    source_index = [i for i in (_to_int(x) for x in source_index) if i and i > 0]

    analysis = str(raw.get("analysis") or raw.get("explanation") or "").strip() or None
    return {
        "seq": seq,
        "type": qtype,
        "stem": stem,
        "options": options,
        "answer": answer,
        "analysis": analysis,
        "difficulty": difficulty,
        "grading_points": grading_points,
        "source_index": source_index,
    }


def parse_grade_result(outputs: dict[str, Any]) -> dict[str, Any]:
    """简答题判分 Workflow 输出（docs/04 §4）：score 0-100 + 命中/遗漏要点 + 评语。

    容错两种 End 节点写法：直接输出 `score/hit_points/miss_points/comment`，
    或只输出一个名为 `result`/`output`/`text` 的 JSON 字符串（自动展开）。
    """
    data = outputs
    if not any(key in data for key in _GRADE_KEYS):
        for key in _GRADE_WRAPPERS:
            parsed = loads_lenient(data.get(key)) if isinstance(data.get(key), str) else None
            if isinstance(parsed, dict):
                logger.info("判分 Workflow 用 %s 包裹 JSON，已自动展开", key)
                data = parsed
                break

    score = _to_float(data.get("score"))
    score = 0.0 if score is None else max(0.0, min(100.0, score))

    def _list(key: str) -> list[str]:
        value = data.get(key)
        if isinstance(value, list):
            return [str(v) for v in value if str(v).strip()]
        if isinstance(value, str) and value.strip():
            return [v.strip() for v in re.split(r"[;；,\n]", value) if v.strip()]
        return []

    return {
        "score": round(score, 2),
        "hit_points": _list("hit_points"),
        "miss_points": _list("miss_points"),
        "comment": str(data.get("comment") or "").strip(),
    }


# ── 小工具 ──────────────────────────────────────────────────────────────
def _normalize_options(value: Any) -> list[dict[str, str]]:
    """选项归一：dict(`{key,text}`) 或 字符串（`"A. xxx"` / `"xxx"`）都接受。"""
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for idx, opt in enumerate(value):
        if isinstance(opt, dict):
            key = str(opt.get("key") or opt.get("label") or _OPTION_KEYS[idx]).strip().upper()
            text = str(opt.get("text") or opt.get("content") or "").strip()
        else:
            text = str(opt or "").strip()
            key = _OPTION_KEYS[idx]
            m = _OPTION_PREFIX.match(text)
            if m:
                key, text = m.group(1).upper(), text[m.end() :].strip()
        if text:
            out.append({"key": key, "text": text})
    return out


def _normalize_answer_key(answer: Any, options: list[dict[str, str]]) -> str | None:
    if isinstance(answer, (list, tuple)):
        answer = answer[0] if answer else None
    if isinstance(answer, dict):
        answer = answer.get("key")
    key = str(answer or "").strip().upper()
    if len(key) > 1:
        key = key[0]
    return key if any(o["key"] == key for o in options) else None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

