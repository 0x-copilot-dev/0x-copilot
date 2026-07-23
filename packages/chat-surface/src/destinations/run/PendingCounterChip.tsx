// PendingCounterChip — the "N waiting" merged pending counter (PRD-E2 / FR-F3). 🎨
//
// A single chip tracking the cross-run pending total (parked gates + held drafts
// + undecided row-sets). Hidden at N=0; clicking it opens the Approvals rail tab.
// Pure presentational — the host threads the merged `count` (from `usePendingWork`)
// and the tab-open callback. Mounts beside C2's `PostureChip`.
//
// Kit-only styling: the design-system `.ui-pill` recipe (a small, quiet counter
// pill). No host-app one-off styling, no raw font-size / letter-spacing.
//
// Boundary: framework-agnostic — no bare window/document/fetch; tokens only.

import type { ReactElement } from "react";

export interface PendingCounterChipProps {
  /** Merged cross-run pending total. */
  readonly count: number;
  /** Opens the Approvals rail tab. */
  readonly onClick: () => void;
}

export function PendingCounterChip({
  count,
  onClick,
}: PendingCounterChipProps): ReactElement | null {
  if (count <= 0) {
    // Hidden at zero — nothing is waiting, so the chip does not exist.
    return null;
  }
  return (
    <button
      type="button"
      className="ui-pill"
      data-testid="pending-counter-chip"
      data-count={count}
      onClick={onClick}
      aria-label={`${count} pending — open Approvals`}
    >
      {count} waiting
    </button>
  );
}
