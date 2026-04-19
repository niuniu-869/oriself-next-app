"""
Skill runner · v2.5.2 主引擎。

设计：
- 对话轮 (`TurnRunner.stream_turn`) · 流式文本 async generator
    - 拼 system prompt：SKILL.md + ETHOS.md + domain + 当前 phase + techniques + exemplary
    - 拼 history：往轮的 (user, oriself_visible) 对
    - 调 backend.stream_text()，token 透传给上层
    - stream 结束后从尾部解析 STATUS sentinel，剥除，返回 ParsedTurn
    - **不 retry，不 fallback**。LLM 输出什么用户就看到什么。不满意由前端点「重写」。
- 报告轮 (`ReportRunner.compose`) · v2.5.2 · LLM 直吐 HTML
    - 独立 prompt：CONVERGE.md + domain + 元数据（session_id_short / today）
    - 调 backend.complete_text()，拿原始文本
    - 剥 markdown fence → shape/parse/extract/consistency 链
    - 最多 3 次 retry；失败返回 None，由 routes 层告诉用户「报告生成卡住了」
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
from dataclasses import dataclass, field
from typing import AsyncIterator, List, Optional, Tuple

from .guardrails import (
    ParsedTurn,
    parse_status_sentinel,
    resolve_mbti_or_fail,
    strip_markdown_fence,
    verify_report_html_parseable,
    verify_report_html_shape,
    extract_card_title_from_html,
)
from .llm_client import LLMBackend, Message
from .quill import derive_lines as _derive_quill_lines
from .schemas import (
    ConvergeOutput,
    MAX_ROUNDS,
    ONBOARDING_ROUND,
    REPORT_MAX_RETRIES,
    REPORT_TIMEOUT_SEC,
    UserPreferences,
    effective_target_rounds,
)
from .skill_loader import SkillBundle, load_skill_bundle
from .utils.prompt_sanitize import sanitize_user_input


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


@dataclass
class Turn:
    """最小轮记录。"""
    round_number: int
    user_message: str
    oriself_text: str = ""          # LLM 的可见文本（已剥 STATUS）
    status: str = "CONTINUE"        # CONTINUE / CONVERGE / NEED_USER
    discarded: bool = False         # 用户点「重写」后旧轮标这个
    quill_lines: List[str] = field(default_factory=list)  # v2.5.3 · 本轮给用户看的笔触批注


@dataclass
class SessionState:
    session_id: str
    domain: str
    turns: List[Turn] = field(default_factory=list)
    user_preferences: Optional[UserPreferences] = None

    @property
    def round_count(self) -> int:
        return len([t for t in self.turns if not t.discarded])

    def live_turns(self) -> List[Turn]:
        return [t for t in self.turns if not t.discarded]


# ---------------------------------------------------------------------------
# Phase 选择器
# ---------------------------------------------------------------------------


def _midpoint_round(target: int) -> int:
    return max(4, target // 2)


def _near_end_round(target: int) -> int:
    return max(_midpoint_round(target) + 2, target - 2)


def _collect_seen_from_history(
    session: SessionState,
    bundle: SkillBundle,
) -> Tuple[set, set]:
    """从 live turns 反推这封信里已经出现过的 phase / technique 集合。

    用途：quill 行"同 phase / 同 technique 只显示一次"的去重判定。
    注意 preferences 在 R2 前是 None，所以 R1 的 phase 算出来就是 phase-onboarding，
    不会漂移；后续 target_rounds 变化只会影响 midpoint / near_end 的具体轮号，
    不会让"已出现过"判定产生重复行。
    """
    seen_phases: set = set()
    seen_techs: set = set()
    for t in session.live_turns():
        pk = choose_phase_key(session, t.round_number)
        if not pk:
            continue
        seen_phases.add(pk)
        ref = bundle.refs.get(pk)
        if ref is None:
            continue
        needs = ref.meta.get("needs") or []
        if isinstance(needs, list):
            for n in needs:
                if n:
                    seen_techs.add(str(n))
    return seen_phases, seen_techs


def choose_phase_key(session: SessionState, current_round: int) -> str:
    """选本轮应加载的 phase 文件（v2.5.0 命名，去掉数字前缀）。

    - R1 → phase-onboarding
    - R_mid（target/2）且未做过 → phase-midpoint
    - R_near（target-2）且未做过 → phase-soft-closing
    - midpoint 之后 → phase-deep
    - 否则 → phase-warmup（R≤3）或 phase-exploring
    """
    if current_round == ONBOARDING_ROUND:
        return "phase-onboarding"

    target = effective_target_rounds(session.user_preferences)
    mid = _midpoint_round(target)
    near = _near_end_round(target)

    # v2.4 · 不再把 phase action_type 写进 turn；用轮号判断是否走过
    did_midpoint = current_round > mid
    did_soft_closing = current_round > near

    if current_round == mid and not did_midpoint:
        return "phase-midpoint"
    if current_round == near and not did_soft_closing:
        return "phase-soft-closing"
    if current_round > mid:
        return "phase-deep"
    if current_round <= 3:
        return "phase-warmup"
    return "phase-exploring"


# ---------------------------------------------------------------------------
# Prompt 组装辅助
# ---------------------------------------------------------------------------


def _runtime_state_block(
    session: SessionState,
    current_round: int,
    phase_key: str,
) -> str:
    target = effective_target_rounds(session.user_preferences)
    today = _dt.date.today()
    session_id_short = (session.session_id or "")[:8]
    prefs = session.user_preferences
    prefs_line = ""
    if prefs is not None and current_round > ONBOARDING_ROUND:
        prefs_line = (
            f"\n- 用户偏好（Phase 0 握手得出）：style={prefs.style}, "
            f"target_rounds={prefs.target_rounds or '(默认 20)'}, pace={prefs.pace}"
            f"{(', 开场情绪=' + prefs.opening_mood) if prefs.opening_mood else ''}"
            f"{(', 备注=' + prefs.note) if prefs.note else ''}"
        )
    return (
        f"\n\n---\n\n# Runtime State\n"
        f"- 当前轮：R{current_round}\n"
        f"- 本轮 phase：{phase_key}\n"
        f"- target_rounds_hint（soft）：{target}\n"
        f"- hard_cap：{MAX_ROUNDS}（到此服务端强制 converge）\n"
        f"- session_id_short：{session_id_short}\n"
        f"- 今日：{today.isoformat()}"
        f"{prefs_line}\n\n"
        f"# 记得结尾带 STATUS 行\n\n"
        f"最末一行独立写一个：`STATUS: CONTINUE` / `STATUS: CONVERGE` / `STATUS: NEED_USER`。"
        f"服务端会自动剥除，用户看不到这行。漏写按 CONTINUE 处理。"
    )


# ---------------------------------------------------------------------------
# TurnRunner · 对话轮流式
# ---------------------------------------------------------------------------


class TurnRunner:
    def __init__(
        self,
        backend: LLMBackend,
        bundle: Optional[SkillBundle] = None,
    ):
        self.backend = backend
        self.bundle = bundle or load_skill_bundle()

    def _build_conversation_messages(
        self,
        session: SessionState,
        user_message: str,
    ) -> List[Message]:
        current_round = session.round_count + 1
        phase_key = choose_phase_key(session, current_round)

        system_prompt = self.bundle.compose_conversation_prompt(
            domain=session.domain,
            phase_key=phase_key,
            current_round=current_round,
        )
        system_prompt += _runtime_state_block(session, current_round, phase_key)

        # v2.5.0 观测：打印每轮 system prompt 字节数，便于调优 progressive disclosure
        logger.info(
            "[skill-compose] round=%d phase=%s system_bytes=%d",
            current_round,
            phase_key,
            len(system_prompt.encode("utf-8")),
        )

        msgs: List[Message] = [
            Message(role="system", content=system_prompt, cache_breakpoint=True),
        ]
        for t in session.live_turns():
            msgs.append(Message(role="user", content=t.user_message))
            if t.oriself_text:
                msgs.append(Message(role="assistant", content=t.oriself_text))
        msgs.append(Message(role="user", content=user_message))
        return msgs

    async def stream_turn(
        self,
        session: SessionState,
        user_message_raw: str,
        *,
        rewrite_hint: Optional[str] = None,
    ) -> AsyncIterator[Tuple[str, object]]:
        """对话轮 · 流式生成。

        yields tuples of (kind, payload):
          - ("quill", List[str]) · token 开始前的笔触批注（0..2 条）
          - ("token", str) · token chunk，直接透传给用户
          - ("final",  "")  · 流结束前的标记（紧跟 status + visible）
          - ("status", CONTINUE/CONVERGE/NEED_USER)
          - ("visible", 完整剥除 STATUS 后的可见文本)
          - ("error", err_message)
        """
        user_message = sanitize_user_input(user_message_raw, max_length=4000)
        # 先算 quill（token 之前就要给前端，制造"Oriself 在落笔前停了一下"的节奏）
        current_round = session.round_count + 1
        phase_key = choose_phase_key(session, current_round)
        phase_ref = self.bundle.refs.get(phase_key)
        needs_list: List[str] = []
        if phase_ref is not None:
            raw_needs = phase_ref.meta.get("needs") or []
            if isinstance(raw_needs, list):
                needs_list = [str(n) for n in raw_needs if n]
        seen_phases, seen_techniques = _collect_seen_from_history(
            session, self.bundle
        )
        quill_lines, _, _ = _derive_quill_lines(
            phase_key=phase_key,
            needs=needs_list,
            seen_phases=seen_phases,
            seen_techniques=seen_techniques,
        )
        yield ("quill", quill_lines)

        messages = self._build_conversation_messages(session, user_message)
        if rewrite_hint:
            # 给 LLM 一个显式的"上一次不好"提示
            messages.append(
                Message(
                    role="system",
                    content=(
                        "[rewrite-hint] 用户对上一轮的回复不满意，请换一个说法重新回答。"
                        + (f"用户的意见：{rewrite_hint}" if rewrite_hint.strip() else "")
                    ),
                )
            )

        buffer = ""
        try:
            async for chunk in self.backend.stream_text(messages):
                if not chunk:
                    continue
                buffer += chunk
                yield ("token", chunk)
        except Exception as exc:
            # 原文进 server 日志用于排查；只把一个脱敏 code 送到前端，避免把
            # provider 名 / 原 JSON 糊到用户正在看的对话流里。
            logger.warning("stream_turn backend error: %s", exc)
            yield ("error", "UPSTREAM_LLM_STREAM_FAILED")
            return

        parsed = parse_status_sentinel(buffer)
        yield ("final", "")
        yield ("status", parsed.status)
        yield ("visible", parsed.visible_text)


# ---------------------------------------------------------------------------
# ReportRunner · 报告生成（唯一保留 schema + retry）
# ---------------------------------------------------------------------------


@dataclass
class ReportResult:
    output: Optional[ConvergeOutput]
    retries: int
    error_reasons: List[str]


class ReportRunner:
    def __init__(
        self,
        backend: LLMBackend,
        bundle: Optional[SkillBundle] = None,
    ):
        self.backend = backend
        self.bundle = bundle or load_skill_bundle()

    def _build_converge_messages(
        self,
        session: SessionState,
        retry_hint: Optional[str] = None,
    ) -> List[Message]:
        system = self.bundle.compose_converge_prompt(domain=session.domain)

        today = _dt.date.today()
        session_id_short = (session.session_id or "")[:8]
        target = effective_target_rounds(session.user_preferences)
        live = session.live_turns()

        # 历史对话作为 user 消息一次性塞入
        transcript_lines = []
        for t in live:
            transcript_lines.append(f"[R{t.round_number} · user]\n{t.user_message}")
            if t.oriself_text:
                transcript_lines.append(f"[R{t.round_number} · oriself]\n{t.oriself_text}")
        transcript = "\n\n".join(transcript_lines)

        meta_block = (
            f"# 元数据（直接写进 HTML，不要留占位符）\n"
            f"- session_id_short: {session_id_short}\n"
            f"- today_iso: {today.isoformat()}\n"
            f"- today_en: {today.strftime('%b %d, %Y')}\n"
            f"- today_cn: {today.year} 年 {today.month} 月 {today.day} 日\n"
            f"- 对话总轮数: {len(live)}\n"
            f"- target_rounds_hint: {target}\n"
        )

        msgs: List[Message] = [
            Message(role="system", content=system, cache_breakpoint=True),
            Message(role="user", content=meta_block + "\n\n# 完整对话\n\n" + transcript),
        ]
        if retry_hint:
            msgs.append(
                Message(
                    role="system",
                    content="[retry hint] 上一次的输出被拒绝，原因：\n" + retry_hint,
                )
            )
        return msgs

    async def compose(self, session: SessionState) -> ReportResult:
        """v2.5.2 · LLM 直吐 HTML，不走 JSON。

        校验链（任一失败即 retry）：
        1. backend 调用本身（网络 / timeout / 4xx）
        2. strip_markdown_fence（剥潜在 ```html … ``` 包装）
        3. verify_report_html_shape（doctype / html / 无 script/iframe / 无模板占位）
        4. verify_report_html_parseable（html.parser 能扫完 + 可见文本 ≥ 30 字符）
        5. resolve_mbti_or_fail（可见文本里有且仅有一个 4 字母 MBTI）
        6. 抽 <title> 作为 card_title
        """
        last_reasons: List[str] = []
        for attempt in range(REPORT_MAX_RETRIES):
            hint = "\n".join(last_reasons[:5]) if attempt > 0 else None
            messages = self._build_converge_messages(session, retry_hint=hint)

            try:
                raw = await self.backend.complete_text(
                    messages, timeout=REPORT_TIMEOUT_SEC
                )
            except Exception as exc:
                last_reasons = [f"LLM backend error: {exc}"]
                logger.warning("converge attempt %d backend error: %s", attempt + 1, exc)
                continue

            html = strip_markdown_fence(raw or "").strip()

            shape = verify_report_html_shape(html)
            if not shape.passed:
                last_reasons = shape.reasons
                logger.info(
                    "converge attempt %d html-shape fail: %s", attempt + 1, last_reasons
                )
                continue

            parseable = verify_report_html_parseable(html)
            if not parseable.passed:
                last_reasons = parseable.reasons
                logger.info(
                    "converge attempt %d html-parse fail: %s", attempt + 1, last_reasons
                )
                continue

            mbti_type, mbti_result = resolve_mbti_or_fail(html)
            if not mbti_result.passed or mbti_type is None:
                last_reasons = mbti_result.reasons
                logger.info(
                    "converge attempt %d mbti-resolve fail: %s", attempt + 1, last_reasons
                )
                continue

            card_title = extract_card_title_from_html(html)

            try:
                output = ConvergeOutput(
                    mbti_type=mbti_type,
                    card_title=card_title,
                    report_html=html,
                )
            except Exception as exc:
                last_reasons = [f"ConvergeOutput validate: {exc}"]
                logger.info(
                    "converge attempt %d schema fail: %s", attempt + 1, last_reasons
                )
                continue

            return ReportResult(output=output, retries=attempt, error_reasons=[])

        return ReportResult(
            output=None, retries=REPORT_MAX_RETRIES, error_reasons=last_reasons
        )


# ---------------------------------------------------------------------------
# State 演进
# ---------------------------------------------------------------------------


def advance_state(
    session: SessionState,
    user_message: str,
    oriself_visible: str,
    status: str,
) -> SessionState:
    """把完成的一轮追加到 session。返回新的 SessionState（不可变风格）。"""
    round_number = session.round_count + 1
    new_turn = Turn(
        round_number=round_number,
        user_message=user_message,
        oriself_text=oriself_visible,
        status=status,
        discarded=False,
    )

    new_prefs = session.user_preferences
    if new_prefs is None and round_number == ONBOARDING_ROUND + 1:
        new_prefs = _parse_preferences_heuristic(user_message)

    return SessionState(
        session_id=session.session_id,
        domain=session.domain,
        turns=session.turns + [new_turn],
        user_preferences=new_prefs,
    )


# ---------------------------------------------------------------------------
# R2 启发式解析偏好（保留 v2.3 实现）
# ---------------------------------------------------------------------------


def _parse_preferences_heuristic(text: str) -> UserPreferences:
    lower = (text or "").strip()

    style = "default"
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

    pace = "default"
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
