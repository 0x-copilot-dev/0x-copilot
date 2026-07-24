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

/** One canonical Work Ledger event type, in append-only contract order. */
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
  | "receipt.emitted"
  | "operation.requested"
  | "operation.classified"
  | "operation.completed"
  | "operation.failed"
  | "artifact.created"
  | "artifact.revised"
  | "artifact.promoted"
  | "artifact.presentation_decided"
  | "effect.staged"
  | "effect.revised"
  | "effect.decision_recorded"
  | "effect.claimed"
  | "effect.applied"
  | "effect.indeterminate"
  | "effect.reconciled"
  | "gate.opened.v2"
  | "gate.resolved.v2";

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
  "operation.requested",
  "operation.classified",
  "operation.completed",
  "operation.failed",
  "artifact.created",
  "artifact.revised",
  "artifact.promoted",
  "artifact.presentation_decided",
  "effect.staged",
  "effect.revised",
  "effect.decision_recorded",
  "effect.claimed",
  "effect.applied",
  "effect.indeterminate",
  "effect.reconciled",
  "gate.opened.v2",
  "gate.resolved.v2",
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
export type Producer = "model" | "subagent" | "user" | "system";
export type EffectClass =
  | "none"
  | "internal_reversible"
  | "external_reversible"
  | "external_destructive"
  | "unknown";
export type OperationClassificationBasis =
  | "descriptor"
  | "catalog"
  | "provider_annotation"
  | "policy_override"
  | "default";
export type OperationOutcome =
  | "succeeded"
  | "staged"
  | "blocked"
  | "cancelled"
  | "failed";
export type OperationResultKind =
  | "none"
  | "artifact"
  | "activity"
  | "artifact_and_activity";
export type ArtifactKind = "code" | "document" | "dataset" | "file";
export type ArtifactAuthor =
  | "model"
  | "subagent"
  | "user"
  | "system"
  | "import";
export type ArtifactPresentationPreference =
  | "auto"
  | "canvas"
  | "chat_card"
  | "none";
export type PresentationDecision =
  | "canvas"
  | "chat_card"
  | "activity_only"
  | "none";
export type SurfaceSubjectType =
  | "artifact"
  | "stage"
  | "record"
  | "receipt"
  | "gate";
export type EffectPolicy = "auto" | "ask" | "require" | "block";
export type EffectDecisionKind = "approve" | "reject" | "restore" | "cancel";
export type EffectActor = "user" | "policy" | "system";
export type EffectOutcome =
  | "applied"
  | "partial"
  | "failed"
  | "cancelled"
  | "indeterminate"
  | "already_applied"
  | "precondition_drift";
export type EffectExecutorKind =
  | "mcp"
  | "workspace"
  | "browser"
  | "sandbox"
  | "builtin";
export type EffectStageStatus =
  | "staged"
  | "approved"
  | "rejected"
  | "cancelled"
  | "claimed"
  | "applied"
  | "partial"
  | "failed"
  | "indeterminate"
  | "precondition_drift";
export type GateKind = "authentication" | "grant" | "capability" | "policy";
export type GateDecision = "granted" | "denied" | "cancelled";

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

export interface OperationRequestedPayload {
  v: 1;
  operation_id: string;
  producer: Producer;
  capability: string;
  op: string;
  args_digest: string;
  parent_operation_id?: string;
}

export interface OperationClassifiedPayload {
  v: 1;
  operation_id: string;
  effect_class: EffectClass;
  basis: OperationClassificationBasis;
  confidence: number;
}

export interface OperationCompletedPayload {
  v: 1;
  operation_id: string;
  outcome: OperationOutcome;
  result_ref?: string;
  latency_ms?: number;
}

export interface OperationFailedPayload {
  v: 1;
  operation_id: string;
  failure_code: string;
  retryable: boolean;
}

export interface ArtifactCreatedPayload {
  v: 1;
  artifact_id: string;
  kind: ArtifactKind;
  revision: number;
  content_ref: string;
  content_digest: string;
  author: ArtifactAuthor;
}

export interface ArtifactRevisedPayload {
  v: 1;
  artifact_id: string;
  revision: number;
  parent_revision: number;
  content_ref: string;
  content_digest: string;
  author: ArtifactAuthor;
}

export interface ArtifactPromotedPayload {
  v: 1;
  artifact_id: string;
  source_ref: string;
  kind: ArtifactKind;
  revision: number;
}

export interface ArtifactPresentationDecidedPayload {
  v: 1;
  artifact_id: string;
  decision: PresentationDecision;
  basis: string;
  surface_id?: string;
}

export interface EffectStagedPayload {
  v: 1;
  stage_id: string;
  operation_id: string;
  executor: EffectExecutorKind;
  target_ref: string;
  target_digest: string;
  proposal_ref: string;
  proposal_digest: string;
  policy: EffectPolicy;
}

export interface EffectRevisedPayload {
  v: 1;
  stage_id: string;
  revision: number;
  proposal_ref: string;
  proposal_digest: string;
  author: ArtifactAuthor;
}

