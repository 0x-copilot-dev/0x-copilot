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

import type { ApprovalPresentation } from "../approvals/presentation";
import type { ActivityParam } from "../approvals/types";
import { TcWriteGatePayload } from "./TcWriteGatePayload";

export interface TcWriteGateCardProps {
  /** Verb-first line: "Create an issue in Parth-test". */
  readonly title: string;
  /** Connector slug; omitted from the eyebrow when unknown. */
  readonly connector: string | null;
  /** The call's arguments, already projected + sanitised for display. */
  readonly params: readonly ActivityParam[];
  /**
   * The server-projected SHAPE of this call — the batch it will execute, or the
   * draft it will send. Same field, same default, same meaning as
   * `TcWriteGateRow.presentation`: absent-means-omitted, so a gate without one
   * renders byte-for-byte what this card rendered before shapes existed.
   */
  readonly presentation?: ApprovalPresentation | null;
  /**
   * The exact command a `run_command` ask will execute (PRD-shell-execution
   * §14.1).
   *
   * IT IS THE WHOLE CARD, on a command ask. The write gate's wire shape carries
   * no `arguments` and no presentation, so without this the canvas twin of a
   * command approval was a title, a no-undo-less reversibility line, and an
   * enabled Approve button — a decision offered over a payload the surface had
   * not shown. `TcWriteGatePayload`'s own header called that out as the one
   * payload where "the card and the process disagree about what was approved"
   * is the entire risk, and this prop is that gap closed.
   *
   * Approve is NOT additionally gated on the payload having rendered, unlike
   * `TcWriteGateRow`'s. The row gates because it can be COLLAPSED — its rule is
   * "no approval before the payload has been seen", and a collapsed row has not
   * shown it. This card has no disclosure: the payload is on screen whenever
   * there is one, so the rule is satisfied structurally. Adding the predicate
   * here would instead disable Approve on every ordinary MCP write gate, whose
   * wire shape legitimately carries neither params nor presentation — the
   * dead-control failure `TcWriteGateRow` documents at length.
   */
  readonly commandText?: string | null;
  /** `r<short>·<seq>` — the audit anchor this decision will be recorded under. */
  readonly ledgerId: string;
  /** True when the write cannot be undone from inside the app. */
  readonly irreversible: boolean;
  readonly onApprove: () => void;
  readonly onDecline: () => void;
  readonly busy?: boolean;
}

export function TcWriteGateCard({
  title,
  connector,
  params,
  presentation = null,
  commandText = null,
  ledgerId,
  irreversible,
  onApprove,
  onDecline,
  busy = false,
}: TcWriteGateCardProps): ReactElement {
  return (
    <div
      // `tc-write-gate-card` is not decoration — `review-surfaces.css` hangs
      // one rule off it, the per-CONTAINER `overflow-wrap: anywhere` that the
      // row's expanded body has had since a 138-character token measured a
      // 356px column out to 1022px. A command is an unbroken token far more
      // often than prose is, so forwarding `commandText` here without it would
      // have imported that failure onto the canvas.
      className="ui-card tc-write-gate-card"
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

      {/* The same component the transcript row's expanded body renders, so the
          two surfaces cannot disagree about what is being approved — and now
          with the same PROPS, which is what that sentence needed to be true.
          Passing the component but not its evidence made the two surfaces
          disagree in the one direction that matters: the row showed the command
          and the canvas showed a heading. */}
      <TcWriteGatePayload
        params={params}
        presentation={presentation}
        commandText={commandText}
        irreversible={irreversible}
        ledgerId={ledgerId}
        testIdPrefix="tc-write-gate-card"
      />

      <div style={actionsStyle}>
        <button
          type="button"
          className="ui-button ui-button--md ui-button--primary"
          data-testid="tc-write-gate-card-approve"
          disabled={busy}
          onClick={onApprove}
        >
          Approve
        </button>
        <button
          type="button"
          className="ui-button ui-button--md"
          data-testid="tc-write-gate-card-decline"
          disabled={busy}
          onClick={onDecline}
        >
          Decline
        </button>
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

const actionsStyle: CSSProperties = {
  display: "flex",
  gap: "var(--space-sm, 8px)",
  alignItems: "center",
};
