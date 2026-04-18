"""v2.4 smoke tests · 保证核心 happy path 不炸。"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from oriself_server.guardrails import (
    parse_status_sentinel,
    verify_report_html_consistency,
    verify_report_html_shape,
)
from oriself_server.llm_client import Message, make_backend
from oriself_server.schemas import ConvergeOutput, UserPreferences
from oriself_server.skill_loader import load_skill_bundle
from oriself_server.skill_runner import (
    ReportRunner,
    SessionState,
    TurnRunner,
    advance_state,
    choose_phase_key,
)


# ---------------------------------------------------------------------------
# STATUS sentinel
# ---------------------------------------------------------------------------


def test_status_parse_basic():
    p = parse_status_sentinel("你好\n\nSTATUS: CONTINUE")
    assert p.status == "CONTINUE"
    assert p.status_explicit is True
    assert p.visible_text == "你好"


def test_status_parse_converge():
    p = parse_status_sentinel("结束吧\nSTATUS: CONVERGE\n")
    assert p.status == "CONVERGE"
    assert p.visible_text == "结束吧"


def test_status_parse_missing_defaults_continue():
    p = parse_status_sentinel("没写 status 行")
    assert p.status == "CONTINUE"
    assert p.status_explicit is False


def test_status_lowercase_not_recognized():
    p = parse_status_sentinel("正文\nstatus: continue")
    assert p.status == "CONTINUE"
    assert p.status_explicit is False


# ---------------------------------------------------------------------------
# Guardrails · report_html
# ---------------------------------------------------------------------------


def test_html_shape_rejects_script():
    r = verify_report_html_shape("<html><body><script>alert(1)</script></body></html>")
    assert not r.passed
    assert any("script" in reason.lower() for reason in r.reasons)


def test_html_shape_rejects_event_handler():
    r = verify_report_html_shape('<html><body><div onclick="evil()">x</div></body></html>')
    assert not r.passed


def test_html_shape_accepts_clean():
    r = verify_report_html_shape(
        "<!DOCTYPE html><html><body><p>clean</p></body></html>"
    )
    assert r.passed


def test_html_consistency_mismatch():
    html = "<html><title>INFJ</title><body>The INFP one</body></html>"
    r = verify_report_html_consistency(html, "INFJ")
    assert not r.passed


def test_html_consistency_ok():
    html = "<html><title>INFJ</title><body>INFJ consistent</body></html>"
    r = verify_report_html_consistency(html, "INFJ")
    assert r.passed


# ---------------------------------------------------------------------------
# Phase picker
# ---------------------------------------------------------------------------


def test_phase_r1_onboarding():
    s = SessionState(session_id="x", domain="mbti")
    assert choose_phase_key(s, 1) == "phase0-onboarding"


def test_phase_midpoint():
    prefs = UserPreferences(target_rounds=20)
    s = SessionState(session_id="x", domain="mbti", user_preferences=prefs)
    assert choose_phase_key(s, 10) == "phase3_5-midpoint"


def test_phase_soft_closing():
    prefs = UserPreferences(target_rounds=20)
    s = SessionState(session_id="x", domain="mbti", user_preferences=prefs)
    assert choose_phase_key(s, 18) == "phase4_8-soft-closing"


# ---------------------------------------------------------------------------
# Mock stream + compose end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_stream_turn_emits_status():
    backend = make_backend("mock")
    bundle = load_skill_bundle()
    runner = TurnRunner(backend=backend, bundle=bundle)
    session = SessionState(session_id=str(uuid.uuid4()), domain="mbti")

    status = "?"
    visible = ""
    async for kind, payload in runner.stream_turn(session, "嗨"):
        if kind == "status":
            status = payload
        elif kind == "visible":
            visible = payload

    assert status in ("CONTINUE", "CONVERGE", "NEED_USER")
    assert visible
    assert "STATUS" not in visible  # sentinel 已剥


@pytest.mark.asyncio
async def test_mock_compose_report():
    backend = make_backend("mock")
    bundle = load_skill_bundle()
    runner = TurnRunner(backend=backend, bundle=bundle)
    reporter = ReportRunner(backend=backend, bundle=bundle)
    session = SessionState(session_id=str(uuid.uuid4()), domain="mbti")

    # 跑 3 轮
    for i in range(3):
        visible = ""
        status = "CONTINUE"
        async for kind, payload in runner.stream_turn(session, f"第 {i+1} 轮用户回复"):
            if kind == "visible":
                visible = payload
            elif kind == "status":
                status = payload
        session = advance_state(session, f"第 {i+1} 轮用户回复", visible, status)

    result = await reporter.compose(session)
    assert result.output is not None
    assert result.output.mbti_type
    assert len(result.output.report_html) >= 1000
    assert result.output.mbti_type in result.output.report_html
