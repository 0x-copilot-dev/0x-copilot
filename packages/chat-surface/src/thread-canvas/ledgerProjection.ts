// Work Ledger client fold (Generative Surfaces v2, PRD-B1 / SDR §5).
//
// `projectLedger` is a pure selector over the SAME `session.events` array
// `useEventProjector` / `projectSurfaceTabs` consume — a PEER of those
// projectors, never a second SSE subscription (FR-3.3, the one-projector
// invariant). It folds exactly the two v2 event types B1 renders —
// `surface.created` and `view.derived` — and tolerates+ignores every other v2
// event type in the stream (C/D/E waves add consumers, not projector rewrites).
//
// The fold is the TypeScript twin of PRD-A3's Python `SurfaceStoreProjection`
// (`services/ai-backend/src/agent_runtime/surfaces_v2/projection.py`). Its
// `toParitySnapshot` output byte-matches that fold's `model_dump(mode="json")`
// snapshot of the SAME golden events — pinned by `ledgerProjection.parity.test.ts`
// against the shared A1/A3 JSON fixtures. Drift on either side fails CI.

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";
import {
  formatLedgerId,
  type SurfaceCreatedPayload,
  type ViewDerivedPayload,
} from "@0x-copilot/api-types";

import type { SurfaceTab } from "./eventProjector";

// ---------------------------------------------------------------------------
// Value unions (verbatim from SDR §5 / the A1 `SurfaceKind` / `ViewTier`)
// ---------------------------------------------------------------------------

export type LedgerSurfaceKind =
  | "record"
  | "message"
  | "table"
  | "call"
  | "raw"
  | "receipt"
  | "gate";

export type LedgerViewTier = "raw" | "generic" | "shaped";

/** The connector server + operation a surface was sourced from. Always present
 *  (empty strings when the create carried no `source`) so the parity snapshot
 *  matches the Python fold's `connector`/`op` string columns. */
export interface LedgerSurfaceSource {
  readonly connector: string;
  readonly op: string;
}

/** A surface's derived-view state, folded from the last `view.derived`. Mirrors
 *  A3's `SurfaceViewState` (metadata only — `generatorModel` is the A1
 *  `gen.model`, never a `preference`). */
export interface LedgerSurfaceView {
  readonly tier: LedgerViewTier;
  readonly basis: string;
  readonly specRef: string | null;
  readonly generatorModel: string | null;
}

/** One surface's folded metadata (no hydrated payload content — that comes from
 *  the `/surfaces` endpoint via `useSurfacesV2`). B2 reads per-surface
 *  provenance off this; B3 extends it with view lifecycle. */
export interface LedgerSurface {
  readonly surfaceId: string;
  readonly kind: LedgerSurfaceKind;
  readonly title: string;
  readonly source: LedgerSurfaceSource;
  readonly payloadRef: string;
  /** Full folded view state, or null until the first `view.derived` lands. */
  readonly view: LedgerSurfaceView | null;
  /** Convenience mirror of `view?.tier ?? null` (PRD-B1 §2). */
  readonly viewTier: LedgerViewTier | null;
  readonly createdSeq: number; // first `sequence_no` — anchors `ledgerId`
  readonly lastSeq: number; // highest seq touching this surface — tab order key
  readonly ledgerId: string; // "r<short>·<seq>" via the A1 formatter, from createdSeq
}

export interface LedgerProjection {
  /** The folded run's id (from the events; `""` when no v2 events were seen).
   *  Threaded onto the parity snapshot's `run_id`, matching the Python fold. */
  readonly runId: string;
  /** Keyed by `surfaceId`, in first-seen (insertion) order. */
  readonly surfaces: ReadonlyMap<string, LedgerSurface>;
  /** Tab strip: newest mutation first (`lastSeq` desc, `createdSeq` desc tiebreak). */
  readonly tabs: readonly LedgerSurface[];
  /** Highest `surface.created`/`view.derived` `sequence_no` seen; 0 = none. This
   *  is the hydration trigger — it advances only when surface content could have
   *  changed, so `useSurfacesV2` refetches on this, not on unrelated events. */
  readonly lastLedgerSeq: number;
  /** Highest `sequence_no` over ALL events in the stream (the run's watermark) —
   *  the parity twin of the Python fold's `latest_sequence_no`, which counts
   *  every event, not just the two the surface fold consumes. */
  readonly latestSequenceNo: number;
}

