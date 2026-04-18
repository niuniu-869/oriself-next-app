"""
FastAPI routes · 对话（信件）管理 · v2.4。

对话轮契约从结构化 JSON 改为**流式 Markdown + STATUS sentinel**：
- `POST /letters`                    · 创建新信件
- `POST /letters/{id}/turn`          · SSE 流式对话（token 逐字推）
- `POST /letters/{id}/turn/rewrite`  · 标记最近一轮 discarded 后重新流式
- `POST /letters/{id}/result`        · 触发报告生成（独立 LLM 调用，3 次 retry）
- `GET  /letters/{id}/state`         · 元数据（round_count / status）
- `GET  /letters/{id}/transcript`    · 回看对话（只返非 discarded 的轮）

对外品牌统一用 "letter"；内部 DB / 代码层沿用 "session"。

SSE 事件：
- `event: token`  · data `{"delta": "..."}`           · 一个或多个字符
- `event: done`   · data `{"round": N, "status": "...", "visible": "..."}`
- `event: error`  · data `{"message": "..."}`
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from ..database import get_sessionmaker
from ..llm_client import make_backend
from ..models import Conversation, TestResult, TestSession
from ..schemas import MAX_ROUNDS, UserPreferences
from ..skill_loader import load_skill_bundle
from ..skill_runner import (
    ReportRunner,
    SessionState,
    Turn,
    TurnRunner,
    _parse_preferences_heuristic,
)
from ..utils.html_sanitize import escape_user_quote, sanitize_report_html


router = APIRouter(prefix="/letters", tags=["letters"])


def _generate_issue_slug(mbti_type: str) -> str:
    return f"{mbti_type.lower()}-{secrets.token_hex(3)}"


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


class RewriteRequest(BaseModel):
    hint: Optional[str] = Field(default=None, max_length=500)


class StateResponse(BaseModel):
    letter_id: str
    round_count: int
    status: str             # active | completed | failed
    last_status: Optional[str] = None  # 最近一轮的 STATUS sentinel
    has_report: bool = False
    issue_slug: Optional[str] = None


class TranscriptTurn(BaseModel):
    speaker: str  # "you" | "oriself"
    text: str
    round: int


class TranscriptResponse(BaseModel):
    letter_id: str
    status: str
    turns: List[TranscriptTurn]
    issue_slug: Optional[str] = None


class ResultResponse(BaseModel):
    letter_id: str
    mbti_type: str
    insight_paragraphs: list
    card: dict
    issue_slug: Optional[str] = None


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
        .order_by(Conversation.round_number.asc(), Conversation.id.asc())
        .all()
    )
    turns: List[Turn] = []
    for c in convs:
        turns.append(
            Turn(
                round_number=c.round_number,
                user_message=c.user_message,
                oriself_text=c.oriself_text or "",
                status=c.status_sentinel or "CONTINUE",
                discarded=bool(c.discarded),
            )
        )
    prefs = None
    if sess.prefs_json:
        try:
            prefs = UserPreferences.model_validate_json(sess.prefs_json)
        except Exception:
            prefs = None
    return SessionState(
        session_id=session_id,
        domain=sess.domain,
        turns=turns,
        user_preferences=prefs,
    )


# ---------------------------------------------------------------------------
# Create
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


# ---------------------------------------------------------------------------
# Persist helper · 把流完后的一轮落库
# ---------------------------------------------------------------------------


def _persist_turn(
    db: Session,
    sess: TestSession,
    user_message: str,
    raw_stream: str,
    visible_text: str,
    status: str,
) -> int:
    """把一轮对话写入 DB；顺便更新 session 状态。返回 round_number。"""
    state = _load_session_state(db, sess.session_id)
    round_number = state.round_count + 1

    # 幂等：如果已有活动轮（非 discarded）在这个 round_number，抛 409
    dup = (
        db.query(Conversation)
        .filter(
            Conversation.session_id == sess.session_id,
            Conversation.round_number == round_number,
            Conversation.discarded.is_(False),
        )
        .first()
    )
    if dup is not None:
        raise HTTPException(
            status_code=409,
            detail=f"round {round_number} already exists",
        )

    conv = Conversation(
        session_id=sess.session_id,
        round_number=round_number,
        user_message=user_message,
        oriself_text=visible_text,
        raw_stream=raw_stream,
        status_sentinel=status,
        discarded=False,
    )
    db.add(conv)

    # R2 → 解析 preferences
    if round_number == 2 and not sess.prefs_json:
        prefs = _parse_preferences_heuristic(user_message)
        sess.prefs_json = prefs.model_dump_json()

    db.commit()
    return round_number


# ---------------------------------------------------------------------------
# POST /letters/{id}/turn  ·  SSE 流
# ---------------------------------------------------------------------------


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stream_turn_core(
    db: Session,
    sess: TestSession,
    user_message: str,
    rewrite_hint: Optional[str] = None,
):
    """核心流式 generator · /turn 和 /turn/rewrite 共用。"""
    state = _load_session_state(db, sess.session_id)

    # Round budget 硬拦截：R30 后直接让前端去 /result
    if state.round_count >= MAX_ROUNDS:
        yield _sse("error", {"message": "已到 30 轮硬上限，请前往报告页"})
        return

    bundle = load_skill_bundle()
    backend = make_backend(sess.provider)
    runner = TurnRunner(backend=backend, bundle=bundle)

    raw_accum = ""
    visible = ""
    status = "CONTINUE"
    had_error = False

    try:
        async for kind, payload in runner.stream_turn(
            state, user_message, rewrite_hint=rewrite_hint
        ):
            if kind == "token":
                raw_accum += payload
                # 轻量过滤：不主动剥 STATUS 行（它在末尾，用户可能已经看到一两个字符，
                # 前端也可以做末行剥除）。这里按 gstack 流式规范：先传原文，最后
                # 在 `done` 事件里给最终 visible_text，前端用 visible_text 覆盖。
                yield _sse("token", {"delta": payload})
            elif kind == "error":
                had_error = True
                yield _sse("error", {"message": payload})
                return
            elif kind == "final":
                continue
            elif kind == "status":
                status = payload
            elif kind == "visible":
                visible = payload
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("stream core error")
        yield _sse("error", {"message": f"stream error: {exc}"})
        return

    if had_error:
        return

    # 没收到 visible → 兜底用原文去除 STATUS 行（虽然 runner 的 parser 已经处理过）
    if not visible:
        visible = raw_accum.strip()

    try:
        round_number = _persist_turn(db, sess, user_message, raw_accum, visible, status)
    except HTTPException as he:
        yield _sse("error", {"message": he.detail, "status_code": he.status_code})
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("persist error")
        yield _sse("error", {"message": f"persist error: {exc}"})
        return

    yield _sse("done", {
        "round": round_number,
        "status": status,
        "visible": visible,
    })


@router.post("/{letter_id}/turn")
async def take_turn(
    letter_id: str, req: TurnRequest, db: Session = Depends(get_db)
):
    sess = db.get(TestSession, letter_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    if sess.status == "completed":
        raise HTTPException(status_code=400, detail="session already completed")

    return StreamingResponse(
        _stream_turn_core(db, sess, req.user_message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# POST /letters/{id}/turn/rewrite  ·  重写最近一轮
# ---------------------------------------------------------------------------


@router.post("/{letter_id}/turn/rewrite")
async def rewrite_last_turn(
    letter_id: str, req: RewriteRequest, db: Session = Depends(get_db)
):
    """把最近一轮（非 discarded 的最大 round_number）标记为 discarded，
    然后用相同的 user_message + 一个可选 hint 重新跑一次流。"""
    sess = db.get(TestSession, letter_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    if sess.status == "completed":
        raise HTTPException(status_code=400, detail="session already completed")

    last = (
        db.query(Conversation)
        .filter(
            Conversation.session_id == letter_id,
            Conversation.discarded.is_(False),
        )
        .order_by(Conversation.round_number.desc(), Conversation.id.desc())
        .first()
    )
    if last is None:
        raise HTTPException(status_code=400, detail="no turn to rewrite")

    original_user_message = last.user_message
    last.discarded = True
    db.commit()

    return StreamingResponse(
        _stream_turn_core(db, sess, original_user_message, rewrite_hint=req.hint),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# GET /letters/{id}/state
# ---------------------------------------------------------------------------


@router.get("/{letter_id}/state", response_model=StateResponse)
def get_state(letter_id: str, db: Session = Depends(get_db)):
    sess = db.get(TestSession, letter_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="letter not found")
    state = _load_session_state(db, letter_id)
    last_status = None
    live = state.live_turns()
    if live:
        last_status = live[-1].status

    result = db.query(TestResult).filter(TestResult.session_id == letter_id).first()
    return StateResponse(
        letter_id=letter_id,
        round_count=state.round_count,
        status=sess.status,
        last_status=last_status,
        has_report=result is not None,
        issue_slug=result.issue_slug if result else None,
    )


# ---------------------------------------------------------------------------
# GET /letters/{id}/transcript
# ---------------------------------------------------------------------------


@router.get("/{letter_id}/transcript", response_model=TranscriptResponse)
def get_transcript(letter_id: str, db: Session = Depends(get_db)):
    sess = db.get(TestSession, letter_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="letter not found")

    convs = (
        db.query(Conversation)
        .filter(
            Conversation.session_id == letter_id,
            Conversation.discarded.is_(False),
        )
        .order_by(Conversation.round_number.asc())
        .all()
    )

    turns: List[TranscriptTurn] = []
    for c in convs:
        if c.user_message:
            turns.append(TranscriptTurn(speaker="you", text=c.user_message, round=c.round_number))
        if c.oriself_text:
            turns.append(TranscriptTurn(speaker="oriself", text=c.oriself_text, round=c.round_number))

    issue_slug: Optional[str] = None
    tr = db.query(TestResult).filter(TestResult.session_id == letter_id).first()
    if tr is not None:
        issue_slug = tr.issue_slug

    return TranscriptResponse(
        letter_id=letter_id,
        status=sess.status,
        turns=turns,
        issue_slug=issue_slug,
    )


# ---------------------------------------------------------------------------
# POST /letters/{id}/result  ·  触发报告生成
# ---------------------------------------------------------------------------


@router.post("/{letter_id}/result", response_model=ResultResponse)
async def compose_result(letter_id: str, db: Session = Depends(get_db)):
    """触发报告生成。已生成则直接返回；没有则 LLM 跑一次 CONVERGE.md（最多 3 次 retry）。"""
    sess = db.get(TestSession, letter_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="letter not found")

    existing = db.query(TestResult).filter(TestResult.session_id == letter_id).first()
    if existing is not None:
        return ResultResponse(
            letter_id=letter_id,
            mbti_type=existing.mbti_type,
            insight_paragraphs=json.loads(existing.insight_json),
            card=json.loads(existing.card_json),
            issue_slug=existing.issue_slug,
        )

    state = _load_session_state(db, letter_id)
    if state.round_count < 2:
        raise HTTPException(status_code=400, detail="对话轮数太少，还没法写报告")

    bundle = load_skill_bundle()
    backend = make_backend(sess.provider)
    runner = ReportRunner(backend=backend, bundle=bundle)

    result = await runner.compose(state)
    if result.output is None:
        sess.status = "failed"
        db.commit()
        raise HTTPException(
            status_code=502,
            detail={
                "message": "报告生成失败，请稍后重试",
                "reasons": result.error_reasons[:3],
            },
        )

    co = result.output

    # 生成唯一 slug
    slug = None
    for _ in range(3):
        candidate = _generate_issue_slug(co.mbti_type)
        if not db.query(TestResult).filter(TestResult.issue_slug == candidate).first():
            slug = candidate
            break
    if slug is None:
        slug = _generate_issue_slug(co.mbti_type)

    # 清洗 pull_quotes + HTML
    card_json = co.card.model_dump()
    for pq in card_json.get("pull_quotes", []):
        pq["text"] = escape_user_quote(pq.get("text", ""))
    safe_html = sanitize_report_html(co.report_html)

    insight_list = [p.model_dump() for p in co.insight_paragraphs]
    confidence = {dim: r.model_dump() for dim, r in co.confidence_per_dim.items()}

    db.add(
        TestResult(
            session_id=letter_id,
            mbti_type=co.mbti_type,
            insight_json=json.dumps(insight_list, ensure_ascii=False),
            card_json=json.dumps(card_json, ensure_ascii=False),
            confidence_json=json.dumps(confidence, ensure_ascii=False),
            issue_slug=slug,
            issue_title=co.card.title,
            issue_html=safe_html,
            issue_is_public=True,
            issue_generated_at=datetime.now(timezone.utc),
        )
    )
    sess.status = "completed"
    db.commit()

    return ResultResponse(
        letter_id=letter_id,
        mbti_type=co.mbti_type,
        insight_paragraphs=insight_list,
        card=card_json,
        issue_slug=slug,
    )
