"""
多 provider LLM 客户端 · v2.6.0。

v2.6.0 变化（真模型按需）：
- 新增 `call_tools_only(messages, tools)` 抽象：Pass 1 工具规划契约。
  非流式、强制返回 tool_calls、message.content 永远丢弃。
- OpenAICompatibleBackend 用 `tool_choice="required"` 实现（deepseek/qwen/openai 都支持）。
- MockBackend 给出 happy-path fixture：每轮选 1 phase + 0..2 technique，R1-R3 含 exemplary-session。

v2.5.2 保留：
- converge 走 `complete_text` 直吐 HTML；timeout 默认 300s。
- 对话轮 `stream_text`，provider 不传 `response_format`。

支持的 provider：
- `openai_compatible`（Qwen / DeepSeek / Kimi / OpenAI / 302.ai Gemini 等兼容端）
- `mock` · 确定性 · 无 key / 单测 / 演示
"""
from __future__ import annotations

import json
import os
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, List, Optional

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
# Tool call（v2.6 · Pass 1 协议契约）
# ---------------------------------------------------------------------------


@dataclass
class ToolCallRequest:
    """LLM 在 Pass 1 调用工具的解析结果。

    `arguments` 已经由调用方解析为 dict；解析失败时 `arguments_parse_error` 非空，
    此时 arguments={}，供上层判定 `invalid_skill` 等违规。
    """

    name: str
    arguments: Dict[str, object] = field(default_factory=dict)
    raw_arguments: str = ""
    call_id: Optional[str] = None
    arguments_parse_error: Optional[str] = None


@dataclass
class Pass1Result:
    """Pass 1 完整返回结构，供 harness 落 trace 用。

    `content_dropped` 始终为 message.content 原文：协议要求**整段丢弃**，
    但仍要记录到 DB 让 benchmark 能看到 LLM 是否在 Pass 1 偷写了正文。
    """

    tool_calls: List[ToolCallRequest] = field(default_factory=list)
    content_dropped: str = ""
    raw_response: Dict[str, object] = field(default_factory=dict)


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

    @abstractmethod
    async def call_tools_only(
        self,
        messages: List[Message],
        tools: List[dict],
        *,
        timeout: float = 60.0,
        tool_choice: str = "required",
    ) -> Pass1Result:
        """Pass 1 · 工具规划契约。

        - 非流式调用，禁止 stream
        - 服务端要求 `tool_choice="required"`，强制 LLM 选一个工具
        - message.content 一律丢弃（v2.6 ADR-2）；调用方只读 tool_calls
        - 失败抛异常；不做兜底（v2.6 ADR-6 · 不兜底但全可观测）
        """
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

    # ---- Pass 1 · 工具规划契约（v2.6） ----
    async def call_tools_only(
        self,
        messages: List[Message],
        tools: List[dict],
        *,
        timeout: float = 60.0,
        tool_choice: str = "required",
    ) -> Pass1Result:
        """OpenAI compatible：tool_choice="required"，stream=False，丢弃 content。

        provider 兼容性：
        - OpenAI / DeepSeek / Qwen DashScope / Kimi 都支持 tool_choice="required"。
        - Gemini-302 走 OpenAI compatible 路径同样支持。
        - 任何 provider 报错（4xx/5xx）直接抛异常，不兜底——这是 v2.6 ADR-6。
        """
        payload: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "tools": tools,
            "tool_choice": tool_choice,
            "stream": False,
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
            if resp.status_code >= 400:
                body = resp.text
                raise RuntimeError(
                    f"{self.provider_name} call_tools_only "
                    f"{resp.status_code}: {body[:400]}"
                )
            data = resp.json()
        return _parse_pass1_response(data)


# ---------------------------------------------------------------------------
# Pass 1 解析 helper
# ---------------------------------------------------------------------------


