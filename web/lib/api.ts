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
  IssueMeta,
  LetterCreateResponse,
  LetterResult,
  LetterState,
  TurnResponse,
} from './types';

function baseUrl(): string {
  // Server-side: use internal Docker URL. Client-side: relative /api path.
  if (typeof window === 'undefined') {
    return process.env.API_INTERNAL_URL || 'http://localhost:8000';
  }
  return '/api';
}

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${baseUrl()}${path}`;
  const res = await fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    cache: 'no-store',
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}: ${text || path}`);
  }
  return res.json() as Promise<T>;
}

// ───── Letters ─────

export async function createLetter(
  provider?: string,
  domain = 'mbti'
): Promise<LetterCreateResponse> {
  return jsonFetch('/letters', {
    method: 'POST',
    body: JSON.stringify({ provider: provider ?? undefined, domain }),
  });
}

export async function getLetterState(letterId: string): Promise<LetterState> {
  return jsonFetch(`/letters/${letterId}/state`);
}

export async function sendTurn(
  letterId: string,
  userMessage: string
): Promise<TurnResponse> {
  return jsonFetch(`/letters/${letterId}/turn`, {
    method: 'POST',
    body: JSON.stringify({ user_message: userMessage }),
  });
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
  isPublic: boolean
): Promise<IssueMeta> {
  return jsonFetch(`/issues/${slug}/publish`, {
    method: 'PATCH',
    body: JSON.stringify({ is_public: isPublic }),
  });
}
