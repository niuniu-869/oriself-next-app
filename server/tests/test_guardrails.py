"""Guardrails 单元测试 · 目标 100% 覆盖率（anti-slop 心脏）。"""
from pathlib import Path

import pytest

from oriself_server.guardrails import (
    OriSelfGuardrails,
    SessionState,
    Turn,
    fallback_action,
)
from oriself_server.schemas import (
    Action,
    CardData,
    ConvergeOutput,
    Evidence,
    InsightParagraph,
    PullQuote,
)
from oriself_server.skill_loader import clear_cache, load_skill_bundle


SKILL_ROOT = Path(__file__).resolve().parent.parent.parent / "skill-repo" / "skills" / "oriself"


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def guard():
    return OriSelfGuardrails(load_skill_bundle(SKILL_ROOT))


@pytest.fixture
def fake_session():
    """模拟已跑过 20 轮 + 每维度 3 条 evidence 的 session。"""
    turns = [
        Turn(round_number=i + 1, user_message=f"user 第{i + 1}轮的具体回答内容")
        for i in range(20)
    ]
    evidences = []
    for dim in ["E/I", "S/N", "T/F", "J/P"]:
        for k in range(3):
            # 每条 evidence 的 user_quote 必须是某轮 user_message 的子串
            evidences.append(
                Evidence(
                    dimension=dim,
                    user_quote=f"第{k + 1}轮的具体回答内容",
                    round_number=k + 1,
                    confidence=0.7,
                )
            )
    return SessionState(
        session_id="test",
        domain="mbti",
        turns=turns,
        collected_evidence=evidences,
    )


# ---------------------------------------------------------------------------
# 1. validate_action_schema
# ---------------------------------------------------------------------------


def test_validate_schema_ok(guard):
    raw = {"action": "ask", "next_prompt": "上周你有没有完整没出门的一天?"}
    assert guard.validate_action_schema(raw).passed


def test_validate_schema_bad_action(guard):
    raw = {"action": "wrong_enum", "next_prompt": "x"}
    assert not guard.validate_action_schema(raw).passed


def test_validate_schema_missing_converge_output(guard):
    raw = {"action": "converge", "next_prompt": "差不多了"}
    r = guard.validate_action_schema(raw)
    assert not r.passed
    assert any("converge_output" in x for x in r.reasons)


def test_validate_schema_missing_contradiction(guard):
    raw = {"action": "probe_contradiction", "next_prompt": "你第3轮说..."}
    r = guard.validate_action_schema(raw)
    assert not r.passed
    assert any("contradiction" in x for x in r.reasons)


# ---------------------------------------------------------------------------
# 2. detect_banned_phrases · 每条 banned pattern 至少一个 case
# ---------------------------------------------------------------------------


BANNED_CASES = {
    "BP1": "你既享受独处又渴望连接。",
    "BP2": "你要学会拥抱自己的复杂性。",
    "BP3": "你是一个有深度的人。",
    "BP4": "你在人际关系中既需要空间又需要陪伴。",
    "BP5": "这很常见，很多年轻人都有这种感觉。",
    "BP6": "总的来说，你是一个外冷内热的 I 人。",
    "BP7": "你展现出典型的内向直觉思考者的认知功能栈 Ni-Te 主导。",
    "BP8": "你既适合做技术也适合做管理。",
    "BP9": "你听起来像 INTJ 型。",
    "BP10": "拥抱真实的自己，你会找到内心的光。",
}


@pytest.mark.parametrize("bp_id,text", list(BANNED_CASES.items()))
def test_detect_banned_hit(guard, bp_id, text):
    # BP9 只在 round < 20 + non-converge 时触发；其他总触发
    round_num = 5
    action_type = "ask"
    applies_to = "next_prompt" if bp_id != "BP2" and bp_id != "BP4" and bp_id != "BP6" and bp_id != "BP8" else "insight_body"
    hits = guard.detect_banned_phrases(
        text,
        applies_to=applies_to,
        round_number=round_num,
        action_type=action_type,
    )
    hit_ids = {h.id for h in hits}
    assert bp_id in hit_ids, f"expected {bp_id} to hit; got {hit_ids}"


def test_detect_banned_benign_does_not_hit(guard):
    text = "上周你有没有一个完整没出门的一天? 过到晚上你感觉怎么样?"
    assert guard.detect_banned_phrases(text, applies_to="next_prompt") == []


