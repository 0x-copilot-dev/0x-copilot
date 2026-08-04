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

import type { CSSProperties, ReactElement } from "react";

import type { ActivityParam } from "../approvals/types";

/** Byte-exact copy — asserted verbatim by `TcWriteGateCard.test.tsx`. */
export const REVERSIBLE_COPY =
  "You can undo this from the connector if it's wrong.";
export const IRREVERSIBLE_COPY = "This cannot be undone from here.";

export interface TcWriteGatePayloadProps {
  readonly params: readonly ActivityParam[];
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

export function TcWriteGatePayload({
  params,
  irreversible,
  ledgerId,
  testIdPrefix,
}: TcWriteGatePayloadProps): ReactElement {
  return (
    <>
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
