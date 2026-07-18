import { useCallback, type CSSProperties, type ReactElement } from "react";
import type { TodoExtractionId } from "@0x-copilot/api-types";

// Canonical brand site is `@0x-copilot/api-types/brands.ts`
// (P3-A2 extractor publisher writes the wire shape). Re-exported so
// existing `from "../extraction-banner"` imports keep working.
export type { TodoExtractionId };
export type TodoPriority = "low" | "med" | "high";

export interface ProposedTodo {
  readonly text: string;
  readonly priority: TodoPriority;
  readonly due?: string;
  readonly excerpt?: string;
}

export interface TodoExtraction {
  readonly id: TodoExtractionId;
  readonly source: { readonly thread_id: string; readonly run_id: string };
  /** Optional, but the banner renders a more useful one-liner when present. */
  readonly source_title?: string;
  readonly proposed_todos: ReadonlyArray<ProposedTodo>;
  readonly status: "pending" | "accepted" | "rejected" | "snoozed";
  readonly created_at: string;
}

export interface ExtractionBannerProps {
  /**
   * Pending extractions to display. Caller is responsible for filtering
   * to `status === "pending"` and ordering (typically `created_at desc`).
   * Empty array → component renders nothing (returns `null`).
   */
  readonly extractions: ReadonlyArray<TodoExtraction>;
  /** Accept a single proposal — atomically inserts the todo. */
  readonly onAccept: (id: TodoExtractionId) => void;
  /** Reject a single proposal — never re-proposed. */
  readonly onReject: (id: TodoExtractionId) => void;
  /** Accept every visible proposal at once. */
  readonly onAcceptAll: () => void;
  /** View-local dismiss; the extraction stays `pending` server-side. */
  readonly onDismiss: () => void;
}

const PANEL_BACKGROUND = "var(--color-surface)";
const PANEL_BORDER = "var(--color-border)";
const PANEL_BORDER_STRONG = "var(--color-border-strong)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const TEXT_FAINT = "var(--color-text-subtle)";
const ACCENT = "var(--color-accent)";
const DANGER = "var(--color-danger)";

function totalProposals(extractions: ReadonlyArray<TodoExtraction>): number {
  let count = 0;
  for (const e of extractions) count += e.proposed_todos.length;
  return count;
}

