"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  getAllLetters,
  removeLetter,
  type LocalLetterEntry,
} from "@/lib/history";

/**
 * 首页 · 最近在你浏览器里写过的信。
 *
 * - 纯 localStorage，没有网络。
 * - 空态直接不渲染，保留作品集极简感。
 * - 每条一行：左侧标题 / 元信息，右侧入口链接，最右小 × 可删（本地删除，不影响后端数据）。
 */
export function RecentLetters() {
  const [entries, setEntries] = useState<LocalLetterEntry[]>([]);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setEntries(getAllLetters());
    setMounted(true);
  }, []);

  const handleRemove = useCallback((id: string) => {
    removeLetter(id);
    setEntries(getAllLetters());
  }, []);

  // SSR 期间 / 空历史 · 不渲染整块
  if (!mounted || entries.length === 0) return null;

  return (
    <section
      aria-label="你最近的信"
      className="w-full max-w-[620px] mx-auto px-8 pb-20"
    >
      <p className="font-mono text-[10px] tracking-widest uppercase text-ink-muted mb-6">
        最近在你的浏览器里 · 本地保存 · 未上传
      </p>
      <ul className="space-y-5">
        {entries.map((e) => (
          <LetterRow key={e.letterId} entry={e} onRemove={handleRemove} />
        ))}
      </ul>
    </section>
  );
}

function LetterRow({
  entry,
  onRemove,
}: {
  entry: LocalLetterEntry;
  onRemove: (id: string) => void;
}) {
  const isDone = entry.status === "completed" && !!entry.issueSlug;
  const targetHref = isDone
    ? `/issues/${entry.issueSlug}`
    : `/letters/${entry.letterId}`;
  const targetLabel = isDone ? "看报告" : "回到这封信";

  return (
    <li className="flex items-start justify-between gap-4 border-b border-rule pb-4">
      <div className="min-w-0 flex-1">
        <Link
          href={targetHref}
          className="group block no-underline"
          aria-label={`${targetLabel} · ${entry.letterId.slice(0, 8)}`}
        >
          <div className="flex items-baseline gap-3">
            {isDone && entry.mbtiType ? (
              <span className="font-mono text-[12px] tracking-[0.18em] text-accent">
                {entry.mbtiType}
              </span>
            ) : (
              <span className="font-mono text-[10px] tracking-widest uppercase text-ink-muted">
                进行中 · R{String(entry.roundCount).padStart(2, "0")}
              </span>
            )}
            <span
              className="font-mono text-[10px] tracking-widest uppercase text-ink-muted"
              aria-hidden
            >
              {formatDate(entry.updatedAt)}
            </span>
          </div>

          <p
            className="fraunces-body italic text-[17px] leading-snug text-ink mt-[6px] truncate group-hover:text-accent transition-colors duration-300"
            title={entry.cardTitle ?? entry.letterId}
          >
            {entry.cardTitle ??
              (isDone ? "（未命名报告）" : "还没收尾的一封信")}
          </p>
        </Link>
      </div>

      <div className="flex items-center gap-4 pt-[2px]">
        <Link
          href={targetHref}
          className="fraunces-body italic text-[14px] text-accent hover:text-accent-soft border-b border-accent/40 hover:border-accent transition-colors whitespace-nowrap"
        >
          {targetLabel} <span className="font-mono not-italic">→</span>
        </Link>
        <button
          type="button"
          onClick={() => onRemove(entry.letterId)}
          aria-label="从本地历史移除这条（后端数据不受影响）"
          title="从本地历史移除（后端数据不受影响）"
          className="font-mono text-[14px] leading-none text-ink-muted hover:text-accent transition-colors bg-transparent border-0 cursor-pointer p-1"
        >
          ×
        </button>
      </div>
    </li>
  );
}

function formatDate(ts: number): string {
  try {
    const d = new Date(ts);
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${d.getFullYear()}.${m}.${day}`;
  } catch {
    return "";
  }
}