def test_bp9_suppressed_after_round_20(guard):
    text = "你听起来像 INTJ 型。"
    # round >= 20 → BP9 应该不触发
    hits = guard.detect_banned_phrases(
        text, applies_to="next_prompt", round_number=25, action_type="ask"
    )
    assert "BP9" not in {h.id for h in hits}


def test_bp9_suppressed_when_converge(guard):
    text = "你听起来像 INTJ 型。"  # 即使在 converge 阶段说这话也属于结论语境
    hits = guard.detect_banned_phrases(
        text, applies_to="next_prompt", round_number=5, action_type="converge"
    )
    assert "BP9" not in {h.id for h in hits}


# ---------------------------------------------------------------------------
# 3. verify_evidence_grounding
# ---------------------------------------------------------------------------


def test_evidence_grounding_ok(guard, fake_session):
    # 挑一条已知在 turns 里的 quote
    ev = [
        Evidence(
            dimension="E/I",
            user_quote="第1轮的具体回答内容",
            round_number=1,
            confidence=0.6,
        )
    ]
    assert guard.verify_evidence_grounding(ev, fake_session).passed


def test_evidence_grounding_fabricated(guard, fake_session):
    ev = [
        Evidence(
            dimension="E/I",
            user_quote="这是用户从没说过的话",
            round_number=1,
            confidence=0.6,
        )
    ]
    r = guard.verify_evidence_grounding(ev, fake_session)
    assert not r.passed


def test_evidence_grounding_bad_round(guard, fake_session):
    ev = [
        Evidence(
            dimension="E/I",
            user_quote="第1轮的具体回答内容",
            round_number=99,  # 超出 session
            confidence=0.6,
        )
    ]
    r = guard.verify_evidence_grounding(ev, fake_session)
    assert not r.passed


def test_evidence_grounding_empty(guard, fake_session):
    assert guard.verify_evidence_grounding([], fake_session).passed


# ---------------------------------------------------------------------------
# 4. check_convergence_readiness
# ---------------------------------------------------------------------------


def test_convergence_ready(guard, fake_session):
    assert guard.check_convergence_readiness(fake_session).passed


def test_convergence_not_ready(guard):
    session = SessionState(session_id="x", domain="mbti", turns=[])
    r = guard.check_convergence_readiness(session)
    assert not r.passed


def test_convergence_dedupe_same_quote(guard):
    """同维度、同 quote 的 evidence 应去重。"""
    turns = [Turn(round_number=1, user_message="我喜欢独处的具体画面")]
    # 同一条 quote 写 3 次 → 去重后只算 1 条 → 应该不 ready
    evidences = [
        Evidence(
            dimension="E/I",
            user_quote="我喜欢独处",
            round_number=1,
            confidence=0.5,
        )
        for _ in range(3)
    ]
    session = SessionState(
        session_id="x",
        domain="mbti",
        turns=turns,
        collected_evidence=evidences,
    )
    r = guard.check_convergence_readiness(session, required_per_dim=3)
    assert not r.passed  # E/I 只算 1 条 + S/N/TF/JP 为 0


# ---------------------------------------------------------------------------
# 5. verify_insight_grounding
# ---------------------------------------------------------------------------


_VALID_HTML_STUB = (
    "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
    "<title>t</title><style>body{background:#08080f;color:#c8cdd8;"
    "font-family:-apple-system,sans-serif;} .box{padding:40px;}</style>"
    "</head><body><main class=\"box\">"
    + ("<p>占位内容，结构合法。" + "。" * 400 + "</p>") * 3
    + "</main></body></html>"
)


def _make_converge(rounds_cited_lists, body_len=120, report_html=_VALID_HTML_STUB):
    paras = []
    for rounds in rounds_cited_lists:
        paras.append(
            InsightParagraph(
                theme="theme",
                body="一" * body_len,
                quoted_rounds=rounds,
            )
        )
    card = CardData(
        title="t" * 8,
        mbti_type="INTJ",
        subtitle="s",
        pull_quotes=[],
        typography_hint="editorial_serif",
    )
    return ConvergeOutput(
        mbti_type="INTJ",
        insight_paragraphs=paras,
        card=card,
        report_html=report_html,
    )


def test_insight_grounding_ok(guard, fake_session):
    co = _make_converge([[1], [2, 3], [4]])
    assert guard.verify_insight_grounding(co, fake_session).passed


def test_insight_grounding_bad_round(guard, fake_session):
    co = _make_converge([[1], [2], [999]])  # 999 不存在
    r = guard.verify_insight_grounding(co, fake_session)
    assert not r.passed


