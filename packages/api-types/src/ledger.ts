// Work Ledger vocabulary (Generative Surfaces v2, SDR §5 / PRD-A1). Mirrors the
// JSON SSOT at
// `packages/service-contracts/src/copilot_service_contracts/work_ledger.json`
// and the pydantic models in
// `services/ai-backend/src/agent_runtime/surfaces_v2/`. Cross-language parity
// tests (`ledger.test.ts` + `test_ledger_contract_parity.py`) pin all three
// together — drift on any side fails CI.
//
// This is the single canonical home for all v2 ledger/domain type additions
// across every wave; `index.ts` only ever re-exports from here. The JSON is
// imported directly by relative path (like `adapterAllowlist.ts`) so the same
// on-disk file drives the runtime guards + id codec below.
import contract from "../../service-contracts/src/copilot_service_contracts/work_ledger.json";

// ---------------------------------------------------------------------------
// Event types
// ---------------------------------------------------------------------------

/** One of the 14 ledger event types (SDR §5). */
export type LedgerEventType =
  | "gate.opened"
  | "gate.resolved"
  | "action.classified"
  | "read.executed"
  | "surface.created"
  | "view.derived"
  | "view.preference"
  | "shape.requested"
  | "write.staged"
  | "revision.added"
  | "decision.recorded"
  | "write.applied"
  | "usage.recorded"
  | "receipt.emitted";

/** Runtime SSOT tuple for the event-type union, in contract order. Pinned to
 * the service-contracts JSON `events` key order by `ledger.test.ts`. Later
 * waves append (never reorder) — SDR §12. */
export const LEDGER_EVENT_TYPES = [
  "gate.opened",
  "gate.resolved",
  "action.classified",
  "read.executed",
  "surface.created",
  "view.derived",
  "view.preference",
  "shape.requested",
  "write.staged",
  "revision.added",
  "decision.recorded",
  "write.applied",
  "usage.recorded",
  "receipt.emitted",
] as const satisfies readonly LedgerEventType[];

// ---------------------------------------------------------------------------
// Value unions (one per `enums` key in the JSON, values verbatim)
// ---------------------------------------------------------------------------

export type GateAuthState = "missing" | "expired" | "insufficient";
export type GateOutcome = "connected" | "cancelled";
export type WritePolicy = "ask_first" | "allow_always";
export type ActionClass = "read" | "write" | "unknown";
export type ClassificationBasis = "catalog" | "annotation" | "default";
export type SurfaceKind =
  | "record"
  | "message"
  | "table"
  | "call"
  | "raw"
  | "receipt"
  | "gate";
export type ViewTier = "raw" | "generic" | "shaped";
export type ViewBasis = "schema" | "registry" | "generated";
export type ViewKeep = "generic" | "shaped";
export type RevisionAuthor = "agent" | "user";
export type DecisionKind = "approve" | "reject" | "hold" | "restore";
export type DecisionActor = "user" | "policy";
export type ApplyResult = "applied" | "partial" | "failed";
export type UsagePurpose =
  | "run"
  | "subagent"
  | "view_shaping"
  | "shape_request";

// ---------------------------------------------------------------------------
// Shared value objects
// ---------------------------------------------------------------------------

/** The connector server + operation an action / surface targets. */
export interface LedgerOpRef {
  connector: string;
  op: string;
}

/** A row the agent staged but deliberately withheld, with its reason. */
export interface AgentHold {
  row_key: string;
  reason: string;
}

/** Generation provenance for a shaped view (`view.derived.gen`). */
export interface ViewGen {
  model: string;
  ms: number;
}

/** Exactly one of `{rev}` (single artifact) or `{row_keys}` (row set). */
export type DecisionScope =
  | { rev: number; row_keys?: never }
  | { row_keys: readonly string[]; rev?: never };

// ---------------------------------------------------------------------------
// Payload interfaces (one per event type; fields per SDR §5)
// ---------------------------------------------------------------------------

export interface GateOpenedPayload {
  v: 1;
  gate_id: string;
  connector: string;
  purpose: string;
  scopes: readonly string[];
  auth_state: GateAuthState;
}

