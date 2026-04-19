"""
多 provider LLM 客户端 · v2.5.2。

v2.5.2 变化：
- converge 不再走 JSON schema：`complete_json` → `complete_text`，返回整段字符串
  （LLM 直吐 HTML）。服务端从 HTML 抽 MBTI + title，见 guardrails.py
- 默认 converge timeout 从 120s 提到 300s（长 HTML 生成 + 后端长尾）

v2.4 保留：
- 每个 backend 暴露两个方法：
    * `stream_text(messages)` → `AsyncIterator[str]` · 对话轮 SSE
    * `complete_text(messages)` → `str` · 报告生成（converge）
- 对话轮不再要 JSON，provider 侧不传 `response_format`
- MockBackend 产出带 `STATUS: ...` 末行的文本；converge 产 HTML

支持的 provider：
- `openai_compatible`（Qwen / DeepSeek / Kimi / OpenAI / 302.ai Gemini 等兼容端）
- `mock` · 确定性 · 无 key / 单测 / 演示
"""
from __future__ import annotations

import json
import os
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, List

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
    async def complete_text(
        self,
        messages: List[Message],
        *,
        timeout: float = 300.0,
    ) -> str:
        """报告轮 · 一次请求返回完整文本（HTML）。失败抛异常。"""
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

    # ---- 报告轮 · 非流式，返回原始文本 ----
    async def complete_text(
        self,
        messages: List[Message],
        *,
        timeout: float = 300.0,
    ) -> str:
        payload: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
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
        if not isinstance(content, str):
            raise ValueError(f"LLM returned non-string content: {type(content)}")
        return content


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


_MOCK_CONVERGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>一个安静的栏目</title>
  <style>
    body{background:#08080f;color:#c8cdd8;margin:0;
         font-family:'Noto Serif SC',-apple-system,serif;}
    main{max-width:720px;margin:auto;padding:40px;}
    h1{font-size:3rem;font-weight:800;letter-spacing:-0.03em;margin:0 0 16px;}
    .mbti{font-size:5rem;font-weight:800;color:#818cf8;
          font-family:'JetBrains Mono',ui-monospace,monospace;letter-spacing:0.05em;}
    .section{padding:32px;background:rgba(15,15,25,0.6);
             border:1px solid rgba(255,255,255,0.06);margin:20px 0;border-radius:16px;}
    .num{font-size:0.7rem;letter-spacing:0.12em;color:#5a5e72;display:block;margin-bottom:12px;}
    blockquote{background:rgba(129,140,248,0.06);border-left:2px solid #818cf8;
               padding:12px 16px;margin:10px 0;border-radius:8px;}
    blockquote span{display:inline-block;color:#c084fc;margin-right:12px;
                    font-family:'JetBrains Mono',monospace;font-size:0.75rem;}
    .dim{display:grid;grid-template-columns:60px 1fr 60px;gap:12px;
         align-items:center;margin:8px 0;}
    .dim-letter{font-family:'JetBrains Mono',monospace;color:#818cf8;}
    .bar{height:4px;background:rgba(255,255,255,0.08);border-radius:2px;overflow:hidden;}
    .bar-fill{height:100%;background:#818cf8;}
    .foot{margin-top:48px;color:#5a5e72;font-size:0.85rem;}
  </style>
</head>
<body>
  <main>
    <section>
      <div class="mbti">INTJ</div>
      <h1>一个安静的栏目</h1>
      <p style="color:#c084fc;">mock · 演示用名片</p>
    </section>
    <section class="section">
      <span class="num">01 · 看起来的你</span>
      <p>这是 mock 生成的第一段洞见占位文本。正式版由 LLM 按 CONVERGE.md 指导生成。
      这里多铺一些字数让它读起来像真的一段话。<sup>R1</sup></p>
    </section>
    <section class="section">
      <span class="num">02 · 停了一下</span>
      <p>第二段 mock 占位。真实版这里会写 TA 的矛盾或反差。再补点字数让节奏自然一点。<sup>R3</sup></p>
    </section>
    <section class="section">
      <span class="num">03 · 还没跟自己说的一句</span>
      <p>第三段 mock 占位。真实版这里会写 TA 整场对话里隐隐指向的东西。<sup>R5</sup></p>
    </section>
    <section class="section">
      <span class="num">维度</span>
      <div class="dim"><span class="dim-letter">I</span>
        <div class="bar"><div class="bar-fill" style="width:72%"></div></div>
        <span>0.72</span></div>
      <div class="dim"><span class="dim-letter">N</span>
        <div class="bar"><div class="bar-fill" style="width:65%"></div></div>
        <span>0.65</span></div>
      <div class="dim"><span class="dim-letter">T</span>
        <div class="bar"><div class="bar-fill" style="width:60%"></div></div>
        <span>0.60</span></div>
      <div class="dim"><span class="dim-letter">J</span>
        <div class="bar"><div class="bar-fill" style="width:58%"></div></div>
        <span>0.58</span></div>
    </section>
    <section class="section">
      <span class="num">你的原话</span>
      <blockquote><span>R1</span>占位原话一</blockquote>
    </section>
    <p class="foot">你不用今天就懂自己。</p>
  </main>
</body>
</html>
"""


class MockBackend(LLMBackend):
    """确定性脚本 mock · v2.5.2 · 文本流 + 收束 HTML。

    - `stream_text`：按轮数从 _MOCK_TURN_SCRIPTS 取一条文本，逐字 yield；
      末尾补一行 `STATUS: CONTINUE`。到第 8 轮改成 `STATUS: CONVERGE`。
    - `complete_text`：返回一份自包含 mock HTML 文档。
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
    async def complete_text(
        self,
        messages: List[Message],
        *,
        timeout: float = 300.0,
    ) -> str:
        return _MOCK_CONVERGE_HTML


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
