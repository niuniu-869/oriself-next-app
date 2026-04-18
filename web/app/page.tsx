import Link from "next/link";

/**
 * Landing · 作品集风格。
 *
 * 不卖。当成一个作品。一个名字、一行版权页式注释、一个入口。
 * 没有 hero 广告语、没有"发生什么"分段、没有 CTA 文案动员。
 */
export default function LandingPage() {
  return (
    <main className="relative z-10 min-h-screen flex flex-col">
      {/* Hero — single word, monumental */}
      <section className="flex-1 flex flex-col items-center justify-center px-6">
        {/* The name. That's it. */}
        <h1
          className="text-ink text-center"
          style={{
            fontFamily: "var(--font-serif)",
            fontVariationSettings: '"opsz" 144, "SOFT" 100, "WONK" 1',
            fontStyle: "italic",
            fontWeight: 400,
            fontSize: "clamp(96px, 18vw, 220px)",
            lineHeight: 0.92,
            letterSpacing: "-0.045em",
          }}
        >
          OriSelf
        </h1>

        {/* Catalogue note · masthead style */}
        <p className="font-mono text-[11px] tracking-widest uppercase text-ink-muted mt-10 text-center">
          一份对话式人格画像 · 中文 · 2026
        </p>

        {/* Entry — small, off-white-on-ink button-less link */}
        <Link
          href="/letters/new"
          className="mt-20 inline-block fraunces-body-soft italic text-accent text-[18px] border-b border-accent pb-1 transition-colors duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:text-accent-soft hover:border-accent-soft"
        >
          进入 →
        </Link>
      </section>

      {/* Colophon — barely visible, edge of the page */}
      <footer className="px-8 pb-8 pt-16">
        <div className="max-w-[1200px] mx-auto flex justify-between items-baseline font-mono text-[10px] tracking-widest uppercase text-ink-muted">
          <span>OriSelf · Issue 04 · v2.4.0</span>
          <div className="flex items-baseline gap-[14px]">
            <a
              href="https://github.com/niuniu-869/oriself-next"
              className="hover:text-accent transition-colors"
              target="_blank"
              rel="noopener"
              aria-label="Skill 仓库 · GitHub"
            >
              Skill ↗
            </a>
            <span aria-hidden className="opacity-40">
              ·
            </span>
            <a
              href="https://github.com/niuniu-869/oriself-next-app"
              className="hover:text-accent transition-colors"
              target="_blank"
              rel="noopener"
              aria-label="App 仓库 · GitHub"
            >
              App ↗
            </a>
            <span aria-hidden className="opacity-40">
              ·
            </span>
            <span>Apache 2.0</span>
          </div>
        </div>
      </footer>
    </main>
  );
}
