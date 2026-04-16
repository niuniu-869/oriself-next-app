import { notFound } from 'next/navigation';
import { getLetterState } from '@/lib/api';
import { LetterView } from './letter-view';

/**
 * /letters/:id · the conversation itself.
 *
 * Server component wraps the client component with the initial state to
 * avoid loading flicker.
 */
export const dynamic = 'force-dynamic';

export default async function LetterPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  try {
    const state = await getLetterState(id);
    return <LetterView letterId={id} initialState={state} />;
  } catch {
    notFound();
  }
}
