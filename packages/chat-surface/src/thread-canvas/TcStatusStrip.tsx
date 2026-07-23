// Status strip (Generative Surfaces v2, PRD-B2 D6 / FR-F2).
//
// One line at the bottom of the v2 canvas mirroring the run's latest ledger
// beat. A pure render of the `StatusStripLine` the selector produced; mounted by
// ThreadCanvas only when the v2 canvas is on. The `"gate"` arm is a typed stub —
// unreachable until PRD-C2 emits `gate.opened`.

import type { CSSProperties, ReactElement } from "react";

import { StatusLine } from "@0x-copilot/design-system";

import type { StatusStripLine } from "./statusLine";

export interface TcStatusStripProps {
  readonly line: StatusStripLine;
}

const rootStyle: CSSProperties = {
  padding: "4px 12px",
  borderTop: "1px solid var(--color-border-subtle)",
  background: "var(--color-surface)",
};

function textFor(line: StatusStripLine): string {
  switch (line.kind) {
    case "idle":
      return "No activity yet";
    case "assembling":
      return line.text !== "" ? line.text : "Assembling view…";
    case "gate":
      // Reserved stub — PRD-C2 fills the gate context (FR-F2).
      return line.text;
    case "op":
    default:
      return line.text;
  }
}

export function TcStatusStrip({ line }: TcStatusStripProps): ReactElement {
  return (
    <div style={rootStyle} data-testid="tc-status-strip" role="status">
      <StatusLine data-status-kind={line.kind}>{textFor(line)}</StatusLine>
    </div>
  );
}
