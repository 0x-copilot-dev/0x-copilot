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
  type DecisionKind,
  type DecisionRecordedPayload,
  type GateOpenedPayload,
  type GateResolvedPayload,
  type RevisionAddedPayload,
  type RevisionAuthor,
  type SurfaceCreatedPayload,
  type ViewDerivedPayload,
  type ViewPreferencePayload,
  type WriteStagedPayload,
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

/** Durable tier pin (`view.preference.keep`) — the "Keep generic" / "Shaped"
 *  toggle state, folded from the ledger so it survives reload (PRD-B3). */
export type LedgerViewKeep = "generic" | "shaped";

/** Per-surface view-lifecycle state (PRD-B3). Folds `view.derived` +
 *  `view.preference` into the explicit tier ladder the canvas renders:
 *  `effectiveTier` = `keep ?? tier-of-latest-view.derived`, where a
 *  `keep: "shaped"` folds only once `shapedAvailable`, and `keep: "generic"`
 *  always folds. `regenCount` bounds the Regenerate affordance (server cap
 *  authoritative). */
export interface LedgerSurfaceViewState {
  /** Tier of the latest `view.derived` (before any preference is applied). */
  readonly tier: LedgerViewTier;
  readonly basis: string;
  readonly specRef: string | null;
  /** Durable pin, or null until the user toggles. */
  readonly keep: LedgerViewKeep | null;
  /** Whether a shaped derivation has ever landed (enables the Shaped toggle). */
  readonly shapedAvailable: boolean;
  /** Prior user regenerations folded from the ledger (non-first, non-registry). */
  readonly regenCount: number;
  /** The rendered tier after applying `keep` against `shapedAvailable`. */
  readonly effectiveTier: LedgerViewTier;
}

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
  /** Durable tier pin folded from `view.preference` (PRD-B3), or null. */
  readonly preference: LedgerViewKeep | null;
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
  /** PRD-B3 view-lifecycle state (tier ladder + preference + regen), or null
   *  until the first `view.derived` lands. Drives the toast + toggle + regen. */
  readonly viewState: LedgerSurfaceViewState | null;
  readonly createdSeq: number; // first `sequence_no` — anchors `ledgerId`
  readonly lastSeq: number; // highest seq touching this surface — tab order key
  readonly ledgerId: string; // "r<short>·<seq>" via the A1 formatter, from createdSeq
}

// ---------------------------------------------------------------------------
// Gate model (PRD-C2) — folded from `gate.opened` / `gate.resolved`
// ---------------------------------------------------------------------------

export type LedgerGateAuthState = "missing" | "expired" | "insufficient";
export type LedgerGateOutcome = "connected" | "cancelled";
/** Read/write class the gate card renders on. The SDR §5 `gate.opened` row does
 *  NOT carry `op_class`, so the fold fails CLOSED to `"write"` — the gate card
 *  shows the write-policy choice (and hides the read-only pledge) unless a future
 *  ledger field proves the op is a read. */
export type LedgerGateOpClass = "read" | "write";
export type LedgerGateWritePolicy = "ask_first" | "allow_always";

/** One gate's folded state — the canvas gate card renders directly from this
 *  (SDR §5 reserves `surface.created{kind: gate}` but the card folds from the
 *  `gate.opened` event, never a synthesized fake surface). */
export interface LedgerGate {
  readonly gateId: string;
  /** The connector server id the host's `McpAuthPort` connects, recovered from
   *  the deterministic `mcp_auth:<run_id>:<server_id>` gate id. */
  readonly serverId: string;
  readonly connector: string;
  readonly purpose: string;
  readonly scopes: readonly string[];
  readonly authState: LedgerGateAuthState;
  readonly opClass: LedgerGateOpClass;
  readonly ledgerId: string;
  readonly createdSeq: number;
  readonly lastSeq: number;
  /** True once a `gate.resolved` folded in. */
  readonly resolved: boolean;
  readonly outcome: LedgerGateOutcome | null;
  readonly writePolicy: LedgerGateWritePolicy | null;
}

// ---------------------------------------------------------------------------
// Staged-write model (PRD-D1) — folded from `write.staged` / `revision.added` /
// `decision.recorded`. The TypeScript twin of the Python `StagedWriteFold`
// (`agent_runtime/surfaces_v2/staging.py`). NOT part of `toParitySnapshot`
// (that mirrors the SurfaceStore fold only) — a peer fold over the same events.
// ---------------------------------------------------------------------------

