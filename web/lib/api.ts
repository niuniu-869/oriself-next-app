/**
 * Thin API client · v2.4。
 *
 * 对话轮走 SSE token 流。报告生成走独立 POST。
 * In the browser, requests go through Next's /api/* rewrite.
 * In Server Components, we also use /api/* — fetch() 在 Next 15 里能自动解析 rewrite。
 */

import type {
  FeedbackPayload,
  FeedbackResponse,
  IssueMeta,
  LetterCreateResponse,
  LetterResult,
  LetterState,
  LetterTranscript,
  TurnDonePayload,
  TurnStatus,
} from "./types";

function baseUrl(): string {
  if (typeof window === "undefined") {
    return process.env.API_INTERNAL_URL || "http://localhost:8000";
  }
  return "/api";
}

/**
 * 把后端/上游 provider 的原始错误文本脱敏成一句"人话"。
 *
 * 原则：
 *  - 不回显 provider 名（gemini / openai / qwen / kimi / deepseek / 302.ai 等）
 *  - 不回显 JSON / HTTP 状态码以外的原始 payload
 *  - 根据语义给一句 Oriself 口吻的友好提示
 */
export function friendlyError(raw: unknown): string {
  const msg = raw instanceof Error ? raw.message : String(raw ?? "");
  const low = msg.toLowerCase();
  if (/overload|rate[_\s-]?limit|429|503|cpu overloaded|too many/.test(low)) {
    return "Oriself 这会儿有点喘不过气，稍等一下再试一次。";
  }
  if (/timeout|timed out|504|gateway/.test(low)) {
    return "这一段走得有点慢，像是网在喘气 —— 点一下再试。";
  }
  if (/401|403|unauthorized|forbidden|api[_\s-]?key/.test(low)) {
    return "Oriself 现在联系不上它的笔，稍后再来。";
  }
  if (/5\d\d|internal server error|bad gateway/.test(low)) {
    return "Oriself 走神了 —— 稍等一下，再点一次或「让 Oriself 重写」。";
  }
  if (/network|failed to fetch|econnreset|socket/.test(low)) {
    return "网络断了一下，稍后重试。";
  }
  return "刚才那一笔没递出去 —— 稍等一下再试试。";
}

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${baseUrl()}${path}`;
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    // 原文保留到一条给控制台的诊断日志里，用户看到的 Error 消息走脱敏。
    if (typeof window !== "undefined" && typeof console !== "undefined") {
      console.warn(
        `[oriself api] ${res.status} ${res.statusText} @ ${path} :: ${(text || "").slice(0, 400)}`,
      );
    }
    throw new Error(friendlyError(`${res.status} ${text}`));
  }
  return res.json() as Promise<T>;
}

// ───── Letters ─────

export async function createLetter(
  provider?: string,
  domain = "mbti",
): Promise<LetterCreateResponse> {
  return jsonFetch("/letters", {
    method: "POST",
    body: JSON.stringify({ provider: provider ?? undefined, domain }),
  });
}

export async function getLetterState(letterId: string): Promise<LetterState> {
  return jsonFetch(`/letters/${letterId}/state`);
}

export async function getLetterTranscript(
  letterId: string,
): Promise<LetterTranscript> {
  return jsonFetch(`/letters/${letterId}/transcript`);
}

export interface TurnStreamOptions {
  onToken?: (delta: string) => void;
  onError?: (message: string) => void;
  /** token 流之前一次性给出的铅笔批注（0..2 条）。 */
  onQuill?: (lines: string[]) => void;
  signal?: AbortSignal;
}

/**
 * 流式对话 · SSE token 透传 + 结束时给 DonePayload。
 *
 * 事件：
 *  - event: quill   { lines: string[] }      // token 之前，可选
 *  - event: token   { delta: string }
 *  - event: done    { round, status, visible }
 *  - event: error   { message }
 */
export async function sendTurnStream(
  letterId: string,
  userMessage: string,
  opts: TurnStreamOptions = {},
): Promise<TurnDonePayload> {
  return streamToDone(
    `${baseUrl()}/letters/${letterId}/turn`,
    JSON.stringify({ user_message: userMessage }),
    opts,
  );
}

/** 重写最近一轮 · 同样走 SSE。 */
export async function rewriteLastTurn(
  letterId: string,
  opts: TurnStreamOptions & { hint?: string } = {},
): Promise<TurnDonePayload> {
  const { hint, ...rest } = opts;
  return streamToDone(
    `${baseUrl()}/letters/${letterId}/turn/rewrite`,
    JSON.stringify({ hint: hint ?? null }),
    rest,
  );
}

async function streamToDone(
  url: string,
  body: string,
  opts: TurnStreamOptions,
): Promise<TurnDonePayload> {
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body,
    cache: "no-store",
    signal: opts.signal,
  });

  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    if (typeof window !== "undefined" && typeof console !== "undefined") {
      console.warn(
        `[oriself stream] ${res.status} ${res.statusText} :: ${(text || "").slice(0, 400)}`,
      );
    }
    throw new Error(friendlyError(`${res.status} ${text}`));
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let done: TurnDonePayload | null = null;
  let errorMsg: string | null = null;

  const handleFrame = (frame: string) => {
    const lines = frame.split(/\r?\n/);
    let evtName = "message";
    const dataLines: string[] = [];
    for (const line of lines) {
      if (!line || line.startsWith(":")) continue;
      if (line.startsWith("event:")) {
        evtName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    if (dataLines.length === 0) return;
    let payload: unknown;
    try {
      payload = JSON.parse(dataLines.join("\n"));
    } catch {
      return;
    }
    if (evtName === "token") {
      const delta = (payload as { delta?: string })?.delta ?? "";
      if (delta) opts.onToken?.(delta);
    } else if (evtName === "quill") {
      const lines = (payload as { lines?: unknown })?.lines;
      if (Array.isArray(lines)) {
        const safe = lines.filter((x): x is string => typeof x === "string");
        if (safe.length > 0) opts.onQuill?.(safe);
      }
    } else if (evtName === "done") {
      done = payload as TurnDonePayload;
    } else if (evtName === "error") {
      const p = payload as { message?: string };
      const raw = p?.message ?? "stream error";
      if (typeof window !== "undefined" && typeof console !== "undefined") {
        console.warn(`[oriself stream] error frame :: ${raw}`);
      }
      errorMsg = friendlyError(raw);
      opts.onError?.(errorMsg);
    }
  };

  while (true) {
    const { done: streamDone, value } = await reader.read();
    if (streamDone) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      if (frame.trim()) handleFrame(frame);
    }
  }
  if (buffer.trim()) handleFrame(buffer);

  if (errorMsg) throw new Error(errorMsg);
  if (!done) throw new Error(friendlyError("stream ended without done event"));
  return done;
}

/** 触发报告生成 · converge 后调用。已生成则返回现成结果；没生成则跑 compose。 */
export async function composeResult(letterId: string): Promise<LetterResult> {
  return jsonFetch(`/letters/${letterId}/result`, { method: "POST" });
}

// 兼容别名（旧 import 名）
export const getResult = composeResult;

// ───── Issues ─────

export async function getIssue(slug: string): Promise<IssueMeta> {
  return jsonFetch(`/issues/${slug}`);
}

export async function publishIssue(
  slug: string,
  isPublic: boolean,
): Promise<IssueMeta> {
  return jsonFetch(`/issues/${slug}/publish`, {
    method: "PATCH",
    body: JSON.stringify({ is_public: isPublic }),
  });
}

// ───── Feedback ─────

export async function submitFeedback(
  payload: FeedbackPayload,
): Promise<FeedbackResponse> {
  return jsonFetch("/feedback", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