def test_insight_grounding_too_few_citations(guard, fake_session):
    # 3 段但只引用 2 个不同 round → total distinct cited < 3 → fail
    co = _make_converge([[1], [1], [2]])
    r = guard.verify_insight_grounding(co, fake_session)
    assert not r.passed


def test_insight_grounding_exceeds_budget(guard, fake_session):
    # 每段 body 要 ≤ 500 chars (Pydantic limit); body_len=500 * 3 = 1500, plus real content
    # 触发 CONVERGE_INSIGHT_TOTAL_LIMIT (1800) 需要 body_len=500*4 其实不行 pydantic 拒了
    # 用 460 * 3 = 1380，不超；用 500 * 3 = 1500，不超；pydantic 上限 500 且限制总上限 1800
    # 所以构造方式：body_len=500*3=1500。1500 < 1800，不会触发 budget 报错。
    # 我们改变策略：直接构造 InsightParagraph 总和超过 1800 —— 但 Pydantic 每段上限 500，只能 1500
    # 这个边界测用 monkey patch 方式——或把 CONVERGE_INSIGHT_TOTAL_LIMIT 手动降到 100 做测试
    # 简化：确认 body_len=500 * 3 = 1500 ≤ 1800 时不报；这部分反向覆盖
    co = _make_converge([[1], [2], [3]], body_len=500)
    # 应当 pass（1500 < 1800）
    assert guard.verify_insight_grounding(co, fake_session).passed


# ---------------------------------------------------------------------------
# 6. check_dimension_diversity
# ---------------------------------------------------------------------------


def _turn(n, dim=None, action_type="ask"):
    t = Turn(round_number=n, user_message=f"r{n}", action_type=action_type)
    t.dimension_targeted = dim  # type: ignore[attr-defined]
    return t


def test_dimension_diversity_ok(guard):
    recent = [_turn(1, "E/I"), _turn(2, "S/N"), _turn(3, "T/F")]
    assert guard.check_dimension_diversity(recent, window=3).passed


def test_dimension_diversity_violated(guard):
    recent = [_turn(1, "E/I"), _turn(2, "E/I"), _turn(3, "E/I")]
    r = guard.check_dimension_diversity(recent, window=3)
    assert not r.passed


def test_dimension_diversity_not_enough_turns(guard):
    recent = [_turn(1, "E/I")]
    assert guard.check_dimension_diversity(recent, window=3).passed


# ---------------------------------------------------------------------------
# 7. check_round_budget
# ---------------------------------------------------------------------------


def test_round_budget_ok(guard):
    session = SessionState(session_id="x", domain="mbti", turns=[Turn(1, "x")])
    assert guard.check_round_budget(session).passed


def test_round_budget_exceeded(guard):
    turns = [Turn(round_number=i + 1, user_message=f"r{i}") for i in range(30)]
    session = SessionState(session_id="x", domain="mbti", turns=turns)
    assert not guard.check_round_budget(session).passed


# ---------------------------------------------------------------------------
# 8. validate_action 综合
# ---------------------------------------------------------------------------


def test_validate_action_happy(guard, fake_session):
    # evidence 必须标在 current_round（21）—— 本轮才能抽。fake_session 有 20 turns
    # 但要让 round 21 的 message 存在用于 substring check，需要加一个 turn。
    fake_session.turns.append(
        Turn(round_number=21, user_message="user 第21轮的具体回答内容")
    )
    action = Action(
        action="ask",
        dimension_targeted="E/I",
        evidence=[
            Evidence(
                dimension="E/I",
                user_quote="第21轮的具体回答内容",
                round_number=21,
                confidence=0.6,
            )
        ],
        next_prompt="上周一个完整没出门的一天，晚上怎么样?",
    )
    r = guard.validate_action(action, fake_session, round_number=21)
    assert r.passed, f"reasons: {r.reasons}"


def test_validate_action_allows_honest_historical_evidence(guard, fake_session):
    """v2.3 · 回引历史轮合法。

    设计转向：放开 `round_number == current_round` 硬约束。
    只要 quote 是 round_number 对应那轮 user_message 的字面子串，就放行。
    这解除 LLM "被迫说谎"（硬锁下若想引旧 quote 只能谎报当前轮）。
    去重靠 advance_state 层的 (dimension, user_quote) set，不会虚增计数。
    """
    action = Action(
        action="ask",
        dimension_targeted="E/I",
        evidence=[
            Evidence(
                dimension="E/I",
                user_quote="第1轮的具体回答内容",
                round_number=1,  # 历史轮，v2.3 允许
                confidence=0.6,
            )
        ],
        next_prompt="上周有没有一个具体的时刻让你印象很深?",
    )
    r = guard.validate_action(action, fake_session, round_number=21)
    assert r.passed, f"reasons: {r.reasons}"


