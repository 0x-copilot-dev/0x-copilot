// TcWriteGateRow — the ask, as ONE card in the chat column. 🎨
//
// The chat column is a feed: it is scanned while the agent is still working, so
// anything taller than a message stops the scroll and buries the reply under
// it. A parked write used to render the full `ask_a_question` card there — the
// title, the reason, the params frame, the ledger id — because a gate had
// nowhere ELSE to put any of it.
//
// It does now, twice over: `TcWriteGateCard` renders the payload on the Studio
// canvas, and this card expands IN PLACE. So the default is one line — risk
// dot, verb-first title, connector, the decision — and the evidence is one
// disclosure away without leaving the transcript.
//
// THE LOAD-BEARING LAYOUT RULE (design spec §1): the header is identical
// collapsed and expanded. Expanding adds a body UNDERNEATH and moves nothing,
// so the button you were reaching for is still under your cursor when the card
// grows. Every affordance whose width could change with state — the disclosure
// label, the chip — is therefore either fixed-size or state-invariant, and the
// only thing the toggle changes about itself is which way its chevron points.
//
// Risk is a 6px dot plus a plain-text chip, not a coloured panel. An
// IRREVERSIBLE write swaps the primary action for "Review": you cannot approve
// one of those in one click from the collapsed card. That safety property is
// only expressible once the card is small enough for the button choice to be
// the loudest thing on it.
//
// IT IS ALSO THE COMMAND ASK (PRD-shell-execution §14.1), and it fits for the
// reasons above rather than by adaptation: a shell command cannot be undone by
// anything this app owns, so it must never be approvable in one click, and the
// always-grant §8.3 offers is by definition wider than the call in front of you.
// Both rules already live here. What the lane adds is `commandText` — the
// verbatim block that IS the payload for that ask — and, with it, §10.2's
// no-undo line. Neither is decoration: a command writes files without going
// through `write_file`, so nothing journals it and the Changes tab cannot revert
// it, and the card is the place that has to say so.
//
// Naming: still `TcWriteGateRow`, and its STRUCTURAL testids are still
// `tc-write-gate*` (`-row`, `-title`, `-connector`, `-review`, `-body*`),
// because a live packaged-app journey (tools/desktop-journeys/write-gate-inline)
// reads those at document level. The component has outgrown "write gate" —
// every prop that is gate-specific is optional so an ordinary tool approval
// renders through the same card — but renaming it is a separate, mechanical
// step that has to move the journey with it.
//
// THE BODY ALSO CARRIES THE SCOPE. `grant_options` says which scopes the server
// will accept for this card; `allow_once` is what a plain Approve has always
// meant, and `allow_always` — when the caller says this lane's `always` is the
// run-scoped rule the `/decision` POST carries — draws one extra ghost button
// under the payload. It is in the body and not the header on the same rule the
// irreversible lane follows: a decision that covers more than the call in front
// of you is not a one-click decision.
//
// The four DECISION controls are the exception: their testids come from props
// (`approveTestId` / `declineTestId` / `bodyApproveTestId` /
// `alwaysApproveTestId`), because the id a decision is pressed for has to be
// unambiguous when two asks stack, and six live desktop journeys select those
// controls by an approval-scoped name. `renderAskCard` supplies them; the
// defaults here are for a standalone mount.
//
// Kit-only styling; framework-agnostic (no window/document/fetch).

import { useId, useState, type ReactElement } from "react";

import type { ApprovalPresentation } from "../approvals/presentation";
import type { ActivityParam } from "../approvals/types";
import { Icon } from "../icons/Icon";
import {
  hasWriteGateEvidence,
  showsCommand,
  TcWriteGatePayload,
} from "./TcWriteGatePayload";

