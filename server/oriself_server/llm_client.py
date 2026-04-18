"""
多 provider LLM 客户端 · v2.4。

v2.4 变化：
- 每个 backend 暴露两个方法：
    * `stream_text(messages)` → `AsyncIterator[str]` · 对话轮专用，SSE token 流
    * `complete_json(messages)` → `dict` · 仅报告生成（converge）用
- 对话轮不再要 JSON，provider 侧不再传 `response_format`
- MockBackend 产出带 `STATUS: ...` 末行的纯文本，以及 converge JSON

支持的 provider：
- `openai_compatible`（Qwen / DeepSeek / Kimi / OpenAI / 302.ai Gemini 等兼容端）
- `mock` · 确定性 · 无 key / 单测 / 演示
"""
from __future__ import annotations

import json
import os
import random
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, List, Optional

import httpx


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


@dataclass
class Message:
    role: str  # system | user | assistant
    content: str
    cache_breakpoint: bool = False  # 预留给未来 Anthropic cache_control


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class LLMBackend(ABC):
    provider_name: str = "base"

    @abstractmethod
    async def stream_text(
        self,
        messages: List[Message],
        *,
        timeout: float = 90.0,
    ) -> AsyncIterator[str]:
        """对话轮 · 流式出纯文本。按 token 或 chunk 逐段 yield。"""
        ...

    @abstractmethod
    async def complete_json(
        self,
        messages: List[Message],
        *,
        response_schema: Optional[dict] = None,
        timeout: float = 120.0,
    ) -> dict:
        """仅报告生成用 · 一次请求返回解析后的 JSON dict。失败抛异常。"""
        ...


# ---------------------------------------------------------------------------
# OpenAI-compatible backend
# ---------------------------------------------------------------------------


