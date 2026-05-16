"""
Skill runner · v2.6.0 主引擎。

模式：
- `loader_mode="static"`（默认 · 等价 v2.5.x）：
    - 拼 system prompt：SKILL.md + ETHOS.md + domain + 当前 phase + techniques + exemplary
    - 拼 history → backend.stream_text() → token 透传
    - stream 结束后从尾部解析 STATUS sentinel，剥除，返回 ParsedTurn
    - **不 retry，不 fallback**。LLM 输出什么用户就看到什么。不满意由前端点「重写」。
- `loader_mode="on-demand"`（v2.6 · 真模型按需）：
    - Pass 1（call_tools_only）：system = SKILL+ETHOS+Runtime+Skill Index；
      tools = [read_skill]；强制只回 tool_calls；message.content 整段丢弃
    - read_skill_batch + 6 项校验（zero_tool_read / over_budget / invalid_skill /
      phase_missing / exemplary_skipped / redundant_read）—— **不兜底只记录**
    - Pass 2（stream_text）：system = SKILL+ETHOS+Runtime+Loaded Skills；tools=[]
    - 末尾 STATUS sentinel 解析仅在 Pass 2

报告轮 (`ReportRunner.compose`) · v2.5.2 · LLM 直吐 HTML（与对话轮无关，不动）。
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, List, Optional, Tuple

from .guardrails import (
    ParsedTurn,
    parse_status_sentinel,
    resolve_mbti_or_fail,
    strip_markdown_fence,
    verify_report_html_parseable,
    verify_report_html_shape,
    extract_card_title_from_html,
)
from .llm_client import LLMBackend, Message, Pass1Result, ToolCallRequest
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
from .skill_loader import (
    LoadedSkill,
    ReadSkillResult,
    SkillBundle,
    SkillViolation,
    load_skill_bundle,
    read_skill_batch,
    read_skill_tool_schema,
)
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
    # v2.6 · 本轮 Pass 1 真实加载到的 skill 名字（routes 层 `_load_session_state`
    # 从 conversations.loaded_skill_names 列读出回灌；on-demand 模式才有值）
    loaded_skills: List[str] = field(default_factory=list)


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


def _runtime_state_block_pass1(
    session: SessionState,
    current_round: int,
    target_rounds: int,
    *,
    bundle: Optional[SkillBundle] = None,
) -> str:
    """v2.6 Pass 1 用的 Runtime State 块。

    codex 第 5 轮 P2 修复：
    - "已读 skill" 列表里**不**包含 phase（每轮必选）
    - **不**包含 exemplary-session 当 current_round ≤ 3 时（R1-R3 必选）
    - 文字改成"本会话已读的 technique / domain（不必再选）"，避免与铁则 #3/#4 冲突

    与 v2.5 的 `_runtime_state_block` 不同：
    - **不预设** phase（让 LLM 自己选）
    - 末尾不写 STATUS 提醒（Pass 1 不输出正文）
    """
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
    already_all = _collect_already_loaded_from_history(session)
    already_filtered: List[str] = []
    if bundle is not None:
        for n in already_all:
            # 协议每轮必选项不进"不必再选"列表
            if bundle.is_phase_name(n):
                continue
            if (
                bundle.is_example_name(n)
                and n == "exemplary-session"
                and 1 <= current_round <= 3
            ):
                continue
            already_filtered.append(n)
    else:
        already_filtered = already_all
    already_line = ""
    if already_filtered:
        already_line = (
            "\n- 本会话此前 Pass 1 已读过的 technique / domain："
            + ", ".join(already_filtered)
            + "\n  · 默认不必重读（你上一轮 assistant 回复在 history 里）"
            + "；本轮要再次看到该 skill 全文则重选——会记 redundant_read 但 Pass 2 仍会装载"
        )
    return (
        f"\n\n---\n\n# Runtime State\n"
        f"- 当前轮：R{current_round} / target {target_rounds}（hard_cap {MAX_ROUNDS}）\n"
        f"- session_id_short：{session_id_short}\n"
        f"- 今日：{today.isoformat()}"
        f"{prefs_line}{already_line}"
    )


def _runtime_state_block_pass2(
    session: SessionState,
    current_round: int,
    target_rounds: int,
    chosen_phase: Optional[str],
) -> str:
    """Pass 2 用的 Runtime State 块。

    与 Pass 1 的差异：
    - 写明本轮 phase（chosen by Pass 1）
    - 末尾恢复 STATUS 协议提醒（这才是真正出正文的轮）
    """
    today = _dt.date.today()
    session_id_short = (session.session_id or "")[:8]
    prefs = session.user_preferences
    prefs_line = ""
    if prefs is not None and current_round > ONBOARDING_ROUND:
        prefs_line = (
            f"\n- 用户偏好：style={prefs.style}, "
            f"target_rounds={prefs.target_rounds or '(默认 20)'}, pace={prefs.pace}"
            f"{(', 开场情绪=' + prefs.opening_mood) if prefs.opening_mood else ''}"
            f"{(', 备注=' + prefs.note) if prefs.note else ''}"
        )
    return (
        f"\n\n---\n\n# Runtime State\n"
        f"- 当前轮：R{current_round} / target {target_rounds}（hard_cap {MAX_ROUNDS}）\n"
        f"- 本轮 phase：{chosen_phase or '<未选 · phase_missing>'}\n"
        f"- session_id_short：{session_id_short}\n"
        f"- 今日：{today.isoformat()}"
        f"{prefs_line}\n\n"
        f"# 记得结尾带 STATUS 行\n\n"
        f"最末一行独立写一个：`STATUS: CONTINUE` / `STATUS: CONVERGE` / `STATUS: NEED_USER`。"
        f"服务端会自动剥除，用户看不到这行。漏写按 CONTINUE 处理。"
    )


def _collect_already_loaded_from_history(session: SessionState) -> List[str]:
    """从 SessionState.turns 反推此前已经加载过的 skill 名字。

    依赖 routes 层 `_load_session_state` 把 conversations.loaded_skill_names
    回填到 Turn.loaded_skills（见 v2.6 Conversation 表新增字段）。无信息时返空。
    """
    out: List[str] = []
    seen: set = set()
    for t in session.live_turns():
        for n in (t.loaded_skills or []):
            if n and n not in seen:
                out.append(n)
                seen.add(n)
    return out


def _serialize_tool_calls(calls: List[ToolCallRequest]) -> str:
    """tool_calls → JSON string，落 conversations.tool_calls_json。"""
    payload = [
        {
            "name": c.name,
            "raw_arguments": c.raw_arguments,
            "arguments": c.arguments,
            "call_id": c.call_id,
            "arguments_parse_error": c.arguments_parse_error,
        }
        for c in calls
    ]
    return json.dumps(payload, ensure_ascii=False)


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


@dataclass
class Pass1Trace:
    """v2.6 · 单轮 Pass 1 的 trace，由 routes 层落库到 conversations 表。

    字段语义对应设计文档 §4 Conversation 表新增列：
    - `tool_calls_json`: 原始 tool_calls 调用清单（含未解析的 raw_arguments）
    - `loaded_skill_names`: 实际加载的文件（去重 + 过滤后）
    - `pass1_violations`: 6 项校验里命中的（含 zero_tool_read 由 runner 层补）
    - `chosen_phase_key`: LLM 这轮选了哪个 phase（不在 catalogue 时为 None）
    - `phase_match_rn`: phase 与 Rn"常规对应"是否一致（仅观测）
    - `skill_loader_mode`: "on-demand" / "static"
    - `model`: 复盘必需（与 provider 配合）
    """
    tool_calls_json: str
    loaded_skill_names: List[str]
    violations: List[SkillViolation]
    chosen_phase_key: Optional[str]
    phase_match_rn: bool
    skill_loader_mode: str
    model: str
    content_dropped: str = ""


class TurnRunner:
    def __init__(
        self,
        backend: LLMBackend,
        bundle: Optional[SkillBundle] = None,
        *,
        loader_mode: str = "static",
    ):
        self.backend = backend
        self.bundle = bundle or load_skill_bundle()
        if loader_mode not in ("static", "on-demand"):
            raise ValueError(
                f"loader_mode must be 'static' or 'on-demand', got: {loader_mode!r}"
            )
        self.loader_mode = loader_mode

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
        """对话轮 · 流式生成。按 loader_mode 分发。

        yields tuples of (kind, payload):
          - ("quill", List[str]) · token 开始前的笔触批注（0..2 条）
          - ("pass1", Pass1Trace) · 仅 on-demand 模式；Pass 1 trace，供 routes 落库
          - ("token", str) · token chunk，直接透传给用户
          - ("final",  "")  · 流结束前的标记（紧跟 status + visible）
          - ("status", CONTINUE/CONVERGE/NEED_USER)
          - ("visible", 完整剥除 STATUS 后的可见文本)
          - ("error", err_message)
        """
        user_message = sanitize_user_input(user_message_raw, max_length=4000)
        if self.loader_mode == "on-demand":
            async for item in self._stream_turn_on_demand(
                session, user_message, rewrite_hint=rewrite_hint
            ):
                yield item
        else:
            async for item in self._stream_turn_static(
                session, user_message, rewrite_hint=rewrite_hint
            ):
                yield item

    # ------------------------------------------------------------------
    # Static 模式（v2.5.x 行为，保留作为 feature flag 回滚开关）
    # ------------------------------------------------------------------
    async def _stream_turn_static(
        self,
        session: SessionState,
        user_message: str,
        *,
        rewrite_hint: Optional[str] = None,
    ) -> AsyncIterator[Tuple[str, object]]:
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
            logger.warning("stream_turn backend error: %s", exc)
            yield ("error", "UPSTREAM_LLM_STREAM_FAILED")
            return

        parsed = parse_status_sentinel(buffer)
        yield ("final", "")
        yield ("status", parsed.status)
        yield ("visible", parsed.visible_text)

    # ------------------------------------------------------------------
    # On-demand 模式（v2.6 · 真模型按需）
    # ------------------------------------------------------------------
    async def _stream_turn_on_demand(
        self,
        session: SessionState,
        user_message: str,
        *,
        rewrite_hint: Optional[str] = None,
    ) -> AsyncIterator[Tuple[str, object]]:
        """Pass 1 · 工具规划契约 → read_skill_batch → Pass 2 · 流式正文。

        v2.6 ADR-2/6：
        - Pass 1 message.content 整段丢弃（已在 backend.call_tools_only 解析时分离）
        - 6 项校验全程记录但不补全；任何 violation 都进 trace 进 DB
        - Pass 1 失败（网络 / 4xx）→ 不兜底，直接发 error，让 benchmark 看到真信号
        """
        current_round = session.round_count + 1
        target_rounds = effective_target_rounds(session.user_preferences)

        # ---- Pass 1 · 工具规划契约 ----
        runtime_block = _runtime_state_block_pass1(
            session, current_round, target_rounds, bundle=self.bundle
        )
        skill_index = self.bundle.build_skill_index_block()
        pass1_system = self.bundle.compose_pass1_system(
            runtime_state_block=runtime_block,
            skill_index_block=skill_index,
        )

        pass1_msgs: List[Message] = [
            Message(role="system", content=pass1_system, cache_breakpoint=True),
        ]
        # Pass 1 也带 history（便于 LLM 判断"该选哪个 phase / 是否第一次见某话题"）
        for t in session.live_turns():
            pass1_msgs.append(Message(role="user", content=t.user_message))
            if t.oriself_text:
                pass1_msgs.append(Message(role="assistant", content=t.oriself_text))
        pass1_msgs.append(Message(role="user", content=user_message))
        if rewrite_hint:
            pass1_msgs.append(
                Message(
                    role="system",
                    content=(
                        "[rewrite-hint] 用户对上一轮回复不满意，请重新规划这一轮要读哪些 skill。"
                        + (f"用户意见：{rewrite_hint}" if rewrite_hint.strip() else "")
                    ),
                )
            )

        catalogue = self.bundle.list_all_names()
        tool_schema = read_skill_tool_schema(catalogue)

        logger.info(
            "[v2.6 pass1] round=%d catalogue=%d system_bytes=%d",
            current_round,
            len(catalogue),
            len(pass1_system.encode("utf-8")),
        )

        try:
            pass1_result: Pass1Result = await self.backend.call_tools_only(
                pass1_msgs, tools=[tool_schema]
            )
        except Exception as exc:
            logger.warning("pass1 call_tools_only error: %s", exc)
            yield ("error", "UPSTREAM_LLM_PASS1_FAILED")
            return

        # ---- 解析 tool_calls + 6 项校验（zero_tool_read 在这层判） ----
        already_loaded = _collect_already_loaded_from_history(session)
        violations: List[SkillViolation] = []
        raw_names: List[str] = []
        for tc in pass1_result.tool_calls:
            if tc.name != "read_skill":
                # schema 只声明 read_skill；其他名字按 invalid_skill 记录
                violations.append(
                    SkillViolation(
                        kind="invalid_skill",
                        detail=f"unexpected tool: {tc.name}",
                    )
                )
                continue
            if tc.arguments_parse_error:
                violations.append(
                    SkillViolation(
                        kind="invalid_skill",
                        detail=f"args parse error: {tc.arguments_parse_error}",
                    )
                )
                continue
            args_names = tc.arguments.get("names")
            if not isinstance(args_names, list):
                violations.append(
                    SkillViolation(
                        kind="invalid_skill",
                        detail=f"names not list: {type(args_names).__name__}",
                    )
                )
                continue
            raw_names.extend([str(n) for n in args_names])

        if not pass1_result.tool_calls:
            violations.append(
                SkillViolation(
                    kind="zero_tool_read",
                    detail=f"R{current_round}: 0 tool_calls",
                )
            )

        read_result = read_skill_batch(
            self.bundle,
            raw_names,
            already_loaded=already_loaded,
            current_round=current_round,
        )
        violations.extend(read_result.violations)

        # codex 第二轮 P2 修复：Pass 2 只装载本轮 LLM 选过的项（含 redundant），
        # **不**累积历史所有 skill。否则 R7 的 Pass 2 system 会同时塞 R1 的
        # phase-onboarding + R2 的 phase-warmup + ... 抵消按需加载的上下文缩减。
        loaded_for_pass2 = read_result.final_names
        # 本轮**新增**的（去掉 already_loaded）落到 conversations.loaded_skill_names；
        # 这样下一轮 already_loaded 自然 union 全部历史选择，不会双重计数。
        newly_loaded = read_result.newly_loaded_names

        # chosen_phase 从 final_names 推（含 redundant，覆盖跨轮 phase 复读）。
        chosen_phase: Optional[str] = next(
            (n for n in loaded_for_pass2 if self.bundle.is_phase_name(n)),
            None,
        )
        theoretical = choose_phase_key(session, current_round)
        phase_match = bool(chosen_phase) and chosen_phase == theoretical

        # 落 Pass 1 trace
        tool_calls_payload = _serialize_tool_calls(pass1_result.tool_calls)
        trace = Pass1Trace(
            tool_calls_json=tool_calls_payload,
            loaded_skill_names=newly_loaded,
            violations=violations,
            chosen_phase_key=chosen_phase,
            phase_match_rn=phase_match,
            skill_loader_mode="on-demand",
            model=str(getattr(self.backend, "model", self.backend.provider_name)),
            content_dropped=pass1_result.content_dropped or "",
        )
        yield ("pass1", trace)

        # 仍然给前端发一条 quill 行（用 chosen_phase 推 needs；缺 phase 时空列表）
        quill_phase = chosen_phase or theoretical
        phase_ref = self.bundle.refs.get(quill_phase) if quill_phase else None
        needs_list: List[str] = []
        if phase_ref is not None:
            raw_needs = phase_ref.meta.get("needs") or []
            if isinstance(raw_needs, list):
                needs_list = [str(n) for n in raw_needs if n]
        seen_phases, seen_techniques = _collect_seen_from_history(
            session, self.bundle
        )
        quill_lines, _, _ = _derive_quill_lines(
            phase_key=quill_phase or "",
            needs=needs_list,
            seen_phases=seen_phases,
            seen_techniques=seen_techniques,
        )
        yield ("quill", quill_lines)

        # ---- Pass 2 · 流式正文 ----
        runtime_block_p2 = _runtime_state_block_pass2(
            session, current_round, target_rounds, chosen_phase
        )
        pass2_system = self.bundle.compose_pass2_system(
            domain=session.domain,
            runtime_state_block=runtime_block_p2,
            loaded_names=loaded_for_pass2,
        )

        logger.info(
            "[v2.6 pass2] round=%d phase=%s loaded=%d new=%d system_bytes=%d violations=%s",
            current_round,
            chosen_phase or "<none>",
            len(loaded_for_pass2),
            len(newly_loaded),
            len(pass2_system.encode("utf-8")),
            [v.kind for v in violations],
        )

        pass2_msgs: List[Message] = [
            Message(role="system", content=pass2_system, cache_breakpoint=True),
        ]
        for t in session.live_turns():
            pass2_msgs.append(Message(role="user", content=t.user_message))
            if t.oriself_text:
                pass2_msgs.append(Message(role="assistant", content=t.oriself_text))
        pass2_msgs.append(Message(role="user", content=user_message))
        if rewrite_hint:
            pass2_msgs.append(
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
            async for chunk in self.backend.stream_text(pass2_msgs):
                if not chunk:
                    continue
                buffer += chunk
                yield ("token", chunk)
        except Exception as exc:
            logger.warning("pass2 stream error: %s", exc)
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
    *,
    loaded_skills: Optional[List[str]] = None,
) -> SessionState:
    """把完成的一轮追加到 session。返回新的 SessionState（不可变风格）。

    `loaded_skills` 在 on-demand 模式下应传本轮 Pass 1 真正加载的 skill 名字，
    让下一轮 Pass 1 看到"本会话已读"列表。static 模式传 None / 空列表即可。
    """
    round_number = session.round_count + 1
    new_turn = Turn(
        round_number=round_number,
        user_message=user_message,
        oriself_text=oriself_visible,
        status=status,
        discarded=False,
        loaded_skills=list(loaded_skills or []),
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
