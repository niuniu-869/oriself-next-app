"""v2.6 真模型按需 · 单元测试 · `ORISELF_SKILL_LOADING=on-demand` 路径。

覆盖目标（与 docs/v2.6-on-demand-design.md 对齐）：

- §3.4 read_skill 工具 schema：enum 动态填 catalogue / minItems=1 / maxItems=8
- §3.5 协议校验 6 项：zero_tool_read / over_budget / invalid_skill /
  phase_missing / exemplary_skipped / redundant_read —— **记录但不补全**
- §3.2 Pass 1 system prompt 含 Skill Index；message.content 整段丢弃
- §3.3 Pass 2 system prompt 不带 Skill Index、不带 tools、含 Loaded Skills
- §4 Conversation 表新增 7 字段：trace 能塞进去（这里只验 dataclass，DB 落库
  在 routes 层另测）

设计纪律：
- 不写"兜底"测试。所有 violation 都是"该记录的就记录，不补"。
- 测 happy path 与 violation 各自的可观测性，不测自动修复。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from oriself_server.llm_client import (
    MockBackend,
    Pass1Result,
    ToolCallRequest,
    _parse_pass1_response,
    make_backend,
)
from oriself_server.skill_loader import (
    LoadedSkill,
    ReadSkillResult,
    SkillViolation,
    clear_cache,
    load_skill_bundle,
    read_skill_batch,
    read_skill_tool_schema,
)
from oriself_server.skill_runner import (
    Pass1Trace,
    SessionState,
    TurnRunner,
)


SKILL_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "skill-repo" / "skills" / "oriself"
)


def setup_function(_):
    clear_cache()


# ---------------------------------------------------------------------------
# §3.4 · read_skill 工具 schema · enum + 范围
# ---------------------------------------------------------------------------


def test_catalogue_lists_phases_techniques_domains_examples():
    """list_all_names 返回 11 个 catalogue 名字（6 phase + 3 technique + 1 domain + 1 example）。"""
    bundle = load_skill_bundle(SKILL_ROOT)
    names = bundle.list_all_names()
    assert "phase-onboarding" in names
    assert "phase-deep" in names
    assert "reflective-listening" in names
    assert "contradiction-probing" in names
    assert "situational-questions" in names
    assert "mbti" in names
    assert "exemplary-session" in names
    # 11 = 6 phases + 3 techniques + 1 domain + 1 example
    assert len(names) == 11
    # 不应暴露 ETHOS / SKILL / CONVERGE 给 LLM 选（每轮必塞或仅报告轮）
    assert "ethos" not in names
    assert "converge" not in names


def test_read_skill_tool_schema_shape():
    bundle = load_skill_bundle(SKILL_ROOT)
    schema = read_skill_tool_schema(bundle.list_all_names())
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "read_skill"
    # description 必须包含动作 + 必须 + 顺序 + 预算 + 禁止
    desc = fn["description"]
    assert "Read" in desc and "every turn" in desc
    assert "phase" in desc and "exemplary-session" in desc
    assert "max 8" in desc
    assert "Do not invent" in desc and "Do not skip" in desc
    # schema 极窄：names 数组，enum + 1..8
    params = fn["parameters"]
    assert params["required"] == ["names"]
    assert params["additionalProperties"] is False
    items = params["properties"]["names"]
    assert items["minItems"] == 1
    assert items["maxItems"] == 8
    assert isinstance(items["items"]["enum"], list)
    assert "phase-deep" in items["items"]["enum"]


# ---------------------------------------------------------------------------
# §3.2 / §3.3 · Pass 1 / Pass 2 system prompt 装配
# ---------------------------------------------------------------------------


def test_pass1_system_contains_skill_index_and_contract():
    bundle = load_skill_bundle(SKILL_ROOT)
    skill_index = bundle.build_skill_index_block()
    runtime = "\n\n---\n\n# Runtime State\n- 当前轮：R3 / target 20"
    p1 = bundle.compose_pass1_system(
        runtime_state_block=runtime,
        skill_index_block=skill_index,
    )
    # 必含 SKILL.md body（铁则 / 灵魂关键词）
    assert "Oriself" in p1 or "OriSelf" in p1
    # 必含 ETHOS
    assert "元原则（ETHOS）" in p1
    # 必含 Skill Index 段（每条 `- name: description`）
    assert "Skill Index" in p1
    assert "phase-onboarding" in p1
    assert "exemplary-session" in p1
    # 协议契约文字必在末段
    assert "Pass 1" in p1
    assert "read_skill" in p1
    assert "不要在 message content" in p1


def test_pass2_system_drops_skill_index_and_includes_loaded_only():
    bundle = load_skill_bundle(SKILL_ROOT)
    runtime = "\n\n---\n\n# Runtime State\n- 当前轮：R5 / target 20\n- 本轮 phase：phase-exploring"
    p2 = bundle.compose_pass2_system(
        domain="mbti",
        runtime_state_block=runtime,
        loaded_names=["phase-exploring", "reflective-listening"],
    )
    # 不含 Skill Index（ADR-5）
    assert "Skill Index" not in p2
    # 含 SKILL body / ETHOS
    assert "元原则（ETHOS）" in p2
    # 含 Loaded Skills 段
    assert "Loaded Skills" in p2
    assert "# 本轮阶段指引 · phase-exploring" in p2
    assert "# 工具箱 · reflective-listening" in p2
    # 不含未选的 technique
    assert "# 工具箱 · contradiction-probing" not in p2
    # Pass 2 聚焦提示
    assert "Pass 2" in p2
    assert "STATUS:" in p2


def test_pass2_system_handles_phase_missing():
    """Pass 1 没选 phase 也不补——Pass 2 system 仍然能拼。"""
    bundle = load_skill_bundle(SKILL_ROOT)
    runtime = "\n\n---\n\n# Runtime State\n- 本轮 phase：<未选>"
    # loaded_names 只有 technique，没 phase
    p2 = bundle.compose_pass2_system(
        domain="mbti",
        runtime_state_block=runtime,
        loaded_names=["reflective-listening"],
    )
    assert "# 工具箱 · reflective-listening" in p2
    # 没强行补 phase 段
    assert "# 本轮阶段指引" not in p2


# ---------------------------------------------------------------------------
# §3.5 · 6 项协议校验 · 每条单独触发，确认"记录但不补全"
# ---------------------------------------------------------------------------


def test_violation_phase_missing():
    bundle = load_skill_bundle(SKILL_ROOT)
    r = read_skill_batch(
        bundle,
        ["reflective-listening"],
        already_loaded=[],
        current_round=4,
    )
    kinds = [v.kind for v in r.violations]
    assert "phase_missing" in kinds
    # 不补全：loaded 仍只是 reflective-listening 一条
    assert [s.name for s in r.loaded] == ["reflective-listening"]
    assert r.final_names == ["reflective-listening"]


def test_violation_phase_missing_when_multiple_phases_selected():
    """codex 第 3 轮 P2：协议 §3.2 #3 要求 names 恰好 1 个 phase；
    LLM 选了 2 个 phase 同样要报 phase_missing（detail 区分 0 vs >1）。"""
    bundle = load_skill_bundle(SKILL_ROOT)
    r = read_skill_batch(
        bundle,
        ["phase-warmup", "phase-deep", "reflective-listening"],
        already_loaded=[],
        current_round=8,
    )
    kinds = [v.kind for v in r.violations]
    assert "phase_missing" in kinds
    detail = next(v.detail for v in r.violations if v.kind == "phase_missing")
    # detail 应能让 benchmark 看到"选了 2 个 phase"
    assert "2 phases" in detail or "phase-warmup" in detail
    assert "phase-deep" in detail


def test_violation_exemplary_skipped_only_in_early_rounds():
    bundle = load_skill_bundle(SKILL_ROOT)
    # R1 必须含 exemplary-session
    r1 = read_skill_batch(
        bundle,
        ["phase-onboarding"],
        already_loaded=[],
        current_round=1,
    )
    assert "exemplary_skipped" in [v.kind for v in r1.violations]
    # R5 不再要求
    r5 = read_skill_batch(
        bundle,
        ["phase-exploring"],
        already_loaded=[],
        current_round=5,
    )
    assert "exemplary_skipped" not in [v.kind for v in r5.violations]


def test_violation_invalid_skill_not_replaced():
    bundle = load_skill_bundle(SKILL_ROOT)
    r = read_skill_batch(
        bundle,
        ["phase-deep", "i-do-not-exist", "contradiction-probing"],
        already_loaded=[],
        current_round=12,
    )
    kinds = [v.kind for v in r.violations]
    assert "invalid_skill" in kinds
    # 不替换；只跳过该项
    assert "i-do-not-exist" not in r.final_names
    assert "phase-deep" in r.final_names
    assert "contradiction-probing" in r.final_names


def test_phase_recompose_across_turns_is_not_redundant():
    """codex 第 4 轮 P2 修复：phase 每轮都强制必选，跨轮重选不记 redundant_read。"""
    bundle = load_skill_bundle(SKILL_ROOT)
    r = read_skill_batch(
        bundle,
        ["phase-deep", "contradiction-probing"],
        already_loaded=["phase-deep", "situational-questions"],
        current_round=8,
    )
    kinds = [v.kind for v in r.violations]
    # phase-deep 跨轮重选 → 不算 redundant_read（它不在 detail 里）
    redundant_details = [
        v.detail for v in r.violations if v.kind == "redundant_read"
    ]
    assert all("phase-deep" not in d for d in redundant_details)
    # contradiction-probing 是新 technique → newly
    assert "contradiction-probing" in r.newly_loaded_names
    # phase-deep 不进 newly（它已经在 already_loaded 中），但仍进 final_names 装载
    assert "phase-deep" in r.final_names
    assert "phase-deep" not in r.newly_loaded_names
    # phase_missing 不应误报（恰好 1 个 phase）
    assert "phase_missing" not in kinds


def test_exemplary_recompose_in_r1_to_r3_is_not_redundant():
    """codex 第 4 轮 P2 修复：R1-R3 exemplary-session 跨轮重选不记 redundant_read。"""
    bundle = load_skill_bundle(SKILL_ROOT)
    r = read_skill_batch(
        bundle,
        ["phase-warmup", "exemplary-session", "reflective-listening"],
        already_loaded=["phase-onboarding", "exemplary-session"],
        current_round=2,
    )
    redundant_details = [
        v.detail for v in r.violations if v.kind == "redundant_read"
    ]
    assert all("exemplary-session" not in d for d in redundant_details)
    # exemplary 不进 newly（已 already_loaded），但进 final_names
    assert "exemplary-session" in r.final_names
    assert "exemplary-session" not in r.newly_loaded_names


def test_exemplary_recompose_after_r3_does_record_redundant():
    """R4 之后再选 exemplary-session 已无协议要求，跨轮重读应记 redundant_read。"""
    bundle = load_skill_bundle(SKILL_ROOT)
    r = read_skill_batch(
        bundle,
        ["phase-exploring", "exemplary-session"],
        already_loaded=["phase-warmup", "exemplary-session"],
        current_round=5,
    )
    kinds = [v.kind for v in r.violations]
    redundant_details = [
        v.detail for v in r.violations if v.kind == "redundant_read"
    ]
    assert "redundant_read" in kinds
    assert any("exemplary-session" in d for d in redundant_details)


def test_violation_redundant_read_still_loads_for_pass2():
    """codex 第 2 轮 P2 修复：跨轮重读 → 记 redundant_read，但 Pass 2 仍要装载该 skill 全文。

    场景：R6 已加载 contradiction-probing；R7 LLM 又选了它 + phase-deep。
    Pass 2 在 R7 应该看到 phase-deep + contradiction-probing 全文（即使后者重复）；
    否则 R7 Pass 2 反而看不到这一轮想强调的工具。
    """
    bundle = load_skill_bundle(SKILL_ROOT)
    r = read_skill_batch(
        bundle,
        ["phase-deep", "contradiction-probing"],
        already_loaded=["contradiction-probing"],
        current_round=7,
    )
    kinds = [v.kind for v in r.violations]
    assert "redundant_read" in kinds
    assert r.redundant_names == ["contradiction-probing"]
    # final_names 含 redundant：Pass 2 仍要拼这一项的 body
    assert r.final_names == ["phase-deep", "contradiction-probing"]
    # 但 newly_loaded_names 仅含本轮新增 → 落库后 already_loaded 不会双重计数
    assert r.newly_loaded_names == ["phase-deep"]


def test_phase_already_loaded_does_not_trigger_phase_missing():
    """codex 第 2 轮 P2 修复：LLM 在 R7 又选已加载的 phase-deep —— phase_missing
    必须**不**误报。"""
    bundle = load_skill_bundle(SKILL_ROOT)
    r = read_skill_batch(
        bundle,
        ["phase-deep", "contradiction-probing"],
        already_loaded=["phase-deep", "contradiction-probing"],
        current_round=7,
    )
    kinds = [v.kind for v in r.violations]
    # redundant_read 仍记，但 phase_missing 不应出现
    assert "redundant_read" in kinds
    assert "phase_missing" not in kinds
    assert r.newly_loaded_names == []


def test_exemplary_already_loaded_does_not_trigger_skipped():
    """R3 LLM 又选 exemplary-session（已加载）—— exemplary_skipped 必须不误报。

    codex 第 4 轮 P2 修复后：R1-R3 exemplary-session 是协议必选，跨轮重选
    不算 redundant_read，也仍计 final_names；exemplary_skipped 不应误报。
    """
    bundle = load_skill_bundle(SKILL_ROOT)
    r = read_skill_batch(
        bundle,
        ["phase-warmup", "exemplary-session"],
        already_loaded=["exemplary-session"],
        current_round=3,
    )
    kinds = [v.kind for v in r.violations]
    assert "exemplary_skipped" not in kinds
    # phase 与 R1-R3 exemplary 在 redundant_read 范围之外
    redundant_details = [
        v.detail for v in r.violations if v.kind == "redundant_read"
    ]
    assert all("exemplary-session" not in d for d in redundant_details)


def test_violation_over_budget_truncates_to_8():
    bundle = load_skill_bundle(SKILL_ROOT)
    catalogue = bundle.list_all_names()
    # 凑 9 个，含 phase
    nine = ["phase-deep"] + [n for n in catalogue if n != "phase-deep"][:8]
    assert len(nine) == 9
    r = read_skill_batch(
        bundle,
        nine,
        already_loaded=[],
        current_round=12,
    )
    assert "over_budget" in [v.kind for v in r.violations]
    # final_names <= 8
    assert len(r.final_names) <= 8


def test_no_violations_on_clean_pass1():
    bundle = load_skill_bundle(SKILL_ROOT)
    r = read_skill_batch(
        bundle,
        ["phase-deep", "contradiction-probing", "situational-questions"],
        already_loaded=[],
        current_round=12,
    )
    assert r.violations == []
    assert [s.name for s in r.loaded] == [
        "phase-deep",
        "contradiction-probing",
        "situational-questions",
    ]


# ---------------------------------------------------------------------------
# §3.2 · Mock backend 的 Pass 1 happy-path fixture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_call_tools_only_r1_includes_exemplary():
    backend = MockBackend()
    bundle = load_skill_bundle(SKILL_ROOT)
    schema = read_skill_tool_schema(bundle.list_all_names())
    from oriself_server.llm_client import Message

    msgs = [Message(role="system", content="x"), Message(role="user", content="嗨")]
    res = await backend.call_tools_only(msgs, tools=[schema])
    assert isinstance(res, Pass1Result)
    assert len(res.tool_calls) == 1
    assert res.tool_calls[0].name == "read_skill"
    names = res.tool_calls[0].arguments.get("names")
    assert "phase-onboarding" in names
    assert "exemplary-session" in names
    # message.content 整段丢弃 → 这里 mock 给的是空串
    assert res.content_dropped == ""


# ---------------------------------------------------------------------------
# `_parse_pass1_response` · OpenAI compatible chat completions 响应解析
# ---------------------------------------------------------------------------


def test_parse_pass1_response_drops_content_keeps_tool_calls():
    """LLM 在 Pass 1 偷写 message.content：要落 trace 但不进对话。"""
    fake = {
        "choices": [
            {
                "message": {
                    "content": "这是 LLM 在 Pass 1 偷写的正文，应该被丢",
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "read_skill",
                                "arguments": json.dumps(
                                    {"names": ["phase-deep", "contradiction-probing"]}
                                ),
                            },
                        }
                    ],
                }
            }
        ]
    }
    parsed = _parse_pass1_response(fake)
    assert parsed.content_dropped.startswith("这是 LLM")
    assert len(parsed.tool_calls) == 1
    tc = parsed.tool_calls[0]
    assert tc.name == "read_skill"
    assert tc.arguments == {"names": ["phase-deep", "contradiction-probing"]}
    assert tc.arguments_parse_error is None


def test_parse_pass1_response_handles_arguments_parse_error():
    fake = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_x",
                            "type": "function",
                            "function": {
                                "name": "read_skill",
                                "arguments": "{not-valid-json",
                            },
                        }
                    ],
                }
            }
        ]
    }
    parsed = _parse_pass1_response(fake)
    tc = parsed.tool_calls[0]
    assert tc.arguments == {}
    assert tc.arguments_parse_error is not None


# ---------------------------------------------------------------------------
# End-to-end · TurnRunner(loader_mode="on-demand") 跑通一轮
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turnrunner_on_demand_emits_pass1_trace_and_streams():
    backend = MockBackend()
    bundle = load_skill_bundle(SKILL_ROOT)
    runner = TurnRunner(backend=backend, bundle=bundle, loader_mode="on-demand")
    session = SessionState(session_id=str(uuid.uuid4()), domain="mbti")

    pass1: Pass1Trace | None = None
    visible = ""
    status = "?"
    tokens_seen = 0
    async for kind, payload in runner.stream_turn(session, "嗨"):
        if kind == "pass1":
            pass1 = payload
        elif kind == "token":
            tokens_seen += 1
        elif kind == "visible":
            visible = payload
        elif kind == "status":
            status = payload

    assert pass1 is not None
    assert pass1.skill_loader_mode == "on-demand"
    assert pass1.chosen_phase_key == "phase-onboarding"
    assert pass1.phase_match_rn is True
    # mock 在 R1 选 phase-onboarding + exemplary-session → 0 violation
    assert [v.kind for v in pass1.violations] == []
    assert "phase-onboarding" in pass1.loaded_skill_names
    assert "exemplary-session" in pass1.loaded_skill_names
    # tool_calls_json 是 JSON array（runner 内部 _serialize_tool_calls）
    decoded = json.loads(pass1.tool_calls_json)
    assert isinstance(decoded, list) and decoded[0]["name"] == "read_skill"

    # Pass 2 真的流式吐了 token 且 STATUS 协议正常
    assert tokens_seen > 0
    assert status in ("CONTINUE", "CONVERGE", "NEED_USER")
    assert "STATUS" not in visible


@pytest.mark.asyncio
async def test_turnrunner_on_demand_does_not_repeat_already_loaded():
    """Mock 在 R2 仍会选 reflective-listening；如果上一轮已加载过 phase-onboarding
    + exemplary-session，则本轮应只新增 phase-warmup + reflective-listening，
    旧的不会重复读。"""
    backend = MockBackend()
    bundle = load_skill_bundle(SKILL_ROOT)
    runner = TurnRunner(backend=backend, bundle=bundle, loader_mode="on-demand")
    session = SessionState(session_id=str(uuid.uuid4()), domain="mbti")

    # 先用 advance_state 模拟 R1 已经跑过且加载了 phase-onboarding + exemplary
    from oriself_server.skill_runner import Turn

    session = SessionState(
        session_id=session.session_id,
        domain="mbti",
        turns=[
            Turn(
                round_number=1,
                user_message="嗨",
                oriself_text="嗨——最近脑子里在转的事？",
                status="CONTINUE",
                discarded=False,
                loaded_skills=["phase-onboarding", "exemplary-session"],
            )
        ],
    )

    pass1: Pass1Trace | None = None
    async for kind, payload in runner.stream_turn(session, "我有点焦虑"):
        if kind == "pass1":
            pass1 = payload

    assert pass1 is not None
    # R2 mock 选 phase-warmup + reflective-listening + exemplary-session
    # exemplary-session 跨轮重选在 R1-R3 是协议必选，不算 redundant_read（codex P2 修）
    kinds = [v.kind for v in pass1.violations]
    assert "redundant_read" not in kinds
    # loaded_skill_names 是"本轮新增"列表 → exemplary-session 已 already_loaded，不进
    assert "exemplary-session" not in pass1.loaded_skill_names
    assert "phase-warmup" in pass1.loaded_skill_names
    assert "reflective-listening" in pass1.loaded_skill_names


# ---------------------------------------------------------------------------
# zero_tool_read · 用一个特意"不调任何工具"的假 backend 触发
# ---------------------------------------------------------------------------


class _SilentBackend(MockBackend):
    """Pass 1 直接返 0 个 tool_calls 的 mock，用于触发 zero_tool_read。"""

    async def call_tools_only(self, messages, tools, *, timeout=60.0, tool_choice="required"):
        return Pass1Result(tool_calls=[], content_dropped="忘了调工具", raw_response={})


class _SamePhaseBackend(MockBackend):
    """让 LLM 跨轮重选同一个 phase（phase-deep）；用于验证 chosen_phase 推断
    不会因为 final_names 过滤掉已加载项而退化为 None。"""

    async def call_tools_only(self, messages, tools, *, timeout=60.0, tool_choice="required"):
        return Pass1Result(
            tool_calls=[
                ToolCallRequest(
                    name="read_skill",
                    arguments={"names": ["phase-deep", "contradiction-probing"]},
                    raw_arguments=json.dumps(
                        {"names": ["phase-deep", "contradiction-probing"]}
                    ),
                    call_id="tc-1",
                )
            ],
            content_dropped="",
            raw_response={"mock": True},
        )


@pytest.mark.asyncio
async def test_chosen_phase_recovers_when_phase_already_loaded():
    """codex 复审 P2：LLM 在 R7 选了已经在 R6 加载过的 phase-deep，
    `final_names` 不会再含 phase-deep（redundant），但 chosen_phase 仍应是 phase-deep。"""
    backend = _SamePhaseBackend()
    bundle = load_skill_bundle(SKILL_ROOT)
    runner = TurnRunner(backend=backend, bundle=bundle, loader_mode="on-demand")
    from oriself_server.skill_runner import Turn

    history = [
        Turn(
            round_number=i,
            user_message=f"u{i}",
            oriself_text=f"o{i}",
            status="CONTINUE",
            discarded=False,
            loaded_skills=(
                ["phase-deep", "contradiction-probing"] if i == 6 else []
            ),
        )
        for i in range(1, 7)
    ]
    session = SessionState(
        session_id=str(uuid.uuid4()),
        domain="mbti",
        turns=history,
    )

    pass1: Pass1Trace | None = None
    async for kind, payload in runner.stream_turn(session, "继续聊"):
        if kind == "pass1":
            pass1 = payload

    assert pass1 is not None
    # phase-deep 在 already_loaded → newly_loaded 里没有；但 chosen_phase 必须仍能识别
    assert pass1.chosen_phase_key == "phase-deep"
    # codex 第 2 轮 P2 修复后：DB 落的是"本轮新增"，不是含 redundant 的 final_names
    assert pass1.loaded_skill_names == []
    # 6 项校验里有 redundant_read（两条都已经在 R6 加载过）
    assert "redundant_read" in [v.kind for v in pass1.violations]
    # 关键：phase_missing **不应**误报
    assert "phase_missing" not in [v.kind for v in pass1.violations]


@pytest.mark.asyncio
async def test_turnrunner_on_demand_zero_tool_read_is_recorded_not_filled():
    backend = _SilentBackend()
    bundle = load_skill_bundle(SKILL_ROOT)
    runner = TurnRunner(backend=backend, bundle=bundle, loader_mode="on-demand")
    session = SessionState(session_id=str(uuid.uuid4()), domain="mbti")

    pass1: Pass1Trace | None = None
    visible = ""
    async for kind, payload in runner.stream_turn(session, "嗨"):
        if kind == "pass1":
            pass1 = payload
        elif kind == "visible":
            visible = payload

    assert pass1 is not None
    kinds = [v.kind for v in pass1.violations]
    assert "zero_tool_read" in kinds
    # 不补全：loaded_skill_names 为空，phase_missing / exemplary_skipped 也都记下
    assert pass1.loaded_skill_names == []
    assert "phase_missing" in kinds
    # 即使如此 Pass 2 仍然跑（仅靠 SKILL+ETHOS+Runtime）— v2.6 ADR-6 要求可观测但不兜底
    assert visible  # 仍然有可见正文


# ---------------------------------------------------------------------------
# Pass 2 不再重新规划 · runner 不传 tools 给 stream_text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_multi_turn_happy_path_has_no_redundant_read():
    """codex 第 5 轮 P3 修复后：跨多轮 mock session 应保持干净 happy path（0 redundant_read）。"""
    from oriself_server.skill_runner import advance_state

    backend = MockBackend()
    bundle = load_skill_bundle(SKILL_ROOT)
    runner = TurnRunner(backend=backend, bundle=bundle, loader_mode="on-demand")
    session = SessionState(session_id=str(uuid.uuid4()), domain="mbti")

    all_violations: list[str] = []
    for i in range(1, 7):
        visible = ""
        status = "CONTINUE"
        loaded: list[str] = []
        async for kind, payload in runner.stream_turn(session, f"用户 R{i} 回复"):
            if kind == "pass1":
                all_violations.extend([v.kind for v in payload.violations])
                loaded = list(payload.loaded_skill_names or [])
            elif kind == "visible":
                visible = payload
            elif kind == "status":
                status = payload
        session = advance_state(
            session, f"用户 R{i} 回复", visible, status, loaded_skills=loaded
        )

    # mock happy path 跨 6 轮不应有任何 redundant_read
    assert "redundant_read" not in all_violations, (
        f"mock 跨轮重复，violations: {all_violations}"
    )
    # phase_missing / exemplary_skipped / over_budget / invalid_skill 也都不该有
    assert "phase_missing" not in all_violations
    assert "exemplary_skipped" not in all_violations


@pytest.mark.asyncio
async def test_pass1_runtime_state_does_not_list_phase_or_required_exemplary_as_already_loaded():
    """codex 第 5 轮 P2 修复：Runtime State 已读列表不应包含 phase / R1-R3 exemplary。"""
    from oriself_server.skill_runner import _runtime_state_block_pass1, Turn

    bundle = load_skill_bundle(SKILL_ROOT)
    session = SessionState(
        session_id="abc12345-678",
        domain="mbti",
        turns=[
            Turn(
                round_number=1,
                user_message="嗨",
                oriself_text="嗨——",
                status="CONTINUE",
                discarded=False,
                loaded_skills=["phase-onboarding", "exemplary-session"],
            ),
            Turn(
                round_number=2,
                user_message="标准聊",
                oriself_text="嗯",
                status="CONTINUE",
                discarded=False,
                loaded_skills=["phase-warmup", "reflective-listening"],
            ),
        ],
    )
    block = _runtime_state_block_pass1(
        session, current_round=3, target_rounds=20, bundle=bundle
    )
    # phase 全部不在已读列表
    assert "phase-onboarding" not in block
    assert "phase-warmup" not in block
    # R3（current_round=3）exemplary-session 仍是必选 → 不在已读列表
    assert "exemplary-session" not in block
    # 但 reflective-listening 是 technique 跨轮 → 应在已读列表里提醒
    assert "reflective-listening" in block


@pytest.mark.asyncio
async def test_pass2_does_not_accumulate_history_skills():
    """codex 第 2 轮 P2 修复：R2+ 的 Pass 2 system 只含本轮 LLM 选的 skill，
    不能再塞入 R1 的 phase-onboarding。"""
    bundle = load_skill_bundle(SKILL_ROOT)
    captured_pass2_systems: list[str] = []

    class _SpyBackend(MockBackend):
        async def stream_text(self, messages, *, timeout=90.0):
            sys_text = next(
                (m.content for m in messages if m.role == "system"), ""
            )
            if "Pass 2" in sys_text:
                captured_pass2_systems.append(sys_text)
            async for ch in super().stream_text(messages, timeout=timeout):
                yield ch

    backend = _SpyBackend()
    runner = TurnRunner(backend=backend, bundle=bundle, loader_mode="on-demand")
    from oriself_server.skill_runner import Turn

    # 假装 R1 已经走完，加载过 phase-onboarding + exemplary-session
    session = SessionState(
        session_id=str(uuid.uuid4()),
        domain="mbti",
        turns=[
            Turn(
                round_number=1,
                user_message="嗨",
                oriself_text="嗨——最近脑子里在转的事？",
                status="CONTINUE",
                discarded=False,
                loaded_skills=["phase-onboarding", "exemplary-session"],
            )
        ],
    )
    async for _ in runner.stream_turn(session, "我有点焦虑"):
        pass

    assert len(captured_pass2_systems) == 1
    sys_text = captured_pass2_systems[0]
    # Mock R2 选 phase-warmup + reflective-listening + exemplary-session
    # (exemplary 是 redundant，但仍要拼)。phase-onboarding 不应再出现
    # 在 Pass 2 system 里——它是 R1 的，本轮 LLM 没选。
    assert "# 本轮阶段指引 · phase-warmup" in sys_text
    assert "# 工具箱 · reflective-listening" in sys_text
    assert "# 本轮阶段指引 · phase-onboarding" not in sys_text


@pytest.mark.asyncio
async def test_pass2_stream_does_not_call_tools_again():
    """`stream_text` 调用不带 tools 参数，避免 LLM 在正文里又想调。"""
    bundle = load_skill_bundle(SKILL_ROOT)

    seen_pass2_tools: list = []

    class _SpyBackend(MockBackend):
        async def stream_text(self, messages, *, timeout=90.0):
            # 我们只关心 stream_text 是否被某层意外塞了 tools；MockBackend
            # 的 stream_text 签名本身不接 tools，所以这里检查 message system
            # 段是否带 read_skill 工具契约文字（Pass 2 不应再带 Pass 1 文本）
            sys_text = next(
                (m.content for m in messages if m.role == "system"), ""
            )
            if "Skill Index" in sys_text:
                seen_pass2_tools.append("skill_index_leaked")
            if "read_skill" in sys_text:
                seen_pass2_tools.append("tool_contract_leaked")
            async for ch in super().stream_text(messages, timeout=timeout):
                yield ch

    backend = _SpyBackend()
    runner = TurnRunner(backend=backend, bundle=bundle, loader_mode="on-demand")
    session = SessionState(session_id=str(uuid.uuid4()), domain="mbti")

    async for _ in runner.stream_turn(session, "嗨"):
        pass

    assert seen_pass2_tools == [], (
        f"Pass 2 system prompt 不应含 Skill Index 或 read_skill 契约文字，"
        f"实际泄漏: {seen_pass2_tools}"
    )
