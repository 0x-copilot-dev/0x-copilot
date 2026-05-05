/**
 * PR 4.5 — Composes the share message body for Slack / email deep-links.
 *
 * Pure function. Falls back to a generic title when none is supplied so the
 * popover always has something to send.
 */

const FALLBACK_TITLE = "Atlas conversation";

export function useShareLinkText(input: {
  chatTitle: string | null | undefined;
  chatUrl: string;
}): { title: string; body: string } {
  const title = input.chatTitle?.trim()
    ? input.chatTitle.trim()
    : FALLBACK_TITLE;
  const body = `Atlas — ${title}\n${input.chatUrl}`;
  return { title, body };
}
