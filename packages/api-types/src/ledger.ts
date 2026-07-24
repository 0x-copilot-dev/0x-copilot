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

/** One of the 15 ledger event types (SDR §5). */
export type LedgerEventType =
  | "gate.opened"
  | "gate.resolved"
  | "action.classified"
  | "read.executed"
  | "surface.created"
  | "view.derived"
  | "view.preference"
  | "shape.requested"
  | "shape.resolved"
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
  "shape.resolved",
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
/** Outcome of a user-invited `shape.requested` attempt (PRD-B4, SDR §5). */
export type ShapeOutcome = "shaped" | "no_fit";

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

/** A row's decision stance in the fold (PRD-D3). */
export type RowStance = "will_apply" | "held";

/** Per-row outcome of a `write.applied` (PRD-D3). */
export type RowOutcome = "applied" | "failed";

/** One field's old→new diff on a staged row (display only, PRD-D3). */
export interface RowFieldChange {
  field: string;
  old?: unknown;
  new?: unknown;
}

/** One proposed row change — the WYSIWYG unit a user approves/holds (PRD-D3).
 *  `target_args` is server-only and never rides the wire view. */
export interface StagedRow {
  row_key: string;
  title: string;
  target_args?: Record<string, unknown>;
  changes: readonly RowFieldChange[];
}

/** Folded per-row state (PRD-D3). `agent_hold_reason` is sticky — it survives a
 *  user override (FR-C7). */
export interface RowState {
  row_key: string;
  stance: RowStance;
  agent_hold_reason?: string | null;
  decided_by?: "agent" | "user" | "policy" | null;
  apply_outcome?: RowOutcome | null;
}

/** Projection summary over a stage's rows (PRD-D3). */
export interface RowCounts {
  total: number;
  will_apply: number;
  held: number;
  applied: number;
  failed: number;
}

