import { notFound } from "next/navigation";
import { getIssue } from "@/lib/api";
import { IssueChrome } from "@/components/issue/issue-chrome";

/**
 * /issues/:slug · the report page.
 *
 * The LLM-generated HTML is untrusted user-generated content, so we render it
 * in an <iframe sandbox>. The iframe is full-viewport; we layer a thin chrome
 * bar over the bottom for navigation, sharing, and feedback — the report stays
 * the visual centerpiece.
 *
 * Key security:
 *  - sandbox="allow-scripts" (no allow-same-origin = no access to parent)
 *  - src points to /api/issues/:slug/render which returns CSP-sandboxed HTML
 */
export const dynamic = "force-dynamic";

export default async function IssuePage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;

  let meta;
  try {
    meta = await getIssue(slug);
  } catch {
    notFound();
  }

  // The render endpoint returns the full standalone HTML document.
  const renderUrl = `/api/issues/${slug}/render`;

  return (
    <>
      {/* The iframe IS the page. Full viewport, chrome bar layered on top. */}
      <iframe
        src={renderUrl}
        title={meta.title}
        sandbox="allow-scripts"
        className="fixed inset-0 w-full h-full border-0 z-20"
      />

      <IssueChrome
        slug={meta.slug}
        initialIsPublic={meta.is_public}
        letterId={meta.letter_id ?? undefined}
      />

      {/* Hidden heading for accessibility / crawlers */}
      <h1 className="sr-only">{meta.title} · OriSelf Issue</h1>
    </>
  );
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  try {
    const meta = await getIssue(slug);
    return {
      title: `${meta.title} · OriSelf`,
      description: `一封关于 ${meta.mbti_type} 的信。`,
      openGraph: {
        title: meta.title,
        description: `一封关于 ${meta.mbti_type} 的信。`,
      },
    };
  } catch {
    return { title: "OriSelf" };
  }
}
