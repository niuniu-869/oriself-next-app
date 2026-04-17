"""
多 provider LLM 客户端 · v2.0 精简版。

v2.0 只保留两个 provider：
- `openai_compatible`: 适配 Qwen / DeepSeek / Kimi / 任何 OpenAI 兼容 API。通过 base_url 切换。
- `mock`: 确定性 mock，单测 / 无密钥演示用。

未来要加 Anthropic / Gemini / Claude 时，在 `backends/` 下加同接口实现即可。
prompt caching 预留 `cache_breakpoint` 参数供后续接入。
"""
from __future__ import annotations

import json
import os
import random
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Iterable, List, Optional

import httpx


# ---------------------------------------------------------------------------
# Message 结构
# ---------------------------------------------------------------------------


@dataclass
class Message:
    role: str  # system | user | assistant
    content: str
    cache_breakpoint: bool = False  # 预留给未来 Anthropic cache_control


# v2.3 · 把 user_message 用 <current_turn>/<history_turn> tag 包了之后，
# 某些工具（mock、测试）需要拿到去 tag 的原文。这里提供一个小解包函数。
_TURN_TAG_RE = re.compile(
    r"^\s*<(current|history)_turn[^>]*>\s*([\s\S]*?)\s*</(current|history)_turn>\s*$"
)


def _unwrap_turn_tag(content: str) -> str:
    """如果 content 是 <current_turn>...</current_turn> 结构，返回内部文本。

    否则原样返回（兼容历史数据 / 非 tag 格式）。
    """
    if not content:
        return content
    m = _TURN_TAG_RE.match(content)
    return m.group(2) if m else content


# ---------------------------------------------------------------------------
# 基础接口
# ---------------------------------------------------------------------------


class LLMBackend(ABC):
    provider_name: str = "base"

    @abstractmethod
    async def complete_json(
        self,
        messages: List[Message],
        *,
        response_schema: Optional[dict] = None,
        max_tokens: int = 1200,
        temperature: float = 0.7,
    ) -> dict:
        """一次请求，返回解析后的 JSON dict。失败抛异常。"""
        ...


# ---------------------------------------------------------------------------
# OpenAI-compatible backend
# ---------------------------------------------------------------------------


