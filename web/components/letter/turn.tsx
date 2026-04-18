'use client';

import { memo } from 'react';
import type { TurnRecord } from '@/lib/types';

interface Props {
  turn: TurnRecord;
  /** 流式中：文本正在增长，在结尾渲染一个跳动游标。 */
  streaming?: boolean;
}

/**
 * 单条 turn。
 *
 * OriSelf · serif body，无气泡。
 * You · 左侧 em-dash 点缀。
 * 无头像、无时间戳。
 *
 * v2.4 · 删除旧的 RevealText 分句动画 —— 真流式时文本每帧都在变，按标点分段的
 * 动画会反复重排很丑。现在只做两件事：`white-space: pre-wrap` 保留换行 +
 * streaming 时末尾挂一个 writing-cursor。
 */
export const Turn = memo(function Turn({ turn, streaming = false }: Props) {
  if (turn.speaker === 'oriself') {
    return (
      <article className="mb-14 animate-settle">
        <p
          className="fraunces-body text-[20px] leading-[1.62] tracking-tight text-ink"
          style={{ whiteSpace: 'pre-wrap' }}
        >
          {turn.text}
          {streaming && <span className="writing-cursor inline-block align-baseline" />}
        </p>
      </article>
    );
  }

  return (
    <article className="mb-14 pl-9 relative animate-settle">
      <span
        aria-hidden
        className="absolute left-0 top-[6px] font-serif text-[26px] leading-none text-accent"
        style={{ fontVariationSettings: '"opsz" 36, "SOFT" 0, "WONK" 0', fontWeight: 300 }}
      >
        —
      </span>
      <p
        className="fraunces-body-soft text-[19px] leading-[1.65] text-ink-soft"
        style={{ whiteSpace: 'pre-wrap' }}
      >
        {turn.text}
      </p>
    </article>
  );
});
