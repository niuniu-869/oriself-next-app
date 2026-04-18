"""
Skill runner · v2.1/v2.2 主引擎。

设计转向：
- 不再在 system prompt 里一次性塞入所有阶段的指令。每轮按当前 round 和
  session 状态挑出**一个** phase 文件拼入 system prompt。避免 LLM 到后期
  忘记前面堆的阶段规则。
- 不再按关键词硬注入 "情绪红灯 → 必须 warm_echo" 之类的强制指令。
  信任模型自己判断；phase 文件里用朋友口吻描述"碰到这种信号该怎么做"。
- quiz 答案的历史压缩：上一轮 scenario_quiz 的 XML 回答会被归纳成自然语言
  摘要，避免结构化答案污染后续轮的语气信号。
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import threading
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Optional, Tuple

from .guardrails import (
    OriSelfGuardrails,
    SessionState,
    Turn,
    fallback_action,
    observe_defensive_exit,
    observe_emotional_hint,
    observe_sensitive_hint,
)
from .llm_client import LLMBackend, Message
from .schemas import (
    Action,
    MAX_RETRIES,
    MAX_ROUNDS,
    ONBOARDING_ROUND,
    UserPreferences,
    effective_target_rounds,
    midpoint_round,
    near_end_round,
)
from .skill_loader import SkillBundle, load_skill_bundle
from .utils.prompt_sanitize import sanitize_user_input


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class TurnResult:
    action: Action
    retries: int
    used_fallback: bool
    guardrail_reasons: List[str]


# ---------------------------------------------------------------------------
# Phase 选择器
# ---------------------------------------------------------------------------


def choose_phase_key(session: SessionState, current_round: int) -> str:
    """根据当前轮号和 session 状态，决定本轮应该加载哪一页 phase 文件。

    规则：
    - R1 → phase0-onboarding
    - R == midpoint(target) 且尚未做过 → phase3_5-midpoint
    - R == near_end(target) 且尚未做过 → phase4_8-soft-closing
    - R >= near_end 且 soft_closing 已完成 → phase5-converge
    - midpoint+1 到 near_end-1 → phase4-deep
    - 否则（R2 到 midpoint-1） → 根据已有 evidence 数选 phase1 或 phase2-3
    """
    if current_round == ONBOARDING_ROUND:
        return "phase0-onboarding"

    target = effective_target_rounds(session.user_preferences)
    mid = midpoint_round(target)
    near = near_end_round(target)

    did_midpoint = any(t.action_type == "midpoint_reflect" for t in session.turns)
    did_soft_closing = any(t.action_type == "soft_closing" for t in session.turns)

    if current_round == mid and not did_midpoint:
        return "phase3_5-midpoint"
    if current_round == near and not did_soft_closing:
        return "phase4_8-soft-closing"

    # 超过 near_end 且软收束完成 → converge
    if current_round >= near and did_soft_closing:
        return "phase5-converge"

    # midpoint 之后 → deep
    if current_round > mid:
        return "phase4-deep"

    # 前半段：根据 evidence 数量区分 warmup vs exploring
    total_ev = len(session.collected_evidence)
    if current_round <= 3 or total_ev < 2:
        return "phase1-warmup"
    return "phase2-3-exploring"


# ---------------------------------------------------------------------------
# Quiz 答案压缩（历史注入时使用）
# ---------------------------------------------------------------------------


_QUIZ_XML_RE = re.compile(r"<\s*quiz_answers[^>]*>([\s\S]*?)<\s*/\s*quiz_answers\s*>", re.IGNORECASE)
_ANSWER_RE = re.compile(r"<\s*answer\s*>([\s\S]*?)<\s*/\s*answer\s*>", re.IGNORECASE)
_QID_RE = re.compile(r"<\s*qid\s*>([\s\S]*?)<\s*/\s*qid\s*>", re.IGNORECASE)
_CHOICE_RE = re.compile(r"<\s*choice\s*>([\s\S]*?)<\s*/\s*choice\s*>", re.IGNORECASE)
_CUSTOM_RE = re.compile(r"<\s*custom\s*>([\s\S]*?)<\s*/\s*custom\s*>", re.IGNORECASE)


def compress_quiz_answer(raw: str) -> str:
    """把结构化 quiz 答案 XML 压成一行自然语言；如果不是 XML 就原样返回。

    入参示例：<quiz_answers><answer><qid>q1</qid><choice>B</choice></answer>...</quiz_answers>
    出参：`[quiz] q1=B, q2=A;C (自定义: ...)`
    """
    if not raw:
        return raw
    m = _QUIZ_XML_RE.search(raw)
    if not m:
        # 不是结构化 XML，可能是用户直接打的"我选 B"之类，原样留
        return raw
    body = m.group(1)
    parts: List[str] = []
    for ans_match in _ANSWER_RE.finditer(body):
        ans_body = ans_match.group(1)
        qid_m = _QID_RE.search(ans_body)
        choice_m = _CHOICE_RE.search(ans_body)
        custom_m = _CUSTOM_RE.search(ans_body)
        qid = qid_m.group(1).strip() if qid_m else "?"
        choice = choice_m.group(1).strip() if choice_m else ""
        custom = custom_m.group(1).strip() if custom_m else ""
        seg = f"{qid}={choice}" if choice else qid
        if custom:
            seg += f"(自定义:{custom[:40]})"
        parts.append(seg)
    return "[quiz 答案] " + "; ".join(parts) if parts else raw


# ---------------------------------------------------------------------------
# v2.2 · 报告 HTML 落盘 + localhost 预览
# ---------------------------------------------------------------------------


DEFAULT_REPORT_DIR = Path("/tmp/oriself_reports")


def write_report_html(
    session_id: str,
    html: str,
    out_dir: Optional[Path] = None,
) -> Path:
    """把 converge 出的 HTML 写到磁盘，返回绝对路径。

    文件名使用 session_id 前缀，便于追踪；父目录会自动创建。
    """
    out_dir = out_dir or DEFAULT_REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = (out_dir / f"{session_id}.html").resolve()
    out_path.write_text(html, encoding="utf-8")
    return out_path


class _SilentHandler(SimpleHTTPRequestHandler):
    """静音版 SimpleHTTPRequestHandler — 不把访问日志喷到 stderr。"""

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def serve_report_localhost(
    html_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
) -> Tuple[str, ThreadingHTTPServer, threading.Thread]:
    """在 localhost 起一个只读静态服务器，返回 (url, server, thread)。

    port=0 时由 OS 随机分配可用端口，避免冲突。线程 daemon=True，进程退出自动关。
    调用方如果要长驻前台，拿到 server 后自己 `server.serve_forever()` 即可；
    这里默认后台线程方式。
    """
    html_path = html_path.resolve()
    serve_dir = html_path.parent
    handler = partial(_SilentHandler, directory=str(serve_dir))
    server = ThreadingHTTPServer((host, port), handler)
    real_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://{host}:{real_port}/{html_path.name}"
    return url, server, thread


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class SkillRunner:
    def __init__(
        self,
        backend: LLMBackend,
        bundle: Optional[SkillBundle] = None,
        guardrails: Optional[OriSelfGuardrails] = None,
    ):
        self.backend = backend
        self.bundle = bundle or load_skill_bundle()
        self.guardrails = guardrails or OriSelfGuardrails(self.bundle)

    # ------------------------------------------------------------------
    # Prompt 组装（v2.1 精简 + 懒加载 phase）
    # ------------------------------------------------------------------
    def _build_messages(
        self, session: SessionState, user_message: str
    ) -> List[Message]:
        current_round = session.round_count + 1
        target_rounds = effective_target_rounds(session.user_preferences)

        phase_key = choose_phase_key(session, current_round)
        system_prompt = self.bundle.compose_system_prompt(
            domain=session.domain,
            phase_key=phase_key,
        )

        # 维度 evidence 计数（仅作 runtime state 回报，不硬性指定 LLM 做什么）
        dim_counts: dict[str, int] = {"E/I": 0, "S/N": 0, "T/F": 0, "J/P": 0}
        for ev in session.collected_evidence:
            if ev.dimension in dim_counts:
                dim_counts[ev.dimension] += 1
        weakest = min(dim_counts.items(), key=lambda x: x[1])
        # v2.3：用符号体现"饱满/缺口"，比光看数字更直观——让 LLM 一眼
        # 看见哪些维度已经够 (>=3)、哪些还缺。这是 **信号注入**，不是禁令。
        dim_summary = ", ".join(
            f"{d}={c}{'✓' if c >= 3 else '·缺'}" for d, c in dim_counts.items()
        )
        deficit_dims = [d for d, c in dim_counts.items() if c < 3]
        deficit_hint = (
            f"\n- 距离收敛还缺：{deficit_dims}（每维度 ≥3 条才能收敛）"
            if deficit_dims else
            "\n- 四维证据已齐（每维度 ≥3 条），可以准备收敛。"
        )

        # 软观察（只提供信息，不强制 action）
        emo = observe_emotional_hint(user_message)
        sens = observe_sensitive_hint(user_message)
        defensive = observe_defensive_exit(user_message)

        signal_note = ""
        observations: List[str] = []
        if emo:
            observations.append("用户当轮话里有情绪词（参考）")
        if sens:
            observations.append("话题碰到敏感面（参考）")
        if defensive:
            observations.append("用户似乎想撤退（参考：如明示，请温柔接受并换话题）")
        if observations:
            signal_note = "\n\n**本轮观察**（仅供你判断，不是指令）：\n- " + "\n- ".join(observations)

        # 偏好回读（R2+）
        prefs_note = ""
        if session.user_preferences is not None and current_round > ONBOARDING_ROUND:
            p = session.user_preferences
            prefs_note = (
                f"\n\n**用户偏好**（Phase 0 已握手，记得尊重）：\n"
                f"- 风格：{p.style}\n"
                f"- 期望轮数：{p.target_rounds or '(默认 20)'}\n"
                f"- 节奏：{p.pace}\n"
                f"- 开场情绪：{p.opening_mood or '(未说)'}\n"
                f"- 备注：{p.note or '(未说)'}"
            )

        # 上一轮是不是 quiz（影响"连续 quiz 禁"的软提示）
        last_was_quiz = (
            session.turns and session.turns[-1].action_type == "scenario_quiz"
        )
        quiz_hint = ""
        if last_was_quiz:
            quiz_hint = (
                "\n\n**提醒**：上一轮刚做过 scenario_quiz，这一轮不能再 quiz。"
                "用 open 风格承接用户的回答（引一两个 TA 的选择展开聊）。"
            )

        # v2.2.3 · 给 converge 用的具体元数据（避免 LLM 留 {{session_id}} 之类占位符）
        today = _dt.date.today()
        session_id_short = (session.session_id or "")[:8]
        meta_lines = (
            f"\n- session_id_short：{session_id_short}\n"
            f"- today_iso：{today.isoformat()}\n"
            f"- today_en：{today.strftime('%b %d, %Y')}\n"
            f"- today_cn：{today.year} 年 {today.month} 月 {today.day} 日"
        )

        # v2.3 · runtime_hint 改走**正向描述**（"输出形式是 X"）而不是
        # "不要做 X"。负向指令 LLM 反而更容易注意到违禁词；正向锚定
        # 输出结构，让"合法输出"成为默认路径。
        runtime_hint = (
            f"\n\n---\n\n# Runtime State\n"
            f"- 当前轮：R{current_round}\n"
            f"- 本轮 phase：{phase_key}\n"
            f"- 硬上限 MAX_ROUNDS：{MAX_ROUNDS}\n"
            f"- 期望轮数（soft target）：{target_rounds}\n"
            f"- 中期回顾轮：R{midpoint_round(target_rounds)}\n"
            f"- 尾声提醒轮：R{near_end_round(target_rounds)}\n"
            f"- 每维度证据：{dim_summary}"
            f"{deficit_hint}\n"
            f"- 目前最弱维度：{weakest[0]}（{weakest[1]} 条）"
            f"{meta_lines}"
            f"{signal_note}{prefs_note}{quiz_hint}\n\n"
            f"# 本轮输出契约\n\n"
            f"**形式**：一个 JSON object，以 `{{` 开始、`}}` 结束，全程无额外文字、"
            f"无 markdown fence。\n\n"
            f"**evidence 的 round_number 是忠实标注**：若本轮 quote 从 R{current_round} "
            f"user_message 抽，填 {current_round}；若回引历史轮的原话（做 reflect/probe "
            f"的 contextual quote），就填那一真实轮号。quote 本身必须是该轮 "
            f"user_message 的字面子串。\n\n"
            f"**converge 的字母一致性**（仅 converge 轮）：MBTI 4 字母的单一真相源是 "
            f"`confidence_per_dim[*].letter`——runtime 会从这里派生 `mbti_type`。"
            f"`report_html` 里出现任何 4 字母 MBTI 串时，请与 "
            f"`confidence_per_dim` 派生值保持字面一致（方法：先在脑子里拼好 4 字母，"
            f"再在 HTML 里**只用那一个字符串**，无论出现在 title / meta / footer 都同一个）。\n\n"
            f"**converge HTML 的元值**：用下面的真实值直接写进 HTML。\n"
            f"  - session 短号：`{session_id_short}`\n"
            f"  - 今天日期可选风格：`{today.isoformat()}` / "
            f"`{today.strftime('%b %d, %Y')}` / "
            f"`{today.year} 年 {today.month} 月 {today.day} 日`"
        )

        msgs: List[Message] = [
            Message(role="system", content=system_prompt + runtime_hint, cache_breakpoint=True),
        ]

        # v2.3 · 历史轮和当前轮用结构化 tag 隔离。
        # 过去的观察：LLM 把 R14 的原话当 R16 塞进本轮 evidence。根因是
        # prompt 里 `[Rn] msg` 这种平铺格式让 LLM 把所有历史轮当"流水帐"，
        # 对"本轮 anchor"感知不强。解法：历史轮打 <history_turn>，当前
        # 轮打 <current_turn> + 系统 message 末尾再次 anchor。
        for t in session.turns:
            user_msg_text = t.user_message
            if t.action_type == "scenario_quiz" or user_msg_text.strip().startswith("<quiz_answers"):
                user_msg_text = compress_quiz_answer(user_msg_text)
            msgs.append(
                Message(
                    role="user",
                    content=(
                        f'<history_turn round="{t.round_number}">\n'
                        f"{user_msg_text}\n"
                        f"</history_turn>"
                    ),
                )
            )

        # 本轮新消息 · 独立 anchor。
        msgs.append(
            Message(
                role="user",
                content=(
                    f'<current_turn round="{current_round}">\n'
                    f"{user_message}\n"
                    f"</current_turn>"
                ),
            )
        )
        return msgs

    # ------------------------------------------------------------------
    # Runtime · step()
    # ------------------------------------------------------------------
    async def step(self, session: SessionState, user_message_raw: str) -> TurnResult:
        user_message = sanitize_user_input(user_message_raw, max_length=4000)
        round_number = session.round_count + 1
        phase_key = choose_phase_key(session, round_number)

        last_reasons: List[str] = []
        last_action: Optional[Action] = None

        for attempt in range(MAX_RETRIES):
            messages = self._build_messages(session, user_message)
            if attempt > 0:
                messages.append(
                    Message(
                        role="system",
                        content=(
                            "[retry hint] 上一次的输出被 guardrails 拒绝，原因：\n- "
                            + "\n- ".join(last_reasons[:6])
                            + "\n请严格按 ACTION JSON SCHEMA 重试。"
                        ),
                    )
                )
            try:
                raw = await self.backend.complete_json(messages)
            except Exception as exc:
                last_reasons = [f"LLM backend error: {exc}"]
                logger.warning("LLM error on attempt %d: %s", attempt + 1, exc)
                continue

            schema_result = self.guardrails.validate_action_schema(raw)
            if not schema_result.passed:
                last_reasons = schema_result.reasons
                logger.info("schema check failed attempt %d: %s", attempt + 1, last_reasons)
                continue

            action = Action.model_validate(raw)
            last_action = action

            temp_turn = Turn(
                round_number=round_number,
                user_message=user_message,
            )
            session_for_check = SessionState(
                session_id=session.session_id,
                domain=session.domain,
                turns=session.turns + [temp_turn],
                collected_evidence=session.collected_evidence,
                user_preferences=session.user_preferences,
            )
            full_result = self.guardrails.validate_action(
                action,
                session_for_check,
                round_number,
                current_user_message=user_message,
            )
            if not full_result.passed:
                last_reasons = full_result.reasons
                logger.info(
                    "guardrails failed attempt %d: %s", attempt + 1, last_reasons
                )
                continue

            return TurnResult(
                action=action,
                retries=attempt,
                used_fallback=False,
                guardrail_reasons=[],
            )

        logger.warning(
            "all %d retries exhausted for round %d, using fallback. reasons=%s",
            MAX_RETRIES,
            round_number,
            last_reasons,
        )
        return TurnResult(
            action=fallback_action(
                round_number,
                session,
                reject_reasons=last_reasons,
            ),
            retries=MAX_RETRIES,
            used_fallback=True,
            guardrail_reasons=last_reasons,
        )

    # ------------------------------------------------------------------
    # 状态演进
    # ------------------------------------------------------------------
    def advance_state(
        self,
        session: SessionState,
        user_message: str,
        result: TurnResult,
    ) -> SessionState:
        round_number = session.round_count + 1
        new_turn = Turn(
            round_number=round_number,
            user_message=user_message,
            action_type=result.action.action,
            dimension_targeted=result.action.dimension_targeted,
            evidence=list(result.action.evidence),
            emotional_hint=observe_emotional_hint(user_message),
            sensitive_hint=observe_sensitive_hint(user_message),
        )
        seen = {(e.dimension, e.user_quote) for e in session.collected_evidence}
        new_collected = list(session.collected_evidence)
        for e in result.action.evidence:
            key = (e.dimension, e.user_quote)
            if key in seen:
                continue
            seen.add(key)
            new_collected.append(e)

        # R2 · onboarding 的回答 → 启发式抽偏好
        new_prefs = session.user_preferences
        if new_prefs is None and round_number == ONBOARDING_ROUND + 1:
            new_prefs = _parse_preferences_heuristic(user_message)

        return SessionState(
            session_id=session.session_id,
            domain=session.domain,
            turns=session.turns + [new_turn],
            collected_evidence=new_collected,
            user_preferences=new_prefs,
        )


# ---------------------------------------------------------------------------
# Phase 0 · preferences 启发式解析
# ---------------------------------------------------------------------------


def _parse_preferences_heuristic(text: str) -> UserPreferences:
    lower = (text or "").strip()

    style: str = "default"
    if any(w in lower for w in ("轻松", "随便", "扯扯", "闲聊", "放松", "随意")):
        style = "casual"
    if any(w in lower for w in ("深入", "聊深点", "聊到一些", "深点", "更深")):
        style = "deep"
    if any(w in lower for w in ("文艺", "诗意", "浪漫")):
        style = "literary"
    if any(w in lower for w in ("理性", "分析", "逻辑")):
        style = "analytical"

    target_rounds: Optional[int] = None
    m = re.search(r"(\d{1,2})\s*轮", lower)
    if m:
        try:
            n = int(m.group(1))
            if 6 <= n <= 30:
                target_rounds = n
        except ValueError:
            pass
    if target_rounds is None:
        if any(w in lower for w in ("短", "快", "10-15", "10 到 15")):
            target_rounds = 12
        elif any(w in lower for w in ("慢慢聊", "慢一点", "25", "30", "25-30", "长一点")):
            target_rounds = 25
        elif any(w in lower for w in ("标准", "20 轮左右", "一般", "20")):
            target_rounds = 20

    pace: str = "default"
    if any(w in lower for w in ("慢", "不急")):
        pace = "slow"
    if any(w in lower for w in ("快", "效率", "抓紧")):
        pace = "quick"
    if any(w in lower for w in ("稳", "标准")):
        pace = "steady"

    return UserPreferences(
        style=style,  # type: ignore[arg-type]
        target_rounds=target_rounds,
        pace=pace,  # type: ignore[arg-type]
        opening_mood=(text or "")[:200] if text else None,
    )
