import type { Metadata } from 'next';
import { Fraunces, Instrument_Sans, JetBrains_Mono, Noto_Serif_SC } from 'next/font/google';
import { CustomCursor } from '@/components/primitives/custom-cursor';
import './globals.css';

const fraunces = Fraunces({
  subsets: ['latin'],
  variable: '--font-fraunces',
  display: 'swap',
  axes: ['SOFT', 'WONK', 'opsz'],
  style: ['normal', 'italic'],
});

const instrument = Instrument_Sans({
  subsets: ['latin'],
  variable: '--font-instrument',
  display: 'swap',
});

const jetbrains = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-jetbrains',
  display: 'swap',
});

const notoSerifSC = Noto_Serif_SC({
  subsets: ['latin'],
  variable: '--font-noto-serif-sc',
  display: 'swap',
  weight: ['300', '400', '500'],
});

export const metadata: Metadata = {
  title: 'OriSelf · 一封关于你的信',
  description:
    '不是测试系统，是陪聊的朋友。20 轮自然对话，交付 MBTI + 个性化洞见 + 独一无二的 editorial 报告。',
  icons: {
    icon: '/favicon.ico',
  },
  openGraph: {
    title: 'OriSelf',
    description: '在这里，你会被认认真真听一次。',
    type: 'website',
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="zh"
      className={`${fraunces.variable} ${instrument.variable} ${jetbrains.variable} ${notoSerifSC.variable}`}
    >
      <body>
        {children}
        <CustomCursor />
      </body>
    </html>
  );
}
