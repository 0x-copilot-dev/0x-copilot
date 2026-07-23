// Pending-work card selector (Generative Surfaces v2, PRD-E2 / SDR §5).
//
// `projectPendingCards` is a pure selector over the SAME `session.events` array
// `projectApprovals` / `projectLedger` consume — a PEER of those projectors,
// never a second SSE subscription (FR-3.3, the one-projector invariant). It
// REUSES `projectLedger`'s already-folded gates + stages rather than re-folding,
// then applies the §5 pending predicate — the TypeScript twin of the Python
// `PendingWorkFold`
// (`services/ai-backend/src/agent_runtime/surfaces_v2/pending_work.py`):
//
//   * an open gate is pending until its matching `gate.resolved` (either outcome)
//     — `projectLedger.openGates` already isolates these;
//   * a single-artifact stage is pending only while `status === "staged"`;
//   * a row-set is pending while `status === "staged"` AND some row is still
//     undecided-by-the-user (`rowsPending` counts rows whose `decidedBy` is
//     neither `"user"` nor `"policy"` and that carry no `applyOutcome`).
//
// Every card field derives from the SAME in-event fields as the Python fold
// (§2 "Field derivation") so the ts⇄py pending-parity holds — the selector never
// dereferences a `proposal_ref`, and hostile title/purpose strings are DATA
// (rendered as text nodes only, never HTML). Cards are ordered ascending by
// `openedSeq` (the cross-run newest-first merge lives in `usePendingWork`).

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import {
  projectLedger,
  type LedgerGate,
  type LedgerStagedWrite,
} from "../../thread-canvas/ledgerProjection";

/** The human target separator — byte-identical to the Python `Titles.SEPARATOR`
 *  so a stage card's `title` matches across languages. */
const TARGET_SEPARATOR = " · ";

/** One thing waiting on the user in the open run — the card the Approvals rail
 *  renders and `usePendingWork` merges with the cross-run fetch. */
export interface PendingCard {
  readonly itemKind: "gate" | "staged_write";
  /** The run this card belongs to (the current run for this selector). */
  readonly runId: string;
  readonly gateId: string | null;
  readonly stageId: string | null;
  /** Canvas jump target (stage cards only; a gate has no surface). */
  readonly surfaceId: string | null;
  /** Gate: the purpose line. Stage: the `"{connector} · {op}"` target line. */
  readonly title: string;
  readonly connector: string;
  /** `r<short>·<seq>` of the opening event (A1 formatter, via `projectLedger`). */
  readonly ledgerId: string;
  /** `sequence_no` of the opening event — the stable card order key. */
  readonly openedSeq: number;
  /** Row-sets only: rows still undecided-by-the-user; null for gates / D1 stages. */
  readonly rowsPending: number | null;
  /** Row-sets only: total rows; null for gates / single-artifact stages. */
  readonly rowsTotal: number | null;
}

/**
 * Project a run's events into its pending cards (gates + held stages).
 *
 * Deterministic + total (mirrors the Python fold, the parity referee): reuses
 * `projectLedger` (which sorts by `sequence_no`, dedupes SSE resends, and
 * tolerates malformed payloads), then filters to the §5 pending set. Returns
 * cards ascending by `openedSeq`.
 */
export function projectPendingCards(
  events: readonly RuntimeEventEnvelope[],
  runId: string | null,
): readonly PendingCard[] {
  const ledger = projectLedger(events);
  const cardRunId = runId ?? ledger.runId;

  const cards: PendingCard[] = [];
  for (const gate of ledger.openGates) {
    cards.push(gateCard(gate, cardRunId));
  }
  for (const stage of ledger.stages.values()) {
    const [pending, rowsPending, rowsTotal] = stagePending(stage);
    if (!pending) continue;
    cards.push(stageCard(stage, cardRunId, rowsPending, rowsTotal));
  }
  cards.sort((a, b) => a.openedSeq - b.openedSeq);
  return cards;
}

function gateCard(gate: LedgerGate, runId: string): PendingCard {
  return {
    itemKind: "gate",
    runId,
    gateId: gate.gateId,
    stageId: null,
    surfaceId: null,
    title: gate.purpose,
    connector: gate.connector,
    ledgerId: gate.ledgerId,
    openedSeq: gate.createdSeq,
    rowsPending: null,
    rowsTotal: null,
  };
}

function stageCard(
  stage: LedgerStagedWrite,
  runId: string,
  rowsPending: number | null,
  rowsTotal: number | null,
): PendingCard {
  return {
    itemKind: "staged_write",
    runId,
    gateId: null,
    stageId: stage.stageId,
    surfaceId: stage.surfaceId.length > 0 ? stage.surfaceId : null,
    title: `${stage.target.connector}${TARGET_SEPARATOR}${stage.target.op}`,
    connector: stage.target.connector,
    ledgerId: stage.ledgerId,
    openedSeq: stage.createdSeq,
    rowsPending,
    rowsTotal,
  };
}

/**
 * The §5 stage pending predicate → `[pending, rowsPending, rowsTotal]`.
 *
 * Single-artifact (`rows === null`): pending iff `status === "staged"`.
 * Row-set: pending iff `status === "staged"` AND at least one row is still
 * undecided-by-the-user. `rowsPending` counts rows whose `decidedBy` is neither
 * `"user"` nor `"policy"` (an agent pre-hold is NOT a user decision — the row
 * still waits, FR-C7) AND that carry no `applyOutcome`.
 */
function stagePending(
  stage: LedgerStagedWrite,
): [boolean, number | null, number | null] {
  if (stage.rows === null) {
    return [stage.status === "staged", null, null];
  }
  const rowsTotal = stage.rows.length;
  const rowsPending = stage.rows.filter(
    (row) =>
      row.decidedBy !== "user" &&
      row.decidedBy !== "policy" &&
      row.applyOutcome === null,
  ).length;
  if (stage.status !== "staged") {
    return [false, rowsPending, rowsTotal];
  }
  return [rowsPending > 0, rowsPending, rowsTotal];
}
