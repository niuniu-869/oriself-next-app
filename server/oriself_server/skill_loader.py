"""
Skill 文件加载器 · v2.6.0 · 真模型按需加载（Pass 1 / Pass 2 双契约）。

v2.6 新增（Phase D · tool-use loop）：
- `list_all_names()` · catalogue 接口；启动时枚举所有可读 skill 名字
- `build_skill_index_block()` · Pass 1 system 用的"name + 一句 description"清单
- `compose_pass1_system(domain, current_round, target_rounds, prefs, ...)` · Pass 1 静态部分（不含 Skill Index 段也由它拼）
- `compose_pass2_system(domain, current_round, loaded_names, prefs, ...)` · Pass 2 不带 Skill Index、不带 tools
- `read_skill_batch(names)` · 把 LLM 选的名字解出 body；6 项校验由 skill_runner 调

v2.5.0 保留：
- 每个 reference md 头部带 YAML frontmatter（`name` / `description` /
  `applies_when` / `needs` / `loaded_when`）。
- SKILL.md body（剥 frontmatter 后）= 灵魂 + 铁则 + STATUS 协议；**每轮必在 context**。
- `compose_conversation_prompt(domain, phase_key, current_round)` 留作
  `ORISELF_SKILL_LOADING=static` 模式的回退实现。
- `compose_converge_prompt(domain)` 与对话轮完全独立（CONVERGE.md 不进对话 prompt）。

对齐 Anthropic 官方 skill 推荐结构（SKILL.md + reference files）：
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
    # v2.6 · catalogue / Pass 1 / Pass 2
    # ------------------------------------------------------------------
    # v2.6 catalogue：可被 read_skill 选中的名字。仅含 phases / techniques /
    # domains / examples 这四个子目录里的文件；ETHOS / SKILL / CONVERGE 不暴露
    # 给 LLM 选择（前两者每轮必塞，CONVERGE 只在报告轮生效）。
    _CATALOGUE_DIRS = ("phases", "techniques", "domains", "examples")

    def list_all_names(self) -> List[str]:
        """枚举 catalogue 名字，稳定排序，供 read_skill schema enum 注入。"""
        out: List[str] = []
        for r in self.refs.values():
            if r.parent_dir in self._CATALOGUE_DIRS:
                out.append(r.name)
        return sorted(set(out))

    def is_phase_name(self, name: str) -> bool:
        ref = self.refs.get(name)
        return bool(ref and ref.parent_dir == "phases")

    def is_example_name(self, name: str) -> bool:
        ref = self.refs.get(name)
        return bool(ref and ref.parent_dir == "examples")

    def is_in_catalogue(self, name: str) -> bool:
        ref = self.refs.get(name)
        return bool(ref and ref.parent_dir in self._CATALOGUE_DIRS)

    def get_skill_body(self, name: str) -> Optional[str]:
        """读 catalogue 内某个 skill 的 body；不在 catalogue 返 None。"""
        ref = self.refs.get(name)
        if ref is None or ref.parent_dir not in self._CATALOGUE_DIRS:
            return None
        return ref.body

    def get_skill_section_label(self, name: str) -> str:
        """Pass 2 拼接时的小节标题前缀（# 工具箱 · X / # 本轮阶段指引 · X / ...）。"""
        ref = self.refs.get(name)
        if ref is None:
            return f"# Skill · {name}"
        if ref.parent_dir == "phases":
            return f"# 本轮阶段指引 · {name}"
        if ref.parent_dir == "techniques":
            return f"# 工具箱 · {name}"
        if ref.parent_dir == "domains":
            return f"# Domain · {name}"
        if ref.parent_dir == "examples":
            return f"# 示例对话 · {name}"
        return f"# Skill · {name}"

    def build_skill_index_block(self) -> str:
        """Pass 1 system 用的 skill 索引：每行 `- name: description`。

        分组顺序：phase → technique → domain → example，让 LLM 容易"先选 phase 再补技法"。
        每条只取 frontmatter.description；description 缺失时用 path.stem。
        """
        groups = {
            "phases": [],
            "techniques": [],
            "domains": [],
            "examples": [],
        }
        for name in self.list_all_names():
            ref = self.refs[name]
            desc = str(ref.meta.get("description") or "").strip()
            line = f"- {name}: {desc}" if desc else f"- {name}"
            groups.setdefault(ref.parent_dir, []).append(line)

        parts: List[str] = ["# Skill Index（按 description 召回；不要猜不在表里的名字）"]
        labels = {
            "phases": "## phases · 每轮必选 1 个",
            "techniques": "## techniques · 按本轮真正需要选 0..N",
            "domains": "## domains · 域透镜（mbti 等）",
            "examples": "## examples · 风格参考（早轮可选）",
        }
        for key in ("phases", "techniques", "domains", "examples"):
            lines = groups.get(key) or []
            if not lines:
                continue
            parts.append(labels[key])
            parts.extend(lines)
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Pass 1 · 工具规划契约 system prompt
    # ------------------------------------------------------------------
    def compose_pass1_system(
        self,
        *,
        runtime_state_block: str,
        skill_index_block: str,
    ) -> str:
        """Pass 1 system prompt = SKILL body + ETHOS + Runtime State + Skill Index + 协议铁则。

        注意装配顺序：稳定文本（SKILL/ETHOS）在前，便于 prompt cache 命中；
        动态部分（Runtime State）与索引块放后面。
        """
        parts: List[str] = []
        parts.append(self.skill_md)
        if self.ethos_md:
            parts.append(f"\n\n---\n\n# 元原则（ETHOS）\n\n{self.ethos_md}")
        parts.append(runtime_state_block)
        parts.append("\n\n---\n\n" + skill_index_block)
        parts.append(_PASS1_CONTRACT_BLOCK)
        return "".join(parts)

    # ------------------------------------------------------------------
    # Pass 2 · 流式正文 system prompt
    # ------------------------------------------------------------------
    def compose_pass2_system(
        self,
        *,
        domain: str,
        runtime_state_block: str,
        loaded_names: List[str],
    ) -> str:
        """Pass 2 system prompt = SKILL body + ETHOS + Runtime State + 已加载 skills。

        注意：
        - **不带** Skill Index（已在 Pass 1 用过；ADR-5）
        - **不带** read_skill 工具（避免 LLM 在正文里又想调；由调用方传 tools=[]）
        - 即使 LLM 在 Pass 1 没选 mbti domain，本方法也不强制补——
          loaded_names 列表是什么就拼什么。Pass 1 校验已记录 phase_missing
          等违规信号，Pass 2 不替模型决策（v2.6 ADR-6 · 不兜底但全可观测）。
        """
        parts: List[str] = []
        parts.append(self.skill_md)
        if self.ethos_md:
            parts.append(f"\n\n---\n\n# 元原则（ETHOS）\n\n{self.ethos_md}")
        parts.append(runtime_state_block)
        if loaded_names:
            parts.append("\n\n---\n\n# Loaded Skills（Pass 1 你已选）")
            for name in loaded_names:
                body = self.get_skill_body(name)
                if not body:
                    # 名字不在 catalogue：不该出现（schema enum 应该已拦），
                    # 真出现就跳过，留给 violations 记录。
                    continue
                label = self.get_skill_section_label(name)
                parts.append(f"\n\n---\n\n{label}\n\n{body}")
        # 末段提醒 STATUS 协议（与 Pass 1 文本不同，更聚焦"创作不规划"）
        parts.append(_PASS2_FOCUS_BLOCK)
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