export interface EffectDecisionRecordedPayload {
  v: 1;
  stage_id: string;
  revision: number;
  decision: EffectDecisionKind;
  actor: EffectActor;
  proposal_digest: string;
  target_digest: string;
}

export interface EffectClaimedPayload {
  v: 1;
  stage_id: string;
  revision: number;
  claim_id: string;
  executor: EffectExecutorKind;
  attempt: number;
}

export interface EffectAppliedPayload {
  v: 1;
  stage_id: string;
  revision: number;
  outcome: EffectOutcome;
  receipt_ref?: string;
  result_digest?: string;
}

export interface EffectIndeterminatePayload {
  v: 1;
  stage_id: string;
  revision: number;
  claim_id: string;
  reason: string;
}

export interface EffectReconciledPayload {
  v: 1;
  stage_id: string;
  revision: number;
  claim_id: string;
  outcome: EffectOutcome;
  receipt_ref?: string;
}

export interface GateOpenedV2Payload {
  v: 1;
  gate_id: string;
  operation_id: string;
  gate_kind: GateKind;
  capability: string;
  reason: string;
}

export interface GateResolvedV2Payload {
  v: 1;
  gate_id: string;
  decision: GateDecision;
  actor: EffectActor;
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
  "operation.requested": OperationRequestedPayload;
  "operation.classified": OperationClassifiedPayload;
  "operation.completed": OperationCompletedPayload;
  "operation.failed": OperationFailedPayload;
  "artifact.created": ArtifactCreatedPayload;
  "artifact.revised": ArtifactRevisedPayload;
  "artifact.promoted": ArtifactPromotedPayload;
  "artifact.presentation_decided": ArtifactPresentationDecidedPayload;
  "effect.staged": EffectStagedPayload;
  "effect.revised": EffectRevisedPayload;
  "effect.decision_recorded": EffectDecisionRecordedPayload;
  "effect.claimed": EffectClaimedPayload;
  "effect.applied": EffectAppliedPayload;
  "effect.indeterminate": EffectIndeterminatePayload;
  "effect.reconciled": EffectReconciledPayload;
  "gate.opened.v2": GateOpenedV2Payload;
  "gate.resolved.v2": GateResolvedV2Payload;
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

export interface ArtifactIntent {
  readonly kind: ArtifactKind;
  readonly title?: string;
  readonly media_type?: string;
  readonly suggested_filename?: string;
  readonly presentation_preference: ArtifactPresentationPreference;
}

export interface OperationRequest {
  readonly operation_id: string;
  readonly run_id: string;
  readonly producer: Producer;
  readonly capability: string;
  readonly op: string;
  readonly canonical_args_ref: string;
  readonly args_digest: string;
  readonly requested_at: string;
  readonly artifact_intent?: ArtifactIntent;
  readonly effect_hint?: EffectClass;
  readonly parent_operation_id?: string;
}

export interface OperationDescriptor {
  readonly capability: string;
  readonly op: string;
  readonly executor: EffectExecutorKind;
  readonly effect_class: EffectClass;
  readonly result_kind: OperationResultKind;
  readonly supports_prepare: boolean;
  readonly supports_reconcile: boolean;
  readonly required_gate_kinds: readonly GateKind[];
  readonly max_inline_result_bytes: number;
}

export interface OperationDisposition {
  readonly operation_id: string;
  readonly outcome: OperationOutcome;
  readonly artifact_ids: readonly string[];
  readonly stage_ids: readonly string[];
  readonly activity_ref?: string;
  readonly agent_summary: string;
  readonly retryable: boolean;
}

export interface Artifact {
  readonly artifact_id: string;
  readonly org_id: string;
  readonly user_id: string;
  readonly conversation_id: string;
  readonly run_id: string;
  readonly kind: ArtifactKind;
  readonly title: string;
  readonly media_type: string;
  readonly current_revision: number;
  readonly created_by: ArtifactAuthor;
  readonly created_at: string;
  readonly updated_at: string;
  readonly deleted_at?: string;
}

export interface ArtifactRevision {
  readonly artifact_id: string;
  readonly revision: number;
  readonly parent_revision?: number;
  readonly content_ref: string;
  readonly content_digest: string;
  readonly byte_size: number;
  readonly author: ArtifactAuthor;
  readonly source_ref?: string;
  readonly created_at: string;
}

export interface SurfaceSubject {
  readonly subject_type: SurfaceSubjectType;
  readonly subject_id: string;
}

export interface EffectTarget {
  readonly executor: EffectExecutorKind;
  readonly capability: string;
  readonly op: string;
  readonly target_ref: string;
  readonly precondition_ref?: string;
  readonly display_label: string;
}

export interface ProposalRef {
  readonly proposal_ref: string;
  readonly proposal_digest: string;
  readonly media_type: string;
  readonly byte_size?: number;
}

export interface EffectStage {
  readonly stage_id: string;
  readonly operation_id: string;
  readonly run_id: string;
  readonly executor: EffectExecutorKind;
  readonly target: EffectTarget;
  readonly proposal: ProposalRef;
  readonly revision: number;
  readonly status: EffectStageStatus;
  readonly policy_snapshot_ref: string;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface EffectDecision {
  readonly stage_id: string;
  readonly revision: number;
  readonly decision: EffectDecisionKind;
  readonly actor: EffectActor;
  readonly proposal_digest: string;
  readonly target_digest: string;
  readonly decided_at: string;
  readonly ledger_id: string;
}

export interface EffectExecutionRequest {
  readonly stage_id: string;
  readonly revision: number;
  readonly idempotency_key: string;
  readonly target_ref: string;
  readonly target_digest: string;
  readonly proposal_ref: string;
  readonly proposal_digest: string;
  readonly actor: EffectActor;
  readonly decision_ledger_id: string;
}

export interface EffectExecutionResult {
  readonly outcome: EffectOutcome;
  readonly receipt_ref?: string;
  readonly result_digest?: string;
  readonly retryable: boolean;
  readonly safe_message?: string;
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
  identifiers: Record<
    string,
    { prefix: string; uuid_versions: readonly number[] }
  >;
  references: {
    max_length: number;
    claim_id_max_length: number;
  };
  digests: {
    max_safe_integer: number;
  };
  enums: Record<string, readonly string[]>;
  events: Record<
    string,
    {
      required: readonly string[];
      optional?: readonly string[];
      enum_fields?: Readonly<Record<string, string>>;
    }
  >;
  compatibility: {
    read_side_only: boolean;
    event_mappings: Readonly<Record<string, string>>;
    legacy_gate_write_input: boolean;
  };
}

const _CONTRACT = contract as unknown as _LedgerContract;

/** True when `x` is one of the contract-defined ledger event-type strings. */
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

/** Strict writer-side payload validation. Unlike the replay-tolerant event
 * guard above, this rejects unknown fields and unknown closed-enum values. */
export function isLedgerPayloadForWrite<K extends LedgerEventType>(
  eventType: K,
  payload: unknown,
): payload is LedgerEventPayloadMap[K] {
  if (!isLedgerEventType(eventType)) return false;
  if (
    typeof payload !== "object" ||
    payload === null ||
    Array.isArray(payload)
  ) {
    return false;
  }
  const value = payload as Record<string, unknown>;
  if (value.v !== 1) return false;
  const schema = _CONTRACT.events[eventType];
  const allowed = new Set([
    ...(schema.required ?? []),
    ...(schema.optional ?? []),
  ]);
  if (schema.required.some((key) => !(key in value))) return false;
  if (Object.keys(value).some((key) => !allowed.has(key))) return false;
  for (const [field, enumName] of Object.entries(schema.enum_fields ?? {})) {
    if (!(field in value)) continue;
    if (!_CONTRACT.enums[enumName]?.includes(value[field] as string))
      return false;
  }
  return _isV21PayloadForWrite(eventType, value);
}

const _SHA256_HEX = /^[0-9a-f]{64}$/;
const _SAFE_CLAIM_ID = /^[a-z0-9][a-z0-9._-]{0,127}$/;

function _isV21PayloadForWrite(
  eventType: LedgerEventType,
  value: Record<string, unknown>,
): boolean {
  const operationId = () =>
    _validCodecValue(OperationIdCodec, value.operation_id);
  const artifactId = () => _validCodecValue(ArtifactIdCodec, value.artifact_id);
  const stageId = () => _validCodecValue(EffectStageIdCodec, value.stage_id);
  const revision = () => _isPositiveSafeInteger(value.revision);

  switch (eventType) {
    case "operation.requested":
      return (
        operationId() &&
        _isNonEmptyString(value.capability) &&
        _isNonEmptyString(value.op) &&
        _isSha256(value.args_digest) &&
        _optionalCodecValue(value, "parent_operation_id", OperationIdCodec) &&
        value.parent_operation_id !== value.operation_id
      );
    case "operation.classified":
      return (
        operationId() &&
        typeof value.confidence === "number" &&
        Number.isFinite(value.confidence) &&
        value.confidence >= 0 &&
        value.confidence <= 1
      );
    case "operation.completed":
      return (
        operationId() &&
        _optionalNonPhysicalReference(value, "result_ref") &&
        _optionalNonNegativeSafeInteger(value, "latency_ms")
      );
    case "operation.failed":
      return (
        operationId() &&
        _isBoundedString(value.failure_code, 1, 128) &&
        typeof value.retryable === "boolean"
      );
    case "artifact.created": {
      if (!artifactId() || !revision() || !_isSha256(value.content_digest))
        return false;
      const parsed = _parseArtifactContentRef(value.content_ref);
      return (
        parsed !== null &&
        parsed.artifact_id === value.artifact_id &&
        parsed.revision === value.revision
      );
    }
    case "artifact.revised": {
      if (
        !artifactId() ||
        !revision() ||
        !_isPositiveSafeInteger(value.parent_revision) ||
        (value.parent_revision as number) >= (value.revision as number) ||
        !_isSha256(value.content_digest)
      ) {
        return false;
      }
      const parsed = _parseArtifactContentRef(value.content_ref);
      return (
        parsed !== null &&
        parsed.artifact_id === value.artifact_id &&
        parsed.revision === value.revision
      );
    }
    case "artifact.promoted":
      return (
        artifactId() && revision() && _isNonPhysicalReference(value.source_ref)
      );
    case "artifact.presentation_decided":
      return (
        artifactId() &&
        _isBoundedString(value.basis, 1, 128) &&
        _optionalNonEmptyString(value, "surface_id")
      );
    case "effect.staged": {
      if (
        !stageId() ||
        !operationId() ||
        !_isTargetReference(value.target_ref) ||
        !_isSha256(value.target_digest) ||
        !_isSha256(value.proposal_digest)
      ) {
        return false;
      }
      if (
        value.executor === "workspace" &&
        !_validCodecValue(WorkspaceTargetRefCodec, value.target_ref)
      ) {
        return false;
      }
      const parsed = _parseProposalRef(value.proposal_ref);
      return (
        parsed !== null &&
        parsed.stage_id === value.stage_id &&
        parsed.revision === 1
      );
    }
    case "effect.revised": {
      if (!stageId() || !revision() || !_isSha256(value.proposal_digest))
        return false;
      const parsed = _parseProposalRef(value.proposal_ref);
      return (
        parsed !== null &&
        parsed.stage_id === value.stage_id &&
        parsed.revision === value.revision
      );
    }
    case "effect.decision_recorded":
      return (
        stageId() &&
        revision() &&
        _isSha256(value.proposal_digest) &&
        _isSha256(value.target_digest)
      );
    case "effect.claimed":
      return (
        stageId() &&
        revision() &&
        _isClaimId(value.claim_id) &&
        _isPositiveSafeInteger(value.attempt)
      );
    case "effect.applied": {
      if (
        !stageId() ||
        !revision() ||
        !_optionalSha256(value, "result_digest")
      ) {
        return false;
      }
      if (!("receipt_ref" in value)) return true;
      const parsed = _parseReceiptRef(value.receipt_ref);
      return parsed !== null && parsed.stage_id === value.stage_id;
    }
    case "effect.indeterminate":
      return (
        stageId() &&
        revision() &&
        _isClaimId(value.claim_id) &&
        _isBoundedString(value.reason, 1, 512)
      );
    case "effect.reconciled": {
      if (!stageId() || !revision() || !_isClaimId(value.claim_id))
        return false;
      if (!("receipt_ref" in value)) return true;
      const parsed = _parseReceiptRef(value.receipt_ref);
      return (
        parsed !== null &&
        parsed.stage_id === value.stage_id &&
        parsed.claim_id === value.claim_id
      );
    }
    case "gate.opened.v2":
      return (
        _isNonEmptyString(value.gate_id) &&
        operationId() &&
        _isNonEmptyString(value.capability) &&
        _isBoundedString(value.reason, 1, 512)
      );
    case "gate.resolved.v2":
      return _isNonEmptyString(value.gate_id);
    default:
      return true;
  }
}

type _StringCodec = {
  parse(text: string): unknown;
};

function _validCodecValue(codec: _StringCodec, value: unknown): boolean {
  if (typeof value !== "string") return false;
  try {
    codec.parse(value);
    return true;
  } catch {
    return false;
  }
}

function _optionalCodecValue(
  value: Record<string, unknown>,
  key: string,
  codec: _StringCodec,
): boolean {
  return !(key in value) || _validCodecValue(codec, value[key]);
}

function _isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function _optionalNonEmptyString(
  value: Record<string, unknown>,
  key: string,
): boolean {
  return !(key in value) || _isNonEmptyString(value[key]);
}

function _isBoundedString(
  value: unknown,
  minLength: number,
  maxLength: number,
): value is string {
  return (
    typeof value === "string" &&
    value.length >= minLength &&
    value.length <= maxLength
  );
}

function _isPositiveSafeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 1;
}

function _isNonNegativeSafeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}

