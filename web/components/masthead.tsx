import Link from "next/link";
import type { ReactNode } from "react";

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
      // z-30 > main (z-10) > composer (z-20) ——
      // 否则移动端 main 铺满视口时会吞掉顶栏右上角 AUTHOR 按钮的点击
      className="fixed top-0 left-0 right-0 z-30 px-5 sm:px-10 py-[18px] sm:py-[22px] flex items-baseline justify-between gap-3 pointer-events-none"
      style={{
        // 实心纸色打底 + 顶部一条更浓的起笔 + 下沿柔渐变 —— 滚动时正文再不会
        // 穿透过来把 ROUND 徽标吃成灰雾。
        background:
          "linear-gradient(to bottom, rgba(245, 240, 230, 0.96) 0%, rgba(245, 240, 230, 0.92) 70%, rgba(245, 240, 230, 0))",
        backdropFilter: "blur(6px)",
        WebkitBackdropFilter: "blur(6px)",
      }}
    >
      <Link
        href="/"
        className="fraunces-brand text-[18px] tracking-tighter text-ink pointer-events-auto"
        style={{ textDecoration: "none" }}
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