export interface TcWriteGateRowProps {
  /** Verb-first line: "Create an issue in Parth-test". */
  readonly title: string;
  /**
   * Connector slug shown as a quiet trailing label; omitted when unknown.
   * Optional because an ordinary `tool_action` ask has no vendor at all — the
   * projection only carries one for MCP calls.
   */
  readonly connector?: string | null;
  /**
   * The access axis, rendered as the second half of the design's
   * `linear · write` meta. Only shown alongside a connector: a bare "write"
   * with nothing to attribute it to is noise, not provenance. Lower-cased on
   * the way out — the projection emits the enum (`WRITE` / `READ`) and the kit
   * forbids getting there with `text-transform`, so what a screen reader hears
   * stays the string on screen.
   *
   * Nullable in its own right, and often null: the projection derives the axis
   * from the payload's `read_only` boolean, and plenty of asks name a connector
   * without carrying one. Absent ⇒ the segment is dropped and the bare vendor
   * renders, rather than a word nothing on the wire asserted.
   */
  readonly access?: string | null;
  /**
   * True when the write cannot be undone from inside the app (the PDP's
   * `destructive` axis). Swaps the primary action for a review trip and adds
   * the "can't be undone" chip. Defaults false — an unlabelled ask is an
   * ordinary one, never a severity nobody asserted.
   */
  readonly irreversible?: boolean;
  /**
   * Why the agent is asking, shown at the top of the expanded body. Optional
   * because the collapsed card must stand on its own; a card that needs its
   * reason read before it can be decided is a card that should not be compact.
   */
  readonly reason?: string | null;
  /** Approve — dispatches the parked write. */
  readonly onApprove: () => void;
  /**
   * Approve, AND stop asking for this call for the rest of the run — the
   * `allow_always` half of the server's `grant_options`, carried on the wire as
   * `decision_scope: "always"`.
   *
   * ABSENT MEANS THE CONTROL IS NOT DRAWN, and that is the contract rather than
   * a convenience. `allow_always` means two different things on the two lanes
   * that emit it: on the write gate it is a RUN-SCOPED allow rule over the
   * subjects this call already carried, and on the filesystem lane it ATTACHES
   * A FOLDER — durable, wider than the path the card named, and settled by an
   * OS dialog rather than by this POST
   * (`runtime_worker/stream_events.py:227-234`). This card can only honour the
   * first, so the caller decides, and a card with nothing to offer offers
   * nothing rather than a button that posts a field the server drops.
   *
   * It lives in the EXPANDED BODY, never the header. The card's rule for an
   * irreversible write generalises: a decision that covers more than the call
   * in front of you is not a one-click decision. Keeping it out of the header
   * also keeps `.tc-write-gate__actions` — a `flex: none` block — from growing a
   * third button and squeezing the title and the connector meta further.
   */
  readonly onApproveAlways?: () => void;
  /** Decline — the run continues, nothing is written. */
  readonly onDecline: () => void;
  /**
   * Told that the reader opened the detail. A NOTIFICATION, never the
   * mechanism — and optional, because most asks have no canvas detail surface
   * to open and no host listening. See `toggle` for why that matters.
   */
  readonly onReview?: () => void;
  /**
   * Disables every control while a decision is in flight.
   *
   * NOT passed by the Run cockpit, and that is correct rather than an omission:
   * it resolves optimistically, so the card is already retiring by the time a
   * `busy` frame could render. The double-submit that flag would have guarded
   * is prevented where it actually happens — `RunDestination.resolveApproval`
   * drops any second decision for an approval before the POST — because a
   * disabled button is a race the host can lose, and an idempotence check is
   * not. This exists for a host that awaits the POST before re-rendering; the
   * irreversible lane's canvas card is the one that does.
   */
  readonly busy?: boolean;
  /**
   * The call's arguments — the evidence the decision is made ON. Rendered only
   * once expanded, and only when non-empty: an empty frame is worse than no
   * frame, because it looks like the answer is "nothing".
   */
  readonly params?: readonly ActivityParam[];
  /**
   * The server-projected SHAPE of this call, when it has one: the batch it will
   * execute, the draft it will send, and — always — the VERB the approve button
   * promises.
   *
   * The verb is the field that matters most, because the producer sets it on
   * every presentation it emits. "Approve & send" and "Approve & sign" are the
   * backend telling the reader what the click does; flattening them to a
   * neutral "Approve" makes an MCP post and a payout batch read identically at
   * the only moment the difference is actionable.
   *
   * Optional and absent-means-omitted like every other gate-specific prop: a
   * parked write rides the `ask_a_question` wire shape, which carries no
   * presentation at all, so `null` must render byte-for-byte what this card
   * rendered before shapes existed.
   */
  readonly presentation?: ApprovalPresentation | null;
  /**
   * The exact command a `run_command` ask will execute — `RunApproval.command`,
   * straight off `payload.command` (PRD-shell-execution §14.1). Present makes
   * this a COMMAND ask; absent renders byte-for-byte what this card rendered
   * before the lane existed.
   *
   * It goes to `TcWriteGatePayload` rather than through `params`, and it is
   * what `hasWriteGateEvidence` counts for this lane — which is the safety
   * property, not a rendering detail. A command ask carries no `arguments` and
   * no presentation (the write gate's wire shape has neither), so the command
   * block is the ONLY thing that puts the action on screen: approving without
   * expanding is structurally impossible, and approving after expanding means
   * the command was rendered.
   *
   * The block brings §10.2's no-undo line with it, from the same component, so
   * the sentence cannot be separated from the thing it is about.
   */
  readonly commandText?: string | null;
  /**
   * `r<short>·<seq>`, joined host-side from the ledger gate. Optional because
   * it is NOT derivable here — it anchors on the `gate.opened` event, a
   * different event from the approval this card was projected from — and a
   * non-gate approval has no ledger row at all. Absent ⇒ the line is omitted.
   */
  readonly ledgerId?: string | undefined;
  /**
   * Testid for the header's one-click Approve. Overridden by `renderAskCard`
   * with `tc-chat-approval-approve-<approvalId>` — the name five live desktop
   * journeys select on by prefix, and the one that stays unique when two asks
   * are parked at once. Same precedent as `ConsentCard`'s `approveTestId`.
   */
  readonly approveTestId?: string;
  /**
   * Testid for Decline. Scoped by `renderAskCard` to
   * `tc-chat-approval-reject-<approvalId>` — "reject" and not "decline"
   * because `tools/desktop-journeys/connectors/gate_audit_events.py`
   * interpolates that exact string, and it is the only EXACT id-scoped
   * consumer of a decision control anywhere.
   */
  readonly declineTestId?: string;
  /**
   * Testid for the body's Approve — the only approve an IRREVERSIBLE write
   * ever gets. It must stay DISTINCT from `approveTestId`, and distinct in a
   * way that survives prefix matching: `renderAskCard` scopes it to
   * `tc-chat-approval-body-approve-<approvalId>`, which deliberately does NOT
   * match `[data-testid^=tc-chat-approval-approve-]`. If it did, the five
   * journeys that press Approve by that prefix would press the one control the
   * design withholds until the payload has been read.
   */
  readonly bodyApproveTestId?: string;
  /**
   * Testid for the run-scoped Approve. Scoped by `renderAskCard` to
   * `tc-chat-approval-always-<approvalId>`, chosen to match NEITHER
   * `[data-testid^=tc-chat-approval-approve-]` nor
   * `tc-chat-approval-reject-<id>` — the six live desktop journeys that press
   * Approve or Decline must not be able to land on the control that widens the
   * decision to the whole run. Same reasoning, and the same shape-of-the-name
   * enforcement, as `bodyApproveTestId`.
   */
  readonly alwaysApproveTestId?: string;
}