export interface GateResolvedPayload {
  v: 1;
  gate_id: string;
  outcome: GateOutcome;
  write_policy?: WritePolicy;
}

/** `class` is the SDR-verbatim wire key (a legal TS property name). */
export interface ActionClassifiedPayload {
  v: 1;
  call_id: string;
  connector: string;
  op: string;
  class: ActionClass;
  basis: ClassificationBasis;
}

export interface ReadExecutedPayload {
  v: 1;
  call_id: string;
  connector: string;
  op: string;
  latency_ms: number;
  payload_ref: string;
}

export interface SurfaceCreatedPayload {
  v: 1;
  surface_id: string;
  kind: SurfaceKind;
  source: LedgerOpRef;
  title: string;
  payload_ref: string;
}

export interface ViewDerivedPayload {
  v: 1;
  surface_id: string;
  tier: ViewTier;
  basis: ViewBasis;
  spec_ref?: string;
  gen?: ViewGen;
}

export interface ViewPreferencePayload {
  v: 1;
  surface_id: string;
  keep: ViewKeep;
  // SDR §5 pins `actor` to the constant `"user"` here (not `DecisionActor`).
  actor: "user";
}

export interface ShapeRequestedPayload {
  v: 1;
  surface_id: string;
  // SDR §5 pins `actor` to the constant `"user"` here.
  actor: "user";
}

export interface WriteStagedPayload {
  v: 1;
  stage_id: string;
  surface_id: string;
  target: LedgerOpRef;
  proposal_ref: string;
  rows?: number;
  agent_holds?: readonly AgentHold[];
}

export interface RevisionAddedPayload {
  v: 1;
  stage_id: string;
  rev: number;
  author: RevisionAuthor;
  diff_ref: string;
}

export interface DecisionRecordedPayload {
  v: 1;
  stage_id: string;
  decision: DecisionKind;
  scope: DecisionScope;
  actor: DecisionActor;
}

export interface WriteAppliedPayload {
  v: 1;
  stage_id: string;
  rev: number;
  result: ApplyResult;
  row_keys?: readonly string[];
  connector_receipt_ref?: string;
}

export interface UsageRecordedPayload {
  v: 1;
  purpose: UsagePurpose;
  model: string;
  tokens_in: number;
  tokens_out: number;
  surface_id?: string;
}

export interface ReceiptEmittedPayload {
  v: 1;
  surface_id: string;
  fold_ref: string;
}

/** Event-type → payload map. The `SurfaceEventV2` definition below references
 * `LedgerEventPayloadMap[K]` for every `K in LedgerEventType`, so a missing key
 * is a compile error — that is the exhaustiveness pin. */
export interface LedgerEventPayloadMap {
  "gate.opened": GateOpenedPayload;
  "gate.resolved": GateResolvedPayload;
  "action.classified": ActionClassifiedPayload;
  "read.executed": ReadExecutedPayload;
  "surface.created": SurfaceCreatedPayload;
  "view.derived": ViewDerivedPayload;
  "view.preference": ViewPreferencePayload;
  "shape.requested": ShapeRequestedPayload;
  "write.staged": WriteStagedPayload;
  "revision.added": RevisionAddedPayload;
  "decision.recorded": DecisionRecordedPayload;
  "write.applied": WriteAppliedPayload;
  "usage.recorded": UsageRecordedPayload;
  "receipt.emitted": ReceiptEmittedPayload;
}

/** One v2 ledger event on the wire (envelope-lite: the fields every projector
 * folds; `ledger_id` is derived, never carried — SDR §5). */
export type SurfaceEventV2 = {
  [K in LedgerEventType]: {
    event_type: K;
    run_id: string;
    sequence_no: number;
    created_at: string;
    payload: LedgerEventPayloadMap[K];
  };
}[LedgerEventType];

// Compile-time: the payload-map keys are a subset of the event-type union
// (`SurfaceEventV2` already pins the other direction — every event type must
// have a payload entry). Together they make the map exactly the event set.
type _MapKeysAreEventTypes = keyof LedgerEventPayloadMap extends LedgerEventType
  ? true
  : never;
const _mapKeysAreEventTypes: _MapKeysAreEventTypes = true;
void _mapKeysAreEventTypes;

