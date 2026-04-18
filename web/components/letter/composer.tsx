"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface Props {
  onSend: (text: string) => void;
  disabled?: boolean;
  /**
   * 草稿持久化 key — 通常传 letterId。每封信独立草稿；不传则不暂存。
   */
  draftKey?: string;
  /**
   * 外部预填 · 用 token 触发（而不是内容）——父组件点"话题种子"按钮时，
   * 传 `{ text, token: Date.now() }` 把文本塞进 textarea 并自动 focus。
   * 即便 text 相同也能再次触发（例如用户清空了再点同一个种子）。
   */
  prefill?: { text: string; token: number } | null;
}

const DRAFT_PREFIX = "oriself:draft:";
const DRAFT_DEBOUNCE_MS = 400;

/**
 * 检测用户是不是在 Mac 上。SSR 期间默认非 Mac，首帧 hydrate 后再矫正。
 * 既然这是纯展示用（快捷键提示文案），首帧闪一下不影响功能。
 */
function detectIsMac(): boolean {
  if (typeof navigator === "undefined") return false;
  // 先看新 API `navigator.userAgentData.platform`，兼容老的 platform / userAgent
  const uaDataPlat = (
    navigator as unknown as { userAgentData?: { platform?: string } }
  ).userAgentData?.platform;
  const plat = uaDataPlat ?? navigator.platform ?? navigator.userAgent ?? "";
  return /Mac|iPhone|iPad|iPod/i.test(plat);
}

function readDraft(key: string): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(DRAFT_PREFIX + key) ?? "";
  } catch {
    return "";
  }
}

function writeDraft(key: string, value: string): void {
  if (typeof window === "undefined") return;
  try {
    if (value) window.localStorage.setItem(DRAFT_PREFIX + key, value);
    else window.localStorage.removeItem(DRAFT_PREFIX + key);
  } catch {
    /* quota exceeded etc. — silent */
  }
}

/**
 * Composer · it's a line, not a box.
 *
 * The underline IS the input. On focus the underline turns oxblood.
 * Cmd/Ctrl+Enter sends.
 *
 * 草稿（ESC 暂存）：
 *  - 输入随时持久化到 localStorage（debounced），下次进同一封信自动恢复。
 *  - ESC = 主动暂存 + 失焦：刷新视觉反馈「刚刚已存」。
 *  - 发送成功后清空草稿。
 */
export function Composer({ onSend, disabled, draftKey, prefill }: Props) {
  const [text, setText] = useState("");
  const [savedHint, setSavedHint] = useState(false);
  // SSR 时先按非 Mac 渲染（Windows/Linux 占多数），hydrate 后矫正；
  // 避免服务端渲染出 ⌘ 后 Windows 用户看到闪屏
  const [isMac, setIsMac] = useState(false);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const debounceRef = useRef<number | null>(null);

  // 平台检测 · 只跑一次
  useEffect(() => {
    setIsMac(detectIsMac());
  }, []);

  // 进入时恢复草稿
  useEffect(() => {
    if (!draftKey) return;
    const draft = readDraft(draftKey);
    if (draft) setText(draft);
  }, [draftKey]);

  // 外部预填 · 点话题种子时由父组件触发
  useEffect(() => {
    if (!prefill || !prefill.text) return;
    setText(prefill.text);
    // focus + 光标移到末尾，方便用户继续编辑
    requestAnimationFrame(() => {
      const ta = taRef.current;
      if (!ta) return;
      ta.focus();
      const end = prefill.text.length;
      try {
        ta.setSelectionRange(end, end);
      } catch {
        /* 某些受控 textarea 可能抛错，忽略 */
      }
    });
    // 依赖 token 而非 text，让同一个种子可以被重复点击
  }, [prefill?.token]); // eslint-disable-line react-hooks/exhaustive-deps

  // 自动增高
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
  }, [text]);

  // 草稿 debounced 持久化
  useEffect(() => {
    if (!draftKey) return;
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      writeDraft(draftKey, text);
    }, DRAFT_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
    };
  }, [text, draftKey]);

  const handleSend = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText("");
    if (draftKey) writeDraft(draftKey, "");
    requestAnimationFrame(() => {
      if (taRef.current) taRef.current.style.height = "auto";
    });
  }, [text, disabled, onSend, draftKey]);

  const handleStashDraft = useCallback(() => {
    if (!draftKey) {
      // 没有 draftKey 时 ESC 仅失焦
      taRef.current?.blur();
      return;
    }
    writeDraft(draftKey, text);
    taRef.current?.blur();
    setSavedHint(true);
    window.setTimeout(() => setSavedHint(false), 1600);
  }, [text, draftKey]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        handleSend();
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        handleStashDraft();
      }
    },
    [handleSend, handleStashDraft],
  );

  return (
    <footer
      // z-20 > main 的 z-10 —— 第 2 轮之后 main 内容撑到视口底部时，
      // main 的 pb-[260px] padding 区域会堆在 composer 上面抢走点击
      // （composer 自己 pointer-events-none，但内层 textarea 的 pointer-events-auto
      // 区域需要比 main 高才能稳定接收点击）。
      className="fixed left-0 right-0 bottom-0 z-20 px-8 pt-20 pb-9 pointer-events-none"
      style={{
        background:
          "linear-gradient(to top, var(--paper) 55%, rgba(245, 240, 230, 0.92) 80%, rgba(245, 240, 230, 0))",
      }}
    >
      <div className="max-w-[620px] mx-auto pointer-events-auto">
        <textarea
          ref={taRef}
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            disabled ? "正在听……" : "写下你想到的第一件事，不必修饰……"
          }
          disabled={disabled}
          className="no-scrollbar w-full bg-transparent fraunces-body-soft text-[20px] leading-[1.55] text-ink resize-none outline-none pt-[6px] pb-[10px] border-b border-rule-strong focus:border-accent transition-colors duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] disabled:opacity-60 placeholder:italic placeholder:text-ink-muted placeholder:opacity-70"
          style={{
            caretColor: "var(--accent)",
            // 超过 200px 自动滚动但无可见条 · 配合 .no-scrollbar 全平台统一
            overflowY: "auto",
            scrollbarWidth: "none",
            msOverflowStyle: "none",
          }}
        />

        <div className="flex justify-between items-center mt-[14px] font-mono text-[10px] tracking-wide uppercase text-ink-muted">
          <span aria-live="polite">
            {savedHint
              ? "已暂存 · 下次回来还在"
              : `${isMac ? "⌘" : "Ctrl"} ↵ 发送 · ESC 暂存`}
          </span>
          <button
            onClick={handleSend}
            disabled={disabled || !text.trim()}
            className="fraunces-body italic text-[15px] text-accent hover:text-accent-soft transition-colors duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] disabled:opacity-40 disabled:hover:text-accent bg-transparent border-0 cursor-pointer normal-case tracking-normal"
          >
            发 送 <span className="font-mono not-italic">→</span>
          </button>
        </div>
      </div>
    </footer>
  );
}
