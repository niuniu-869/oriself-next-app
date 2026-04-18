/**
 * 前后端共享类型 · v2.4。
 *
 * 对话轮不再是 JSON；只有报告生成的一步保留 schema。
 */

export interface LetterCreateResponse {
  letter_id: string;
  provider: string;
  domain: string;
  skill_version: string;
}

/** 每轮 SSE done 事件的 payload。 */
export type TurnStatus = "CONTINUE" | "CONVERGE" | "NEED_USER";

export interface TurnDonePayload {
  round: number;
  status: TurnStatus;
  visible: string;
}

/** 对话轮记录 · 前端渲染用（服务端 transcript 已剥除 STATUS）。 */
export interface TurnRecord {
  speaker: "oriself" | "you";
  text: string;
  round: number;
}

export interface LetterState {
  letter_id: string;
  round_count: number;
  status: "active" | "completed" | "failed";
  last_status?: TurnStatus;
  has_report: boolean;
  issue_slug?: string | null;
}

/** /letters/{id}/transcript */
export interface LetterTranscript {
  letter_id: string;
  status: "active" | "completed" | "failed";
  turns: TurnRecord[];
  issue_slug: string | null;
}

/** /letters/{id}/result */
export interface LetterResult {
  letter_id: string;
  mbti_type: string;
  insight_paragraphs: Array<{ theme: string; body: string; quoted_rounds: number[] }>;
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