// ---------------------------------------------------------------------------
// Entity types (projection outputs later endpoints serve; additive until
// E-wave, removals/renames are breaking per packages/api-types/CLAUDE.md)
// ---------------------------------------------------------------------------

export interface Revision {
  rev: number;
  author: RevisionAuthor;
  diff_ref: string;
  created_at: string;
  ledger_id: string;
}

export interface Decision {
  decision: DecisionKind;
  scope: DecisionScope;
  actor: DecisionActor;
  decided_at: string;
  ledger_id: string;
}

/** `Surface` is the ledger **entity** (the richer canvas/B-E-wave entity).
 * A3's `SurfaceSnapshot` fold projection is intentionally distinct and does not
 * edit this type (2026-07-23 close-out; PRD-A3 Open questions item 1). */
export interface Surface {
  surface_id: string;
  run_id: string;
  kind: SurfaceKind;
  title: string;
  source: LedgerOpRef;
  payload_ref: string;
  ledger_id: string;
  created_at: string;
  view: {
    tier: ViewTier;
    basis: ViewBasis;
    spec_ref?: string;
    preference?: ViewKeep;
  } | null;
}

export interface StagedWrite {
  stage_id: string;
  surface_id: string;
  run_id: string;
  target: LedgerOpRef;
  proposal_ref: string;
  rows: number | null;
  agent_holds: readonly AgentHold[];
  revisions: readonly Revision[];
  decisions: readonly Decision[];
  latest_rev: number;
}

/** FR-E2 wording, wire-safe (NEW, A1-defined). Not a ledger event type — a
 * receipt-format construct the E-wave receipt fold assigns per row. */
export type ReceiptAttribution =
  | "auto_ran"
  | "approved"
  | "held"
  | "rejected"
  | "auto_applied"
  | "no_view_fit";

export interface UsageRecord {
  purpose: UsagePurpose;
  model: string;
  tokens_in: number;
  tokens_out: number;
  run_id: string;
  conversation_id: string;
  surface_id?: string;
  created_at: string;
  ledger_id: string;
}

export interface RunReceiptRow {
  ledger_id: string;
  event_type: LedgerEventType;
  title: string;
  attribution: ReceiptAttribution;
  at: string;
}

export interface RunReceipt {
  run_id: string;
  surface_id: string;
  fold_ref: string;
  generated_at: string;
  tiles: {
    reads_auto_ran: number;
    writes_proposed: number;
    writes_approved: number;
    holds_untouched: number;
  };
  rows: readonly RunReceiptRow[];
}

// ---------------------------------------------------------------------------
// SurfaceStore fold projection (PRD-A3). Served by
// `GET /v1/agent/runs/{run_id}/surfaces`. Distinct from the `Surface` ledger
// entity above (2026-07-23 close-out; PRD-A3 Open questions item 1): these carry
// the fold bookkeeping (`first_sequence_no` / `last_sequence_no`) A1's `Surface`
// lacks, and `generator_model` (A1's `gen.model`) instead of `preference`. B1's
// client fold + parity snapshot target THIS shape (snake_case, metadata-only).
// ---------------------------------------------------------------------------

/** The derived-view state of a surface, folded from `view.derived`. */
export interface SurfaceViewState {
  tier: ViewTier;
  basis: ViewBasis;
  spec_ref?: string;
  generator_model?: string;
}

/** One surface's folded metadata. `view` is present only once a `view.derived`
 * has landed for the surface. PRD-B2 content hydration: `state` carries the
 * surface's materialized `{spec?, data}`, resolved server-side from the run's
 * events — `null`/absent until a content event has landed (honest "not
 * hydrated", never fabricated). The metadata fields are pinned by the
 * cross-language parity snapshot; `state` is additive and NOT part of it (the
 * pure fold cannot produce content). */
export interface SurfaceSnapshot {
  surface_id: string;
  kind: SurfaceKind;
  connector: string;
  op: string;
  title: string;
  payload_ref: string;
  view?: SurfaceViewState | null;
  first_sequence_no: number;
  last_sequence_no: number;
  ledger_id: string;
  /** PRD-B2 hydrated content (`{spec?, data}`); absent/null when not hydrated. */
  state?: Record<string, unknown> | null;
}

