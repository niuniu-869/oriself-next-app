"use client";

import { memo } from "react";
import type { TurnRecord } from "@/lib/types";
import { Markdown } from "@/lib/markdown";
import { QuillNote } from "./quill-note";

interface Props {
  turn: TurnRecord;
  /** 流式中：文本正在增长，在结尾渲染一个跳动游标。 */
  streaming?: boolean;
}

/**
 * 单条 turn。
 *
 * OriSelf · serif body，无气泡；气泡顶部可能挂一条 QuillNote（Oriself 的笔触批注）。
 * You · 左侧 em-dash 点缀。
 * 无头像、无时间戳。
 *
 * v2.4 · 删除旧的 RevealText 分句动画 —— 真流式时文本每帧都在变，按标点分段的
 * 动画会反复重排很丑。现在只做两件事：`white-space: pre-wrap` 保留换行 +
 * streaming 时末尾挂一个 writing-cursor。
 * v2.5.3 · Oriself 气泡上方增加 QuillNote：token 之前就渲染，流完不消失，回看也在。
 * v2.5.4 · streaming 已开始、但第一个 token 还没到的空档期（主要针对 Gemini 这类
 * 有明显 thinking 阶段的 provider）显示「Oriself 思考中」占位；收到第一个
 * token 后自动切回正文 + 游标。
 * v2.5.4 · Oriself 正文接入 <Markdown>：支持粗体 / 斜体 / 行内代码 / 链接 /
 * 有序&无序列表 / 引用 / 标题 / 分割线。用户轮保持纯文本 pre-wrap，避免把
 * 自由书写里的 `*` / `**` 误识为 md 标记。
 */
export const Turn = memo(function Turn({ turn, streaming = false }: Props) {
  if (turn.speaker === "oriself") {
    const thinking = streaming && turn.text === "";
    return (
      <article className="mb-14 animate-settle">
        {turn.quill_lines && turn.quill_lines.length > 0 && (
          <QuillNote lines={turn.quill_lines} />
        )}
        {thinking ? (
          <p
            className="fraunces-body-soft italic text-[17px] leading-[1.65] text-ink-muted"
            aria-live="polite"
          >
            Oriself 思考中
            <span className="animate-blink" aria-hidden>
              …
            </span>
          </p>
        ) : (
          <Markdown
            source={turn.text}
            trailing={
              streaming ? (
                <span className="writing-cursor inline-block align-baseline" />
              ) : null
            }
          />
        )}
      </article>
    );
  }

  return (
    <article className="mb-14 pl-9 relative animate-settle">
      <span
        aria-hidden
        className="absolute left-0 top-[6px] font-serif text-[26px] leading-none text-accent"
        style={{
          fontVariationSettings: '"opsz" 36, "SOFT" 0, "WONK" 0',
          fontWeight: 300,
        }}
      >
        —
      </span>
      <p
        className="fraunces-body-soft text-[19px] leading-[1.65] text-ink-soft"
        style={{ whiteSpace: "pre-wrap" }}
      >
        {turn.text}
      </p>
    </article>
  );
});
