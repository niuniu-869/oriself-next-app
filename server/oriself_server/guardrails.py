"""
OriSelfGuardrails · v2.5.2。

哲学转向：
- v2.0-2.3 我们用 50+ 条规则拒 LLM 输出。实操发现 R1 第一句就可能全部
  retry 到 fallback 模板，用户看到的永远是同一段兜底文案。
- v2.4 只保留**会让系统真的坏掉**的硬拦截：
    1. 对话轮数 ≤ MAX_ROUNDS（算力预算）
    2. report_html 无 XSS 向量（安全边界）
    3. report_html 里 4 字母 MBTI 串唯一（单一真相源）
- v2.5.2 converge 不再走 JSON：LLM 直吐 HTML。本文件新增：
    · verify_report_html_parseable（HTML 语法完整性）
    · extract_mbti_from_html（从可见文本抽 4 字母 token，去重保序）
    · extract_card_title_from_html（抽 <title> 作 card 标题）
- 对话轮的"品味"约束（治疗师腔 / 模板词 / 反射引原话等）全部在 SKILL.md + phase
  文件的散文指令。LLM 偶尔没做好 → 用户点「重写这轮」，不在服务端 retry。
- 报告生成（converge）允许 3 次 retry，这里定义守护规则。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import List, Optional, Tuple

from .schemas import MAX_ROUNDS


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class GuardrailResult:
    passed: bool
    reasons: List[str] = field(default_factory=list)

    @classmethod
    def ok(cls) -> "GuardrailResult":
        return cls(passed=True)

    @classmethod
    def fail(cls, *reasons: str) -> "GuardrailResult":
        return cls(passed=False, reasons=list(reasons))

    def merge(self, other: "GuardrailResult") -> "GuardrailResult":
        return GuardrailResult(
            passed=self.passed and other.passed,
            reasons=self.reasons + other.reasons,
        )


# ---------------------------------------------------------------------------
# 对话轮 · 唯一的硬检查：轮数预算
# ---------------------------------------------------------------------------


def check_round_budget(round_count: int) -> GuardrailResult:
    """轮数到 MAX_ROUNDS 即触发硬收束。

    这个"不合格"不会打回 LLM 重写，而是让 runner 直接切到 converge 流程。
    """
    if round_count >= MAX_ROUNDS:
        return GuardrailResult.fail(
            f"round_count={round_count} reached MAX_ROUNDS={MAX_ROUNDS}"
        )
    return GuardrailResult.ok()


# ---------------------------------------------------------------------------
# 报告轮 · report_html 安全 + 字母一致性
# ---------------------------------------------------------------------------


_RE_SCRIPT = re.compile(r"<\s*script\b", re.IGNORECASE)
_RE_IFRAME = re.compile(r"<\s*(iframe|object|embed|form|input)\b", re.IGNORECASE)
_RE_EVENT_HANDLER = re.compile(r"\son\w+\s*=", re.IGNORECASE)
_RE_JS_URL = re.compile(r"javascript\s*:", re.IGNORECASE)
_RE_TEMPLATE_PLACEHOLDER = re.compile(r"\{\{\s*[\w_.-]+\s*\}\}")
# 4 字母 MBTI 串（按维度合法字母各取一个，恰好连写）
_RE_MBTI_TOKEN = re.compile(r"(?<![A-Za-z])[EI][SN][TF][JP](?![A-Za-z])")


def verify_report_html_shape(html: str) -> GuardrailResult:
    """安全边界 · report_html 不得含 JS 执行向量、iframe、未替换占位符。

    合法手段（不拦）：
    - 外部字体（Google Fonts 等）、外部 CSS、data: URIs、外部图片
    """
    if not html:
        return GuardrailResult.fail("report_html is empty")
    reasons: List[str] = []
    # 骨架检查：doctype + html 标签
    low = html.lower()
    if "<!doctype" not in low:
        reasons.append("report_html 缺少 <!DOCTYPE html> 开头")
    if "<html" not in low or "</html>" not in low:
        reasons.append("report_html 缺少完整 <html>...</html> 标签")
    if _RE_SCRIPT.search(html):
        reasons.append("report_html 含 <script>（禁止 JS 执行）")
    if _RE_IFRAME.search(html):
        reasons.append("report_html 含 <iframe>/<object>/<embed>/<form>/<input>")
    if _RE_EVENT_HANDLER.search(html):
        reasons.append("report_html 含事件处理器（onclick/onerror 等）")
    if _RE_JS_URL.search(html):
        reasons.append("report_html 含 javascript: URL")
    m = _RE_TEMPLATE_PLACEHOLDER.search(html)
    if m:
        reasons.append(
            f"report_html 含未替换的模板占位符 {m.group(0)!r}；"
            "请把服务端给的真实值（session_id_short / today_*）直接写进 HTML"
        )
    return GuardrailResult.ok() if not reasons else GuardrailResult.fail(*reasons)


# ---------------------------------------------------------------------------
# v2.5.2 · HTML 解析 + 信息抽取
# ---------------------------------------------------------------------------


class _TextCollector(HTMLParser):
    """扫一遍 HTML，输出：

    - `.text_parts`: 非 <style>/<script> 标签下的纯文本片段
    - `.title`: <title> 内的文本（首次遇到）
    - `.well_formed`: True 表示解析过程中没触发致命错误
    """

    # 不计入文本抽取的标签（内容不是"页面可见文本"）
    _SKIP_TAGS = {"style", "script"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: List[str] = []
        self.title: Optional[str] = None
        self._in_title: bool = False
        self._skip_stack: List[str] = []
        self.well_formed: bool = True
        self.error: Optional[str] = None

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        tag_low = tag.lower()
        if tag_low in self._SKIP_TAGS:
            self._skip_stack.append(tag_low)
        if tag_low == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        tag_low = tag.lower()
        if self._skip_stack and self._skip_stack[-1] == tag_low:
            self._skip_stack.pop()
        if tag_low == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._in_title:
            if self.title is None:
                self.title = data.strip()
            else:
                # 标题里多段文本拼接
                self.title = (self.title + data).strip()
        if self._skip_stack:
            return
        if data:
            self.text_parts.append(data)

    def error(self, message: str) -> None:  # noqa: D401  (HTMLParser 历史 API)
        self.well_formed = False
        if self.error is None:
            self.error = message


def _parse_html(html: str) -> _TextCollector:
    p = _TextCollector()
    try:
        p.feed(html)
        p.close()
    except Exception as exc:  # html.parser 在极脏 HTML 上极少抛；抛了就按不合格处理
        p.well_formed = False
        p.error = str(exc)
    return p


def verify_report_html_parseable(html: str) -> GuardrailResult:
    """HTML 必须能被 Python 标准库 html.parser 完整扫完。

    这是"能不能被浏览器渲染"的最低门槛代理。比 BeautifulSoup 更严格一点
    （HTMLParser 对未闭合的诸如 `<foo attr=\"...` 会抛），比 html5lib 更宽松。
    """
    if not html or not html.strip():
        return GuardrailResult.fail("HTML 为空")
    p = _parse_html(html)
    if not p.well_formed:
        return GuardrailResult.fail(
            f"HTML 解析失败：{p.error or '未知错误'}"
        )
    # 没解析出任何可见文本，视为坏 HTML（通常是 LLM 吐了一大段 markdown）
    total_text = "".join(p.text_parts).strip()
    if len(total_text) < 30:
        return GuardrailResult.fail(
            f"HTML 里可见文本过少（{len(total_text)} 字符），"
            "疑似截断或仅输出了样式/脚本块"
        )
    return GuardrailResult.ok()


def extract_card_title_from_html(html: str) -> Optional[str]:
    """从 <title>…</title> 抽 card 标题；没抽到返回 None。"""
    if not html:
        return None
    p = _parse_html(html)
    if not p.title:
        return None
    title = p.title.strip()
    return title or None


def extract_mbti_from_html(html: str) -> List[str]:
    """从 HTML 的"可见文本"（剔除 <style>/<script>）里扫 4 字母 MBTI token。

    返回去重保序列表。调用方按下列策略决策：
    - len() == 1 → 合格，这就是 mbti_type
    - len() == 0 → 失败（LLM 忘了写 MBTI）
    - len() > 1  → 失败（多种 MBTI 同时出现 = 一致性违反）
    """
    if not html:
        return []
    p = _parse_html(html)
    # 用空白把相邻标签的文本分隔开，避免 `<h1>INTP</h1><p>context…` 这种被拼成
    # `INTPcontext…` 导致 token 边界误判。
    visible = " ".join(part for part in p.text_parts if part)
    if p.title:
        visible = p.title + " " + visible
    tokens = _RE_MBTI_TOKEN.findall(visible)
    # 去重保序
    seen: List[str] = []
    for t in tokens:
        if t not in seen:
            seen.append(t)
    return seen


def resolve_mbti_or_fail(html: str) -> Tuple[Optional[str], GuardrailResult]:
    """把 extract_mbti_from_html 的三分支决策包装成 (mbti, result)。

    成功：返回 (mbti_type, ok)
    失败：返回 (None, fail_result)
    """
    tokens = extract_mbti_from_html(html)
    if not tokens:
        return None, GuardrailResult.fail(
            "HTML 可见文本里找不到 4 字母 MBTI（如 'INTP'）。"
            "请在显眼位置明确写出 TA 的 MBTI 类型。"
        )
    if len(tokens) > 1:
        return None, GuardrailResult.fail(
            f"HTML 里出现多种 MBTI 字母串 {tokens}；"
            "必须只出现一种 —— 全文所有 4 字母位置都写成同一个类型。"
        )
    return tokens[0], GuardrailResult.ok()


def verify_report_html_consistency(html: str, mbti_type: str) -> GuardrailResult:
    """字母一致性 · HTML 里每一处 4 字母 MBTI 串都必须等于给定值。

    v2.5.2 起主要用于**回归校验**：ReportRunner 已经通过 resolve_mbti_or_fail
    把 mbti 从 HTML 抽出来，此函数给已有测试保留同名 API（HTML 中的四字母必须唯一
    且匹配），语义上等价于 resolve_mbti_or_fail + 等值比较。
    """
    if not html or not mbti_type:
        return GuardrailResult.ok()
    tokens = extract_mbti_from_html(html)
    if not tokens:
        return GuardrailResult.fail(
            f"report_html 里没找到任何 4 字母 MBTI；期望 {mbti_type!r}"
        )
    mismatched = [t for t in tokens if t != mbti_type]
    if mismatched:
        return GuardrailResult.fail(
            f"report_html 里出现 {sorted(mismatched)} 与派生 mbti_type={mbti_type!r} 不一致；"
            f"请把 HTML 里所有 4 字母 MBTI 处都写成 {mbti_type!r}"
        )
    return GuardrailResult.ok()


# ---------------------------------------------------------------------------
# Markdown fence 剥离（LLM 有时顽固地包 ```html）
# ---------------------------------------------------------------------------


_FENCE_RE = re.compile(
    r"^\s*```(?:html)?\s*\n?(.*?)\n?```[\s]*$",
    re.DOTALL | re.IGNORECASE,
)


def strip_markdown_fence(text: str) -> str:
    """LLM 偶尔用 ```html ... ``` 包 HTML，剥掉。"""
    if not text:
        return text
    m = _FENCE_RE.match(text.strip())
    if m:
        return m.group(1).strip()
    return text


# ---------------------------------------------------------------------------
# STATUS 解析（对话轮末行 sentinel）
# ---------------------------------------------------------------------------


# 精确匹配 gstack 风格：独立一行、大写、无装饰
# 兼容前缀空白 + 末尾可选的冒号 / 空白
_STATUS_RE = re.compile(
    r"(?:^|\n)\s*STATUS\s*:\s*(CONTINUE|CONVERGE|NEED_USER)\s*\.?\s*$",
    re.MULTILINE,
)


@dataclass
class ParsedTurn:
    visible_text: str  # 给用户看的（STATUS 行已剥除）
    status: str        # CONTINUE / CONVERGE / NEED_USER
    status_explicit: bool  # LLM 是否真的声明了，还是我们按默认 CONTINUE 兜底


def parse_status_sentinel(raw: str) -> ParsedTurn:
    """从 LLM 纯文本输出的**末尾**扫一行 STATUS。

    - 抽到 → 从 visible_text 里剥除该行
    - 没抽到 → visible_text = raw.strip()，status = CONTINUE（默认）

    为什么只扫末尾：gstack 的 Completion Status 协议规定 STATUS 是收尾信号，
    LLM 偶尔会在中间段放"STATUS: ..."作叙述文字，那不算。我们只认**最后一行**。
    """
    raw = raw or ""
    # 扫最后一次出现
    matches = list(_STATUS_RE.finditer(raw))
    if not matches:
        return ParsedTurn(
            visible_text=raw.strip(),
            status="CONTINUE",
            status_explicit=False,
        )
    last = matches[-1]
    status = last.group(1)
    # 剥除该行 —— 用 span 把 STATUS 行及其前导换行一并去掉
    visible = (raw[: last.start()] + raw[last.end():]).rstrip()
    return ParsedTurn(
        visible_text=visible,
        status=status,
        status_explicit=True,
    )
