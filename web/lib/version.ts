/**
 * 版本号工具。
 *
 * 前端构建版本(APP_VERSION)= web/package.json 的 "version",单一事实源,
 * 升级只改 package.json 一处(tsconfig 已开 resolveJsonModule)。
 *
 * 后端版本(getServerVersion)实时拉自后端 /health,只在 Server Component 里调,
 * 让页脚反映「线上真正在跑的后端版本」,而不是构建期写死的数字。
 */
import pkg from "@/package.json";

export const APP_VERSION: string = pkg.version;

/**
 * 拉后端 /health 的实时版本号。仅服务端调用。
 *
 * 后端没起 / 超时 / 形态不对时返回 null —— 调用方据此回退到只显示前端版本。
 * 结果缓存 60s(版本号几乎不变),避免每次首页渲染都打一次后端。
 */
export async function getServerVersion(): Promise<string | null> {
  const base = process.env.API_INTERNAL_URL || "http://localhost:8000";
  try {
    const res = await fetch(`${base}/health`, {
      next: { revalidate: 60 },
      signal: AbortSignal.timeout(2000),
    });
    if (!res.ok) return null;
    const data: unknown = await res.json();
    const version = (data as { version?: unknown }).version;
    return typeof version === "string" ? version : null;
  } catch {
    return null;
  }
}
