"""Skill runner 集成测试（用 mock backend，不需要 API key）。"""
import pytest

from oriself_server.guardrails import OriSelfGuardrails, SessionState
from oriself_server.llm_client import MockBackend
from oriself_server.skill_loader import clear_cache, load_skill_bundle
from oriself_server.skill_runner import SkillRunner


@pytest.fixture(autouse=True)
def _cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def runner():
    bundle = load_skill_bundle()
    return SkillRunner(
        backend=MockBackend(seed=42),
        bundle=bundle,
        guardrails=OriSelfGuardrails(bundle),
    )


@pytest.mark.asyncio
async def test_step_first_round_onboarding(runner):
    """v2.1: R1 固定是 onboarding（偏好握手）。mock backend 产 ask 会被 guardrails
    拒掉，然后走 fallback_action 返回 onboarding 保底。"""
    session = SessionState(session_id="t1", domain="mbti")
    r = await runner.step(session, "我想测一下 MBTI，开始吧。")
    assert r.action.action == "onboarding"
    assert len(r.action.next_prompt) > 0


@pytest.mark.asyncio
async def test_full_30_rounds_reaches_converge(runner):
    session = SessionState(session_id="t-full", domain="mbti")
    results = []
    # 生成用户回答的脚本（保证 mock 能抽出 quote 作为 evidence）
    user_scripts = [
        "第{}轮我讲一个具体的场景，上周某一天发生的事情很长一段。".format(i + 1)
        for i in range(30)
    ]
    last_action_type = None
    for i, user_msg in enumerate(user_scripts):
        r = await runner.step(session, user_msg)
        results.append(r)
        session = runner.advance_state(session, user_msg, r)
        last_action_type = r.action.action
        if last_action_type == "converge":
            break

    assert last_action_type == "converge", (
        f"expected converge within 30 rounds, got: {last_action_type}"
    )
    converge_result = results[-1]
    # mock 给到 converge 时，通常 used_fallback=False，因为自己构造的 evidence 可能不够用
    # 不断言 used_fallback—关心的是 action type 能达到 converge


@pytest.mark.asyncio
async def test_step_empty_user_input_handled(runner):
    session = SessionState(session_id="t-empty", domain="mbti")
    # 空消息不会崩；R1 仍然固定保底到 onboarding
    r = await runner.step(session, "")
    assert r.action.action in ("onboarding", "ask", "reflect", "redirect")


class _BrokenBackend(MockBackend):
    """总是返回坏 JSON 的 backend。用于测 retry + fallback 路径。"""

    async def complete_json(self, messages, **kwargs):
        raise ValueError("simulated LLM malformed JSON")


@pytest.mark.asyncio
async def test_retry_then_fallback():
    bundle = load_skill_bundle()
    runner = SkillRunner(
        backend=_BrokenBackend(),
        bundle=bundle,
        guardrails=OriSelfGuardrails(bundle),
    )
    session = SessionState(session_id="t-broken", domain="mbti")
    r = await runner.step(session, "我想聊聊最近")
    assert r.used_fallback is True
    assert r.retries == 3
    # v2.1: R1 的 fallback 是 onboarding，不再是 ask
    assert r.action.action == "onboarding"
