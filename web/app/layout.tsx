import type { Metadata } from "next";
import {
  Fraunces,
  Instrument_Sans,
  JetBrains_Mono,
  Noto_Serif_SC,
} from "next/font/google";
import { CustomCursor } from "@/components/primitives/custom-cursor";
import { AuthorBadge } from "@/components/primitives/author-badge";
import "./globals.css";

const fraunces = Fraunces({
  subsets: ["latin"],
  variable: "--font-fraunces",
  display: "swap",
  axes: ["SOFT", "WONK", "opsz"],
  style: ["normal", "italic"],
});

const instrument = Instrument_Sans({
  subsets: ["latin"],
  variable: "--font-instrument",
  display: "swap",
});

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains",
  display: "swap",
});

const notoSerifSC = Noto_Serif_SC({
  subsets: ["latin"],
  variable: "--font-noto-serif-sc",
  display: "swap",
  weight: ["300", "400", "500"],
});

export const metadata: Metadata = {
  title: "OriSelf",
  description: "一份对话式人格画像 · 中文 · 2026",
  icons: {
    icon: "/favicon.ico",
  },
  openGraph: {
    title: "OriSelf",
    description: "一份对话式人格画像 · 中文 · 2026",
    type: "website",
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
        <AuthorBadge />
        <CustomCursor />
      </body>
    </html>
  );
}
