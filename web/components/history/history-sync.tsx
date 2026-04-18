"use client";

import { useEffect } from "react";
import { upsertLetter, type LocalLetterEntry } from "@/lib/history";

type Patch = Partial<LocalLetterEntry> & { letterId: string };

/**
 * 挂到 server component 里的 client bridge：mount 时把一份快照 upsert 到
 * localStorage 历史。issue 页（server-rendered）用它来保证：用户直接打开
 * 报告链接也能补齐本地历史条目。
 *
 * 渲染为 null —— 纯副作用。
 */
export function HistorySync(props: Patch) {
  useEffect(() => {
    upsertLetter(props);
    // 逐字段依赖：对象身份每次父组件渲染都会变，避免重复写。
  }, [
    props.letterId,
    props.status,
    props.roundCount,
    props.issueSlug,
    props.mbtiType,
    props.cardTitle,
  ]);
  return null;
}