function _optionalNonNegativeSafeInteger(
  value: Record<string, unknown>,
  key: string,
): boolean {
  return !(key in value) || _isNonNegativeSafeInteger(value[key]);
}

function _isSha256(value: unknown): value is string {
  return typeof value === "string" && _SHA256_HEX.test(value);
}

function _optionalSha256(value: Record<string, unknown>, key: string): boolean {
  return !(key in value) || _isSha256(value[key]);
}

function _isClaimId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    _SAFE_CLAIM_ID.test(value) &&
    !value.includes("..")
  );
}

function _isNonPhysicalReference(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > _CONTRACT.references.max_length ||
    value.trim() !== value
  ) {
    return false;
  }
  const lower = value.toLowerCase();
  return !(
    value.startsWith("/") ||
    value.startsWith("~") ||
    value.startsWith("\\") ||
    lower.startsWith("file://") ||
    lower.startsWith("filesystem://") ||
    /^[a-zA-Z]:[\\/]/.test(value)
  );
}

function _isTargetReference(value: unknown): value is string {
  return (
    _isNonPhysicalReference(value) &&
    value.includes("://") &&
    !value.split("/").some((part) => part === "." || part === "..")
  );
}

function _optionalNonPhysicalReference(
  value: Record<string, unknown>,
  key: string,
): boolean {
  return !(key in value) || _isNonPhysicalReference(value[key]);
}

