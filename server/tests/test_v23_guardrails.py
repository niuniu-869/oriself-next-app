"""v2.3 新增逻辑的专项测试。

覆盖：
- Evidence round_number 放开硬锁（诚实回引历史轮合法）
- ConvergeOutput.mbti_type 由 confidence_per_dim 派生并覆盖
- verify_report_html_consistency 抓 HTML 里 4 字母飘移
- fallback_action 的 deficit-aware seeding
"""
from __future__ import annotations

import pytest

from oriself_server.guardrails import (
    OriSelfGuardrails,
    SessionState,
    Turn,
    _extract_missing_dimensions,
    _pick_deficit_seed,
    fallback_action,
)
from oriself_server.schemas import (
    Action,
    CardData,
    ConvergeOutput,
    Evidence,
    InsightParagraph,
    derive_mbti_type,
)


_VALID_HTML = (
    "<!DOCTYPE html><html lang='zh-CN'><head><title>t</title></head>"
    "<body>" + "<p>填充内容。</p>" * 200 + "</body></html>"
)


# ---------------------------------------------------------------------------
# 1. mbti_type 派生与覆盖
# ---------------------------------------------------------------------------


def test_derive_mbti_type_from_confidence():
    cpd = {
        "E/I": {"letter": "I", "score": 0.8},
        "S/N": {"letter": "N", "score": 0.75},
        "T/F": {"letter": "F", "score": 0.6},
        "J/P": {"letter": "J", "score": 0.7},
    }
    co = ConvergeOutput(
        confidence_per_dim=cpd,
        insight_paragraphs=[
            InsightParagraph(theme=f"t{i}", body="一" * 80, quoted_rounds=[i])
            for i in range(1, 4)
        ],
        card=CardData(title="标题很好", subtitle="sub", pull_quotes=[]),
        report_html=_VALID_HTML.replace("一", "I"),  # 避免含其他 4 字母
    )
    assert co.mbti_type == "INFJ"
    assert co.card.mbti_type == "INFJ"


def test_mbti_type_override_even_if_llm_writes_different():
    """LLM 写错的 mbti_type 会被 confidence_per_dim 派生值覆盖。"""
    cpd = {
        "E/I": {"letter": "I", "score": 0.8},
        "S/N": {"letter": "N", "score": 0.75},
        "T/F": {"letter": "F", "score": 0.6},
        "J/P": {"letter": "J", "score": 0.7},
    }
    co = ConvergeOutput(
        mbti_type="INTP",  # LLM 写错了（和 confidence 里的字母冲突）
        confidence_per_dim=cpd,
        insight_paragraphs=[
            InsightParagraph(theme=f"t{i}", body="一" * 80, quoted_rounds=[i])
            for i in range(1, 4)
        ],
        card=CardData(title="标题很好", mbti_type="INTP", subtitle="sub", pull_quotes=[]),
        report_html=_VALID_HTML,
    )
    # 派生值覆盖 LLM 的错字
    assert co.mbti_type == "INFJ"
    assert co.card.mbti_type == "INFJ"


def test_backward_compat_mbti_type_only():
    """旧 caller 只传 mbti_type 不传 confidence_per_dim → 自动合成。"""
    co = ConvergeOutput(
        mbti_type="INTJ",
        insight_paragraphs=[
            InsightParagraph(theme=f"t{i}", body="一" * 80, quoted_rounds=[i])
            for i in range(1, 4)
        ],
        card=CardData(title="标题很好", mbti_type="INTJ", subtitle="sub", pull_quotes=[]),
        report_html=_VALID_HTML,
    )
    assert co.mbti_type == "INTJ"
    assert co.confidence_per_dim["E/I"].letter == "I"
    assert co.confidence_per_dim["S/N"].letter == "N"


def test_backward_compat_flat_float_confidence():
    """旧 caller 传 flat float confidence_per_dim → 自动升级为 DimResult。"""
    co = ConvergeOutput(
        mbti_type="ENFP",
        confidence_per_dim={"E/I": 0.8, "S/N": 0.7, "T/F": 0.6, "J/P": 0.5},
        insight_paragraphs=[
            InsightParagraph(theme=f"t{i}", body="一" * 80, quoted_rounds=[i])
            for i in range(1, 4)
        ],
        card=CardData(title="标题很好", mbti_type="ENFP", subtitle="sub", pull_quotes=[]),
        report_html=_VALID_HTML,
    )
    assert co.mbti_type == "ENFP"
    assert co.confidence_per_dim["E/I"].score == 0.8
    assert co.confidence_per_dim["E/I"].letter == "E"


