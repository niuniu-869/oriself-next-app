"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Masthead } from "@/components/masthead";
import { Composer } from "@/components/letter/composer";
import { Turn } from "@/components/letter/turn";
import { sendTurn, getResult } from "@/lib/api";
import type { LetterState, TurnRecord } from "@/lib/types";

interface Props {
  letterId: string;
  initialState: LetterState;
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
export function LetterView({ letterId, initialState }: Props) {
  const router = useRouter();
  const [turns, setTurns] = useState<TurnRecord[]>(initialState.turns ?? []);
  const [isThinking, setIsThinking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to the freshest turn
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length, isThinking]);

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
      setError(null);

      try {
        const res = await sendTurn(letterId, text.trim());
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

        {isThinking && (
          <div className="mb-14 opacity-70">
            <p className="fraunces-body text-[20px] leading-[1.62] text-ink">
              <span className="inline-block">
                正在听
                <span className="writing-cursor" />
              </span>
            </p>
          </div>
        )}

        {error && (
          <p className="font-mono text-[11px] tracking-wide uppercase text-accent mt-10">
            {error}
          </p>
        )}

        <div ref={endRef} />
      </main>

      <Composer onSend={handleSend} disabled={isThinking} draftKey={letterId} />
    </>
  );
}