function _parseArtifactContentRef(
  value: unknown,
): ParsedArtifactContentRef | null {
  if (typeof value !== "string") return null;
  try {
    return ArtifactContentRefCodec.parse(value);
  } catch {
    return null;
  }
}

function _parseProposalRef(value: unknown): ParsedProposalRef | null {
  if (typeof value !== "string") return null;
  try {
    return ProposalUriCodec.parse(value);
  } catch {
    return null;
  }
}

function _parseReceiptRef(value: unknown): ParsedEffectReceiptRef | null {
  if (typeof value !== "string") return null;
  try {
    return EffectReceiptRefCodec.parse(value);
  } catch {
    return null;
  }
}

/** Read-side semantic mapping for legacy v2 events. This names the destination
 * concept only; it never rewrites payloads and writers must not use it. */
export function compatibilityEventType(
  eventType: string,
): LedgerEventType | null {
  const mapped = _CONTRACT.compatibility.event_mappings[eventType];
  return mapped !== undefined && isLedgerEventType(mapped) ? mapped : null;
}

export interface LegacyOperationProjection {
  readonly legacy_call_id: string;
  readonly connector: string;
  readonly op: string;
  readonly action_class: string | null;
  readonly classification_basis: string | null;
  readonly completed: boolean;
  readonly latency_ms: number | null;
  readonly result_ref: string | null;
  readonly semantic_event_types: readonly string[];
}

