import Link from "next/link";

/**
 * 全局 404 · v2.5.2
 *
 * Next.js 默认那张纯白英文 "This page could not be found." 与整站中文 /
 * paper 主题完全不搭，访客一瞬间就出戏。这里用和首页一致的 Fraunces italic
 * 品牌字配合柔光 paper 背景，保留"一封信走丢了"的语义隐喻。
 */
export default function NotFound() {
  return (
    <main className="relative z-10 min-h-screen flex flex-col items-center justify-center px-6 text-center">
      <p className="font-mono text-[10px] tracking-[0.3em] uppercase text-ink-muted mb-10">
        04 · 信封走丢了
      </p>

      <h1
        className="text-ink mb-10"
        style={{
          fontFamily: "var(--font-serif)",
          fontVariationSettings: '"opsz" 144, "SOFT" 100, "WONK" 1',
          fontStyle: "italic",
          fontWeight: 400,
          fontSize: "clamp(72px, 14vw, 168px)",
          lineHeight: 0.95,
          letterSpacing: "-0.045em",
        }}
      >
        不在这儿
      </h1>

      <p className="fraunces-body-soft italic text-[17px] leading-[1.7] text-ink-soft max-w-[440px] mb-14">
        这条地址像一封没有收件人的信 —— 可能是链接变了、信件被删除了，或者你
        只是打错了一个字。
      </p>

      <Link
        href="/"
        className="fraunces-body-soft italic text-accent text-[18px] border-b border-accent pb-1 transition-colors duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:text-accent-soft hover:border-accent-soft"
      >
        回到 OriSelf →
      </Link>
    </main>
  );
}
