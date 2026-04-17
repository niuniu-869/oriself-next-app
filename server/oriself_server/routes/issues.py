"""
FastAPI routes · 公开报告（Issue）访问。

Issue 是一封信收敛后由 LLM 生成的完整 HTML 报告——每个 MBTI 类型有完全独立
的视觉设计。slug 形如 `intj-a94b2c`，用户可选择公开后通过
`/issues/{slug}` 分享。

提供：
- `GET /issues/{slug}`            · 元数据（JSON）
- `GET /issues/{slug}/render`     · 完整 HTML 文档，供前端 iframe 沙箱嵌入
- `PATCH /issues/{slug}/publish`  · 切换公开/私有（MVP 不鉴权；生产需 owner token）
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_sessionmaker
from ..models import TestResult


router = APIRouter(prefix="/issues", tags=["issues"])


def get_db():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class IssueResponse(BaseModel):
    slug: str
    title: str
    mbti_type: str
    is_public: bool
    created_at: datetime
    letter_id: Optional[str] = None  # owner 操作（回看对话）入口


class PublishRequest(BaseModel):
    is_public: bool


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/{slug}", response_model=IssueResponse)
def get_issue(slug: str, db: Session = Depends(get_db)):
    """元数据。公开才可访问。"""
    result = (
        db.query(TestResult)
        .filter(TestResult.issue_slug == slug)
        .first()
    )
    if result is None or not result.issue_html:
        raise HTTPException(status_code=404, detail="issue not found")
    if not result.issue_is_public:
        raise HTTPException(status_code=403, detail="issue is private")
    return IssueResponse(
        slug=result.issue_slug,
        title=result.issue_title or result.mbti_type,
        mbti_type=result.mbti_type,
        is_public=result.issue_is_public,
        created_at=result.issue_generated_at or result.created_at,
        letter_id=result.session_id,
    )


@router.get("/{slug}/render")
def render_issue(slug: str, db: Session = Depends(get_db)):
    """
    完整 HTML 文档，供前端 iframe 嵌入。

    安全：LLM 生成的 HTML 不可信，必须通过 iframe sandbox 隔离。
    本端点返回 CSP sandbox 头，禁用 top-level navigation / forms / popups。
    """
    result = (
        db.query(TestResult)
        .filter(TestResult.issue_slug == slug)
        .first()
    )
    if result is None or not result.issue_html:
        return HTMLResponse(
            content="<!doctype html><title>404</title><h1>Issue not found</h1>",
            status_code=404,
        )
    if not result.issue_is_public:
        return HTMLResponse(
            content="<!doctype html><title>403</title><h1>This issue is private</h1>",
            status_code=403,
        )

    # 关键：沙箱化 LLM 生成的 HTML
    headers = {
        "Content-Security-Policy": (
            "sandbox allow-scripts allow-same-origin; "
            "default-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://fonts.gstatic.com data:; "
            "img-src * data:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' data: https://fonts.gstatic.com;"
        ),
        "X-Content-Type-Options": "nosniff",
    }
    return HTMLResponse(content=result.issue_html, headers=headers)


@router.patch("/{slug}/publish", response_model=IssueResponse)
def publish_issue(
    slug: str, req: PublishRequest, db: Session = Depends(get_db)
):
    """
    切换 issue 公开 / 私有。

    MVP 不鉴权——任何知道 slug 的人都能改（slug 本身是只在收敛后返回给 owner
    的）。生产阶段应换成 owner token / JWT。
    """
    result = (
        db.query(TestResult)
        .filter(TestResult.issue_slug == slug)
        .first()
    )
    if result is None or not result.issue_html:
        raise HTTPException(status_code=404, detail="issue not found")
    result.issue_is_public = req.is_public
    db.commit()
    db.refresh(result)
    return IssueResponse(
        slug=result.issue_slug,
        title=result.issue_title or result.mbti_type,
        mbti_type=result.mbti_type,
        is_public=result.issue_is_public,
        created_at=result.issue_generated_at or result.created_at,
        letter_id=result.session_id,
    )
