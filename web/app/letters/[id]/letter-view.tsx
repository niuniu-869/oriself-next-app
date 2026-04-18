"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Masthead } from "@/components/masthead";
import { Composer } from "@/components/letter/composer";
import { Turn } from "@/components/letter/turn";
import {
  composeResult,
  rewriteLastTurn,
  sendTurnStream,
} from "@/lib/api";
import type { LetterState, TurnRecord, TurnStatus } from "@/lib/types";

interface Props {
  letterId: string;
  initialState: LetterState;
  /** 已 converge 的 letter — 渲染"看报告"入口替换 composer。 */
  issueSlug?: string | null;
  /** 回看时注入的完整历史轮。 */
  initialTurns: TurnRecord[];
}

/**
 * Letter view · v2.4 的对话界面。
 *
 * 不变式：
 *  - 无气泡、无头像、无时间戳。
 *  - OriSelf 的新回复按 token 流逐字出现。
 *  - Composer 是一条线，不是一个框。
 *  - 最近一个 oriself 轮下方显示「让 TA 重写」按钮。
 *  - LLM 在流末尾声明 STATUS: CONVERGE → 服务端剥除；前端收到 done.status=CONVERGE
 *    自动触发报告生成并跳 /issues/:slug。
 */
export function LetterView({ letterId, initialState, issueSlug, initialTurns }: Props) {
  const router = useRouter();
  const isCompleted = initialState.status === "completed";

  const [turns, setTurns] = useState<TurnRecord[]>(initialTurns);
  const [isStreaming, setIsStreaming] = useState(false);
  const [lastStatus, setLastStatus] = useState<TurnStatus | null>(
    initialState.last_status ?? null,
  );
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  // 自动滚到最新
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, isStreaming]);

  // ============================================================
  // 流式辅助
  // ============================================================

  const openOriselfStreamingTurn = useCallback((round: number) => {
    setTurns((prev) => [...prev, { speaker: "oriself", text: "", round }]);
  }, []);

  const appendOriselfToken = useCallback((delta: string) => {
    setTurns((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      if (last.speaker !== "oriself") return prev;
      const updated = { ...last, text: last.text + delta };
      return [...prev.slice(0, -1), updated];
    });
  }, []);

  const finalizeOriselfTurn = useCallback((visible: string, round: number) => {
    setTurns((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      if (last.speaker !== "oriself") return prev;
      return [...prev.slice(0, -1), { speaker: "oriself", text: visible, round }];
    });
  }, []);

  const handleConverge = useCallback(async () => {
    try {
      const result = await composeResult(letterId);
      if (result.issue_slug) {
        router.push(`/issues/${result.issue_slug}`);
      } else {
        setError("报告生成成功但没有 issue slug，请刷新页面");
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "报告生成卡住了，稍后再试",
      );
    }
  }, [letterId, router]);

  // ============================================================
  // 发送一轮
  // ============================================================

  const handleSend = useCallback(
    async (text: string) => {
      if (!text.trim() || isStreaming) return;

      const nextRound =
        (turns.filter((t) => t.speaker === "you").slice(-1)[0]?.round ?? 0) + 1;
      setTurns((prev) => [
        ...prev,
        { speaker: "you", text: text.trim(), round: nextRound },
      ]);

      setIsStreaming(true);
      setError(null);
      openOriselfStreamingTurn(nextRound);

      try {
        const done = await sendTurnStream(letterId, text.trim(), {
          onToken: appendOriselfToken,
        });
        finalizeOriselfTurn(done.visible, done.round);
        setLastStatus(done.status);
        if (done.status === "CONVERGE") {
          await handleConverge();
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "发送失败，稍后再试");
      } finally {
        setIsStreaming(false);
      }
    },
    [
      letterId,
      turns,
      isStreaming,
      openOriselfStreamingTurn,
      appendOriselfToken,
      finalizeOriselfTurn,
      handleConverge,
    ],
  );

  // ============================================================
  // 重写最近一轮
  // ============================================================

  const handleRewrite = useCallback(async () => {
    if (isStreaming) return;
    const lastOri = [...turns].reverse().find((t) => t.speaker === "oriself");
    if (!lastOri) return;

    setTurns((prev) => {
      for (let i = prev.length - 1; i >= 0; i--) {
        if (prev[i].speaker === "oriself") {
          return prev.slice(0, i);
        }
      }
      return prev;
    });

    setIsStreaming(true);
    setError(null);
    openOriselfStreamingTurn(lastOri.round);

    try {
      const done = await rewriteLastTurn(letterId, {
        onToken: appendOriselfToken,
      });
      finalizeOriselfTurn(done.visible, done.round);
      setLastStatus(done.status);
      if (done.status === "CONVERGE") {
        await handleConverge();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "重写失败，稍后再试");
    } finally {
      setIsStreaming(false);
    }
  }, [
    letterId,
    isStreaming,
    turns,
    openOriselfStreamingTurn,
    appendOriselfToken,
    finalizeOriselfTurn,
    handleConverge,
  ]);

  // ============================================================
  // Render
  // ============================================================

  const currentRound = Math.max(...turns.map((t) => t.round), 0);

  const lastOriselfIdx = (() => {
    for (let i = turns.length - 1; i >= 0; i--) {
      if (turns[i].speaker === "oriself") return i;
    }
    return -1;
  })();

  return (
    <>
      <Masthead
        meta={
          <>
            <span>letter</span>
            <span className="mx-[10px] opacity-50">·</span>
            <span className="text-accent">
              round {String(currentRound).padStart(2, "0")}
            </span>
          </>
        }
      />

      <main className="relative z-10 max-w-[620px] mx-auto px-8 pt-[140px] pb-[260px]">
        {turns.length === 0 && (
          <div className="mb-14">
            <span
              className="text-accent"
              style={{
                fontFamily: "var(--font-serif)",
                fontVariationSettings: '"opsz" 144, "SOFT" 100, "WONK" 1',
                fontStyle: "italic",
                fontWeight: 400,
                fontSize: "clamp(72px, 12vw, 132px)",
                lineHeight: 1,
                letterSpacing: "-0.04em",
              }}
            >
              01.
            </span>
          </div>
        )}

        {turns.map((turn, i) => {
          const isLastOriself = i === lastOriselfIdx;
          const showStreaming = isLastOriself && isStreaming;
          return (
            <div key={`${turn.round}-${turn.speaker}-${i}`}>
              <Turn turn={turn} streaming={showStreaming} />
              {isLastOriself && !isStreaming && !isCompleted && (
                <div className="-mt-10 mb-14 pl-0">
                  <button
                    type="button"
                    onClick={handleRewrite}
                    className="font-mono text-[10px] tracking-widest uppercase text-ink-muted hover:text-accent transition-colors duration-300 bg-transparent border-0 cursor-pointer p-0"
                    disabled={isStreaming}
                    aria-label="让 OriSelf 重写这一轮"
                  >
                    让 TA 重写 <span className="not-italic">↻</span>
                  </button>
                </div>
              )}
            </div>
          );
        })}

        {lastStatus === "NEED_USER" && !isStreaming && (
          <p className="font-mono text-[11px] tracking-wide uppercase text-accent mt-4">
            TA 好像在等你多说一点 —— 聊到哪都行。
          </p>
        )}

        {error && (
          <p className="font-mono text-[11px] tracking-wide uppercase text-accent mt-10">
            {error}
          </p>
        )}

        <div ref={endRef} />
      </main>

      {isCompleted ? (
        <CompletedFooter issueSlug={issueSlug ?? null} />
      ) : (
        <Composer
          onSend={handleSend}
          disabled={isStreaming}
          draftKey={letterId}
        />
      )}
    </>
  );
}

