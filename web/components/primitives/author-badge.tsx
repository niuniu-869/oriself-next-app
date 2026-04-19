"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import { AuthorModal } from "./author-modal";

/**
 * AuthorBadge · 右下角常驻 "AUTHOR" 入口 + 作者微信弹窗。
 *
 * - 极小极低调，mono + tracking widest，像画册版权页那样远远挂着。
 * - 点击弹 AuthorModal。IssueChrome 在报告页已内置另一个入口共享同一 modal。
 * - issue 页 chrome 已占据底部核心区，这里避开不渲染按钮，避免撞 chrome 的按钮。
 */
export function AuthorBadge() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  // 只在首页挂右下角 badge。
  //   - letter 页：composer 右下会撞"发送"按钮，改由 Masthead 右侧入口承载。
  //   - issue 页：chrome 左组已内置 AUTHOR 入口。
  const onLanding = pathname === "/";
  if (!onLanding) return null;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="关于作者"
        className="fixed bottom-[14px] right-[14px] z-[50] font-mono text-[10px] tracking-widest uppercase text-ink-muted hover:text-accent bg-transparent border-0 p-2 transition-colors"
      >
        AUTHOR
      </button>

      <AuthorModal open={open} onClose={() => setOpen(false)} />
    </>
  );
}
