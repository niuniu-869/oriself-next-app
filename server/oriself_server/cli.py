"""
CLI runner · 本地终端里直接体验 skill。

用法:
    python -m oriself_server.cli --provider mock          # 离线，零密钥
    python -m oriself_server.cli --provider qwen          # 真实 LLM，需设置 ORISELF_QWEN_API_KEY

运行时键入 `:quit` 退出；键入 `:state` 查看当前 evidence 计数。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from typing import List

from .guardrails import OriSelfGuardrails, SessionState, Turn
from .llm_client import make_backend
from .schemas import Evidence
from .skill_loader import load_skill_bundle
from .skill_runner import SkillRunner


def _format_action_for_human(action_dict: dict) -> str:
    """把 LLM 返回的 Action JSON 渲染成人类可读的终端输出。"""
    lines = []
    action_type = action_dict.get("action", "?")
    header = {
        "ask": "ASK",
        "reflect": "REFLECT",
        "probe_contradiction": "PROBE",
        "redirect": "REDIRECT",
        "converge": "CONVERGE",
    }.get(action_type, action_type.upper())
    lines.append(f"\n[AI · {header}]")
    if action_type != "converge":
        np = action_dict.get("next_prompt", "").strip()
        lines.append(f"  {np}")
    else:
        co = action_dict.get("converge_output") or {}
        mbti = co.get("mbti_type", "????")
        lines.append(f"  MBTI · {mbti}")
        for i, p in enumerate(co.get("insight_paragraphs", []), start=1):
            lines.append(f"\n  [{i}] {p.get('theme', '')}")
            lines.append(f"      {p.get('body', '')}")
        card = co.get("card") or {}
        if card:
            lines.append(f"\n  Card title: {card.get('title', '')}")
            lines.append(f"  Subtitle:   {card.get('subtitle', '')}")
            pulls = card.get("pull_quotes", [])
            for pq in pulls:
                lines.append(f"  · \"{pq.get('text', '')}\" (R{pq.get('round', '?')})")
    # evidence 小提示
    ev = action_dict.get("evidence", [])
    if ev:
        lines.append(
            f"\n  (internal) this turn collected {len(ev)} evidence: "
            + ", ".join(f"{e.get('dimension')}:{e.get('user_quote','')[:10]}..." for e in ev)
        )
    return "\n".join(lines)


async def run_cli(provider: str, domain: str) -> int:
    bundle = load_skill_bundle()
    if not bundle.skill_md:
        print("[!] SKILL.md 没找到，检查 skills/oriself/ 目录。", file=sys.stderr)
        return 2
    guardrails = OriSelfGuardrails(bundle)
    try:
        backend = make_backend(provider)
    except RuntimeError as exc:
        print(f"[!] backend 初始化失败: {exc}", file=sys.stderr)
        return 2
    runner = SkillRunner(backend=backend, bundle=bundle, guardrails=guardrails)

    session = SessionState(
        session_id=str(uuid.uuid4())[:8],
        domain=domain,
        turns=[],
        collected_evidence=[],
    )
    print("=" * 60)
    print(f"OriSelf Next · CLI (provider={provider}, domain={domain})")
    print(f"Session ID: {session.session_id}")
    print("输入 `:quit` 退出, `:state` 查看 evidence 计数")
    print("=" * 60)

    # 第 0 轮 AI 开场（不消耗用户输入；我们调一次 step 给开场 ask）
    opening_fake_user = "我想测一下 MBTI，开始吧。"
    print("\n[User · R1] 我想测一下 MBTI，开始吧。")
    result = await runner.step(session, opening_fake_user)
    session = runner.advance_state(session, opening_fake_user, result)
    print(_format_action_for_human(result.action.model_dump()))
    if result.used_fallback:
        print(f"  (fallback used · reasons: {result.guardrail_reasons})")

    while True:
        if result.action.action == "converge":
            print("\n--- 会话已收敛，结束 ---")
            break
        try:
            user_in = input(f"\n[User · R{session.round_count + 1}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[!] 用户中断")
            return 0
        if not user_in:
            continue
        if user_in == ":quit":
            return 0
        if user_in == ":state":
            counts = {"E/I": 0, "S/N": 0, "T/F": 0, "J/P": 0}
            for ev in session.collected_evidence:
                if ev.dimension in counts:
                    counts[ev.dimension] += 1
            print(f"  rounds={session.round_count}, evidence per dim: {counts}")
            continue

        result = await runner.step(session, user_in)
        session = runner.advance_state(session, user_in, result)
        print(_format_action_for_human(result.action.model_dump()))
        if result.used_fallback:
            print(f"  (fallback used · reasons: {result.guardrail_reasons})")

        if session.round_count >= 30:
            print("\n--- 达到 MAX_ROUNDS=30，强制结束 ---")
            break

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="oriself_server.cli")
    parser.add_argument(
        "--provider",
        default="mock",
        choices=["mock", "qwen", "deepseek", "kimi", "openai"],
        help="LLM provider（mock 不需要密钥）",
    )
    parser.add_argument("--domain", default="mbti")
    args = parser.parse_args()
    return asyncio.run(run_cli(args.provider, args.domain))


if __name__ == "__main__":
    sys.exit(main())
