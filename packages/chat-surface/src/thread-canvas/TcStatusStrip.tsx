// Status strip (Generative Surfaces v2, PRD-B2 D6 / FR-F2).
//
// One line at the bottom of the v2 canvas, drawn ONLY while a surface is still
// being built. A settled surface renders nothing — see `statusLine.ts` for why
// the old `event_type · connector.op · ledgerId` line was removed rather than
// relabelled.
//
// Returning `null` unmounts the wrapper, not just its text. That is the point:
// the root carries a `borderTop`, so an "empty" strip would still paint a rule
// across the canvas and reserve its padding. This package already draws that
// distinction for a settled approval, whose `<li>` is skipped for the same
// reason — an empty row is not the same as no row.
//
// The a11y trade is deliberate. `role="status"` is a polite live region, and a
// region that unmounts can miss an announcement; keeping it mounted to preserve
// that would mean permanently reserving a rule and padding for a state that has
// nothing to say. The transient "Shaping…" is not the payload here — the surface
// arriving is, and it announces itself.
//
// Not the mini-timeline's mistake, though the shape rhymes. That strip is
// PERMANENT chrome and used to unmount when a new run reset its projection to
// zero beads, so it blinked out at the moment the user pressed send — which
// reads as a crash (see the note in `ThreadCanvas`). This strip is the opposite
// by construction: it is a progress indicator, so appearing when work starts and
// leaving when it finishes IS its semantics, and it leaves in the same frame the
// finished surface arrives to fill the space.

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

export function TcStatusStrip({
  line,
}: TcStatusStripProps): ReactElement | null {
  if (line.kind === "idle") return null;

  return (
    <div style={rootStyle} data-testid="tc-status-strip" role="status">
      <StatusLine data-status-kind={line.kind}>Shaping…</StatusLine>
    </div>
  );
}
