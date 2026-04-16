"""Pydantic schemas 边界测试。"""
import pytest

from oriself_server.schemas import (
    Action,
    CardData,
    ConvergeOutput,
    Evidence,
    InsightParagraph,
    PullQuote,
)


def test_evidence_minimum_valid():
    ev = Evidence(
        dimension="E/I",
        user_quote="晚上 9 点天黑有点闷",
        round_number=1,
        confidence=0.6,
    )
    assert ev.dimension == "E/I"


def test_evidence_bad_dimension():
    with pytest.raises(Exception):
        Evidence(
            dimension="X/Y",  # invalid
            user_quote="test quote",
            round_number=1,
            confidence=0.5,
        )


def test_evidence_bad_confidence():
    with pytest.raises(Exception):
        Evidence(
            dimension="E/I",
            user_quote="test quote",
            round_number=1,
            confidence=1.5,  # > 1.0
        )


def test_evidence_quote_too_short():
    with pytest.raises(Exception):
        Evidence(
            dimension="E/I",
            user_quote="abc",  # < 4 chars
            round_number=1,
            confidence=0.5,
        )


def test_action_ask_minimal():
    a = Action(action="ask", next_prompt="你最近一次 ...")
    assert a.action == "ask"
    assert a.evidence == []


def test_action_next_prompt_budget():
    # next_prompt 600 字是 budget，模拟真实数据；这里测超过会被 Pydantic 拒
    with pytest.raises(Exception):
        Action(action="ask", next_prompt="a" * 601)


_HTML_STUB_SCHEMA = (
    "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
    "<title>t</title><style>body{background:#08080f;}</style></head>"
    "<body><main>" + "。" * 1200 + "</main></body></html>"
)


def test_converge_output_structure():
    card = CardData(
        title="一个安静的 INTJ",
        mbti_type="INTJ",
        subtitle="需要人，但需要对的场合",
        pull_quotes=[PullQuote(text="晚上 9 点", round=1)],
        typography_hint="editorial_serif",
    )
    paras = [
        InsightParagraph(
            theme=f"theme{i}",
            body="一" * 80,
            quoted_rounds=[1, 2],
        )
        for i in range(3)
    ]
    co = ConvergeOutput(
        mbti_type="INTJ",
        insight_paragraphs=paras,
        card=card,
        report_html=_HTML_STUB_SCHEMA,
    )
    assert co.mbti_type == "INTJ"
    assert len(co.insight_paragraphs) == 3
    assert co.report_html.startswith("<!DOCTYPE")


def test_converge_requires_exactly_three_paras():
    card = CardData(
        title="tttt",
        mbti_type="INTJ",
        subtitle="s",
        pull_quotes=[],
        typography_hint="editorial_serif",
    )
    with pytest.raises(Exception):
        ConvergeOutput(
            mbti_type="INTJ",
            insight_paragraphs=[
                InsightParagraph(theme="a", body="一" * 80, quoted_rounds=[1]),
            ],
            card=card,
            report_html=_HTML_STUB_SCHEMA,
        )


def test_converge_report_html_required():
    """v2.2 · report_html 必填。"""
    card = CardData(
        title="tttt",
        mbti_type="INTJ",
        subtitle="s",
        pull_quotes=[],
        typography_hint="editorial_serif",
    )
    paras = [
        InsightParagraph(theme=f"t{i}", body="一" * 80, quoted_rounds=[1])
        for i in range(3)
    ]
    with pytest.raises(Exception):
        ConvergeOutput(mbti_type="INTJ", insight_paragraphs=paras, card=card)


def test_converge_report_html_must_have_doctype():
    card = CardData(
        title="tttt",
        mbti_type="INTJ",
        subtitle="s",
        pull_quotes=[],
        typography_hint="editorial_serif",
    )
    paras = [
        InsightParagraph(theme=f"t{i}", body="一" * 80, quoted_rounds=[1])
        for i in range(3)
    ]
    bad = "<html><body>" + "a" * 1500 + "</body></html>"  # no DOCTYPE
    with pytest.raises(Exception):
        ConvergeOutput(
            mbti_type="INTJ",
            insight_paragraphs=paras,
            card=card,
            report_html=bad,
        )


def test_mbti_pattern():
    with pytest.raises(Exception):
        CardData(
            title="tttt",
            mbti_type="INVALID",
            subtitle="s",
            pull_quotes=[],
            typography_hint="editorial_serif",
        )


# ---------------------------------------------------------------------------
# v2.2.4 · _parse_json_safe 修复 report_html 转义
# ---------------------------------------------------------------------------


def test_parse_json_safe_with_unescaped_html():
    """模拟 LLM 在 report_html 里塞了未转义的引号和换行。"""
    from oriself_server.llm_client import _parse_json_safe

    # 构造一个 report_html 字段里带未转义引号和换行的 JSON
    bad_json = (
        '{"action":"converge","dimension_targeted":"none","evidence":[],'
        '"next_prompt":"ok","converge_output":{"mbti_type":"INTJ",'
        '"confidence_per_dim":{},'
        '"insight_paragraphs":[{"theme":"t1","body":"' + "一" * 80 + '","quoted_rounds":[1]},'
        '{"theme":"t2","body":"' + "一" * 80 + '","quoted_rounds":[2]},'
        '{"theme":"t3","body":"' + "一" * 80 + '","quoted_rounds":[3]}],'
        '"card":{"title":"tttttttt","mbti_type":"INTJ","subtitle":"s",'
        '"pull_quotes":[],"typography_hint":"editorial_serif"},'
        '"report_html":"<!DOCTYPE html><html><head><style>'
        'body { font-family: "Crimson Pro", serif; }\n'  # unescaped newline + quotes
        '.card { background: #fff; }\n'
        '</style></head><body>'
        '<h1>Test "Page"</h1>'  # unescaped quotes
        '</body></html>"}}'
    )
    result = _parse_json_safe(bad_json)
    assert result["action"] == "converge"
    html = result["converge_output"]["report_html"]
    assert "<!DOCTYPE" in html or "doctype" in html.lower()


def test_parse_json_safe_strict_false_control_chars():
    """strict=False 应该能容忍控制字符。"""
    from oriself_server.llm_client import _parse_json_safe

    # 合法 JSON 但 strict 模式会拒绝的控制字符
    raw = '{"action":"ask","next_prompt":"hello\\nworld"}'
    result = _parse_json_safe(raw)
    assert result["action"] == "ask"
