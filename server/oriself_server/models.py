"""
SQLAlchemy ORM 模型 · v2.4。

变化（vs v2.3）：
- `Conversation` 表砍三分之二：删 action_json / action_type / dimension_targeted
  / turn_state / retry_count；新增 oriself_text / raw_stream / status_sentinel
  / discarded
- `EvidenceRecord` 表**删除**（v2.4 不再逐轮抽 evidence）
- `TestSession.skill_version` 默认 "2.4.0"
- `TestResult` 基本保留（报告落库）
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TestSession(Base):
    __tablename__ = "test_sessions"

    session_id = Column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    provider = Column(String(20), nullable=False)
    domain = Column(String(20), nullable=False, default="mbti")
    skill_version = Column(String(16), nullable=False, default="2.4.0")
    status = Column(String(20), default="active")  # active | completed | failed
    # v2.4 · 收敛 prompt 需要的偏好信息；R2 服务端解析后写入
    prefs_json = Column(Text)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    conversations = relationship(
        "Conversation", back_populates="session", cascade="all, delete-orphan"
    )
    result = relationship(
        "TestResult", back_populates="session", uselist=False, cascade="all, delete-orphan"
    )


class Conversation(Base):
    """一轮对话的持久化形态。

    v2.4：
    - `oriself_text` · LLM 输出去除 STATUS 行后的可见文本
    - `raw_stream` · 完整原文（含 STATUS），审计用
    - `status_sentinel` · CONTINUE / CONVERGE / NEED_USER
    - `discarded` · 用户点「重写这轮」后旧轮标 true；不进 transcript
    """
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36), ForeignKey("test_sessions.session_id"), nullable=False
    )
    round_number = Column(Integer, nullable=False)
    user_message = Column(Text, nullable=False)
    oriself_text = Column(Text, nullable=False, default="")
    raw_stream = Column(Text)
    status_sentinel = Column(String(16), default="CONTINUE")
    discarded = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    session = relationship("TestSession", back_populates="conversations")

    __table_args__ = (
        # 同一 (session_id, round_number) 允许：
        #   · 最多一条 active（discarded=False）—— 由 `_persist_turn` 在应用层去重
        #   · 任意条 discarded（每次用户点「重写」都会产生一条）
        # 过去这里有 UniqueConstraint(session_id, round_number, discarded)，
        # 但它把"任意条 discarded"也限成了一条 → 第二次重写同一轮时触发
        # IntegrityError → 500。应用层检查已经够，DB 层不再兜底。
        Index("ix_conv_session_round_desc", "session_id", "round_number"),
        Index("ix_conv_session_status", "session_id", "status_sentinel"),
    )


class TestResult(Base):
    __tablename__ = "test_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36),
        ForeignKey("test_sessions.session_id"),
        unique=True,
        nullable=False,
    )
    mbti_type = Column(String(8), nullable=False)
    insight_json = Column(Text, nullable=False)       # 3 段洞见序列化
    card_json = Column(Text, nullable=False)          # 名片结构化数据
    confidence_json = Column(Text)                    # confidence_per_dim 序列化

    # Issue · 可分享报告（v2.2+）
    issue_slug = Column(String(32), unique=True, index=True)
    issue_title = Column(String(200))
    issue_html = Column(Text)
    issue_is_public = Column(Boolean, default=False, nullable=False)
    issue_generated_at = Column(DateTime)

    created_at = Column(DateTime, default=_utcnow)

    session = relationship("TestSession", back_populates="result")


class Feedback(Base):
    __tablename__ = "feedbacks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    letter_id = Column(String(36), ForeignKey("test_sessions.session_id"), nullable=True)
    issue_slug = Column(String(32), nullable=True, index=True)

    rating = Column(Integer, nullable=True)
    text = Column(Text, nullable=False)
    contact = Column(String(200), nullable=True)
    user_agent = Column(String(500), nullable=True)

    created_at = Column(DateTime, default=_utcnow, index=True)

    __table_args__ = (
        Index("ix_feedback_letter", "letter_id"),
    )