class OpenAICompatibleBackend(LLMBackend):
    """通用 OpenAI-compatible API。适配：
    - Qwen (DashScope): base_url=https://dashscope.aliyuncs.com/compatible-mode/v1
    - DeepSeek: base_url=https://api.deepseek.com/v1
    - Kimi (Moonshot): base_url=https://api.moonshot.cn/v1
    - OpenAI: base_url=https://api.openai.com/v1
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        provider_name: str = "openai_compatible",
        timeout: float = 60.0,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.provider_name = provider_name
        self.timeout = timeout

    async def complete_json(
        self,
        messages: List[Message],
        *,
        response_schema: Optional[dict] = None,
        max_tokens: int = 1200,
        temperature: Optional[float] = None,  # 留着是签名兼容性，默认不传
    ) -> dict:
        # v2.2.3+ · 不再强制 temperature；走 provider 自己的默认。
        # 如果调用方明确传了 temperature 才加进 payload。
        payload: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if temperature is not None:
            payload["temperature"] = temperature
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return _parse_json_safe(content)


# ---------------------------------------------------------------------------
# Mock backend（确定性 · 单测 / 无密钥演示）
# ---------------------------------------------------------------------------


class MockBackend(LLMBackend):
    """根据消息历史轮数返回脚本化 Action。

    脚本目标：30 轮内跑完一个完整 MBTI 会话。前几轮 ask，中段混 reflect/probe，
    尾部 converge。不追真实的 evidence 抽取质量（只追流程可跑通）。
    """

    provider_name = "mock"

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)

    async def complete_json(
        self,
        messages: List[Message],
        *,
        response_schema: Optional[dict] = None,
        max_tokens: int = 1200,
        temperature: float = 0.7,
    ) -> dict:
        # 从 messages 里估算当前是第几轮（user 消息个数）
        user_rounds = [m for m in messages if m.role == "user"]
        current_round = len(user_rounds)
        last_user_message = _unwrap_turn_tag(
            user_rounds[-1].content if user_rounds else ""
        )
        return self._script_action(current_round, last_user_message, messages)

    def _script_action(
        self, current_round: int, last_user: str, messages: List[Message]
    ) -> dict:
        dims = ["E/I", "S/N", "T/F", "J/P"]
        dim = dims[(current_round - 1) % 4]
        # evidence 从用户消息里摘前 20 个字作 quote（保证是子串）
        ev_quote = (last_user[:20] or "占位").strip()

        if current_round <= 2:
            return {
                "action": "ask",
                "dimension_targeted": dim,
                "evidence": [],
                "next_prompt": f"（mock · R{current_round}）上周一个完整没出门的一天，过到晚上你感觉怎么样？",
            }

        if current_round >= 22:
            return self._build_converge(messages)

        if current_round % 5 == 0 and current_round >= 7:
            # 偶尔做 reflect
            return {
                "action": "reflect",
                "dimension_targeted": dim,
                "evidence": [
                    {
                        "dimension": dim,
                        "user_quote": ev_quote,
                        "round_number": current_round,
                        "confidence": 0.6,
                        "interpretation": "mock interpretation",
                    }
                ],
                "next_prompt": f"（mock · reflect · R{current_round}）'{ev_quote[:12]}'... 具体讲一下那个场景？",
            }

        return {
            "action": "ask",
            "dimension_targeted": dim,
            "evidence": [
                {
                    "dimension": dim,
                    "user_quote": ev_quote,
                    "round_number": current_round,
                    "confidence": 0.55,
                    "interpretation": "mock interpretation",
                }
            ],
            "next_prompt": f"（mock · R{current_round}）再讲一个 {dim} 维度相关的最近具体场景？",
        }

    def _build_converge(self, messages: List[Message]) -> dict:
        # 从所有 user 消息拿前 3 条做 pull_quotes & insight 引用
        # v2.3：user content 现在被 <current_turn>/<history_turn> tag 包裹，
        # 要 strip 掉 anchor tag 才能拿到真实内容。
        user_msgs = [_unwrap_turn_tag(m.content) for m in messages if m.role == "user"]
        pulls = []
        rounds_cited = []
        for i, msg in enumerate(user_msgs[:3], start=1):
            pulls.append({"text": (msg[:30] or "占位").strip(), "round": i})
            rounds_cited.append(i)

        insight = [
            {
                "theme": "看起来的你",
                "body": (
                    f"第 {rounds_cited[0] if rounds_cited else 1} 轮你说 "
                    f"'{pulls[0]['text'] if pulls else '占位'}' —— 这一条让 mock "
                    "停了一下。你可能比你以为的要更 I 一些。" * 2
                )[:400],
                "quoted_rounds": rounds_cited[:1] or [1],
            },
            {
                "theme": "一个小矛盾",
                "body": (
                    f"你在第 {rounds_cited[0] if rounds_cited else 1} 轮和第 "
                    f"{rounds_cited[-1] if rounds_cited else 2} 轮说的话放一起看，"
                    "你在别人来找你聊的那种关系里会退后一步看自己。" * 2
                )[:400],
                "quoted_rounds": rounds_cited[:2] or [1, 2],
            },
            {
                "theme": "一句还没跟自己说的话",
                "body": (
                    "你整场没主动说 '我想要什么'，但证据其实都在轮子里。"
                    f"第 {rounds_cited[-1] if rounds_cited else 3} 轮里那个场景，"
                    "你自己那一份是存在的。下次它出现的时候，别急着塞回脑子里。"
                )[:400],
                "quoted_rounds": rounds_cited[-1:] or [3],
            },
        ]

        # v2.2 · mock 也得产完整 HTML（stub 版，用于流程测试）
        para_bodies = "".join(
            f"<section class=\"section\" style=\"animation-delay:{i*0.12:.2f}s\">"
            f"<span class=\"num\">0{i+1}</span>"
            f"<h2>{p['theme']}</h2><p>{p['body']}</p></section>"
            for i, p in enumerate(insight)
        )
        quotes_html = "".join(
            f"<blockquote><span>R{q['round']}</span>{q['text']}</blockquote>"
            for q in pulls
        )
        html = (
            "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>一个安静的 INTJ</title><style>"
            "body{background:#08080f;color:#c8cdd8;margin:0;"
            "font-family:-apple-system,'PingFang SC','Noto Sans SC',sans-serif;}"
            "body::before{content:'';position:fixed;inset:0;z-index:-1;"
            "background:radial-gradient(ellipse at 15% 80%,rgba(99,102,241,0.1),transparent 50%),"
            "radial-gradient(ellipse at 80% 20%,rgba(139,92,246,0.07),transparent 50%);}"
            "main{max-width:860px;margin:auto;padding:clamp(16px,4vw,40px);}"
            "h1{font-size:clamp(2.2rem,5vw,3.5rem);font-weight:800;"
            "letter-spacing:-0.03em;font-family:'Noto Serif SC',Georgia,serif;"
            "color:#e8eaf0;}"
            ".hero{padding:80px 0;}"
            ".mbti{font-size:clamp(3rem,8vw,6rem);font-weight:800;color:#818cf8;"
            "letter-spacing:0.05em;font-family:'SF Mono',ui-monospace,monospace;}"
            ".section{padding:40px;background:rgba(15,15,25,0.6);"
            "border:1px solid rgba(255,255,255,0.06);margin:24px 0;border-radius:20px;"
            "backdrop-filter:blur(20px);"
            "box-shadow:0 1px 3px rgba(0,0,0,0.25),0 24px 48px -12px rgba(0,0,0,0.5);"
            "animation:reveal 0.8s cubic-bezier(0.16,1,0.3,1) both;}"
            "@keyframes reveal{from{opacity:0;transform:translateY(28px);}"
            "to{opacity:1;transform:translateY(0);}}"
            ".num{font-size:0.7rem;letter-spacing:0.12em;text-transform:uppercase;"
            "color:#5a5e72;}"
            "h2{font-family:'Noto Serif SC',Georgia,serif;color:#e8eaf0;}"
            "p{line-height:1.8;}"
            "blockquote{background:rgba(129,140,248,0.06);border-left:2px solid #818cf8;"
            "padding:16px 20px;margin:12px 0;border-radius:8px;}"
            "blockquote span{display:inline-block;font-size:0.7rem;"
            "color:#c084fc;margin-right:12px;letter-spacing:0.1em;}"
            "</style></head><body><main>"
            f"<section class=\"hero\"><div class=\"mbti\">INTJ</div>"
            "<h1>一个安静的 INTJ</h1>"
            "<p style=\"color:#c084fc;\">mock · 演示用名片</p></section>"
            + para_bodies
            + "<section class=\"section\"><span class=\"num\">— 你的原话</span>"
            + quotes_html
            + "</section>"
            "<section class=\"section\"><p>mock 版本仅用于流程校验。"
            "真实版由 LLM 按 phase5-converge.md 指导生成。</p></section>"
            "</main></body></html>"
        )
        return {
            "action": "converge",
            "dimension_targeted": "none",
            "evidence": [],
            "next_prompt": "差不多了。接下来我给你一段话，看完可以骂也可以挑刺。",
            "converge_output": {
                "mbti_type": "INTJ",
                "confidence_per_dim": {
                    "E/I": 0.7,
                    "S/N": 0.65,
                    "T/F": 0.6,
                    "J/P": 0.6,
                },
                "insight_paragraphs": insight,
                "card": {
                    "title": "一个安静的 INTJ",
                    "mbti_type": "INTJ",
                    "subtitle": "mock · 演示用名片",
                    "pull_quotes": pulls,
                    "typography_hint": "editorial_serif",
                },
                "report_html": html,
            },
        }


# ---------------------------------------------------------------------------
# JSON 解析 · 容错：LLM 偶尔会在 JSON 外包一层 markdown
# ---------------------------------------------------------------------------


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

# report_html 字段的开头定位
_HTML_FIELD_START = re.compile(
    r'"report_html"\s*:\s*"', re.DOTALL
)


def _repair_html_json_field(raw: str) -> str:
    """尝试修复 report_html 字段里未转义的字符。

    LLM 经常在长 HTML 字符串里漏转义引号和换行。策略：
    1. 找到 "report_html": " 的起始位置
    2. 向后扫描找到字符串的真正结尾（考虑 JSON 后续结构 }", } 等）
    3. 把中间的 HTML 内容做 JSON 安全转义
    4. 重新拼接
    """
    m = _HTML_FIELD_START.search(raw)
    if not m:
        return raw

    prefix = raw[:m.end()]  # 从头到 "report_html": " 的引号之后
    rest = raw[m.end():]  # HTML 内容开始

    # 找 HTML 字符串的结尾：从尾部反向找 </html> 后第一个引号
    html_end_marker = rest.rfind("</html>")
    if html_end_marker == -1:
        html_end_marker = rest.rfind("</HTML>")
    if html_end_marker == -1:
        return raw  # 找不到 </html>，放弃修复

    # </html> 之后向后找到第一个不在 HTML 里的引号（闭合 JSON 字符串）
    scan_from = html_end_marker + len("</html>")
    close_quote_pos = rest.find('"', scan_from)
    if close_quote_pos == -1:
        # 可能 </html> 就是结尾，引号在紧接着
        close_quote_pos = scan_from

    html_raw = rest[:close_quote_pos]
    suffix = rest[close_quote_pos:]  # 从闭合引号到末尾（"}\n} 这类）

    # 转义 HTML 内容使之成为合法 JSON 字符串
    html_escaped = (
        html_raw
        .replace("\\", "\\\\")   # 先转义反斜杠
        .replace('"', '\\"')     # 转义引号
        .replace("\n", "\\n")    # 转义换行
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )

    return prefix + html_escaped + suffix


def _parse_json_safe(content: str) -> dict:
    content = content.strip()
    if not content:
        raise ValueError("empty LLM content")
    # 剥 markdown fence
    m = _JSON_FENCE_RE.search(content)
    if m:
        content = m.group(1)

    # 第 1 次尝试：标准解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 第 2 次尝试：strict=False（容忍控制字符）
    try:
        return json.loads(content, strict=False)
    except json.JSONDecodeError:
        pass

    # 第 3 次尝试：修复 report_html 字段的转义问题
    try:
        repaired = _repair_html_json_field(content)
        return json.loads(repaired, strict=False)
    except (json.JSONDecodeError, Exception):
        pass

    raise ValueError(f"LLM did not return valid JSON: {content[:200]!r}")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


PROVIDER_PRESETS: dict[str, dict] = {
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_env": "ORISELF_QWEN_MODEL",
        "default_model": "qwen-max",
        "api_key_env": "ORISELF_QWEN_API_KEY",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model_env": "ORISELF_DEEPSEEK_MODEL",
        "default_model": "deepseek-chat",
        "api_key_env": "ORISELF_DEEPSEEK_API_KEY",
    },
    "kimi": {
        "base_url": "https://api.moonshot.cn/v1",
        "model_env": "ORISELF_KIMI_MODEL",
        "default_model": "moonshot-v1-32k",
        "api_key_env": "ORISELF_KIMI_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model_env": "ORISELF_OPENAI_MODEL",
        "default_model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
    },
}


def make_backend(provider: str) -> LLMBackend:
    if provider == "mock":
        seed = int(os.environ.get("ORISELF_MOCK_SEED", "42"))
        return MockBackend(seed=seed)

    preset = PROVIDER_PRESETS.get(provider)
    if preset is None:
        raise ValueError(f"unknown provider: {provider}")
    api_key = os.environ.get(preset["api_key_env"])
    if not api_key:
        raise RuntimeError(
            f"missing env {preset['api_key_env']} for provider={provider}. "
            f"设置后重试，或改用 provider=mock。"
        )
    model = os.environ.get(preset["model_env"], preset["default_model"])
    return OpenAICompatibleBackend(
        api_key=api_key,
        base_url=preset["base_url"],
        model=model,
        provider_name=provider,
    )