/** `GET /v1/agent/runs/{run_id}/surfaces` response — the run's SurfaceStore. */
export interface RunSurfacesResponse {
  run_id: string;
  surfaces: readonly SurfaceSnapshot[];
  latest_sequence_no: number;
}

// ---------------------------------------------------------------------------
// Runtime members (guards + the ledger-id codec) — the only non-type exports,
// mirroring the `isSurfaceSpec` precedent.
// ---------------------------------------------------------------------------

const _EVENT_TYPE_SET: ReadonlySet<string> = new Set(LEDGER_EVENT_TYPES);

interface _LedgerContract {
  ledger_id: {
    prefix: string;
    short_len: number;
    separator: string;
    seq_min_width: number;
  };
  events: Record<string, { required: readonly string[] }>;
}

const _CONTRACT = contract as unknown as _LedgerContract;

/** True when `x` is one of the 14 ledger event-type strings. */
export function isLedgerEventType(x: unknown): x is LedgerEventType {
  return typeof x === "string" && _EVENT_TYPE_SET.has(x);
}

/** Structural guard for a v2 ledger event: known `event_type`, positive-int
 * `sequence_no`, `payload.v === 1`, and every key the SSOT declares required
 * for that event type present on the payload. Never trusts extra structure. */
export function isSurfaceEventV2(x: unknown): x is SurfaceEventV2 {
  if (typeof x !== "object" || x === null) return false;
  const ev = x as Record<string, unknown>;
  if (!isLedgerEventType(ev.event_type)) return false;
  if (typeof ev.run_id !== "string") return false;
  if (
    typeof ev.sequence_no !== "number" ||
    !Number.isInteger(ev.sequence_no) ||
    ev.sequence_no < 1
  ) {
    return false;
  }
  if (typeof ev.created_at !== "string") return false;
  const payload = ev.payload;
  if (typeof payload !== "object" || payload === null) return false;
  const p = payload as Record<string, unknown>;
  if (p.v !== 1) return false;
  const required = _CONTRACT.events[ev.event_type]?.required ?? [];
  for (const key of required) {
    if (!(key in p)) return false;
  }
  return true;
}

/** The two parts a ledger id decodes to (never a run handle). */
export interface ParsedLedgerId {
  run_short: string;
  sequence_no: number;
}

/** Render `(runId, sequenceNo)` as the user-visible id `r<short>·<seq>`.
 * `short` = first `short_len` chars of `runId.toLowerCase()` with `-` stripped;
 * `seq` zero-pads to `seq_min_width`, growing beyond without truncation.
 * Throws `RangeError` for `sequenceNo < 1` or a run id too short to shorten. */
export function formatLedgerId(runId: string, sequenceNo: number): string {
  const { prefix, short_len, separator, seq_min_width } = _CONTRACT.ledger_id;
  if (!Number.isInteger(sequenceNo) || sequenceNo < 1) {
    throw new RangeError(
      `sequence_no must be an integer >= 1 to form a ledger id; got ${sequenceNo}`,
    );
  }
  const normalized = runId.toLowerCase().replaceAll("-", "");
  if (normalized.length < short_len) {
    throw new RangeError(
      `run_id must normalise to at least ${short_len} characters to form a ledger id`,
    );
  }
  const short = normalized.slice(0, short_len);
  const seq = String(sequenceNo).padStart(seq_min_width, "0");
  return `${prefix}${short}${separator}${seq}`;
}

/** Decode `r<short>·<seq>` into its parts, or `null` when it does not match the
 * SSOT format. Charset is `[a-z0-9]` (not hex-only). */
export function parseLedgerId(text: string): ParsedLedgerId | null {
  const { prefix, short_len, separator, seq_min_width } = _CONTRACT.ledger_id;
  const pattern = new RegExp(
    `^${prefix}([a-z0-9]{${short_len}})${separator}([0-9]{${seq_min_width},})$`,
  );
  const match = typeof text === "string" ? pattern.exec(text) : null;
  if (match === null) return null;
  return { run_short: match[1], sequence_no: Number(match[2]) };
}
