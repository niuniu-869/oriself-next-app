"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

interface Props {
  slug: string;
}

/**
 * ArrivalCeremony · 封缄时刻。
 *
 * CONVERGE 成功后，letter-view 会 router.push 到 /issues/:slug?arrived=1。
 * 本组件在 `arrived=1` 时全屏铺纸，用一段小叙事把"这封信拿到了一个永久地址"
 * 这件事显式说出来：
 *
 *   0.0s  纸面铺满，覆盖身后的 iframe
 *   0.1s  标题淡入「这封信已经写完」
 *   0.8s  分割线与小字「它停在这个地址」淡入
 *   1.4s  地址开始打字机式逐字出现（mono, accent）
 *   ~2.7s 地址打完，CTA 升起：主按钮「看信 →」自动 focus；次按钮「复制地址 ⎘」
 *   6.0s  自动消散，进入 iframe
 *
 * 交互：
 *   - 回车 / 空格 / ESC 立即关闭
 *   - 地址点击即复制；下方出现「已抄下 ✓」
 *   - 关闭时 router.replace 去掉 `?arrived=1`，刷新不会重播
 */
export function ArrivalCeremony({ slug }: Props) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const shouldShow = searchParams.get("arrived") === "1";

  const [open, setOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);
  // phase 0 → 尚未开始；1 → 标题在；2 → 分割线在；3 → 开始打字
  const [phase, setPhase] = useState<0 | 1 | 2 | 3>(0);
  const [typed, setTyped] = useState(0);
  const [copied, setCopied] = useState(false);
  const dismissedRef = useRef(false);

  // 地址文本在客户端才能拿到完整 host；SSR 时退化为相对路径
  const [displayAddr, setDisplayAddr] = useState(`/issues/${slug}`);
  const [fullUrl, setFullUrl] = useState(`/issues/${slug}`);

  useEffect(() => {
    if (typeof window === "undefined") return;
    setDisplayAddr(`${window.location.host}/issues/${slug}`);
    setFullUrl(`${window.location.origin}/issues/${slug}`);
  }, [slug]);

  // 只有在 URL 带 ?arrived=1 时挂载
  useEffect(() => {
    if (!shouldShow) return;
    setOpen(true);
  }, [shouldShow]);

  // 阶段节奏
  useEffect(() => {
    if (!open) return;
    const t1 = setTimeout(() => setPhase(1), 60);
    const t2 = setTimeout(() => setPhase(2), 800);
    const t3 = setTimeout(() => setPhase(3), 1400);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
    };
  }, [open]);

  // 打字机
  useEffect(() => {
    if (phase < 3) return;
    const total = displayAddr.length;
    let step = 0;
    const iv = setInterval(() => {
      step += 1;
      setTyped(step);
      if (step >= total) clearInterval(iv);
    }, 38);
    return () => clearInterval(iv);
  }, [phase, displayAddr]);

  const dismiss = useCallback(() => {
    if (dismissedRef.current) return;
    dismissedRef.current = true;
    setLeaving(true);
    // 让透明度过渡先跑，再替换掉 query；否则会瞬间黑屏
    setTimeout(() => {
      setOpen(false);
      router.replace(`/issues/${slug}`);
    }, 480);
  }, [router, slug]);

  // 自动消散
  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => dismiss(), 6000);
    return () => clearTimeout(t);
  }, [open, dismiss]);

  // 键盘跳过
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" || e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        dismiss();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, dismiss]);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(fullUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // 静默：极少数浏览器禁了 clipboard；底栏还有一枚 pill 兜底
    }
  }, [fullUrl]);

  if (!open) return null;

  const addrDone = typed >= displayAddr.length;
  const addrShown = displayAddr.slice(0, typed);
  const showCtas = addrDone;

  return (
    <div
      role="dialog"
      aria-label="这封信已经写完"
      aria-live="polite"
      className={`fixed inset-0 z-40 bg-paper transition-opacity ease-ease duration-500 ${
        leaving ? "opacity-0" : "opacity-100"
      }`}
    >
      <div className="absolute inset-0 flex flex-col items-center justify-center px-6 sm:px-8 text-center">
        {/* 标题 */}
        <h2
          className="transition-all duration-[700ms] ease-spring"
          style={{
            fontFamily: "var(--font-serif)",
            fontVariationSettings: '"opsz" 144, "SOFT" 100, "WONK" 1',
            fontStyle: "italic",
            fontWeight: 400,
            fontSize: "clamp(48px, 7vw, 84px)",
            lineHeight: 1.05,
            letterSpacing: "-0.035em",
            color: "var(--ink)",
            opacity: phase >= 1 ? 1 : 0,
            transform: phase >= 1 ? "translateY(0)" : "translateY(14px)",
          }}
        >
          这封信已经写完
        </h2>

        {/* 分割线 · 它停在这个地址 */}
        <div
          className="mt-8 sm:mt-12 flex items-center gap-4 sm:gap-5 w-full max-w-[460px] transition-all duration-[600ms] ease-ease"
          style={{
            opacity: phase >= 2 ? 1 : 0,
            transform: phase >= 2 ? "translateY(0)" : "translateY(10px)",
          }}
        >
          <span className="flex-1 h-px bg-rule-strong" />
          <span className="font-mono text-[10px] tracking-widest uppercase text-ink-muted whitespace-nowrap">
            它停在这个地址
          </span>
          <span className="flex-1 h-px bg-rule-strong" />
        </div>

        {/* 地址（打字机 + 点击复制） */}
        <button
          type="button"
          onClick={handleCopy}
          className="mt-6 sm:mt-8 font-mono text-accent hover:text-accent-soft bg-transparent border-0 p-0 break-all max-w-full"
          style={{
            fontSize: "clamp(14px, 3.4vw, 22px)",
            letterSpacing: "0.01em",
            opacity: phase >= 3 ? 1 : 0,
            transition:
              "opacity 400ms cubic-bezier(0.22, 1, 0.36, 1), color 200ms",
          }}
          title="点击复制这封信的地址"
          aria-label="复制这封信的地址"
          tabIndex={showCtas ? 0 : -1}
        >
          <span className="inline-block border-b border-accent/50 pb-[3px]">
            {addrShown}
            {phase >= 3 && !addrDone && (
              <span className="writing-cursor" aria-hidden />
            )}
          </span>
        </button>

        {/* 复制反馈小字 */}
        <div className="mt-3 h-[14px] font-mono text-[10px] tracking-widest uppercase text-ink-muted">
          {copied ? "已抄下 ✓" : addrDone ? "点一下即可复制" : ""}
        </div>

        {/* 主 / 次 CTA */}
        <div
          className="mt-10 sm:mt-14 flex items-center gap-8 sm:gap-10 transition-all duration-[700ms] ease-spring"
          style={{
            opacity: showCtas ? 1 : 0,
            transform: showCtas ? "translateY(0)" : "translateY(14px)",
            pointerEvents: showCtas ? "auto" : "none",
          }}
        >
          <button
            type="button"
            onClick={dismiss}
            className="fraunces-body italic text-[19px] text-accent hover:text-accent-soft border-b border-accent/60 hover:border-accent pb-[3px] transition-colors bg-transparent p-0"
            autoFocus
            aria-label="进入报告"
          >
            看信 <span className="font-mono not-italic">→</span>
          </button>
          <button
            type="button"
            onClick={handleCopy}
            className="font-mono text-[11px] tracking-widest uppercase text-ink-muted hover:text-accent transition-colors bg-transparent border-0 p-0"
          >
            复制地址 <span className="not-italic">⎘</span>
          </button>
        </div>

        {/* 底部跳过提示 */}
        <p
          className="absolute bottom-6 sm:bottom-10 left-1/2 -translate-x-1/2 font-mono text-[10px] tracking-widest uppercase text-ink-muted transition-opacity duration-700 whitespace-nowrap"
          style={{ opacity: showCtas ? 0.6 : 0 }}
        >
          按 Enter 或 Esc 直接看信
        </p>
      </div>
    </div>
  );
}
