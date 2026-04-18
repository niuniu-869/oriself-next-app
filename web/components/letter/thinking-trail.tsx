"use client";

import type { TurnStreamPhaseEvent } from "@/lib/api";

interface Props {
  trail: TurnStreamPhaseEvent[];
}

/**
 * ThinkingTrail · 把 SSE phase 事件优雅地呈现给用户。
 *
 * 做设计取舍：不是把后端阶段名直接塞进 UI（"validating" 之类让人摸不着），
 * 而是翻译成和整体美学一致的短句。每条用一个轻淡的 em-dash 领起，
 * 仅最新一条带闪烁光标，让人感觉对方在当下推进，而不是在读 log。
 */
export function ThinkingTrail({ trail }: Props) {
  const lines = trail
    .map(phaseLine)
    .filter((x): x is string => Boolean(x));

  // 还没收到第一条 phase · 给个兜底避免空白闪动
  if (lines.length === 0) {
    return (
      <div className="mb-14 opacity-70" aria-live="polite">
        <p className="fraunces-body text-[20px] leading-[1.62] text-ink">
          <span className="inline-block">
            正在听
            <span className="writing-cursor" />
          </span>
        </p>
      </div>
    );
  }

  return (
    <div className="mb-14 opacity-80" aria-live="polite">
      <ul className="space-y-[6px] font-mono text-[11px] tracking-wide uppercase text-ink-muted">
        {lines.map((line, i) => {
          const isLast = i === lines.length - 1;
          return (
            <li
              key={`${line}-${i}`}
              className={`flex items-baseline gap-2 ${
                isLast ? "text-accent" : ""
              } animate-settle`}
              style={{ animationDelay: `${i * 0.05}s` }}
            >
              <span aria-hidden className="text-ink-muted/60">
                —
              </span>
              <span className="normal-case tracking-normal fraunces-body-soft italic text-[15px]">
                {line}
              </span>
              {isLast && <span className="writing-cursor" />}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function phaseLine(evt: TurnStreamPhaseEvent): string | null {
  switch (evt.phase) {
    case "listening":
      // 第一条——强调是"听"而不是"处理"
      if (evt.phase_key === "phase0-onboarding") return "在读你写下的第一行";
      if (evt.phase_key === "phase3_5-midpoint") return "在把前面几轮放在一起看";
      if (evt.phase_key === "phase4_8-soft-closing") return "在给这封信收尾做准备";
      if (evt.phase_key === "phase5-converge") return "在把整场对话一起消化";
      return "在听你说";
    case "thinking":
      if (evt.is_converge) return "在写给你的那封信";
      if ((evt.attempt ?? 1) > 1) return `再想一遍（第 ${evt.attempt} 次）`;
      return "在想下一句怎么问";
    case "validating":
      return "在把话核一遍";
    case "retrying":
      if (evt.reason === "guardrails") return "有一处不对，重新写";
      if (evt.reason === "schema") return "结构不稳，重新写";
      return "再写一次";
    case "composed":
      // 这条出现得很短暂（紧接 final）——给一个"呼吸"过渡句
      if (evt.action_type === "converge") return "写好了，正在把报告交给你";
      if (evt.action_type === "reflect") return "想到一个点想跟你说";
      return "想好了";
    case "fallback":
      return "一时说不出贴切的话，先接住";
    default:
      return null;
  }
}