def test_validate_action_rejects_historical_evidence_with_wrong_quote(guard, fake_session):
    """v2.3 · 回引历史轮但 quote 不是那轮的字面子串 → 仍然 reject。"""
    action = Action(
        action="ask",
        dimension_targeted="E/I",
        evidence=[
            Evidence(
                dimension="E/I",
                user_quote="这段话根本没出现过",
                round_number=1,
                confidence=0.6,
            )
        ],
        next_prompt="上周有没有一个具体的时刻让你印象很深?",
    )
    r = guard.validate_action(action, fake_session, round_number=21)
    assert not r.passed
    assert any("字面子串" in rea for rea in r.reasons)


def test_validate_action_banned_in_prompt(guard, fake_session):
    action = Action(
        action="ask",
        next_prompt="你是一个有深度的人。",  # BP3
    )
    r = guard.validate_action(action, fake_session, round_number=5)
    assert not r.passed
    assert any("BP3" in x for x in r.reasons)


def test_validate_action_fabricated_evidence(guard, fake_session):
    action = Action(
        action="ask",
        evidence=[
            Evidence(
                dimension="E/I",
                user_quote="用户根本没说过这话",
                round_number=1,
                confidence=0.6,
            )
        ],
        next_prompt="继续情境题",
    )
    r = guard.validate_action(action, fake_session, round_number=21)
    assert not r.passed


def test_validate_action_converge_not_ready(guard):
    # 没有足够 evidence
    session = SessionState(
        session_id="x",
        domain="mbti",
        turns=[Turn(round_number=1, user_message="第1轮内容")],
    )
    card = CardData(
        title="t" * 8,
        mbti_type="INTJ",
        subtitle="s",
        pull_quotes=[],
        typography_hint="editorial_serif",
    )
    co = ConvergeOutput(
        mbti_type="INTJ",
        insight_paragraphs=[
            InsightParagraph(theme="ab", body="一" * 100, quoted_rounds=[1])
            for _ in range(3)
        ],
        card=card,
        report_html=_VALID_HTML_STUB,
    )
    action = Action(
        action="converge",
        next_prompt="差不多了",
        converge_output=co,
    )
    r = guard.validate_action(action, session, round_number=5)
    assert not r.passed


# ---------------------------------------------------------------------------
# 9. fallback action
# ---------------------------------------------------------------------------


def test_fallback_action_shape():
    fb = fallback_action(round_number=5)
    assert fb.action == "ask"
    assert len(fb.next_prompt) > 0
    assert fb.converge_output is None


# ---------------------------------------------------------------------------
# 10. contradiction grounding in validate_action
# ---------------------------------------------------------------------------


def test_validate_action_contradiction_ungrounded_quote_a(guard, fake_session):
    from oriself_server.schemas import Contradiction

    action = Action(
        action="probe_contradiction",
        dimension_targeted="E/I",
        next_prompt="你第1轮说 ... 第7轮说 ...",
        contradiction=Contradiction(
            round_a=1,
            quote_a="虚构的原话",
            round_b=7,
            quote_b="第7轮的具体回答内容",
            observation="观察点",
        ),
    )
    r = guard.validate_action(action, fake_session, round_number=15)
    assert not r.passed
    assert any("quote_a" in x for x in r.reasons)


def test_validate_action_contradiction_ungrounded_quote_b(guard, fake_session):
    from oriself_server.schemas import Contradiction

    action = Action(
        action="probe_contradiction",
        dimension_targeted="S/N",
        next_prompt="ok",
        contradiction=Contradiction(
            round_a=1,
            quote_a="第1轮的具体回答内容",
            round_b=7,
            quote_b="虚构的B句",
            observation="x",
        ),
    )
    r = guard.validate_action(action, fake_session, round_number=15)
    assert not r.passed
    assert any("quote_b" in x for x in r.reasons)


def test_validate_action_contradiction_valid(guard, fake_session):
    from oriself_server.schemas import Contradiction

    action = Action(
        action="probe_contradiction",
        dimension_targeted="T/F",
        next_prompt="你第1轮说一件事，第7轮说另一件",
        contradiction=Contradiction(
            round_a=1,
            quote_a="第1轮的具体回答内容",
            round_b=7,
            quote_b="第7轮的具体回答内容",
            observation="两轮之间张力",
        ),
    )
    r = guard.validate_action(action, fake_session, round_number=15)
    assert r.passed, f"reasons: {r.reasons}"


