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
//
// THE COMMAND BLOCK joined that list (PRD-shell-execution §14.1) and is the one
// piece of evidence that is not a projection of `arguments` — a command ask
// carries none. It is here rather than in `TcWriteGateRow` so the predicate
// stays one answer, and so the block travels with §10.2's no-undo line.
//
// ⚠️ ONLY ONE OF THE TWO SURFACES PASSES IT SO FAR. `TcWriteGateCard` (the
// Studio canvas twin) forwards `params` and `ledgerId` and neither
// `presentation` nor `commandText`, so a command ask opened on the canvas shows
// its title and no command. That gap predates this lane — the shaped evidence
// has the same hole — but a command is the one payload where "the card and the
// process disagree about what was approved" is the whole risk, so it is called
// out here rather than left to be discovered.

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
/**
 * The command lane's own no-undo line (PRD-shell-execution §10.2, second of the
 * three places the concession is stated).
 *
 * IT REPLACES `IRREVERSIBLE_COPY` rather than joining it, and the difference
 * between the two sentences is the whole reason it exists. "This cannot be
 * undone from here" is a property of the act; this names the CAUSE, and the
 * cause is the part the reader cannot infer: a command writes files without
 * going through `write_file`, so no `HostWriteRecord` is journaled, so the
 * Changes tab has nothing to revert and — until AC4.1–4.3 land — nothing to
 * list either. A card that said only "cannot be undone" would leave the reader
 * to assume the app at least SAW it.
 */
export const COMMAND_NO_UNDO_COPY =
  "Changes made by a command can't be undone from here.";

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
  /**
   * The exact command a `run_command` ask will execute (PRD-shell-execution
   * §14.1). Absent-means-omitted like every other lane-specific field, so every
   * card that is not a command ask renders byte-for-byte what it rendered
   * before this existed.
   *
   * NOT `params`. `buildParams` keeps primitive top-level arguments and prints
   * them into a `<dd>` grid with no cap and no whitespace preservation — a
   * multi-line command re-flowed into one is not the command that will run, and
   * the reader is being asked to consent to the exact bytes.
   *
   * It is also the EVIDENCE for this ask: see `hasWriteGateEvidence`. Nothing
   * else on a command card is — the write gate's wire shape carries no
   * `arguments` and no presentation at all — so without this the one card that
   * must never be approved blind would have no approve control at all.
   */
  readonly commandText?: string | null;
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
  commandText?: string | null,
): boolean {
  // THE COMMAND IS THE EVIDENCE (PRD-shell-execution §14.1). A command ask
  // arrives with zero params and no presentation — the write gate's wire shape
  // carries neither — so answering this from those two alone returns false for
  // exactly the card that most needs an answer, and Approve would be withheld
  // forever on a card that is in fact showing the whole thing being consented
  // to. Third parameter rather than a caller-side `||` for the reason this
  // predicate exists at all: the component that DRAWS the evidence is the one
  // that gets to say whether any rendered, and two files answering that
  // question is how the "no approval before the payload has been seen" rule
  // comes to mean two things depending on where you are standing.
  if (showsCommand(commandText)) {
    return true;
  }
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

/**
 * Whether there is a command to draw — the ONE predicate, used by
 * `hasWriteGateEvidence` above, by the block that renders it below, and by
 * `TcWriteGateRow`'s run-scoped-Approve gate.
 *
 * Whatever hides the command must be the same condition that shows it. Two
 * near-identical tests would eventually disagree on some string, and the
 * disagreement that matters points one way only: a card that counted a
 * whitespace-only command as evidence would unlock Approve over an empty frame,
 * which is precisely the blind approval the rule forbids.
 *
 * Exported for the third caller, which asks a DIFFERENT question of the same
 * fact: "is this the command lane?". `TcWriteGateRow` needs that because the
 * card can no longer read "irreversible" as "destructive" — §14.1 splits those
 * onto two wire fields — and the presence of a command is the only thing on
 * this side of the wire that separates the irreversible class the server grants
 * a run-scoped `always` (`execute`) from the one it never will (`destructive`).
 *
 * Emptiness is judged on the TRIMMED string and the value is rendered
 * UNTRIMMED. What runs is `/bin/sh -c "<command>"` with the bytes the model
 * sent; a card that quietly tidied them would be showing a different command
 * from the one being approved.
 */
export function showsCommand(
  commandText?: string | null,
): commandText is string {
  return (
    commandText !== undefined &&
    commandText !== null &&
    commandText.trim().length > 0
  );
}

export function TcWriteGatePayload({
  params,
  presentation = null,
  commandText = null,
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
  const command = showsCommand(commandText) ? commandText : null;
  return (
    <>
      {/* THE COMMAND LEADS, above the batch and the draft, because on a command
          ask it is not evidence ABOUT the action — it is the action, and it is
          the only thing on the card that is.

          Rendered as a text node inside a `<pre>`: the string is model-authored
          and may itself have come from tool output, so it never styles the
          card. `<pre>` and not a `<p>` because the reader is consenting to
          exact bytes — a shell command's line breaks and its runs of spaces are
          semantic, and `pre-wrap` is what keeps them while still wrapping a
          long line rather than pushing the frame wider than the card.

          Capped AND scrollable, together, for the same reason the draft
          preview is: a cap alone clips in silence, and a command whose tail is
          invisible is exactly the thing consent cannot be given over. */}
      {command === null ? null : (
        <>
          <pre
            className="tc-write-gate__command"
            data-testid={`${testIdPrefix}-command`}
          >
            {command}
          </pre>
          {/* Second of the three places §10.2 states the concession, and the
              only one attached to the decision itself. A LINE, not a chip and
              not a hover: it has to be readable at the moment of consent
              without an interaction, because the fact it carries is the one
              the rest of the app cannot make good on afterwards. */}
          <p
            className="tc-write-gate__no-undo"
            data-testid={`${testIdPrefix}-no-undo`}
          >
            {COMMAND_NO_UNDO_COPY}
          </p>
        </>
      )}

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
          a caution here would make the safe option look like the risky one.

          SUPPRESSED ON A COMMAND ASK, because the no-undo line above already
          said it and said it better. Stacking them would print two
          "cannot be undone" sentences three lines apart, which reads as a bug
          and trains people to skim the one that matters. Keyed on the command
          rather than on `irreversible` so it also covers the degraded case: a
          command card whose payload lost `risk_level` would otherwise print
          REVERSIBLE_COPY — "you can undo this from the connector" — over an
          action that has no connector and cannot be undone at all. */}
      {command !== null ? null : (
        <p
          className="ui-caption"
          data-testid={`${testIdPrefix}-reversibility`}
          style={flatStyle}
        >
          {irreversible ? IRREVERSIBLE_COPY : REVERSIBLE_COPY}
        </p>
      )}

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