export interface LegacyStageProjection {
  readonly legacy_stage_id: string;
  readonly surface_id: string;
  readonly executor: "mcp";
  readonly target: Readonly<LedgerOpRef>;
  readonly proposal_ref: string;
  readonly latest_revision: number;
  readonly decision_count: number;
  readonly apply_results: readonly string[];
  readonly semantic_event_types: readonly string[];
  readonly authoritative_v21: false;
}

export interface LegacyPresentationProjection {
  readonly event_type: "surface.created" | "view.derived";
  readonly surface_id: string;
}

export interface LegacyGateProjection {
  readonly gate_id: string;
  readonly connector: string;
  readonly opened: boolean;
  readonly resolved: boolean;
  readonly outcome: string | null;
  readonly valid_generalized_write_input: false;
}

export interface LegacyCompatibilityProjection {
  readonly operations: readonly LegacyOperationProjection[];
  readonly stages: readonly LegacyStageProjection[];
  readonly presentation_events: readonly LegacyPresentationProjection[];
  readonly legacy_gates: readonly LegacyGateProjection[];
  readonly passthrough_event_types: readonly string[];
}

interface LegacyReadableEvent {
  readonly event_type: string;
  readonly payload: Readonly<Record<string, unknown>>;
}

/** Read old v2 events without fabricating the ids/digests required by v2.1
 * writers. The output is a compatibility view, never a source of new events. */
