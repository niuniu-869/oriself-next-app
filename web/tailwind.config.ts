import type { Config } from 'tailwindcss';

/**
 * Tailwind tokens for OriSelf.
 *
 * All colors/fonts are re-declared as CSS variables in `app/globals.css` so
 * that non-Tailwind CSS (e.g. Typeset animations) can use them too. This file
 * is Tailwind's view of the same source of truth.
 */
const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    // Disable Tailwind's default color palette. We only want our tokens.
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      paper: {
        DEFAULT: 'var(--paper)',
        deep: 'var(--paper-deep)',
        warm: 'var(--paper-warm)',
      },
      ink: {
        DEFAULT: 'var(--ink)',
        soft: 'var(--ink-soft)',
        muted: 'var(--ink-muted)',
      },
      accent: {
        DEFAULT: 'var(--accent)',
        soft: 'var(--accent-soft)',
      },
      rule: {
        DEFAULT: 'var(--rule)',
        strong: 'var(--rule-strong)',
      },
    },
    fontFamily: {
      serif: 'var(--font-serif)',
      sans: 'var(--font-sans)',
      mono: 'var(--font-mono)',
    },
    extend: {
      letterSpacing: {
        tightest: '-0.022em',
        tighter: '-0.015em',
        tight: '-0.005em',
        wide: '0.14em',
        wider: '0.18em',
        widest: '0.24em',
      },
      transitionTimingFunction: {
        spring: 'cubic-bezier(0.34, 1.25, 0.64, 1)',
        ease: 'cubic-bezier(0.22, 1, 0.36, 1)',
      },
      animation: {
        settle: 'settle 0.9s cubic-bezier(0.34, 1.25, 0.64, 1) forwards',
        rise: 'rise 0.6s cubic-bezier(0.34, 1.25, 0.64, 1) forwards',
        blink: 'blink 1.1s infinite',
      },
      keyframes: {
        settle: {
          from: { opacity: '0', transform: 'translateY(10px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        rise: {
          from: { opacity: '0', transform: 'translateY(18px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        blink: {
          '0%, 40%': { opacity: '1' },
          '50%, 90%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
      },
    },
  },
  plugins: [],
};

export default config;
