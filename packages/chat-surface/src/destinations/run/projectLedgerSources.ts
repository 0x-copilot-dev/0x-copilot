// projectLedgerSources — everything read this run, grouped by connector
// (Generative Surfaces v2, PRD-E1 / FR-E3).
//
// A PURE PEER of `projectReceipt` / `projectCitations` over the SAME
// `session.events` array (the one-projector invariant, FR-3.3) — never a second
// SSE subscription. It folds one row per `read.executed`, grouped by connector
// in first-seen order, rows in sequence order, each with time + ledger id +
// latency + the "auto-ran (read)" qualifier. The Sources rail renders directly
// from this.
//
// This projection is CLIENT-SIDE ONLY in v2 — there is no server fold/endpoint,
// because no other consumer exists (SDR §3's "Sources fold" box is satisfied by
// this selector; the divergence is documented in SDR §3 per the docs DoD).

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";
import { formatLedgerId } from "@0x-copilot/api-types";

const TITLE_SEPARATOR = " · ";
const READ_QUALIFIER = "auto-ran (read)" as const;

export interface LedgerSourceRow {
  readonly op: string;
  readonly title: string;
  readonly at: string;
  readonly ledgerId: string;
  readonly latencyMs: number | null;
  readonly qualifier: typeof READ_QUALIFIER;
}

export interface LedgerSourceGroup {
  readonly connector: string;
  readonly rows: readonly LedgerSourceRow[];
}

export interface LedgerSourcesProjection {
  readonly groups: readonly LedgerSourceGroup[];
  readonly total: number;
}

const EMPTY: LedgerSourcesProjection = { groups: [], total: 0 };

/**
 * Project the run stream into the Sources rail's grouped read list. Pure over
 * `events` (referentially stable for an unchanged array via the caller's
 * `useMemo`). Malformed payloads are skipped, never thrown.
 */
export function projectLedgerSources(
  events: readonly RuntimeEventEnvelope[],
): LedgerSourcesProjection {
  if (events.length === 0) return EMPTY;

  const ordered = [...events].sort((a, b) => seqOf(a) - seqOf(b));

  // First pass: surface titles keyed by their producing payload_ref (§2 title
  // resolution — a read whose payload_ref matches a surface's shows that title).
  const titleByPayloadRef = new Map<string, string>();
  let runId = "";
  for (const event of ordered) {
    if (runId === "" && typeof event.run_id === "string") runId = event.run_id;
    if (event.event_type !== "surface.created") continue;
    const payload = asRecord(event.payload);
    if (payload === null) continue;
    const payloadRef = strOr(payload.payload_ref, "");
    if (payloadRef === "" || titleByPayloadRef.has(payloadRef)) continue;
    titleByPayloadRef.set(payloadRef, strOr(payload.title, ""));
  }

  // Second pass: one row per read.executed, grouped by connector (first-seen).
  const groups = new Map<string, LedgerSourceRow[]>();
  let total = 0;
  for (const event of ordered) {
    if (event.event_type !== "read.executed") continue;
    const payload = asRecord(event.payload);
    if (payload === null) continue;
    const connector = strOr(payload.connector, "");
    const op = strOr(payload.op, "");
    const payloadRef = strOr(payload.payload_ref, "");
    const latencyRaw = payload.latency_ms;
    const latencyMs =
      typeof latencyRaw === "number" && Number.isFinite(latencyRaw)
        ? latencyRaw
        : null;
    let title = titleByPayloadRef.get(payloadRef) ?? "";
    if (title === "") title = `${connector}${TITLE_SEPARATOR}${op}`;
    const row: LedgerSourceRow = {
      op,
      title,
      at: typeof event.created_at === "string" ? event.created_at : "",
      ledgerId: safeLedgerId(runId, seqOf(event)),
      latencyMs,
      qualifier: READ_QUALIFIER,
    };
    const existing = groups.get(connector);
    if (existing === undefined) groups.set(connector, [row]);
    else existing.push(row);
    total += 1;
  }

  return {
    groups: [...groups.entries()].map(([connector, rows]) => ({
      connector,
      rows,
    })),
    total,
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function strOr(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function seqOf(event: RuntimeEventEnvelope): number {
  const raw = event.sequence_no;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : 0;
}

function safeLedgerId(runId: string, seq: number): string {
  try {
    return formatLedgerId(runId, seq);
  } catch {
    return `r${runId}·${seq}`;
  }
}