# ---------------------------------------------------------------------------
# 2. HTML 字母一致性
# ---------------------------------------------------------------------------


def test_html_consistency_reject_on_mismatch():
    guard = OriSelfGuardrails()
    # HTML 里 title 写 INFJ 但维度区拼出 INFP
    bad_html = (
        "<!DOCTYPE html><html><head><title>INFJ</title></head>"
        "<body><h1>一个 INFJ</h1>"
        "<div>I</div><div>N</div><div>F</div><div>P</div>"
        "<p>拼起来是 INFP</p>"
        + "<p>填充。</p>" * 200
        + "</body></html>"
    )
    r = guard.verify_report_html_consistency(bad_html, "INFJ")
    assert not r.passed
    assert any("INFP" in x for x in r.reasons)


def test_html_consistency_pass_on_match():
    guard = OriSelfGuardrails()
    good_html = (
        "<!DOCTYPE html><html><head><title>INFJ</title></head>"
        "<body><h1>一个把问题还回去的 INFJ</h1>"
        "<footer>INFJ · 2026</footer>"
        + "<p>填充。</p>" * 200
        + "</body></html>"
    )
    r = guard.verify_report_html_consistency(good_html, "INFJ")
    assert r.passed


def test_html_consistency_ignores_dimension_labels():
    """HTML 里 'E / I' 这种单字母 label（空格分开）不该被当成 4 字母 MBTI 串。"""
    guard = OriSelfGuardrails()
    html = (
        "<!DOCTYPE html><html><head><title>INFJ</title></head>"
        "<body><h1>INFJ</h1>"
        "<div class='dim'>E / I</div><div>I</div>"
        "<div class='dim'>S / N</div><div>N</div>"
        + "<p>填充。</p>" * 200
        + "</body></html>"
    )
    r = guard.verify_report_html_consistency(html, "INFJ")
    assert r.passed, f"reasons: {r.reasons}"


# ---------------------------------------------------------------------------
# 3. Evidence round 放开
# ---------------------------------------------------------------------------


def _session_with_turns(messages: list[str]) -> SessionState:
    turns = [
        Turn(round_number=i + 1, user_message=m)
        for i, m in enumerate(messages)
    ]
    return SessionState(session_id="t", domain="mbti", turns=turns)


def test_evidence_allows_historical_round_with_valid_quote():
    guard = OriSelfGuardrails()
    session = _session_with_turns(["R1 原话 abc", "R2 原话 def", "R3 原话 ghi"])
    evs = [
        Evidence(
            dimension="E/I",
            user_quote="R1 原话",
            round_number=1,
            confidence=0.7,
        )
    ]
    r = guard.verify_evidence_grounding(evs, session)
    assert r.passed


def test_evidence_rejects_historical_round_with_wrong_quote():
    guard = OriSelfGuardrails()
    session = _session_with_turns(["R1 原话 abc", "R2 原话 def", "R3 原话 ghi"])
    evs = [
        Evidence(
            dimension="E/I",
            user_quote="根本没出现过",
            round_number=1,
            confidence=0.7,
        )
    ]
    r = guard.verify_evidence_grounding(evs, session)
    assert not r.passed


# ---------------------------------------------------------------------------
# 4. Deficit-aware fallback
# ---------------------------------------------------------------------------


def test_extract_missing_dimensions():
    reasons = [
        "some other reason",
        "convergence not ready: missing dimensions ['E/I', 'J/P'] (E/I=0/3, S/N=3/3, T/F=8/3, J/P=0/3)",
    ]
    dims = _extract_missing_dimensions(reasons)
    assert dims == ["E/I", "J/P"]


def test_fallback_deficit_aware_picks_missing_dim():
    session = _session_with_turns(["hi"] * 5)
    reasons = [
        "convergence not ready: missing dimensions ['E/I'] (E/I=0/3, S/N=5/3, T/F=5/3, J/P=5/3)"
    ]
    action = fallback_action(round_number=19, session=session, reject_reasons=reasons)
    assert action.action == "ask"
    assert action.dimension_targeted == "E/I"
    # 带的 next_prompt 应该来自 E/I seeds（含"一个人"/"跟人"等关键词之一）
    assert any(k in action.next_prompt for k in ("一个人", "跟人", "找人"))


def test_fallback_without_deficit_falls_back_to_default():
    """非维度原因触发的 fallback 走原始 FALLBACK_NEXT_PROMPT。"""
    session = _session_with_turns(["hi"] * 5)
    reasons = ["some other schema error"]
    action = fallback_action(round_number=8, session=session, reject_reasons=reasons)
    assert action.action == "ask"
    assert action.dimension_targeted == "none"
    assert "印象最深" in action.next_prompt
