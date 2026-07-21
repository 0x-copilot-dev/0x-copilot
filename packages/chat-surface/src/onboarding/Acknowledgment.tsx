// Acknowledgment — State C of the FTUE (PRD-P3 §3.5, SPEC §Copy strings).
//
// Pure presentational: renders the variant title + the three mono echo lines
// (model · tools · privacy) with a jade check. It owns NO timing and does NO
// I/O — the host binder drives the run-create + the ~1.5s handoff (via
// `useFirstRunLaunch`) and picks the variant from the launch phase. An optional
// `error` line keeps a rare queued-then-failed create from being a silent dead
// end.

import type { ReactElement } from "react";

/** Title copy — byte-verbatim vs SPEC. Pinned by the unit test. */
export const FIRST_RUN_ACK_TITLES = {
  starting: "Starting your first run",
  queued: "Queued — starts when the model lands",
} as const;

export type AcknowledgmentVariant = "starting" | "queued";

export interface AcknowledgmentProps {
  readonly variant: AcknowledgmentVariant;
  readonly modelLine: string;
  readonly toolsLine: string;
  readonly privacyLine: string;
  /** Rare: a queued create that later failed. Rendered below the lines. */
  readonly error?: string | null;
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
}: AcknowledgmentProps): ReactElement {
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
      {error !== null && error !== "" ? (
        <p className="fr-ack__error" role="alert" data-testid="first-run-ack-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}