# ---------------------------------------------------------------------------
# v2.6 · Pass 1 协议契约文本 / Pass 2 聚焦文本 / read_skill schema
# ---------------------------------------------------------------------------


# Pass 1 协议契约文字。**不写抽象价值观**，只写动作 + 必须 + 顺序 + 预算 + 禁止。
# 与 §3.4 「执行合同写法」对齐。
_PASS1_CONTRACT_BLOCK = """

---

# 本轮你正在做什么（Pass 1 · 工具规划契约）

铁则：
1. 必须调用一次 `read_skill(names: string[])`。
2. names 至少 1 个，最多 8 个，不能为空。
3. names 必须**包含恰好 1 个 phase**（phase-onboarding / phase-warmup /
   phase-exploring / phase-midpoint / phase-deep / phase-soft-closing 之一），
   选哪个由 Runtime State 的轮号决定。**phase 每轮都要选**——哪怕这一轮的
   phase 与上一轮相同（例如 phase-deep 跨多轮深挖期），仍然要在 names 里写出来。
4. 当 R1-R3 时，names **必须包含** `exemplary-session`，即使前一轮已经选过。
5. 选 phase 之后，再按"该 phase 真正需要的 technique"补 0..N 个 technique；
   不必凑满 8 个。
   - 默认情况下：本会话已读过的 technique / domain **不必再选**（你能在
     history 里看到上一轮自己用它时的回复）。
   - 但如果本轮真的需要把该 technique / domain 的指引正文重新摆在你面前，
     直接重选即可。服务端会记一条 `redundant_read`（仅观测信号，**不是错误**），
     并把该 skill 的全文重新装进本轮 Pass 2 system prompt。
6. 不要在 message content 里写**任何**对话回复——本轮只做规划，
   服务端会丢弃 message content，正文必须等 Pass 2。
"""


