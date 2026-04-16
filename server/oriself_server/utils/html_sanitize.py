"""
HTML 清洗。两种场景：

1. `escape_user_quote` · 用户原话转义，完全不保留标签，用于名片 pull_quotes。
2. `sanitize_report_html` · 深度清洗 LLM 生成的报告 HTML。
   Skill prompt 已禁止 `<script>` / event handler / iframe，但 defense-in-depth
   永远对；再加 iframe sandbox 是第三道防线。
"""
from __future__ import annotations

import html
import re


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def escape_user_quote(text: str, max_length: int = 300) -> str:
    """把用户原话做 HTML escape，截断到 max_length。"""
    if not text:
        return ""
    if len(text) > max_length:
        text = text[:max_length].rstrip() + "..."
    # 删控制字符
    text = _CONTROL_CHARS_RE.sub("", text)
    return html.escape(text, quote=True)


# --- report_html 清洗 ---
# 匹配整块 <script>…</script>（非贪婪，大小写不敏感，跨行）
_SCRIPT_BLOCK_RE = re.compile(
    r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL
)
# 匹配单独的 <script …> 开标签（没有 close 的异常情况）
_SCRIPT_OPEN_RE = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
# 匹配不允许的嵌入标签
_FORBIDDEN_TAGS_RE = re.compile(
    r"<(?:iframe|object|embed|form|input|textarea|button)\b[^>]*>",
    re.IGNORECASE,
)
# 匹配 on*=... 事件处理器属性（HTML5 最多 70+ 个 on* 事件，正则覆盖所有）
# 注意：允许 CSS font-size 的 opsz 之类，所以 on 后必须跟字母且整个是属性（前面有空格或等）
_EVENT_HANDLER_RE = re.compile(
    r"""\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""",
    re.IGNORECASE,
)
# 匹配 javascript: / vbscript: 开头的 URL 协议
_BAD_PROTOCOL_RE = re.compile(
    r"""(?P<attr>\s(?:href|src|formaction|action|xlink:href))\s*=\s*(?P<q>["'])\s*(?:javascript|vbscript|data:text/html)\s*:[^"']*(?P=q)""",
    re.IGNORECASE,
)


def sanitize_report_html(html_str: str) -> str:
    """剥掉 <script> / event handler / javascript: URLs / forbidden embeds。

    这是 skill prompt 规则之上的第二道防护——即使 LLM 突破了 prompt，
    注入的可执行代码也进不去。iframe sandbox 是第三道防护。
    """
    if not html_str:
        return ""
    s = html_str
    s = _SCRIPT_BLOCK_RE.sub("<!-- script removed -->", s)
    s = _SCRIPT_OPEN_RE.sub("<!-- script tag removed -->", s)
    s = _FORBIDDEN_TAGS_RE.sub("<!-- forbidden tag removed -->", s)
    s = _EVENT_HANDLER_RE.sub("", s)
    s = _BAD_PROTOCOL_RE.sub(r"\g<attr>=\g<q>#removed\g<q>", s)
    return s
