// Status-strip selector (Generative Surfaces v2, PRD-B2 D6 / FR-F2).
//
// `projectStatusLine` is a PURE PEER of `projectProvenance` over the SAME
// `session.events` array (one-projector invariant). It folds to a single line
// mirroring the run's latest consequential ledger beat — the strip pinned at the
// bottom of the v2 canvas. Deterministic + total: malformed payloads degrade,
// never throw; unknown event types are ignored.

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";
import { formatLedgerId } from "@0x-copilot/api-types";

export interface StatusStripLine {
  /** `"gate"` is a reserved stub — unreachable until PRD-C2 emits `gate.opened`. */
  readonly kind: "idle" | "op" | "assembling" | "gate";
  readonly text: string; // e.g. "read.executed · linear.get_issue · r7f3·042"
  readonly ledgerId: string | null;
}

const IDLE: StatusStripLine = { kind: "idle", text: "", ledgerId: null };

const CONSEQUENTIAL = new Set<string>([
  "read.executed",
  "surface.created",
  "view.derived",
]);

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

function safeLedgerId(runId: string, seq: number): string | null {
  if (runId === "" || seq < 1) return null;
  try {
    return formatLedgerId(runId, seq);
  } catch {
    return `r${runId}·${seq}`;
  }
}

/**
 * Fold to the strip line. The LATEST consequential event
 * (`read.executed`/`surface.created`/`view.derived`) sets `kind: "op"` with
 * `event-name · connector.op · ledgerId`; if that surface is still `pending`
 * (no `view.derived` yet) the line reads `kind: "assembling"`; no v2 events ⇒
 * `kind: "idle"`. `connector.op` sourcing: `read.executed` carries them
 * directly; `surface.created` via `source{connector,op}`; `view.derived` carries
 * NEITHER, so resolve by joining to that surface's `surface.created` (same array,
 * by `surface_id`) — if unresolved, the `· connector.op` segment is omitted,
 * never fabricated.
 */
export function projectStatusLine(
  events: readonly RuntimeEventEnvelope[],
): StatusStripLine {
  const ordered = [...events].sort((a, b) => seqOf(a) - seqOf(b));

  // First pass: index each surface's `connector.op` (from its `surface.created`)
  // and note whether it has reached a `view.derived` (else it is assembling).
  const sourceBySurface = new Map<string, { connector: string; op: string }>();
  const viewedSurfaces = new Set<string>();
  let runId = "";
  for (const event of ordered) {
    if (runId === "" && typeof event.run_id === "string") runId = event.run_id;
    const payload = asRecord(event.payload);
    if (payload === null) continue;
    if (event.event_type === "surface.created") {
      const surfaceId = strOr(payload.surface_id, "");
      if (surfaceId === "") continue;
      const source = asRecord(payload.source);
      sourceBySurface.set(surfaceId, {
        connector: source !== null ? strOr(source.connector, "") : "",
        op: source !== null ? strOr(source.op, "") : "",
      });
    } else if (event.event_type === "view.derived") {
      const surfaceId = strOr(payload.surface_id, "");
      if (surfaceId !== "") viewedSurfaces.add(surfaceId);
    }
  }

  // Second pass: the latest consequential event wins.
  let latest: RuntimeEventEnvelope | null = null;
  for (const event of ordered) {
    if (CONSEQUENTIAL.has(event.event_type)) latest = event;
  }
  if (latest === null) return IDLE;

  const payload = asRecord(latest.payload) ?? {};
  const seq = seqOf(latest);
  const ledgerId = safeLedgerId(latest.run_id ?? runId, seq);

  // Resolve connector.op for the latest event by type.
  let connector = "";
  let op = "";
  if (latest.event_type === "read.executed") {
    connector = strOr(payload.connector, "");
    op = strOr(payload.op, "");
  } else if (latest.event_type === "surface.created") {
    const source = asRecord(payload.source);
    connector = source !== null ? strOr(source.connector, "") : "";
    op = source !== null ? strOr(source.op, "") : "";
  } else {
    // view.derived — join to its surface.created for connector.op.
    const surfaceId = strOr(payload.surface_id, "");
    const src = sourceBySurface.get(surfaceId);
    if (src !== undefined) {
      connector = src.connector;
      op = src.op;
    }
  }

  const opSegment =
    connector !== "" && op !== "" ? ` · ${connector}.${op}` : "";
  const idSegment = ledgerId !== null ? ` · ${ledgerId}` : "";
  const text = `${latest.event_type}${opSegment}${idSegment}`;

  // "assembling" when the newest surface still has no derived view. Use the
  // latest surface.created that has not yet been viewed.
  let assembling = false;
  for (const [surfaceId] of sourceBySurface) {
    if (!viewedSurfaces.has(surfaceId)) assembling = true;
  }

  return {
    kind: assembling ? "assembling" : "op",
    text,
    ledgerId,
  };
}
