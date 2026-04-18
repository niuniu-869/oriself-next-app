"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { publishIssue } from "@/lib/api";
import { FeedbackSheet } from "@/components/feedback/feedback-sheet";

interface Props {
  slug: string;
  initialIsPublic: boolean;
  letterId?: string;
}

/**
 * Issue chrome · 报告页底部一条克制的工具栏。
 *
 * 设计：
 *  - 默认半透明、几乎贴底、文字而非图标，让 iframe 内的报告本身是视觉主角。
 *  - hover 时整条加深一点点，提示可交互。
 *  - 包含：← 返回首页 · 再写一封 · 公开/私有 · 反馈 · 复制链接
 *  - 公开/私有切换是 PATCH 调用，立刻反映到本地状态，乐观更新。
 */
export function IssueChrome({ slug, initialIsPublic, letterId }: Props) {
  const [isPublic, setIsPublic] = useState(initialIsPublic);
  const [toggling, setToggling] = useState(false);
  const [copied, setCopied] = useState(false);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleTogglePublic = useCallback(async () => {
    if (toggling) return;
    setToggling(true);
    setError(null);
    const next = !isPublic;
    // 乐观更新
    setIsPublic(next);
    try {
      const res = await publishIssue(slug, next);
      setIsPublic(res.is_public);
    } catch (err) {
      // 回滚
      setIsPublic(!next);
      setError(err instanceof Error ? err.message : "切换失败");
    } finally {
      setToggling(false);
    }
  }, [slug, isPublic, toggling]);

  const handleCopyLink = useCallback(async () => {
    try {
      const url =
        typeof window !== "undefined"
          ? `${window.location.origin}/issues/${slug}`
          : `/issues/${slug}`;
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setError("复制失败");
    }
  }, [slug]);

  return (
    <>
      <div
        className="fixed left-0 right-0 bottom-0 z-30 pointer-events-none"
        style={{
          background:
            "linear-gradient(to top, rgba(245, 240, 230, 0.96) 0%, rgba(245, 240, 230, 0.86) 60%, rgba(245, 240, 230, 0))",
        }}
      >
        <nav
          className="pointer-events-auto max-w-[920px] mx-auto px-6 py-4 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 font-mono text-[10px] tracking-widest uppercase text-ink-muted"
          aria-label="报告操作"
        >
          {/* 左：返回与重写 */}
          <div className="flex items-center gap-5">
            <Link
              href="/"
              className="hover:text-accent transition-colors"
              aria-label="返回首页"
            >
              ← 首页
            </Link>
            {letterId && (
              <Link
                href={`/letters/${letterId}`}
                className="hover:text-accent transition-colors"
                aria-label="回看这封信的对话"
              >
                ← 回看对话
              </Link>
            )}
            <Link
              href="/letters/new"
              className="hover:text-accent transition-colors"
              aria-label="开始一封新的信"
            >
              再写一封 →
            </Link>
          </div>

          {/* 右：分享 / 反馈 */}
          <div className="flex items-center gap-5">
            <button
              type="button"
              onClick={handleTogglePublic}
              disabled={toggling}
              aria-pressed={isPublic}
              className="hover:text-accent transition-colors disabled:opacity-50 cursor-pointer"
              title={
                isPublic ? "当前公开 · 任何人凭链接可看" : "当前私有 · 仅你可见"
              }
            >
              {isPublic ? "● 公开" : "○ 私有"}
            </button>
            <button
              type="button"
              onClick={handleCopyLink}
              disabled={!isPublic}
              className="hover:text-accent transition-colors disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
              title={isPublic ? "复制公开链接" : "先公开再复制"}
            >
              {copied ? "已复制 ✓" : "复制链接"}
            </button>
            <button
              type="button"
              onClick={() => setFeedbackOpen(true)}
              className="group inline-flex items-center gap-[6px] border border-accent/70 hover:border-accent hover:bg-accent/5 text-accent transition-colors px-[10px] py-[5px] rounded-[2px] normal-case tracking-[0.08em] text-[11px] cursor-pointer"
              aria-label="对这封信提反馈"
              title="对这封信说一句（匿名，1 分钟就好）"
            >
              <span aria-hidden className="font-mono text-[12px] leading-none">
                ✎
              </span>
              <span className="fraunces-body italic">对这封信说一句</span>
            </button>
          </div>

          {error && (
            <p className="basis-full text-accent normal-case tracking-normal">
              {error}
            </p>
          )}
        </nav>
      </div>

      <FeedbackSheet
        open={feedbackOpen}
        onClose={() => setFeedbackOpen(false)}
        letterId={letterId}
        issueSlug={slug}
      />
    </>
  );
}
