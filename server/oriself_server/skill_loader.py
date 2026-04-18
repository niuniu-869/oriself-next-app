"""
Skill 文件加载器 · v2.4。

变化：
- 加载 SKILL.md / ETHOS.md / CONVERGE.md / phases/ / techniques/ / examples/ / domains/
- 提供两种 prompt 拼装入口：
    * compose_conversation_prompt(domain, phase_key) · 对话轮用
    * compose_converge_prompt(domain) · 报告生成轮用（独立 prompt，无 phase/technique/exemplary）
- 不再解析 banned-outputs.md（v2.4 已删）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

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
class SkillBundle:
    skill_md: str
    ethos_md: str
    converge_md: str
    domain_md: Dict[str, str] = field(default_factory=dict)
    techniques: Dict[str, str] = field(default_factory=dict)
    examples: Dict[str, str] = field(default_factory=dict)
    phases: Dict[str, str] = field(default_factory=dict)

    # Phase 加载顺序（runner 选 phase 时用作回落名单）
    PHASE_ORDER = (
        "phase0-onboarding",
        "phase1-warmup",
        "phase2-3-exploring",
        "phase3_5-midpoint",
        "phase4-deep",
        "phase4_8-soft-closing",
    )

    def get_phase(self, phase_key: str) -> str:
        return self.phases.get(phase_key, "")

    # ------------------------------------------------------------------
    # 对话轮 · system prompt
    # ------------------------------------------------------------------
    def compose_conversation_prompt(
        self,
        domain: str = "mbti",
        phase_key: Optional[str] = None,
    ) -> str:
        """装配对话轮的 system prompt。

        顺序：
        1. SKILL.md · 身份 + 灵魂 + 铁则 + STATUS 协议
        2. ETHOS.md · 元原则
        3. domain/{domain}.md · 域透镜
        4. phases/{phase_key}.md · 本轮指引（只一页）
        5. techniques/*.md · 工具箱
        6. examples/exemplary-session.md · 示例对话
        """
        parts = [self.skill_md]

        if self.ethos_md:
            parts.append(f"\n\n---\n\n# 元原则（ETHOS）\n\n{self.ethos_md}")

        if domain in self.domain_md:
            parts.append(f"\n\n---\n\n# Domain · {domain}\n\n{self.domain_md[domain]}")

        if phase_key and phase_key in self.phases:
            parts.append(f"\n\n---\n\n# 本轮阶段指引\n\n{self.phases[phase_key]}")

        for name in ("situational-questions", "reflective-listening", "contradiction-probing"):
            if name in self.techniques:
                parts.append(f"\n\n---\n\n# 工具箱 · {name}\n\n{self.techniques[name]}")

        if "exemplary-session" in self.examples:
            parts.append(f"\n\n---\n\n# 示例对话\n\n{self.examples['exemplary-session']}")

        return "".join(parts)

    # ------------------------------------------------------------------
    # 报告轮 · system prompt（独立、不带 phase/technique/exemplary）
    # ------------------------------------------------------------------
    def compose_converge_prompt(self, domain: str = "mbti") -> str:
        """装配报告生成 prompt。

        顺序：
        1. CONVERGE.md · 完整设计指引 + 输出 JSON schema
        2. domain/{domain}.md · 维度定义（帮 LLM 写 confidence_per_dim）
        3. ETHOS.md · "给 TA 一张只属于 TA 的网页"这条元原则

        不带 phase / techniques / exemplary-session —— 那是对话轮的语言风格参考，
        对报告生成是噪音。
        """
        parts = [self.converge_md or "# CONVERGE 指引缺失，请检查 skill-repo"]

        if domain in self.domain_md:
            parts.append(f"\n\n---\n\n# Domain · {domain}\n\n{self.domain_md[domain]}")

        if self.ethos_md:
            parts.append(f"\n\n---\n\n# 元原则（ETHOS）\n\n{self.ethos_md}")

        return "".join(parts)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=4)
def load_skill_bundle(root: Path | str = DEFAULT_SKILL_ROOT) -> SkillBundle:
    root = Path(root)
    skill_md = _read(root / "SKILL.md")
    ethos_md = _read(root / "ETHOS.md")
    converge_md = _read(root / "CONVERGE.md")

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

    return SkillBundle(
        skill_md=skill_md,
        ethos_md=ethos_md,
        converge_md=converge_md,
        domain_md=domain_md,
        techniques=techniques,
        examples=examples,
        phases=phases,
    )


def clear_cache() -> None:
    load_skill_bundle.cache_clear()