/** One `write.applied.row_results` entry — a per-row apply outcome (PRD-D3). */
export interface WriteAppliedRowResult {
  row_key: string;
  outcome: RowOutcome;
  detail?: string;
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

/** Outcome of a user-invited shaping attempt (PRD-B4, additive to SDR §5).
 *  `reason` is the safe lint/validation summary on a `no_fit` (never raw model
 *  output); absent on a `shaped` outcome. */
export interface ShapeResolvedPayload {
  v: 1;
  surface_id: string;
  outcome: ShapeOutcome;
  reason?: string;
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

/** A half-open `[start, end)` char range of a revision's NEW text and its author
 *  (PRD-D1). Offsets index code points into the new revision body; the server
 *  computes these by diffing the user edit against the previous revision. */
export interface AuthorshipSpan {
  start: number;
  end: number;
  author: RevisionAuthor;
}

export interface RevisionAddedPayload {
  v: 1;
  stage_id: string;
  rev: number;
  author: RevisionAuthor;
  diff_ref: string;
  /** Additive (SDR §5 note, PRD-D1): the `draft://…` / `stage://…` snapshot ref. */
  proposal_ref?: string;
  /** Additive (PRD-D1): the server-computed "edited by you" spans; `[]`/absent
   *  for the agent's rev 1. */
  authorship_spans?: readonly AuthorshipSpan[];
  /** Additive (PRD-D3): the full inline row-set for a bulk (table) stage. */
  rowset?: { rows: readonly StagedRow[] };
}

export interface DecisionRecordedPayload {
  v: 1;
  stage_id: string;
  decision: DecisionKind;
  scope: DecisionScope;
  actor: DecisionActor;
  /** Additive (PRD-D3): present + `true` only on the apply-scoped approve (the
   *  frozen decision that authorizes exactly `scope.row_keys` to execute). */
  apply?: boolean;
}

/** Why an apply refused / failed (PRD-D2, additive to SDR §5). */
export type WriteFailureCode =
  | "precondition_drift"
  | "connector_error"
  | "attempt_indeterminate";

/** The `write.applied.failure` object — present only on a `failed` result. */
export interface WriteAppliedFailure {
  code: WriteFailureCode;
  detail?: string;
}

/** The `write.applied.decided_by` object — the receipt-row attribution (PRD-D2). */
export interface WriteAppliedDecidedBy {
  actor: "user";
  decision_seq: number;
}

export interface WriteAppliedPayload {
  v: 1;
  stage_id: string;
  rev: number;
  result: ApplyResult;
  row_keys?: readonly string[];
  connector_receipt_ref?: string;
  /** Additive (SDR §5 note, PRD-D2): present only on a `failed` result. */
  failure?: WriteAppliedFailure;
  /** Additive (PRD-D2): the approving decision, for the receipt fold (E1). */
  decided_by?: WriteAppliedDecidedBy;
  /** Additive (PRD-D3): per-row apply outcomes (present on a row-set apply). */
  row_results?: readonly WriteAppliedRowResult[];
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
  "shape.resolved": ShapeResolvedPayload;
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

// ---------------------------------------------------------------------------
// Staged-write HTTP wire types (PRD-D1). Request bodies + the `StagedWriteView`
// the three `/v1/agent/stages/*` routes return (the wire projection of the
// server's `StagedWriteState` fold, with ledger ids attached per revision +
// decision). Additive; the pydantic mirror is `schemas/stages.py`.
// ---------------------------------------------------------------------------

/** Body for `POST /v1/agent/stages/{stage_id}/revisions` — a user free-form edit.
 *  `run_id` rides the query string (stage state is a pure fold of that run). */
export interface StageRevisionRequest {
  base_rev: number;
  content_text: string;
  title?: string | null;
}

/** Body for `POST /v1/agent/stages/{stage_id}/decisions`. `rev` is required for
 *  `approve`/`reject` (WYSIWYG) and ignored for `restore` (server re-pins the
 *  latest rev); `hold` reaches the server and 422s (single-artifact — D3). */
export interface StageDecisionRequest {
  decision: DecisionKind;
  rev?: number;
  /** PRD-D3 — row-set stance toggle scope (approve/hold). Exactly one of `rev`
   *  or `row_keys` (never both). */
  row_keys?: readonly string[];
}

/** Body for `POST /v1/agent/stages/{stage_id}/apply` (PRD-D3). The applied set
 *  must equal the current will-apply set exactly (409 on a mismatch). */
export interface StageApplyRequest {
  rev: number;
  row_keys: readonly string[];
}

/** One field diff on a staged row in the wire view (PRD-D3). */
export interface StageRowChangeView {
  field: string;
  old?: unknown;
  new?: unknown;
}

/** One staged row in the wire view: content + folded state (PRD-D3). */
export interface StageRowView {
  row_key: string;
  title: string;
  changes: readonly StageRowChangeView[];
  stance: RowStance;
  agent_hold_reason?: string | null;
  decided_by?: "agent" | "user" | "policy" | null;
  apply_outcome?: RowOutcome | null;
}

/** Row-count summary in the wire view (PRD-D3). */
export interface StageRowCountsView {
  total: number;
  will_apply: number;
  held: number;
  applied: number;
  failed: number;
}

/** One revision on the stage wire view. */
export interface StageRevisionView {
  rev: number;
  author: RevisionAuthor;
  proposal_ref: string;
  diff_ref: string;
  authorship_spans: readonly AuthorshipSpan[];
  ledger_id: string;
}

/** One recorded decision on the stage wire view. */
export interface StageDecisionView {
  decision: DecisionKind;
  scope_rev: number | null;
  actor: DecisionActor;
  ledger_id: string;
}

/** `GET/POST /v1/agent/stages/{stage_id}[...]` response — the folded staged
 *  write. `status` is `staged|rejected|approved|applied` (`applied` is D2). */
export interface StagedWriteView {
  stage_id: string;
  surface_id: string;
  run_id: string;
  draft_id: string;
  target: LedgerOpRef;
  latest_rev: number;
  approved_rev: number | null;
  status: string;
  revisions: readonly StageRevisionView[];
  decisions: readonly StageDecisionView[];
  /** PRD-D3 — populated for a bulk row-set stage; `null` for single-artifact. */
  rows?: readonly StageRowView[] | null;
  row_counts?: StageRowCountsView | null;
}

// ---------------------------------------------------------------------------
// Suggest-a-shape HTTP wire types (PRD-B4). The user-invited shaping attempt
// on a raw/generic fallback surface. `POST /v1/agent/surfaces/{surface_id}/
// shape-request`; the outcome arrives over the run SSE stream as `shape.requested`
// / `shape.resolved` ledger events (no polling). Additive.
// ---------------------------------------------------------------------------

/** Body for `POST /v1/agent/surfaces/{surface_id}/shape-request`. `run_id` is
 *  required (the canvas is per-run — FR-A2); org/user are stamped by the facade
 *  and are never client-supplied. */
export interface ShapeRequestBody {
  run_id: string;
}

/** `202 Accepted` body — the request is scheduled; the outcome streams as
 *  ledger events. */
export interface ShapeRequestAccepted {
  surface_id: string;
  status: "requested";
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
// Receipt export (PRD-E3). Served by `GET /v1/agent/runs/{run_id}/receipt/export`
// — the receipt's first + only wire surface, re-folded from the ledger and
// HMAC-chained with the shared `packages/audit-chain` signer so flipping one
// byte anywhere (a row payload, the receipt fold, a signature, the order) fails
// verification. Type-only mirror; no fetch wrapper. The synthetic final row's
// `event_type` is `"receipt.export"` — an export-format construct, NOT a ledger
// event type (never in `LEDGER_EVENT_TYPES`).
// ---------------------------------------------------------------------------

/** One signed row of the export chain. */
export interface ReceiptExportRow {
  /** 1-based position in the export chain. */
  seq: number;
  /** `LedgerIdCodec.format(run_id, sequence_no)` — `r<short>·<seq>`. */
  ledger_id: string;
  /** SDR §5 wire value, or the synthetic `"receipt.export"` for the final row. */
  event_type: LedgerEventType | "receipt.export";
  /** The run-stream sequence (synthetic row: highest folded seq + 1, or 1). */
  sequence_no: number;
  /** ISO-8601. */
  created_at: string;
  /** The envelope payload (synthetic row: the receipt fold). */
  payload: Record<string, unknown>;
  /** Hex of the prior row's signature; `null` on the first row. */
  prev_hash: string | null;
  /** Hex HMAC-SHA256. */
  signature: string;
  key_version: number;
}

/** `GET /v1/agent/runs/{run_id}/receipt/export` — the durable, tamper-evident
 * receipt export. `receipt` is E1's fold output, re-derived at export time. */
export interface ReceiptExportBundle {
  export_version: 1;
  run_id: string;
  generated_at: string;
  receipt: RunReceipt;
  rows: readonly ReceiptExportRow[];
  /** Hex of the last (synthetic) row's signature. */
  head_hash: string;
}

// ---------------------------------------------------------------------------
// SurfaceStore fold projection (PRD-A3). Served by
// `GET /v1/agent/runs/{run_id}/surfaces`. Distinct from the `Surface` ledger
// entity above (2026-07-23 close-out; PRD-A3 Open questions item 1): these carry
// the fold bookkeeping (`first_sequence_no` / `last_sequence_no`) A1's `Surface`
// lacks, and `generator_model` (A1's `gen.model`) instead of `preference`. B1's
// client fold + parity snapshot target THIS shape (snake_case, metadata-only).
// ---------------------------------------------------------------------------

/** The derived-view state of a surface, folded from `view.derived`. PRD-B3 adds
 * `preference` — the durable tier pin folded from `view.preference` (the
 * server half of "Keep generic survives reload"), absent until the user pins. */
export interface SurfaceViewState {
  tier: ViewTier;
  basis: ViewBasis;
  spec_ref?: string;
  generator_model?: string;
  preference?: ViewKeep;
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

// ---------------------------------------------------------------------------
// Pending work (Generative Surfaces v2, PRD-E2) — the cross-run queue read
// model. `GET /v1/agent/pending-work` aggregates everything still waiting on the
// user (parked gates, held drafts, undecided row-sets) plus the fleet of runs
// with in-flight / held work. A read-side response only: no new event types.
// ---------------------------------------------------------------------------

/** What a pending queue card decides on. */
export type PendingItemKind = "gate" | "staged_write";

/** One thing waiting on the user, with enough to render a card + jump to its
 * surface. `gate_id` / `title=purpose` for a gate; `stage_id` / `surface_id` /
 * `title="{connector} · {op}"` for a staged write. `rows_pending` / `rows_total`
 * are present only for row-sets. */
export interface PendingWorkItem {
  v: 1;
  item_kind: PendingItemKind;
  run_id: string;
  conversation_id: string;
  conversation_title: string | null;
  gate_id: string | null;
  stage_id: string | null;
  surface_id: string | null;
  title: string;
  connector: string;
  op: string | null;
  /** `r<short>·<seq>` of the opening event (A1 formatter). */
  ledger_id: string;
  opened_sequence_no: number;
  /** ISO-8601 timestamp of the opening event. */
  opened_at: string;
  rows_pending: number | null;
  rows_total: number | null;
}

/** One run in the fleet view — this run plus other runs with in-flight or held
 * work. `pending_count` is this run's items in the merged queue. */
export interface PendingAgentRow {
  v: 1;
  run_id: string;
  conversation_id: string;
  conversation_title: string | null;
  /** `AgentRunStatus` value, presentation-ready. */
  run_status: string;
  pending_count: number;
}

/** `GET /v1/agent/pending-work` response — the cross-run aggregate. */
export interface PendingWorkResponse {
  v: 1;
  items: readonly PendingWorkItem[];
  agents: readonly PendingAgentRow[];
}

/** Structural guard for a `PendingWorkResponse`: `v === 1` and both collections
 * are arrays. Defensive at the client boundary — the fold treats each item as
 * data (hostile strings render as text only), so this only pins the envelope. */
export function isPendingWorkResponse(x: unknown): x is PendingWorkResponse {
  if (typeof x !== "object" || x === null) return false;
  const r = x as Record<string, unknown>;
  return r.v === 1 && Array.isArray(r.items) && Array.isArray(r.agents);
}
