// <PagePreview /> — rendered markdown for a Library page.
//
// Source:
//   docs/atlas-new-design/destinations/library-prd.md §3.4.2 (page
//     detail body — rendered markdown using the same renderer chat
//     messages use). Streamdown is the existing dep in chat-surface
//     (`packages/chat-surface/package.json` peerDep `streamdown`).
//
// Invariants:
//   - **Pure presentation.** Receives markdown via props; never
//     fetches.
//   - **Streamdown only.** No new markdown library — reuse the chat
//     surface's existing renderer (cross-audit §1.6 SP-1 single-
//     source-of-truth: one markdown renderer across destinations).
//   - `mode="static"` for fully-loaded pages; `mode="streaming"` is
//     supported for the very rare case where the page body is being
//     streamed in (e.g. a slow signed-URL fetch from object store).

import type { CSSProperties, ReactElement } from "react";
import { Streamdown } from "streamdown";

export interface PagePreviewProps {
  readonly markdown: string;
  /** Defaults to "static". */
  readonly mode?: "static" | "streaming";
  /** Optional className for host-level overrides. */
  readonly className?: string;
}

const wrapperStyle: CSSProperties = {
  padding: 24,
  borderRadius: 10,
  border: "1px solid var(--color-border)",
  background: "var(--color-surface)",
  color: "var(--color-text)",
  fontSize: 14,
  lineHeight: 1.6,
  minHeight: 240,
  overflow: "auto",
  // Anchor target for in-page navigation (headings auto-id'd by
  // Streamdown — library-prd §3.4.2).
  scrollPaddingTop: 16,
};

const emptyStyle: CSSProperties = {
  ...wrapperStyle,
  color: "var(--color-text-subtle)",
  fontStyle: "italic",
};

export function PagePreview({
  markdown,
  mode = "static",
  className,
}: PagePreviewProps): ReactElement {
  if (markdown.length === 0) {
    return (
      <div
        style={emptyStyle}
        className={className}
        data-testid="library-page-preview"
        data-state="empty"
      >
        This page is empty.
      </div>
    );
  }

  return (
    <div
      style={wrapperStyle}
      className={className}
      data-testid="library-page-preview"
      data-state="ready"
      data-mode={mode}
    >
      <Streamdown mode={mode}>{markdown}</Streamdown>
    </div>
  );
}