_PASS2_FOCUS_BLOCK = """

---

# 本轮你正在做什么（Pass 2 · 创作正文）

- 你已经通过 Pass 1 选好了上面"Loaded Skills"里的内容；这一轮**只创作回复**，
  不再调任何工具。
- 末尾仍按既定协议独立写一行 `STATUS: CONTINUE` / `STATUS: CONVERGE`
  / `STATUS: NEED_USER`，服务端会自动剥除。
"""


@dataclass
class LoadedSkill:
    """read_skill_batch 实际加载到的一项。"""
    name: str
    body: str


@dataclass
class SkillViolation:
    """v2.6 · 6 项协议校验中的一条。

    `kind` ∈ {"zero_tool_read", "over_budget", "invalid_skill",
              "phase_missing", "exemplary_skipped", "redundant_read"}
    `detail` 给 benchmark 复盘用（具体哪个名字 / 个数等）。
    """
    kind: str
    detail: str = ""


@dataclass
class ReadSkillResult:
    """`read_skill_batch` 的返回结构（v2.6 · 经 codex 第二轮复审重做语义）。

    - `loaded`：本轮 Pass 2 应该装载的全部 skill（含本轮 LLM 选过但已加载过的）
    - `final_names`：与 loaded 一一对应的名字列表；**含 redundant 项**，Pass 2 直接拼这一份
    - `newly_loaded_names`：final_names 减去 already_loaded 后的"本轮新增"集合，
      落库到 `conversations.loaded_skill_names`，让下一轮 already_loaded 自然 union
    - `redundant_names`：final_names 里在 already_loaded 中的子集（trace 用）
    - `violations`：5 项校验里命中的（不含 zero_tool_read，由 runner 层判）

    语义关键：phase_missing / exemplary_skipped **基于 final_names**，不基于
    newly_loaded_names。否则 LLM 在 R3 正确地选了已加载的 phase + exemplary 反而
    会被误报；那是协议违反 ADR-6（不兜底但全可观测）的反面。
    """
    loaded: List[LoadedSkill] = field(default_factory=list)
    final_names: List[str] = field(default_factory=list)
    newly_loaded_names: List[str] = field(default_factory=list)
    redundant_names: List[str] = field(default_factory=list)
    violations: List[SkillViolation] = field(default_factory=list)