const EMPTY_PARAMS: readonly ActivityParam[] = [];

/** The neutral verbs. Used whenever the wire named none — which is most asks. */
const DEFAULT_APPROVE = "Approve";
// Deliberately "Decline", not the old card's "Reject". `reject_label` is never
// set by any producer path, so this default is what has always painted and what
// still paints; the prop exists so a future producer CAN name the verb, not as
// a back door for reverting this copy.
const DEFAULT_DECLINE = "Decline";
// Names the REACH, not the verb. "Always allow" is what the wire word says and
// is exactly the sentence that made this control dangerous to draw: on this lane
// the rule expires with the run, and a user who reads "always" as "forever" has
// consented to something wider than what happens.
const ALWAYS_LABEL = "Approve, and don't ask again this run";

export function TcWriteGateRow({
  title,
  connector = null,
  access = null,
  irreversible = false,
  reason = null,
  onApprove,
  onApproveAlways,
  onDecline,
  onReview,
  busy = false,
  params = EMPTY_PARAMS,
  presentation = null,
  commandText = null,
  ledgerId,
  approveTestId = "tc-write-gate-approve",
  declineTestId = "tc-write-gate-decline",
  bodyApproveTestId = "tc-write-gate-body-approve",
  // Deliberately NOT `…-always-approve`: `review-surfaces.css` selects
  // `[data-testid$="-approve"]` inside the body, and a suffix collision between
  // the scope control and the approve controls is exactly the class of mix-up
  // the `…-body-approve-<id>` naming rule exists to stop.
  alwaysApproveTestId = "tc-write-gate-always",
}: TcWriteGateRowProps): ReactElement {
  // Local, for the same reason `TcInlineArtifactCard` keeps its own: nothing
  // outside this card acts on whether it is open, and lifting it would
  // re-render the whole transcript on every expand.
  const [open, setOpen] = useState(false);
  // The payload is the ONLY thing that licenses approving an irreversible
  // write, so "expanded" is not enough on its own — an empty body is a real
  // state (a gate can open before its approval projection lands), and approving
  // over one is precisely the blind approval the design forbids. In that case
  // the card keeps sending the reviewer to the canvas.
  //
  // Asked of the payload component rather than of `params.length`, because a
  // rows card is fully evidenced with ZERO params: `buildParams` keeps only
  // primitive top-level arguments, so the list of payees it was projected from
  // never reaches the params frame. Counting params there would withhold
  // approval over a card that shows every line item it is about to sign.
  const payloadSeen =
    open && hasWriteGateEvidence(params, presentation, commandText);
  const bodyApprove = irreversible && payloadSeen;
  // The run-scoped Approve. Gated on `open` — expanding is the deliberate step
  // that separates "yes to this call" from "yes to every call like it for the
  // rest of the run" — and on a second condition, because this is where the
  // property is drawn and one enforcement point is one deploy from zero.
  //
  // THAT SECOND CONDITION USED TO BE A FLAT `!irreversible`, and PRD-shell-
  // execution §14.1 is what broke it. The claim behind it was "the server
  // already guarantees this by withholding `allow_always` for a destructive
  // op" — true only while `irreversible` and `op_class: destructive` were the
  // same fact. They are now deliberately two facts on two sides of the wire: a
  // command carries `op_class: "execute"` AND `risk_level: "high"`, so the
  // server DOES offer `allow_always` (`ToolAccessGate._grant_options`, when
  // §8.3's simple-command test passed) on a card this client draws
  // irreversible. A flat `!irreversible` therefore deletes the one control
  // §8.3 exists to provide, and deletes it in the silent direction — the card
  // renders, the decision is takeable, and the option the server sent is just
  // never drawn.
  //
  // So the condition now names the LANE, which is what it always meant.
  // `op_class` does not reach this component — `buildIrreversible` folds it
  // into a boolean that `execute` and `destructive` now share — and the command
  // is the only field on this side of the wire that separates them.
  // `showsCommand` is therefore the client's `op_class`, and it is asked of the
  // component that DRAWS the block so the answer cannot drift from what is
  // actually on screen.
  //
  // Both original enforcement points survive unweakened:
  //
  // * A DESTRUCTIVE op still gets no scope control here at all, expanded or
  //   not — it carries no command, so this stays a flat refusal for it. That
  //   is the second point the server's `[allow_once]` is deliberately backed
  //   by: an advance yes to a class of deletes is what the PDP's destructive
  //   rung exists to prevent, and one enforcement point is one deploy from
  //   none.
  // * A REVERSIBLE ask is untouched: it short-circuits before the command is
  //   consulted, so the MCP write lane behaves exactly as it did.
  //
  // NOT gated on `payloadSeen`, and for the reversible lane that omission is
  // load-bearing. The write gate's wire shape
  // (`ToolAccessGate._approval_payload`) carries no `arguments` and no
  // `presentation` at all, so `hasWriteGateEvidence` is FALSE for precisely the
  // cards that offer this scope; requiring it would make the control
  // unreachable in production while every fixture-built test of it passed — the
  // dead-branch failure this card has already shipped once, when
  // `isIrreversible` substring-matched an axis that could not produce the word.
  // NOT widened for the command lane, and the earlier attempt to is instructive.
  // A `|| showsCommand(commandText)` clause here was a DEAD BRANCH: the only
  // production caller (`TcChat.renderAskCard`) short-circuits on
  // `!isIrreversible(approval)` BEFORE `onApproveAlways` is ever passed, and
  // `_CommandApproval.RISK_LEVEL` is hardcoded "high", so every command card is
  // irreversible and the clause could never be reached. It typechecked, and its
  // fixture tests passed by constructing a state the app cannot produce — the
  // same dead-branch shape this card has shipped once before.
  //
  // The behaviour it tried to add is also not wanted yet: the server offers
  // `GRANT_OPTIONS = ("allow_once",)` for a command (`stream_events.py:457`),
  // and the PRD's Phase 1 is explicit that EVERY command asks. §8.3's
  // run-scoped always-grant is a later phase and starts on the SERVER offering
  // it; a client-only widening would have drawn a control whose decision the
  // wire refuses to carry.
  const scopeApprove = onApproveAlways !== undefined && open && !irreversible;
  const risk = irreversible ? "high" : "normal";
  const descriptionId = useId();
  // DERIVED, not a canned reassurance. The old copy was a standing claim about
  // the product ("You're always asked before Copilot acts outside this chat"),
  // which is reassuring and says nothing about the decision actually in front
  // of you. This says what is true of THIS ask, and in doing so carries the
  // fact that is otherwise visual-only: `data-risk` is not an ARIA attribute
  // and the dot is `aria-hidden`, so without this the difference between a
  // reversible write and one nothing here can undo reached a screen-reader user
  // through the chip alone.
  const description = irreversible
    ? "The run is paused on this decision. This cannot be undone from inside the app, so approving requires opening the payload first."
    : "The run is paused on this decision. Nothing runs until you approve or decline.";
  // Resolved ONCE and used by both approve buttons. Two reads of the same field
  // would be two chances to disagree, and a header promising "Approve & sign"
  // over a body button that says "Approve" is two controls claiming to do
  // different things with one click.
  const approveLabel = presentation?.approveLabel ?? DEFAULT_APPROVE;
  const declineLabel = presentation?.rejectLabel ?? DEFAULT_DECLINE;

  const toggle = (): void => {
    const willOpen = !open;
    setOpen(willOpen);
    // Fired as a NOTIFICATION, never as the mechanism. The dead-control
    // regression happened because this card's only behaviour lived behind a
    // host callback with no producer, so it must work whether or not anyone is
    // listening — including a listener that is absent, and one that throws.
    // Same reasoning as the transport's `#safeNotifyRetry`: an observer must
    // not break the flow it is observing. Deliberately outside the state
    // updater, because raising from inside one aborts the update and would put
    // the failure right back in charge of whether the card opens.
    if (!willOpen || onReview === undefined) return;
    try {
      onReview();
    } catch {
      // observer errors must not stop the card from opening
    }
  };

  // `linear · write`. Degrades a segment at a time rather than printing a
  // dangling separator, and disappears entirely for a non-MCP ask.
  const vendor = connector !== null && connector.length > 0 ? connector : null;
  const axis =
    access !== null && access !== undefined && access.length > 0
      ? access.toLowerCase()
      : null;
  const meta =
    vendor === null ? null : axis === null ? vendor : `${vendor} · ${axis}`;

  return (
    <article
      className="tc-write-gate"
      data-testid="tc-write-gate"
      data-open={open ? "true" : "false"}
      data-risk={risk}
      // The card had no accessible name at all: AT announced an unnamed
      // article containing three buttons. `data-risk` is not an ARIA
      // attribute and the dot is decorative, so the chip below is what
      // actually carries "destructive" to a screen reader.
      aria-label={`Approval: ${title}`}
      // …and `aria-label` alone was the whole announcement: an approval read as
      // its title plus three buttons, with no statement of what pressing one
      // does. The description below restores what `ConsentCard` carried before
      // the three shapes were consolidated into this one.
      //
      // TWO THINGS ABOUT ITS FORM, both learned the hard way and both worth
      // keeping. It is `aria-describedby` pointing at a visually-hidden node,
      // NOT `aria-description` — the latter is ARIA 1.3 and thinly implemented,
      // and with it the computed accessible description was measurably EMPTY,
      // i.e. deleted for screen readers rather than merely unpainted. And the
      // hidden node uses the clip-rect idiom, never `display:none` /
      // `visibility:hidden` / `width:0`, each of which removes the node from
      // the accessibility tree and lands in the same place.
      aria-describedby={descriptionId}
    >
      <span id={descriptionId} className="apc__a11y-only">
        {description}
      </span>
      <div
        className="tc-write-gate__hd"
        data-testid="tc-write-gate-row"
        data-risk={risk}
      >
        <span
          aria-hidden="true"
          className="tc-write-gate__dot"
          data-testid="tc-write-gate-dot"
        />
        <span
          className="tc-write-gate__title"
          data-testid="tc-write-gate-title"
        >
          {title}
        </span>
        {meta === null ? null : (
          <span
            className="tc-write-gate__meta"
            data-testid="tc-write-gate-connector"
          >
            {meta}
          </span>
        )}
        {/* Lowercase at the source per the kit's status-label rule — no
            `text-transform`, so the string a screen reader reads is the string
            on screen. This is also the ONLY non-visual signal that the write is
            destructive; the dot is aria-hidden and `data-risk` is not ARIA. */}
        {irreversible ? (
          <span
            className="ui-badge ui-badge--danger tc-write-gate__chip"
            data-testid="tc-write-gate-chip"
          >
            {"can't be undone"}
          </span>
        ) : null}
        <span className="tc-write-gate__actions">
          {/* Decline first, matching the design: the safe refusal is what a fast
              keyboard user reaches on the first Tab, and it stays one click in
              every state. Declining is safe by definition, and making someone
              expand to say no is what leaves a write parked forever. */}
          <button
            type="button"
            className="ui-button ui-button--sm ui-button--ghost tc-write-gate__reject"
            data-testid={declineTestId}
            disabled={busy}
            onClick={onDecline}
          >
            {declineLabel}
          </button>
          {/* The rule is about ORDER, not location: an approve control for an
              irreversible write must not be reachable in one click from the
              collapsed card. So there is still no `approveTestId` control for
              one — approving happens in the body, below the payload that
              justifies it. A reversible write keeps its one-click Approve,
              which is the entire point of a compact card. */}
          {irreversible ? null : (
            <button
              type="button"
              className="ui-button ui-button--sm ui-button--primary"
              data-testid={approveTestId}
              disabled={busy}
              onClick={onApprove}
            >
              {approveLabel}
            </button>
          )}
          {/* TWO DISCLOSURES, ON PURPOSE.
           *
           * A chevron is the right control when expanding is optional — it is
           * 22px of quiet furniture next to a decision you can already make.
           * That is the reversible case, and it is what the design draws.
           *
           * It is the WRONG control when expanding is the only way forward. For
           * an irreversible write Approve is deliberately withheld until the
           * payload has been seen, so the disclosure IS the primary action, and
           * a 22px unlabelled glyph would hide the single path through the card
           * — the dead-control regression in another costume. That one keeps a
           * named button, in ember rather than sky because the card is
           * destructive.
           *
           * Both carry `tc-write-gate-review`: it is the "open the detail"
           * control in either costume, and a live journey clicks it by that
           * name on the reversible lane that production actually emits.
           *
           * Neither changes width when toggled — the label is state-invariant
           * and the chevron is fixed-size — so the header row is byte-identical
           * collapsed and expanded and the actions never move. */}
          {irreversible ? (
            <button
              type="button"
              className="ui-button ui-button--sm ui-button--danger tc-write-gate__review"
              data-testid="tc-write-gate-review"
              aria-expanded={open}
              disabled={busy}
              onClick={toggle}
            >
              Review
              <Icon name="chevronDown" size={12} strokeWidth={2} />
            </button>
          ) : (
            <button
              type="button"
              className="ui-icon-button ui-icon-button--ghost tc-write-gate__chev"
              data-testid="tc-write-gate-review"
              aria-expanded={open}
              aria-label={
                open ? "Hide what it will send" : "Show what it will send"
              }
              disabled={busy}
              onClick={toggle}
            >
              <Icon name="chevronDown" size={12} strokeWidth={2} />
            </button>
          )}
        </span>
      </div>
      {open ? (
        <div className="tc-write-gate__body" data-testid="tc-write-gate-body">
          {reason === null || reason.length === 0 ? null : (
            <p
              className="tc-write-gate__reason"
              data-testid="tc-write-gate-body-reason"
            >
              {reason}
            </p>
          )}
          <TcWriteGatePayload
            params={params}
            presentation={presentation}
            commandText={commandText}
            irreversible={irreversible}
            ledgerId={ledgerId}
            testIdPrefix="tc-write-gate-body"
          />
          {bodyApprove ? (
            // A DISTINCT testid from the header's, deliberately: the assertion
            // that an irreversible write exposes no `approveTestId` control
            // keeps its original meaning — no one-click approval — rather than
            // silently becoming true of a button that is now two clicks away.
            // The distinction has to hold under PREFIX matching too, which is
            // why the scoped names are `…-approve-<id>` and
            // `…-body-approve-<id>` rather than `…-approve-body-<id>`.
            <button
              type="button"
              className="ui-button ui-button--sm ui-button--primary"
              data-testid={bodyApproveTestId}
              disabled={busy}
              onClick={onApprove}
            >
              {approveLabel}
            </button>
          ) : null}
          {scopeApprove ? (
            // A GHOST, deliberately, beside a primary Approve that is one click
            // away in the header. The wider decision must never be the louder
            // one: a run-scoped rule is the convenient answer, and drawing it as
            // the recommended answer is how people stop reading the cards.
            //
            // The sentence under it is the scope, in words, because "always" is
            // the one thing on this card the wire word gets wrong — the rule
            // this writes expires with the run.
            <div className="tc-write-gate__scope">
              <button
                type="button"
                className="ui-button ui-button--sm ui-button--ghost tc-write-gate__scope-btn"
                data-testid={alwaysApproveTestId}
                disabled={busy}
                onClick={onApproveAlways}
              >
                {ALWAYS_LABEL}
              </button>
              <span
                className="tc-write-gate__scope-note"
                data-testid="tc-write-gate-body-scope-note"
              >
                Applies to this call only, and ends when the run does.
              </span>
            </div>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}
