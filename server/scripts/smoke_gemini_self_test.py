"""
Gemini 3.1 Pro 自测自 · 端到端 smoke。

跑通真实业务逻辑（不 bypass server）：
- 起 subprocess uvicorn :8765（避开 8000）
- ORISELF_PROVIDER=gemini · ORISELF_GEMINI_MODEL=gemini-3.1-pro-preview
- ORISELF_SKILL_LOADING=on-demand（v2.6 默认）
- 临时 SQLite 文件，跑完删

对话循环：
- R1：脚本固定一句中性开场（"嗨"）作为用户首发
- R2..R30：oriself 上一轮 visible → 喂给被试者 Gemini（**无任何 system prompt**），
  让它续上一轮 assistant 角色作为这轮的 user_message
- 收 SSE done frame 拿 status；status==CONVERGE 且 round>=6 → 触发报告
- R==30 硬上限 → 同样触发报告

输出：每轮一行进度（R{N} | phase | bytes | status | violations | first40chars），
末尾打印 issue_slug + html bytes + mbti_type。

用法：
    cd server
    python3 scripts/smoke_gemini_self_test.py
    # 调整：
    #   --port 8765
    #   --max-rounds 30
    #   --oriself-model gemini-3.1-pro-preview
    #   --subject-model gemini-3.1-pro-preview
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

import httpx


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = REPO_ROOT / "server"


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def _spawn_server(
    *,
    port: int,
    db_path: Path,
    oriself_model: str,
    log_file: Path,
) -> subprocess.Popen:
    env = os.environ.copy()
    env["ORISELF_PROVIDER"] = "gemini"
    env["ORISELF_GEMINI_MODEL"] = oriself_model
    env["ORISELF_DB_PATH"] = str(db_path)
    env["ORISELF_SKILL_LOADING"] = env.get("ORISELF_SKILL_LOADING", "on-demand")
    env.setdefault("PYTHONUNBUFFERED", "1")

    log_fh = open(log_file, "w", buffering=1)
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "oriself_server.main:app",
            "--host", "127.0.0.1", "--port", str(port),
            "--log-level", "info",
        ],
        cwd=str(SERVER_DIR),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    proc._log_fh = log_fh  # type: ignore[attr-defined]
    return proc


async def _wait_health(base_url: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    last_err: Optional[Exception] = None
    async with httpx.AsyncClient(timeout=2.0) as c:
        while time.time() < deadline:
            try:
                r = await c.get(f"{base_url}/health")
                if r.status_code == 200:
                    return
            except Exception as exc:
                last_err = exc
            await asyncio.sleep(0.4)
    raise RuntimeError(f"server /health not ready in {timeout}s: {last_err}")


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
    finally:
        try:
            proc._log_fh.close()  # type: ignore[attr-defined]
        except Exception:
            pass


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


async def _stream_turn(
    base_url: str, letter_id: str, user_message: str, *, timeout: float = 180.0,
) -> Tuple[str, str, float, float, float]:
    """POST /letters/{id}/turn 并消费 SSE。

    返回 (visible, status, ttft, ttfb, total_elapsed):
    - ttft: POST 发起 → 第一个 `event: token` data 到达的秒数
            （含 Pass 1 工具调用 + Pass 2 首 token；这就是用户感知到的"等多久看到字"）
    - ttfb: POST 发起 → SSE 流第一行字节到达的秒数（含 quill 预热帧）
    - total_elapsed: 整轮（含 done frame）总耗时
    """
    url = f"{base_url}/letters/{letter_id}/turn"
    body = {"user_message": user_message}

    visible = ""
    status = "CONTINUE"
    error_msg: Optional[str] = None
    t0 = time.time()
    ttfb: Optional[float] = None
    ttft: Optional[float] = None

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, read=timeout)) as c:
        async with c.stream("POST", url, json=body) as resp:
            if resp.status_code != 200:
                txt = (await resp.aread()).decode("utf-8", "replace")
                raise RuntimeError(f"turn HTTP {resp.status_code}: {txt[:400]}")

            current_event = ""
            async for line in resp.aiter_lines():
                if ttfb is None:
                    ttfb = time.time() - t0
                if not line:
                    current_event = ""
                    continue
                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                    continue
                if line.startswith("data:"):
                    raw = line.split(":", 1)[1].strip()
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        continue
                    if current_event == "token" and ttft is None:
                        ttft = time.time() - t0
                    elif current_event == "done":
                        visible = payload.get("visible", visible)
                        status = payload.get("status", status)
                    elif current_event == "error":
                        error_msg = payload.get("message", "stream error")
                    # quill 不计入 TTFT（它是 token 之前的预热帧）

    total = time.time() - t0
    if error_msg and not visible:
        raise RuntimeError(f"server SSE error: {error_msg}")
    return visible, status, (ttft or total), (ttfb or total), total


# ---------------------------------------------------------------------------
# Subject (Gemini, no system prompt)
# ---------------------------------------------------------------------------


async def _subject_reply(
    *,
    base_url: str,
    api_key: str,
    model: str,
    history: List[Tuple[str, str]],
    timeout: float = 60.0,
) -> str:
    """裸调被试者：history 是 [(role, content), ...]，role ∈ {oriself, subject}。

    转换为 Gemini messages：oriself→user / subject→assistant，**不带任何 system**。
    让 LLM continue 下一条 assistant message——也就是被试者的下一句。
    """
    messages = []
    for role, content in history:
        if not content:
            continue
        if role == "oriself":
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "assistant", "content": content})

    if not messages:
        return "嗨"

    payload = {"model": model, "messages": messages, "stream": False}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"subject HTTP {r.status_code}: {r.text[:400]}")
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"subject empty choices: {data}")
        msg = choices[0].get("message") or {}
        content = (msg.get("content") or "").strip()
        if not content:
            # 某些代理把 think 内容塞 reasoning_content；fallback 一下
            content = (msg.get("reasoning_content") or "").strip()
        if not content:
            raise RuntimeError(f"subject no content: {msg!r}")
        return content[:1500]


# ---------------------------------------------------------------------------
# DB peek: read pass1 trace per round (skill_loader_mode / loaded skills / violations)
# ---------------------------------------------------------------------------


def _peek_pass1(db_path: Path, letter_id: str, round_number: int) -> dict:
    import sqlite3
    out: dict = {}
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.execute(
            """
            SELECT skill_loader_mode, loaded_skill_names, pass1_violations_json,
                   chosen_phase_key
            FROM conversations
            WHERE session_id=? AND round_number=? AND discarded=0
            ORDER BY id DESC LIMIT 1
            """,
            (letter_id, round_number),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return out
        out["loader_mode"] = row[0]
        out["loaded"] = json.loads(row[1]) if row[1] else []
        out["violations"] = json.loads(row[2]) if row[2] else []
        out["phase"] = row[3]
    except Exception as exc:
        out["_db_err"] = str(exc)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _short(s: str, n: int = 50) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


async def main_async(args: argparse.Namespace) -> int:
    base_url_proxy = (
        os.environ.get("ORISELF_GEMINI_BASE_URL")
        or os.environ.get("GEMINI_BASE_URL")
    )
    api_key = (
        os.environ.get("ORISELF_GEMINI_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
    )
    if not base_url_proxy or not api_key:
        print("[FAIL] need GEMINI_BASE_URL + GEMINI_API_KEY in env (.env auto-loaded by server)")
        return 2

    tmp_dir = Path(tempfile.mkdtemp(prefix="oriself_smoke_"))
    db_path = tmp_dir / "smoke.db"
    server_log = tmp_dir / "server.log"

    print(f"[smoke] tmp dir: {tmp_dir}")
    print(f"[smoke] oriself model: {args.oriself_model}")
    print(f"[smoke] subject model: {args.subject_model}")
    print(f"[smoke] proxy: {base_url_proxy}")

    proc = _spawn_server(
        port=args.port,
        db_path=db_path,
        oriself_model=args.oriself_model,
        log_file=server_log,
    )
    base = f"http://127.0.0.1:{args.port}"
    rc = 1
    try:
        try:
            await _wait_health(base, timeout=25.0)
        except Exception as exc:
            print(f"[FAIL] server didn't come up: {exc}")
            print(f"[FAIL] server log: {server_log}")
            try:
                print(server_log.read_text()[-2000:])
            except Exception:
                pass
            return 3

        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(f"{base}/letters", json={"provider": "gemini", "domain": "mbti"})
            r.raise_for_status()
            letter = r.json()
        letter_id = letter["letter_id"]
        print(f"[smoke] letter created: {letter_id}  skill={letter['skill_version']}")

        history: List[Tuple[str, str]] = []
        user_msg = "嗨"
        last_status = "CONTINUE"
        last_round = 0
        ttft_records: List[Tuple[int, float, float, float]] = []  # (rn, ttft, ttfb, total)

        for rn in range(1, args.max_rounds + 1):
            try:
                visible, status, ttft, ttfb, total = await _stream_turn(
                    base, letter_id, user_msg, timeout=240.0
                )
            except Exception as exc:
                print(f"[FAIL] R{rn} stream: {exc}")
                return 4

            history.append(("subject", user_msg))
            history.append(("oriself", visible))
            last_status = status
            last_round = rn
            ttft_records.append((rn, ttft, ttfb, total))

            trace = _peek_pass1(db_path, letter_id, rn)
            v_kinds = [v.get("kind") for v in trace.get("violations", [])] if trace else []
            print(
                f"R{rn:02d} | ttft={ttft:5.1f}s ttfb={ttfb:5.1f}s tot={total:5.1f}s"
                f" | mode={trace.get('loader_mode','?')}"
                f" | phase={trace.get('phase') or '-':<20s}"
                f" | new={len(trace.get('loaded') or [])}"
                f" | viol={','.join(v_kinds) or '-':<26s}"
                f" | st={status:<9s}"
                f" | subj→ {_short(user_msg, 28)}"
                f" | ori→ {_short(visible, 50)}"
            )

            if status == "CONVERGE" and rn >= 6:
                print(f"[smoke] CONVERGE declared at R{rn}, going to /result")
                break
            if rn == args.max_rounds:
                print(f"[smoke] hit max-rounds R{rn}, going to /result")
                break

            try:
                user_msg = await _subject_reply(
                    base_url=base_url_proxy,
                    api_key=api_key,
                    model=args.subject_model,
                    history=history,
                    timeout=90.0,
                )
            except Exception as exc:
                print(f"[FAIL] R{rn+1} subject reply: {exc}")
                return 5

        if last_round < 6:
            print(f"[FAIL] only got {last_round} rounds, can't trigger /result (<6)")
            return 6

        print(f"[smoke] POST /letters/{letter_id}/result")
        async with httpx.AsyncClient(timeout=180.0) as c:
            r = await c.post(f"{base}/letters/{letter_id}/result")
            if r.status_code != 200:
                print(f"[FAIL] /result HTTP {r.status_code}: {r.text[:600]}")
                return 7
            result = r.json()

        slug = result.get("issue_slug") or "?"
        mbti = result.get("mbti_type") or "?"
        card_title = result.get("card_title") or "?"

        async with httpx.AsyncClient(timeout=20.0) as c:
            r_meta = await c.get(f"{base}/issues/{slug}")
            issue_title = "?"
            if r_meta.status_code == 200:
                issue_title = (r_meta.json() or {}).get("title") or "?"
            r_html = await c.get(f"{base}/issues/{slug}/render")
            if r_html.status_code != 200:
                print(f"[FAIL] /issues/{slug}/render HTTP {r_html.status_code}: {r_html.text[:300]}")
                return 8
            html_bytes = len(r_html.content)
            csp = r_html.headers.get("content-security-policy", "")

        print()
        print(f"[smoke OK] mbti={mbti}")
        print(f"[smoke OK] issue_slug={slug}  title={issue_title}  card={card_title}")
        print(f"[smoke OK] /render html_bytes={html_bytes}  csp_set={bool(csp)}  rounds={last_round}")

        # TTFT 汇总（用户最关心：每轮等多久看到第一个字）
        if ttft_records:
            ttfts = [r[1] for r in ttft_records]
            ttfbs = [r[2] for r in ttft_records]
            print()
            print(f"[ttft] median={sorted(ttfts)[len(ttfts)//2]:.1f}s"
                  f"  min={min(ttfts):.1f}s  max={max(ttfts):.1f}s"
                  f"  ttfb_median={sorted(ttfbs)[len(ttfbs)//2]:.1f}s")
            print("[ttft] per-round (含 Pass 1 工具调用 + Pass 2 首 token)：")
            for rn, ttft, ttfb, total in ttft_records:
                p2_stream = total - ttft  # Pass 2 流式持续时间
                print(f"  R{rn:02d}: ttft={ttft:5.1f}s  ttfb={ttfb:5.1f}s"
                      f"  pass2_stream={p2_stream:5.1f}s  total={total:5.1f}s")

        rc = 0
    finally:
        _terminate(proc)
        print(f"[smoke] server log: {server_log}")
        # 不删 tmp_dir，留作复盘
        print(f"[smoke] db: {db_path}  (留作复盘)")

    return rc


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Gemini 3.1 Pro 自测自 e2e smoke")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--max-rounds", type=int, default=30)
    p.add_argument("--oriself-model", default="gemini-3.1-pro-preview")
    p.add_argument("--subject-model", default="gemini-3.1-pro-preview")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    sys.exit(asyncio.run(main_async(args)))
