// The evidence a parked write is decided ON: its arguments, whether it can be
// undone, and the ledger row the decision gets recorded under. 🎨
//
// Extracted so the two surfaces that show it — the Studio canvas card and the
// transcript row's expanded body — render the SAME bytes from the same code.
// They are not decorative twins: the safety rule for an irreversible write is
// "no approval before the payload has been seen", so if the two surfaces could
// disagree about what the payload is, the rule would mean two different things
// depending on where you happened to be standing.
//
// `testIdPrefix` exists because the canvas card and the inline body can be
// mounted AT ONCE for the same gate (Studio shows the gate region while the
// chat column shows the row), so a shared testid would make every
// cockpit-level query ambiguous.
//
// THE SHAPED EVIDENCE LIVES HERE TOO — `presentation.rows` and
// `presentation.preview` are not decoration next to the params frame, they are
// the SAME `arguments` in the shape that argument actually has. The backend
// projects them from the exact bytes the connector will receive
// (`ApprovalPresentationProjector`), precisely so a card cannot show a
// description of an action in place of the action. Rendering them from a
// different file than the params frame would put two projections of one payload
// in two components and reopen the divergence this extraction closed — and the
// "no approval before the payload has been seen" rule would once again mean two
// things depending on where you were standing.
//
// So: what the decision is made ON is one component, one answer. `hasWriteGateEvidence`
// is that answer as a predicate, because the caller that withholds Approve must
// ask this file whether anything rendered rather than re-deriving it from three
// fields it does not own.

import type { CSSProperties, ReactElement } from "react";

import type {
  ApprovalPresentation,
  ApprovalRow,
} from "../approvals/presentation";
import type { ActivityParam } from "../approvals/types";

/** Byte-exact copy — asserted verbatim by `TcWriteGateCard.test.tsx`. */
export const REVERSIBLE_COPY =
  "You can undo this from the connector if it's wrong.";
export const IRREVERSIBLE_COPY = "This cannot be undone from here.";

export interface TcWriteGatePayloadProps {
  readonly params: readonly ActivityParam[];
  /**
   * The server-projected SHAPE of this call — the batch it will execute, or the
   * draft it will send. Optional and absent-means-omitted, like every other
   * gate-specific field: the write-gate lane rides the `ask_a_question` wire
   * shape, which carries no presentation at all, so `null` must render exactly
   * what this component rendered before shapes existed.
   */
  readonly presentation?: ApprovalPresentation | null;
  readonly irreversible: boolean;
  /**
   * `r<short>·<seq>` — the audit anchor. Optional because it is NOT derivable
   * here: it is anchored on the `gate.opened` event's own `sequence_no`, a
   * different event from the `approval_requested` the transcript folds, so a
   * locally computed one would point at a different ledger row. Absent ⇒ the
   * line is omitted rather than guessed.
   */
  readonly ledgerId?: string | undefined;
  readonly testIdPrefix: string;
}

/**
 * Whether the body actually put the thing being approved on screen.
 *
 * The caller uses this to decide whether an irreversible write may be approved
 * at all, so it answers ONE question: is the payload visible? A batch is; a
 * draft is; a key/value frame is. `provenance` is not — it says which run and
 * which account, which is attribution, not the effect being consented to.
 *
 * It exists because "did the payload render" stopped being answerable from
 * `params.length` the moment a rows card could carry twelve payees and zero
 * params (`buildParams` keeps only primitive top-level arguments, so the list of
 * mappings it was projected from is skipped). Gating on params alone would
 * withhold approval over a card that is in fact fully evidenced.
 */
export function hasWriteGateEvidence(
  params: readonly ActivityParam[],
  presentation?: ApprovalPresentation | null,
): boolean {
  if (params.length > 0) {
    return true;
  }
  if (presentation === undefined || presentation === null) {
    return false;
  }
  if (presentation.layout === "rows") {
    return presentation.rows.length > 0;
  }
  if (presentation.layout === "preview") {
    return presentation.preview !== null;
  }
  return false;
}