export type LedgerStagedWriteStatus =
  | "staged"
  | "rejected"
  | "approved"
  | "applied"
  // PRD-D3 — a row-set apply was decided (frozen), the terminal not yet folded.
  | "apply_pending"
  // PRD-D3 — some approved rows failed mid-apply (terminal in D3).
  | "partially_applied"
  // PRD-D2 defensive: a `write.applied` folded onto a non-approved / rev-mismatched
  // stage (unreachable absent a bug — the server's approval gate + D1 freeze
  // prevent it). The TS twin of the Python fold's CORRUPT so parity holds.
  | "corrupt";

/** The outcome of the last `write.applied` folded onto a stage (PRD-D2/D3). */
export type LedgerApplyResult = "applied" | "partial" | "failed";

/** A row's decision stance in the fold (PRD-D3). */
export type LedgerRowStance = "will_apply" | "held";

/** One folded row of a bulk row-set: content (title/diffs) + state. Rendered by
 *  `TcStagedTableSurface`. `agentHoldReason` is STICKY — it survives a user
 *  override (FR-C7). */
export interface LedgerStagedRow {
  readonly rowKey: string;
  readonly title: string;
  readonly changes: readonly LedgerRowChange[];
  readonly stance: LedgerRowStance;
  readonly agentHoldReason: string | null;
  readonly decidedBy: "agent" | "user" | "policy" | null;
  readonly applyOutcome: "applied" | "failed" | null;
}

/** One field diff on a staged row (display only, PRD-D3). */
export interface LedgerRowChange {
  readonly field: string;
  readonly old: unknown;
  readonly new: unknown;
}

/** Projection summary over a row-set's rows (PRD-D3). */
export interface LedgerRowCounts {
  readonly total: number;
  readonly willApply: number;
  readonly held: number;
  readonly applied: number;
  readonly failed: number;
}

/** A half-open `[start, end)` char range of a revision's NEW text and its
 *  author — the "edited by you" highlight ranges (PRD-D1). */
export interface LedgerAuthorshipSpan {
  readonly start: number;
  readonly end: number;
  readonly author: RevisionAuthor;
}

/** One folded revision: its number, author, snapshot ref, spans, ledger id. */
export interface LedgerStageRevision {
  readonly rev: number;
  readonly author: RevisionAuthor;
  readonly proposalRef: string;
  readonly diffRef: string;
  readonly authorshipSpans: readonly LedgerAuthorshipSpan[];
  readonly seq: number;
  readonly ledgerId: string;
}

/** One folded decision (approve/reject/restore). */
export interface LedgerStageDecision {
  readonly decision: DecisionKind;
  readonly scopeRev: number | null;
  readonly actor: string;
  readonly seq: number;
  readonly ledgerId: string;
}

/** A staged single-artifact write — the staged-draft surface + rev-pinned
 *  approve bar render directly from this. `latestRev` is what the bar pins;
 *  `status` drives approve/reject/restore affordances. Rebuilt from the ledger
 *  so it survives reload. */
export interface LedgerStagedWrite {
  readonly stageId: string;
  readonly surfaceId: string;
  readonly draftId: string;
  readonly target: LedgerSurfaceSource;
  readonly latestRev: number;
  readonly approvedRev: number | null;
  readonly status: LedgerStagedWriteStatus;
  readonly revisions: readonly LedgerStageRevision[];
  readonly decisions: readonly LedgerStageDecision[];
  readonly createdSeq: number;
  readonly lastSeq: number;
  readonly ledgerId: string;
  /** The highest-`rev` revision, or null when empty — the bar pins this. */
  readonly latestRevision: LedgerStageRevision | null;
  /** The last `write.applied` outcome (PRD-D2), or null until one folds. `applied`
   *  ⇒ status `applied` (confirmation row); `failed` ⇒ status back to `staged`
   *  (held), with `applyFailureCode` naming the refusal. */
  readonly applyResult: LedgerApplyResult | null;
  readonly applyFailureCode: string | null;
  /** PRD-D3 — the folded rows of a bulk row-set, or `null` for a single-artifact
   *  (D1) stage. `TcStagedTableSurface` renders from this. */
  readonly rows: readonly LedgerStagedRow[] | null;
  readonly rowCounts: LedgerRowCounts | null;
}