function ProposalRow({
  extraction,
  onAccept,
  onReject,
}: {
  extraction: TodoExtraction;
  onAccept: (id: TodoExtractionId) => void;
  onReject: (id: TodoExtractionId) => void;
}): ReactElement {
  const head = extraction.proposed_todos[0];
  const remainder = Math.max(0, extraction.proposed_todos.length - 1);

  const rowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: "10px 12px",
    border: `1px solid ${PANEL_BORDER}`,
    borderRadius: 8,
    backgroundColor: PANEL_BACKGROUND,
    color: TEXT_PRIMARY,
    boxSizing: "border-box",
  };
  const textWrap: CSSProperties = {
    flex: 1,
    minWidth: 0,
    display: "flex",
    flexDirection: "column",
    gap: 2,
  };
  const titleStyle: CSSProperties = {
    fontSize: "var(--font-size-sm)",
    fontWeight: 600,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const subStyle: CSSProperties = {
    fontSize: "var(--font-size-xs)",
    color: TEXT_FAINT,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const actionGroup: CSSProperties = { display: "flex", gap: 6 };
  const acceptStyle: CSSProperties = {
    height: 26,
    padding: "0 10px",
    borderRadius: 6,
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    backgroundColor: "transparent",
    color: ACCENT,
    fontSize: "var(--font-size-xs)",
    fontWeight: 600,
    cursor: "pointer",
  };
  const rejectStyle: CSSProperties = {
    ...acceptStyle,
    color: DANGER,
  };

  const previewText = head !== undefined ? head.text : "(no preview)";
  const previewMeta: string[] = [];
  if (head?.due !== undefined) previewMeta.push(head.due);
  if (extraction.source_title !== undefined)
    previewMeta.push(`from ${extraction.source_title}`);
  if (remainder > 0) previewMeta.push(`+${remainder} more`);

  return (
    <div
      style={rowStyle}
      data-testid="extraction-row"
      data-extraction-id={extraction.id}
    >
      <div style={textWrap}>
        <span style={titleStyle} data-testid="extraction-row-text">
          {previewText}
        </span>
        {previewMeta.length > 0 ? (
          <span style={subStyle} data-testid="extraction-row-meta">
            {previewMeta.join(" · ")}
          </span>
        ) : null}
      </div>
      <div style={actionGroup}>
        <button
          type="button"
          onClick={() => onAccept(extraction.id)}
          style={acceptStyle}
          data-testid="extraction-row-accept"
          aria-label={`Accept extraction ${extraction.id}`}
        >
          Accept
        </button>
        <button
          type="button"
          onClick={() => onReject(extraction.id)}
          style={rejectStyle}
          data-testid="extraction-row-reject"
          aria-label={`Reject extraction ${extraction.id}`}
        >
          Reject
        </button>
      </div>
    </div>
  );
}

export function ExtractionBanner({
  extractions,
  onAccept,
  onReject,
  onAcceptAll,
  onDismiss,
}: ExtractionBannerProps): ReactElement | null {
  // Stable callbacks so caller-supplied handlers don't churn the DOM.
  const handleAccept = useCallback(
    (id: TodoExtractionId) => onAccept(id),
    [onAccept],
  );
  const handleReject = useCallback(
    (id: TodoExtractionId) => onReject(id),
    [onReject],
  );

  if (extractions.length === 0) return null;

  const total = totalProposals(extractions);

  const wrapper: CSSProperties = {
    border: `1px solid ${PANEL_BORDER_STRONG}`,
    borderRadius: 12,
    backgroundColor: PANEL_BACKGROUND,
    padding: 14,
    display: "flex",
    flexDirection: "column",
    gap: 10,
    color: TEXT_PRIMARY,
    boxSizing: "border-box",
  };
  const headerRow: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 12,
  };
  const titleStyle: CSSProperties = {
    flex: 1,
    minWidth: 0,
    fontSize: "var(--font-size-md)",
    fontWeight: 600,
  };
  const subStyle: CSSProperties = {
    fontSize: "var(--font-size-xs)",
    color: TEXT_SECONDARY,
  };
  const acceptAllStyle: CSSProperties = {
    height: 28,
    padding: "0 12px",
    borderRadius: 6,
    border: `1px solid ${ACCENT}`,
    backgroundColor: "transparent",
    color: ACCENT,
    fontSize: "var(--font-size-sm)",
    fontWeight: 600,
    cursor: "pointer",
  };
  const dismissStyle: CSSProperties = {
    height: 28,
    padding: "0 8px",
    borderRadius: 6,
    border: "none",
    backgroundColor: "transparent",
    color: TEXT_FAINT,
    fontSize: "var(--font-size-xl)",
    lineHeight: "1",
    cursor: "pointer",
  };
  const listStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };

  const titleText =
    total === 1
      ? "Copilot found 1 possible todo from your last chat"
      : `Atlas found ${total} possible todos from your last chat`;

  return (
    <section
      role="region"
      aria-label={
        total === 1
          ? "1 proposed todo from Copilot"
          : `${total} proposed todos from Atlas`
      }
      data-testid="extraction-banner"
      data-extraction-count={extractions.length}
      data-proposal-count={total}
      style={wrapper}
    >
      <div style={headerRow}>
        <div style={titleStyle}>
          <div>{titleText}</div>
          <div style={subStyle}>Review &amp; add, or dismiss for now.</div>
        </div>
        <button
          type="button"
          onClick={onAcceptAll}
          style={acceptAllStyle}
          data-testid="extraction-banner-accept-all"
          aria-label="Accept all proposed todos"
        >
          Accept all
        </button>
        <button
          type="button"
          onClick={onDismiss}
          style={dismissStyle}
          data-testid="extraction-banner-dismiss"
          aria-label="Dismiss extraction banner"
        >
          {"×"}
        </button>
      </div>
      <div style={listStyle} data-testid="extraction-banner-list">
        {extractions.map((extraction) => (
          <ProposalRow
            key={extraction.id}
            extraction={extraction}
            onAccept={handleAccept}
            onReject={handleReject}
          />
        ))}
      </div>
    </section>
  );
}
