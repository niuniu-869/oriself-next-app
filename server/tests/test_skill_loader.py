"""skill_loader 加载 + banned pattern 解析测试。"""
from pathlib import Path

from oriself_server.skill_loader import (
    BannedPattern,
    SkillBundle,
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


def test_load_skill_bundle_smoke():
    bundle = load_skill_bundle(SKILL_ROOT)
    assert bundle.skill_md, "SKILL.md not loaded"
    assert "mbti" in bundle.domain_md
    assert "situational-questions" in bundle.techniques
    assert "banned-outputs" in bundle.examples
    assert "exemplary-session" in bundle.examples


def test_banned_patterns_parsed():
    bundle = load_skill_bundle(SKILL_ROOT)
    assert len(bundle.banned_patterns) >= 8  # at least 8 per design
    ids = {p.id for p in bundle.banned_patterns}
    # 确保关键 pattern 全部加载
    for expected in {"BP1", "BP3", "BP5", "BP9"}:
        assert expected in ids, f"missing {expected}"
    # BP9 应带 note 并声明 round 约束
    bp9 = next(p for p in bundle.banned_patterns if p.id == "BP9")
    assert "round_number" in bp9.note or bp9.severity == "critical"


def test_system_prompt_composition():
    bundle = load_skill_bundle(SKILL_ROOT)
    # 不带 phase_key：skill_md + domain + techniques + example
    prompt = bundle.compose_system_prompt(domain="mbti")
    assert "OriSelf" in prompt and "陪聊" in prompt
    assert "Domain · mbti" in prompt
    assert "situational" in prompt.lower()
    # 不应拼入任何 phase 文件（因为 phase_key=None）
    assert "# 本轮阶段指引" not in prompt

    # 带 phase_key：拼入对应 phase 文件
    prompt2 = bundle.compose_system_prompt(domain="mbti", phase_key="phase0-onboarding")
    assert "# 本轮阶段指引" in prompt2
    assert "握手" in prompt2

    # 不同 phase 切换，互不污染
    prompt3 = bundle.compose_system_prompt(domain="mbti", phase_key="phase5-converge")
    assert "收敛" in prompt3 or "insight" in prompt3.lower()
    assert "握手" not in prompt3.split("# 本轮阶段指引")[1]


def test_phases_loaded():
    bundle = load_skill_bundle(SKILL_ROOT)
    assert len(bundle.phases) >= 6
    for key in (
        "phase0-onboarding",
        "phase1-warmup",
        "phase2-3-exploring",
        "phase3_5-midpoint",
        "phase4-deep",
        "phase4_8-soft-closing",
        "phase5-converge",
    ):
        assert key in bundle.phases, f"missing {key}"
        assert bundle.phases[key].strip(), f"{key} is empty"


def test_cache_behavior():
    bundle1 = load_skill_bundle(SKILL_ROOT)
    bundle2 = load_skill_bundle(SKILL_ROOT)
    assert bundle1 is bundle2  # same cached object
    clear_cache()
    bundle3 = load_skill_bundle(SKILL_ROOT)
    assert bundle3 is not bundle1  # reloaded
