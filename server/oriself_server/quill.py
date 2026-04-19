"""
Quill · v2.5.3 · 给用户看的"Oriself 此刻的笔触"。

设计初衷：
- 让用户在 token 流出前看到一行浅灰的铅笔批注，知道 Oriself 在酝酿什么
- 绝不泄露工程术语（phase_key / technique / 装配 / 加载 / 字节 / 百分比）
- 同 phase / 同 technique 在一封信里只显示一次，避免啰嗦

文案铁则（在 CODE REVIEW 里请守住）：
  禁止使用 "靠近 / 贴近 / 贴得 / 凑近 / 走近 / 更近" 及任何身体距离隐喻——
  会有人际入侵感。改用"时间停留 / 视线停留 / 笔触"类隐喻。
  第二人称统一用 "Oriself" + "你"；不出现 "TA / 他 / 她 / 系统 / 模型"。
"""
from __future__ import annotations

from typing import Iterable, List, Set, Tuple


# phase_key → 一行浅灰批注
_PHASE_LINES = {
    "phase-onboarding":   "Oriself 正在读你递过来的第一句话",
    "phase-warmup":       "Oriself 在斟酌怎么开口",
    "phase-exploring":    "Oriself 想多问一些",
    "phase-midpoint":     "Oriself 停下来，把前半程听到的摊在桌上",
    "phase-deep":         "Oriself 把笔尖停在这一行",
    "phase-soft-closing": "Oriself 开始把这封信慢慢收拢",
}

# technique name → 一行浅灰批注
_TECHNIQUE_LINES = {
    "reflective-listening":  "Oriself 把你刚说的，在心里念了一遍",
    "situational-questions": "Oriself 想到了一个画面，想让你也看看",
    "contradiction-probing": "Oriself 在你说的话里停了一下",
}


def phase_line(phase_key: str) -> str:
    return _PHASE_LINES.get(phase_key, "")


def technique_line(name: str) -> str:
    return _TECHNIQUE_LINES.get(name, "")


def derive_lines(
    *,
    phase_key: str,
    needs: Iterable[str],
    seen_phases: Set[str],
    seen_techniques: Set[str],
) -> Tuple[List[str], Set[str], Set[str]]:
    """计算本轮要显示的 quill 行 + 更新后的 seen 集合。

    规则：
    - phase 若在 seen_phases 里 → 不再显示
    - needs 里每个 technique 若在 seen_techniques 里 → 不再显示
    - 最多一行 phase + 一行 technique，共 ≤2 行
    - 返回的 seen_* 已合并本轮新出现的键，方便调用方写回

    不显示的 phase/technique 也算"已见"——本次虽然没展示，
    但 session 内它确实出现过，后续同样不重复写给用户看。
    """
    new_seen_phases = set(seen_phases)
    new_seen_techniques = set(seen_techniques)
    lines: List[str] = []

    if phase_key and phase_key not in new_seen_phases:
        line = phase_line(phase_key)
        if line:
            lines.append(line)
        new_seen_phases.add(phase_key)

    # 最多选 1 个 technique 呈现（避免同轮出两条 technique 显得像清单）
    for tech in needs:
        if not tech or tech in new_seen_techniques:
            continue
        line = technique_line(tech)
        new_seen_techniques.add(tech)
        if line and len([l for l in lines if l != ""]) < 2:
            # 已有 phase 行就只塞 1 个 technique 凑够 2；未见过的 technique 都记进 seen
            lines.append(line)
            break  # 出第一条就收手

    # 其余 needs 里没展示的（因为只挑第一条）仍然要标记成"已见"，后续轮不再露脸
    for tech in needs:
        if tech:
            new_seen_techniques.add(tech)

    return lines, new_seen_phases, new_seen_techniques
