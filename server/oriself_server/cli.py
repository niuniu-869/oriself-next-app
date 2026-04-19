"""
CLI runner · v2.4 · 本地终端里直接体验 skill。

用法：
    python -m oriself_server.cli --provider mock          # 离线，零密钥
    python -m oriself_server.cli --provider qwen          # 真实 LLM

运行时键入：
    `:quit`    退出
    `:rewrite` 重写最近一轮（上一条不满意时）
    `:state`   查看轮数 / 最近 status
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from typing import Optional

from .llm_client import make_backend
from .skill_loader import load_skill_bundle
from .skill_runner import (
    ReportRunner,
    SessionState,
    Turn,
    TurnRunner,
    advance_state,
)


async def _stream_one_turn(
    runner: TurnRunner,
    state: SessionState,
    user_message: str,
    *,
    rewrite_hint: Optional[str] = None,
) -> tuple[str, str]:
    """跑一轮对话流，同时把 token 打到 stdout。返回 (visible_text, status)。"""
    print(f"\n[OriSelf · R{state.round_count + 1}]\n", end="", flush=True)
    visible = ""
    status = "CONTINUE"
    async for kind, payload in runner.stream_turn(
        state, user_message, rewrite_hint=rewrite_hint
    ):
        if kind == "token":
            print(payload, end="", flush=True)
        elif kind == "visible":
            visible = payload
        elif kind == "status":
            status = payload
        elif kind == "error":
            print(f"\n[!] 流错误：{payload}")
            return "", "NEED_USER"
    print(f"\n(STATUS: {status})")
    return visible, status


async def run_cli(provider: str, domain: str) -> int:
    bundle = load_skill_bundle()
    if not bundle.skill_md:
        print("[!] SKILL.md 没找到，检查 skill-repo/skills/oriself/ 目录。", file=sys.stderr)
        return 2
    try:
        backend = make_backend(provider)
    except RuntimeError as exc:
        print(f"[!] backend 初始化失败: {exc}", file=sys.stderr)
        return 2

    turn_runner = TurnRunner(backend=backend, bundle=bundle)
    report_runner = ReportRunner(backend=backend, bundle=bundle)

    session = SessionState(
        session_id=str(uuid.uuid4()),
        domain=domain,
    )
    print("=" * 60)
    print(f"OriSelf v2.4 · CLI (provider={provider}, domain={domain})")
    print(f"Session ID: {session.session_id[:8]}")
    print("  `:quit`    退出")
    print("  `:rewrite` 重写最近一轮")
    print("  `:state`   查看状态")
    print("=" * 60)

    # R1 用户开场
    opening = "嗨"
    print(f"\n[User · R1] {opening}")
    visible, status = await _stream_one_turn(turn_runner, session, opening)
    session = advance_state(session, opening, visible, status)

    while True:
        if status == "CONVERGE":
            print("\n--- LLM 声明 CONVERGE，开始生成报告 ---")
            result = await report_runner.compose(session)
            if result.output is None:
                print(f"[!] 报告生成失败（{result.retries} 次重试）")
                print("reasons:", result.error_reasons[:3])
                return 1
            co = result.output
            print(f"\nMBTI · {co.mbti_type}")
            if co.card_title:
                print(f"Title: {co.card_title}")
            print(f"(report_html: {len(co.report_html)} chars)")
            return 0

        if session.round_count >= 30:
            print("\n--- 达到 MAX_ROUNDS=30，强制进入收束 ---")
            status = "CONVERGE"
            continue

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
            last = session.live_turns()[-1] if session.live_turns() else None
            print(
                f"  rounds={session.round_count}, last_status="
                f"{last.status if last else 'n/a'}"
            )
            continue
        if user_in == ":rewrite":
            # 把最近一轮 mark discarded，用上一次的 user_message 重跑
            live = session.live_turns()
            if not live:
                print("  无可重写的轮")
                continue
            last = live[-1]
            # 在 in-memory session 里手动标 discarded
            for t in session.turns:
                if not t.discarded and t.round_number == last.round_number:
                    t.discarded = True
                    break
            visible, status = await _stream_one_turn(
                turn_runner,
                session,
                last.user_message,
                rewrite_hint="上一次的回答不对，换个说法",
            )
            session = advance_state(session, last.user_message, visible, status)
            continue

        visible, status = await _stream_one_turn(turn_runner, session, user_in)
        session = advance_state(session, user_in, visible, status)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="oriself_server.cli")
    parser.add_argument(
        "--provider",
        default="mock",
        choices=["mock", "qwen", "deepseek", "kimi", "openai", "gemini"],
        help="LLM provider（mock 不需要密钥）",
    )
    parser.add_argument("--domain", default="mbti")
    args = parser.parse_args()
    return asyncio.run(run_cli(args.provider, args.domain))


if __name__ == "__main__":
    sys.exit(main())
