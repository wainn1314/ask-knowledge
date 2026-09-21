"""错题本服务（docs/05 §8）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.core.constants import WrongStatus
from app.core.errors import not_found
from app.db.base import utcnow
from app.models.learning import AnswerRecord, Question, WrongBook
from app.schemas.wrong_book import WrongBookUpdateIn
from app.services.support import kb_name_map, paginate


def list_wrong_book(
    db: Session,
    user: CurrentUser,
    *,
    kb_id: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    stmt = select(WrongBook).where(
        WrongBook.tenant_id == user.tenant_id,
        WrongBook.user_id == user.id,
        WrongBook.is_deleted.is_(False),
    )
    if kb_id:
        stmt = stmt.where(WrongBook.kb_id == kb_id)
    if status:
        stmt = stmt.where(WrongBook.status == status)
    stmt = stmt.order_by(WrongBook.last_wrong_at.desc())
    rows, total = paginate(db, stmt, page=page, page_size=page_size)
    names = kb_name_map(db, [str(r.kb_id) for r in rows])
    items: list[dict[str, Any]] = []
    for row in rows:
        question = db.get(Question, row.question_id)
        if question is None:
            continue
        items.append(_item(row, question, names.get(str(row.kb_id))))
    return items, total


def get_wrong_book(db: Session, user: CurrentUser, wrong_id: str) -> dict[str, Any]:
    row = _user_row(db, user, wrong_id)
    question = db.get(Question, row.question_id)
    if question is None:
        raise not_found("题目不存在")
    names = kb_name_map(db, [str(row.kb_id)])
    detail = _item(row, question, names.get(str(row.kb_id)))
    last = db.scalar(
        select(AnswerRecord)
        .where(AnswerRecord.question_id == question.id, AnswerRecord.user_id == user.id)
        .order_by(AnswerRecord.created_at.desc())
        .limit(1)
    )
    detail.update(
        {
            "options": question.options,
            "answer": question.answer,
            "analysis": question.analysis,
            "grading_points": question.grading_points,
            "sources": question.source_segments,
            "last_feedback": last.feedback if last else None,
            "last_user_answer": last.user_answer if last else None,
        }
    )
    return detail


def update_wrong_book(
    db: Session, user: CurrentUser, wrong_id: str, payload: WrongBookUpdateIn
) -> dict[str, Any]:
    row = _user_row(db, user, wrong_id)
    if payload.status is not None:
        row.status = payload.status.value
        row.mastered_at = utcnow() if payload.status == WrongStatus.MASTERED else None
    if payload.remark is not None:
        row.remark = payload.remark
    db.flush()
    question = db.get(Question, row.question_id)
    return _item(row, question, None) if question else {"id": str(row.id)}


def delete_wrong_book(db: Session, user: CurrentUser, wrong_id: str) -> None:
    row = _user_row(db, user, wrong_id)
    row.mark_deleted()
    db.flush()


def retry_wrong_book(db: Session, user: CurrentUser, wrong_id: str) -> dict[str, Any]:
    """重做：返回题目（不含答案），作答仍走 /quizzes/{quiz_id}/answers。"""
    row = _user_row(db, user, wrong_id)
    question = db.get(Question, row.question_id)
    if question is None:
        raise not_found("题目不存在")
    return {
        "id": str(row.id),
        "question_id": str(question.id),
        "type": question.type,
        "stem": question.stem,
        "options": question.options,
        "status": row.status,
        "wrong_count": int(row.wrong_count or 0),
    }


def _user_row(db: Session, user: CurrentUser, wrong_id: str) -> WrongBook:
    row = db.scalar(
        select(WrongBook).where(
            WrongBook.id == wrong_id,
            WrongBook.tenant_id == user.tenant_id,
            WrongBook.user_id == user.id,
            WrongBook.is_deleted.is_(False),
        )
    )
    if row is None:
        raise not_found("错题不存在")
    return row


def _item(row: WrongBook, question: Question, kb_name: str | None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "question_id": str(question.id),
        "kb_id": str(row.kb_id),
        "kb_name": kb_name,
        "type": question.type,
        "stem": question.stem,
        "wrong_count": int(row.wrong_count or 0),
        "status": row.status,
        "last_wrong_at": row.last_wrong_at,
        "remark": row.remark,
    }
