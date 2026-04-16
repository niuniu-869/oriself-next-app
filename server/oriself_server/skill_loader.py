"""
Skill 文件加载器 (v2.1)。

变化：
- 新增 phases/ 目录加载
- compose_system_prompt 接受 phase_key 参数，只拼入当前 phase 的指令
- SKILL.md（精简本）+ domain + (单一 phase) + techniques + exemplary-session
  以分层方式组合；每轮 system prompt 只带当前 phase 的内容，避免 LLM
  在后期忘记前面塞的阶段规则。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import yaml


# Skill 本体来自 git submodule `skill-repo/`（即 niuniu-869/oriself-next 仓库）。
# 仓库内部结构是 `skills/oriself/...`，所以完整路径是 skill-repo/skills/oriself。
# server/oriself_server/skill_loader.py → parent.parent.parent = app root
import os as _os

_ENV_ROOT = _os.environ.get("ORISELF_SKILL_ROOT")
if _ENV_ROOT:
    DEFAULT_SKILL_ROOT = Path(_ENV_ROOT)
else:
    DEFAULT_SKILL_ROOT = (
        Path(__file__).resolve().parent.parent.parent
        / "skill-repo"
        / "skills"
        / "oriself"
    )


@dataclass
class BannedPattern:
    id: str
    kind: str
    pattern: str
    severity: str
    applies_to: List[str] = field(default_factory=list)
    note: str = ""


@dataclass
class SkillBundle:
    skill_md: str
    domain_md: Dict[str, str]
    techniques: Dict[str, str]
    examples: Dict[str, str]
    phases: Dict[str, str]           # v2.1 新增：phase_key -> content
    banned_patterns: List[BannedPattern]

    # Phase 加载顺序建议（runner 选 phase 时用）
    PHASE_ORDER = (
        "phase0-onboarding",
        "phase1-warmup",
        "phase2-3-exploring",
        "phase3_5-midpoint",
        "phase4-deep",
        "phase4_8-soft-closing",
        "phase5-converge",
    )

    def get_phase(self, phase_key: str) -> str:
        """按 key 取 phase 文件。fallback 到空串（让 prompt 优雅降级）。"""
        return self.phases.get(phase_key, "")

    def compose_system_prompt(
        self,
        domain: str = "mbti",
        phase_key: Optional[str] = None,
    ) -> str:
        """组装当前轮的 system prompt。

        顺序：SKILL.md (身份+灵魂+schema) → domain lens → 当前 phase 指令
        → techniques（3 份仅作工具箱参考）→ exemplary-session。

        注意：phase 文件只会被拼入一个。其他 phase 不进入上下文，避免塞爆。
        """
        parts = [self.skill_md]

        if domain in self.domain_md:
            parts.append(
                f"\n\n---\n\n# Domain · {domain}\n\n{self.domain_md[domain]}"
            )

        if phase_key and phase_key in self.phases:
            parts.append(
                f"\n\n---\n\n# 本轮阶段指引\n\n{self.phases[phase_key]}"
            )

        for name in ("situational-questions", "reflective-listening", "contradiction-probing"):
            if name in self.techniques:
                parts.append(
                    f"\n\n---\n\n# 工具箱 · {name}\n\n{self.techniques[name]}"
                )

        if "exemplary-session" in self.examples:
            parts.append(
                f"\n\n---\n\n# 示例对话\n\n{self.examples['exemplary-session']}"
            )
        return "".join(parts)


# ---------------------------------------------------------------------------
# banned-outputs.md 解析
# ---------------------------------------------------------------------------

_PATTERN_BLOCK_RE = re.compile(r"```yaml pattern\s*\n(.*?)\n```", re.DOTALL)


def _parse_banned_patterns(md_text: str) -> List[BannedPattern]:
    patterns: List[BannedPattern] = []
    for match in _PATTERN_BLOCK_RE.finditer(md_text):
        block = match.group(1)
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        required = {"id", "kind", "pattern", "severity"}
        if not required.issubset(data.keys()):
            continue
        patterns.append(
            BannedPattern(
                id=str(data["id"]),
                kind=str(data["kind"]),
                pattern=str(data["pattern"]),
                severity=str(data["severity"]),
                applies_to=list(data.get("applies_to", [])),
                note=str(data.get("note", "")),
            )
        )
    return patterns


# ---------------------------------------------------------------------------
# 主加载接口
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=4)
def load_skill_bundle(root: Path | str = DEFAULT_SKILL_ROOT) -> SkillBundle:
    root = Path(root)
    skill_md = _read(root / "SKILL.md")

    domain_md: Dict[str, str] = {}
    domains_dir = root / "domains"
    if domains_dir.exists():
        for p in sorted(domains_dir.glob("*.md")):
            domain_md[p.stem] = _read(p)

    techniques: Dict[str, str] = {}
    tech_dir = root / "techniques"
    if tech_dir.exists():
        for p in sorted(tech_dir.glob("*.md")):
            techniques[p.stem] = _read(p)

    examples: Dict[str, str] = {}
    ex_dir = root / "examples"
    if ex_dir.exists():
        for p in sorted(ex_dir.glob("*.md")):
            examples[p.stem] = _read(p)

    phases: Dict[str, str] = {}
    phases_dir = root / "phases"
    if phases_dir.exists():
        for p in sorted(phases_dir.glob("*.md")):
            phases[p.stem] = _read(p)

    banned = _parse_banned_patterns(examples.get("banned-outputs", ""))

    return SkillBundle(
        skill_md=skill_md,
        domain_md=domain_md,
        techniques=techniques,
        examples=examples,
        phases=phases,
        banned_patterns=banned,
    )


def clear_cache() -> None:
    load_skill_bundle.cache_clear()
