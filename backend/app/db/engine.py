"""数据库引擎与会话工厂。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _sqlite_path(url: str) -> Path | None:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    return Path(url[len(prefix) :])


def build_engine(url: str | None = None) -> Engine:
    db_url = url or settings.database_url
    kwargs: dict = {"echo": settings.db_echo, "pool_pre_ping": True}
    if db_url.startswith("sqlite"):
        path = _sqlite_path(db_url)
        if path is not None and not db_url.endswith(":memory:"):
            path.parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False}
    eng = create_engine(db_url, **kwargs)

    if db_url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _record):  # pragma: no cover - 方言细节
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    logger.info("数据库已连接: %s", db_url.split("@")[-1])
    return eng


engine: Engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def init_db() -> None:
    """建表（MVP 用 create_all；生产迁移方案见 docs/03 §10）。"""
    from app import models  # noqa: F401 - 触发模型注册
    from app.db.base import Base

    Base.metadata.create_all(bind=engine)
    logger.info("数据表已就绪: %d 张", len(Base.metadata.tables))