export function projectLegacyLedgerForRead(
  events: readonly LegacyReadableEvent[],
): LegacyCompatibilityProjection {
  const operations = new Map<string, Record<string, any>>();
  const stages = new Map<string, Record<string, any>>();
  const presentations: LegacyPresentationProjection[] = [];
  const gates = new Map<string, Record<string, any>>();
  const passthrough = new Set<string>();

  for (const event of events) {
    const payload = event.payload;
    switch (event.event_type) {
      case "action.classified": {
        const callId = String(payload.call_id);
        const operation = _legacyOperation(operations, callId, payload);
        operation.action_class = String(payload.class);
        operation.classification_basis = String(payload.basis);
        _appendCompatibilitySemantic(operation, "operation.classified");
        break;
      }
      case "read.executed": {
        const callId = String(payload.call_id);
        const operation = _legacyOperation(operations, callId, payload);
        operation.completed = true;
        operation.latency_ms = Number(payload.latency_ms);
        operation.result_ref = String(payload.payload_ref);
        _appendCompatibilitySemantic(operation, "operation.completed");
        break;
      }
      case "surface.created":
      case "view.derived":
        presentations.push({
          event_type: event.event_type,
          surface_id: String(payload.surface_id),
        });
        break;
      case "write.staged": {
        const stageId = String(payload.stage_id);
        const target = payload.target as Record<string, unknown>;
        stages.set(stageId, {
          legacy_stage_id: stageId,
          surface_id: String(payload.surface_id),
          executor: "mcp",
          target: {
            connector: String(target.connector),
            op: String(target.op),
          },
          proposal_ref: String(payload.proposal_ref),
          latest_revision: 0,
          decision_count: 0,
          apply_results: [],
          semantic_event_types: ["effect.staged"],
          authoritative_v21: false,
        });
        break;
      }
      case "revision.added": {
        const stage = stages.get(String(payload.stage_id));
        if (stage === undefined)
          throw new Error("legacy revision has no staged write");
        stage.latest_revision = Math.max(
          Number(stage.latest_revision),
          Number(payload.rev),
        );
        _appendCompatibilitySemantic(stage, "effect.revised");
        break;
      }
      case "decision.recorded": {
        const stage = stages.get(String(payload.stage_id));
        if (stage === undefined)
          throw new Error("legacy decision has no staged write");
        stage.decision_count = Number(stage.decision_count) + 1;
        _appendCompatibilitySemantic(stage, "effect.decision_recorded");
        break;
      }
      case "write.applied": {
        const stage = stages.get(String(payload.stage_id));
        if (stage === undefined)
          throw new Error("legacy apply has no staged write");
        (stage.apply_results as string[]).push(String(payload.result));
        _appendCompatibilitySemantic(stage, "effect.applied");
        break;
      }
      case "gate.opened": {
        const gateId = String(payload.gate_id);
        gates.set(gateId, {
          gate_id: gateId,
          connector: String(payload.connector),
          opened: true,
          resolved: false,
          outcome: null,
          valid_generalized_write_input: false,
        });
        break;
      }
      case "gate.resolved": {
        const gateId = String(payload.gate_id);
        const gate = gates.get(gateId) ?? {
          gate_id: gateId,
          connector: "",
          opened: false,
          resolved: false,
          outcome: null,
          valid_generalized_write_input: false,
        };
        gate.resolved = true;
        gate.outcome = String(payload.outcome);
        gates.set(gateId, gate);
        break;
      }
      default:
        passthrough.add(event.event_type);
    }
  }

  return {
    operations: [...operations.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([, operation]) => operation as LegacyOperationProjection),
    stages: [...stages.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([, stage]) => stage as LegacyStageProjection),
    presentation_events: presentations,
    legacy_gates: [...gates.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([, gate]) => gate as LegacyGateProjection),
    passthrough_event_types: [...passthrough].sort(),
  };
}

function _legacyOperation(
  operations: Map<string, Record<string, any>>,
  callId: string,
  payload: Readonly<Record<string, unknown>>,
): Record<string, any> {
  const existing = operations.get(callId);
  if (existing !== undefined) return existing;
  const operation = {
    legacy_call_id: callId,
    connector: String(payload.connector),
    op: String(payload.op),
    action_class: null,
    classification_basis: null,
    completed: false,
    latency_ms: null,
    result_ref: null,
    semantic_event_types: [] as string[],
  };
  operations.set(callId, operation);
  return operation;
}

