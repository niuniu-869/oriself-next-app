"""
SQLite / SQLAlchemy 集成。独立于 oriself-core，v2.0 新建 oriself_v2.db。
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


DEFAULT_DB_PATH = Path(os.environ.get("ORISELF_DB_PATH", "oriself_v2.db")).resolve()
DEFAULT_DB_URL = f"sqlite:///{DEFAULT_DB_PATH}"


def make_engine(url: str = DEFAULT_DB_URL) -> Engine:
    return create_engine(
        url,
        echo=False,
        future=True,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )


_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def get_sessionmaker() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SessionLocal


def init_db() -> None:
    from .models import Base

    Base.metadata.create_all(bind=get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def reset_for_tests(url: str = "sqlite:///:memory:") -> None:
    """测试用：替换 engine + 建表。"""
    global _engine, _SessionLocal
    _engine = make_engine(url)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    init_db()