export function TcWriteGatePayload({
  params,
  presentation = null,
  irreversible,
  ledgerId,
  testIdPrefix,
}: TcWriteGatePayloadProps): ReactElement {
  // Keyed on the DECLARED layout, not on "whatever is populated". The layout is
  // the server's answer to which shape this call has, and the client parser
  // already corrects a shape that has nothing to draw back to `params`
  // (`parseApprovalPresentation`) — so trusting the field cannot produce an
  // empty frame, while second-guessing it could draw two.
  const rows =
    presentation !== null && presentation.layout === "rows"
      ? presentation.rows
      : [];
  const preview =
    presentation !== null && presentation.layout === "preview"
      ? presentation.preview
      : null;
  const provenance = presentation?.provenance ?? null;
  return (
    <>
      {/* The batch leads: it IS the action, and every param beside it is
          context for it. Rendered read-only — the per-row Approve/Reject pair
          the design draws needs a wire that does not exist (the host seam is
          `onApprove(approvalId)` → one `/decision` POST with no per-row field),
          and a button that posts the wrong thing is worse than a line of text
          that posts nothing. Statuses are not drawn either: every row the
          producer can emit is `pending`, and nothing ever mutates one. */}
      {rows.length > 0 ? (
        <ul
          className="tc-write-gate__rows"
          data-testid={`${testIdPrefix}-rows`}
        >
          {rows.map((row, index) => (
            <BatchRow key={row.rowId ?? `${row.label}-${index}`} row={row} />
          ))}
        </ul>
      ) : null}

      {/* The message about to leave the workspace, verbatim. A params table can
          say a post is going to #launch; only the draft can say whether it
          should. Plain text, `pre-wrap` — this string is model-authored and may
          itself have come from tool output, so it never styles the card. */}
      {preview !== null ? (
        <p
          className="tc-write-gate__preview"
          data-testid={`${testIdPrefix}-preview`}
        >
          {preview.text}
          {/* Rendered WITH the preview, never as an optional extra: the frame
              scrolls and the producer truncates at 2000 characters, so the
              volumetric line is what keeps a partial draft honest about how
              much there is. */}
          {preview.meta === null ? null : (
            <span
              className="tc-write-gate__preview-meta"
              data-testid={`${testIdPrefix}-preview-meta`}
            >
              {preview.meta}
            </span>
          )}
        </p>
      ) : null}

      {params.length > 0 ? (
        <dl data-testid={`${testIdPrefix}-params`} style={paramsStyle}>
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

      {/* "Launch Week ops · Safe 3-of-5" — which run, against which account.
          Quiet, and in the BODY rather than the header, because the header is
          byte-identical collapsed and expanded and a variable-width span in it
          would move the buttons.

          NOTE: unreachable from any payload today. `ApprovalPresentationProjector`
          passes `provenance=` on none of its three return paths, so the contract
          default `None` is what ships and the parser yields null. Wired because
          it costs a degrading line; do not read it rendering in a test as
          evidence the field arrives. */}
      {provenance === null ? null : (
        <p
          className="tc-write-gate__provenance"
          data-testid={`${testIdPrefix}-provenance`}
        >
          {provenance}
        </p>
      )}

      {/* Stated as a fact about the write, not as a warning about this choice —
          a caution here would make the safe option look like the risky one. */}
      <p
        className="ui-caption"
        data-testid={`${testIdPrefix}-reversibility`}
        style={flatStyle}
      >
        {irreversible ? IRREVERSIBLE_COPY : REVERSIBLE_COPY}
      </p>

      {ledgerId !== undefined && ledgerId.length > 0 ? (
        <div style={footerStyle}>
          <span
            className="ui-mono-caps"
            data-testid={`${testIdPrefix}-ledger-id`}
          >
            {ledgerId}
          </span>
        </div>
      ) : null}
    </>
  );
}

/**
 * One line item of a batch: who, what note, how much.
 *
 * The monogram is server-derived (`_initials`) and decorative, so it is
 * `aria-hidden` — the label beside it is what a screen reader reads, and a
 * batch that announced "MP Mira Patel" twelve times would be worse than one
 * that announced twelve names.
 */
function BatchRow({ row }: { readonly row: ApprovalRow }): ReactElement {
  return (
    <li className="tc-write-gate__row">
      <span className="tc-write-gate__row-name">
        {row.initials === null ? null : (
          <span className="tc-write-gate__avatar" aria-hidden="true">
            {row.initials}
          </span>
        )}
        <span className="tc-write-gate__row-label">{row.label}</span>
        {row.note === null ? null : (
          <span className="tc-write-gate__row-note">{row.note}</span>
        )}
      </span>
      <span className="tc-write-gate__row-value">{row.value}</span>
    </li>
  );
}

// Carried verbatim from `TcWriteGateCard`, fallbacks included, so extracting
// this changed no pixel on the canvas.
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

const footerStyle: CSSProperties = {
  borderTop: "1px solid var(--color-border-subtle, #22252e)",
  paddingTop: "6px",
};
