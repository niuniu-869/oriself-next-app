import Link from 'next/link';

/**
 * Landing page · next.oriself.com
 *
 * 极简：一行 display、一段副文、一个 italic-underline CTA。
 * 没有 nav、没有 logo（logo 在落地只会干扰），没有 footer 罗列。
 */
export default function LandingPage() {
  return (
    <main className="relative z-10">
      {/* Hero */}
      <section className="min-h-screen flex flex-col items-center justify-center px-6 py-32">
        <div className="max-w-[620px] text-center">
          {/* Issue label, masthead-style */}
          <p className="font-mono text-[10px] tracking-widest uppercase text-ink-muted mb-10">
            OriSelf · established 2026
          </p>

          {/* The single sentence */}
          <h1 className="fraunces-display-italic text-[clamp(38px,7vw,76px)] leading-[1.08] tracking-tightest text-ink mb-12">
            在这里，你会被
            <br />
            <span className="text-accent">认认真真</span>
            听一次。
          </h1>

          {/* The explanation, kept to one paragraph */}
          <p className="fraunces-body text-[19px] leading-[1.65] text-ink-soft mb-16 max-w-[32ch] mx-auto">
            不是选择题，不是诊断报告。
            是一次大约二十分钟的对话——
            你说什么，这里就记什么，
            最后给你写一封属于你的信。
          </p>

          {/* The single CTA — italic, underline, not a button */}
          <Link
            href="/letters/new"
            className="inline-block fraunces-body-soft italic text-accent text-[20px] border-b border-accent pb-1 transition-colors duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:text-accent-soft hover:border-accent-soft"
          >
            开始一次 →
          </Link>
        </div>
      </section>

      {/* What happens here — three small sections, typeset, not cards */}
      <section className="max-w-[620px] mx-auto px-6 py-24">
        <div className="mb-20">
          <p className="font-mono text-[10px] tracking-widest uppercase text-accent mb-6">
            I · 发生什么
          </p>
          <p className="fraunces-body text-[19px] leading-[1.68] text-ink">
            你会和一位访谈者聊大约 20 轮。
            TA 不评判、不诊断，只认真听你说，
            偶尔问一个你以前没被问过的问题。
          </p>
        </div>

        <div className="mb-20">
          <p className="font-mono text-[10px] tracking-widest uppercase text-accent mb-6">
            II · 不会看到什么
          </p>
          <p className="fraunces-body text-[19px] leading-[1.68] text-ink">
            不会有进度条、打勾的清单、"您已完成 60%"。
            这是聊天，不是做题。
            你什么时候想停就可以停。
          </p>
        </div>

        <div>
          <p className="font-mono text-[10px] tracking-widest uppercase text-accent mb-6">
            III · 最后会收到
          </p>
          <p className="fraunces-body text-[19px] leading-[1.68] text-ink">
            一页只属于你的信，
            设计上是独一无二的——每种人格类型的信长得完全不同。
            可以自己留着，也可以公开分享。
          </p>
        </div>
      </section>

      {/* Colophon */}
      <footer className="border-t border-rule mt-16 py-12 px-6">
        <div className="max-w-[620px] mx-auto flex flex-col gap-4 md:flex-row md:items-baseline md:justify-between font-mono text-[10px] tracking-widest uppercase text-ink-muted">
          <span>
            OriSelf · Issue 04 · <span className="text-accent">v2.2.3</span>
          </span>
          <span>
            <a
              href="https://github.com/niuniu-869/oriself-next"
              className="hover:text-accent transition-colors"
              target="_blank"
              rel="noopener"
            >
              Open source · Apache 2.0
            </a>
          </span>
        </div>
      </footer>
    </main>
  );
}
