"""离线 Dify 实现（DIFY_MODE=mock）：内存知识库 + 关键词检索 + 模板化生成。

存在意义（不是"假的 Dify"，而是**可替换的契约实现**）：
1. 无 Dify 环境（本机无 Docker）也能完整跑通上传 → 解析 → 入库 → 问答 → 出题 → 判分 → 错题本；
2. 单测不依赖外部服务，可离线、可重复（本实现全部确定性，无随机数）；
3. 与 `HttpDifyClient` 共用同一份入参/出参契约 + 同一套 Workflow 输出校验（mapping.py），
   因此切换 `DIFY_MODE=http` 时上层代码零改动。

打分口径说明：mock 的 score 由关键词重合度映射到 0.55~0.95，仅用于阈值过滤演示；
真实向量/混合检索分数由 Dify 给出（`HttpDifyClient` 原样返回）。
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from app.core.constants import DifyApp, QuestionType
from app.core.errors import dify_error
from app.services.dify.client import CHUNK_SEPARATOR, RetrievedSegment
from app.services.dify.mapping import to_sources
from app.services.dify.workflow_client import WorkflowClientMixin

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[a-zA-Z0-9_]+")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")


class MockDifyClient(WorkflowClientMixin):
    """内存实现：datasets / documents / segments / conversations 全部进程内保存。"""

    mode = "mock"

    def __init__(self) -> None:
        self._datasets: dict[str, dict[str, Any]] = {}
        self._conversations: dict[str, dict[str, Any]] = {}
        #: 计数：走「直传原文件」（create-by-file）通道的文档数，供测试断言通道选择
        self.native_file_uploads = 0

    # ── 健康检查 ────────────────────────────────────────────────────────
    def health(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reachable": True,
            "datasets": len(self._datasets),
            "documents": sum(len(d["documents"]) for d in self._datasets.values()),
            "note": "离线 mock 实现：切到 DIFY_MODE=http 即调用真实 Dify",
        }

    # ── 知识库 ──────────────────────────────────────────────────────────
    def create_dataset(self, *, name: str, description: str = "") -> dict[str, Any]:
        dataset_id = f"ds-{uuid.uuid4().hex[:12]}"
        self._datasets[dataset_id] = {
            "id": dataset_id,
            "name": name,
            "description": description,
            "documents": {},
        }
        return {"id": dataset_id, "name": name}

    def delete_dataset(self, *, dataset_id: str) -> None:
        self._datasets.pop(dataset_id, None)

    def create_document_by_text(
        self,
        *,
        dataset_id: str,
        name: str,
        text: str,
        chunk_size: int,
        chunk_overlap: int,
        separator: str = CHUNK_SEPARATOR,
    ) -> dict[str, Any]:
        dataset = self._datasets.get(dataset_id)
        if dataset is None:
            raise dify_error("mock: dataset 不存在", dataset_id=dataset_id)
        document_id = f"doc-{uuid.uuid4().hex[:12]}"
        chunks = _chunk_text(text, chunk_size or 512, chunk_overlap or 0, separator)
        dataset["documents"][document_id] = {
            "id": document_id,
            "name": name,
            "indexing_status": "completed",
            "segments": [
                {
                    "id": f"seg-{uuid.uuid4().hex[:10]}",
                    "position": idx + 1,
                    "content": chunk,
                    "word_count": len(chunk),
                }
                for idx, chunk in enumerate(chunks)
            ],
        }
        return {
            "document_id": document_id,
            "name": name,
            "indexing_status": "completed",
            "batch": f"batch-{uuid.uuid4().hex[:8]}",
        }

    def create_document_by_file(
        self,
        *,
        dataset_id: str,
        name: str,
        filename: str,
        content: bytes,
        mime_type: str = "application/octet-stream",
        chunk_size: int = 512,
        chunk_overlap: int = 50,
        separator: str = CHUNK_SEPARATOR,
        doc_form: str = "text_model",
    ) -> dict[str, Any]:
        """直传原文件（离线 mock：不做真实解析，按文本退化抽取，仅保证契约与计数一致）。"""
        self.native_file_uploads += 1
        text = content.decode("utf-8", errors="replace")
        if len(text.strip()) < 20:
            text = f"[{filename}]（mock 不解析二进制格式，仅用于契约验证）"
        payload = self.create_document_by_text(
            dataset_id=dataset_id,
            name=name,
            text=text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separator=separator,
        )
        payload["source"] = "create-by-file"
        return payload

    def get_indexing_status(
        self, *, dataset_id: str, document_id: str, batch: str | None = None
    ) -> str:
        doc = self._find_document(dataset_id, document_id)
        return str(doc["indexing_status"])

    def delete_document(self, *, dataset_id: str, document_id: str) -> None:
        dataset = self._datasets.get(dataset_id)
        if dataset:
            dataset["documents"].pop(document_id, None)

    def list_documents(self, *, dataset_id: str, page: int = 1, limit: int = 20) -> dict[str, Any]:
        dataset = self._datasets.get(dataset_id) or {"documents": {}}
        docs = list(dataset["documents"].values())
        return {
            "items": [
                {
                    "dify_document_id": d["id"],
                    "name": d["name"],
                    "indexing_status": d["indexing_status"],
                    "word_count": sum(s["word_count"] for s in d["segments"]),
                    "segment_count": len(d["segments"]),
                }
                for d in docs
            ],
            "total": len(docs),
            "page": page,
            "page_size": limit,
        }

    def list_segments(
        self, *, dataset_id: str, document_id: str, page: int = 1, limit: int = 20
    ) -> dict[str, Any]:
        doc = self._find_document(dataset_id, document_id)
        segments = doc["segments"]
        start = (max(page, 1) - 1) * limit
        return {
            "items": [dict(s) for s in segments[start : start + limit]],
            "total": len(segments),
            "page": page,
            "page_size": limit,
        }

    def retrieval_test(
        self,
        *,
        dataset_id: str,
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.5,
        retrieval_mode: str = "hybrid",
        rerank_enabled: bool = True,
    ) -> list[RetrievedSegment]:
        return _search([dataset_id], self._datasets, query, top_k, score_threshold)

    # ── 问答 ────────────────────────────────────────────────────────────
    def chat_stream(
        self,
        *,
        query: str,
        dataset_ids: list[str],
        user: str,
        conversation_id: str | None = None,
        top_k: int = 5,
        score_threshold: float = 0.5,
        answer_style: str = "教学式，简洁分点",
        retrieval_mode: str = "hybrid",
        rerank_enabled: bool = True,
    ) -> Iterator[dict[str, Any]]:
        started = time.perf_counter()
        conversation = self._ensure_conversation(conversation_id, query)
        conv_id = conversation["id"]
        message_id = f"msg-{uuid.uuid4().hex[:12]}"

        segments = _search(dataset_ids, self._datasets, query, top_k, score_threshold)
        if segments:
            yield {"type": "sources", "items": to_sources(segments), "prefetch": False}
            answer = _compose_answer(query, segments, answer_style)
        else:
            answer = "知识库中未找到与问题相关的内容，请换一种问法或补充资料。"

        for piece in _split_for_stream(answer):
            yield {"type": "delta", "text": piece}

        prompt_tokens = _estimate_tokens(query) + sum(_estimate_tokens(s.content) for s in segments)
        completion_tokens = _estimate_tokens(answer)
        yield {
            "type": "usage",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
        conversation["messages"].append(
            {"id": f"msg-{uuid.uuid4().hex[:12]}", "role": "user", "content": query, "sources": []}
        )
        conversation["messages"].append(
            {
                "id": message_id,
                "role": "assistant",
                "content": answer,
                "sources": to_sources(segments),
            }
        )
        if conversation.get("name") in (None, "", "新会话"):
            conversation["name"] = query[:40]
        yield {
            "type": "end",
            "conversation_id": conv_id,
            "message_id": message_id,
            "finish_reason": "stop",
        }

    def list_conversations(self, *, user: str, limit: int = 20) -> list[dict[str, Any]]:
        items = list(self._conversations.values())[-limit:]
        return [
            {
                "dify_conversation_id": c["id"],
                "name": c.get("name"),
                "created_at": c.get("created_at"),
                "updated_at": c.get("updated_at"),
            }
            for c in items
        ]

    def delete_conversation(self, *, conversation_id: str, user: str) -> None:
        self._conversations.pop(conversation_id, None)

    def list_messages(
        self, *, conversation_id: str, user: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        conversation = self._conversations.get(conversation_id)
        if not conversation:
            return []
        return [
            {
                "dify_message_id": m["id"],
                "role": m["role"],
                "content": m["content"],
                "sources": m.get("sources") or [],
                "created_at": conversation.get("updated_at"),
            }
            for m in conversation["messages"][-limit:]
        ]

    def upload_file(
        self, *, filename: str, content: bytes, mime_type: str, user: str
    ) -> dict[str, Any]:
        return {"id": f"file-{uuid.uuid4().hex[:12]}", "name": filename, "size": len(content)}

    # ── Workflow（复用 WorkflowClientMixin 的校验与签名） ───────────────
    def _declared_inputs(self, app: str) -> dict[str, str]:
        """mock 无 `/parameters` 探针：始终返回空 → 上层发「并集」入参（含设计契约字段）。"""
        return {}

    def run_workflow(
        self, *, app: str, inputs: dict[str, Any], user: str, timeout: float | None = None
    ) -> dict[str, Any]:
        started = time.perf_counter()
        if app == DifyApp.QUIZ.value:
            outputs = _generate_quiz_outputs(inputs, self._datasets)
        elif app == DifyApp.QUIZ_GRADE.value:
            outputs = _grade_outputs(inputs)
        else:
            raise dify_error(f"mock: 未知 Workflow 应用 {app}")
        return {
            "workflow_run_id": f"run-{uuid.uuid4().hex[:12]}",
            "outputs": outputs,
            "elapsed_time": round(time.perf_counter() - started, 3),
            "total_tokens": _estimate_tokens(str(outputs)),
        }

    # ── 内部工具 ────────────────────────────────────────────────────────
    def _find_document(self, dataset_id: str, document_id: str) -> dict[str, Any]:
        dataset = self._datasets.get(dataset_id)
        if not dataset or document_id not in dataset["documents"]:
            raise dify_error("mock: document 不存在", dataset_id=dataset_id, document_id=document_id)
        return dataset["documents"][document_id]

    def _ensure_conversation(self, conversation_id: str | None, query: str) -> dict[str, Any]:
        if conversation_id:
            conversation = self._conversations.get(conversation_id)
            if conversation is None:
                raise dify_error("mock: 会话不存在", conversation_id=conversation_id)
        else:
            conv_id = f"conv-{uuid.uuid4().hex[:12]}"
            conversation = {
                "id": conv_id,
                "name": query[:40] or "新会话",
                "messages": [],
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
            self._conversations[conv_id] = conversation
        conversation["updated_at"] = _now_iso()
        return conversation


# ══ 模块级工具（无状态、确定性） ══════════════════════════════════════════
_STOPWORDS = {"的", "了", "是", "和", "与", "在", "对", "中", "为", "以及", "一个", "我们", "可以", "进行"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _split_for_stream(text: str, size: int = 12) -> list[str]:
    """模拟逐字流式输出（前端渲染体验与真实 Dify 一致）。"""
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def _estimate_tokens(text: str) -> int:
    """粗略 token 估算（中文按 1、其余按 1/4），仅用于 mock 的用量展示。"""
    cjk = len(_CJK.findall(text or ""))
    other = len(text or "") - cjk
    return max(1, cjk + other // 4)


def _terms(query: str) -> list[str]:
    """查询词切分：中文二元组 + 英文/数字词，去重后取前 20 个。"""
    text = (query or "").strip()
    if not text:
        return []
    terms: list[str] = [w.lower() for w in _WORD.findall(text) if len(w) >= 2]
    cjk_only = "".join(_CJK.findall(text))
    terms += [cjk_only[i : i + 2] for i in range(max(0, len(cjk_only) - 1))]
    if len(cjk_only) >= 3:
        terms += [cjk_only[i : i + 3] for i in range(len(cjk_only) - 2)]
    seen: list[str] = []
    for t in terms:
        if t and t not in seen and t not in _STOPWORDS:
            seen.append(t)
    return seen[:20]


def _keywords(text: str, limit: int = 5) -> list[str]:
    """抽取关键词（2-4 元组按频次），用于 mock 的判分要点与出题线索。"""
    cjk_only = "".join(_CJK.findall(text or ""))
    freq: dict[str, int] = {}
    for n in (2, 3, 4):
        for i in range(max(0, len(cjk_only) - n + 1)):
            gram = cjk_only[i : i + n]
            if gram in _STOPWORDS or any(w in gram for w in _STOPWORDS):
                continue
            freq[gram] = freq.get(gram, 0) + 1
    ordered = sorted(freq.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    out: list[str] = []
    for gram, _count in ordered:
        if any(gram in kept for kept in out):
            continue
        out.append(gram)
        if len(out) >= limit:
            break
    return out


def _chunk_text(text: str, size: int, overlap: int, separator: str = CHUNK_SEPARATOR) -> list[str]:
    """与 Dify 分块器口径一致：按 separator 切分并拼接到 size（单位：字符），块间留 overlap。"""
    normalized = (text or "").replace("\r\n", "\n").strip()
    if not normalized:
        return []
    size = max(64, int(size or 512))
    overlap = max(0, min(int(overlap or 0), size // 2))

    raw_units = (
        normalized.split(separator)
        if separator and separator in normalized
        else _SENTENCE_SPLIT.split(normalized)
    )
    units: list[str] = []
    for part in raw_units:
        part = part.strip()
        if not part:
            continue
        if len(part) <= size:
            units.append(part)
            continue
        buffer = ""
        for sentence in _SENTENCE_SPLIT.split(part):
            if not sentence:
                continue
            if len(buffer) + len(sentence) <= size:
                buffer += sentence
                continue
            if buffer.strip():
                units.append(buffer.strip())
            buffer = ""
            while len(sentence) > size:
                units.append(sentence[:size])
                sentence = sentence[size - overlap :]
            buffer = sentence
        if buffer.strip():
            units.append(buffer.strip())

    chunks: list[str] = []
    current = ""
    for unit in units:
        if not current:
            current = unit
        elif len(current) + len(separator) + len(unit) <= size:
            current = f"{current}{separator}{unit}"
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}{separator}{unit}" if tail else unit
    if current:
        chunks.append(current)
    return chunks


def _search(
    dataset_ids: list[str],
    datasets: dict[str, dict[str, Any]],
    query: str,
    top_k: int,
    score_threshold: float,
) -> list[RetrievedSegment]:
    """关键词重合度检索：命中数映射到 0.55~0.95，再按阈值与 top_k 过滤。"""
    terms = _terms(query)
    if not terms:
        return []
    scored: list[tuple[float, str, dict[str, Any], dict[str, Any]]] = []
    for dataset_id in dataset_ids:
        dataset = datasets.get(dataset_id)
        if not dataset:
            continue
        for doc in dataset["documents"].values():
            for seg in doc["segments"]:
                hits = sum(seg["content"].count(term) for term in terms)
                if hits <= 0:
                    continue
                scored.append((0.55 + 0.45 * (hits / (hits + 3)), dataset_id, doc, seg))
    scored.sort(key=lambda item: (-item[0], item[2]["name"], item[3]["position"]))

    out: list[RetrievedSegment] = []
    limit = max(1, int(top_k or 5))
    for score, dataset_id, doc, seg in scored:
        if score_threshold and score < float(score_threshold):
            continue
        out.append(
            RetrievedSegment(
                content=seg["content"],
                score=round(score, 4),
                document_id=doc["id"],
                document_name=doc["name"],
                segment_id=seg["id"],
                position=seg["position"],
                dataset_id=dataset_id,
            )
        )
        if len(out) >= limit:
            break
    return out


# ── 生成类（出题 / 判分 / 答案组装） ─────────────────────────────────────
def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text or "") if s.strip()]


def _first_sentence(text: str, limit: int = 80) -> str:
    sentences = _sentences(text)
    sentence = sentences[0] if sentences else (text or "").strip()
    return sentence[:limit] + ("…" if len(sentence) > limit else "")


def _compose_answer(query: str, segments: list[RetrievedSegment], answer_style: str) -> str:
    """离线作答：抽取命中段落的关键句 + [n] 引用编号（与真实 Dify 输出格式一致）。"""
    lines = [
        f"（离线 mock 生成 · 答案风格：{answer_style}）",
        f"问题：{query}",
        "",
        "依据知识库资料，可以这样理解：",
    ]
    for idx, seg in enumerate(segments[:3], start=1):
        lines.append(f"{idx}. {_first_sentence(seg.content, 120)} [{idx}]")
    names = "、".join(dict.fromkeys(s.document_name for s in segments if s.document_name))
    lines += [
        "",
        f"以上要点来自 {names}（共 {len(segments)} 条引用），点击引用编号可查看原文片段。",
        "提示：设置 DIFY_MODE=http 后，本回答将由 Dify 中的 chat-qa 应用生成。",
    ]
    return "\n".join(lines)


def _mutate(sentence: str, donor: str) -> str | None:
    """把句子里的一个中文词替换成另一段的词，用于构造「错误选项 / 错误判断」。"""
    words = [w for w in _keywords(sentence, limit=8) if len(w) >= 2]
    donors = [w for w in _keywords(donor, limit=8) if len(w) >= 2 and w not in words]
    if not words or not donors:
        return None
    mutated = sentence.replace(words[0], donors[0], 1)
    return mutated if mutated != sentence else None


def _generate_quiz_outputs(
    inputs: dict[str, Any], datasets: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """离线出题：从命中的资料段落构造题目，输出结构与真实 Workflow 完全一致。"""
    types = [str(t).lower() for t in (inputs.get("types") or [QuestionType.SINGLE.value])]
    valid_types = {q.value for q in QuestionType}
    types = [t for t in types if t in valid_types] or [QuestionType.SINGLE.value]
    count = max(1, min(int(inputs.get("count") or 5), 30))
    difficulty = str(inputs.get("difficulty") or "medium").lower()
    topic = str(inputs.get("topic") or "核心知识点")
    dataset_ids = [str(d) for d in (inputs.get("kb_ids") or [])]

    segments = _search(dataset_ids, datasets, topic, max(count * 3, 8), 0.0)
    if not segments:
        raise dify_error("mock: 知识库中没有可用于出题的内容", topic=topic)

    questions: list[dict[str, Any]] = []
    for index in range(count):
        seg_index = index % len(segments)
        segment = segments[seg_index]
        donor = segments[(seg_index + 1) % len(segments)]
        qtype = types[index % len(types)]
        lead = _first_sentence(segment.content, 60)
        base = {
            "difficulty": difficulty,
            "analysis": f"依据资料原文：{_first_sentence(segment.content, 200)}",
            "source_index": [seg_index + 1],
        }

        if qtype == QuestionType.SINGLE.value:
            wrongs = [w for w in (_mutate(lead, donor.content) for _ in range(3)) if w]
            while len(wrongs) < 3:
                wrongs.append(f"资料中未提及的说法：{topic}（干扰项{len(wrongs) + 1}）")
            slot = index % 4
            texts = wrongs[:3]
            texts.insert(slot, lead)
            questions.append(
                {
                    **base,
                    "type": QuestionType.SINGLE.value,
                    "stem": f"关于「{topic}」，下列说法与资料一致的是？",
                    "options": [
                        {"key": "ABCD"[i], "text": text} for i, text in enumerate(texts)
                    ],
                    "answer": "ABCD"[slot],
                }
            )
        elif qtype == QuestionType.MULTI.value:
            tail_text = segment.content.split("\n")[-1] if "\n" in segment.content else segment.content
            second = _first_sentence(tail_text, 60) or f"{topic}属于本节核心概念"
            correct = [lead, second] if second != lead else [lead, f"{topic}属于本节核心概念"]
            wrongs = [w for w in (_mutate(correct[0], donor.content) for _ in range(3)) if w]
            while len(wrongs) < 2:
                wrongs.append(f"与资料不符的说法{len(wrongs) + 1}：{topic}")
            texts = correct[:2] + wrongs[:2]
            questions.append(
                {
                    **base,
                    "type": QuestionType.MULTI.value,
                    "stem": f"关于「{topic}」，下列哪些说法符合资料（多选）？",
                    "options": [
                        {"key": "ABCD"[i], "text": text} for i, text in enumerate(texts)
                    ],
                    "answer": ["A", "B"],
                }
            )
        elif qtype == QuestionType.JUDGE.value:
            mutated = _mutate(lead, donor.content) if index % 2 else None
            if mutated:
                questions.append(
                    {
                        **base,
                        "type": QuestionType.JUDGE.value,
                        "stem": f"判断：资料中指出「{mutated}」。",
                        "options": None,
                        "answer": False,
                    }
                )
            else:
                questions.append(
                    {
                        **base,
                        "type": QuestionType.JUDGE.value,
                        "stem": f"判断：资料中指出「{lead}」。",
                        "options": None,
                        "answer": True,
                    }
                )
        else:
            questions.append(
                {
                    **base,
                    "type": QuestionType.SHORT.value,
                    "stem": f"请简述「{topic}」在资料中的主要说明。",
                    "options": None,
                    "answer": _first_sentence(segment.content, 200),
                    "grading_points": _keywords(segment.content, limit=4),
                }
            )
    return {"questions": questions, "need_more": False}


def _grade_outputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """离线判分：按要点命中率给分（0-100），score>=60 由上层判定为正确。"""
    user_answer = str(inputs.get("user_answer") or "").strip()
    reference = str(inputs.get("reference_answer") or "")
    points = [str(p) for p in (inputs.get("grading_points") or []) if str(p).strip()]
    if not points:
        points = _keywords(reference, limit=5)
    if not user_answer:
        return {"score": 0.0, "hit_points": [], "miss_points": points, "comment": "未作答"}
    if not points:
        ratio = 1.0 if len(user_answer) >= 10 else 0.5
        return {
            "score": round(ratio * 100, 2),
            "hit_points": [],
            "miss_points": [],
            "comment": "资料未提供评分要点，按作答完整度给分（离线 mock）",
        }
    hit = [p for p in points if p in user_answer]
    miss = [p for p in points if p not in hit]
    return {
        "score": round(100 * len(hit) / len(points), 2),
        "hit_points": hit,
        "miss_points": miss,
        "comment": f"命中 {len(hit)}/{len(points)} 个要点（离线 mock 判分）",
    }