def read_skill_batch(
    bundle: SkillBundle,
    raw_names: List[str],
    *,
    already_loaded: Optional[List[str]] = None,
    current_round: int = 1,
) -> ReadSkillResult:
    """服务端 read_skill 实现 + 5 项协议校验（不含 zero_tool_read）。

    设计纪律（v2.6 §3.5 · 不兜底但全可观测）：
    - **不补全**：phase 缺失 / exemplary 缺失 → 只记 violation，不偷偷加
    - **不替换**：invalid name → 跳过该项，不替成最近邻
    - **保留输入顺序**：方便 LLM 在 Pass 2 system prompt 里看到自己想要的顺序
    - 重复读同 session 已有项 → 记 `redundant_read`，但本轮 Pass 2 仍要装载该项的全文
      （否则当 LLM 第 5 轮再选 phase-deep 时 Pass 2 反而看不到 phase-deep 指引）

    输入：
    - `raw_names`：LLM 在 Pass 1 给的（可能多次 tool_call 拼出来的）原始名字数组
    - `already_loaded`：本会话此前已经加载过的名字（runner 维护，跨轮 union）
    - `current_round`：用于判 `exemplary_skipped`（仅 R1-R3 才必须含 exemplary）
    """
    already_set = set(already_loaded or [])
    violations: List[SkillViolation] = []

    # ---- over_budget · schema 应该已拦，这里再防守一层 ----
    if len(raw_names) > 8:
        violations.append(
            SkillViolation(
                kind="over_budget",
                detail=f"names_count={len(raw_names)} > 8 · 仅取前 8",
            )
        )
        raw_names = raw_names[:8]

    # ---- invalid_skill / redundant_read · 顺序去重（仅同一 call 内去重） ----
    seen: set = set()
    final_names: List[str] = []
    loaded: List[LoadedSkill] = []
    newly: List[str] = []
    redundant: List[str] = []

    for name in raw_names:
        if not isinstance(name, str) or not name:
            violations.append(
                SkillViolation(kind="invalid_skill", detail=f"non-string: {name!r}")
            )
            continue
        if name in seen:
            # 同一 Pass 1 内重复（同 call 或多 tool_call）：无论类型一律记 redundant_read。
            # Pass 2 只装载一次，所以不再重复 append。
            violations.append(
                SkillViolation(kind="redundant_read", detail=f"dup-in-call: {name}")
            )
            continue
        seen.add(name)
        if not bundle.is_in_catalogue(name):
            violations.append(
                SkillViolation(kind="invalid_skill", detail=name)
            )
            continue
        body = bundle.get_skill_body(name)
        if not body:
            violations.append(
                SkillViolation(kind="invalid_skill", detail=f"empty-body: {name}")
            )
            continue
        # 本轮"有效选择"：LLM 真选过且 catalogue 内的项都进 final_names。
        loaded.append(LoadedSkill(name=name, body=body))
        final_names.append(name)
        if name in already_set:
            # 跨轮重读语义（codex 第 4 轮 P2 修复）：
            # - phase：每轮 Pass 1 协议都强制必选 1 个；跨轮重选**不算** redundant，
            #   是协议常态（R3 R4 R5 都可能落 phase-warmup 同一个）。
            # - exemplary-session：R1-R3 强制必选；这三轮内跨轮重选**不算** redundant。
            # - technique / domain / 其他 example：跨轮重读**记** redundant_read，
            #   因为它们没有"必须每轮重选"的协议要求；模型应当依赖前一轮的加载。
            #
            # 这一区分让 LLM 可以同时遵守 §3.2 协议铁则 #4（R1-R3 必选 exemplary）
            # 和 #5（已读不重）—— 这两条铁则在 phase / R1-R3 example 上语义重叠，
            # 服务端这里给一个明确边界，避免"怎么做都触发违规"。
            is_phase = bundle.is_phase_name(name)
            is_required_example = (
                bundle.is_example_name(name)
                and name == "exemplary-session"
                and 1 <= current_round <= 3
            )
            if not (is_phase or is_required_example):
                violations.append(
                    SkillViolation(
                        kind="redundant_read",
                        detail=f"already-loaded: {name}",
                    )
                )
                redundant.append(name)
        else:
            newly.append(name)

    # ---- phase_missing · "恰好 1 个 phase"（§3.2 协议 #3） ----
    # codex 第 3 轮 P2：原来只判 0 phase；多 phase（如同时选 phase-warmup +
    # phase-deep）也会让 Pass 2 收到冲突指引，必须同样标 violation。
    # 保持设计 §3.5 的 6 项 violation 列表不变 —— 把"非 1 个 phase"统一归
    # phase_missing，detail 里区分 0 vs >1，让 benchmark 能精确还原现场。
    phase_picks = [n for n in final_names if bundle.is_phase_name(n)]
    if len(phase_picks) != 1:
        if len(phase_picks) == 0:
            detail = f"0 phases selected; final_names={final_names}"
        else:
            detail = (
                f"{len(phase_picks)} phases selected (must be exactly 1): "
                f"{phase_picks}"
            )
        violations.append(
            SkillViolation(kind="phase_missing", detail=detail)
        )

    # ---- exemplary_skipped · 仅 R1-R3，基于 final_names ----
    if 1 <= current_round <= 3:
        has_exemplary = any(bundle.is_example_name(n) for n in final_names)
        if not has_exemplary:
            violations.append(
                SkillViolation(
                    kind="exemplary_skipped",
                    detail=f"R{current_round}: exemplary-session not selected",
                )
            )

    return ReadSkillResult(
        loaded=loaded,
        final_names=final_names,
        newly_loaded_names=newly,
        redundant_names=redundant,
        violations=violations,
    )


def read_skill_tool_schema(catalogue: List[str]) -> dict:
    """生成 read_skill 工具定义（OpenAI compatible function tool schema）。

    schema 极窄：
    - `enum` 动态填 catalogue（启动时由 list_all_names 注入）
    - `minItems=1` `maxItems=8`
    - description 写成"动作 + 必须 + 顺序 + 预算 + 禁止"，不写抽象价值观
    """
    return {
        "type": "function",
        "function": {
            "name": "read_skill",
            "description": (
                "Read one or more named skills before composing the response. "
                "Use this every turn before answering. "
                "Selection order: pick exactly 1 phase based on Runtime State "
                "(re-select the same phase if this turn is in the same phase as the previous turn); "
                "then techniques referenced by that phase that genuinely apply this turn; "
                "on R1-R3 always include 'exemplary-session' even if a prior turn loaded it. "
                "Budget: max 8 names per call. "
                "Already-loaded techniques/domains do not need to be re-selected by default "
                "(your previous assistant reply is in history); "
                "if you do need the skill body again this turn, re-select it — "
                "the server will log a redundant_read (observability only, not an error) "
                "and still ship the full body in this turn's Pass 2 system prompt. "
                "phase and R1-R3 'exemplary-session' are protocol-required every turn. "
                "Do not invent names. Do not skip this call. "
                "Do not place any reply in message content during this pass."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "names": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": list(catalogue),
                        },
                        "minItems": 1,
                        "maxItems": 8,
                    }
                },
                "required": ["names"],
                "additionalProperties": False,
            },
        },
    }