export interface LedgerProjection {
  /** The folded run's id (from the events; `""` when no v2 events were seen).
   *  Threaded onto the parity snapshot's `run_id`, matching the Python fold. */
  readonly runId: string;
  /** Keyed by `surfaceId`, in first-seen (insertion) order. */
  readonly surfaces: ReadonlyMap<string, LedgerSurface>;
  /** Keyed by `gateId`, in first-seen order (PRD-C2). */
  readonly gates: ReadonlyMap<string, LedgerGate>;
  /** Keyed by `stageId`, in first-seen order (PRD-D1). */
  readonly stages: ReadonlyMap<string, LedgerStagedWrite>;
  /** Gate cards still awaiting the user (unresolved), newest first — the canvas
   *  renders these as pending gate cards. */
  readonly openGates: readonly LedgerGate[];
  /** Optimistic posture signal: a resolved gate chose `allow_always`. The host
   *  ORs this with `GET /v1/mcp/servers` (the authoritative posture) so the chip
   *  flips the instant the gate resolves, before the connectors refetch lands. */
  readonly bypassFromLedger: boolean;
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
  // PRD-B3 view-lifecycle fold state (composed into `viewState` at freeze).
  keep: LedgerViewKeep | null;
  shapedAvailable: boolean;
  regenCount: number;
}

/** Mutable per-gate accumulator, frozen into a `LedgerGate` at the end. */
interface GateAccumulator {
  gateId: string;
  serverId: string;
  connector: string;
  purpose: string;
  scopes: readonly string[];
  authState: LedgerGateAuthState;
  opClass: LedgerGateOpClass;
  ledgerId: string;
  createdSeq: number;
  lastSeq: number;
  resolved: boolean;
  outcome: LedgerGateOutcome | null;
  writePolicy: LedgerGateWritePolicy | null;
}

/** Mutable per-stage accumulator, frozen into a `LedgerStagedWrite` at the end. */
interface StageAccumulator {
  stageId: string;
  surfaceId: string;
  draftId: string;
  target: LedgerSurfaceSource;
  latestRev: number;
  approvedRev: number | null;
  status: LedgerStagedWriteStatus;
  revisions: LedgerStageRevision[];
  decisions: LedgerStageDecision[];
  createdSeq: number;
  lastSeq: number;
  ledgerId: string;
  applyResult: LedgerApplyResult | null;
  applyFailureCode: string | null;
  // PRD-D3 row-set fold state (mirrors the Python `_StageAccumulator`).
  isRowset: boolean;
  rowOrder: string[];
  stagedRows: Map<string, { title: string; changes: LedgerRowChange[] }>;
  agentHoldReasons: Map<string, string>;
  rowStances: Map<string, LedgerRowStance>;
  rowDecidedBy: Map<string, "agent" | "user" | "policy">;
  rowApplyOutcomes: Map<string, "applied" | "failed">;
}

