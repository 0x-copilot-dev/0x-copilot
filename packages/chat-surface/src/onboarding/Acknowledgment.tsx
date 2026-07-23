// Acknowledgment — State C of the FTUE (PRD-P3 §3.5, PRD-P8 §7, SPEC §Copy
// strings).
//
// Pure presentational: renders the variant title + the three mono echo lines
// (model · tools · privacy) with a jade check. It owns NO timing and does NO
// I/O — the host binder drives the run-create + the ~1.5s handoff (via
// `useFirstRunLaunch`) and picks the variant from the launch phase. An optional
// `error` line keeps a rare queued-then-failed create from being a silent dead
// end.
//
// PRD-P8 §7 adds the third variant. `queued` promises "starts when the model
// lands"; once the model demonstrably is NOT landing that title is a lie the
// echo lines directly contradict ("· download paused at 40%"), and the ack has
// no control, so the FTUE terminates on a screen that argues with itself. The
// `stalled` variant is the honest title plus the two optional slots that make it
// actionable — `note` and `actionLabel`/`onAction` — both omitted-means-nothing,
// exactly as `FirstRunLocalCard` treats its optional callbacks.

import type { ReactElement } from "react";

import { Button } from "@0x-copilot/design-system";

import { FIRST_RUN_COPY } from "./firstRun";

/**
 * Title copy — byte-verbatim vs SPEC. Pinned by the unit test.
 *
 * `stalled` is a REFERENCE to `FIRST_RUN_COPY.ack.stalled.title`, never a second
 * literal: PRD-P8 §5 puts every new FTUE string in `firstRun.ts`, and the launch
 * lane reads the same object through `firstRunAckLines`.
 */
export const FIRST_RUN_ACK_TITLES = {
  starting: "Starting your first run",
  queued: "Queued — starts when the model lands",
  stalled: FIRST_RUN_COPY.ack.stalled.title,
} as const;

export type AcknowledgmentVariant = "starting" | "queued" | "stalled";

export interface AcknowledgmentProps {
  readonly variant: AcknowledgmentVariant;
  readonly modelLine: string;
  readonly toolsLine: string;
  readonly privacyLine: string;
  /** Rare: a queued create that later failed. Rendered below the lines. */
  readonly error?: string | null;
  /**
   * P8 §7 — the sub-line under the title (`firstRunAckNote`). Only `stalled`
   * has one; omitted/null ⇒ nothing renders.
   */
  readonly note?: string | null;
  /**
   * P8 §7 — the label for the ack's one action (`firstRunAckAction`). Rendered
   * only when BOTH the label and `onAction` are supplied, so a host that has
   * not wired the escape never gets a button that does nothing.
   */
  readonly actionLabel?: string | null;
  /** P8 §7 — bound to `FirstRunAckCtx.onBack` (un-sends, re-opens composer). */
  readonly onAction?: () => void;
}

function AckLine({ text }: { readonly text: string }): ReactElement {
  return (
    <p className="ln">
      <span className="ln__check" aria-hidden="true">
        ✓
      </span>
      <span className="ln__text">{text}</span>
    </p>
  );
}

export function Acknowledgment({
  variant,
  modelLine,
  toolsLine,
  privacyLine,
  error = null,
  note = null,
  actionLabel = null,
  onAction,
}: AcknowledgmentProps): ReactElement {
  const showAction =
    actionLabel !== null && actionLabel !== "" && onAction !== undefined;
  return (
    <div className="fr-ack" data-testid="first-run-ack" data-variant={variant}>
      <h1 className="fr-ack__title" data-testid="first-run-ack-title">
        {FIRST_RUN_ACK_TITLES[variant]}
      </h1>
      <div className="fr-ack__lines">
        <AckLine text={modelLine} />
        <AckLine text={toolsLine} />
        <AckLine text={privacyLine} />
      </div>
      {note !== null && note !== "" ? (
        <p className="fr-ack__note" data-testid="first-run-ack-note">
          {note}
        </p>
      ) : null}
      {error !== null && error !== "" ? (
        <p
          className="fr-ack__error"
          role="alert"
          data-testid="first-run-ack-error"
        >
          {error}
        </p>
      ) : null}
      {showAction ? (
        <Button
          type="button"
          variant="primary"
          size="sm"
          onClick={onAction}
          data-testid="first-run-ack-back"
        >
          {actionLabel}
        </Button>
      ) : null}
    </div>
  );
}
