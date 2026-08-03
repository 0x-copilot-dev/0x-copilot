// TcWriteGateCard — the parked write, on the Studio canvas. 🎨
//
// Two things brought this into existence.
//
// 1. The canvas is the detail surface, and it was idle exactly when it was
//    needed. A staged write already opens there (`pendingCardsProjection` gives
//    it a `surfaceId`); a gate got `surfaceId: null`, so while a write was
//    parked the widest pane in the app showed "Answered in chat · no artifact
//    was created" and every scrap of detail had to be crammed into the chat
//    column.
//
// 2. A write gate on the canvas rendered the CONNECT card. Both gate kinds ride
//    the same `gate.opened` event, so `ledger.openGates` carries both, and a
//    write gate reports `auth_state: insufficient` — which `TcGateCard` labels
//    "More access needed" above a **Connect** button, for a connector that is
//    already connected. `_auth_state` picks that value deliberately (a write
//    gate has no credential problem to report, and defaulting to `missing`
//    would put a falsehood in a compliance record); the defect was rendering
//    it through the OAuth card.
//
// The payload comes from the APPROVAL, not the ledger row: `gate.opened` is a
// durable compliance record and carries no tool arguments by design, so the
// arguments a reviewer needs to judge the write can only come from the
// interrupt the approval was projected from.
//
// Kit-only styling; framework-agnostic (no window/document/fetch).

import type { CSSProperties, ReactElement } from "react";

import type { ActivityParam } from "../approvals/types";

export interface TcWriteGateCardProps {
  /** Verb-first line: "Create an issue in Parth-test". */
  readonly title: string;
  /** Connector slug; omitted from the eyebrow when unknown. */
  readonly connector: string | null;
  /** The call's arguments, already projected + sanitised for display. */
  readonly params: readonly ActivityParam[];
  /** `r<short>·<seq>` — the audit anchor this decision will be recorded under. */
  readonly ledgerId: string;
  /** True when the write cannot be undone from inside the app. */
  readonly irreversible: boolean;
  readonly onApprove: () => void;
  readonly onDecline: () => void;
  readonly busy?: boolean;
}

const REVERSIBLE_COPY = "You can undo this from the connector if it's wrong.";
const IRREVERSIBLE_COPY = "This cannot be undone from here.";

export function TcWriteGateCard({
  title,
  connector,
  params,
  ledgerId,
  irreversible,
  onApprove,
  onDecline,
  busy = false,
}: TcWriteGateCardProps): ReactElement {
  return (
    <div
      className="ui-card"
      data-testid="tc-write-gate-card"
      data-risk={irreversible ? "high" : "normal"}
      style={rootStyle}
    >
      <p
        className="ui-eyebrow"
        data-testid="tc-write-gate-card-eyebrow"
        style={flatStyle}
      >
        {connector !== null && connector.length > 0
          ? `Waiting on you · ${connector}`
          : "Waiting on you"}
      </p>

      <h3
        className="ui-title"
        data-testid="tc-write-gate-card-title"
        style={flatStyle}
      >
        {title}
      </h3>

      {params.length > 0 ? (
        <dl data-testid="tc-write-gate-card-params" style={paramsStyle}>
          {params.map((param) => (
            <div key={param.label} style={paramRowStyle}>
              <dt className="ui-mono-caps" style={flatStyle}>
                {param.label}
              </dt>
              <dd className="ui-body" style={flatStyle}>
                {param.value}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}

      {/* Stated as a fact about the write, not as a warning about this choice —
          a caution here would make the safe option look like the risky one. */}
      <p
        className="ui-caption"
        data-testid="tc-write-gate-card-reversibility"
        style={flatStyle}
      >
        {irreversible ? IRREVERSIBLE_COPY : REVERSIBLE_COPY}
      </p>

      <div style={actionsStyle}>
        <button
          type="button"
          className="ui-button ui-button--primary"
          data-testid="tc-write-gate-card-approve"
          disabled={busy}
          onClick={onApprove}
        >
          Approve
        </button>
        <button
          type="button"
          className="ui-button"
          data-testid="tc-write-gate-card-decline"
          disabled={busy}
          onClick={onDecline}
        >
          Decline
        </button>
      </div>

      <div style={footerStyle}>
        <span
          className="ui-mono-caps"
          data-testid="tc-write-gate-card-ledger-id"
        >
          {ledgerId}
        </span>
      </div>
    </div>
  );
}

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm, 8px)",
  padding: "var(--space-md, 12px)",
};

const flatStyle: CSSProperties = { margin: 0 };

const paramsStyle: CSSProperties = {
  display: "grid",
  gap: "var(--space-2xs, 4px)",
  margin: 0,
};

const paramRowStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 8rem) minmax(0, 1fr)",
  gap: "var(--space-sm, 8px)",
  alignItems: "baseline",
};

const actionsStyle: CSSProperties = {
  display: "flex",
  gap: "var(--space-sm, 8px)",
  alignItems: "center",
};

const footerStyle: CSSProperties = {
  borderTop: "1px solid var(--color-border-subtle, #22252e)",
  paddingTop: "6px",
};