class OpenAICompatibleBackend(LLMBackend):
    """通用 OpenAI-compatible API：Qwen / DeepSeek / Kimi / OpenAI / Gemini-302 等。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        provider_name: str = "openai_compatible",
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.provider_name = provider_name

    # ---- 对话轮 · 流式文本 ----
    async def stream_text(
        self,
        messages: List[Message],
        *,
        timeout: float = 90.0,
    ) -> AsyncIterator[str]:
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    raise RuntimeError(
                        f"{self.provider_name} stream {resp.status_code}: {body[:400]}"
                    )
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    # OpenAI SSE 协议：每行 `data: {...}`，结束行 `data: [DONE]`
                    if line.startswith(":"):
                        continue  # 注释 / heartbeat
                    if line.startswith("data:"):
                        data = line[5:].strip()
                    else:
                        data = line.strip()
                    if not data or data == "[DONE]":
                        if data == "[DONE]":
                            break
                        continue
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue  # 坏行忽略
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    chunk = delta.get("content")
                    if chunk:
                        yield chunk

    # ---- 报告轮 · 非流式 JSON ----
    async def complete_json(
        self,
        messages: List[Message],
        *,
        response_schema: Optional[dict] = None,
        timeout: float = 120.0,
    ) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
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
# Mock backend
# ---------------------------------------------------------------------------


# mock 对话的预设文本池。按轮数挑，末尾自动拼 STATUS。
_MOCK_TURN_SCRIPTS = [
    # R1 握手
    (
        "嗨——我不是什么人格测评系统，就是一个想陪你聊 20 分钟上下的朋友。"
        "聊完不发卷子、不打分，就是希望你聊完能多看自己一点点。\n\n"
        "开始之前想跟你对一下三件事（你挑愿意说的回就行）：\n"
        "1. 想聊得轻松点还是深入点？\n"
        "2. 短的（10-15 轮）、标准（20 轮左右）、还是慢慢聊（25-30 轮）？\n"
        "3. 最近想聊的或者不想碰的话题？"
    ),
    # R2
    "嗯，那就不赶。最近脑子里在转的事里，有哪一件你挺想跟人说说的？",
    # R3
    "你刚说的那个画面——具体是哪一刻开始觉得的？",
    # R4
    "周末 6 小时空出来，你的默认剧本是什么？不用想应该的，想真的默认。",
    # R5
    "你最近一次觉得'这个人懂我'是跟谁？你们在做什么？",
    # R6
    "你的日历 app 现在打开是什么状态？真实的不是理想的。",
    # R7
    "你最近一次帮朋友做决定，你是怎么想的？你说了什么？",
    # R8
    "你现在闭眼想你现在的工作 / 学业，第一个蹦出来的画面是什么？",
]


class MockBackend(LLMBackend):
    """确定性脚本 mock · v2.4 · 文本流 + 收束 JSON。

    - `stream_text`：按轮数从 _MOCK_TURN_SCRIPTS 取一条文本，逐字 yield；
      末尾补一行 `STATUS: CONTINUE`。到第 8 轮改成 `STATUS: CONVERGE`。
    - `complete_json`：返回一个占位 converge 结构，便于前后端联调。
    """

    provider_name = "mock"

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)

    # ---- 对话轮 ----
    async def stream_text(
        self,
        messages: List[Message],
        *,
        timeout: float = 90.0,
    ) -> AsyncIterator[str]:
        # 估算当前轮：用 user message 数量（不含 system）
        user_rounds = [m for m in messages if m.role == "user"]
        current_round = len(user_rounds)
        idx = min(current_round - 1, len(_MOCK_TURN_SCRIPTS) - 1)
        idx = max(idx, 0)
        body = _MOCK_TURN_SCRIPTS[idx]
        status = "STATUS: CONVERGE" if current_round >= 8 else "STATUS: CONTINUE"
        full = body + "\n\n" + status
        # 逐字 yield，模拟流
        for ch in full:
            yield ch

    # ---- 报告轮 ----
    async def complete_json(
        self,
        messages: List[Message],
        *,
        response_schema: Optional[dict] = None,
        timeout: float = 120.0,
    ) -> dict:
        user_msgs = [m.content for m in messages if m.role == "user"]
        pulls = []
        rounds_cited = []
        for i, msg in enumerate(user_msgs[:3], start=1):
            pulls.append({"text": (msg[:30] or "占位").strip(), "round": i})
            rounds_cited.append(i)
        if not rounds_cited:
            rounds_cited = [1]
            pulls = [{"text": "占位原话", "round": 1}]

        insight = [
            {
                "theme": "看起来的你",
                "body": (
                    "这是 mock 生成的第一段洞见占位文本。正式版由 LLM 按 CONVERGE.md 指导生成。"
                    "至少要 60 字的正文才能通过长度检查，所以这里再多铺一些字数凑一下长度 ok 这样差不多。"
                ),
                "quoted_rounds": rounds_cited[:1],
            },
            {
                "theme": "一个小矛盾",
                "body": (
                    "第二段 mock 占位文本。真实版这里会写 TA 的矛盾或反差。"
                    "再补点字数达到最低 60 字阈值，保证 JSON schema 通过。"
                ),
                "quoted_rounds": rounds_cited[:2],
            },
            {
                "theme": "一句还没跟自己说的话",
                "body": (
                    "第三段 mock 占位文本。真实版这里会写 TA 整场对话里隐隐指向但自己没说出的东西。"
                    "再加几个字把长度撑起来方便通过校验这样就够用了可以了。"
                ),
                "quoted_rounds": rounds_cited[-1:],
            },
        ]

        quotes_html = "".join(
            f"<blockquote><span>R{q['round']}</span>{q['text']}</blockquote>"
            for q in pulls
        )
        para_bodies = "".join(
            f"<section class=\"section\"><span class=\"num\">0{i+1}</span>"
            f"<h2>{p['theme']}</h2><p>{p['body']}</p></section>"
            for i, p in enumerate(insight)
        )
        html = (
            "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>mock · INTJ</title><style>"
            "body{background:#08080f;color:#c8cdd8;margin:0;"
            "font-family:'Noto Serif SC',-apple-system,sans-serif;}"
            "main{max-width:720px;margin:auto;padding:40px;}"
            "h1{font-size:3rem;font-weight:800;letter-spacing:-0.03em;}"
            ".mbti{font-size:5rem;font-weight:800;color:#818cf8;"
            "font-family:'JetBrains Mono',ui-monospace,monospace;}"
            ".section{padding:32px;background:rgba(15,15,25,0.6);"
            "border:1px solid rgba(255,255,255,0.06);margin:20px 0;border-radius:16px;}"
            ".num{font-size:0.7rem;letter-spacing:0.12em;color:#5a5e72;}"
            "blockquote{background:rgba(129,140,248,0.06);border-left:2px solid #818cf8;"
            "padding:12px 16px;margin:10px 0;border-radius:8px;}"
            "blockquote span{display:inline-block;color:#c084fc;margin-right:12px;}"
            "</style></head><body><main>"
            "<section><div class=\"mbti\">INTJ</div>"
            "<h1>一个安静的 INTJ</h1>"
            "<p style=\"color:#c084fc;\">mock · 演示用名片</p></section>"
            + para_bodies
            + "<section class=\"section\"><span class=\"num\">你的原话</span>"
            + quotes_html
            + "</section></main></body></html>"
        )

        return {
            "mbti_type": "INTJ",
            "confidence_per_dim": {
                "E/I": {"letter": "I", "score": 0.72},
                "S/N": {"letter": "N", "score": 0.65},
                "T/F": {"letter": "T", "score": 0.60},
                "J/P": {"letter": "J", "score": 0.58},
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
        }


# ---------------------------------------------------------------------------
# JSON 容错解析（仅 complete_json 用）
# ---------------------------------------------------------------------------


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_HTML_FIELD_START = re.compile(r'"report_html"\s*:\s*"', re.DOTALL)


def _repair_html_json_field(raw: str) -> str:
    """修复 report_html 字段里未转义的字符（LLM 常漏）。"""
    m = _HTML_FIELD_START.search(raw)
    if not m:
        return raw
    prefix = raw[: m.end()]
    rest = raw[m.end():]
    html_end_marker = rest.rfind("</html>")
    if html_end_marker == -1:
        html_end_marker = rest.rfind("</HTML>")
    if html_end_marker == -1:
        return raw
    scan_from = html_end_marker + len("</html>")
    close_quote_pos = rest.find('"', scan_from)
    if close_quote_pos == -1:
        close_quote_pos = scan_from
    html_raw = rest[:close_quote_pos]
    suffix = rest[close_quote_pos:]
    html_escaped = (
        html_raw
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return prefix + html_escaped + suffix


def _parse_json_safe(content: str) -> dict:
    content = content.strip()
    if not content:
        raise ValueError("empty LLM content")
    m = _JSON_FENCE_RE.search(content)
    if m:
        content = m.group(1)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(content, strict=False)
    except json.JSONDecodeError:
        pass
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
    "gemini": {
        # base_url 必须来自 env（ORISELF_GEMINI_BASE_URL 或 GEMINI_BASE_URL）。
        # 不给 302.ai 之类的公共默认，防止用户忘了配时走到错误代理。
        "base_url": (
            os.environ.get("ORISELF_GEMINI_BASE_URL")
            or os.environ.get("GEMINI_BASE_URL")
            or ""  # 空串 → make_backend 里会报错
        ),
        "model_env": "ORISELF_GEMINI_MODEL",
        "default_model": "gemini-3-flash-preview",
        # api_key 同时接受 ORISELF_GEMINI_API_KEY / GEMINI_API_KEY
        "api_key_env": "ORISELF_GEMINI_API_KEY",
        "api_key_env_fallback": "GEMINI_API_KEY",
    },
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
    if not api_key and preset.get("api_key_env_fallback"):
        api_key = os.environ.get(preset["api_key_env_fallback"])
    if not api_key:
        raise RuntimeError(
            f"missing env {preset['api_key_env']} for provider={provider}. "
            f"设置后重试，或改用 provider=mock。"
        )
    if not preset["base_url"]:
        raise RuntimeError(
            f"missing env ORISELF_{provider.upper()}_BASE_URL / "
            f"{provider.upper()}_BASE_URL for provider={provider}. "
            "设置后重试。"
        )
    model = os.environ.get(preset["model_env"], preset["default_model"])
    return OpenAICompatibleBackend(
        api_key=api_key,
        base_url=preset["base_url"],
        model=model,
        provider_name=provider,
    )
