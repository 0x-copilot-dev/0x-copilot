/**
 * Derive a conversation's display title from the first thing the user typed.
 *
 * ONE COPY, BECAUSE THERE WERE ALREADY TWO AND THEY HAD DRIFTED.
 * `apps/frontend`'s `titleFromPrompt` cut at 44 and appended `...`;
 * `apps/desktop`'s `firstRunTitle` cut at 60 and appended nothing — while its
 * comment claimed it "matches the web `titleFromPrompt` heuristic". It did not.
 * Same failure as `modelCatalog`: neither copy was wrong in isolation, the
 * duplication was the bug.
 *
 * The desktop cut is what reached the window header as
 * "Use the web_search tool exactly once to find the official Py" — 60 bytes,
 * mid-word, with nothing to say it had been cut. Measured in the live app:
 * `scrollWidth === clientWidth`, so no CSS ellipsis was involved; the string
 * really was 60 characters by the time it was stored.
 *
 * `services/ai-backend` derives the same title server-side when a conversation
 * reaches its first message with none set (`derive_conversation_title`). The
 * rules below are mirrored there and the two test suites pin the same cases —
 * change both together, exactly as the SIWE message template requires.
 */

/** Longest title we keep. Past this the tail carries no information in a
 *  header that is itself ellipsized by CSS. */
const MAX_TITLE_LENGTH = 60;

/** Below this a word-boundary cut removes more than it saves, so we cut hard
 *  and let the ellipsis do the talking. */
const MIN_WORD_BOUNDARY = 24;

/**
 * @param prompt Raw user input. Newlines and runs of whitespace collapse — a
 *   pasted multi-line prompt otherwise stores its line breaks into a
 *   single-line header.
 * @param fallback Used when the prompt is empty (an attachment-only send).
 */
export function conversationTitleFromPrompt(
  prompt: string,
  fallback = "New chat",
): string {
  const normalized = prompt.replace(/\s+/g, " ").trim();
  if (normalized === "") {
    return fallback;
  }
  if (normalized.length <= MAX_TITLE_LENGTH) {
    return normalized;
  }
  // Cut on a word boundary so the title ends on a word rather than mid-token,
  // then mark it — an unmarked cut reads as a rendering bug, which is exactly
  // how the 60-char one was reported.
  const clipped = normalized.slice(0, MAX_TITLE_LENGTH);
  const lastSpace = clipped.lastIndexOf(" ");
  const body =
    lastSpace >= MIN_WORD_BOUNDARY ? clipped.slice(0, lastSpace) : clipped;
  // Trailing punctuation before an ellipsis reads as a typo (`the official,…`).
  return `${body.replace(/[\s,;:.!?-]+$/, "")}…`;
}