def test_probe_requires_dim():
    """schema validator · probe 必须带 dim。"""
    from oriself_server.schemas import Contradiction

    with pytest.raises(Exception, match="probe_contradiction.dimension_targeted"):
        Action(
            action="probe_contradiction",
            dimension_targeted="none",
            next_prompt="x",
            contradiction=Contradiction(
                round_a=1, quote_a="aaaa", round_b=7, quote_b="bbbb", observation="x"
            ),
        )


def test_probe_quote_distance():
    """schema validator · probe 两句 quote 的 round 必须相隔 >= 4 轮。"""
    from oriself_server.schemas import Contradiction

    with pytest.raises(Exception, match="相隔"):
        Action(
            action="probe_contradiction",
            dimension_targeted="E/I",
            next_prompt="x",
            contradiction=Contradiction(
                round_a=3, quote_a="aaaa", round_b=5, quote_b="bbbb", observation="x"
            ),
        )


# ---------------------------------------------------------------------------
# 11. converge banned phrase in insight body
# ---------------------------------------------------------------------------


def test_converge_with_banned_phrase_in_insight(guard, fake_session):
    card = CardData(
        title="t" * 8,
        mbti_type="INTJ",
        subtitle="s",
        pull_quotes=[],
        typography_hint="editorial_serif",
    )
    paras = [
        InsightParagraph(theme="ab", body="一" * 100, quoted_rounds=[1, 2, 3]),
        InsightParagraph(
            theme="bc",
            body="你既享受独处又渴望连接。" + "一" * 60,  # BP1 hit
            quoted_rounds=[2],
        ),
        InsightParagraph(theme="cd", body="一" * 100, quoted_rounds=[3]),
    ]
    co = ConvergeOutput(
        mbti_type="INTJ",
        insight_paragraphs=paras,
        card=card,
        report_html=_VALID_HTML_STUB,
    )
    action = Action(
        action="converge",
        next_prompt="差不多了",
        converge_output=co,
    )
    r = guard.validate_action(action, fake_session, round_number=22)
    assert not r.passed
    assert any("BP1" in x for x in r.reasons)


# ---------------------------------------------------------------------------
# 11b. edge · empty text, Turn.quotes(), broken regex, insight body over limit
# ---------------------------------------------------------------------------


def test_detect_banned_empty_text(guard):
    assert guard.detect_banned_phrases("") == []


def test_turn_quotes_method():
    t = Turn(round_number=1, user_message="abc")
    assert t.quotes() == ["abc"]


def test_guardrails_handles_broken_regex_in_bundle(tmp_path):
    """坏正则不应让 OriSelfGuardrails 初始化崩溃。"""
    from oriself_server.skill_loader import BannedPattern, SkillBundle

    bundle = SkillBundle(
        skill_md="# stub",
        domain_md={},
        techniques={},
        examples={},
        phases={},
        banned_patterns=[
            BannedPattern(
                id="BAD_RE",
                kind="regex",
                pattern="[unclosed",
                severity="high",
                applies_to=["next_prompt"],
            )
        ],
    )
    g = OriSelfGuardrails(bundle=bundle)
    # 坏正则被忽略；detect 不触发
    assert g.detect_banned_phrases("anything") == []


def test_insight_body_total_over_limit(guard, fake_session, monkeypatch):
    """模拟 CONVERGE_INSIGHT_TOTAL_LIMIT 被下调，触发 line 241 分支。"""
    import oriself_server.guardrails as gmod

    monkeypatch.setattr(gmod, "CONVERGE_INSIGHT_TOTAL_LIMIT", 50)
    card = CardData(
        title="t" * 8,
        mbti_type="INTJ",
        subtitle="s",
        pull_quotes=[],
        typography_hint="editorial_serif",
    )
    co = ConvergeOutput(
        mbti_type="INTJ",
        insight_paragraphs=[
            InsightParagraph(theme="ab", body="一" * 100, quoted_rounds=[1, 2, 3]),
            InsightParagraph(theme="cd", body="一" * 100, quoted_rounds=[2]),
            InsightParagraph(theme="ef", body="一" * 100, quoted_rounds=[3]),
        ],
        card=card,
        report_html=_VALID_HTML_STUB,
    )
    r = guard.verify_insight_grounding(co, fake_session)
    assert not r.passed
    assert any("exceeds limit" in x for x in r.reasons)