function _appendCompatibilitySemantic(
  state: Record<string, any>,
  eventType: string,
): void {
  const values = state.semantic_event_types as string[];
  if (!values.includes(eventType)) values.push(eventType);
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
// v2.1 operation/artifact/effect ids, opaque references, and canonical digests
// ---------------------------------------------------------------------------

export class ArtifactEffectFormatError extends Error {
  override readonly name = "ArtifactEffectFormatError";
}

export class CanonicalJsonError extends Error {
  override readonly name = "CanonicalJsonError";
}

type _IdentifierKey = "operation_id" | "artifact_id" | "effect_stage_id";

const _UUID_CANONICAL =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[47][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const _CLAIM_ID = /^[a-z0-9][a-z0-9._-]{0,127}$/;
const _OPAQUE_TOKEN = /^[A-Za-z0-9_-]{1,256}$/;

function _formatPrefixedUuid(key: _IdentifierKey, uuid: string): string {
  _validateBareUuid(key, uuid);
  return `${_CONTRACT.identifiers[key].prefix}${uuid}`;
}

function _parsePrefixedUuid(key: _IdentifierKey, text: string): string {
  const prefix = _CONTRACT.identifiers[key].prefix;
  if (typeof text !== "string" || !text.startsWith(prefix)) {
    throw new ArtifactEffectFormatError(`not a valid ${key}: ${String(text)}`);
  }
  const uuid = text.slice(prefix.length);
  _validateBareUuid(key, uuid);
  return uuid;
}

function _validateBareUuid(key: _IdentifierKey, uuid: string): void {
  if (!_UUID_CANONICAL.test(uuid)) {
    throw new ArtifactEffectFormatError(
      `${key} must contain a canonical lowercase UUID4 or UUID7`,
    );
  }
  const version = Number(uuid[14]);
  if (!_CONTRACT.identifiers[key].uuid_versions.includes(version)) {
    throw new ArtifactEffectFormatError(
      `${key} must contain a canonical lowercase UUID4 or UUID7`,
    );
  }
}

export class OperationIdCodec {
  static format(uuid: string): string {
    return _formatPrefixedUuid("operation_id", uuid);
  }
  static parse(text: string): string {
    return _parsePrefixedUuid("operation_id", text);
  }
}

export class ArtifactIdCodec {
  static format(uuid: string): string {
    return _formatPrefixedUuid("artifact_id", uuid);
  }
  static parse(text: string): string {
    return _parsePrefixedUuid("artifact_id", text);
  }
}

export class EffectStageIdCodec {
  static format(uuid: string): string {
    return _formatPrefixedUuid("effect_stage_id", uuid);
  }
  static parse(text: string): string {
    return _parsePrefixedUuid("effect_stage_id", text);
  }
}

export interface ParsedArtifactContentRef {
  artifact_id: string;
  revision: number;
}

export class ArtifactContentRefCodec {
  static format(artifactId: string, revision: number): string {
    ArtifactIdCodec.parse(artifactId);
    _requirePositiveRevision(revision);
    return `artifact://${artifactId}/revisions/${revision}`;
  }
  static parse(text: string): ParsedArtifactContentRef {
    const match = _referenceMatch(
      /^artifact:\/\/([^/]+)\/revisions\/([1-9][0-9]*)$/,
      text,
      "artifact content reference",
    );
    const artifactId = match[1];
    ArtifactIdCodec.parse(artifactId);
    return { artifact_id: artifactId, revision: _parseRevision(match[2]) };
  }
}

export interface ParsedOperationArgsRef {
  operation_id: string;
}

export class OperationArgsRefCodec {
  static format(operationId: string): string {
    OperationIdCodec.parse(operationId);
    return `operation://${operationId}/args`;
  }
  static parse(text: string): ParsedOperationArgsRef {
    const match = _referenceMatch(
      /^operation:\/\/([^/]+)\/args$/,
      text,
      "operation args reference",
    );
    const operationId = match[1];
    OperationIdCodec.parse(operationId);
    return { operation_id: operationId };
  }
}

export interface ParsedProposalRef {
  stage_id: string;
  revision: number;
}

export class ProposalUriCodec {
  static format(stageId: string, revision: number): string {
    EffectStageIdCodec.parse(stageId);
    _requirePositiveRevision(revision);
    return `proposal://${stageId}/revisions/${revision}`;
  }
  static parse(text: string): ParsedProposalRef {
    const match = _referenceMatch(
      /^proposal:\/\/([^/]+)\/revisions\/([1-9][0-9]*)$/,
      text,
      "proposal reference",
    );
    const stageId = match[1];
    EffectStageIdCodec.parse(stageId);
    return { stage_id: stageId, revision: _parseRevision(match[2]) };
  }
}

export interface ParsedEffectReceiptRef {
  stage_id: string;
  claim_id: string;
}

export class EffectReceiptRefCodec {
  static format(stageId: string, claimId: string): string {
    EffectStageIdCodec.parse(stageId);
    _validateClaimId(claimId);
    return `receipt://effects/${stageId}/${claimId}`;
  }
  static parse(text: string): ParsedEffectReceiptRef {
    const match = _referenceMatch(
      /^receipt:\/\/effects\/([^/]+)\/([^/]+)$/,
      text,
      "effect receipt reference",
    );
    const stageId = match[1];
    const claimId = match[2];
    EffectStageIdCodec.parse(stageId);
    _validateClaimId(claimId);
    return { stage_id: stageId, claim_id: claimId };
  }
}

export interface ParsedWorkspaceTargetRef {
  grant_id: string;
  path_token: string;
}

export class WorkspaceTargetRefCodec {
  static format(grantId: string, pathToken: string): string {
    _validateOpaqueToken(grantId, "grant_id");
    _validateOpaqueToken(pathToken, "path_token");
    return `workspace-target://${grantId}/${pathToken}`;
  }
  static parse(text: string): ParsedWorkspaceTargetRef {
    const match = _referenceMatch(
      /^workspace-target:\/\/([^/]+)\/([^/]+)$/,
      text,
      "workspace target reference",
    );
    const grantId = match[1];
    const pathToken = match[2];
    _validateOpaqueToken(grantId, "grant_id");
    _validateOpaqueToken(pathToken, "path_token");
    return { grant_id: grantId, path_token: pathToken };
  }
}

function _referenceMatch(
  pattern: RegExp,
  text: string,
  label: string,
): RegExpExecArray {
  if (
    typeof text !== "string" ||
    text.length > _CONTRACT.references.max_length ||
    text.trim() !== text
  ) {
    throw new ArtifactEffectFormatError(
      `not a valid ${label}: ${String(text)}`,
    );
  }
  const match = pattern.exec(text);
  if (
    match === null ||
    match.slice(1).some((part) => part === "." || part === "..")
  ) {
    throw new ArtifactEffectFormatError(`not a valid ${label}: ${text}`);
  }
  return match;
}

function _requirePositiveRevision(revision: number): void {
  if (!Number.isSafeInteger(revision) || revision < 1) {
    throw new ArtifactEffectFormatError(
      "revision must be a positive safe integer",
    );
  }
}

function _parseRevision(text: string): number {
  const revision = Number(text);
  _requirePositiveRevision(revision);
  return revision;
}

function _validateClaimId(claimId: string): void {
  if (
    typeof claimId !== "string" ||
    claimId.length > _CONTRACT.references.claim_id_max_length ||
    !_CLAIM_ID.test(claimId) ||
    claimId.includes("..")
  ) {
    throw new ArtifactEffectFormatError("claim_id must be a safe opaque token");
  }
}

function _validateOpaqueToken(value: string, fieldName: string): void {
  if (typeof value !== "string" || !_OPAQUE_TOKEN.test(value)) {
    throw new ArtifactEffectFormatError(
      `${fieldName} must be a safe opaque token`,
    );
  }
}

export function canonicalJson(value: unknown): string {
  return _renderCanonical(value, new Set<object>(), "$");
}

export function canonicalJsonBytes(value: unknown): Uint8Array {
  return new TextEncoder().encode(canonicalJson(value));
}

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  if (!(bytes instanceof Uint8Array)) {
    throw new TypeError("sha256Hex accepts Uint8Array input only");
  }
  const input = new Uint8Array(bytes).buffer;
  const digest = await globalThis.crypto.subtle.digest("SHA-256", input);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export async function canonicalJsonSha256(value: unknown): Promise<string> {
  return sha256Hex(canonicalJsonBytes(value));
}

function _renderCanonical(
  value: unknown,
  active: Set<object>,
  path: string,
): string {
  if (value === null) return "null";
  if (value === true) return "true";
  if (value === false) return "false";
  if (typeof value === "string") {
    _rejectUnpairedSurrogates(value, path);
    return JSON.stringify(value) as string;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new CanonicalJsonError(`${path} must be a finite JSON number`);
    }
    if (Number.isInteger(value) && !Number.isSafeInteger(value)) {
      throw new CanonicalJsonError(
        `${path} integer exceeds the cross-language safe range`,
      );
    }
    return JSON.stringify(Object.is(value, -0) ? 0 : value) as string;
  }
  if (Array.isArray(value)) {
    if (active.has(value))
      throw new CanonicalJsonError(`${path} contains a cycle`);
    const ownKeys = Reflect.ownKeys(value);
    const indexKeys = Array.from({ length: value.length }, (_, index) =>
      String(index),
    );
    const allowed = new Set(["length", ...indexKeys]);
    if (
      indexKeys.some((_, index) => !(index in value)) ||
      ownKeys.some((key) => typeof key !== "string" || !allowed.has(key))
    ) {
      throw new CanonicalJsonError(
        `${path} arrays must be dense and have no custom properties`,
      );
    }
    active.add(value);
    try {
      return `[${value
        .map((item, index) =>
          _renderCanonical(item, active, `${path}[${index}]`),
        )
        .join(",")}]`;
    } finally {
      active.delete(value);
    }
  }
  if (typeof value === "object") {
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) {
      throw new CanonicalJsonError(`${path} must be a plain JSON object`);
    }
    if (active.has(value))
      throw new CanonicalJsonError(`${path} contains a cycle`);
    const keys = Reflect.ownKeys(value);
    if (keys.some((key) => typeof key !== "string")) {
      throw new CanonicalJsonError(`${path} object keys must be strings`);
    }
    const stringKeys = keys as string[];
    for (const key of stringKeys) {
      _rejectUnpairedSurrogates(key, `${path}.<key>`);
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (
        descriptor === undefined ||
        !descriptor.enumerable ||
        !("value" in descriptor)
      ) {
        throw new CanonicalJsonError(
          `${path}.${key} must be an enumerable data property`,
        );
      }
    }
    stringKeys.sort(_compareCodePoints);
    active.add(value);
    try {
      const record = value as Record<string, unknown>;
      return `{${stringKeys
        .map(
          (key) =>
            `${JSON.stringify(key) as string}:${_renderCanonical(
              record[key],
              active,
              `${path}.${key}`,
            )}`,
        )
        .join(",")}}`;
    } finally {
      active.delete(value);
    }
  }
  throw new CanonicalJsonError(
    `${path} contains unsupported value type ${typeof value}`,
  );
}

function _rejectUnpairedSurrogates(value: string, path: string): void {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) {
        throw new CanonicalJsonError(
          `${path} contains an unpaired Unicode surrogate`,
        );
      }
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      throw new CanonicalJsonError(
        `${path} contains an unpaired Unicode surrogate`,
      );
    }
  }
}

function _compareCodePoints(left: string, right: string): number {
  const a = Array.from(left, (char) => char.codePointAt(0) ?? 0);
  const b = Array.from(right, (char) => char.codePointAt(0) ?? 0);
  const length = Math.min(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    if (a[index] !== b[index]) return a[index] - b[index];
  }
  return a.length - b.length;
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
