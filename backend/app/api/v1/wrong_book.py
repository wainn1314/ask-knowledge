"""错题本路由（docs/05 §8）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.core.errors import envelope
from app.db.session import get_db
from app.schemas.wrong_book import WrongBookBriefOut, WrongBookDetailOut, WrongBookItemOut, WrongBookUpdateIn
from app.services import wrong_book_service

router = APIRouter(tags=["wrong-book"])


@router.get("/wrong-book", summary="错题列表")
def list_wrong_book(
    kb_id: str | None = Query(default=None),
    status: str | None = Query(default=None, description="unmastered / reviewing / mastered"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    items, total = wrong_book_service.list_wrong_book(
        db, user, kb_id=kb_id, status=status, page=page, page_size=page_size
    )
    return envelope(
        0,
        "ok",
        {
            "items": [WrongBookItemOut.model_validate(item).model_dump() for item in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


@router.get("/wrong-book/{wrong_id}", summary="错题详情")
def get_wrong_book(
    wrong_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    data = wrong_book_service.get_wrong_book(db, user, wrong_id)
    return envelope(0, "ok", WrongBookDetailOut.model_validate(data).model_dump())


@router.patch("/wrong-book/{wrong_id}", summary="更新掌握状态 / 备注")
def update_wrong_book(
    wrong_id: str,
    payload: WrongBookUpdateIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return envelope(0, "ok", wrong_book_service.update_wrong_book(db, user, wrong_id, payload))


@router.delete("/wrong-book/{wrong_id}", summary="删除错题（软删）")
def delete_wrong_book(
    wrong_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    wrong_book_service.delete_wrong_book(db, user, wrong_id)
    return envelope(0, "ok", {"id": wrong_id})


@router.post("/wrong-book/{wrong_id}/retry", summary="重做（返回题目，不含答案）")
def retry_wrong_book(
    wrong_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    data = wrong_book_service.retry_wrong_book(db, user, wrong_id)
    return envelope(0, "ok", WrongBookBriefOut.model_validate(data).model_dump())