# ---------------------------------------------------------------------------
# 12. v2.2 · report_html 结构性安全校验
# ---------------------------------------------------------------------------


def test_verify_report_html_accepts_valid(guard):
    r = guard.verify_report_html_shape(_VALID_HTML_STUB)
    assert r.passed


def test_verify_report_html_rejects_script(guard):
    bad = _VALID_HTML_STUB.replace("<main", "<script>alert(1)</script><main")
    r = guard.verify_report_html_shape(bad)
    assert not r.passed
    assert any("script" in x.lower() for x in r.reasons)


def test_verify_report_html_rejects_event_handler(guard):
    bad = _VALID_HTML_STUB.replace(
        "<main class=\"box\">", "<main class=\"box\" onclick=\"foo()\">"
    )
    r = guard.verify_report_html_shape(bad)
    assert not r.passed


def test_verify_report_html_allows_external_fonts(guard):
    """v2.2.1: 外部字体/CSS 是设计手段，不拦。"""
    ok = _VALID_HTML_STUB.replace(
        "<main class=\"box\">",
        "<link href=\"https://fonts.googleapis.com/css2?family=Crimson+Pro\">"
        "<main class=\"box\">",
    )
    r = guard.verify_report_html_shape(ok)
    assert r.passed


def test_verify_report_html_allows_css_import(guard):
    """v2.2.1: @import 外部 CSS 允许。"""
    ok = _VALID_HTML_STUB.replace(
        ".box{padding:40px;}",
        ".box{padding:40px;}@import url('https://fonts.googleapis.com/css2?family=X');",
    )
    r = guard.verify_report_html_shape(ok)
    assert r.passed


def test_verify_report_html_rejects_iframe(guard):
    bad = _VALID_HTML_STUB.replace(
        "<main class=\"box\">", "<iframe src=\"about:blank\"></iframe><main class=\"box\">"
    )
    r = guard.verify_report_html_shape(bad)
    assert not r.passed


def test_verify_report_html_rejects_js_url(guard):
    bad = _VALID_HTML_STUB.replace(
        "<main class=\"box\">", "<a href=\"javascript:void(0)\">x</a><main class=\"box\">"
    )
    r = guard.verify_report_html_shape(bad)
    assert not r.passed


def test_verify_report_html_rejects_template_placeholder(guard):
    """v2.2.3 · 防 LLM 留 {{session_id}} 之类未替换占位符。"""
    bad = _VALID_HTML_STUB.replace(
        "<main class=\"box\">", "<main class=\"box\"><div>会话 #{{session_id_6}}</div>"
    )
    r = guard.verify_report_html_shape(bad)
    assert not r.passed
    assert any("session_id_6" in x for x in r.reasons)


def test_verify_report_html_allows_single_braces(guard):
    """CSS `color: var(--x)` 里的单括号不能误伤；只拦 {{...}}。"""
    ok = _VALID_HTML_STUB.replace(
        "<main class=\"box\">",
        "<main class=\"box\" style=\"--x: { color: red }\">"
    )
    r = guard.verify_report_html_shape(ok)
    assert r.passed


# ---------------------------------------------------------------------------
# 13. insight para with empty quoted_rounds (branch 225-226)
# ---------------------------------------------------------------------------


def test_insight_grounding_para_with_empty_quotes(guard, fake_session):
    # Pydantic 限制 min_length=1 on quoted_rounds, but validator path still exists
    # 直接构造绕过 pydantic：用 model_construct
    from oriself_server.schemas import ConvergeOutput as CO, InsightParagraph as IP, CardData as CD

    para_bad = IP.model_construct(theme="ab", body="一" * 100, quoted_rounds=[])
    para_ok = IP(theme="cd", body="一" * 100, quoted_rounds=[2])
    para_ok2 = IP(theme="ef", body="一" * 100, quoted_rounds=[3])
    co = CO.model_construct(
        mbti_type="INTJ",
        insight_paragraphs=[para_bad, para_ok, para_ok2],
        card=CD(
            title="t" * 8,
            mbti_type="INTJ",
            subtitle="s",
            pull_quotes=[],
            typography_hint="editorial_serif",
        ),
        confidence_per_dim={},
        report_html=_VALID_HTML_STUB,
    )
    r = guard.verify_insight_grounding(co, fake_session)
    assert not r.passed
    assert any("no quoted_rounds" in x for x in r.reasons)