// ---------------------------------------------------------------------------
// URI codec — mount/tab URIs. `<scheme>://surfaces-v2/<surface_id>`.
// ---------------------------------------------------------------------------

const SURFACES_V2_MARKER = "surfaces-v2";

const KNOWN_KINDS: ReadonlySet<string> = new Set<LedgerSurfaceKind>([
  "record",
  "message",
  "table",
  "call",
  "raw",
  "receipt",
  "gate",
]);

/** kind → mount scheme. 1:1 except `call → record` (FR-A3): a call surface
 *  renders through the RecordRenderer. `raw`/`receipt`/`gate` keep their own
 *  scheme so NO adapter matches — TcSurfaceMount's tier-3 fallback renders them
 *  honestly (D29), no mount branch. An unknown kind falls to `raw` (tier-3). */
function schemeForKind(kind: string): string {
  if (kind === "call") return "record";
  if (KNOWN_KINDS.has(kind)) return kind;
  return "raw";
}

/** Mount/tab URI for a surface: `<scheme>://surfaces-v2/<surface_id>`. */
export function tabUriForSurface(surface: LedgerSurface): string {
  return `${schemeForKind(surface.kind)}://${SURFACES_V2_MARKER}/${surface.surfaceId}`;
}

/** Recover the `surface_id` from a surfaces-v2 tab/mount URI (the path tail
 *  after the last `/`), scheme-independent so it works for every kind. Returns
 *  `null` for a URI that is not a surfaces-v2 URI (v1 URIs, garbage) — the host
 *  uses this to build `resolveSurfaceState`. Round-trips:
 *  `surfaceIdForTabUri(tabUriForSurface(s)) === s.surfaceId`. */
export function surfaceIdForTabUri(uri: string): string | null {
  if (typeof uri !== "string") return null;
  const schemeIdx = uri.indexOf("://");
  if (schemeIdx <= 0) return null;
  const rest = uri.slice(schemeIdx + 3);
  const marker = `${SURFACES_V2_MARKER}/`;
  if (!rest.startsWith(marker)) return null;
  const id = rest.slice(marker.length);
  return id.length > 0 ? id : null;
}

// ---------------------------------------------------------------------------
// Fold
// ---------------------------------------------------------------------------

/** Mutable per-surface accumulator, frozen into a `LedgerSurface` at the end. */
interface SurfaceAccumulator {
  surfaceId: string;
  kind: LedgerSurfaceKind;
  title: string;
  source: LedgerSurfaceSource;
  payloadRef: string;
  view: LedgerSurfaceView | null;
  createdSeq: number;
  lastSeq: number;
  ledgerId: string;
}

