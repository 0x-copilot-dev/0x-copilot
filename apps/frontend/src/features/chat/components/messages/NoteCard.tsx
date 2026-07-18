// PR A1 / F1 — context-compression note ("Atlas summarised N older
// messages to keep this conversation efficient.").
//
// Renders as the design's quiet inline acknowledgement — uses the
// shared `<StatusLine>` primitive (design-system, PR 8.0.1) so the
// rhythm matches `Got it. Drafting customer-led.` and similar inline
// notes. No card chrome; the design treats compression as a footnote
// to the conversation, not an event the user needs to act on.
//
// Wired to envelope `compression_note` (`activity_kind === "note"`)
// projected by `RuntimeEventPresentationProjector`. Payload supplies
// a human summary; we fall back to a generic line when missing.

import { StatusLine } from "@0x-copilot/design-system";
import type { ReactElement } from "react";

export interface NoteCardProps {
  summary?: string | null;
  beforeTokens?: number | null;
  afterTokens?: number | null;
  strategy?: string | null;
}

export function NoteCard({
  summary,
  beforeTokens,
  afterTokens,
  strategy,
}: NoteCardProps): ReactElement {
  const text =
    summary?.trim() ||
    deriveSummary({ beforeTokens, afterTokens, strategy }) ||
    "Copilot summarised older messages to keep this conversation efficient.";
  return <StatusLine className="aui-note-card">{text}</StatusLine>;
}

function deriveSummary(input: {
  beforeTokens?: number | null;
  afterTokens?: number | null;
  strategy?: string | null;
}): string | null {
  const before = input.beforeTokens;
  const after = input.afterTokens;
  if (
    typeof before === "number" &&
    typeof after === "number" &&
    before > after
  ) {
    const saved = before - after;
    return `Atlas summarised ~${saved.toLocaleString()} tokens of older context to keep this conversation efficient.`;
  }
  return null;
}
