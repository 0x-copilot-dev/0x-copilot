// projectReceipt — the run receipt as a pure client fold (Generative Surfaces v2,
// PRD-E1, SDR §5/§7 S6).
//
// A PEER of `projectCitations` / `projectLedgerSources` over the SAME
// `session.events` array (the one-projector invariant, FR-3.3) — never a second
// SSE subscription. It is the TypeScript twin of PRD-E1's Python `ReceiptFold`
// (`services/ai-backend/src/agent_runtime/surfaces_v2/receipt.py`): folding the
// same run's events yields a byte-identical `RunReceipt`, pinned by the shared
// `work_ledger_expected_receipt.json` fixture (`projectReceipt.test.ts`). Drift
// on either side fails CI.
//
// The receipt is null until a `receipt.emitted` event is seen (the server emits
// it, with a matching `surface.created {kind: receipt}`, at run termination); at
// that moment the receipt tab exists and this fold renders it. Stage terminal
// status is reused from `projectLedger` (the TS twin of `StagedWriteFold`) — the
// receipt fold never re-derives it a second way.

import type {
  RunReceipt,
  RunReceiptRow,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";
import { formatLedgerId } from "@0x-copilot/api-types";

import {
  projectLedger,
  type LedgerStagedWriteStatus,
} from "../../thread-canvas/ledgerProjection";

export interface ReceiptProjection {
  /** The folded receipt, or null until a `receipt.emitted` event is seen. */
  readonly receipt: RunReceipt | null;
  /** The highest `receipt.emitted` sequence number seen, or null. */
  readonly emittedSeq: number | null;
}

const RECEIPT_SURFACE_PREFIX = "receipt://";
const FOLD_REF_PREFIX = "ledger://";
const FOLD_REF_SEPARATOR = "@";
const TITLE_SEPARATOR = " · ";

/**
 * Project the run stream into the receipt. Pure over `events` (referentially
 * stable for an unchanged array via the caller's `useMemo`). `receipt` is null
 * until a `receipt.emitted` event lands.
 */
export function projectReceipt(
  events: readonly RuntimeEventEnvelope[],
): ReceiptProjection {
  let emittedSeq: number | null = null;
  for (const event of events) {
    if (event.event_type === "receipt.emitted") {
      const seq = seqOf(event);
      if (emittedSeq === null || seq > emittedSeq) emittedSeq = seq;
    }
  }
  if (emittedSeq === null) {
    return { receipt: null, emittedSeq: null };
  }
  return { receipt: foldReceipt(events), emittedSeq };
}

// ---------------------------------------------------------------------------
// Fold (the TS twin of the Python ReceiptFold — §2 rules verbatim)
// ---------------------------------------------------------------------------

interface RowDraft {
  seq: number;
  ledgerId: string;
  eventType: RunReceiptRow["event_type"];
  title: string;
  attribution: RunReceiptRow["attribution"];
  at: string;
}

interface StageFacts {
  surfaceId: string;
  rowsStaged: number;
  approveActors: string[];
  lastHoldSeq: number | null;
  lastHoldAt: string;
  lastDecisionSeq: number | null;
  lastDecisionAt: string;
  stagedSeq: number;
  stagedAt: string;
  appliedCount: number;
  applyEvents: Array<[number, string]>;
  lastApplySeq: number | null;
  lastApplyAt: string;
  hasAnyApply: boolean;
}

/** Fold a run's events into a {@link RunReceipt} (deterministic + total). */
export function foldReceipt(
  events: readonly RuntimeEventEnvelope[],
): RunReceipt {
  const ordered = [...events].sort((a, b) => seqOf(a) - seqOf(b));

  // Reuse the client ledger fold for per-stage terminal status ONLY.
  const ledger = projectLedger(ordered);
  const runId = ledger.runId;

  const readRows: RowDraft[] = [];
  const surfaceTitles = new Map<string, string>();
  const surfaceTitleByPayloadRef = new Map<string, string>();
  const rawSurfaceRows = new Map<string, RowDraft>();
  const rejectRows: RowDraft[] = [];
  const stages = new Map<string, StageFacts>();
  let throughSeq = 0;
  let generatedAt = "";

  for (const event of ordered) {
    const seq = seqOf(event);
    const createdAt =
      typeof event.created_at === "string" ? event.created_at : "";
    const payload = asRecord(event.payload) ?? {};
    if (seq > throughSeq) {
      throughSeq = seq;
      generatedAt = createdAt;
    }

    switch (event.event_type) {
      case "surface.created":
        noteSurface(
          payload,
          seq,
          createdAt,
          runId,
          surfaceTitles,
          surfaceTitleByPayloadRef,
          rawSurfaceRows,
        );
        break;
      case "read.executed":
        readRows.push(
          readRow(payload, seq, createdAt, runId, surfaceTitleByPayloadRef),
        );
        break;
      case "view.derived":
        noteRawView(
          payload,
          seq,
          createdAt,
          runId,
          surfaceTitles,
          rawSurfaceRows,
        );
        break;
      case "write.staged":
        noteWriteStaged(payload, seq, createdAt, stages);
        break;
      case "decision.recorded":
        noteDecision(
          payload,
          seq,
          createdAt,
          runId,
          stages,
          rejectRows,
          surfaceTitles,
        );
        break;
      case "write.applied":
        noteWriteApplied(payload, seq, createdAt, stages);
        break;
      default:
        break; // tolerate + ignore every other event type
    }
  }

  const { tiles, heldRows } = tilesAndHolds(
    runId,
    readRows.length,
    stages,
    ledger.stages,
  );
  const appliedRows = buildAppliedRows(runId, stages, surfaceTitles);

  const drafts: RowDraft[] = [
    ...readRows,
    ...appliedRows,
    ...rejectRows,
    ...rawSurfaceRows.values(),
    ...heldRows,
  ];
  drafts.sort((a, b) => a.seq - b.seq);
  const rows: RunReceiptRow[] = drafts.map((d) => ({
    ledger_id: d.ledgerId,
    event_type: d.eventType,
    title: d.title,
    attribution: d.attribution,
    at: d.at,
  }));

  return {
    run_id: runId,
    surface_id: `${RECEIPT_SURFACE_PREFIX}${runId}`,
    fold_ref: `${FOLD_REF_PREFIX}${runId}${FOLD_REF_SEPARATOR}${throughSeq}`,
    generated_at: generatedAt,
    tiles,
    rows,
  };
}

function noteSurface(
  payload: Record<string, unknown>,
  seq: number,
  createdAt: string,
  runId: string,
  surfaceTitles: Map<string, string>,
  surfaceTitleByPayloadRef: Map<string, string>,
  rawSurfaceRows: Map<string, RowDraft>,
): void {
  const surfaceId = strOr(payload.surface_id, "");
  if (surfaceId === "") return;
  const title = strOr(payload.title, "");
  if (!surfaceTitles.has(surfaceId)) surfaceTitles.set(surfaceId, title);
  const payloadRef = strOr(payload.payload_ref, "");
  if (payloadRef !== "" && !surfaceTitleByPayloadRef.has(payloadRef)) {
    surfaceTitleByPayloadRef.set(payloadRef, title);
  }
  if (strOr(payload.kind, "") === "raw" && !rawSurfaceRows.has(surfaceId)) {
    rawSurfaceRows.set(surfaceId, {
      seq,
      ledgerId: safeLedgerId(runId, seq),
      eventType: "surface.created",
      title,
      attribution: "no_view_fit",
      at: createdAt,
    });
  }
}

function readRow(
  payload: Record<string, unknown>,
  seq: number,
  createdAt: string,
  runId: string,
  surfaceTitleByPayloadRef: Map<string, string>,
): RowDraft {
  const connector = strOr(payload.connector, "");
  const op = strOr(payload.op, "");
  const payloadRef = strOr(payload.payload_ref, "");
  let title = surfaceTitleByPayloadRef.get(payloadRef) ?? "";
  if (title === "") title = `${connector}${TITLE_SEPARATOR}${op}`;
  return {
    seq,
    ledgerId: safeLedgerId(runId, seq),
    eventType: "read.executed",
    title,
    attribution: "auto_ran",
    at: createdAt,
  };
}

function noteRawView(
  payload: Record<string, unknown>,
  seq: number,
  createdAt: string,
  runId: string,
  surfaceTitles: Map<string, string>,
  rawSurfaceRows: Map<string, RowDraft>,
): void {
  if (strOr(payload.tier, "") !== "raw") return;
  const surfaceId = strOr(payload.surface_id, "");
  if (surfaceId === "" || rawSurfaceRows.has(surfaceId)) return;
  rawSurfaceRows.set(surfaceId, {
    seq,
    ledgerId: safeLedgerId(runId, seq),
    eventType: "view.derived",
    title: surfaceTitles.get(surfaceId) ?? "",
    attribution: "no_view_fit",
    at: createdAt,
  });
}

function noteWriteStaged(
  payload: Record<string, unknown>,
  seq: number,
  createdAt: string,
  stages: Map<string, StageFacts>,
): void {
  const stageId = strOr(payload.stage_id, "");
  if (stageId === "" || stages.has(stageId)) return;
  stages.set(stageId, {
    surfaceId: strOr(payload.surface_id, ""),
    rowsStaged: rowsOrOne(payload.rows),
    approveActors: [],
    lastHoldSeq: null,
    lastHoldAt: "",
    lastDecisionSeq: null,
    lastDecisionAt: "",
    stagedSeq: seq,
    stagedAt: createdAt,
    appliedCount: 0,
    applyEvents: [],
    lastApplySeq: null,
    lastApplyAt: "",
    hasAnyApply: false,
  });
}

function noteDecision(
  payload: Record<string, unknown>,
  seq: number,
  createdAt: string,
  runId: string,
  stages: Map<string, StageFacts>,
  rejectRows: RowDraft[],
  surfaceTitles: Map<string, string>,
): void {
  const stageId = strOr(payload.stage_id, "");
  const facts = stages.get(stageId);
  const decision = strOr(payload.decision, "");
  const actor = strOr(payload.actor, "");
  if (facts !== undefined) {
    facts.lastDecisionSeq = seq;
    facts.lastDecisionAt = createdAt;
    if (decision === "approve") facts.approveActors.push(actor);
    else if (decision === "hold") {
      facts.lastHoldSeq = seq;
      facts.lastHoldAt = createdAt;
    }
  }
  if (decision === "reject") {
    rejectRows.push({
      seq,
      ledgerId: safeLedgerId(runId, seq),
      eventType: "decision.recorded",
      title:
        facts !== undefined ? (surfaceTitles.get(facts.surfaceId) ?? "") : "",
      attribution: "rejected",
      at: createdAt,
    });
  }
}

function noteWriteApplied(
  payload: Record<string, unknown>,
  seq: number,
  createdAt: string,
  stages: Map<string, StageFacts>,
): void {
  const stageId = strOr(payload.stage_id, "");
  const facts = stages.get(stageId);
  if (facts === undefined) return;
  facts.hasAnyApply = true;
  facts.lastApplySeq = seq;
  facts.lastApplyAt = createdAt;
  const result = strOr(payload.result, "");
  if (result === "applied" || result === "partial") {
    facts.applyEvents.push([seq, createdAt]);
    facts.appliedCount += rowKeysOrOne(payload.row_keys);
  }
}

function tilesAndHolds(
  runId: string,
  readCount: number,
  stages: Map<string, StageFacts>,
  ledgerStages: ReadonlyMap<string, { status: LedgerStagedWriteStatus }>,
): { tiles: RunReceipt["tiles"]; heldRows: RowDraft[] } {
  let writesProposed = 0;
  let writesApproved = 0;
  let holdsUntouched = 0;
  const heldRows: RowDraft[] = [];

  for (const [stageId, facts] of stages) {
    writesProposed += facts.rowsStaged;
    writesApproved += facts.appliedCount;
    const status = ledgerStages.get(stageId)?.status ?? "staged";
    const remainder = heldRemainder(facts, status);
    holdsUntouched += remainder;
    if (remainder > 0) heldRows.push(heldRow(facts, runId, remainder));
  }

  return {
    tiles: {
      reads_auto_ran: readCount,
      writes_proposed: writesProposed,
      writes_approved: writesApproved,
      holds_untouched: holdsUntouched,
    },
    heldRows,
  };
}

function heldRemainder(
  facts: StageFacts,
  status: LedgerStagedWriteStatus,
): number {
  if (facts.hasAnyApply)
    return Math.max(0, facts.rowsStaged - facts.appliedCount);
  if (status === "rejected") return facts.rowsStaged;
  return 0;
}

function heldRow(facts: StageFacts, runId: string, count: number): RowDraft {
  let seq: number;
  let at: string;
  let eventType: RunReceiptRow["event_type"];
  if (facts.lastHoldSeq !== null) {
    seq = facts.lastHoldSeq;
    at = facts.lastHoldAt;
    eventType = "decision.recorded";
  } else if (facts.lastApplySeq !== null) {
    seq = facts.lastApplySeq;
    at = facts.lastApplyAt;
    eventType = "write.applied";
  } else if (facts.lastDecisionSeq !== null) {
    seq = facts.lastDecisionSeq;
    at = facts.lastDecisionAt;
    eventType = "decision.recorded";
  } else {
    seq = facts.stagedSeq;
    at = facts.stagedAt;
    eventType = "write.staged";
  }
  return {
    seq,
    ledgerId: safeLedgerId(runId, seq),
    eventType,
    title: `${count} rows held, untouched`,
    attribution: "held",
    at,
  };
}

function buildAppliedRows(
  runId: string,
  stages: Map<string, StageFacts>,
  surfaceTitles: Map<string, string>,
): RowDraft[] {
  const rows: RowDraft[] = [];
  for (const facts of stages.values()) {
    const attribution: RunReceiptRow["attribution"] = facts.approveActors.some(
      (a) => a === "policy",
    )
      ? "auto_applied"
      : "approved";
    const title = surfaceTitles.get(facts.surfaceId) ?? "";
    for (const [seq, at] of facts.applyEvents) {
      rows.push({
        seq,
        ledgerId: safeLedgerId(runId, seq),
        eventType: "write.applied",
        title,
        attribution,
        at,
      });
    }
  }
  return rows;
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function strOr(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function rowsOrOne(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : 1;
}

function rowKeysOrOne(value: unknown): number {
  return Array.isArray(value) ? value.length : 1;
}

function seqOf(event: RuntimeEventEnvelope): number {
  const raw = event.sequence_no;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : 0;
}

/** The A1 formatter throws for a too-short run id / seq < 1; the fold must never
 *  throw, so fall back to a plainly composed id (mirrors `ledgerProjection`). */
function safeLedgerId(runId: string, seq: number): string {
  try {
    return formatLedgerId(runId, seq);
  } catch {
    return `r${runId}·${seq}`;
  }
}
