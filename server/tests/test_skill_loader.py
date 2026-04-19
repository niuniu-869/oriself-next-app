"""skill_loader 加载测试 · v2.5.0 · frontmatter 驱动。"""
from pathlib import Path

from oriself_server.skill_loader import (
    SkillBundle,
    _parse_frontmatter,
    clear_cache,
    load_skill_bundle,
)


# server/tests/test_*.py → parent.parent.parent = app root → skill-repo/skills/oriself
SKILL_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "skill-repo" / "skills" / "oriself"
)


def setup_function(_):
    clear_cache()


# ---------------------------------------------------------------------------
# Frontmatter 解析
# ---------------------------------------------------------------------------


def test_parse_frontmatter_basic():
    text = "---\nname: foo\ndescription: bar\n---\n\nbody here"
    meta, body = _parse_frontmatter(text)
    assert meta == {"name": "foo", "description": "bar"}
    assert body == "body here"


def test_parse_frontmatter_missing():
    meta, body = _parse_frontmatter("no frontmatter here")
    assert meta == {}
    assert body == "no frontmatter here"


def test_parse_frontmatter_malformed():
    # 只有开头 --- 没闭合 → 当作没 frontmatter
    meta, body = _parse_frontmatter("---\nunclosed")
    assert meta == {}


# ---------------------------------------------------------------------------
# Bundle 载入
# ---------------------------------------------------------------------------


def test_load_skill_bundle_smoke():
    bundle = load_skill_bundle(SKILL_ROOT)
    assert bundle.skill_md, "SKILL.md body not loaded"
    # frontmatter 解析出来
    assert bundle.skill_meta.get("name") == "oriself"
    assert bundle.skill_meta.get("version") == "2.5.2"
    # reference 文件全到位
    assert "ethos" in bundle.refs
    assert "converge" in bundle.refs
    assert "mbti" in bundle.domain_md
    assert "situational-questions" in bundle.techniques
    assert "reflective-listening" in bundle.techniques
    assert "contradiction-probing" in bundle.techniques
    assert "exemplary-session" in bundle.examples


def test_phases_loaded():
    bundle = load_skill_bundle(SKILL_ROOT)
    # v2.5.0 · 6 个 phase，新命名（去数字前缀）
    assert len(bundle.phases) == 6
    for key in (
        "phase-onboarding",
        "phase-warmup",
        "phase-exploring",
        "phase-midpoint",
        "phase-deep",
        "phase-soft-closing",
    ):
        assert key in bundle.phases, f"missing {key}"
        assert bundle.phases[key].strip(), f"{key} body is empty"
    # 旧命名已移除
    assert "phase0-onboarding" not in bundle.phases
    assert "phase4-deep" not in bundle.phases


def test_frontmatter_needs_on_phases():
    bundle = load_skill_bundle(SKILL_ROOT)
    deep = bundle.refs["phase-deep"]
    # phase-deep 需要 contradiction-probing + situational-questions
    assert isinstance(deep.meta.get("needs"), list)
    assert "contradiction-probing" in deep.meta["needs"]
    assert "situational-questions" in deep.meta["needs"]


def test_ref_body_strips_frontmatter():
    bundle = load_skill_bundle(SKILL_ROOT)
    # body 应该不包含 YAML frontmatter 原文
    deep = bundle.refs["phase-deep"]
    assert not deep.body.startswith("---")
    assert "name: phase-deep" not in deep.body


# ---------------------------------------------------------------------------
# compose_conversation_prompt · progressive disclosure
# ---------------------------------------------------------------------------


def test_conversation_prompt_without_phase():
    bundle = load_skill_bundle(SKILL_ROOT)
    prompt = bundle.compose_conversation_prompt(domain="mbti", current_round=5)
    # 基本骨架
    assert "Oriself" in prompt
    assert "灵魂" in prompt or "铁则" in prompt
    assert "元原则（ETHOS）" in prompt
    assert "Domain · mbti" in prompt
    # 没指定 phase → 没有阶段指引段
    assert "# 本轮阶段指引" not in prompt


def test_conversation_prompt_with_phase_onboarding():
    bundle = load_skill_bundle(SKILL_ROOT)
    prompt = bundle.compose_conversation_prompt(
        domain="mbti", phase_key="phase-onboarding", current_round=1
    )
    assert "# 本轮阶段指引 · phase-onboarding" in prompt
    assert "R1 · 开场" in prompt
    # R1 · exemplary-session 应该加载
    assert "# 示例对话" in prompt


def test_conversation_prompt_with_phase_deep_loads_needed_techniques():
    bundle = load_skill_bundle(SKILL_ROOT)
    prompt = bundle.compose_conversation_prompt(
        domain="mbti", phase_key="phase-deep", current_round=12
    )
    # phase-deep.needs = [contradiction-probing, situational-questions]
    assert "# 工具箱 · contradiction-probing" in prompt
    assert "# 工具箱 · situational-questions" in prompt
    # 没在 needs 里的 reflective-listening 不应被塞
    assert "# 工具箱 · reflective-listening" not in prompt


def test_conversation_prompt_exemplary_only_in_early_rounds():
    bundle = load_skill_bundle(SKILL_ROOT)
    p_early = bundle.compose_conversation_prompt(
        domain="mbti", phase_key="phase-warmup", current_round=2
    )
    p_late = bundle.compose_conversation_prompt(
        domain="mbti", phase_key="phase-deep", current_round=12
    )
    assert "# 示例对话" in p_early
    assert "# 示例对话" not in p_late


def test_conversation_prompt_byte_size_reasonable():
    """v2.5.x 目标：R4+ 每轮 system prompt 不超过 v2.4 的 ~42KB。

    实测演化：
    - v2.5.0 初始：R12 phase-deep ≈ 35KB，R18 soft-closing ≈ 26KB
    - v2.5.1：+ mbti 信号清单 / phase-deep 二选一禁令 / phase-exploring
      协作强制+诗意回拉+silent 抢救 → R12 ≈ 40KB
    断言上限 42KB（v2.4 基线），主要防止退化：有人把 techniques 又固定塞回去
    会立刻 > 45KB。
    """
    bundle = load_skill_bundle(SKILL_ROOT)
    p_deep = bundle.compose_conversation_prompt(
        domain="mbti", phase_key="phase-deep", current_round=12
    )
    size = len(p_deep.encode("utf-8"))
    assert size < 42_000, f"R12 phase-deep prompt {size} bytes, 预期 < 42KB"

    # 收束期更短（needs=[]）
    p_close = bundle.compose_conversation_prompt(
        domain="mbti", phase_key="phase-soft-closing", current_round=18
    )
    assert len(p_close.encode("utf-8")) < 32_000


# ---------------------------------------------------------------------------
# compose_converge_prompt · 报告轮
# ---------------------------------------------------------------------------


def test_converge_prompt_composition():
    bundle = load_skill_bundle(SKILL_ROOT)
    prompt = bundle.compose_converge_prompt(domain="mbti")
    # CONVERGE.md 的特征词
    assert "Awwwards" in prompt or "设计师" in prompt
    assert "Domain · mbti" in prompt
    # converge prompt 不应该混入对话轮的 phase / techniques / exemplary
    assert "# 本轮阶段指引" not in prompt
    assert "情境化提问" not in prompt
    assert "示例对话" not in prompt


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_cache_behavior():
    bundle1 = load_skill_bundle(SKILL_ROOT)
    bundle2 = load_skill_bundle(SKILL_ROOT)
    assert bundle1 is bundle2
    clear_cache()
    bundle3 = load_skill_bundle(SKILL_ROOT)
    assert bundle3 is not bundle1
