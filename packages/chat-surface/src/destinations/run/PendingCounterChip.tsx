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
  /**
   * True when NONE of the counted items belongs to the run on screen.
   *
   * It sits beside `PostureChip`, so "Writes wait for you" + "2 waiting" reads
   * as one sentence — "2 writes are waiting for you, here" — while every item
   * can be parked in some other conversation. Saying "elsewhere" costs one word
   * and stops the reader hunting this thread for work that is not in it.
   */
  readonly allElsewhere?: boolean;
  /** Opens the Approvals rail tab. */
  readonly onClick: () => void;
}

export function PendingCounterChip({
  count,
  allElsewhere = false,
  onClick,
}: PendingCounterChipProps): ReactElement | null {
  if (count <= 0) {
    // Hidden at zero — nothing is waiting, so the chip does not exist.
    return null;
  }
  const label = allElsewhere ? "elsewhere" : "waiting";
  return (
    <button
      type="button"
      className="ui-pill"
      data-testid="pending-counter-chip"
      data-count={count}
      data-scope={allElsewhere ? "elsewhere" : "here"}
      onClick={onClick}
      aria-label={`${count} pending ${allElsewhere ? "in other chats" : "here"} — open Approvals`}
    >
      {count} {label}
    </button>
  );
}
