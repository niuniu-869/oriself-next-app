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

export interface LetterAction {
  action: 'onboarding' | 'warmup' | 'explore' | 'reflect' | 'warm_echo' | 'deepen' | 'soft_close' | 'converge';
  dimension_targeted?: string;
  next_question?: string;
  echo?: string;
  text?: string;  // fallback
  evidence?: Evidence[];
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
  speaker: 'oriself' | 'you';
  text: string;
  round: number;
}

export interface LetterState {
  letter_id: string;
  round_count: number;
  status: 'active' | 'completed' | 'failed';
  evidence_count_per_dim: Record<string, number>;
  turns?: TurnRecord[];  // populated by the wrapper from /letters/:id (future)
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
}
