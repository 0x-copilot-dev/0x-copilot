// TcWriteGateRow — a parked write, as ONE line in the chat column. 🎨
//
// The chat column is a feed: it is scanned while the agent is still working, so
// anything taller than a message stops the scroll and buries the reply under
// it. A parked write used to render the full `ask_a_question` card there — the
// title, the reason, the params frame, the ledger id — because a gate had
// nowhere ELSE to put any of it.
//
// It does now. `TcWriteGateCard` renders the payload, the reversibility line
// and the audit trail on the Studio canvas, which is the detail surface a
// staged write has always used and which sat on "no artifact was created" for
// the entire time a decision was pending. With the detail housed, the ask
// collapses to what a decision actually needs: what, where, and two buttons.
//
// Risk is a 6px dot, not a coloured panel. An IRREVERSIBLE write swaps the
// primary action for "Review →": you cannot approve one of those from the feed
// without opening the canvas first. That safety property is only expressible
// once the row is small enough for the button choice to be the loudest thing on
// it.
//
// Kit-only styling; framework-agnostic (no window/document/fetch).

import type { CSSProperties, ReactElement } from "react";

export interface TcWriteGateRowProps {
  /** Verb-first line: "Create an issue in Parth-test". */
  readonly title: string;
  /** Connector slug shown as a quiet trailing label; omitted when unknown. */
  readonly connector: string | null;
  /**
   * True when the write cannot be undone from inside the app (the PDP's
   * `destructive` axis). Swaps the primary action for a canvas trip.
   */
  readonly irreversible: boolean;
  /** Approve — dispatches the parked write. */
  readonly onApprove: () => void;
  /** Decline — the run continues, nothing is written. */
  readonly onDecline: () => void;
  /** Open the detail on the canvas. Also the primary action when irreversible. */
  readonly onReview: () => void;
  /** Disables both actions while a decision is in flight. */
  readonly busy?: boolean;
}

export function TcWriteGateRow({
  title,
  connector,
  irreversible,
  onApprove,
  onDecline,
  onReview,
  busy = false,
}: TcWriteGateRowProps): ReactElement {
  return (
    <div
      className="ui-card"
      data-testid="tc-write-gate-row"
      data-risk={irreversible ? "high" : "normal"}
      style={rowStyle}
    >
      <span
        aria-hidden="true"
        data-testid="tc-write-gate-dot"
        style={dotStyle(irreversible)}
      />
      <span
        className="ui-body"
        data-testid="tc-write-gate-title"
        style={titleStyle}
      >
        {title}
      </span>
      {connector !== null && connector.length > 0 ? (
        <span
          className="ui-mono-caps"
          data-testid="tc-write-gate-connector"
          style={connectorStyle}
        >
          {connector}
        </span>
      ) : null}
      <span style={actionsStyle}>
        {irreversible ? (
          // No approve button at all. A destructive write is not something to
          // click past in a feed — the canvas is where it can be read.
          <button
            type="button"
            className="ui-button ui-button--sm ui-button--primary"
            data-testid="tc-write-gate-review"
            disabled={busy}
            onClick={onReview}
          >
            Review →
          </button>
        ) : (
          <button
            type="button"
            className="ui-button ui-button--sm ui-button--primary"
            data-testid="tc-write-gate-approve"
            disabled={busy}
            onClick={onApprove}
          >
            Approve
          </button>
        )}
        <button
          type="button"
          className="ui-button ui-button--sm"
          data-testid="tc-write-gate-decline"
          disabled={busy}
          onClick={onDecline}
        >
          Decline
        </button>
      </span>
    </div>
  );
}

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-xs, 6px)",
  padding: "var(--space-2xs, 4px) var(--space-2xs, 4px)",
  paddingLeft: "var(--space-sm, 8px)",
  minWidth: 0,
};

const titleStyle: CSSProperties = {
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const connectorStyle: CSSProperties = {
  flex: "0 0 auto",
  color: "var(--color-text-muted, #9aa0aa)",
};

const actionsStyle: CSSProperties = {
  marginLeft: "auto",
  display: "flex",
  gap: "var(--space-2xs, 4px)",
  flex: "0 0 auto",
};

function dotStyle(irreversible: boolean): CSSProperties {
  return {
    flex: "0 0 auto",
    width: "6px",
    height: "6px",
    borderRadius: "50%",
    background: irreversible
      ? "var(--color-danger, #f0764f)"
      : "var(--color-accent, #5fb2ec)",
  };
}
