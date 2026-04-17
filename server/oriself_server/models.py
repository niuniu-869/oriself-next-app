"""
SQLAlchemy ORM 模型 · v2.0。

变化点（vs oriself-core）：
- 新增 `turn_state` 列（崩溃恢复状态机）
- 新增 `skill_version` 列（对齐审阅决策）
- `(session_id, round_number)` UNIQUE 约束
- `EvidenceRecord` 独立表存 verified evidence
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
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
    skill_version = Column(String(16), nullable=False, default="2.0.0")
    status = Column(String(20), default="active")  # active | completed | failed
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    conversations = relationship(
        "Conversation", back_populates="session", cascade="all, delete-orphan"
    )
    evidences = relationship(
        "EvidenceRecord", back_populates="session", cascade="all, delete-orphan"
    )
    result = relationship(
        "TestResult", back_populates="session", uselist=False, cascade="all, delete-orphan"
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36), ForeignKey("test_sessions.session_id"), nullable=False
    )
    round_number = Column(Integer, nullable=False)
    user_message = Column(Text, nullable=False)
    action_json = Column(Text)  # raw LLM-emitted action JSON
    action_type = Column(String(32))  # quick filter
    dimension_targeted = Column(String(8))
    turn_state = Column(String(16), default="pending")  # pending|generating|validated|saved|failed
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    session = relationship("TestSession", back_populates="conversations")

    __table_args__ = (
        UniqueConstraint("session_id", "round_number", name="uq_session_round"),
        Index("ix_conv_session_round_desc", "session_id", "round_number"),
        Index("ix_conv_session_state", "session_id", "turn_state"),
    )


class EvidenceRecord(Base):
    __tablename__ = "evidence_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36), ForeignKey("test_sessions.session_id"), nullable=False
    )
    round_number = Column(Integer, nullable=False)
    dimension = Column(String(8), nullable=False)  # E/I | S/N | T/F | J/P
    user_quote = Column(Text, nullable=False)
    confidence = Column(Float, default=0.5)
    interpretation = Column(Text)
    created_at = Column(DateTime, default=_utcnow)

    session = relationship("TestSession", back_populates="evidences")

    __table_args__ = (
        Index("ix_evidence_session_dim", "session_id", "dimension"),
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
    insight_json = Column(Text, nullable=False)  # 3 段洞见序列化
    card_json = Column(Text, nullable=False)  # 名片结构化数据

    # ===== Issue (v2.2+) · LLM 生成的可分享报告 =====
    # issue_slug 形如 "intj-a94b2c"；用户公开分享时 URL: /issues/{slug}
    # issue_html 是 LLM 端到端生成的完整 HTML 文档（per-MBTI 独立设计）
    issue_slug = Column(String(32), unique=True, index=True)
    issue_title = Column(String(200))  # 报告标题，LLM 生成（如「一本关于沉默的书」）
    issue_html = Column(Text)  # 完整的独立 HTML 文档，前端 iframe 嵌入
    issue_is_public = Column(Boolean, default=False, nullable=False)
    issue_generated_at = Column(DateTime)

    created_at = Column(DateTime, default=_utcnow)

    session = relationship("TestSession", back_populates="result")


class Feedback(Base):
    """用户反馈。匿名可，可选关联到一封信 / 一个 issue。

    设计哲学：反馈是单向投递通道，不需要回复链；不强制邮箱，但允许留邮箱以便回访。
    rating 是 1-5 整数（1=差 5=好），text 是自由表述（≤2000 字）。
    """

    __tablename__ = "feedbacks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 关联信 / issue（可空：landing 页也可投递反馈）
    letter_id = Column(String(36), ForeignKey("test_sessions.session_id"), nullable=True)
    issue_slug = Column(String(32), nullable=True, index=True)

    rating = Column(Integer, nullable=True)  # 1-5
    text = Column(Text, nullable=False)
    contact = Column(String(200), nullable=True)  # 可选邮箱 / 微信
    user_agent = Column(String(500), nullable=True)

    created_at = Column(DateTime, default=_utcnow, index=True)

    __table_args__ = (
        Index("ix_feedback_letter", "letter_id"),
    )
