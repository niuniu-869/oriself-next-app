"""
Pydantic schemas · v2.5.2 极简版。

设计转向：
- 对话轮 **不再**有 schema。LLM 输出纯文本，服务端透传 + 解析末行 STATUS。
- 报告生成（converge）也**不再**走 JSON schema：LLM 直接吐完整 HTML 文档，
  服务端解析 HTML → 抽取 MBTI 四字母 + `<title>` 作为 card_title。
- 品味约束全在 skill prompt 里（散文指令），不再走 Pydantic validator。
- 硬约束只剩 4 条：轮数上限、mbti 正则、report_html 安全与可解析、mbti 字母唯一一致。
  轮数/正则常量在本文件；HTML 相关全搬到 guardrails.py。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

MAX_ROUNDS = 30              # 硬上限：到 R30 服务端强制 converge
DEFAULT_TARGET_ROUNDS = 20   # 用户没说时的默认
ONBOARDING_ROUND = 1
MIN_CONVERGE_ROUND = 6       # 最低收束轮数，与 SKILL.md 铁则对齐；
                             # 低于此轮号的 CONVERGE 一律被服务端降级为 CONTINUE，
                             # 避免 LLM 偶发早收束让用户"R2 就跳报告页"
REPORT_MAX_RETRIES = 3       # 报告生成最多重试 3 次
REPORT_TIMEOUT_SEC = 300     # 单次 converge LLM 调用超时（含生成长 HTML 余量）


# ---------------------------------------------------------------------------
# 类型别名
# ---------------------------------------------------------------------------

ChatStyle = Literal["casual", "deep", "literary", "analytical", "default"]
Pace = Literal["quick", "steady", "slow", "default"]
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
# Converge 结果 · v2.5.2 · 不再 Pydantic 校验业务字段
# ---------------------------------------------------------------------------


class ConvergeOutput(BaseModel):
    """v2.5.2 converge 产物 · 极简三字段。

    - `mbti_type`: 服务端从 HTML 抽取的唯一 4 字母字符串（正则兜底校验）
    - `card_title`: 服务端从 HTML `<title>` 抽取的文本（给"最近信件"列表显示用）
    - `report_html`: 完整自包含 HTML 文档

    没有 insight_paragraphs / card / confidence_per_dim。那些作为"页面内容结构"仍在
    skill 文本里要求，但服务端不再以字段形式持久化——全在 HTML 里。
    """
    mbti_type: str = Field(pattern=r"^[EI][SN][TF][JP]$")
    card_title: Optional[str] = Field(default=None, max_length=200)
    report_html: str = Field(
        min_length=1000,
        max_length=80000,
        description="完整自包含 HTML 页面字符串，<!DOCTYPE html> 到 </html>",
    )
