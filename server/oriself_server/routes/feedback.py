"""
FastAPI routes · 用户反馈。

设计：
- 单向投递。无登录、无回复链——只是一条记录写入 DB。
- 关联可选：可以来自 letter / issue / landing 任意位置。
- 防滥用：text 长度限制 + per-IP 简单速率限制（in-process token bucket，
  生产应换成 Redis；MVP 够用）。
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..database import get_sessionmaker
from ..models import Feedback, TestResult, TestSession
from ..utils.html_sanitize import escape_user_quote


router = APIRouter(prefix="/feedback", tags=["feedback"])


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------


def get_db():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Rate limiter · per-IP token bucket
# ---------------------------------------------------------------------------


_BUCKET: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW_SECONDS = 600.0  # 10 分钟
_RATE_LIMIT = 5               # 每 IP 10 分钟最多 5 条


def _check_rate_limit(ip: str) -> None:
    now = time.time()
    bucket = _BUCKET[ip]
    # 清掉过期的
    cutoff = now - _RATE_WINDOW_SECONDS
    while bucket and bucket[0] < cutoff:
        bucket.pop(0)
    if len(bucket) >= _RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="提交过于频繁，请稍后再来",
        )
    bucket.append(now)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class FeedbackCreate(BaseModel):
    text: str = Field(min_length=2, max_length=2000)
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    letter_id: Optional[str] = Field(default=None, max_length=36)
    issue_slug: Optional[str] = Field(default=None, max_length=32)
    contact: Optional[str] = Field(default=None, max_length=200)

    @field_validator("text")
    @classmethod
    def _strip_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("反馈内容不能为空")
        return v

    @field_validator("contact")
    @classmethod
    def _strip_contact(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None


class FeedbackResponse(BaseModel):
    id: int
    created_at: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=FeedbackResponse, status_code=201)
def create_feedback(
    payload: FeedbackCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """创建一条反馈。匿名 OK；letter_id / issue_slug 必须真实存在或为空。"""
    client_ip = (request.client.host if request.client else "unknown")
    _check_rate_limit(client_ip)

    # 校验 letter_id / issue_slug 真实存在（防伪造引用）
    if payload.letter_id is not None:
        if db.get(TestSession, payload.letter_id) is None:
            raise HTTPException(status_code=400, detail="letter_id 不存在")
    if payload.issue_slug is not None:
        exists = (
            db.query(TestResult)
            .filter(TestResult.issue_slug == payload.issue_slug)
            .first()
        )
        if exists is None:
            raise HTTPException(status_code=400, detail="issue_slug 不存在")

    # 转义文本——只是 escape，避免 XSS（虽然反馈不会被 LLM 反吐，但管理后台展示时有用）
    safe_text = escape_user_quote(payload.text)
    safe_contact = escape_user_quote(payload.contact) if payload.contact else None

    fb = Feedback(
        letter_id=payload.letter_id,
        issue_slug=payload.issue_slug,
        rating=payload.rating,
        text=safe_text,
        contact=safe_contact,
        user_agent=request.headers.get("user-agent", "")[:500],
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)

    return FeedbackResponse(
        id=fb.id,
        created_at=fb.created_at.isoformat(),
    )
