/**
 * Thin API client.
 *
 * In the browser, requests go through Next's /api/* rewrite (set in
 * next.config.mjs), which proxies to the backend via API_INTERNAL_URL.
 * This keeps the backend URL out of the client bundle.
 *
 * In Server Components, we also use /api/* — fetch() on the server resolves
 * the rewrite correctly in Next 15.
 */

import type {
  FeedbackPayload,
  FeedbackResponse,
  IssueMeta,
  LetterCreateResponse,
  LetterResult,
  LetterState,
  TurnResponse,
} from "./types";

function baseUrl(): string {
  // Server-side: use internal Docker URL. Client-side: relative /api path.
  if (typeof window === "undefined") {
    return process.env.API_INTERNAL_URL || "http://localhost:8000";
  }
  return "/api";
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
    throw new Error(`${res.status} ${res.statusText}: ${text || path}`);
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

export async function sendTurn(
  letterId: string,
  userMessage: string,
): Promise<TurnResponse> {
  return jsonFetch(`/letters/${letterId}/turn`, {
    method: "POST",
    body: JSON.stringify({ user_message: userMessage }),
  });
}

/**
 * 后端推给前端的阶段事件。对应 `server/routes/letters.py` 的 `on_phase`。
 *
 * - listening：在理解用户这一轮输入、装配上下文
 * - thinking：LLM 正在生成（耗时最大的一段）
 * - validating：JSON 返回了，guardrails 校验中
 * - retrying：一次尝试失败，准备重试
 * - composed：成功，action 已经捏好
 * - fallback：最终走降级
 */
export type TurnStreamPhase =
  | "listening"
  | "thinking"
  | "validating"
  | "retrying"
  | "composed"
  | "fallback";

export interface TurnStreamPhaseEvent {
  phase: TurnStreamPhase;
  attempt?: number;
  reason?: string;
  round?: number;
  phase_key?: string;
  is_converge?: boolean;
  action_type?: string;
  dimension_targeted?: string | null;
  evidence_count?: number;
  reasons?: string[];
}

export interface TurnStreamOptions {
  onPhase?: (evt: TurnStreamPhaseEvent) => void;
  signal?: AbortSignal;
}

/**
 * 流式版 sendTurn — SSE 协议。
 *
 * 订阅阶段事件，最终 resolve 成完整 TurnResponse（与 /turn 接口一致）。
 * 若后端推 `event: error`，抛出。
 */
export async function sendTurnStream(
  letterId: string,
  userMessage: string,
  opts: TurnStreamOptions = {},
): Promise<TurnResponse> {
  const res = await fetch(`${baseUrl()}/letters/${letterId}/turn/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ user_message: userMessage }),
    cache: "no-store",
    signal: opts.signal,
  });

  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `${res.status} ${res.statusText}: ${text || "stream not available"}`,
    );
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let final: TurnResponse | null = null;
  let errorMsg: string | null = null;

  // SSE 分帧：\n\n 作帧分隔符。帧内逐行解析 event: / data:（忽略 : 开头的注释行）。
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
      return; // 损坏帧忽略
    }

    if (evtName === "phase") {
      opts.onPhase?.(payload as TurnStreamPhaseEvent);
    } else if (evtName === "final") {
      final = payload as TurnResponse;
    } else if (evtName === "error") {
      const p = payload as { message?: string };
      errorMsg = p?.message ?? "stream error";
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // 按 \n\n 切帧；保留未完成的尾巴在 buffer 里
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      if (frame.trim()) handleFrame(frame);
    }
  }
  // flush 结尾
  if (buffer.trim()) handleFrame(buffer);

  if (errorMsg) throw new Error(errorMsg);
  if (!final) throw new Error("stream ended without final event");
  return final;
}

export async function getResult(letterId: string): Promise<LetterResult> {
  return jsonFetch(`/letters/${letterId}/result`);
}

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
