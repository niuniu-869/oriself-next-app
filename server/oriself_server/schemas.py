"""
Pydantic schemas · 运行时类型校验。

设计原则（v2.1）：
- 这里只定义**结构不变式**：字段形状、长度、字面 grounding 需要的 round 字段等。
- 品味判断（什么时候共情、什么时候切 quiz、怎么说话像朋友）**不在这里**，
  在 phases/*.md 里以朋友口吻写给 LLM 读。
- 关键词常量（EMOTIONAL_*, SENSITIVE_*, THERAPIST_*）仅作为 phase 文件渲染
  时的**参考提示**，不再作为 guardrails reject 依据。
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

ActionType = Literal[
    "onboarding",         # Phase 0 · 偏好握手（风格 / 轮数 / 节奏）
    "warm_echo",          # 纯共情轮，不追问（敏感 / 情绪 / 防御时用）
    "ask",
    "reflect",
    "scenario_quiz",      # 场景题轮：一个生活场景 + 3-5 道选择/排序题
    "probe_contradiction",
    "redirect",
    "midpoint_reflect",   # Phase 3.5 · 中期回顾，不新问，温暖总结 + 确认方向
    "soft_closing",       # Phase 4.8 · 尾声提醒，告知将结束，问继续 or 收束
    "converge",
]
Dimension = Literal["E/I", "S/N", "T/F", "J/P"]
DimensionOrNone = Literal["E/I", "S/N", "T/F", "J/P", "none"]
TypographyHint = Literal["editorial_serif", "editorial_mono", "editorial_minimal"]
ChatStyle = Literal["casual", "deep", "literary", "analytical", "default"]
Pace = Literal["quick", "steady", "slow", "default"]
QuizQuestionType = Literal[
    "single_choice",
    "multiple_choice",
    "true_false",
    "ranking",
    "open_text",
]
NextMode = Literal["open", "quiz"]


class UserPreferences(BaseModel):
    """用户对话偏好 · Phase 0 的产物，后续每轮都要尊重。

    target_rounds=None 时走系统默认 (20)。HARD_CAP 仍是 MAX_ROUNDS (30)。
    """
    style: ChatStyle = "default"
    target_rounds: Optional[int] = Field(default=None, ge=6, le=30)
    pace: Pace = "default"
    opening_mood: Optional[str] = Field(default=None, max_length=200)
    note: Optional[str] = Field(default=None, max_length=300)


class Evidence(BaseModel):
    """一条从用户回复中抽取的证据。

    guardrails.verify_evidence_grounding 会字面校验 user_quote 是用户某轮消息的子串。
    """

    dimension: Dimension
    user_quote: str = Field(min_length=4, max_length=300)
    round_number: int = Field(ge=1)
    confidence: float = Field(ge=0.0, le=1.0)
    interpretation: Optional[str] = Field(default=None, max_length=120)


class Contradiction(BaseModel):
    """probe_contradiction 行动携带的矛盾结构。"""

    round_a: int = Field(ge=1)
    quote_a: str = Field(min_length=4, max_length=300)
    round_b: int = Field(ge=1)
    quote_b: str = Field(min_length=4, max_length=300)
    observation: str = Field(max_length=180)


class QuizOption(BaseModel):
    key: str = Field(min_length=1, max_length=4)   # A / B / C / D / 1 / 2
    text: str = Field(min_length=1, max_length=160)


class QuizQuestion(BaseModel):
    """scenario_quiz 里的一道题。"""
    id: str = Field(min_length=1, max_length=8)
    type: QuizQuestionType
    stem: str = Field(min_length=2, max_length=200)
    options: List[QuizOption] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _options_required_for_choice(self) -> "QuizQuestion":
        needs_options = self.type in ("single_choice", "multiple_choice", "true_false", "ranking")
        if needs_options and len(self.options) < 2:
            raise ValueError(
                f"quiz question type={self.type} 至少需要 2 个 options，当前 {len(self.options)}"
            )
        if self.type == "open_text" and self.options:
            raise ValueError("open_text 不应带 options")
        return self


class QuizScenario(BaseModel):
    """一个场景测评轮的完整结构。"""
    title: str = Field(min_length=2, max_length=30)
    intro: str = Field(min_length=10, max_length=280)  # 口语化场景描述
    questions: List[QuizQuestion] = Field(min_length=3, max_length=5)


class PullQuote(BaseModel):
    text: str = Field(min_length=4, max_length=300)
    round: int = Field(ge=1)


class CardData(BaseModel):
    """名片结构化数据，前端 Editorial 模板渲染。"""

    title: str = Field(min_length=4, max_length=40)
    mbti_type: str = Field(pattern=r"^[EI][SN][TF][JP]$")
    subtitle: str = Field(max_length=60)
    pull_quotes: List[PullQuote] = Field(max_length=3)
    typography_hint: TypographyHint = "editorial_serif"


class InsightParagraph(BaseModel):
    theme: str = Field(min_length=2, max_length=40)
    body: str = Field(min_length=60, max_length=500)
    quoted_rounds: List[int] = Field(min_length=1)


class ConvergeOutput(BaseModel):
    """v2.2：交付物升级为自包含 HTML 页面。

    字段职责：
    - `report_html` · 最终交付物，完整自包含 HTML（<!DOCTYPE ...</html>），
      用于发给用户（本地 agent 起 localhost 渲染；云端 agent 把字符串返给前端即可）。
    - `insight_paragraphs` · 保留为**结构性 grounding 证据**（guardrails 仍做
      quoted_rounds / body 长度校验）。HTML 里的内容应该由这 3 段扩写。
    - `card` · 保留名片元数据（HTML 里可视化，也可供分享图生成）。
    """
    mbti_type: str = Field(pattern=r"^[EI][SN][TF][JP]$")
    confidence_per_dim: dict = Field(default_factory=dict)
    insight_paragraphs: List[InsightParagraph] = Field(min_length=3, max_length=3)
    card: CardData
    report_html: str = Field(
        min_length=1000,
        max_length=80000,
        description="完整自包含 HTML 页面字符串，从 <!DOCTYPE html> 开始到 </html> 结束",
    )

    @field_validator("report_html")
    @classmethod
    def _html_shape(cls, v: str) -> str:
        text = (v or "").strip()
        low = text.lower()
        if "<!doctype" not in low:
            raise ValueError("report_html 必须以 <!DOCTYPE html> 开头")
        if "<html" not in low or "</html>" not in low:
            raise ValueError("report_html 必须包含完整 <html>...</html>")
        return text


class Action(BaseModel):
    """每轮 LLM 输出的主结构。"""

    action: ActionType
    dimension_targeted: DimensionOrNone = "none"
    evidence: List[Evidence] = Field(default_factory=list)
    contradiction: Optional[Contradiction] = None
    next_prompt: str = Field(default="", max_length=600)
    quiz_scenario: Optional[QuizScenario] = None
    next_mode: NextMode = "open"   # 下一轮倾向：让 LLM 自决 open/quiz
    converge_output: Optional[ConvergeOutput] = None

    @field_validator("next_prompt")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _probe_requires_dim(self) -> "Action":
        if self.action == "probe_contradiction" and self.dimension_targeted == "none":
            raise ValueError(
                "probe_contradiction.dimension_targeted 不能是 'none'，"
                "probe 本来就是在测某个维度的内部张力"
            )
        return self

    @model_validator(mode="after")
    def _probe_quote_distance(self) -> "Action":
        if (
            self.action == "probe_contradiction"
            and self.contradiction is not None
        ):
            ra = self.contradiction.round_a
            rb = self.contradiction.round_b
            if abs(ra - rb) < 4:
                raise ValueError(
                    f"probe_contradiction quote_a(R{ra}) 和 quote_b(R{rb}) "
                    f"相隔 {abs(ra - rb)} 轮，<4 轮的 probe 是硬憋，不是真矛盾"
                )
        return self

    @model_validator(mode="after")
    def _quiz_shape(self) -> "Action":
        """scenario_quiz 必须带 quiz_scenario；其它 action 不得带。"""
        if self.action == "scenario_quiz":
            if self.quiz_scenario is None:
                raise ValueError("scenario_quiz 必须带 quiz_scenario 结构")
            if self.evidence:
                raise ValueError("scenario_quiz 轮不得抽 evidence（用户还没回答）")
            if self.contradiction is not None:
                raise ValueError("scenario_quiz 轮不得带 contradiction")
        else:
            if self.quiz_scenario is not None:
                raise ValueError(
                    f"只有 action=scenario_quiz 才能带 quiz_scenario，当前 {self.action}"
                )
        return self

    @model_validator(mode="after")
    def _onboarding_clean(self) -> "Action":
        if self.action == "onboarding":
            if self.evidence:
                raise ValueError("onboarding 轮不得抽 evidence（第 1 轮只做偏好握手）")
            if self.contradiction is not None:
                raise ValueError("onboarding 轮不得带 contradiction")
            if self.converge_output is not None:
                raise ValueError("onboarding 轮不得带 converge_output")
            if self.dimension_targeted != "none":
                raise ValueError("onboarding 轮 dimension_targeted 必须是 none")
        return self

    @model_validator(mode="after")
    def _warm_echo_clean(self) -> "Action":
        if self.action == "warm_echo":
            if self.contradiction is not None:
                raise ValueError("warm_echo 轮不得带 contradiction")
            if self.converge_output is not None:
                raise ValueError("warm_echo 轮不得带 converge_output")
        return self

    @model_validator(mode="after")
    def _midpoint_clean(self) -> "Action":
        if self.action == "midpoint_reflect":
            if self.evidence:
                raise ValueError("midpoint_reflect 不得抽新 evidence（回顾已有，不新挖）")
            if self.contradiction is not None:
                raise ValueError("midpoint_reflect 不得带 contradiction")
            if self.converge_output is not None:
                raise ValueError("midpoint_reflect 不得带 converge_output")
            if self.dimension_targeted != "none":
                raise ValueError("midpoint_reflect 轮 dimension_targeted 必须是 none")
        return self

    @model_validator(mode="after")
    def _soft_closing_clean(self) -> "Action":
        if self.action == "soft_closing":
            if self.evidence:
                raise ValueError("soft_closing 不得抽新 evidence")
            if self.contradiction is not None:
                raise ValueError("soft_closing 不得带 contradiction")
            if self.converge_output is not None:
                raise ValueError("soft_closing 不得带 converge_output")
            if self.dimension_targeted != "none":
                raise ValueError("soft_closing 轮 dimension_targeted 必须是 none")
        return self


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

NEXT_PROMPT_LIMIT_ASK_REFLECT = 600
CONVERGE_INSIGHT_TOTAL_LIMIT = 1800
MAX_ROUNDS = 30
DEFAULT_TARGET_ROUNDS = 20
ONBOARDING_ROUND = 1
MAX_RETRIES = 3


def effective_target_rounds(prefs: Optional[UserPreferences]) -> int:
    if prefs is None or prefs.target_rounds is None:
        return DEFAULT_TARGET_ROUNDS
    return min(MAX_ROUNDS, max(6, prefs.target_rounds))


def midpoint_round(target_rounds: int) -> int:
    return max(4, target_rounds // 2)


def near_end_round(target_rounds: int) -> int:
    return max(midpoint_round(target_rounds) + 2, target_rounds - 2)


# ---------------------------------------------------------------------------
# 信号参考清单（仅作 phase prompt 渲染参考，不作 guardrails reject 依据）
#
# v2.1 设计转向：这些词命中后，不再硬拒 LLM 的 action 选择。
# 而是：phase 文件以朋友口吻讲"怎么判断用户状态、怎么反应"。
# 下面的列表给 phase prompt 提供"常见信号示例"，同时留给将来可能的
# 离线分析 / 日志标注用。
# ---------------------------------------------------------------------------

EMOTIONAL_DISTRESS_REFERENCE = (
    "累", "难过", "孤独", "委屈", "后悔", "害怕", "想哭", "撑不住",
    "迷茫", "烦", "受伤", "压抑", "崩溃", "喘不过气", "没意思",
    "失眠", "不想动", "麻木", "堵着", "窒息", "空空的", "心慌",
    "绝望", "虚", "废了", "没劲", "没动力",
)

SENSITIVE_TOPIC_REFERENCE = (
    "童年", "小时候", "父亲", "母亲", "爸爸", "妈妈", "家里吵",
    "家暴", "被打", "被骂", "被抛弃", "去世", "走了", "没了",
    "自残", "自杀", "想死", "性侵", "霸凌", "被欺负",
    "创伤", "被伤害", "被背叛",
)

THERAPIST_TONE_REFERENCE = (
    "我听见了你",
    "我想邀请你",
    "我好奇你",
    "我感受到你",
    "让我们一起",
    "我会陪着你",
    "这听起来很",
    "你愿意多和我说",
)

# 防御撤退信号：**这一条**仍保留作为 guardrails 参考，因为它是**用户明示边界**
# 的字面信号（"不想聊这个"），属于结构性尊重，不是品味判断。但 v2.1 里这个
# 检查也只生成提示注入 prompt，不做 reject。
DEFENSIVE_EXIT_MARKERS = (
    "不想聊", "不聊这个", "换个话题", "不想说了", "pass 吧",
)


# ---------------------------------------------------------------------------
# 兼容别名（让 v2.0 call sites 不炸，渐进迁移）
# ---------------------------------------------------------------------------

EMOTIONAL_DISTRESS_MARKERS = EMOTIONAL_DISTRESS_REFERENCE
SENSITIVE_TOPIC_MARKERS = SENSITIVE_TOPIC_REFERENCE
THERAPIST_TONE_PATTERNS = THERAPIST_TONE_REFERENCE
