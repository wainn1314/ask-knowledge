"""出题 / 判分服务（docs/05 §7、docs/04 §3-§4）。"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, KbContext
from app.core.config import settings
from app.core.constants import (
    SHORT_ANSWER_PASS_SCORE,
    AuditScene,
    Difficulty,
    QuestionType,
    QuizStatus,
    UsageScene,
    WrongStatus,
)
from app.core.errors import bad_request, not_found
from app.core.logging import get_logger
from app.models.learning import AnswerRecord, Question, Quiz, WrongBook
from app.schemas.chat import SourceItem
from app.schemas.quiz import AnswerIn, QuizCreateIn
from app.services.dify import get_dify_client
from app.services.dify.mapping import format_material, to_sources
from app.services.moderation import get_moderator
from app.services.support import paginate, record_usage

logger = get_logger(__name__)

#: 出题缓存（相同参数 + 相同检索块 hash，30 分钟内复用，省 token）
_quiz_cache: dict[str, tuple[float, dict[str, Any]]] = {}

#: 片段 / 整份资料至少要有这么多「有信息量」的字符，否则视为不可用素材。
#: 真实教训：Markdown 分隔线被 `\n\n` 切成 3 字符的碎片块（内容只有 `---`），在 KB 的
#: 相似度阈值下反而胜出 → 出题资料为空 → 模型只能按 topic（≈知识库名）编题。
_MIN_SEGMENT_CHARS = 20
_MIN_MATERIAL_CHARS = 50
#: 片段里不算信息量的字符（空白、Markdown 装饰符、标点）
_NOISE_CHARS = re.compile(r"[\s\-—–_*#>|`~+=·:：;；,，.。!！?？()（）\[\]【】{}<>/\\\"'“”‘’]+")
#: 派生主题（试卷标题）时的长度：太短没意义，太长不像标题
_MIN_TOPIC_CHARS = 4
_TOPIC_CHARS = 40
#: 代码围栏（```markdown 之类）在派生主题时先去掉，否则标题会带上语言名
_CODE_FENCE = re.compile(r"`{3,}[A-Za-z0-9_+-]*")


def _substance(text: str) -> str:
    """只保留片段里真正有信息量的字符（用于判断「有没有可出题的正文」）。"""
    return _NOISE_CHARS.sub("", text or "")


def generate_quiz(db: Session, ctx: KbContext, user: CurrentUser, payload: QuizCreateIn) -> dict[str, Any]:
    """同步调用 Dify Workflow 出题 → 落库 Quiz/Question → 返回题目（不含答案）。"""
    if not ctx.dataset_id:
        raise bad_request("该知识库尚未关联 Dify dataset，无法出题")
    started = time.perf_counter()
    segments = _retrieve_segments(db, ctx, payload)
    if not segments:
        raise bad_request(
            "未检索到可用的知识内容（文档可能尚未入库或被切成了空片段），请先上传资料或更换主题"
        )
    topic = payload.topic or _derive_topic(segments, ctx.kb.name)
    # 出题的**唯一依据**：按所选知识库检索到的原文（App B 的检索节点只能静态绑库，不可依赖）
    material = format_material(to_sources(segments), max_chars=settings.quiz_material_max_chars)
    if len(_substance(material)) < _MIN_MATERIAL_CHARS:
        # 素材太少就不硬出题：宁可明确报错，也不要让模型按知识库名「编题」
        raise bad_request("知识库里的可用正文太少，无法出题；请补充资料后重试")
    cache_key = _cache_key(payload, topic, [s.segment_id or "" for s in segments])
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.info("出题命中缓存 key=%s", cache_key[:12])
        return cached

    moderator = get_moderator()
    topic_verdict = moderator.check_text(
        topic, AuditScene.QUERY.value, tenant_id=ctx.kb.tenant_id, object_type="quiz"
    )
    topic_verdict.raise_if_blocked("出题主题包含违规内容")

    result = get_dify_client().generate_questions(
        dataset_ids=[ctx.dataset_id],
        types=[t.value for t in payload.types],
        count=payload.count,
        difficulty=payload.difficulty.value,
        topic=topic,
        material=material,
        user=user.dify_user,
    )
    questions = result.get("questions") or []
    source_map = {idx: seg for idx, seg in enumerate(segments, start=1)}
    quiz = Quiz(
        tenant_id=ctx.kb.tenant_id,
        kb_id=ctx.kb_id,
        creator_id=user.id,
        title=topic[:200],
        config=payload.model_dump(mode="json"),
        question_count=len(questions),
        dify_workflow_run_id=result.get("workflow_run_id"),
        status=QuizStatus.READY.value,
    )
    db.add(quiz)
    db.flush()
    rows: list[Question] = []
    for idx, item in enumerate(questions, start=1):
        refs = [source_map[i] for i in item.get("source_index") or [] if i in source_map]
        row = Question(
            tenant_id=ctx.kb.tenant_id,
            quiz_id=quiz.id,
            kb_id=ctx.kb_id,
            seq=int(item.get("seq") or idx),
            type=str(item.get("type")),
            stem=str(item.get("stem")),
            options=item.get("options"),
            answer=item.get("answer"),
            analysis=item.get("analysis"),
            difficulty=str(item.get("difficulty") or Difficulty.MEDIUM.value),
            grading_points=item.get("grading_points"),
            source_segments=to_sources(refs) if refs else None,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    latency_ms = int((time.perf_counter() - started) * 1000)
    tokens = int(result.get("total_tokens") or 0)
    record_usage(
        db,
        tenant_id=ctx.kb.tenant_id,
        user_id=user.id,
        scene=UsageScene.QUIZ.value,
        ref_id=str(quiz.id),
        completion_tokens=tokens,
        latency_ms=latency_ms,
    )
    out = {
        "quiz_id": str(quiz.id),
        "status": quiz.status,
        "question_count": len(rows),
        "title": quiz.title,
        "questions": [_question_out(row, include_answer=False) for row in rows],
    }
    _cache_put(cache_key, out)
    logger.info(
        "出题完成 quiz=%s count=%s tokens=%s 资料=%s字 来源=%s份文档",
        quiz.id,
        len(rows),
        tokens,
        len(material),
        len({str(s.document_id) for s in segments}),
    )
    return out



def _retrieve_segments(db: Session, ctx: KbContext, payload: QuizCreateIn) -> list[Any]:
    """出题前先检索，保证有可出题的素材（这份素材会作为【资料】下发给 App B）。

    - 多路检索合并去重：主题 → 知识库名 → 最近一份可用户文档名（覆盖更多知识点）；
    - 丢掉没有实质内容的碎片块（如只含 Markdown 分隔线的块）；
    - 有效素材被相似度阈值挡光时，放宽阈值重取一次（碎片会在上一步被过滤掉）。

    召回条数按题目数放大：出题的题量再多，也不能只给模型一两条素材。
    """
    from pathlib import Path

    from app.core.constants import DocStatus
    from app.models.documents import Document

    client = get_dify_client()
    queries: list[str] = []
    if payload.topic:
        queries.append(payload.topic)
    queries.append(ctx.kb.name)
    latest = db.scalar(
        select(Document)
        .where(
            Document.kb_id == ctx.kb_id,
            Document.is_deleted.is_(False),
            Document.status == DocStatus.READY.value,
        )
        .order_by(Document.created_at.desc())
        .limit(1)
    )
    if latest is not None:
        queries.append(Path(latest.filename).stem)

    top_k = max(6, min(12, payload.count * 2))
    threshold = float(ctx.kb.score_threshold or 0.0)
    segments = _search_multi(client, ctx.dataset_id or "", queries, top_k, threshold)
    if not segments and threshold > 0:
        logger.info("出题按阈值 %s 未取到可用资料，放宽阈值重取 kb=%s", threshold, ctx.kb_id)
        segments = _search_multi(client, ctx.dataset_id or "", queries, top_k, 0.0)
    if segments:
        logger.info(
            "出题检索命中 query=%s segments=%s 有效资料=%s字",
            queries,
            len(segments),
            sum(len(_substance(s.content)) for s in segments),
        )
    return segments


def _search_multi(
    client: Any, dataset_id: str, queries: list[str], top_k: int, score_threshold: float
) -> list[Any]:
    """多路检索 → 合并去重 → 过滤无实质内容的碎片 → 按相似度排序（取前 top_k 条）。"""
    merged: dict[str, Any] = {}
    for query in dict.fromkeys(q for q in queries if q):
        for segment in client.retrieval_test(
            dataset_id=dataset_id, query=query, top_k=top_k, score_threshold=score_threshold
        ):
            key = str(
                segment.segment_id
                or f"{segment.document_id}:{segment.position}:{(segment.content or '')[:24]}"
            )
            merged.setdefault(key, segment)
    usable = [seg for seg in merged.values() if len(_substance(seg.content)) >= _MIN_SEGMENT_CHARS]
    usable.sort(key=lambda seg: float(seg.score or 0.0), reverse=True)
    return usable[:top_k]


def _cache_key(payload: QuizCreateIn, topic: str, segment_ids: list[str]) -> str:
    raw = json.dumps(
        {
            "kb": payload.kb_id,
            "types": sorted(t.value for t in payload.types),
            "count": payload.count,
            "difficulty": payload.difficulty.value,
            "topic": topic,
            "segments": sorted(segment_ids),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _derive_topic(segments: list[Any], fallback: str) -> str:
    """未指定主题时，用命中的首段内容派生主题（去掉 Markdown 噪声，空则退回知识库名）。"""
    for segment in segments:
        text = _CODE_FENCE.sub(" ", str(segment.content or ""))
        text = re.sub(r"[#*>`\-\s]+", " ", text).strip()
        text = re.sub(r"\s+", " ", text)
        if len(text) >= _MIN_TOPIC_CHARS:
            return text[:_TOPIC_CHARS]
    return fallback


def _cache_get(key: str) -> dict[str, Any] | None:
    entry = _quiz_cache.get(key)
    if entry is None:
        return None
    expire_at, value = entry
    if expire_at < time.monotonic():
        _quiz_cache.pop(key, None)
        return None
    return value


def _cache_put(key: str, value: dict[str, Any]) -> None:
    ttl = settings.quiz_cache_minutes * 60
    if ttl <= 0:
        return
    if len(_quiz_cache) > 200:
        _quiz_cache.clear()
    _quiz_cache[key] = (time.monotonic() + ttl, value)


def _question_out(row: Question, *, include_answer: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(row.id),
        "seq": int(row.seq or 0),
        "type": row.type,
        "difficulty": row.difficulty,
        "stem": row.stem,
        "options": row.options,
    }
    if include_answer:
        data.update(
            {
                "answer": row.answer,
                "analysis": row.analysis,
                "grading_points": row.grading_points,
                "sources": _sources_out(row.source_segments),
            }
        )
    return data


def _sources_out(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "index": int(item.get("index") or len(out) + 1),
                "document_id": item.get("document_id"),
                "document_name": item.get("document_name"),
                "segment_id": item.get("segment_id"),
                "score": float(item.get("score") or 0.0),
                "content": item.get("content") or "",
                "position": item.get("segment_position"),
            }
        )
    return out


def list_quizzes(
    db: Session, user: CurrentUser, *, kb_id: str | None = None, page: int = 1, page_size: int = 20
) -> tuple[list[Quiz], int]:
    stmt = select(Quiz).where(
        Quiz.tenant_id == user.tenant_id,
        Quiz.creator_id == user.id,
        Quiz.is_deleted.is_(False),
    )
    if kb_id:
        stmt = stmt.where(Quiz.kb_id == kb_id)
    stmt = stmt.order_by(Quiz.created_at.desc())
    return paginate(db, stmt, page=page, page_size=page_size)


def get_quiz(db: Session, user: CurrentUser, quiz_id: str, *, include_answer: bool) -> dict[str, Any]:
    quiz = _user_quiz(db, user, quiz_id)
    rows = db.scalars(
        select(Question).where(Question.quiz_id == quiz.id).order_by(Question.seq.asc())
    ).all()
    return {
        "quiz_id": str(quiz.id),
        "status": quiz.status,
        "question_count": int(quiz.question_count or len(rows)),
        "title": quiz.title,
        "questions": [_question_out(row, include_answer=include_answer) for row in rows],
    }


def delete_quiz(db: Session, user: CurrentUser, quiz_id: str) -> None:
    quiz = _user_quiz(db, user, quiz_id)
    quiz.mark_deleted()
    db.flush()


def _user_quiz(db: Session, user: CurrentUser, quiz_id: str) -> Quiz:
    quiz = db.scalar(
        select(Quiz).where(
            Quiz.id == quiz_id, Quiz.tenant_id == user.tenant_id, Quiz.is_deleted.is_(False)
        )
    )
    if quiz is None:
        raise not_found("试卷不存在")
    if str(quiz.creator_id) != user.id and not user.is_admin:
        raise not_found("试卷不存在")
    return quiz


# ── 作答与判分 ──────────────────────────────────────────────────────────
def submit_answers(
    db: Session, user: CurrentUser, quiz_id: str, answers: list[AnswerIn]
) -> dict[str, Any]:
    """批量提交作答：客观题规则判分、简答题 LLM 判分，并维护错题本。"""
    if not answers:
        raise bad_request("未提交任何作答")
    quiz = _user_quiz(db, user, quiz_id)
    rows = {
        str(q.id): q
        for q in db.scalars(select(Question).where(Question.quiz_id == quiz.id)).all()
    }
    items: list[dict[str, Any]] = []
    wrong_ids: list[str] = []
    total_score = 0.0
    correct = 0
    for ans in answers:
        question = rows.get(ans.question_id)
        if question is None:
            raise bad_request(f"题目不属于该试卷：{ans.question_id}")
        started = time.perf_counter()
        is_correct, score, feedback = _grade(db, question, ans.answer, user)
        cost_ms = int((time.perf_counter() - started) * 1000)
        db.add(
            AnswerRecord(
                tenant_id=quiz.tenant_id,
                user_id=user.id,
                quiz_id=quiz.id,
                question_id=question.id,
                user_answer=ans.answer,
                is_correct=is_correct,
                score=score,
                feedback=feedback,
                cost_ms=cost_ms,
            )
        )
        _touch_wrong_book(db, quiz, question, user, is_correct)
        total_score += score or 0.0
        if is_correct:
            correct += 1
        else:
            wrong_ids.append(str(question.id))
        items.append(
            {
                "question_id": str(question.id),
                "type": question.type,
                "is_correct": is_correct,
                "score": round(float(score or 0.0), 2),
                "correct_answer": question.answer,
                "user_answer": ans.answer,
                "feedback": feedback,
                "analysis": question.analysis,
            }
        )
    db.flush()
    return {
        "quiz_id": str(quiz.id),
        "total": len(items),
        "correct": correct,
        "score": round(total_score / max(1, len(items)), 2),
        "items": items,
        "wrong_question_ids": wrong_ids,
    }


def quiz_result(db: Session, user: CurrentUser, quiz_id: str) -> dict[str, Any]:
    """成绩单：取每题最近一次作答。"""
    quiz = _user_quiz(db, user, quiz_id)
    questions = {
        str(q.id): q
        for q in db.scalars(
            select(Question).where(Question.quiz_id == quiz.id).order_by(Question.seq.asc())
        ).all()
    }
    records = db.scalars(
        select(AnswerRecord)
        .where(AnswerRecord.quiz_id == quiz.id, AnswerRecord.user_id == user.id)
        .order_by(AnswerRecord.created_at.asc())
    ).all()
    latest: dict[str, AnswerRecord] = {}
    for record in records:
        latest[str(record.question_id)] = record
    items: list[dict[str, Any]] = []
    wrong_ids: list[str] = []
    total_score = 0.0
    correct = 0
    for qid, record in latest.items():
        question = questions.get(qid)
        if question is None:
            continue
        if record.is_correct:
            correct += 1
        else:
            wrong_ids.append(qid)
        total_score += float(record.score or 0.0)
        items.append(
            {
                "question_id": qid,
                "type": question.type,
                "is_correct": bool(record.is_correct),
                "score": round(float(record.score or 0.0), 2),
                "correct_answer": question.answer,
                "user_answer": record.user_answer,
                "feedback": record.feedback,
                "analysis": question.analysis,
            }
        )
    items.sort(key=lambda item: int(questions[item["question_id"]].seq or 0))
    return {
        "quiz_id": str(quiz.id),
        "total": len(items),
        "correct": correct,
        "score": round(total_score / max(1, len(items)), 2),
        "items": items,
        "wrong_question_ids": wrong_ids,
    }




# ── 判分规则 ────────────────────────────────────────────────────────────
def _grade(
    db: Session, question: Question, answer: Any, user: CurrentUser
) -> tuple[bool, float, str | None]:
    """返回 (是否正确, 得分, 反馈)。客观题 0/100 分制，简答题 0-100 分。"""
    if question.type == QuestionType.SHORT.value:
        text = str(answer or "").strip()
        if not text:
            return False, 0.0, "未作答"
        result = get_dify_client().grade_short_answer(
            stem=question.stem,
            reference_answer=str(question.answer or ""),
            grading_points=question.grading_points,
            user_answer=text,
            user=user.dify_user,
        )
        score = float(result.get("score") or 0.0)
        hit = "、".join(result.get("hit_points") or []) or "无"
        miss = "、".join(result.get("miss_points") or []) or "无"
        comment = str(result.get("comment") or "").strip()
        feedback = f"{comment}（命中要点：{hit}；遗漏：{miss}）".strip()
        record_usage(
            db,
            tenant_id=str(question.tenant_id),
            user_id=user.id,
            scene=UsageScene.QUIZ_GRADE.value,
            ref_id=str(question.id),
        )
        return score >= SHORT_ANSWER_PASS_SCORE, score, feedback
    correct = _normalize_objective(question, answer)
    if correct is None:
        return False, 0.0, "作答格式不正确"
    if correct:
        return True, 100.0, "回答正确"
    return False, 0.0, f"正确答案：{_answer_display(question.answer, question.type)}"


def _normalize_objective(question: Question, answer: Any) -> bool | None:
    """单选/多选/判断题的作答归一化比较；格式异常返回 None。"""
    if question.type == QuestionType.JUDGE.value:
        expected = question.answer
        if isinstance(answer, bool) and isinstance(expected, bool):
            return answer is expected
        if isinstance(answer, str) and isinstance(expected, bool):
            text = answer.strip().lower()
            if text in {"true", "对", "正确", "yes", "1", "t"}:
                return expected is True
            if text in {"false", "错", "错误", "no", "0", "f"}:
                return expected is False
        return None
    got = _answer_keys(answer)
    expected_keys = _answer_keys(question.answer)
    if got is None or expected_keys is None or not got or not expected_keys:
        return None
    if question.type == QuestionType.MULTI.value:
        return got == expected_keys
    return got == expected_keys and len(got) == 1


def _answer_keys(value: Any) -> set[str] | None:
    if isinstance(value, (list, tuple, set)):
        keys = {str(v).strip().upper()[:1] for v in value if str(v).strip()}
        return keys or None
    if isinstance(value, dict):
        return _answer_keys(value.get("key"))
    if isinstance(value, str) and value.strip():
        return {value.strip().upper()[:1]}
    return None


def _answer_display(value: Any, qtype: str) -> str:
    if qtype == QuestionType.JUDGE.value:
        return "正确" if value is True else "错误"
    if isinstance(value, (list, tuple, set)):
        return "、".join(str(v) for v in value)
    return str(value)


def _touch_wrong_book(
    db: Session, quiz: Quiz, question: Question, user: CurrentUser, is_correct: bool
) -> None:
    """答错 → 入错题本 / 累加次数；答对 → 置「复习中」。"""
    from app.db.base import utcnow

    row = db.scalar(
        select(WrongBook).where(
            WrongBook.user_id == user.id, WrongBook.question_id == question.id
        )
    )
    if not is_correct:
        if row is None:
            db.add(
                WrongBook(
                    tenant_id=str(quiz.tenant_id),
                    user_id=user.id,
                    kb_id=str(question.kb_id),
                    question_id=str(question.id),
                    wrong_count=1,
                    last_wrong_at=utcnow(),
                    status=WrongStatus.UNMASTERED.value,
                )
            )
        else:
            row.wrong_count = int(row.wrong_count or 0) + 1
            row.last_wrong_at = utcnow()
            row.status = WrongStatus.UNMASTERED.value
            row.mastered_at = None
            row.is_deleted = False
    elif row is not None and row.status == WrongStatus.UNMASTERED.value:
        row.status = WrongStatus.REVIEWING.value
    db.flush()
