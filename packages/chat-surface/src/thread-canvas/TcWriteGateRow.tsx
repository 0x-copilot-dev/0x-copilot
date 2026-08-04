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

import { useState, type CSSProperties, type ReactElement } from "react";

import type { ActivityParam } from "../approvals/types";
import { TcWriteGatePayload } from "./TcWriteGatePayload";

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
  /**
   * The call's arguments — the evidence the decision is made ON. Rendered only
   * once expanded. Defaulted so existing call sites are unchanged.
   */
  readonly params?: readonly ActivityParam[];
  /**
   * `r<short>·<seq>`, joined host-side from the ledger gate. Optional because
   * it is NOT derivable here — it anchors on the `gate.opened` event, a
   * different event from the approval this row was projected from — and a
   * non-gate approval has no ledger row at all. Absent ⇒ the line is omitted.
   */
  readonly ledgerId?: string | undefined;
}

const EMPTY_PARAMS: readonly ActivityParam[] = [];

export function TcWriteGateRow({
  title,
  connector,
  irreversible,
  onApprove,
  onDecline,
  onReview,
  busy = false,
  params = EMPTY_PARAMS,
  ledgerId,
}: TcWriteGateRowProps): ReactElement {
  // Local, for the same reason `TcInlineArtifactCard` keeps its own: nothing
  // outside this row acts on whether it is open, and lifting it would re-render
  // the whole transcript on every expand.
  const [open, setOpen] = useState(false);
  // The payload is the ONLY thing that licenses approving an irreversible
  // write, so "expanded" is not enough on its own — an empty params frame is a
  // real state (a gate can open before its approval projection lands), and
  // approving over an empty body is precisely the blind approval the design
  // forbids. In that case the row keeps sending the reviewer to the canvas.
  const payloadSeen = open && params.length > 0;
  const bodyApprove = irreversible && payloadSeen;

  const toggle = (): void => {
    const willOpen = !open;
    setOpen(willOpen);
    // Fired as a NOTIFICATION, never as the mechanism. The dead-control
    // regression happened because this row's only behaviour lived behind a host
    // callback with no producer, so the row must now work whether or not anyone
    // is listening — including a listener that throws. Same reasoning as the
    // transport's `#safeNotifyRetry`: an observer must not break the flow it is
    // observing. Deliberately outside the state updater, because raising from
    // inside one aborts the update and would put the failure right back in
    // charge of whether the row opens.
    if (!willOpen) return;
    try {
      onReview();
    } catch {
      // observer errors must not stop the row from opening
    }
  };

  return (
    <article
      className="tc-write-gate"
      data-testid="tc-write-gate"
      data-open={open ? "true" : "false"}
    >
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
          {/* The rule is about ORDER, not location: an approve control for an
            irreversible write must not be reachable in one click from the
            collapsed row. So there is still no `tc-write-gate-approve` here —
            approving one of these happens in the body, below the payload that
            justifies it. A reversible write keeps its one-click Approve, which
            is the entire point of a compact row. */}
          {irreversible ? (
            <button
              type="button"
              className="ui-button ui-button--sm ui-button--primary"
              data-testid="tc-write-gate-review"
              aria-expanded={open}
              disabled={busy}
              onClick={toggle}
            >
              {open ? "Hide" : "Review →"}
            </button>
          ) : (
            <>
              <button
                type="button"
                className="ui-button ui-button--sm"
                data-testid="tc-write-gate-review"
                aria-expanded={open}
                disabled={busy}
                onClick={toggle}
              >
                {open ? "Hide" : "Review"}
              </button>
              <button
                type="button"
                className="ui-button ui-button--sm ui-button--primary"
                data-testid="tc-write-gate-approve"
                disabled={busy}
                onClick={onApprove}
              >
                Approve
              </button>
            </>
          )}
          {/* Decline stays one click in every state. Declining is safe by
            definition, and making someone expand to say no is what leaves a
            write parked forever. */}
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
      {open ? (
        <div className="tc-write-gate__body" data-testid="tc-write-gate-body">
          <TcWriteGatePayload
            params={params}
            irreversible={irreversible}
            ledgerId={ledgerId}
            testIdPrefix="tc-write-gate-body"
          />
          {bodyApprove ? (
            // A DISTINCT testid from the row's, deliberately: the assertion that
            // an irreversible write exposes no `tc-write-gate-approve` keeps its
            // original meaning — no one-click approval — rather than silently
            // becoming true of a button that is now two clicks away.
            <button
              type="button"
              className="ui-button ui-button--sm ui-button--primary"
              data-testid="tc-write-gate-body-approve"
              disabled={busy}
              onClick={onApprove}
            >
              Approve
            </button>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

/**
 * Row geometry, measured against the cards it sits between.
 *
 * `.ui-card` is a PANEL recipe — `--radius-xl` (16px) and `--space-xl` padding.
 * Borrowing it for a row and then fighting it with 2px inline padding produced a
 * 30px box with a 16px radius, i.e. `height / 2`: a pill, not a card. Beside it
 * the tool cards run 42px, so the ask read as a chip that had swallowed two
 * buttons, and the 24px buttons filled 80% of their own container with 3px of
 * clearance — which is what made the primary look oversized. The button was
 * never the problem; the box around it was.
 *
 * `--radius-lg` puts it back in the card family, and `--space-sm` (8px) of
 * vertical padding gives 24 + 16 + 2 = 42px — the tool card's exact height, so
 * the ask sits ON the column's rhythm instead of near it. Buttons then fill 57%
 * rather than 80%. (`--space-xs` is 4px, not 6: guessing it produced 34px.)
 */
const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-xs, 6px)",
  padding: "var(--space-sm, 8px)",
  paddingLeft: "var(--space-md, 12px)",
  borderRadius: "var(--radius-lg, 12px)",
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
