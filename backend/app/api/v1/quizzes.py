"""出题 / 判分路由（docs/05 §7）。"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, KbContext, get_current_user, get_kb_context
from app.core.constants import KbRole
from app.core.errors import envelope
from app.db.session import get_db
from app.schemas.quiz import AnswerIn, QuestionOut, QuizBriefOut, QuizCreateIn, QuizResultOut
from app.services import quiz_service
from app.services.support import kb_name_map

router = APIRouter(tags=["quizzes"])


@router.post("/quizzes", status_code=201, summary="生成题目")
def create_quiz(
    payload: QuizCreateIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """同步等待 Dify Workflow（超时 120s）；返回的题目不含答案与解析。"""
    ctx: KbContext = get_kb_context(payload.kb_id, db, user, KbRole.EDITOR.value)
    return envelope(0, "created", quiz_service.generate_quiz(db, ctx, user, payload))


@router.get("/quizzes", summary="我的出题记录")
def list_quizzes(
    kb_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    items, total = quiz_service.list_quizzes(
        db, user, kb_id=kb_id, page=page, page_size=page_size
    )
    names = kb_name_map(db, [str(q.kb_id) for q in items])
    out = []
    for quiz in items:
        data = QuizBriefOut.model_validate(quiz).model_dump()
        data["kb_name"] = names.get(str(quiz.kb_id))
        out.append(data)
    return envelope(
        0, "ok", {"items": out, "total": total, "page": page, "page_size": page_size}
    )


@router.get("/quizzes/{quiz_id}", summary="试卷详情")
def get_quiz(
    quiz_id: str,
    include_answer: bool = Query(default=True, description="是否返回答案与解析"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    data = quiz_service.get_quiz(db, user, quiz_id, include_answer=include_answer)
    questions = [QuestionOut.model_validate(q).model_dump() for q in data["questions"]]
    return envelope(0, "ok", {**data, "questions": questions})


@router.post("/quizzes/{quiz_id}/answers", summary="批量提交作答并判分")
def submit_answers(
    quiz_id: str,
    answers: list[AnswerIn] = Body(..., embed=False),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    data = quiz_service.submit_answers(db, user, quiz_id, answers)
    return envelope(0, "ok", data)


@router.get("/quizzes/{quiz_id}/result", summary="成绩单")
def get_result(
    quiz_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return envelope(0, "ok", QuizResultOut.model_validate(quiz_service.quiz_result(db, user, quiz_id)).model_dump())


@router.delete("/quizzes/{quiz_id}", summary="删除试卷（软删）")
def delete_quiz(
    quiz_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    quiz_service.delete_quiz(db, user, quiz_id)
    return envelope(0, "ok", {"quiz_id": quiz_id})
