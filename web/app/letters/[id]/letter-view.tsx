"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Masthead } from "@/components/masthead";
import { Composer } from "@/components/letter/composer";
import { Turn } from "@/components/letter/turn";
import { ThinkingTrail } from "@/components/letter/thinking-trail";
import { sendTurnStream, getResult } from "@/lib/api";
import type { TurnStreamPhaseEvent } from "@/lib/api";
import type { LetterState, TurnRecord } from "@/lib/types";

interface Props {
  letterId: string;
  initialState: LetterState;
  /** 已 converge 的 letter — 渲染"看报告"入口替换 composer。 */
  issueSlug?: string | null;
}

/**
 * Letter view — the conversation interface.
 *
 * Invariants:
 *  - No bubbles, no avatars, no timestamps.
 *  - OriSelf's new lines reveal line-by-line (handled by Turn component).
 *  - Composer is a line, not a box.
 *  - On converge, redirect to /issues/:slug.
 */
export function LetterView({ letterId, initialState, issueSlug }: Props) {
  const router = useRouter();
  const isCompleted = initialState.status === "completed";
  const [turns, setTurns] = useState<TurnRecord[]>(initialState.turns ?? []);
  const [isThinking, setIsThinking] = useState(false);
  const [phaseTrail, setPhaseTrail] = useState<TurnStreamPhaseEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to the freshest turn
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length, isThinking, phaseTrail.length]);

  const handleSend = useCallback(
    async (text: string) => {
      if (!text.trim() || isThinking) return;

      // Optimistic user turn
      const userTurn: TurnRecord = {
        speaker: "you",
        text: text.trim(),
        round: turns.length + 1,
      };
      setTurns((prev) => [...prev, userTurn]);
      setIsThinking(true);
      setPhaseTrail([]);
      setError(null);

      try {
        const res = await sendTurnStream(letterId, text.trim(), {
          onPhase: (evt) => {
            // 同一 phase 的连续事件压扁成一条，避免 "thinking" 被 retry 打成 N 条
            setPhaseTrail((prev) => {
              if (prev.length && prev[prev.length - 1].phase === evt.phase) {
                return [...prev.slice(0, -1), evt];
              }
              return [...prev, evt];
            });
          },
        });
        const { action } = res;

        // v2.3：可见文本统一在 next_prompt。converge 不带可见话术（直接跳报告）。
        // 兼容字段保留作 mock/旧 provider 的兜底。
        const visible =
          action.next_prompt?.trim() ||
          action.next_question?.trim() ||
          action.echo?.trim() ||
          action.text?.trim() ||
          (action.action === "converge" ? "信收束了，正在写报告……" : "……");

        const oriselfTurn: TurnRecord = {
          speaker: "oriself",
          text: visible,
          round: res.round_number,
        };

        setTurns((prev) => [...prev, oriselfTurn]);

        // Converge → redirect to issue page
        if (action.action === "converge") {
          const result = await getResult(letterId);
          if (result.issue_slug) {
            router.push(`/issues/${result.issue_slug}`);
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "发送失败，稍后再试");
      } finally {
        setIsThinking(false);
        setPhaseTrail([]);
      }
    },
    [letterId, turns.length, isThinking, router],
  );

  const currentRound = turns.length;

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
            {/* Just the round number. No headline, no copy. */}
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

        {turns.map((turn, i) => (
          <Turn
            key={`${turn.round}-${turn.speaker}-${i}`}
            turn={turn}
            // Last OriSelf turn reveals line-by-line; older turns settle in instantly
            reveal={turn.speaker === "oriself" && i === turns.length - 1}
          />
        ))}

        {isThinking && <ThinkingTrail trail={phaseTrail} />}

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
          disabled={isThinking}
          draftKey={letterId}
        />
      )}
    </>
  );
}

/**
 * 已收尾的信不再接受输入，给一条克制的回看提示 + 直达报告的入口。
 * 保持和 Composer 一致的底部 fixed 结构，避免视觉跳变。
 */
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
