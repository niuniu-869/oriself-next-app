"""
OriSelfGuardrails · 工程护栏（v2.1 瘦身版）。

哲学转向：
- v2.0 里我们用关键词命中强制 action（情绪词 → warm_echo、敏感词 → 禁直问）。
  实操发现这种硬编码脆、笨、越权——用户用"虚空"不用"空"就漏，硬扭模型到
  warm_echo 反而失去语感。
- v2.1 只保留**结构不变式**：JSON schema、字面 grounding、probe 距离、
  单问号、轮数上限、特殊轮次 action 匹配、quiz 结构完整性。
- 品味级的"什么时候共情 / 什么时候换话题 / 语气贴不贴"由 phases/*.md
  以朋友口吻告诉 LLM，信任模型自己判断。

每个函数返回 GuardrailResult（passed + reasons），不抛异常。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from .schemas import (
    CONVERGE_INSIGHT_TOTAL_LIMIT,
    DEFENSIVE_EXIT_MARKERS,
    EMOTIONAL_DISTRESS_REFERENCE,
    MAX_ROUNDS,
    ONBOARDING_ROUND,
    SENSITIVE_TOPIC_REFERENCE,
    Action,
    ConvergeOutput,
    Evidence,
    UserPreferences,
    effective_target_rounds,
    midpoint_round,
    near_end_round,
)
from .skill_loader import BannedPattern, SkillBundle, load_skill_bundle


# ---------------------------------------------------------------------------
# Result 类型
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
# History / Session state
# ---------------------------------------------------------------------------


@dataclass
class Turn:
    round_number: int
    user_message: str
    action_type: Optional[str] = None
    dimension_targeted: Optional[str] = None
    evidence: List[Evidence] = field(default_factory=list)
    # v2.1：以下两个字段保留为**观察标记**（便于日志 / 分析），
    # guardrails 不基于它们 reject action。
    emotional_hint: bool = False
    sensitive_hint: bool = False

    def quotes(self) -> List[str]:
        return [self.user_message]


@dataclass
class SessionState:
    session_id: str
    domain: str
    turns: List[Turn] = field(default_factory=list)
    collected_evidence: List[Evidence] = field(default_factory=list)
    user_preferences: Optional[UserPreferences] = None

    @property
    def round_count(self) -> int:
        return len(self.turns)

    def all_user_messages(self) -> List[str]:
        return [t.user_message for t in self.turns]


# ---------------------------------------------------------------------------
# 信号观察函数（仅用于 prompt 注入参考 + 日志打标，不做 reject）
# ---------------------------------------------------------------------------


def observe_emotional_hint(text: str) -> bool:
    if not text:
        return False
    return any(marker in text for marker in EMOTIONAL_DISTRESS_REFERENCE)


def observe_sensitive_hint(text: str) -> bool:
    if not text:
        return False
    return any(marker in text for marker in SENSITIVE_TOPIC_REFERENCE)


def observe_defensive_exit(text: str) -> bool:
    """用户明示不想聊（字面信号，不是品味推断）。"""
    if not text:
        return False
    return any(marker in text for marker in DEFENSIVE_EXIT_MARKERS)


# v2.0 别名兼容
detect_emotional_distress = observe_emotional_hint
detect_sensitive_topic = observe_sensitive_hint
detect_defensive_signal = observe_defensive_exit


# ---------------------------------------------------------------------------
# Guardrails 主类
# ---------------------------------------------------------------------------


class OriSelfGuardrails:
    """工程护栏。只校验结构不变式。品味由 phase 文件教 LLM。"""

    def __init__(self, bundle: Optional[SkillBundle] = None):
        self.bundle = bundle or load_skill_bundle()
        self._compiled_banned: List[tuple[BannedPattern, re.Pattern]] = []
        for bp in self.bundle.banned_patterns:
            if bp.kind == "regex":
                try:
                    self._compiled_banned.append((bp, re.compile(bp.pattern)))
                except re.error:
                    continue

    # ------------------------------------------------------------------
    # 1. Schema 校验
    # ------------------------------------------------------------------
    def validate_action_schema(self, raw: dict) -> GuardrailResult:
        try:
            action = Action.model_validate(raw)
        except Exception as exc:
            return GuardrailResult.fail(f"schema invalid: {exc}")

        if action.action == "converge" and action.converge_output is None:
            return GuardrailResult.fail("action=converge but converge_output is missing")

        if action.action == "probe_contradiction" and action.contradiction is None:
            return GuardrailResult.fail("action=probe_contradiction but contradiction is missing")

        if action.action == "scenario_quiz" and action.quiz_scenario is None:
            return GuardrailResult.fail("action=scenario_quiz but quiz_scenario is missing")

        return GuardrailResult.ok()

    # ------------------------------------------------------------------
    # 2. Banned phrases（保留：这是从 examples/banned-outputs.md 加载的
    #    AI slop 模板检测，属于结构性品质不是关键词情绪检测）
    # ------------------------------------------------------------------
    def detect_banned_phrases(
        self,
        text: str,
        *,
        applies_to: str = "next_prompt",
        round_number: int = 1,
        action_type: str = "ask",
    ) -> List[BannedPattern]:
        if not text:
            return []
        hits: List[BannedPattern] = []
        for bp, compiled in self._compiled_banned:
            if applies_to not in bp.applies_to and bp.applies_to:
                continue
            if bp.id == "BP9":
                if round_number >= 20 or action_type == "converge":
                    continue
            if compiled.search(text):
                hits.append(bp)
        return hits

    # ------------------------------------------------------------------
    # 3. Evidence grounding（字面校验）
    #
    # v2.3 设计转向：不再硬锁 `round_number == current_round`。
    # 实证观察（v2.2 session 复盘）：当 LLM 想引前几轮原话做 evidence 时，
    # 硬锁会逼 LLM 把 round_number 谎报为当前轮，反而绕开校验、引错内容。
    # 新策略：LLM 可以诚实标注抽自哪一轮；guardrails 只管"quote 必须是
    # 该轮 user_message 字面子串"这条结构不变式。去重在 advance_state 层
    # 做（`(dimension, user_quote)` set），回头刷旧 quote 也不会虚增计数。
    # ------------------------------------------------------------------
    def verify_evidence_grounding(
        self, evidences: Sequence[Evidence], session: SessionState,
        *, current_round: Optional[int] = None,
    ) -> GuardrailResult:
        if not evidences:
            return GuardrailResult.ok()
        by_round: Dict[int, str] = {t.round_number: t.user_message for t in session.turns}
        # v2.3：current_round 参数保留但不再 reject——允许诚实回引历史轮
        reasons: List[str] = []
        for ev in evidences:
            msg = by_round.get(ev.round_number)
            if msg is None:
                reasons.append(
                    f"evidence.round_number={ev.round_number} 不存在于 session; "
                    f"请把 round_number 标成 quote 真正来源的那一轮"
                )
                continue
            if ev.user_quote not in msg:
                reasons.append(
                    f"evidence.user_quote 不是 R{ev.round_number} 的字面子串: "
                    f"quote={ev.user_quote[:40]!r}; "
                    f"请把 round_number 改成 quote 真正来源的那一轮，"
                    f"或者 quote 改成该轮里真的出现过的字面片段"
                )
        return GuardrailResult.ok() if not reasons else GuardrailResult.fail(*reasons)

    # ------------------------------------------------------------------
    # 4. Convergence readiness
    # ------------------------------------------------------------------
    def check_convergence_readiness(
        self,
        session: SessionState,
        *,
        required_per_dim: int = 3,
        dimensions: Iterable[str] = ("E/I", "S/N", "T/F", "J/P"),
    ) -> GuardrailResult:
        counts: Dict[str, int] = {d: 0 for d in dimensions}
        seen_quotes: set[tuple[str, str]] = set()
        for ev in session.collected_evidence:
            key = (ev.dimension, ev.user_quote)
            if key in seen_quotes:
                continue
            seen_quotes.add(key)
            if ev.dimension in counts:
                counts[ev.dimension] += 1
        missing = [d for d, n in counts.items() if n < required_per_dim]
        if missing:
            details = ", ".join(f"{d}={counts[d]}/{required_per_dim}" for d in counts)
            return GuardrailResult.fail(
                f"convergence not ready: missing dimensions {missing} ({details})"
            )
        return GuardrailResult.ok()

    # ------------------------------------------------------------------
    # 5a. Report HTML shape（v2.2）· 结构性安全 + 自包含校验
    # ------------------------------------------------------------------
    _RE_SCRIPT = re.compile(r"<\s*script\b", re.IGNORECASE)
    _RE_IFRAME = re.compile(r"<\s*(iframe|object|embed|form|input)\b", re.IGNORECASE)
    _RE_EVENT_HANDLER = re.compile(r"\son\w+\s*=", re.IGNORECASE)
    _RE_JS_URL = re.compile(r"javascript\s*:", re.IGNORECASE)
    # v2.2.3 · 模板占位符泄漏（LLM 以为是模板引擎，留了没替换的变量）
    _RE_TEMPLATE_PLACEHOLDER = re.compile(r"\{\{\s*[\w_.-]+\s*\}\}")
    # v2.3 · MBTI 4 字母串（按维度合法字母各取一个，恰好 4 个字母连写）
    #    用于 HTML 里的字面一致性校验：每一处 4 字母串都必须等于派生 mbti_type
    _RE_MBTI_TOKEN = re.compile(r"(?<![A-Za-z])[EI][SN][TF][JP](?![A-Za-z])")

    def verify_report_html_shape(self, html: str) -> "GuardrailResult":
        """检查 report_html 的安全边界。

        只做**安全性**结构检查，不做品味/审美/自包含审查：
        - 必须是 <!DOCTYPE ... </html> 的完整文档（schema 已保证）
        - 禁止 <script>（JS 执行）
        - 禁止 <iframe> / <object> / <embed> / <form> / <input>（防嵌套页面 + 数据外发）
        - 禁止事件处理器 `onclick` `onerror` 等（间接 JS 执行）
        - 禁止 `javascript:` URL（间接 JS 执行）

        **允许**：
        - 外部字体（Google Fonts 等）、外部 CSS、data: URIs、外部图片
          ——这些是设计手段，不是安全问题。LLM 自己决定要不要用。
        """
        if not html:
            return GuardrailResult.fail("report_html is empty")
        reasons: List[str] = []
        if self._RE_SCRIPT.search(html):
            reasons.append("report_html 含 <script>，禁止（只读页面，不许 JS）")
        if self._RE_IFRAME.search(html):
            reasons.append("report_html 含 <iframe>/<object>/<embed>/<form>/<input>，禁止")
        if self._RE_EVENT_HANDLER.search(html):
            reasons.append("report_html 含事件处理器（onclick/onerror 等），禁止")
        if self._RE_JS_URL.search(html):
            reasons.append("report_html 含 javascript: URL，禁止")
        m = self._RE_TEMPLATE_PLACEHOLDER.search(html)
        if m:
            reasons.append(
                f"report_html 含未替换的模板占位符 {m.group(0)!r} —— "
                "Runtime State 里已经给了 session_id_short / today_* 真实值，"
                "请直接把值写进 HTML，不要留 {{...}} 占位符"
            )
        return GuardrailResult.ok() if not reasons else GuardrailResult.fail(*reasons)

    # ------------------------------------------------------------------
    # 5b. Report HTML 与 mbti_type 的字面一致性（v2.3 新增）
    #
    # 设计：ConvergeOutput.mbti_type 由 confidence_per_dim 派生（单一真相源），
    # 但 LLM 写的 report_html 里仍可能出现不同的 4 字母串（title 写 INFJ、
    # 维度区拼出 INFP）。这里 post-hoc 扫描 HTML 里所有"恰好 4 字母且字母
    # 合法组合"的 token，任一与派生值不等 → reject，把实际冲突值告诉 LLM。
    # ------------------------------------------------------------------
    def verify_report_html_consistency(
        self, html: str, mbti_type: str
    ) -> "GuardrailResult":
        if not html or not mbti_type:
            return GuardrailResult.ok()
        found = set(self._RE_MBTI_TOKEN.findall(html))
        mismatched = [tok for tok in found if tok != mbti_type]
        if mismatched:
            return GuardrailResult.fail(
                f"report_html 里出现 {sorted(mismatched)} 与派生 mbti_type="
                f"{mbti_type!r} 不一致; 派生值来自 confidence_per_dim，"
                f"请把 HTML 里所有 MBTI 4 字母处都写成 {mbti_type!r}"
            )
        return GuardrailResult.ok()

    # ------------------------------------------------------------------
    # 5. Insight grounding
    # ------------------------------------------------------------------
    def verify_insight_grounding(
        self, converge: ConvergeOutput, session: SessionState
    ) -> GuardrailResult:
        round_set = {t.round_number for t in session.turns}
        reasons: List[str] = []
        total_cited: set[int] = set()
        for i, para in enumerate(converge.insight_paragraphs, start=1):
            if not para.quoted_rounds:
                reasons.append(f"insight para {i} has no quoted_rounds")
                continue
            for r in para.quoted_rounds:
                if r not in round_set:
                    reasons.append(
                        f"insight para {i} cites non-existent round {r}"
                    )
                else:
                    total_cited.add(r)
        if len(total_cited) < 3:
            reasons.append(
                f"insight total distinct cited rounds = {len(total_cited)} (need ≥ 3)"
            )
        total_body = sum(len(p.body) for p in converge.insight_paragraphs)
        if total_body > CONVERGE_INSIGHT_TOTAL_LIMIT:
            reasons.append(
                f"insight bodies total {total_body} chars exceeds limit {CONVERGE_INSIGHT_TOTAL_LIMIT}"
            )
        return GuardrailResult.ok() if not reasons else GuardrailResult.fail(*reasons)

    # ------------------------------------------------------------------
    # 6. Dimension diversity
    # ------------------------------------------------------------------
    def check_dimension_diversity(
        self, recent_turns: Sequence[Turn], *, window: int = 4
    ) -> GuardrailResult:
        targeted = [
            (t.round_number, t.dimension_targeted)
            for t in recent_turns[-window:]
            if t.dimension_targeted not in (None, "none")
        ]
        if len(targeted) < window:
            return GuardrailResult.ok()
        dims = {d for _, d in targeted}
        if len(dims) == 1:
            return GuardrailResult.fail(
                f"last {window} rounds all targeted dimension={targeted[0][1]} — "
                "violates diversity rule; 切换到另一个维度或做 reflect"
            )
        return GuardrailResult.ok()

    # ------------------------------------------------------------------
    # 7. Probe 频率
    # ------------------------------------------------------------------
    def check_probe_frequency(
        self, session: SessionState, *, new_action: str, window: int = 4
    ) -> GuardrailResult:
        if new_action != "probe_contradiction":
            return GuardrailResult.ok()
        recent = session.turns[-(window - 1):]
        if any(t.action_type == "probe_contradiction" for t in recent):
            return GuardrailResult.fail(
                f"probe_contradiction 距上次 probe 不足 {window} 轮；"
                "频次硬约束：每 4 轮最多 1 次 probe。本轮请用 reflect / ask。"
            )
        return GuardrailResult.ok()

    # ------------------------------------------------------------------
    # 8. 反射当轮 · 用户给新场景时必须引本轮原话（结构性，不是品味）
    # ------------------------------------------------------------------
    def check_reflects_current_turn(
        self,
        action: Action,
        current_user_message: str,
        *,
        min_len: int = 80,
        min_quote_len: int = 6,
    ) -> GuardrailResult:
        if action.action not in ("ask", "reflect"):
            return GuardrailResult.ok()
        if len(current_user_message) < min_len:
            return GuardrailResult.ok()
        np = action.next_prompt
        for i in range(len(current_user_message) - min_quote_len + 1):
            frag = current_user_message[i : i + min_quote_len]
            if frag in np:
                return GuardrailResult.ok()
        return GuardrailResult.fail(
            f"用户当轮 message ({len(current_user_message)} 字) 给了新场景，"
            f"但 next_prompt 没引用任何 ≥{min_quote_len} 字片段 —— "
            "你在忽略用户刚说的话，去扒历史轮。reflect 本轮新素材，不要跳过。"
        )

    # ------------------------------------------------------------------
    # 9. MAX_ROUNDS
    # ------------------------------------------------------------------
    def check_round_budget(self, session: SessionState) -> GuardrailResult:
        if session.round_count >= MAX_ROUNDS:
            return GuardrailResult.fail(
                f"round_count={session.round_count} reached MAX_ROUNDS={MAX_ROUNDS}"
            )
        return GuardrailResult.ok()

    # ------------------------------------------------------------------
    # 10. Phase 0 · 第 1 轮必须是 onboarding
    # ------------------------------------------------------------------
    def check_phase0_onboarding(
        self, action: Action, round_number: int
    ) -> GuardrailResult:
        if round_number == ONBOARDING_ROUND and action.action != "onboarding":
            return GuardrailResult.fail(
                f"第 {ONBOARDING_ROUND} 轮必须是 onboarding（偏好握手），"
                f"当前是 {action.action}。"
            )
        if round_number > ONBOARDING_ROUND and action.action == "onboarding":
            return GuardrailResult.fail(
                f"onboarding 只能用于第 {ONBOARDING_ROUND} 轮，"
                f"当前是 R{round_number}。"
            )
        return GuardrailResult.ok()

    # ------------------------------------------------------------------
    # 11. 中期回顾 · target//2 轮强制 midpoint_reflect
    # ------------------------------------------------------------------
    def check_midpoint_reflect(
        self, action: Action, round_number: int, session: SessionState
    ) -> GuardrailResult:
        target = effective_target_rounds(session.user_preferences)
        mid = midpoint_round(target)
        if round_number != mid:
            if action.action == "midpoint_reflect":
                return GuardrailResult.fail(
                    f"midpoint_reflect 只能用于 R{mid}（target={target} 的中点），"
                    f"当前 R{round_number}"
                )
            return GuardrailResult.ok()
        already_did = any(t.action_type == "midpoint_reflect" for t in session.turns)
        if already_did:
            return GuardrailResult.ok()
        if action.action != "midpoint_reflect":
            return GuardrailResult.fail(
                f"R{round_number} 是中期回顾轮（target_rounds={target} 的一半），"
                f"action 必须是 midpoint_reflect（不提新问题，做温暖总结 + 确认方向）。"
                f"当前 action={action.action}。"
            )
        return GuardrailResult.ok()

    # ------------------------------------------------------------------
    # 12. 尾声温柔提醒 · target-2 轮强制 soft_closing
    # ------------------------------------------------------------------
    def check_soft_closing(
        self, action: Action, round_number: int, session: SessionState
    ) -> GuardrailResult:
        target = effective_target_rounds(session.user_preferences)
        near = near_end_round(target)
        if round_number != near:
            if action.action == "soft_closing":
                return GuardrailResult.fail(
                    f"soft_closing 只能用于 R{near}（target-2），当前 R{round_number}"
                )
            return GuardrailResult.ok()
        already_did = any(t.action_type == "soft_closing" for t in session.turns)
        if already_did:
            return GuardrailResult.ok()
        if action.action in ("soft_closing", "converge"):
            return GuardrailResult.ok()
        return GuardrailResult.fail(
            f"R{round_number} 是尾声提醒轮（target={target} 的倒数第 2 轮），"
            f"action 必须是 soft_closing。当前 action={action.action}。"
        )

    # ------------------------------------------------------------------
    # 13. 一轮一问
    # ------------------------------------------------------------------
    def check_single_question(
        self, action: Action
    ) -> GuardrailResult:
        """next_prompt 里的问号数 ≤ 1。onboarding / converge / scenario_quiz 免检。"""
        if action.action in ("onboarding", "converge", "scenario_quiz"):
            return GuardrailResult.ok()
        np = action.next_prompt or ""
        q_count = np.count("?") + np.count("？")
        if q_count > 1:
            return GuardrailResult.fail(
                f"next_prompt 含 {q_count} 个问号，每轮最多 1 个。"
                "一次只问一个问题。"
            )
        return GuardrailResult.ok()

    # ------------------------------------------------------------------
    # 14. Quiz 结构/节奏规则（v2.1 新增）
    # ------------------------------------------------------------------
    def check_quiz_rules(
        self, action: Action, round_number: int, session: SessionState
    ) -> GuardrailResult:
        """scenario_quiz 使用的结构与节奏限制：
        - 特殊轮次（R1 onboarding / R_mid / R_near / converge 该走的地方）禁 quiz
        - 连续两轮 quiz 禁止（除非上一轮是 quiz 的语义延续，本轮也不能再 quiz）
        - Phase 5 硬上限之前 quiz 可用，但 target 很大（≥25）时建议少用（软规则写在 phase 文件里）
        """
        if action.action != "scenario_quiz":
            return GuardrailResult.ok()

        target = effective_target_rounds(session.user_preferences)
        mid = midpoint_round(target)
        near = near_end_round(target)

        if round_number == ONBOARDING_ROUND:
            return GuardrailResult.fail("R1 必须 onboarding，不能走 quiz")
        if round_number == mid:
            return GuardrailResult.fail(f"R{mid} 是中期回顾轮，不能走 quiz")
        if round_number == near:
            return GuardrailResult.fail(f"R{near} 是尾声提醒轮，不能走 quiz")

        # 连续 quiz 禁止
        if session.turns and session.turns[-1].action_type == "scenario_quiz":
            return GuardrailResult.fail(
                "上一轮已经是 scenario_quiz，本轮不得再 quiz。"
                "非必要不连续两轮 quiz——给用户透气的空间。"
            )
        return GuardrailResult.ok()

    # ------------------------------------------------------------------
    # 高层 · 完整校验
    # ------------------------------------------------------------------
    def validate_action(
        self,
        action: Action,
        session: SessionState,
        round_number: int,
        *,
        current_user_message: Optional[str] = None,
    ) -> GuardrailResult:
        """主入口。结构不变式全过就放行。品味由 phase prompt 托管。"""
        result = GuardrailResult.ok()

        # 特殊轮次 action 匹配
        result = result.merge(self.check_phase0_onboarding(action, round_number))
        result = result.merge(self.check_midpoint_reflect(action, round_number, session))
        result = result.merge(self.check_soft_closing(action, round_number, session))

        # Quiz 结构/节奏
        result = result.merge(self.check_quiz_rules(action, round_number, session))

        # 一轮一问
        result = result.merge(self.check_single_question(action))

        # banned phrases（AI slop 模板，非关键词情绪检测）
        banned_np = self.detect_banned_phrases(
            action.next_prompt,
            applies_to="next_prompt",
            round_number=round_number,
            action_type=action.action,
        )
        if banned_np:
            result = result.merge(
                GuardrailResult.fail(
                    *(f"banned phrase in next_prompt: {bp.id}" for bp in banned_np)
                )
            )

        # evidence grounding
        result = result.merge(
            self.verify_evidence_grounding(
                action.evidence, session, current_round=round_number
            )
        )

        # dimension diversity
        if action.action in ("ask", "reflect", "probe_contradiction"):
            result = result.merge(self.check_dimension_diversity(session.turns))

        # probe 频率
        result = result.merge(
            self.check_probe_frequency(session, new_action=action.action)
        )

        # reflect 本轮场景
        if current_user_message is not None:
            result = result.merge(
                self.check_reflects_current_turn(action, current_user_message)
            )

        # contradiction grounding
        if action.contradiction:
            round_a_msg = next(
                (
                    t.user_message
                    for t in session.turns
                    if t.round_number == action.contradiction.round_a
                ),
                None,
            )
            round_b_msg = next(
                (
                    t.user_message
                    for t in session.turns
                    if t.round_number == action.contradiction.round_b
                ),
                None,
            )
            if round_a_msg is None or action.contradiction.quote_a not in round_a_msg:
                result = result.merge(
                    GuardrailResult.fail(
                        f"contradiction.quote_a not grounded in round "
                        f"{action.contradiction.round_a}"
                    )
                )
            if round_b_msg is None or action.contradiction.quote_b not in round_b_msg:
                result = result.merge(
                    GuardrailResult.fail(
                        f"contradiction.quote_b not grounded in round "
                        f"{action.contradiction.round_b}"
                    )
                )

        # converge 额外校验
        if action.action == "converge" and action.converge_output:
            banned_insight = []
            for para in action.converge_output.insight_paragraphs:
                banned_insight.extend(
                    self.detect_banned_phrases(
                        para.body,
                        applies_to="insight_body",
                        round_number=round_number,
                        action_type="converge",
                    )
                )
            if banned_insight:
                result = result.merge(
                    GuardrailResult.fail(
                        *(f"banned phrase in insight: {bp.id}" for bp in banned_insight)
                    )
                )
            result = result.merge(
                self.verify_insight_grounding(action.converge_output, session)
            )
            # v2.2 · HTML 交付物结构性校验（安全 + 自包含）
            result = result.merge(
                self.verify_report_html_shape(action.converge_output.report_html)
            )
            # v2.3 · HTML 里每处 4 字母 MBTI 串必须等于派生 mbti_type
            result = result.merge(
                self.verify_report_html_consistency(
                    action.converge_output.report_html,
                    action.converge_output.mbti_type or "",
                )
            )
            readiness = self.check_convergence_readiness(session)
            if not readiness.passed and session.round_count < MAX_ROUNDS:
                result = result.merge(readiness)

        return result


# ---------------------------------------------------------------------------
# 降级响应
# ---------------------------------------------------------------------------


FALLBACK_NEXT_PROMPT = (
    "我先换一个角度。最近一次让你印象最深的一件小事是什么？—— "
    "不用是大事，上周的一杯咖啡、一次通勤、一场饭局都算。"
)

# v2.3 · 每维度的开放问题种子。fallback 根据 reject 里的"缺失维度"挑一条，
# 让 LLM 下一轮有机会抽到该维度的 evidence，跳出死循环。
#
# 设计：不用"禁止做 X"而用"提议换个角度问 X"——fallback 输出合法且
# 能推进状态。具体措辞尽量贴朋友口吻，避免问卷腔。
_DIMENSION_SEEDS: Dict[str, List[str]] = {
    "E/I": [
        "说一件你最近一个人在家待了半天的事——那段时间你是觉得满还是觉得闷？",
        "上次跟人聊完天，你是觉得被充满了，还是需要缓一阵？",
        "你最近一次很想找人说话、但又没找的时刻是什么样的？",
    ],
    "S/N": [
        "你给我讲一个你最近注意到的小细节吧——别人可能都没留意那种。",
        "如果今天脑子里有个念头反复冒出来，是什么？",
        "你更容易被一个具体的场景打动，还是被一句话、一个想法打动？",
    ],
    "T/F": [
        "上次做一个不太好选的决定，你是先想通了再定，还是先有了感觉再去圆它？",
        "有人跟你抱怨一件事，你下意识想做什么——先帮 TA 想办法，还是先陪 TA 待着？",
        "你最近一次在「该说实话还是照顾对方感受」之间犹豫，是什么事？",
    ],
    "J/P": [
        "明天如果一整天没安排，你会提前想好干嘛，还是醒来再说？",
        "上次计划被打乱你什么反应——马上重排，还是先放着走走看？",
        "你的书桌 / 手机桌面更像「每样东西有位置」还是「能用就行」？",
    ],
}

_DIM_MISSING_RE = re.compile(r"missing dimensions \[([^\]]+)\]")
_DIM_TOKEN_RE = re.compile(r"'([EI][/]?[IS]?[/]?[NFJP])'|\"([EISNTFJP/]+)\"")


def _extract_missing_dimensions(reasons: Sequence[str]) -> List[str]:
    """从 convergence readiness 的 reject 文案里抽出 missing 维度列表。

    样本输入："convergence not ready: missing dimensions ['E/I', 'J/P'] (...)"
    样本输出：["E/I", "J/P"]

    用独立函数是为了可测 —— fallback 不该依赖 stringly-typed 解析的
    "恰好能用"，这里把解析抽出来可以单测。
    """
    canonical = {"E/I", "S/N", "T/F", "J/P"}
    found: List[str] = []
    seen: set[str] = set()
    for reason in reasons:
        m = _DIM_MISSING_RE.search(reason)
        if not m:
            continue
        for token in re.findall(r"[EISNTFJP]/[EISNTFJP]", m.group(1)):
            if token in canonical and token not in seen:
                seen.add(token)
                found.append(token)
    return found


def _pick_deficit_seed(dims: Sequence[str], session: Optional[SessionState]) -> tuple[str, str]:
    """从缺失维度里挑一条种子问题。

    选择策略：取 reject reasons 里出现的第一个维度（避免随机性造成
    session 行为不可复现）；同一维度内轮换，避免连续 fallback 问同一句。
    返回 (dimension, prompt)。
    """
    dim = dims[0] if dims else "S/N"
    seeds = _DIMENSION_SEEDS.get(dim) or _DIMENSION_SEEDS["S/N"]
    # 用 session 里已走过的 fallback 计数做轮换（stable）
    prev = 0
    if session is not None:
        prev = sum(
            1 for t in session.turns
            if t.action_type in ("ask", "reflect") and t.dimension_targeted == dim
        )
    prompt = seeds[prev % len(seeds)]
    return dim, prompt

FALLBACK_ONBOARDING_PROMPT = (
    "嗨，我是陪你聊聊天的朋友，不是打分的系统。随便聊聊最近的生活就行，"
    "没有对错答案。\n\n开始前想先问你三件事：\n"
    "1. 你想聊得轻松点还是深入点？\n"
    "2. 想聊多久？短的 10-15 轮、标准 20 轮左右、慢慢聊 25-30 轮都行。\n"
    "3. 最近脑子里有事在转？或者有什么话题不想碰也直接说。"
)

FALLBACK_MIDPOINT_PROMPT = (
    "聊到这儿我想先停一下跟你对对感觉。\n\n"
    "你前半场讲的几件事里，我听出你对自己想做什么是清楚的——但讲到被评价时会发呆压着。"
    "这个描述接近你自己的感受吗？或者哪里我听偏了，你告诉我一下。"
)

FALLBACK_SOFT_CLOSING_PROMPT = (
    "嗯，差不多聊到这儿，我这边其实已经有一段想跟你说的话。\n\n"
    "现在你决定：\n"
    "1. 如果有一条线你还想再聊两轮，说一下；\n"
    "2. 想现在就听那段，就说'给我'；\n"
    "3. 换个轻松话题收个尾也行。"
)


def fallback_action(
    round_number: int,
    session: Optional[SessionState] = None,
    reject_reasons: Optional[Sequence[str]] = None,
) -> Action:
    """3 次 retry 都失败时的保底 action。

    v2.3：fallback 变得"有方向"——若 reject 原因里含 convergence-not-ready
    的维度缺口，就挑一条缺失维度的种子开放问题；这样下一轮用户的回答
    有机会落在缺口维度上，LLM 能抽到新 evidence，跳出死循环。
    """
    if round_number == ONBOARDING_ROUND:
        return Action(
            action="onboarding",
            dimension_targeted="none",
            evidence=[],
            contradiction=None,
            next_prompt=FALLBACK_ONBOARDING_PROMPT,
            converge_output=None,
        )
    if session is not None:
        target = effective_target_rounds(session.user_preferences)
        if round_number == midpoint_round(target) and not any(
            t.action_type == "midpoint_reflect" for t in session.turns
        ):
            return Action(
                action="midpoint_reflect",
                dimension_targeted="none",
                evidence=[],
                contradiction=None,
                next_prompt=FALLBACK_MIDPOINT_PROMPT,
                converge_output=None,
            )
        if round_number == near_end_round(target) and not any(
            t.action_type == "soft_closing" for t in session.turns
        ):
            return Action(
                action="soft_closing",
                dimension_targeted="none",
                evidence=[],
                contradiction=None,
                next_prompt=FALLBACK_SOFT_CLOSING_PROMPT,
                converge_output=None,
            )
    # v2.3 · deficit-aware：如果 reject 原因含维度缺口，换个角度指向缺口
    missing_dims = _extract_missing_dimensions(reject_reasons or [])
    if missing_dims:
        dim, prompt = _pick_deficit_seed(missing_dims, session)
        return Action(
            action="ask",
            dimension_targeted=dim,  # type: ignore[arg-type]
            evidence=[],
            contradiction=None,
            next_prompt=prompt,
            converge_output=None,
        )
    return Action(
        action="ask",
        dimension_targeted="none",
        evidence=[],
        contradiction=None,
        next_prompt=FALLBACK_NEXT_PROMPT,
        converge_output=None,
    )
