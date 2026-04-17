/**
 * Shared types between server (FastAPI) and web (Next.js).
 *
 * These should eventually be generated from the FastAPI OpenAPI schema via
 * openapi-typescript. For now, hand-written — keep in sync with
 * server/oriself_server/schemas.py and routes/*.py.
 */

export interface LetterCreateResponse {
  letter_id: string;
  provider: string;
  domain: string;
  skill_version: string;
}

export interface Evidence {
  dimension: string;
  user_quote: string;
  round_number: number;
  confidence: number;
  interpretation?: string;
}

export type LetterActionType =
  | "onboarding"
  | "warm_echo"
  | "ask"
  | "reflect"
  | "scenario_quiz"
  | "probe_contradiction"
  | "redirect"
  | "midpoint_reflect"
  | "soft_closing"
  | "converge";

export interface LetterAction {
  action: LetterActionType;
  dimension_targeted?: string;
  /** v2.3 · 唯一可见文本字段。runner 会保证非空（converge 除外）。 */
  next_prompt?: string;
  evidence?: Evidence[];
  // 兼容字段（旧 mock / 兜底）
  next_question?: string;
  echo?: string;
  text?: string;
}

export interface TurnResponse {
  round_number: number;
  action: LetterAction;
  used_fallback: boolean;
  retries: number;
  guardrail_reasons: string[];
}

/** Client-side turn record (normalized for rendering). */
export interface TurnRecord {
  speaker: "oriself" | "you";
  text: string;
  round: number;
}

export interface LetterState {
  letter_id: string;
  round_count: number;
  status: "active" | "completed" | "failed";
  evidence_count_per_dim: Record<string, number>;
  turns?: TurnRecord[]; // populated by the wrapper from /letters/:id (future)
}

export interface LetterResult {
  letter_id: string;
  mbti_type: string;
  insight_paragraphs: Array<{ title: string; text: string }>;
  card: Record<string, unknown>;
  issue_slug: string | null;
}

export interface IssueMeta {
  slug: string;
  title: string;
  mbti_type: string;
  is_public: boolean;
  created_at: string;
  letter_id?: string | null;
}

export interface FeedbackPayload {
  text: string;
  rating?: number;
  letter_id?: string;
  issue_slug?: string;
  contact?: string;
}

export interface FeedbackResponse {
  id: number;
  created_at: string;
}
