'use client';

import { memo } from 'react';
import type { TurnRecord } from '@/lib/types';

interface Props {
  turn: TurnRecord;
  /** If true, reveal line-by-line (for the freshest OriSelf response). */
  reveal?: boolean;
}

/**
 * A single turn in the letter.
 *
 * OriSelf speaks first-class: serif body, no bubble.
 * You speaks indented with an em-dash drop cap.
 * No avatars, no timestamps, no "…said:" — the typography IS the distinction.
 */
export const Turn = memo(function Turn({ turn, reveal = false }: Props) {
  if (turn.speaker === 'oriself') {
    return (
      <article className="mb-14 animate-settle">
        <p className="fraunces-body text-[20px] leading-[1.62] tracking-tight text-ink">
          {reveal ? <RevealText text={turn.text} /> : turn.text}
        </p>
      </article>
    );
  }

  // You
  return (
    <article className="mb-14 pl-9 relative animate-settle">
      <span
        aria-hidden
        className="absolute left-0 top-[6px] font-serif text-[26px] leading-none text-accent"
        style={{ fontVariationSettings: '"opsz" 36, "SOFT" 0, "WONK" 0', fontWeight: 300 }}
      >
        —
      </span>
      <p className="fraunces-body-soft text-[19px] leading-[1.65] text-ink-soft">
        {turn.text}
      </p>
    </article>
  );
});

/**
 * RevealText · splits into lines and stagger-reveals each.
 *
 * Pure CSS approach: wrap each chunk in a span with increasing animation-delay.
 * Splits on Chinese sentence endings (。？！……——) and commas for natural
 * breath pauses.
 */
function RevealText({ text }: { text: string }) {
  // Split into clauses at punctuation + keep the punctuation with the clause
  const segments = text.split(/(?<=[。？！；……——])/).filter(Boolean);

  return (
    <>
      {segments.map((seg, i) => (
        <span
          key={i}
          className="inline-block opacity-0 animate-rise"
          style={{
            animationDelay: `${i * 0.35}s`,
            animationFillMode: 'forwards',
          }}
        >
          {seg}
        </span>
      ))}
      <span
        className="writing-cursor"
        style={{ animationDelay: `${segments.length * 0.35}s` }}
      />
    </>
  );
}
