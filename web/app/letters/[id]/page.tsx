import { notFound } from "next/navigation";
import { getLetterState, getLetterTranscript } from "@/lib/api";
import { LetterView } from "./letter-view";

/**
 * /letters/:id · the conversation itself.
 *
 * Server component 同时拉 state（轻量元数据）和 transcript（历史 turns），
 * 让"回看一封信"也能立刻看到完整对话。
 *
 * 失败容忍：transcript 拿不到时降级为空数组，仍能进 view（active 信件
 * 也能继续聊）。
 */
export const dynamic = "force-dynamic";

export default async function LetterPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let state;
  try {
    state = await getLetterState(id);
  } catch {
    notFound();
  }

  let initialTurns = state.turns ?? [];
  let issueSlug: string | null = null;
  try {
    const transcript = await getLetterTranscript(id);
    initialTurns = transcript.turns;
    issueSlug = transcript.issue_slug;
  } catch {
    // transcript 失败不阻断 — view 仍能跑
  }

  return (
    <LetterView
      letterId={id}
      initialState={{ ...state, turns: initialTurns }}
      issueSlug={issueSlug}
    />
  );
}
