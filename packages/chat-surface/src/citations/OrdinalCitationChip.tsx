// PR 1.1-rev2 / 04 — inline chip for model-declared `[[N]]` citation tokens.
//
// Headless renderer. Same architecture as CitationChip: the substrate
// resolves the ordinal → binding lookup; this component just draws the
// chip. The substrate also owns any diagnostics (e.g. citationDebug on
// web) — emitted via the optional `onResolutionChange` callback at the
// wrapper boundary, not inside this file.

import type { ReactElement } from "react";

/**
 * Resolved-ordinal payload the wrapping substrate hands to the chip.
 * `null` means the ordinal was not found (e.g. the model hallucinated
 * `[[99]]`); the chip renders the muted `?` placeholder.
 */
export interface OrdinalResolution {
  /** Synthetic citation id (`tool:<source_tool_call_id>`) for handshakes. */
  readonly citationId: string;
  /** The bound LangGraph tool_call_id. */
  readonly callId: string;
  /** Context-provided click handler, if the substrate wired one. */
  readonly onSelect: (() => void) | null;
}

export interface OrdinalCitationChipProps {
  readonly conversationOrdinal: number;
  /** Substrate-resolved binding; `null` renders the unresolved placeholder. */
  readonly resolved: OrdinalResolution | null;
  /**
   * Per-chip click override. Takes precedence over `resolved.onSelect`.
   * Kept for tests and legacy mounts that need to intercept clicks
   * outside the resolution context.
   */
  readonly onSelect?: (ordinal: number, sourceToolCallId: string) => void;
}

export function OrdinalCitationChip({
  conversationOrdinal,
  resolved,
  onSelect,
}: OrdinalCitationChipProps): ReactElement {
  if (resolved === null) {
    return (
      <span
        className="citation-chip citation-chip--unresolved"
        aria-label={`Unresolved citation [[${conversationOrdinal}]]`}
        title="This citation could not be resolved."
      >
        ?
      </span>
    );
  }
  return (
    <a
      className="citation-chip"
      data-citation-id={resolved.citationId}
      data-conversation-ordinal={String(conversationOrdinal)}
      data-source-tool-call-id={resolved.callId}
      href={`#tool-call-${resolved.callId}`}
      title={`Tool call #${conversationOrdinal}`}
      onClick={(event) => {
        if (onSelect) {
          event.preventDefault();
          onSelect(conversationOrdinal, resolved.callId);
          return;
        }
        if (resolved.onSelect) {
          event.preventDefault();
          resolved.onSelect();
        }
      }}
    >
      {conversationOrdinal}
    </a>
  );
}
