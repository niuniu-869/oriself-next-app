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

    engine = get_engine()

    # 一次性迁移：v2.4.x 之前 conversations 上有一个
    # UniqueConstraint(session_id, round_number, discarded)，错误地把同一
    # round 的 discarded 记录也限成一条，导致用户第二次「重写同一轮」时
    # commit 触发 IntegrityError → 500。新代码把它拆了，这里把遗留索引也
    # drop 掉。DROP INDEX IF EXISTS 在新库 / 已 drop 过的库上无副作用。
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "DROP INDEX IF EXISTS uq_session_round_discarded"
            )
    except Exception:
        # 迁移失败不阻断启动——旧索引仍然存在时只影响"第二次重写同一轮"这一路径。
        pass

    Base.metadata.create_all(bind=engine)

    # v2.5.3 · in-place 兼容迁移：为旧库的 conversations 补上 quill_json 列。
    # create_all 不会为已存在的表新增列，所以这里显式 ALTER。SQLite 走 PRAGMA；
    # PostgreSQL 走 ADD COLUMN IF NOT EXISTS。失败不阻断启动——列缺失时回落到
    # `quill_json IS NULL` 的默认路径，仅会让旧 letter 看不到 quill 行。
    try:
        url = str(engine.url)
        with engine.begin() as conn:
            if url.startswith("sqlite"):
                cols = conn.exec_driver_sql(
                    "PRAGMA table_info(conversations)"
                ).fetchall()
                existing = {row[1] for row in cols}  # row[1] = column name
                if "quill_json" not in existing:
                    conn.exec_driver_sql(
                        "ALTER TABLE conversations ADD COLUMN quill_json TEXT"
                    )
            else:
                conn.exec_driver_sql(
                    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS quill_json TEXT"
                )
    except Exception:
        pass


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
