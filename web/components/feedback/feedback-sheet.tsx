"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { submitFeedback } from "@/lib/api";

interface Props {
  open: boolean;
  onClose: () => void;
  letterId?: string;
  issueSlug?: string;
}

/**
 * Feedback sheet · 一个克制的弹层。
 *
 * 不打断对话美学：底部上滑、半透明压暗背景、ESC 收起。
 * 字段：评分（5 颗星，可不选）+ 文本（必填，2-2000 字）+ 联系方式（可选）。
 * 提交后短暂显示「收到了」状态再自动关闭，避免用户怀疑没生效。
 */
export function FeedbackSheet({ open, onClose, letterId, issueSlug }: Props) {
  const [text, setText] = useState("");
  const [rating, setRating] = useState<number | null>(null);
  const [contact, setContact] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  // ESC 收起 + 焦点管理
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    // 进入时给文本框焦点
    requestAnimationFrame(() => taRef.current?.focus());
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // 关闭时复位
  useEffect(() => {
    if (!open) {
      setDone(false);
      setError(null);
    }
  }, [open]);

  const handleSubmit = useCallback(async () => {
    const trimmed = text.trim();
    if (trimmed.length < 2) {
      setError("写一两句就好");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await submitFeedback({
        text: trimmed,
        rating: rating ?? undefined,
        contact: contact.trim() || undefined,
        letter_id: letterId,
        issue_slug: issueSlug,
      });
      setDone(true);
      setText("");
      setRating(null);
      setContact("");
      // 1.4s 后自动收起
      setTimeout(onClose, 1400);
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败，稍后再试");
    } finally {
      setSubmitting(false);
    }
  }, [text, rating, contact, letterId, issueSlug, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-end justify-center"
      role="dialog"
      aria-modal="true"
      aria-label="反馈"
    >
      {/* Scrim */}
      <button
        type="button"
        aria-label="关闭"
        onClick={onClose}
        className="absolute inset-0 bg-ink/30 backdrop-blur-[2px] cursor-default"
      />

      {/* Sheet */}
      <div
        className="relative w-full max-w-[560px] mx-4 mb-4 sm:mb-12 bg-paper border border-rule rounded-[2px] shadow-[0_24px_60px_rgba(28,24,20,0.18)] animate-rise"
        style={{ animationDelay: "0s", animationFillMode: "forwards" }}
      >
        <header className="px-8 pt-8 pb-4 flex items-baseline justify-between border-b border-rule">
          <h2
            className="text-ink"
            style={{
              fontFamily: "var(--font-serif)",
              fontStyle: "italic",
              fontWeight: 400,
              fontVariationSettings: '"opsz" 72, "SOFT" 50, "WONK" 0',
              fontSize: "28px",
              lineHeight: 1.1,
            }}
          >
            写一句话给我们
          </h2>
          <button
            onClick={onClose}
            className="font-mono text-[10px] tracking-widest uppercase text-ink-muted hover:text-accent transition-colors"
            aria-label="关闭"
          >
            ESC
          </button>
        </header>

        <div className="px-8 py-6">
          {done ? (
            <p
              className="fraunces-body text-[20px] text-accent text-center py-10 italic"
              role="status"
            >
              收到了，谢谢。
            </p>
          ) : (
            <>
              {/* Rating */}
              <div className="mb-6">
                <p className="font-mono text-[10px] tracking-widest uppercase text-ink-muted mb-2">
                  这次体验
                </p>
                <div className="flex gap-3">
                  {[1, 2, 3, 4, 5].map((n) => (
                    <button
                      key={n}
                      type="button"
                      onClick={() => setRating(rating === n ? null : n)}
                      aria-label={`评分 ${n} 分`}
                      aria-pressed={rating === n}
                      className={`text-[24px] leading-none transition-colors ${
                        rating !== null && n <= rating
                          ? "text-accent"
                          : "text-ink-muted/40 hover:text-ink-muted"
                      }`}
                    >
                      ●
                    </button>
                  ))}
                  <span className="font-mono text-[10px] tracking-wide uppercase text-ink-muted self-end ml-2">
                    {rating === null ? "可选" : `${rating} / 5`}
                  </span>
                </div>
              </div>

              {/* Text */}
              <div className="mb-5">
                <p className="font-mono text-[10px] tracking-widest uppercase text-ink-muted mb-2">
                  你想说的
                </p>
                <textarea
                  ref={taRef}
                  rows={4}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  maxLength={2000}
                  placeholder="哪里好、哪里不对、哪里可以更好……"
                  className="w-full bg-transparent fraunces-body-soft text-[18px] leading-[1.6] text-ink resize-y outline-none border-b border-rule-strong focus:border-accent transition-colors py-2 placeholder:italic placeholder:text-ink-muted placeholder:opacity-70"
                  style={{ caretColor: "var(--accent)" }}
                />
                <p className="font-mono text-[10px] tracking-wide uppercase text-ink-muted mt-1 text-right">
                  {text.length} / 2000
                </p>
              </div>

              {/* Contact */}
              <div className="mb-7">
                <p className="font-mono text-[10px] tracking-widest uppercase text-ink-muted mb-2">
                  联系方式 · 可选
                </p>
                <input
                  type="text"
                  value={contact}
                  onChange={(e) => setContact(e.target.value)}
                  maxLength={200}
                  placeholder="邮箱 / 微信 / 任意 — 仅用于回访"
                  className="w-full bg-transparent fraunces-body-soft text-[16px] text-ink outline-none border-b border-rule-strong focus:border-accent transition-colors py-2 placeholder:italic placeholder:text-ink-muted placeholder:opacity-70"
                  style={{ caretColor: "var(--accent)" }}
                />
              </div>

              {error && (
                <p className="font-mono text-[10px] tracking-wide uppercase text-accent mb-4">
                  {error}
                </p>
              )}

              <div className="flex justify-between items-center pt-2">
                <span className="font-mono text-[10px] tracking-widest uppercase text-ink-muted">
                  匿名 · 不收集 IP 之外的标识
                </span>
                <button
                  onClick={handleSubmit}
                  disabled={submitting || text.trim().length < 2}
                  className="fraunces-body italic text-[16px] text-accent hover:text-accent-soft disabled:opacity-40 disabled:hover:text-accent transition-colors bg-transparent border-0 cursor-pointer"
                >
                  {submitting ? "投递中…" : "投 递 →"}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
