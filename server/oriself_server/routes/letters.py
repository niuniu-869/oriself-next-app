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


class TranscriptTurn(BaseModel):
    """单条可见 turn — 给 /transcript 用。

    一轮对话会拆成两条：先 you（用户原话）、再 oriself（可见回应）。
    converge 轮如果没有可见文本，留一句兜底 "信收束了，正在写报告……"。
    """

    speaker: str  # "you" | "oriself"
    text: str
    round: int


class TranscriptResponse(BaseModel):
    letter_id: str
    status: str
    turns: List[TranscriptTurn]
    issue_slug: Optional[str] = None  # 已 converge 时给个直达报告的链接


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


def _persist_turn(
    db: Session,
    sess: TestSession,
    user_message: str,
    result,  # SkillRunner 的 TurnResult，避免循环 import 这里用 rt 类型省略
) -> TurnResponse:
    """把 runner 产出写入 DB，处理 converge 分支，返回对外响应。

    幂等性：同一 round_number 已存在时抛 409。converge 时附带生成 issue。
    抽成独立函数让同步 / SSE 两条路径都能复用。
    """
    session_id = sess.session_id
    state = _load_session_state(db, session_id)
    round_number = state.round_count + 1

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
        user_message=user_message,
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

    if result.action.action == "converge" and result.action.converge_output:
        co = result.action.converge_output
        card_json = co.card.model_dump()
        for pq in card_json.get("pull_quotes", []):
            pq["text"] = escape_user_quote(pq.get("text", ""))

        slug = None
        for _ in range(3):
            candidate = _generate_issue_slug(co.mbti_type)
            if not db.query(TestResult).filter(
                TestResult.issue_slug == candidate
            ).first():
                slug = candidate
                break
        if slug is None:
            slug = _generate_issue_slug(co.mbti_type)

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
                issue_slug=slug,
                issue_title=co.card.title,
                issue_html=safe_html,
                issue_is_public=True,
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

    return _persist_turn(db, sess, req.user_message, result)


@router.post("/{letter_id}/turn/stream")
async def take_turn_stream(
    letter_id: str, req: TurnRequest, db: Session = Depends(get_db)
):
    """SSE 版本的 /turn。

    事件（`event: <name>`）：
    - `phase`：runner 阶段推进（listening → thinking → validating → composed）。
      data = `{"phase": "...", ...extras}`
    - `final`：正式 TurnResponse。data 与同步版 /turn 一致。
    - `error`：失败中断。data = `{"message": "..."}`。

    设计：runner 在后台 task 跑，phase 回调把事件塞进 asyncio.Queue；
    主 generator 边 drain queue 边 yield。期间定期 emit 注释行作心跳，
    避免中间代理（Vercel / nginx）buffer 或断线。
    """
    sess = db.get(TestSession, letter_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    if sess.status == "completed":
        raise HTTPException(status_code=400, detail="session already completed")

    state = _load_session_state(db, letter_id)

    bundle = load_skill_bundle()
    backend = make_backend(sess.provider)
    runner = SkillRunner(backend=backend, bundle=bundle)

    queue: asyncio.Queue = asyncio.Queue()

    async def on_phase(phase: str, data: dict) -> None:
        await queue.put({"event": "phase", "data": {"phase": phase, **data}})

    async def runner_task():
        try:
            result = await runner.step(state, req.user_message, on_phase=on_phase)
            await queue.put({"event": "__result__", "data": result})
        except Exception as exc:  # noqa: BLE001 — 把异常平移给 generator
            logger.exception("runner failure in stream")
            await queue.put({"event": "__error__", "data": {"message": str(exc)}})

    def sse_pack(event: str, payload: dict) -> str:
        # SSE 规范：event 单行 + data 单行（或多行 data:）+ 空行分隔。
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def event_stream():
        task = asyncio.create_task(runner_task())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=10.0)
                except asyncio.TimeoutError:
                    # 心跳 · 注释行 SSE 规范里会被客户端忽略但保持连接
                    yield ": heartbeat\n\n"
                    continue

                name = item["event"]
                if name == "__result__":
                    result = item["data"]
                    try:
                        turn_response = _persist_turn(db, sess, req.user_message, result)
                    except HTTPException as he:
                        yield sse_pack("error", {"message": he.detail, "status": he.status_code})
                        return
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("persist failure in stream")
                        yield sse_pack("error", {"message": f"persist error: {exc}"})
                        return
                    yield sse_pack("final", turn_response.model_dump())
                    return
                if name == "__error__":
                    yield sse_pack("error", item["data"])
                    return
                yield sse_pack(name, item["data"])
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # 关掉 nginx/ingress buffer
            "Connection": "keep-alive",
        },
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


@router.get("/{letter_id}/transcript", response_model=TranscriptResponse)
def get_transcript(letter_id: str, db: Session = Depends(get_db)):
    """回看一封信的完整对话。

    设计：state 端点保持轻量（只回 round 计数 / evidence 维度），不塞历史。
    历史用单独 endpoint，前端只在"回看对话"场景拉一次。
    """
    sess = db.get(TestSession, letter_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="letter not found")

    convs = (
        db.query(Conversation)
        .filter(Conversation.session_id == letter_id)
        .order_by(Conversation.round_number.asc())
        .all()
    )

    turns: List[TranscriptTurn] = []
    for c in convs:
        # user 段
        if c.user_message:
            turns.append(
                TranscriptTurn(
                    speaker="you",
                    text=c.user_message,
                    round=c.round_number,
                )
            )
        # oriself 段 — 从 action_json 抽可见文本，逻辑与前端 letter-view 兜底一致
        visible: Optional[str] = None
        if c.action_json:
            try:
                action = json.loads(c.action_json)
            except Exception:
                action = {}
            for k in ("next_prompt", "next_question", "echo", "text"):
                v = action.get(k)
                if v and isinstance(v, str) and v.strip():
                    visible = v.strip()
                    break
            if visible is None and action.get("action") == "converge":
                visible = "信收束了，正在写报告……"
        if visible:
            turns.append(
                TranscriptTurn(
                    speaker="oriself",
                    text=visible,
                    round=c.round_number,
                )
            )

    # 已 converge 时附带 issue_slug，前端可挂个 "看报告" 入口
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