def _parse_pass1_response(data: dict) -> Pass1Result:
    """OpenAI compatible chat/completions 响应 → Pass1Result。

    - 解析 `choices[0].message.tool_calls`，每个含 function.name / arguments。
    - arguments 是字符串，用 json.loads 解析；失败时把原文留在 raw_arguments，
      并记 arguments_parse_error，上层按 `invalid_skill` 处置。
    - message.content 整段保留到 content_dropped 字段（不还给 caller，但落 trace 用）。
    """
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("call_tools_only: empty choices in response")
    msg = choices[0].get("message") or {}
    tool_calls_raw = msg.get("tool_calls") or []
    parsed_calls: List[ToolCallRequest] = []
    for tc in tool_calls_raw:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = str(fn.get("name") or "")
        raw_args = fn.get("arguments")
        if raw_args is None:
            raw_args_str = ""
        elif isinstance(raw_args, str):
            raw_args_str = raw_args
        else:
            raw_args_str = json.dumps(raw_args, ensure_ascii=False)
        parsed_args: Dict[str, object] = {}
        parse_err: Optional[str] = None
        if raw_args_str:
            try:
                obj = json.loads(raw_args_str)
                if isinstance(obj, dict):
                    parsed_args = obj
                else:
                    parse_err = f"arguments not object: {type(obj).__name__}"
            except json.JSONDecodeError as exc:
                parse_err = f"json decode: {exc}"
        parsed_calls.append(
            ToolCallRequest(
                name=name,
                arguments=parsed_args,
                raw_arguments=raw_args_str,
                call_id=str(tc.get("id") or "") or None,
                arguments_parse_error=parse_err,
            )
        )
    content = msg.get("content")
    return Pass1Result(
        tool_calls=parsed_calls,
        content_dropped=content if isinstance(content, str) else "",
        raw_response=data,
    )


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
    """确定性脚本 mock · v2.6.0。

    - `stream_text`：按轮数从 _MOCK_TURN_SCRIPTS 取一条文本，逐字 yield；
      末尾补一行 `STATUS: CONTINUE`。到第 8 轮改成 `STATUS: CONVERGE`。
    - `complete_text`：返回一份自包含 mock HTML 文档。
    - `call_tools_only`（v2.6 新增）：happy-path fixture，按推断轮数返回
      合规 tool_calls：1 phase + 0..2 technique；R1-R3 含 exemplary-session。
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

    # ---- Pass 1 · 工具规划契约 ----
    async def call_tools_only(
        self,
        messages: List[Message],
        tools: List[dict],
        *,
        timeout: float = 60.0,
        tool_choice: str = "required",
    ) -> Pass1Result:
        """Mock fixture：基于消息里的 user-轮数推断 phase 选择。

        规则（codex 第 7 轮 P3 修复 · 不依赖实例状态）：
        HTTP 路由每轮 make_backend 创建新 MockBackend 实例 → 实例字段会重置；
        因此跨轮去重只能基于 messages 推断的 user_rounds 数（=本轮号），用一份
        固定的 R→names 映射来保证：
        - phase 每轮强制选（R≥4 仍 重复同一 phase 是协议常态，服务端不记 redundant）
        - R1-R3 协议必选 exemplary-session（服务端 R1-R3 跨轮重读豁免）
        - technique 在第一次进入需要它的 phase 时选；后续同 phase 轮不再选
        - 永远只调 read_skill 一次；arguments.names 控制在 ≤ 4

        固定 R→names 映射：
        - R1: phase-onboarding + exemplary-session
        - R2: phase-warmup + reflective-listening + exemplary-session
        - R3: phase-warmup + exemplary-session  (reflective-listening 已在 R2 选过)
        - R4: phase-exploring + situational-questions
        - R5: phase-exploring  (situational-questions 已在 R4 选过)
        - R6: phase-deep + contradiction-probing
        - R7+: phase-deep  (contradiction-probing / situational-questions 已选)
        """
        user_rounds = [m for m in messages if m.role == "user"]
        rn = max(1, len(user_rounds))
        if rn == 1:
            names = ["phase-onboarding", "exemplary-session"]
        elif rn == 2:
            names = ["phase-warmup", "reflective-listening", "exemplary-session"]
        elif rn == 3:
            names = ["phase-warmup", "exemplary-session"]
        elif rn == 4:
            names = ["phase-exploring", "situational-questions"]
        elif rn == 5:
            names = ["phase-exploring"]
        elif rn == 6:
            names = ["phase-deep", "contradiction-probing"]
        else:
            names = ["phase-deep"]
        args = {"names": names}
        raw_args = json.dumps(args, ensure_ascii=False)
        return Pass1Result(
            tool_calls=[
                ToolCallRequest(
                    name="read_skill",
                    arguments=args,
                    raw_arguments=raw_args,
                    call_id=f"mock-call-{rn}",
                )
            ],
            content_dropped="",
            raw_response={"mock": True, "round": rn},
        )


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
