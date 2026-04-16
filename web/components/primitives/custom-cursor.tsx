'use client';

import { useEffect, useRef } from 'react';

/**
 * CustomCursor · a subtle oxblood dot that follows the mouse, expands into a
 * ring on interactive surfaces. Disabled on touch devices.
 */
export function CustomCursor() {
  const dotRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Skip on touch-primary devices
    if (window.matchMedia('(hover: none)').matches) return;

    const dot = dotRef.current;
    if (!dot) return;

    const interactiveSelector =
      'a, button, textarea, input, [role="button"], [data-interactive]';

    let mouseX = 0;
    let mouseY = 0;
    let curX = 0;
    let curY = 0;
    let rafId = 0;

    const onMove = (e: MouseEvent) => {
      mouseX = e.clientX;
      mouseY = e.clientY;
      dot.style.opacity = '1';
      const target = e.target as HTMLElement;
      if (target?.matches?.(interactiveSelector)) {
        dot.classList.add('expanded');
      } else {
        dot.classList.remove('expanded');
      }
    };

    const onLeave = () => {
      dot.style.opacity = '0';
    };

    const raf = () => {
      curX += (mouseX - curX) * 0.22;
      curY += (mouseY - curY) * 0.22;
      dot.style.transform = `translate(${curX}px, ${curY}px) translate(-50%, -50%)`;
      rafId = requestAnimationFrame(raf);
    };

    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseleave', onLeave);
    raf();

    return () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseleave', onLeave);
      cancelAnimationFrame(rafId);
    };
  }, []);

  return (
    <div
      ref={dotRef}
      aria-hidden
      className="cursor-dot pointer-events-none fixed top-0 left-0 w-[6px] h-[6px] bg-accent rounded-full z-[9999] opacity-0 transition-opacity duration-[250ms] ease-[cubic-bezier(0.22,1,0.36,1)]"
      style={{
        mixBlendMode: 'multiply',
        transition:
          'opacity 250ms var(--ease), width 250ms var(--ease), height 250ms var(--ease), background 250ms var(--ease), border 250ms var(--ease)',
      }}
    />
  );
}
