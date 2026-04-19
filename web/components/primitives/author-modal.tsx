"use client";

import { useCallback, useEffect, useState } from "react";

export const WECHAT_ID = "niuniu-869";

interface Props {
  open: boolean;
  onClose: () => void;
}

/**
 * AuthorModal · 作者微信对话框，受控 open。
 *
 * 被 AuthorBadge（全局右下角）和 IssueChrome（报告页 chrome）两处共用。
 */
export function AuthorModal({ open, onClose }: Props) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(WECHAT_ID);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* 静默：部分浏览器禁剪贴板，长按 select 也可复制 */
    }
  }, []);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="作者"
      className="fixed inset-0 z-[55] flex items-center justify-center px-4 sm:px-6"
    >
      <button
        type="button"
        aria-label="关闭"
        onClick={onClose}
        className="absolute inset-0 bg-ink/30 backdrop-blur-[2px] cursor-default border-0"
      />

      <div className="relative w-full max-w-[420px] bg-paper border border-rule rounded-[2px] shadow-[0_24px_60px_rgba(28,24,20,0.18)] animate-rise">
        <header className="px-6 sm:px-7 pt-6 pb-4 flex items-baseline justify-between border-b border-rule">
          <h2
            className="text-ink"
            style={{
              fontFamily: "var(--font-serif)",
              fontStyle: "italic",
              fontWeight: 400,
              fontVariationSettings: '"opsz" 72, "SOFT" 50, "WONK" 0',
              fontSize: "24px",
              lineHeight: 1.1,
            }}
          >
            作者
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="font-mono text-[10px] tracking-widest uppercase text-ink-muted hover:text-accent transition-colors bg-transparent border-0"
            aria-label="关闭"
          >
            ESC
          </button>
        </header>

        <div className="px-6 sm:px-7 py-7">
          <p className="font-mono text-[10px] tracking-widest uppercase text-ink-muted mb-3">
            想聊聊 · 加微信
          </p>
          <div className="flex items-baseline gap-3 flex-wrap">
            <span
              className="font-mono text-accent select-all break-all"
              style={{ fontSize: "22px", letterSpacing: "0.02em" }}
            >
              {WECHAT_ID}
            </span>
            <button
              type="button"
              onClick={handleCopy}
              className="font-mono text-[11px] tracking-widest uppercase text-ink-muted hover:text-accent transition-colors bg-transparent border-0"
              aria-label="复制微信号"
            >
              {copied ? "已抄下 ✓" : "复制 ⎘"}
            </button>
          </div>

          <p className="fraunces-body-soft italic text-[14px] leading-[1.6] text-ink-soft mt-6">
            欢迎反馈、合作、吐槽。也欢迎只是打个招呼。
          </p>
        </div>
      </div>
    </div>
  );
}
