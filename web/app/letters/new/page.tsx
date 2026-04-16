import { redirect } from 'next/navigation';
import { createLetter } from '@/lib/api';

/**
 * /letters/new · creates a letter on the server, then redirects to /letters/:id.
 *
 * This is a Server Component — the create call happens on the server, so no
 * API URL leaks to the client and the redirect is zero-flash.
 */
export const dynamic = 'force-dynamic';

export default async function NewLetterPage() {
  try {
    const letter = await createLetter();
    redirect(`/letters/${letter.letter_id}`);
  } catch (err) {
    // If backend is unreachable, fall back to landing with an error state
    // Actual redirect keeps working via throw.
    if (err instanceof Error && err.message.startsWith('NEXT_REDIRECT')) throw err;
    throw err;
  }
}
