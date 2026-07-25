// Canonical pending-work v2.1 client projection (E1 D6).
//
// The server response is intentionally narrow: it names only an authorised
// run, an opaque canonical subject, and its ledger-derived state. This selector
// preserves that contract. It never invents a title from a subject id, opens a
// reference, or carries arbitrary server text into the UI.

import type {
  PendingWorkItemV2,
  PendingWorkStatusV2,
  PendingWorkSubjectKindV2,
} from "@0x-copilot/api-types";

/** A safe, UI-routable pending subject. IDs remain opaque and are never display
 * text; the rail renders only the controlled labels below. */
export interface PendingWorkCardV2 {
  readonly runId: string;
  readonly subjectKind: PendingWorkSubjectKindV2;
  readonly subjectId: string;
  readonly status: PendingWorkStatusV2;
  readonly openedSeq: number;
  readonly latestSeq: number;
}

/** Stable identity across response pages and refreshes. */
export function pendingWorkCardV2Key(card: PendingWorkCardV2): string {
  return `${card.runId}::${card.subjectKind}::${card.subjectId}`;
}

/**
 * Project one or more server pages into a deterministic card list.
 *
 * The API deliberately orders candidate runs and subjects. We preserve the
 * first server ordinal, while duplicate records resolve to the newest known
 * ledger state. This makes page append / retry deterministic without letting a
 * stale duplicate regress a card.
 */
export function projectPendingWorkV2(
  items: readonly PendingWorkItemV2[],
): readonly PendingWorkCardV2[] {
  const byKey = new Map<
    string,
    { readonly card: PendingWorkCardV2; readonly ordinal: number }
  >();

  for (const [ordinal, item] of items.entries()) {
    const candidate = toCard(item);
    const key = pendingWorkCardV2Key(candidate);
    const previous = byKey.get(key);
    if (previous === undefined) {
      byKey.set(key, { card: candidate, ordinal });
      continue;
    }
    if (isNewer(candidate, previous.card)) {
      // Keep the original position: the server's page/run ordering remains
      // stable even when a later page supplies a fresher duplicate state.
      byKey.set(key, { card: candidate, ordinal: previous.ordinal });
    }
  }

  return [...byKey.values()]
    .sort((a, b) => a.ordinal - b.ordinal)
    .map(({ card }) => card);
}

/** Controlled copy: no subject ID, target, path, or server-supplied text. */
export function pendingWorkSubjectLabelV2(
  subjectKind: PendingWorkSubjectKindV2,
): string {
  switch (subjectKind) {
    case "effect":
      return "PROPOSED CHANGE";
    case "gate":
      return "ACCESS NEEDED";
  }
}

/** Controlled state copy: the enum is validated at the API boundary. */
export function pendingWorkStatusLabelV2(status: PendingWorkStatusV2): string {
  switch (status) {
    case "open":
      return "Ready for review";
    case "held":
      return "Held for review";
    case "queued":
      return "Queued to apply";
    case "approved":
      return "Approved, waiting to apply";
    case "claimed":
      return "Applying now";
    case "indeterminate":
    case "recovery":
      return "Needs recovery";
  }
}

function toCard(item: PendingWorkItemV2): PendingWorkCardV2 {
  return {
    runId: item.run_id,
    subjectKind: item.subject_kind,
    subjectId: item.subject_id,
    status: item.status,
    openedSeq: item.opened_sequence_no,
    latestSeq: item.latest_sequence_no,
  };
}

function isNewer(
  candidate: PendingWorkCardV2,
  current: PendingWorkCardV2,
): boolean {
  return (
    candidate.latestSeq > current.latestSeq ||
    (candidate.latestSeq === current.latestSeq &&
      candidate.openedSeq > current.openedSeq)
  );
}
