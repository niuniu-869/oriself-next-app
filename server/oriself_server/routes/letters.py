"""
FastAPI routes · 对话（信件）管理。

提供：
- `POST /letters` · 创建新的信件（会话）
- `POST /letters/{id}/turn` · 每轮对话
- `GET /letters/{id}/state` · 当前状态
- `GET /letters/{id}/result` · 收敛后的结果（内部，含 issue slug）

对外品牌统一用 "letter"（一封信），内部 DB / 代码层沿用 "session"。
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_sessionmaker
from ..guardrails import OriSelfGuardrails, SessionState, Turn
from ..llm_client import make_backend
from ..models import Conversation, EvidenceRecord, TestResult, TestSession
from ..schemas import Evidence
from ..skill_loader import load_skill_bundle
from ..skill_runner import SkillRunner
from ..utils.html_sanitize import escape_user_quote, sanitize_report_html


def _generate_issue_slug(mbti_type: str) -> str:
    """生成公开分享 URL 的 slug，形如 'intj-a94b2c'。

    格式 = {mbti 小写}-{6 位 hex}，16^6 = 1.67e7 种组合；
    DB 层 UNIQUE 约束保底，极端情况重试。
    """
    return f"{mbti_type.lower()}-{secrets.token_hex(3)}"


router = APIRouter(prefix="/letters", tags=["letters"])


# ---------------------------------------------------------------------------
# Request / Response
# ---------------------------------------------------------------------------


class CreateLetterRequest(BaseModel):
    provider: str = Field(default_factory=lambda: os.environ.get("ORISELF_PROVIDER", "mock"))
    domain: str = "mbti"


class CreateLetterResponse(BaseModel):
    letter_id: str
    provider: str
    domain: str
    skill_version: str


class TurnRequest(BaseModel):
    user_message: str = Field(min_length=1, max_length=4000)


class TurnResponse(BaseModel):
    round_number: int
    action: dict
    used_fallback: bool
    retries: int
    guardrail_reasons: List[str] = Field(default_factory=list)


class StateResponse(BaseModel):
    letter_id: str
    round_count: int
    status: str
    evidence_count_per_dim: dict


class ResultResponse(BaseModel):
    letter_id: str
    mbti_type: str
    insight_paragraphs: list
    card: dict
    issue_slug: Optional[str] = None  # 收敛后生成的报告 slug（若已生成）


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


def _load_session_state(db: Session, session_id: str) -> SessionState:
    sess = db.get(TestSession, session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    convs = (
        db.query(Conversation)
        .filter(Conversation.session_id == session_id)
        .order_by(Conversation.round_number.asc())
        .all()
    )
    turns: List[Turn] = []
    for c in convs:
        ev: List[Evidence] = []
        if c.action_json:
            try:
                data = json.loads(c.action_json)
                for e in data.get("evidence", []):
                    ev.append(Evidence.model_validate(e))
            except Exception:
                ev = []
        turns.append(
            Turn(
                round_number=c.round_number,
                user_message=c.user_message,
                action_type=c.action_type,
                evidence=ev,
            )
        )
    collected: List[Evidence] = []
    for rec in db.query(EvidenceRecord).filter(EvidenceRecord.session_id == session_id).all():
        collected.append(
            Evidence(
                dimension=rec.dimension,
                user_quote=rec.user_quote,
                round_number=rec.round_number,
                confidence=rec.confidence or 0.5,
                interpretation=rec.interpretation or "",
            )
        )
    return SessionState(
        session_id=session_id,
        domain=sess.domain,
        turns=turns,
        collected_evidence=collected,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=CreateLetterResponse)
def create_letter(req: CreateLetterRequest, db: Session = Depends(get_db)):
    sess = TestSession(provider=req.provider, domain=req.domain)
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return CreateLetterResponse(
        letter_id=sess.session_id,
        provider=sess.provider,
        domain=sess.domain,
        skill_version=sess.skill_version,
    )


@router.post("/{letter_id}/turn", response_model=TurnResponse)
async def take_turn(
    letter_id: str, req: TurnRequest, db: Session = Depends(get_db)
):
    session_id = letter_id  # 内部沿用 session_id 变量名
    sess = db.get(TestSession, session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    if sess.status == "completed":
        raise HTTPException(status_code=400, detail="session already completed")

    state = _load_session_state(db, session_id)

    bundle = load_skill_bundle()
    backend = make_backend(sess.provider)
    runner = SkillRunner(backend=backend, bundle=bundle)
    result = await runner.step(state, req.user_message)

    round_number = state.round_count + 1

    # 幂等性：若这一 round_number 已存在，拒绝（UNIQUE 约束保证，但显式 check 更友好）
    existing = (
        db.query(Conversation)
        .filter(
            Conversation.session_id == session_id,
            Conversation.round_number == round_number,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"round {round_number} already exists (session stale?)",
        )

    conv = Conversation(
        session_id=session_id,
        round_number=round_number,
        user_message=req.user_message,
        action_json=result.action.model_dump_json(),
        action_type=result.action.action,
        dimension_targeted=result.action.dimension_targeted,
        turn_state="saved",
        retry_count=result.retries,
    )
    db.add(conv)
    for ev in result.action.evidence:
        db.add(
            EvidenceRecord(
                session_id=session_id,
                round_number=ev.round_number,
                dimension=ev.dimension,
                user_quote=ev.user_quote,
                confidence=ev.confidence,
                interpretation=ev.interpretation or "",
            )
        )

    # 若 action=converge，写 TestResult + 标记 session 完成
    if result.action.action == "converge" and result.action.converge_output:
        co = result.action.converge_output
        card_json = co.card.model_dump()
        # 对 pull_quotes 做 escape
        for pq in card_json.get("pull_quotes", []):
            pq["text"] = escape_user_quote(pq.get("text", ""))

        # 生成唯一 slug（最多重试 3 次，概率上够用）
        slug = None
        for _ in range(3):
            candidate = _generate_issue_slug(co.mbti_type)
            if not db.query(TestResult).filter(
                TestResult.issue_slug == candidate
            ).first():
                slug = candidate
                break
        if slug is None:
            slug = _generate_issue_slug(co.mbti_type)  # 祈祷一下

        # 二次 sanitize（skill prompt 第一道 + schema 校验第二道 + iframe sandbox 第四道）
        safe_html = sanitize_report_html(co.report_html)

        db.add(
            TestResult(
                session_id=session_id,
                mbti_type=co.mbti_type,
                insight_json=json.dumps(
                    [p.model_dump() for p in co.insight_paragraphs],
                    ensure_ascii=False,
                ),
                card_json=json.dumps(card_json, ensure_ascii=False),
                # Issue 字段 · 用户收敛后就能拿到公开链接
                issue_slug=slug,
                issue_title=co.card.title,
                issue_html=safe_html,
                issue_is_public=True,  # MVP 默认公开，用户可通过 PATCH 收紧
                issue_generated_at=datetime.now(timezone.utc),
            )
        )
        sess.status = "completed"

    db.commit()

    return TurnResponse(
        round_number=round_number,
        action=result.action.model_dump(),
        used_fallback=result.used_fallback,
        retries=result.retries,
        guardrail_reasons=result.guardrail_reasons,
    )


@router.get("/{letter_id}/state", response_model=StateResponse)
def get_state(letter_id: str, db: Session = Depends(get_db)):
    sess = db.get(TestSession, letter_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="letter not found")
    state = _load_session_state(db, letter_id)
    counts: dict[str, int] = {"E/I": 0, "S/N": 0, "T/F": 0, "J/P": 0}
    seen: set[tuple[str, str]] = set()
    for ev in state.collected_evidence:
        key = (ev.dimension, ev.user_quote)
        if key in seen:
            continue
        seen.add(key)
        if ev.dimension in counts:
            counts[ev.dimension] += 1
    return StateResponse(
        letter_id=letter_id,
        round_count=state.round_count,
        status=sess.status,
        evidence_count_per_dim=counts,
    )


@router.get("/{letter_id}/result", response_model=ResultResponse)
def get_result(letter_id: str, db: Session = Depends(get_db)):
    result = db.query(TestResult).filter(TestResult.session_id == letter_id).first()
    if result is None:
        raise HTTPException(status_code=404, detail="result not ready")
    return ResultResponse(
        letter_id=letter_id,
        mbti_type=result.mbti_type,
        insight_paragraphs=json.loads(result.insight_json),
        card=json.loads(result.card_json),
        issue_slug=result.issue_slug,
    )
