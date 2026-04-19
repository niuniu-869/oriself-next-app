'use client';

import { memo } from 'react';

interface Props {
  lines: string[];
}

/**
 * Oriself 的"笔触批注" · 出现在 oriself 气泡最上方。
 *
 * 设计意图：
 * - 浅灰斜体、左侧一条极细的装订线，像信纸边角的铅笔批注
 * - token 流出前就渲染（淡入），流完后不消失，回看也在
 * - 永不包含工程术语，详见 server/oriself_server/quill.py 文案铁则
 *
 * 视觉与 `Turn` 的 oriself 正文严格区分：
 *   正文 · Fraunces body · ink · 20px
 *   批注 · italic · ink-muted/55 · 13px · 左竖线 rule/40
 */
export const QuillNote = memo(function QuillNote({ lines }: Props) {
  if (!lines || lines.length === 0) return null;
  return (
    <div
      className="mb-3 pl-3 border-l border-rule/40 animate-settle"
      aria-hidden="true"
    >
      {lines.map((line, i) => (
        <p
          key={i}
          className="fraunces-body-soft italic text-[13px] leading-[1.6] text-ink-muted/70 tracking-wide"
        >
          {line}
        </p>
      ))}
    </div>
  );
});
