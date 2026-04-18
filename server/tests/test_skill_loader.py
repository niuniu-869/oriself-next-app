"""skill_loader 加载测试 · v2.4。"""
from pathlib import Path

from oriself_server.skill_loader import SkillBundle, clear_cache, load_skill_bundle


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
    assert bundle.ethos_md, "ETHOS.md not loaded"
    assert bundle.converge_md, "CONVERGE.md not loaded"
    assert "mbti" in bundle.domain_md
    assert "situational-questions" in bundle.techniques
    assert "exemplary-session" in bundle.examples
    # v2.4 · banned-outputs 已删除
    assert "banned-outputs" not in bundle.examples


def test_conversation_prompt_composition():
    bundle = load_skill_bundle(SKILL_ROOT)
    # 不带 phase_key
    prompt = bundle.compose_conversation_prompt(domain="mbti")
    assert "OriSelf" in prompt and "陪聊" in prompt
    assert "元原则（ETHOS）" in prompt
    assert "Domain · mbti" in prompt
    assert "situational" in prompt.lower()
    assert "# 本轮阶段指引" not in prompt

    # 带 phase_key
    prompt2 = bundle.compose_conversation_prompt(domain="mbti", phase_key="phase0-onboarding")
    assert "# 本轮阶段指引" in prompt2
    assert "握手" in prompt2


def test_converge_prompt_composition():
    bundle = load_skill_bundle(SKILL_ROOT)
    prompt = bundle.compose_converge_prompt(domain="mbti")
    assert "Awwwards" in prompt or "设计师" in prompt
    assert "Domain · mbti" in prompt
    # converge prompt 不应该混入对话轮的 phase / techniques / exemplary-session
    assert "# 本轮阶段指引" not in prompt
    assert "情境化提问" not in prompt
    assert "示例对话" not in prompt


def test_phases_loaded():
    bundle = load_skill_bundle(SKILL_ROOT)
    # v2.4 · 只剩 6 个 phase（phase5-converge 被删）
    assert len(bundle.phases) == 6
    for key in (
        "phase0-onboarding",
        "phase1-warmup",
        "phase2-3-exploring",
        "phase3_5-midpoint",
        "phase4-deep",
        "phase4_8-soft-closing",
    ):
        assert key in bundle.phases, f"missing {key}"
        assert bundle.phases[key].strip(), f"{key} is empty"
    assert "phase5-converge" not in bundle.phases


def test_cache_behavior():
    bundle1 = load_skill_bundle(SKILL_ROOT)
    bundle2 = load_skill_bundle(SKILL_ROOT)
    assert bundle1 is bundle2
    clear_cache()
    bundle3 = load_skill_bundle(SKILL_ROOT)
    assert bundle3 is not bundle1
