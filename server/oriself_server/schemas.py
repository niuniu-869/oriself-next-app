"""
Pydantic schemas · v2.4 精简版。

设计转向：
- 对话轮 **不再**有 schema。LLM 输出纯文本，服务端透传 + 解析末行 STATUS。
- 只有 converge 报告生成这一步保留结构化 JSON 合同。
- 品味约束全在 skill prompt 里（散文指令），不再走 Pydantic validator。
- 硬约束只剩 4 条：轮数上限、mbti 正则、report_html XSS 安全、mbti 字母一致性。
  前 2 条在本文件里；后 2 条在 guardrails.py 里。
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

MAX_ROUNDS = 30              # 硬上限：到 R30 服务端强制 converge
DEFAULT_TARGET_ROUNDS = 20   # 用户没说时的默认
ONBOARDING_ROUND = 1
REPORT_MAX_RETRIES = 3       # 报告生成最多重试 3 次


# ---------------------------------------------------------------------------
# 类型别名
# ---------------------------------------------------------------------------

ChatStyle = Literal["casual", "deep", "literary", "analytical", "default"]
Pace = Literal["quick", "steady", "slow", "default"]
TypographyHint = Literal["editorial_serif", "editorial_mono", "editorial_minimal"]
TurnStatus = Literal["CONTINUE", "CONVERGE", "NEED_USER"]


# ---------------------------------------------------------------------------
# User preferences · R1 onboarding 的产物
# ---------------------------------------------------------------------------


class UserPreferences(BaseModel):
    """用户对话偏好 · hint，不做硬校验。

    v2.4 · target_rounds 仍保留，但只作节奏提示。服务端 hard cap 仍是 30。
    """
    style: ChatStyle = "default"
    target_rounds: Optional[int] = Field(default=None, ge=6, le=30)
    pace: Pace = "default"
    opening_mood: Optional[str] = Field(default=None, max_length=200)
    note: Optional[str] = Field(default=None, max_length=300)


def effective_target_rounds(prefs: Optional[UserPreferences]) -> int:
    if prefs is None or prefs.target_rounds is None:
        return DEFAULT_TARGET_ROUNDS
    return min(MAX_ROUNDS, max(6, prefs.target_rounds))


# ---------------------------------------------------------------------------
# Converge / 报告 schema · 唯一保留结构化的地方
# ---------------------------------------------------------------------------


class PullQuote(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    round: int = Field(ge=1)


class CardData(BaseModel):
    """名片元数据。"""
    title: str = Field(min_length=2, max_length=40)
    mbti_type: Optional[str] = Field(default=None, pattern=r"^[EI][SN][TF][JP]$")
    subtitle: str = Field(max_length=60)
    pull_quotes: List[PullQuote] = Field(max_length=3, default_factory=list)
    typography_hint: TypographyHint = "editorial_serif"


class InsightParagraph(BaseModel):
    theme: str = Field(min_length=2, max_length=40)
    body: str = Field(min_length=60, max_length=500)
    quoted_rounds: List[int] = Field(min_length=1)


class DimResult(BaseModel):
    """单维度的判定结果。LLM 在 confidence_per_dim 里填四条。"""
    letter: Literal["E", "I", "S", "N", "T", "F", "J", "P"]
    score: float = Field(ge=0.0, le=1.0)


DIM_LETTERS: Dict[str, set[str]] = {
    "E/I": {"E", "I"},
    "S/N": {"S", "N"},
    "T/F": {"T", "F"},
    "J/P": {"J", "P"},
}

DIM_ORDER: tuple[str, ...] = ("E/I", "S/N", "T/F", "J/P")


def derive_mbti_type(confidence_per_dim: Dict[str, DimResult]) -> str:
    """从 4 维 DimResult 派生 4 字母 MBTI。单一真相源。"""
    missing = [d for d in DIM_ORDER if d not in confidence_per_dim]
    if missing:
        raise ValueError(
            f"confidence_per_dim 缺少维度 {missing}，必须包含 {list(DIM_ORDER)} 全部四项"
        )
    letters: List[str] = []
    for dim in DIM_ORDER:
        dr = confidence_per_dim[dim]
        if dr.letter not in DIM_LETTERS[dim]:
            raise ValueError(
                f"confidence_per_dim['{dim}'].letter='{dr.letter}' "
                f"不在合法集合 {DIM_LETTERS[dim]}"
            )
        letters.append(dr.letter)
    return "".join(letters)


class ConvergeOutput(BaseModel):
    """converge 报告 · v2.4 · 由 ReportRunner.compose() 独立调用 LLM 产出。

    字段职责：
    - `confidence_per_dim` · **单一真相源**。runtime 从这里派生 `mbti_type`
    - `mbti_type` · 可省；若填了也会被派生值覆盖
    - `report_html` · 完整自包含 HTML。安全由 guardrails.verify_report_html_shape 把关；
      4 字母一致性由 guardrails.verify_report_html_consistency 把关
    - `insight_paragraphs` · 恰好 3 段；每段至少引 1 轮号
    - `card` · 名片元数据；`card.mbti_type` 由 validator 对齐派生值
    """
    mbti_type: Optional[str] = Field(
        default=None,
        pattern=r"^[EI][SN][TF][JP]$",
        description="若填了必须等于 confidence_per_dim 派生值；不填 runtime 派生",
    )
    confidence_per_dim: Dict[str, DimResult] = Field(
        default_factory=dict,
        description="必须包含 E/I, S/N, T/F, J/P 四个键，每个是 {letter, score}",
    )
    insight_paragraphs: List[InsightParagraph] = Field(min_length=3, max_length=3)
    card: CardData
    report_html: str = Field(
        min_length=1000,
        max_length=80000,
        description="完整自包含 HTML 页面字符串，<!DOCTYPE html> 到 </html>",
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

    @model_validator(mode="before")
    @classmethod
    def _synth_confidence_from_mbti(cls, data):
        """向后兼容：接受两种旧格式 → 统一升级为新结构化 DimResult。

        支持的输入形态：
        1. 新：{"E/I": {"letter": "I", "score": 0.7}, ...}
        2. 旧：{"E/I": 0.7, ...} + mbti_type="INTJ"
        3. 仅有 mbti_type + 无 confidence_per_dim → 默认 score=0.7 合成
        """
        if not isinstance(data, dict):
            return data
        cpd = data.get("confidence_per_dim")
        mt = data.get("mbti_type")
        letters_from_mt: Dict[str, str] = {}
        if mt and isinstance(mt, str) and len(mt) == 4:
            letters_from_mt = dict(zip(DIM_ORDER, mt))

        if not cpd:
            if letters_from_mt:
                data = dict(data)
                data["confidence_per_dim"] = {
                    dim: {"letter": letter, "score": 0.7}
                    for dim, letter in letters_from_mt.items()
                }
            return data

        if isinstance(cpd, dict) and any(
            isinstance(v, (int, float)) and not isinstance(v, bool)
            for v in cpd.values()
        ):
            upgraded: Dict[str, dict] = {}
            for dim, val in cpd.items():
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    letter = letters_from_mt.get(dim)
                    if letter is None:
                        letter = dim.split("/")[0]
                    upgraded[dim] = {"letter": letter, "score": float(val)}
                else:
                    upgraded[dim] = val
            data = dict(data)
            data["confidence_per_dim"] = upgraded
        return data

    @model_validator(mode="after")
    def _align_mbti_type_with_confidence(self) -> "ConvergeOutput":
        """派生权威 mbti_type，覆盖 LLM 自己写的字段，对齐 card.mbti_type。"""
        derived = derive_mbti_type(self.confidence_per_dim)
        object.__setattr__(self, "mbti_type", derived)
        object.__setattr__(self.card, "mbti_type", derived)
        return self
