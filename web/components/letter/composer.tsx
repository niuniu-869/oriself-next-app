'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

interface Props {
  onSend: (text: string) => void;
  disabled?: boolean;
}

/**
 * Composer · it's a line, not a box.
 *
 * The underline IS the input. On focus the underline turns oxblood.
 * Cmd/Ctrl+Enter sends.
 */
export function Composer({ onSend, disabled }: Props) {
  const [text, setText] = useState('');
  const taRef = useRef<HTMLTextAreaElement>(null);

  // Auto-grow
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 200) + 'px';
  }, [text]);

  const handleSend = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText('');
    // Reset textarea height
    requestAnimationFrame(() => {
      if (taRef.current) taRef.current.style.height = 'auto';
    });
  }, [text, disabled, onSend]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  return (
    <footer
      className="fixed left-0 right-0 bottom-0 z-[8] px-8 pt-20 pb-9 pointer-events-none"
      style={{
        background:
          'linear-gradient(to top, var(--paper) 55%, rgba(245, 240, 230, 0.92) 80%, rgba(245, 240, 230, 0))',
      }}
    >
      <div className="max-w-[620px] mx-auto pointer-events-auto">
        <textarea
          ref={taRef}
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={disabled ? '正在听……' : '写下你想到的第一件事，不必修饰……'}
          disabled={disabled}
          className="w-full bg-transparent fraunces-body-soft text-[20px] leading-[1.55] text-ink resize-none outline-none pt-[6px] pb-[10px] border-b border-rule-strong focus:border-accent transition-colors duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] disabled:opacity-60 placeholder:italic placeholder:text-ink-muted placeholder:opacity-70"
          style={{ caretColor: 'var(--accent)' }}
        />

        <div className="flex justify-between items-center mt-[14px] font-mono text-[10px] tracking-wide uppercase text-ink-muted">
          <span>⌘ ↵ 发送 · ESC 暂存</span>
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
