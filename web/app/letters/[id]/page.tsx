import { notFound } from "next/navigation";
import { getLetterState, getLetterTranscript } from "@/lib/api";
import { LetterView } from "./letter-view";

/**
 * /letters/:id · 对话本体。
 *
 * v2.4 · Server component 拉 state（元数据）+ transcript（历史轮），
 * 让"回看一封信"也能立刻看到完整对话。
 *
 * 失败容忍：transcript 拿不到时降级为空数组，仍能进 view。
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

  let initialTurns: import("@/lib/types").TurnRecord[] = [];
  let issueSlug: string | null = null;
  try {
    const transcript = await getLetterTranscript(id);
    initialTurns = transcript.turns;
    issueSlug = transcript.issue_slug;
  } catch {
    // transcript 失败不阻断
  }

  return (
    <LetterView
      letterId={id}
      initialState={state}
      initialTurns={initialTurns}
      issueSlug={issueSlug}
    />
  );
}
