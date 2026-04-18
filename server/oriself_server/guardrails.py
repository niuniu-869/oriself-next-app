"""
OriSelfGuardrails · v2.4 精简版。

哲学转向：
- v2.0-2.3 我们用 50+ 条规则拒 LLM 输出。实操发现 R1 第一句就可能全部
  retry 到 fallback 模板，用户看到的永远是同一段兜底文案。
- v2.4 只保留**会让系统真的坏掉**的硬拦截：
    1. 对话轮数 ≤ MAX_ROUNDS（算力预算）
    2. report_html 无 XSS 向量（安全边界）
    3. report_html 里 4 字母 MBTI 串 == 派生 mbti_type（单一真相源一致性）
- 对话轮的"品味"约束（治疗师腔 / 模板词 / 反射引原话等）全部迁至 SKILL.md + phase
  文件的散文指令。LLM 偶尔没做好 → 用户点「重写这轮」，不在服务端 retry。
- 报告生成（converge）允许 3 次 retry，这里定义守护规则。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

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


def verify_report_html_consistency(html: str, mbti_type: str) -> GuardrailResult:
    """字母一致性 · HTML 里每一处 4 字母 MBTI 串都必须等于派生值。"""
    if not html or not mbti_type:
        return GuardrailResult.ok()
    found = set(_RE_MBTI_TOKEN.findall(html))
    mismatched = [tok for tok in found if tok != mbti_type]
    if mismatched:
        return GuardrailResult.fail(
            f"report_html 里出现 {sorted(mismatched)} 与派生 mbti_type={mbti_type!r} 不一致；"
            f"请把 HTML 里所有 4 字母 MBTI 处都写成 {mbti_type!r}"
        )
    return GuardrailResult.ok()


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
