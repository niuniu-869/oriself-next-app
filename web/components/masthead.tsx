import Link from 'next/link';
import type { ReactNode } from 'react';

/**
 * Masthead · the whisper-loud header shown on letter and issue pages.
 *
 * It fades into the paper below using a gradient mask so the text feels like
 * it sits on the page, not in a toolbar.
 */
export function Masthead({
  meta,
  actions,
}: {
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header
      className="fixed top-0 left-0 right-0 z-10 px-10 py-[22px] flex items-baseline justify-between pointer-events-none"
      style={{
        background:
          'linear-gradient(to bottom, var(--paper) 60%, rgba(245, 240, 230, 0))',
      }}
    >
      <Link
        href="/"
        className="fraunces-brand text-[18px] tracking-tighter text-ink pointer-events-auto"
        style={{ textDecoration: 'none' }}
      >
        OriSelf
      </Link>

      {meta && (
        <div className="font-mono text-[10px] tracking-widest uppercase text-ink-muted pointer-events-auto">
          {meta}
        </div>
      )}

      {actions && (
        <div className="flex gap-[22px] pointer-events-auto">{actions}</div>
      )}
    </header>
  );
}