function strOr(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

/** Recover the draft id from a `draft://<draft_id>/v<version>` proposal ref. */
function draftIdFromProposalRef(ref: unknown): string {
  if (typeof ref !== "string") return "";
  const scheme = "draft://";
  if (!ref.startsWith(scheme)) return "";
  const body = ref.slice(scheme.length);
  const idx = body.lastIndexOf("/v");
  return idx > 0 ? body.slice(0, idx) : "";
}

const KNOWN_REVISION_AUTHORS: ReadonlySet<string> = new Set<RevisionAuthor>([
  "agent",
  "user",
]);

function normalizeRevisionAuthor(value: unknown): RevisionAuthor {
  return value === "user" ? "user" : "agent";
}

const KNOWN_DECISION_KINDS: ReadonlySet<string> = new Set<DecisionKind>([
  "approve",
  "reject",
  "hold",
  "restore",
]);

function readAuthorshipSpans(value: unknown): readonly LedgerAuthorshipSpan[] {
  if (!Array.isArray(value)) return [];
  const out: LedgerAuthorshipSpan[] = [];
  for (const item of value) {
    if (item === null || typeof item !== "object") continue;
    const rec = item as Record<string, unknown>;
    const start = rec.start;
    const end = rec.end;
    const author = rec.author;
    if (
      typeof start === "number" &&
      Number.isInteger(start) &&
      start >= 0 &&
      typeof end === "number" &&
      Number.isInteger(end) &&
      end >= start &&
      typeof author === "string" &&
      KNOWN_REVISION_AUTHORS.has(author)
    ) {
      out.push({ start, end, author: author as RevisionAuthor });
    }
  }
  return out;
}

const KNOWN_AUTH_STATES: ReadonlySet<string> = new Set<LedgerGateAuthState>([
  "missing",
  "expired",
  "insufficient",
]);

function normalizeAuthState(value: unknown): LedgerGateAuthState {
  return typeof value === "string" && KNOWN_AUTH_STATES.has(value)
    ? (value as LedgerGateAuthState)
    : "missing";
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
  const gates = new Map<string, GateAccumulator>();
  const stages = new Map<string, StageAccumulator>();
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
    if (
      eventType !== "surface.created" &&
      eventType !== "view.derived" &&
      eventType !== "view.preference" &&
      eventType !== "gate.opened" &&
      eventType !== "gate.resolved" &&
      eventType !== "write.staged" &&
      eventType !== "revision.added" &&
      eventType !== "decision.recorded" &&
      eventType !== "write.applied"
    ) {
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
      if (seq > lastLedgerSeq) lastLedgerSeq = seq;
    } else if (eventType === "view.derived") {
      applyViewDerived(surfaces, seq, payload);
      if (seq > lastLedgerSeq) lastLedgerSeq = seq;
    } else if (eventType === "view.preference") {
      applyViewPreference(surfaces, seq, payload);
      if (seq > lastLedgerSeq) lastLedgerSeq = seq;
    } else if (eventType === "gate.opened") {
      applyGateOpened(gates, event.run_id, seq, payload);
    } else if (eventType === "gate.resolved") {
      applyGateResolved(gates, seq, payload);
    } else if (eventType === "write.staged") {
      applyWriteStaged(stages, event.run_id, seq, payload);
    } else if (eventType === "revision.added") {
      applyRevisionAdded(stages, event.run_id, seq, payload);
    } else if (eventType === "decision.recorded") {
      applyDecisionRecorded(stages, event.run_id, seq, payload);
    } else {
      applyWriteApplied(stages, seq, payload);
    }
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

  const frozenGates = new Map<string, LedgerGate>();
  for (const [id, acc] of gates) {
    frozenGates.set(id, freezeGate(acc));
  }
  const openGates = [...frozenGates.values()]
    .filter((g) => !g.resolved)
    .sort((a, b) => b.createdSeq - a.createdSeq);
  const bypassFromLedger = [...frozenGates.values()].some(
    (g) => g.resolved && g.writePolicy === "allow_always",
  );

  const frozenStages = new Map<string, LedgerStagedWrite>();
  for (const [id, acc] of stages) {
    frozenStages.set(id, freezeStage(acc));
  }

  return {
    runId,
    surfaces: frozen,
    gates: frozenGates,
    stages: frozenStages,
    openGates,
    bypassFromLedger,
    tabs,
    lastLedgerSeq,
    latestSequenceNo,
  };
}

function applyGateOpened(
  gates: Map<string, GateAccumulator>,
  runId: string,
  seq: number,
  payload: Record<string, unknown>,
): void {
  const p = payload as unknown as Partial<GateOpenedPayload>;
  const gateId = p.gate_id;
  if (typeof gateId !== "string" || gateId.length === 0) return;
  const scopes = Array.isArray(payload.scopes)
    ? (payload.scopes.filter((s) => typeof s === "string") as string[])
    : [];
  const existing = gates.get(gateId);
  if (existing !== undefined) {
    // Upsert (replay): refresh mutable fields, keep the first anchor.
    existing.connector = strOr(payload.connector, existing.connector);
    existing.purpose = strOr(payload.purpose, existing.purpose);
    existing.scopes = scopes;
    existing.authState = normalizeAuthState(payload.auth_state);
    if (seq > existing.lastSeq) existing.lastSeq = seq;
    return;
  }
  gates.set(gateId, {
    gateId,
    serverId: serverIdFromGateId(gateId, runId),
    connector: strOr(payload.connector, ""),
    purpose: strOr(payload.purpose, ""),
    scopes,
    authState: normalizeAuthState(payload.auth_state),
    // op_class is not on the ledger row — fail closed to write (§Design C2).
    opClass: "write",
    ledgerId: safeLedgerId(runId, seq),
    createdSeq: seq,
    lastSeq: seq,
    resolved: false,
    outcome: null,
    writePolicy: null,
  });
}

function applyGateResolved(
  gates: Map<string, GateAccumulator>,
  seq: number,
  payload: Record<string, unknown>,
): void {
  const p = payload as unknown as Partial<GateResolvedPayload>;
  const gateId = p.gate_id;
  if (typeof gateId !== "string") return;
  const acc = gates.get(gateId);
  if (acc === undefined) return; // resolve for an unseen gate is ignored
  const outcome = payload.outcome;
  acc.outcome =
    outcome === "connected" || outcome === "cancelled" ? outcome : null;
  acc.resolved = true;
  const wp = payload.write_policy;
  acc.writePolicy = wp === "ask_first" || wp === "allow_always" ? wp : null;
  if (seq > acc.lastSeq) acc.lastSeq = seq;
}

function applyWriteStaged(
  stages: Map<string, StageAccumulator>,
  runId: string,
  seq: number,
  payload: Record<string, unknown>,
): void {
  const p = payload as unknown as Partial<WriteStagedPayload>;
  const stageId = p.stage_id;
  if (typeof stageId !== "string" || stageId.length === 0) return;
  if (stages.has(stageId)) return; // first write.staged wins (append-only)
  const target =
    payload.target !== null && typeof payload.target === "object"
      ? (payload.target as Record<string, unknown>)
      : {};
  const acc: StageAccumulator = {
    stageId,
    surfaceId: strOr(payload.surface_id, ""),
    draftId: draftIdFromProposalRef(payload.proposal_ref),
    target: {
      connector: strOr(target.connector, ""),
      op: strOr(target.op, ""),
    },
    latestRev: 0,
    approvedRev: null,
    status: "staged",
    revisions: [],
    decisions: [],
    createdSeq: seq,
    lastSeq: seq,
    ledgerId: safeLedgerId(runId, seq),
    applyResult: null,
    applyFailureCode: null,
    isRowset: false,
    rowOrder: [],
    stagedRows: new Map(),
    agentHoldReasons: new Map(),
    rowStances: new Map(),
    rowDecidedBy: new Map(),
    rowApplyOutcomes: new Map(),
  };
  // PRD-D3 — a `rows` count marks a row-set; `agent_holds` seed the sticky per-
  // row pre-hold reasons (decided_by `agent`). Full content arrives with rev 1.
  if (typeof payload.rows === "number" && Number.isFinite(payload.rows)) {
    acc.isRowset = true;
  }
  const holds = payload.agent_holds;
  if (Array.isArray(holds)) {
    for (const raw of holds) {
      if (raw === null || typeof raw !== "object") continue;
      const rk = (raw as Record<string, unknown>).row_key;
      const reason = (raw as Record<string, unknown>).reason;
      if (typeof rk !== "string" || rk.length === 0) continue;
      acc.isRowset = true;
      acc.agentHoldReasons.set(rk, typeof reason === "string" ? reason : "");
      acc.rowStances.set(rk, "held");
      acc.rowDecidedBy.set(rk, "agent");
    }
  }
  stages.set(stageId, acc);
}

function applyRevisionAdded(
  stages: Map<string, StageAccumulator>,
  runId: string,
  seq: number,
  payload: Record<string, unknown>,
): void {
  const p = payload as unknown as Partial<RevisionAddedPayload>;
  const stageId = p.stage_id;
  if (typeof stageId !== "string") return;
  const acc = stages.get(stageId);
  if (acc === undefined) return; // revision for an unseen stage is ignored
  const rev = payload.rev;
  if (typeof rev !== "number" || !Number.isInteger(rev) || rev < 1) return;
  acc.revisions.push({
    rev,
    author: normalizeRevisionAuthor(payload.author),
    proposalRef: strOr(payload.proposal_ref, ""),
    diffRef: strOr(payload.diff_ref, ""),
    authorshipSpans: readAuthorshipSpans(payload.authorship_spans),
    seq,
    ledgerId: safeLedgerId(runId, seq),
  });
  if (rev > acc.latestRev) acc.latestRev = rev;
  if (seq > acc.lastSeq) acc.lastSeq = seq;
  // PRD-D3 — hydrate the inline row-set; agent-held rows stay held, the rest
  // default to will_apply.
  const rowset = payload.rowset;
  if (rowset !== null && typeof rowset === "object") {
    hydrateRowset(acc, (rowset as Record<string, unknown>).rows);
  }
}

function hydrateRowset(acc: StageAccumulator, rawRows: unknown): void {
  if (!Array.isArray(rawRows)) return;
  acc.isRowset = true;
  for (const raw of rawRows) {
    if (raw === null || typeof raw !== "object") continue;
    const r = raw as Record<string, unknown>;
    const rowKey = r.row_key;
    const title = r.title;
    if (typeof rowKey !== "string" || rowKey.length === 0) continue;
    if (typeof title !== "string") continue;
    if (acc.stagedRows.has(rowKey)) continue;
    acc.stagedRows.set(rowKey, {
      title,
      changes: readRowChanges(r.changes),
    });
    acc.rowOrder.push(rowKey);
    if (!acc.rowStances.has(rowKey)) acc.rowStances.set(rowKey, "will_apply");
  }
}

function readRowChanges(value: unknown): LedgerRowChange[] {
  if (!Array.isArray(value)) return [];
  const out: LedgerRowChange[] = [];
  for (const raw of value) {
    if (raw === null || typeof raw !== "object") continue;
    const c = raw as Record<string, unknown>;
    if (typeof c.field !== "string" || c.field.length === 0) continue;
    out.push({ field: c.field, old: c.old ?? null, new: c.new ?? null });
  }
  return out;
}

function applyDecisionRecorded(
  stages: Map<string, StageAccumulator>,
  runId: string,
  seq: number,
  payload: Record<string, unknown>,
): void {
  const p = payload as unknown as Partial<DecisionRecordedPayload>;
  const stageId = p.stage_id;
  if (typeof stageId !== "string") return;
  const acc = stages.get(stageId);
  if (acc === undefined) return;
  const decisionRaw = payload.decision;
  if (
    typeof decisionRaw !== "string" ||
    !KNOWN_DECISION_KINDS.has(decisionRaw)
  ) {
    return;
  }
  const decision = decisionRaw as DecisionKind;
  const scope =
    payload.scope !== null && typeof payload.scope === "object"
      ? (payload.scope as Record<string, unknown>)
      : {};
  const scopeRevRaw = scope.rev;
  const scopeRev =
    typeof scopeRevRaw === "number" &&
    Number.isInteger(scopeRevRaw) &&
    scopeRevRaw >= 1
      ? scopeRevRaw
      : null;
  const scopeRowKeys = Array.isArray(scope.row_keys)
    ? (scope.row_keys.filter((k) => typeof k === "string" && k) as string[])
    : [];
  const apply = (payload as Record<string, unknown>).apply === true;
  const actor = strOr(payload.actor, "");
  acc.decisions.push({
    decision,
    scopeRev,
    actor,
    seq,
    ledgerId: safeLedgerId(runId, seq),
  });
  if (seq > acc.lastSeq) acc.lastSeq = seq;

  // PRD-D3 — a row-scoped decision. `apply=true` freezes to apply_pending;
  // otherwise it is a stance toggle (agent pre-hold reason stays STICKY).
  if (scopeRowKeys.length > 0 && acc.isRowset) {
    if (apply) {
      acc.status = "apply_pending";
      acc.approvedRev = acc.latestRev;
    } else {
      for (const rk of scopeRowKeys) {
        if (!acc.stagedRows.has(rk)) continue;
        if (decision === "approve") {
          acc.rowStances.set(rk, "will_apply");
          acc.rowDecidedBy.set(rk, decidedByOf(actor));
        } else if (decision === "hold") {
          acc.rowStances.set(rk, "held");
          acc.rowDecidedBy.set(rk, decidedByOf(actor));
        }
      }
    }
    return;
  }

  // Single-artifact (D1) rev-scoped path — unchanged.
  if (decision === "approve") {
    acc.status = "approved";
    acc.approvedRev = scopeRev;
  } else if (decision === "reject") {
    acc.status = "rejected";
    acc.approvedRev = null;
  } else if (decision === "restore") {
    acc.status = "staged";
    acc.approvedRev = null;
  }
}

function decidedByOf(actor: string): "agent" | "user" | "policy" {
  return actor === "policy" ? "policy" : "user";
}

/** Fold `write.applied` (PRD-D2) — the TS twin of the Python fold's state machine.
 *  `APPROVED (rev N)` + `applied {rev N}` ⇒ `applied` (terminal); `APPROVED` +
 *  `failed {rev N}` ⇒ `staged` with `approvedRev` cleared (held, approval
 *  consumed) + the failure code; any other current state ⇒ `corrupt` (defensive,
 *  fail-closed — a forged applied cannot masquerade as a real send). */
function applyWriteApplied(
  stages: Map<string, StageAccumulator>,
  seq: number,
  payload: Record<string, unknown>,
): void {
  const stageId = payload.stage_id;
  if (typeof stageId !== "string") return;
  const acc = stages.get(stageId);
  if (acc === undefined) return; // applied for an unseen stage is ignored
  if (seq > acc.lastSeq) acc.lastSeq = seq;
  const result = payload.result;

  // PRD-D3 — a row-set terminal (matched by the frozen apply_pending state).
  if (acc.isRowset) {
    applyRowsetTerminal(acc, result, payload);
    return;
  }

  const revRaw = payload.rev;
  const rev =
    typeof revRaw === "number" && Number.isInteger(revRaw) ? revRaw : null;
  const matchesApproved =
    acc.status === "approved" &&
    acc.approvedRev !== null &&
    rev === acc.approvedRev;
  if (!matchesApproved) {
    acc.status = "corrupt";
    acc.applyResult =
      typeof result === "string" ? (result as LedgerApplyResult) : null;
    return;
  }
  if (result === "applied") {
    acc.status = "applied";
    acc.applyResult = "applied";
    acc.applyFailureCode = null;
  } else if (result === "failed") {
    // Approval consumed: back to held; a fresh approve retries.
    acc.status = "staged";
    acc.approvedRev = null;
    acc.applyResult = "failed";
    acc.applyFailureCode = failureCodeOf(payload.failure);
  } else {
    acc.status = "corrupt";
    acc.applyResult =
      typeof result === "string" ? (result as LedgerApplyResult) : null;
  }
}

/** Fold a row-set `write.applied` (PRD-D3) — only legitimate from apply_pending.
 *  `applied` ⇒ applied; `partial` ⇒ partially_applied (both terminal, per-row
 *  outcomes from `row_results`); `failed` ⇒ back to `staged` (apply consumed,
 *  stances intact); any other current state ⇒ `corrupt` (fail-closed). */
function applyRowsetTerminal(
  acc: StageAccumulator,
  result: unknown,
  payload: Record<string, unknown>,
): void {
  if (acc.status !== "apply_pending") {
    acc.status = "corrupt";
    acc.applyResult =
      typeof result === "string" ? (result as LedgerApplyResult) : null;
    return;
  }
  if (result === "applied") {
    acc.status = "applied";
    acc.applyResult = "applied";
    foldRowResults(acc, payload.row_results);
  } else if (result === "partial") {
    acc.status = "partially_applied";
    acc.applyResult = "partial";
    foldRowResults(acc, payload.row_results);
  } else if (result === "failed") {
    acc.status = "staged";
    acc.approvedRev = null;
    acc.applyResult = "failed";
  } else {
    acc.status = "corrupt";
    acc.applyResult =
      typeof result === "string" ? (result as LedgerApplyResult) : null;
  }
}

function foldRowResults(acc: StageAccumulator, value: unknown): void {
  if (!Array.isArray(value)) return;
  for (const raw of value) {
    if (raw === null || typeof raw !== "object") continue;
    const r = raw as Record<string, unknown>;
    const rk = r.row_key;
    const outcome = r.outcome;
    if (typeof rk !== "string" || !acc.stagedRows.has(rk)) continue;
    if (outcome === "applied" || outcome === "failed") {
      acc.rowApplyOutcomes.set(rk, outcome);
    }
  }
}

/** Pull `failure.code` (a string) from a `write.applied{failed}` payload. */
function failureCodeOf(value: unknown): string | null {
  if (value === null || typeof value !== "object") return null;
  const code = (value as Record<string, unknown>).code;
  return typeof code === "string" && code.length > 0 ? code : null;
}

/** Compose a stage's folded rows + counts (row-set only), or `[null, null]`. */
function composeRows(
  acc: StageAccumulator,
): [readonly LedgerStagedRow[] | null, LedgerRowCounts | null] {
  if (!acc.isRowset) return [null, null];
  const rows: LedgerStagedRow[] = acc.rowOrder.map((rk) => {
    const content = acc.stagedRows.get(rk);
    return {
      rowKey: rk,
      title: content?.title ?? rk,
      changes: content?.changes ?? [],
      stance: acc.rowStances.get(rk) ?? "will_apply",
      agentHoldReason: acc.agentHoldReasons.get(rk) ?? null,
      decidedBy: acc.rowDecidedBy.get(rk) ?? null,
      applyOutcome: acc.rowApplyOutcomes.get(rk) ?? null,
    };
  });
  const counts: LedgerRowCounts = {
    total: rows.length,
    willApply: rows.filter((r) => r.stance === "will_apply").length,
    held: rows.filter((r) => r.stance === "held").length,
    applied: rows.filter((r) => r.applyOutcome === "applied").length,
    failed: rows.filter((r) => r.applyOutcome === "failed").length,
  };
  return [rows, counts];
}

function freezeStage(acc: StageAccumulator): LedgerStagedWrite {
  const latestRevision =
    acc.revisions.length === 0
      ? null
      : acc.revisions.reduce((best, r) => (r.rev > best.rev ? r : best));
  const [rows, rowCounts] = composeRows(acc);
  return {
    stageId: acc.stageId,
    surfaceId: acc.surfaceId,
    draftId: acc.draftId,
    target: acc.target,
    latestRev: acc.latestRev,
    approvedRev: acc.approvedRev,
    status: acc.status,
    revisions: acc.revisions.slice(),
    decisions: acc.decisions.slice(),
    createdSeq: acc.createdSeq,
    lastSeq: acc.lastSeq,
    ledgerId: acc.ledgerId,
    latestRevision,
    applyResult: acc.applyResult,
    applyFailureCode: acc.applyFailureCode,
    rows,
    rowCounts,
  };
}

/** Recover the connector `server_id` from a `mcp_auth:<run_id>:<server_id>` gate
 *  id. `server_id` may itself contain colons (e.g. `seed:linear`), so strip the
 *  known `mcp_auth:<run_id>:` prefix rather than splitting. Falls back to the
 *  full gate id when the prefix does not match. */
function serverIdFromGateId(gateId: string, runId: string): string {
  const prefix = `mcp_auth:${runId}:`;
  if (runId !== "" && gateId.startsWith(prefix)) {
    return gateId.slice(prefix.length);
  }
  const marker = "mcp_auth:";
  if (gateId.startsWith(marker)) {
    const rest = gateId.slice(marker.length);
    const sep = rest.indexOf(":");
    if (sep >= 0) return rest.slice(sep + 1);
  }
  return gateId;
}

function freezeGate(acc: GateAccumulator): LedgerGate {
  return {
    gateId: acc.gateId,
    serverId: acc.serverId,
    connector: acc.connector,
    purpose: acc.purpose,
    scopes: acc.scopes,
    authState: acc.authState,
    opClass: acc.opClass,
    ledgerId: acc.ledgerId,
    createdSeq: acc.createdSeq,
    lastSeq: acc.lastSeq,
    resolved: acc.resolved,
    outcome: acc.outcome,
    writePolicy: acc.writePolicy,
  };
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
    keep: null,
    shapedAvailable: false,
    regenCount: 0,
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
  const tierValue = (typeof tier === "string" ? tier : "") as LedgerViewTier;
  const basisValue = strOr(payload.basis, "");
  // Upgrades merge in place (same surface_id / tab identity — no remount): the
  // latest derivation wins for tier/basis, and a shaped derivation unlocks the
  // toggle without erasing an earlier "Keep generic" pin.
  acc.view = {
    tier: tierValue,
    basis: basisValue,
    specRef: typeof specRef === "string" && specRef.length > 0 ? specRef : null,
    generatorModel,
    preference: acc.keep,
  };
  if (tierValue === "shaped") acc.shapedAvailable = true;
  // regenCount: prior non-first, non-registry derivations (PRD-B3 / SDR). The
  // first derivation seeds the surface; each later non-registry one is a repair.
  if (basisValue !== "registry") acc.regenCount += 1;
  if (seq > acc.lastSeq) acc.lastSeq = seq;
}

/** Fold `view.preference` — the durable tier pin (PRD-B3). Sets `keep` so a
 *  later shaped `view.derived` never clobbers a "Keep generic", and advances
 *  `lastSeq` (mirrors the Python fold). A preference for an unseen surface or a
 *  malformed `keep` is ignored. */
function applyViewPreference(
  surfaces: Map<string, SurfaceAccumulator>,
  seq: number,
  payload: Record<string, unknown>,
): void {
  const p = payload as unknown as Partial<ViewPreferencePayload>;
  const surfaceId = p.surface_id;
  if (typeof surfaceId !== "string") return;
  const acc = surfaces.get(surfaceId);
  if (acc === undefined) return;
  const keep = payload.keep;
  if (keep !== "generic" && keep !== "shaped") return;
  acc.keep = keep;
  if (acc.view !== null) {
    acc.view = { ...acc.view, preference: keep };
  }
  if (seq > acc.lastSeq) acc.lastSeq = seq;
}

/** Effective rendered tier: `keep ?? tier-of-latest-view.derived`, where a
 *  `keep: "shaped"` only folds once a shaped derivation exists (SDR §5). */
function effectiveTierOf(acc: SurfaceAccumulator): LedgerViewTier {
  const derived: LedgerViewTier = acc.view?.tier ?? "raw";
  if (acc.keep === "generic") return "generic";
  if (acc.keep === "shaped" && acc.shapedAvailable) return "shaped";
  return derived;
}

function freeze(acc: SurfaceAccumulator): LedgerSurface {
  // regenCount excludes the first (seeding) derivation — the server cap is the
  // authoritative one; this mirror only disables the Regenerate affordance.
  const regenCount = acc.regenCount > 0 ? acc.regenCount - 1 : 0;
  const viewState: LedgerSurfaceViewState | null =
    acc.view === null
      ? null
      : {
          tier: acc.view.tier,
          basis: acc.view.basis,
          specRef: acc.view.specRef,
          keep: acc.keep,
          shapedAvailable: acc.shapedAvailable,
          regenCount,
          effectiveTier: effectiveTierOf(acc),
        };
  return {
    surfaceId: acc.surfaceId,
    kind: acc.kind,
    title: acc.title,
    source: acc.source,
    payloadRef: acc.payloadRef,
    view: acc.view,
    viewTier: acc.view?.tier ?? null,
    viewState,
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
              // PRD-B3: the durable pin, mirrored so this snapshot byte-equals
              // the Python fold's `SurfaceViewState.model_dump` (`null` unpinned).
              preference: s.view.preference,
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