function CompletedFooter({ issueSlug }: { issueSlug: string | null }) {
  return (
    <footer
      className="fixed left-0 right-0 bottom-0 z-[8] px-8 pt-20 pb-9 pointer-events-none"
      style={{
        background:
          "linear-gradient(to top, var(--paper) 55%, rgba(245, 240, 230, 0.92) 80%, rgba(245, 240, 230, 0))",
      }}
    >
      <div className="max-w-[620px] mx-auto pointer-events-auto flex items-center justify-between gap-6">
        <p className="font-mono text-[10px] tracking-widest uppercase text-ink-muted">
          这封信已收尾 · 在回看
        </p>
        {issueSlug ? (
          <Link
            href={`/issues/${issueSlug}`}
            className="fraunces-body italic text-[16px] text-accent hover:text-accent-soft border-b border-accent/40 hover:border-accent transition-colors pb-[2px]"
          >
            看你的报告 <span className="font-mono not-italic">→</span>
          </Link>
        ) : (
          <Link
            href="/letters/new"
            className="fraunces-body italic text-[16px] text-accent hover:text-accent-soft border-b border-accent/40 hover:border-accent transition-colors pb-[2px]"
          >
            再写一封 <span className="font-mono not-italic">→</span>
          </Link>
        )}
      </div>
    </footer>
  );
}
