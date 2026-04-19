"""
Skill 文件加载器 · v2.5.0 · frontmatter 驱动。

v2.5.0 重构要点（从 v2.4 的"一次性全塞"改为 progressive disclosure）：

- 每个 reference md 头部带 YAML frontmatter（`name` / `description` /
  `applies_when` / `needs` / `loaded_when`）。
- SKILL.md body（剥 frontmatter 后）= 灵魂 + 铁则 + STATUS 协议；**每轮必在 context**。
- `compose_conversation_prompt(domain, phase_key, current_round)`:
    · 每轮：SKILL body + ETHOS + domain + 当前 phase
    · 按当前 phase 的 `frontmatter.needs` 动态装配 techniques（不再固定 3 个全塞）
    · `examples/exemplary-session.md` 仅 R1-R3 加载
- `compose_converge_prompt(domain)` 与对话轮完全独立（CONVERGE.md 不进对话 prompt）。

这样对齐 Anthropic 官方 skill 推荐结构（SKILL.md + reference files）：
https://code.claude.com/docs/en/skills
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import os as _os

import yaml


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


# ---------------------------------------------------------------------------
# Frontmatter 解析
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> Tuple[dict, str]:
    """把 `---\\n<yaml>\\n---\\n<body>` 拆成 (meta, body)。

    - 没有 frontmatter、解析失败、或 frontmatter 不是 dict：返回 ({}, text)
    - 成功：返回 (meta_dict, body_with_leading_newlines_stripped)
    """
    if not text.startswith("---"):
        return {}, text
    # 最多切 3 段：空串 / frontmatter / body
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    body = parts[2].lstrip("\n")
    return meta, body


@dataclass
class RefFile:
    """一个带 frontmatter 的 skill reference 文件。"""

    name: str          # frontmatter.name；fallback 到 path.stem
    path: Path
    meta: dict
    body: str          # 剥除 frontmatter 后的正文

    @property
    def parent_dir(self) -> str:
        return self.path.parent.name


# ---------------------------------------------------------------------------
# SkillBundle
# ---------------------------------------------------------------------------


@dataclass
class SkillBundle:
    """整个 skill 目录加载到内存的索引。"""

    # 顶 SKILL.md 的 frontmatter + body（body 每轮必进 prompt）
    skill_meta: dict = field(default_factory=dict)
    skill_md: str = ""                                   # body（剥 frontmatter）

    # 所有 reference 文件 · 按 frontmatter.name（或 path.stem）索引
    refs: Dict[str, RefFile] = field(default_factory=dict)

    # Phase 选择顺序（v2.5.0 新命名）
    PHASE_ORDER = (
        "phase-onboarding",
        "phase-warmup",
        "phase-exploring",
        "phase-midpoint",
        "phase-deep",
        "phase-soft-closing",
    )

    # -- 便捷访问（兼容旧代码路径；全走 body，不带 frontmatter） ----------
    @property
    def ethos_md(self) -> str:
        return self.refs["ethos"].body if "ethos" in self.refs else ""

    @property
    def converge_md(self) -> str:
        return self.refs["converge"].body if "converge" in self.refs else ""

    @property
    def domain_md(self) -> Dict[str, str]:
        """domain_name → body。key 优先 meta.domain，其次 path.stem。"""
        out: Dict[str, str] = {}
        for r in self.refs.values():
            if r.parent_dir != "domains":
                continue
            key = str(r.meta.get("domain") or r.path.stem)
            out[key] = r.body
        return out

    @property
    def techniques(self) -> Dict[str, str]:
        return {
            r.name: r.body
            for r in self.refs.values()
            if r.parent_dir == "techniques"
        }

    @property
    def phases(self) -> Dict[str, str]:
        return {
            r.name: r.body
            for r in self.refs.values()
            if r.parent_dir == "phases"
        }

    @property
    def examples(self) -> Dict[str, str]:
        return {
            r.name: r.body
            for r in self.refs.values()
            if r.parent_dir == "examples"
        }

    def get_phase(self, phase_key: str) -> str:
        ref = self.refs.get(phase_key)
        return ref.body if ref and ref.parent_dir == "phases" else ""

    # ------------------------------------------------------------------
    # 对话轮 · system prompt（v2.5.0 · frontmatter 驱动）
    # ------------------------------------------------------------------
    def compose_conversation_prompt(
        self,
        domain: str = "mbti",
        phase_key: Optional[str] = None,
        current_round: int = 1,
    ) -> str:
        """装配对话轮 system prompt（progressive disclosure）。

        装配顺序（稳定在前，动态在后，利于 prompt cache）：
        1. SKILL.md body（灵魂 + 铁则 + STATUS 协议）
        2. ethos（元原则）— 每轮都塞
        3. domain/{domain}.md — 每轮都塞
        4. phases/{phase_key}.md — 本轮选 1 个
        5. phase.needs 里列出的 techniques — 动态，不再固定 3 个
        6. examples/exemplary-session.md — 仅 R1-R3
        """
        parts: List[str] = []

        # 1. SKILL body
        parts.append(self.skill_md)

        # 2. ETHOS
        if self.ethos_md:
            parts.append(f"\n\n---\n\n# 元原则（ETHOS）\n\n{self.ethos_md}")

        # 3. Domain
        if domain in self.domain_md:
            parts.append(
                f"\n\n---\n\n# Domain · {domain}\n\n{self.domain_md[domain]}"
            )

        # 4. 当前 phase
        phase_ref: Optional[RefFile] = None
        if phase_key and phase_key in self.refs:
            candidate = self.refs[phase_key]
            if candidate.parent_dir == "phases":
                phase_ref = candidate
                parts.append(
                    f"\n\n---\n\n# 本轮阶段指引 · {phase_key}\n\n{phase_ref.body}"
                )

        # 5. phase.needs 的 techniques
        needs: List[str] = []
        if phase_ref:
            raw = phase_ref.meta.get("needs") or []
            if isinstance(raw, list):
                needs = [str(n) for n in raw]

        for tech_name in needs:
            ref = self.refs.get(tech_name)
            if ref and ref.parent_dir == "techniques":
                parts.append(
                    f"\n\n---\n\n# 工具箱 · {tech_name}\n\n{ref.body}"
                )

        # 6. Exemplary session · 仅 R1-R3（后期对话自身已是足够参考）
        if current_round <= 3 and "exemplary-session" in self.refs:
            parts.append(
                f"\n\n---\n\n# 示例对话\n\n{self.refs['exemplary-session'].body}"
            )

        return "".join(parts)

    # ------------------------------------------------------------------
    # 报告轮 · system prompt（独立、不带 phase/technique/exemplary）
    # ------------------------------------------------------------------
    def compose_converge_prompt(self, domain: str = "mbti") -> str:
        """装配报告生成 prompt。

        装配：
        1. CONVERGE.md body · 完整指引 + 输出 JSON schema
        2. domain/{domain}.md · 维度定义（帮 LLM 写 confidence_per_dim）
        3. ETHOS.md · "给 TA 一张只属于 TA 的网页"这条元原则
        """
        parts: List[str] = []

        if self.converge_md:
            parts.append(self.converge_md)
        else:
            parts.append("# CONVERGE 指引缺失，请检查 skill-repo")

        if domain in self.domain_md:
            parts.append(
                f"\n\n---\n\n# Domain · {domain}\n\n{self.domain_md[domain]}"
            )

        if self.ethos_md:
            parts.append(f"\n\n---\n\n# 元原则（ETHOS）\n\n{self.ethos_md}")

        return "".join(parts)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _read_md(path: Path) -> Tuple[dict, str]:
    if not path.exists():
        return {}, ""
    raw = path.read_text(encoding="utf-8")
    return _parse_frontmatter(raw)


def _build_ref(path: Path) -> RefFile:
    meta, body = _read_md(path)
    name = str(meta.get("name") or path.stem)
    return RefFile(name=name, path=path, meta=meta, body=body)


@lru_cache(maxsize=4)
def load_skill_bundle(root: Path | str = DEFAULT_SKILL_ROOT) -> SkillBundle:
    """加载整个 skill 目录。

    目录结构（v2.5.0 · Anthropic reference 风格）：
        oriself/
        ├── SKILL.md               # 顶级，frontmatter + body
        ├── ETHOS.md               # reference
        ├── CONVERGE.md            # reference（仅报告轮）
        ├── domains/*.md           # reference
        ├── phases/*.md            # reference
        ├── techniques/*.md        # reference
        └── examples/*.md          # reference
    """
    root = Path(root)

    skill_meta, skill_body = _read_md(root / "SKILL.md")

    refs: Dict[str, RefFile] = {}

    # 根级单文件（ETHOS / CONVERGE）
    for filename in ("ETHOS.md", "CONVERGE.md"):
        p = root / filename
        if p.exists():
            r = _build_ref(p)
            refs[r.name] = r

    # 子目录 md（phases / techniques / domains / examples）
    for subdir in ("phases", "techniques", "domains", "examples"):
        d = root / subdir
        if not d.exists():
            continue
        for p in sorted(d.glob("*.md")):
            r = _build_ref(p)
            refs[r.name] = r

    return SkillBundle(
        skill_meta=skill_meta,
        skill_md=skill_body,
        refs=refs,
    )


def clear_cache() -> None:
    load_skill_bundle.cache_clear()