function strOr(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function normalizeKind(value: unknown): LedgerSurfaceKind {
  // Unknown / missing kind is tolerated as `raw` (tier-3, honest — §7).
  return typeof value === "string" && KNOWN_KINDS.has(value)
    ? (value as LedgerSurfaceKind)
    : "raw";
}

function readSource(value: unknown): LedgerSurfaceSource {
  if (value !== null && typeof value === "object") {
    const rec = value as Record<string, unknown>;
    return { connector: strOr(rec.connector, ""), op: strOr(rec.op, "") };
  }
  return { connector: "", op: "" };
}

/**
 * Fold a run's events into a {@link LedgerProjection}.
 *
 * Deterministic + total (mirrors the Python fold, the parity referee):
 * events are processed in ascending `sequence_no`; a duplicate `event_id`
 * (SSE resend) is skipped; `surface.created` upserts by `surface_id` (repeat
 * reads refresh `title`/`payloadRef`/`lastSeq`, keeping the first
 * `createdSeq`/`ledgerId`/`kind`/`source`); `view.derived` for an unseen
 * `surface_id` is dropped; malformed payloads are skipped, never thrown;
 * re-projection is deep-equal.
 */
export function projectLedger(
  events: readonly RuntimeEventEnvelope[],
): LedgerProjection {
  const ordered = [...events].sort((a, b) => seqOf(a) - seqOf(b));

  const surfaces = new Map<string, SurfaceAccumulator>();
  const seenEventIds = new Set<string>();
  let lastLedgerSeq = 0;
  let latestSequenceNo = 0;
  let runId = "";

  for (const event of ordered) {
    // Watermark counts EVERY event (matches the Python fold's
    // `latest_sequence_no`), before the type filter. Same for the run id —
    // every event in a run shares it.
    const eventSeq = seqOf(event);
    if (eventSeq > latestSequenceNo) latestSequenceNo = eventSeq;
    if (runId === "" && typeof event.run_id === "string") runId = event.run_id;
    const eventType = event.event_type;
    if (eventType !== "surface.created" && eventType !== "view.derived") {
      continue; // tolerate + ignore every other (present + future) event type
    }
    const eventId = event.event_id;
    if (typeof eventId === "string" && eventId.length > 0) {
      if (seenEventIds.has(eventId)) continue; // idempotent SSE resend
      seenEventIds.add(eventId);
    }
    const seq = seqOf(event);
    const payload = event.payload;
    if (payload === null || typeof payload !== "object") continue;

    if (eventType === "surface.created") {
      applySurfaceCreated(surfaces, event.run_id, seq, payload);
    } else {
      applyViewDerived(surfaces, seq, payload);
    }
    if (seq > lastLedgerSeq) lastLedgerSeq = seq;
  }

  const frozen = new Map<string, LedgerSurface>();
  for (const [id, acc] of surfaces) {
    frozen.set(id, freeze(acc));
  }

  const tabs = [...frozen.values()].sort((a, b) => {
    if (b.lastSeq !== a.lastSeq) return b.lastSeq - a.lastSeq;
    if (b.createdSeq !== a.createdSeq) return b.createdSeq - a.createdSeq;
    return a.surfaceId < b.surfaceId ? -1 : a.surfaceId > b.surfaceId ? 1 : 0;
  });

  return { runId, surfaces: frozen, tabs, lastLedgerSeq, latestSequenceNo };
}

function applySurfaceCreated(
  surfaces: Map<string, SurfaceAccumulator>,
  runId: string,
  seq: number,
  payload: Record<string, unknown>,
): void {
  const p = payload as unknown as Partial<SurfaceCreatedPayload>;
  const surfaceId = p.surface_id;
  if (typeof surfaceId !== "string" || surfaceId.length === 0) return;
  const title = strOr(payload.title, "");
  const payloadRef = strOr(payload.payload_ref, "");
  const existing = surfaces.get(surfaceId);
  if (existing !== undefined) {
    // Upsert: refresh the mutable projection, keep the first anchor + kind.
    existing.title = title;
    existing.payloadRef = payloadRef;
    if (seq > existing.lastSeq) existing.lastSeq = seq;
    return;
  }
  surfaces.set(surfaceId, {
    surfaceId,
    kind: normalizeKind(payload.kind),
    title,
    source: readSource(payload.source),
    payloadRef,
    view: null,
    createdSeq: seq,
    lastSeq: seq,
    ledgerId: safeLedgerId(runId, seq),
  });
}

function applyViewDerived(
  surfaces: Map<string, SurfaceAccumulator>,
  seq: number,
  payload: Record<string, unknown>,
): void {
  const p = payload as unknown as Partial<ViewDerivedPayload>;
  const surfaceId = p.surface_id;
  if (typeof surfaceId !== "string") return;
  const acc = surfaces.get(surfaceId);
  if (acc === undefined) return; // view for an unseen surface is ignored
  const tier = payload.tier;
  const gen = payload.gen;
  let generatorModel: string | null = null;
  if (gen !== null && typeof gen === "object") {
    const model = (gen as Record<string, unknown>).model;
    generatorModel =
      typeof model === "string" && model.length > 0 ? model : null;
  }
  const specRef = payload.spec_ref;
  acc.view = {
    tier: (typeof tier === "string" ? tier : "") as LedgerViewTier,
    basis: strOr(payload.basis, ""),
    specRef: typeof specRef === "string" && specRef.length > 0 ? specRef : null,
    generatorModel,
  };
  if (seq > acc.lastSeq) acc.lastSeq = seq;
}

function freeze(acc: SurfaceAccumulator): LedgerSurface {
  return {
    surfaceId: acc.surfaceId,
    kind: acc.kind,
    title: acc.title,
    source: acc.source,
    payloadRef: acc.payloadRef,
    view: acc.view,
    viewTier: acc.view?.tier ?? null,
    createdSeq: acc.createdSeq,
    lastSeq: acc.lastSeq,
    ledgerId: acc.ledgerId,
  };
}

function seqOf(event: RuntimeEventEnvelope): number {
  const raw = event.sequence_no;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : 0;
}

/** The A1 formatter throws for a too-short run id or seq < 1; a malformed
 *  event must never throw the fold, so fall back to a plain composed id. */
function safeLedgerId(runId: string, seq: number): string {
  try {
    return formatLedgerId(runId, seq);
  } catch {
    return `r${runId}·${seq}`;
  }
}

// ---------------------------------------------------------------------------
// Adapters — bridge to the shared strip shape + the cross-language parity snapshot
// ---------------------------------------------------------------------------

/** Adapt the ledger tabs to the existing {@link SurfaceTab} strip shape so the
 *  cockpit's pin/close/`activeUri` logic is shared unchanged with the v1 path. */
export function ledgerTabsAsSurfaceTabs(
  p: LedgerProjection,
): readonly SurfaceTab[] {
  return p.tabs.map((surface) => ({
    uri: tabUriForSurface(surface),
    archetype: schemeForKind(surface.kind),
    title: surface.title,
    lastSeq: surface.lastSeq,
  }));
}

/** Language-neutral, metadata-only snapshot (snake_case keys, surfaces sorted by
 *  `surface_id`) that MUST byte-equal PRD-A3's Python fold snapshot
 *  (`SurfaceStoreState.model_dump(mode="json")`) of the SAME events. Deliberately
 *  excludes hydrated payload content the pure fold cannot produce — see the
 *  shared-fixture decision (PRD-B1 Open question 1). */
export function toParitySnapshot(p: LedgerProjection): unknown {
  const surfaces = [...p.surfaces.values()]
    .slice()
    .sort((a, b) =>
      a.surfaceId < b.surfaceId ? -1 : a.surfaceId > b.surfaceId ? 1 : 0,
    )
    .map((s) => ({
      surface_id: s.surfaceId,
      kind: s.kind,
      connector: s.source.connector,
      op: s.source.op,
      title: s.title,
      payload_ref: s.payloadRef,
      view:
        s.view === null
          ? null
          : {
              tier: s.view.tier,
              basis: s.view.basis,
              spec_ref: s.view.specRef,
              generator_model: s.view.generatorModel,
            },
      first_sequence_no: s.createdSeq,
      last_sequence_no: s.lastSeq,
      ledger_id: s.ledgerId,
    }));
  return {
    run_id: p.runId,
    surfaces,
    latest_sequence_no: p.latestSequenceNo,
  };
}
