"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { publishIssue } from "@/lib/api";
import { FeedbackSheet } from "@/components/feedback/feedback-sheet";
import { AuthorModal } from "@/components/primitives/author-modal";

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
 *  - 包含：← 返回首页 · 回看对话 · 再写一封 · 公开/私有 · 复制地址按钮 · 反馈
 *  - 复制地址按钮常驻展示中文文案 "复制地址"，一点即复制完整 URL；
 *    即使私有也允许复制（owner 自己要用），tooltip 会标注分享状态。
 *  - 公开/私有切换是 PATCH 调用，立刻反映到本地状态，乐观更新。
 */
export function IssueChrome({ slug, initialIsPublic, letterId }: Props) {
  const [isPublic, setIsPublic] = useState(initialIsPublic);
  const [toggling, setToggling] = useState(false);
  const [copied, setCopied] = useState(false);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [authorOpen, setAuthorOpen] = useState(false);
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
          className="pointer-events-auto max-w-[920px] mx-auto px-4 sm:px-6 py-3 sm:py-4 flex flex-wrap items-center justify-between gap-x-4 sm:gap-x-6 gap-y-2 font-mono text-[10px] tracking-widest uppercase text-ink-muted"
          aria-label="报告操作"
        >
          {/* 左：导航 + 作者入口 */}
          <div className="flex items-center flex-wrap gap-x-4 sm:gap-x-5 gap-y-1">
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
                ← 回看
              </Link>
            )}
            <Link
              href="/letters/new"
              className="hover:text-accent transition-colors"
              aria-label="开始一封新的信"
            >
              再写一封 →
            </Link>
            <button
              type="button"
              onClick={() => setAuthorOpen(true)}
              className="hover:text-accent transition-colors bg-transparent border-0 p-0"
              aria-label="关于作者"
            >
              AUTHOR
            </button>
          </div>

          {/* 右：公开开关 · 复制地址 · 反馈 */}
          <div className="flex items-center flex-wrap gap-x-3 sm:gap-x-4 gap-y-2">
            <button
              type="button"
              onClick={handleTogglePublic}
              disabled={toggling}
              aria-pressed={isPublic}
              className="hover:text-accent transition-colors disabled:opacity-50"
              title={
                isPublic
                  ? "当前公开 · 任何人凭链接可看"
                  : "当前私有 · 仅你可见，他人访问会 403"
              }
            >
              {isPublic ? "● 公开" : "○ 私有"}
            </button>
            {/* 复制地址按钮 · 中文文案 + ⎘；点击即复制完整 URL */}
            <button
              type="button"
              onClick={handleCopyLink}
              aria-label="复制这封信的地址"
              className={`group inline-flex items-center gap-[6px] border rounded-[2px] px-[10px] py-[5px] normal-case tracking-[0.04em] transition-colors ${
                isPublic
                  ? "border-rule-strong hover:border-accent hover:text-accent"
                  : "border-rule hover:border-accent/60 hover:text-accent/80"
              }`}
              title={
                isPublic
                  ? "复制这封信的地址"
                  : "这封信当前私有 · 复制的链接只有你自己能打开"
              }
            >
              <span
                className={`fraunces-body italic text-[11px] transition-colors ${
                  copied
                    ? "text-accent"
                    : "text-ink-soft group-hover:text-accent"
                }`}
              >
                {copied ? "已抄下" : "复制地址"}
              </span>
              <span
                aria-hidden
                className={`font-mono text-[12px] leading-none not-italic transition-colors ${
                  copied
                    ? "text-accent"
                    : "text-ink-muted group-hover:text-accent"
                }`}
              >
                {copied ? "✓" : "⎘"}
              </span>
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
              {/* 窄屏缩为"反馈"，sm+ 展开为"对这封信说一句" */}
              <span className="fraunces-body italic">
                <span className="sm:hidden">反馈</span>
                <span className="hidden sm:inline">对这封信说一句</span>
              </span>
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

      <AuthorModal open={authorOpen} onClose={() => setAuthorOpen(false)} />
    </>
  );
}
