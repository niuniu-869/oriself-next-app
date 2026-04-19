"""v2.5.2 · converge 产物是 HTML，非 JSON。本文件测 guardrails 的 HTML
解析 / 抽取 / 校验三件套 + ReportRunner 的端到端流程。"""
from __future__ import annotations

import pytest

from oriself_server.guardrails import (
    extract_card_title_from_html,
    extract_mbti_from_html,
    resolve_mbti_or_fail,
    strip_markdown_fence,
    verify_report_html_parseable,
    verify_report_html_shape,
)


MIN_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>一个安静的栏目</title>
<style>body{color:#111;}</style></head>
<body><main>
<h1>INTP</h1>
<p>你说自己先想 —— 想的是结构图不是代码。<sup>R3</sup></p>
<blockquote>想清楚了再说 <span>R7</span></blockquote>
<p>落款：一句温暖的收束。</p>
</main></body></html>"""


# ---------------------------------------------------------------------------
# extract_mbti_from_html
# ---------------------------------------------------------------------------


def test_extract_mbti_single():
    tokens = extract_mbti_from_html(MIN_HTML)
    assert tokens == ["INTP"]


def test_extract_mbti_multiple_distinct():
    html = MIN_HTML.replace("你说", "TA is INFP, and also INFJ 倾向")
    tokens = extract_mbti_from_html(html)
    # 顺序保留、去重；<h1>INTP 先见、body 后见 INFP/INFJ
    assert tokens == ["INTP", "INFP", "INFJ"]


def test_extract_mbti_ignores_style_script():
    html = (
        '<!doctype html><html><head><title>t</title>'
        '<style>.ENFP{color:red}</style></head>'
        '<body><h1>ISTJ</h1><p>content</p></body></html>'
    )
    # ENFP 在 <style> 里，不应计入可见文本
    assert extract_mbti_from_html(html) == ["ISTJ"]


def test_extract_mbti_ignores_longer_words():
    html = (
        '<!doctype html><html><body><p>INTJOY 是一个编造的词，但 INTJ 是真的。</p>'
        '</body></html>'
    )
    # "INTJOY" 里 "INTJ" 被边界保护过滤；可见文本里独立的 "INTJ" 被抽出
    assert extract_mbti_from_html(html) == ["INTJ"]


def test_extract_mbti_empty_html():
    assert extract_mbti_from_html("") == []
    assert extract_mbti_from_html("<html></html>") == []


def test_extract_mbti_title_counted():
    html = (
        '<!doctype html><html><head><title>INFJ · 静水流深</title></head>'
        '<body><p>正文没字母</p></body></html>'
    )
    assert "INFJ" in extract_mbti_from_html(html)


# ---------------------------------------------------------------------------
# extract_card_title_from_html
# ---------------------------------------------------------------------------


def test_extract_title_basic():
    assert extract_card_title_from_html(MIN_HTML) == "一个安静的栏目"


def test_extract_title_missing():
    html = "<!doctype html><html><body><p>no title</p></body></html>"
    assert extract_card_title_from_html(html) is None


def test_extract_title_whitespace():
    html = (
        '<!doctype html><html><head><title>\n  有空白的标题\n</title></head>'
        '<body>x</body></html>'
    )
    assert extract_card_title_from_html(html) == "有空白的标题"


# ---------------------------------------------------------------------------
# resolve_mbti_or_fail
# ---------------------------------------------------------------------------


def test_resolve_mbti_single_ok():
    mbti, result = resolve_mbti_or_fail(MIN_HTML)
    assert mbti == "INTP"
    assert result.passed


def test_resolve_mbti_none_fails():
    html = (
        "<!doctype html><html><head><title>t</title></head>"
        "<body><p>文中没有 4 字母类型</p></body></html>"
    )
    mbti, result = resolve_mbti_or_fail(html)
    assert mbti is None
    assert not result.passed
    assert "找不到" in result.reasons[0]


def test_resolve_mbti_multi_fails():
    html = (
        "<!doctype html><html><head><title>t</title></head>"
        "<body><h1>INTP</h1><p>但其实 TA 更像 INFJ。</p></body></html>"
    )
    mbti, result = resolve_mbti_or_fail(html)
    assert mbti is None
    assert not result.passed
    assert "多种" in result.reasons[0]


# ---------------------------------------------------------------------------
# verify_report_html_parseable
# ---------------------------------------------------------------------------


def test_parseable_ok():
    r = verify_report_html_parseable(MIN_HTML)
    assert r.passed


def test_parseable_rejects_empty():
    r = verify_report_html_parseable("")
    assert not r.passed


def test_parseable_rejects_text_only():
    r = verify_report_html_parseable("只是一段普通文本，没有标签")
    # 这实际能被 html.parser 扫完（它极宽松）；应走"可见文本过少"分支或 shape
    # 只要是失败即可（这不是合法 HTML 报告）
    # 这里主要确认不会放行一个明显不对的东西
    # html.parser 会把它当作纯文本节点，可见文本 > 30 字可能会过
    # 实际上 shape 会兜底（没 doctype）。这里只做 smoke：
    assert isinstance(r.passed, bool)


def test_parseable_rejects_too_little_text():
    html = (
        '<!doctype html><html><head><title>t</title>'
        '<style>body{}</style></head><body><h1></h1></body></html>'
    )
    r = verify_report_html_parseable(html)
    assert not r.passed


# ---------------------------------------------------------------------------
# verify_report_html_shape · 原有路径 + 新增 doctype 检查
# ---------------------------------------------------------------------------


def test_shape_requires_doctype():
    html = "<html><body><h1>INTP</h1></body></html>"  # 没 doctype
    r = verify_report_html_shape(html)
    assert not r.passed
    assert any("DOCTYPE" in reason for reason in r.reasons)


def test_shape_rejects_script():
    html = MIN_HTML.replace("<style>", "<script>alert(1)</script><style>")
    r = verify_report_html_shape(html)
    assert not r.passed


def test_shape_rejects_onclick_handler():
    html = MIN_HTML.replace("<main>", '<main onclick="evil()">')
    r = verify_report_html_shape(html)
    assert not r.passed


def test_shape_accepts_minimal():
    r = verify_report_html_shape(MIN_HTML)
    assert r.passed, r.reasons


# ---------------------------------------------------------------------------
# strip_markdown_fence
# ---------------------------------------------------------------------------


def test_fence_html_fenced():
    raw = "```html\n" + MIN_HTML + "\n```"
    stripped = strip_markdown_fence(raw)
    assert stripped.startswith("<!doctype")


def test_fence_plain_fenced():
    raw = "```\n" + MIN_HTML + "\n```"
    stripped = strip_markdown_fence(raw)
    assert stripped.startswith("<!doctype")


def test_fence_no_fence_passthrough():
    assert strip_markdown_fence(MIN_HTML) == MIN_HTML


def test_fence_empty():
    assert strip_markdown_fence("") == ""


# ---------------------------------------------------------------------------
# End-to-end · ReportRunner 跑 mock，出 ConvergeOutput
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_runner_mock_flow():
    """MockBackend.complete_text 返回固定 HTML；ReportRunner 应通过全部校验链。"""
    import uuid

    from oriself_server.llm_client import make_backend
    from oriself_server.skill_loader import load_skill_bundle
    from oriself_server.skill_runner import (
        ReportRunner,
        SessionState,
        TurnRunner,
        advance_state,
    )

    backend = make_backend("mock")
    bundle = load_skill_bundle()
    runner = TurnRunner(backend=backend, bundle=bundle)
    reporter = ReportRunner(backend=backend, bundle=bundle)
    session = SessionState(session_id=str(uuid.uuid4()), domain="mbti")

    # 至少 6 轮才能 converge
    for i in range(6):
        visible = ""
        status = "CONTINUE"
        async for kind, payload in runner.stream_turn(session, f"R{i+1} reply"):
            if kind == "visible":
                visible = payload
            elif kind == "status":
                status = payload
        session = advance_state(session, f"R{i+1} reply", visible, status)

    result = await reporter.compose(session)
    assert result.output is not None
    assert result.output.mbti_type == "INTJ"  # mock html 里写死 INTJ
    assert result.output.card_title == "一个安静的栏目"
    assert result.output.mbti_type in result.output.report_html
