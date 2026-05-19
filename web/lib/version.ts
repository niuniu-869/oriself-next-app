/**
 * 应用版本号 · 单一事实源。
 *
 * 实际数字写在 web/package.json 的 "version" 字段；这里只做一次性转发，
 * 让页面按 `APP_VERSION` 引用，而不是各处散落硬编码的版本字符串。
 * 升级版本时只改 package.json 一处即可（tsconfig 已开 resolveJsonModule）。
 */
import pkg from "@/package.json";

export const APP_VERSION: string = pkg.version;
