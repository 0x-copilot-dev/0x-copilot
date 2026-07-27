// projectReceiptV2 — additive, pure PRD-E1 D4 accountability projection.
//
// This intentionally does not replace `projectReceipt`: the latter remains the
// existing receipt-surface fold and live `receipt.emitted` gate.  Receipt v2 is
// a safe read model over the same event array. It fetches nothing, opens
// nothing, and never dereferences payload refs.

import {
  ARTIFACT_EVENT_TYPES,
  compatibilityEventType,
  EFFECT_EVENT_TYPES,
  formatLedgerId,
  GATE_V2_EVENT_TYPES,
  LEDGER_EVENT_TYPES,
  OPERATION_EVENT_TYPES,
  type LedgerEventType,
  type ReceiptRunStatusV2,
  type ReceiptUsageReferenceV2,
  type ReceiptUsageTotalV2,
  type RunReceiptV2,
  type UsagePurpose,
} from "@0x-copilot/api-types";

export interface ReceiptV2EventLike {
  readonly event_type: unknown;
  readonly sequence_no: unknown;
  readonly created_at?: unknown;
  readonly payload: unknown;
}

/** A pure availability decision for hosts; it never requests a canvas open. */
export interface ReceiptV2Projection {
  readonly receipt: RunReceiptV2 | null;
  readonly available: boolean;
  readonly chatOnly: boolean;
  readonly shouldAutoOpen: false;
}

interface OrderedEvent {
  readonly sequenceNo: number;
  readonly index: number;
  readonly createdAt: string;
  readonly eventType: LedgerEventType | null;
  readonly payload: Record<string, unknown> | null;
}

interface EffectStage {
  proposed: boolean;
  status: EffectStatus;
  scope: EffectScope;
}

interface UsageAccumulator {
  records: number;
  tokensIn: number;
  tokensOut: number;
}

type EffectScope = "external" | "internal" | "unclassified";
type EffectStatus =
  | "unknown"
  | "proposed"
  | "approved"
  | "rejected"
  | "claimed"
  | "held"
  | "applied"
  | "partial"
  | "failed"
  | "cancelled"
  | "indeterminate"
  | "precondition_drift";

const EVENT = {
  gateOpened: LEDGER_EVENT_TYPES[0],
  gateResolved: LEDGER_EVENT_TYPES[1],
  actionClassified: LEDGER_EVENT_TYPES[2],
  readExecuted: LEDGER_EVENT_TYPES[3],
  writeStaged: LEDGER_EVENT_TYPES[9],
  decisionRecorded: LEDGER_EVENT_TYPES[11],
  writeApplied: LEDGER_EVENT_TYPES[12],
  usageRecorded: LEDGER_EVENT_TYPES[13],
  operationRequested: OPERATION_EVENT_TYPES[0],
  operationClassified: OPERATION_EVENT_TYPES[1],
  operationCompleted: OPERATION_EVENT_TYPES[2],
  operationFailed: OPERATION_EVENT_TYPES[3],
  artifactCreated: ARTIFACT_EVENT_TYPES[0],
  artifactRevised: ARTIFACT_EVENT_TYPES[1],
  artifactPromoted: ARTIFACT_EVENT_TYPES[2],
  effectStaged: EFFECT_EVENT_TYPES[0],
  effectDecisionRecorded: EFFECT_EVENT_TYPES[3],
  effectClaimed: EFFECT_EVENT_TYPES[4],
  effectApplied: EFFECT_EVENT_TYPES[5],
  effectIndeterminate: EFFECT_EVENT_TYPES[6],
  effectReconciled: EFFECT_EVENT_TYPES[7],
  gateOpenedV2: GATE_V2_EVENT_TYPES[0],
  gateResolvedV2: GATE_V2_EVENT_TYPES[1],
} as const;

const FOLD_PREFIX = "ledger://";
const FOLD_SEPARATOR = "@";
const MAX_SAFE_INTEGER = Number.MAX_SAFE_INTEGER;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;
const TIMESTAMP =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const OPAQUE_REF =
  /^[A-Za-z][A-Za-z0-9+.-]*:\/\/[A-Za-z0-9._~-]+(?:\/[A-Za-z0-9._~-]+)*$/;
const SENSITIVE_TEXT =
  /(?:authorization|proxy-authorization|cookie|set-cookie|api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|token|secret|password|credential|private[_-]?key|client[_-]?secret|session)\s*[:=]|\bbearer\s+|(?:^|[^A-Za-z0-9_-])(?:sk-[A-Za-z0-9_-]+|xox[baprs]-[A-Za-z0-9-]+|gh[pousr]_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|AIza[A-Za-z0-9_-]{20,})(?![A-Za-z0-9_-])/i;

const OPERATION_OUTCOMES = new Set([
  "succeeded",
  "staged",
  "blocked",
  "cancelled",
  "failed",
]);
const EFFECT_OUTCOMES = new Set([
  "applied",
  "partial",
  "failed",
  "cancelled",
  "indeterminate",
  "already_applied",
  "precondition_drift",
]);
const EFFECT_DECISIONS = new Set([
  "approve",
  "reject",
  "restore",
  "cancel",
  "hold",
]);
const EFFECT_CLASSES = new Set([
  "none",
  "internal_reversible",
  "external_reversible",
  "external_destructive",
  "unknown",
]);
const USAGE_PURPOSES = new Set<UsagePurpose>([
  "run",
  "subagent",
  "view_shaping",
  "shape_request",
]);
const RUN_STATUSES = new Set<ReceiptRunStatusV2>([
  "unknown",
  "queued",
  "running",
  "waiting_for_approval",
  "cancelling",
  "cancelled",
  "completed",
  "failed",
  "timed_out",
  "blocked",
  "indeterminate",
]);
const TERMINAL_STATUSES = new Set<ReceiptRunStatusV2>([
  "cancelled",
  "completed",
  "failed",
  "timed_out",
  "blocked",
  "indeterminate",
]);
const PENDING_EFFECT_STATUSES = new Set<EffectStatus>([
  "proposed",
  "approved",
  "claimed",
  "held",
]);

const REQUIRED_FIELDS: Readonly<
  Partial<Record<LedgerEventType, readonly string[]>>
> = {
  [EVENT.operationRequested]: [
    "operation_id",
    "producer",
    "capability",
    "op",
    "args_digest",
  ],
  [EVENT.operationClassified]: [
    "operation_id",
    "effect_class",
    "basis",
    "confidence",
  ],
  [EVENT.operationCompleted]: ["operation_id", "outcome"],
  [EVENT.operationFailed]: ["operation_id", "failure_code", "retryable"],
  [EVENT.readExecuted]: [
    "call_id",
    "connector",
    "op",
    "latency_ms",
    "payload_ref",
  ],
  [EVENT.writeStaged]: ["stage_id", "surface_id", "target", "proposal_ref"],
  [EVENT.decisionRecorded]: ["stage_id", "decision", "scope", "actor"],
  [EVENT.writeApplied]: ["stage_id", "rev", "result"],
  [EVENT.usageRecorded]: ["purpose", "model", "tokens_in", "tokens_out"],
  [EVENT.artifactCreated]: [
    "artifact_id",
    "kind",
    "revision",
    "content_ref",
    "content_digest",
    "author",
  ],
  [EVENT.artifactRevised]: [
    "artifact_id",
    "revision",
    "parent_revision",
    "content_ref",
    "content_digest",
    "author",
  ],
  [EVENT.artifactPromoted]: ["artifact_id", "source_ref", "kind", "revision"],
  [EVENT.effectStaged]: [
    "stage_id",
    "operation_id",
    "executor",
    "target_ref",
    "target_digest",
    "proposal_ref",
    "proposal_digest",
    "policy",
  ],
  [EVENT.effectDecisionRecorded]: [
    "stage_id",
    "revision",
    "decision",
    "actor",
    "proposal_digest",
    "target_digest",
  ],
  [EVENT.effectClaimed]: [
    "stage_id",
    "revision",
    "claim_id",
    "executor",
    "attempt",
  ],
  [EVENT.effectApplied]: ["stage_id", "revision", "outcome"],
  [EVENT.effectIndeterminate]: ["stage_id", "revision", "claim_id", "reason"],
  [EVENT.effectReconciled]: ["stage_id", "revision", "claim_id", "outcome"],
  [EVENT.gateOpened]: [
    "gate_id",
    "connector",
    "purpose",
    "scopes",
    "auth_state",
  ],
  [EVENT.gateResolved]: ["gate_id", "outcome"],
  [EVENT.gateOpenedV2]: [
    "gate_id",
    "operation_id",
    "gate_kind",
    "capability",
    "reason",
  ],
  [EVENT.gateResolvedV2]: ["gate_id", "decision", "actor"],
};

/**
 * Fold canonical v2.1 and contract-defined compatibility events into the
 * additive Receipt v2 shape. It is deterministic and total for any event
 * prefix; it returns counters and synthesized ledger ids only.
 */
export function foldReceiptV2(
  runId: string,
  events: readonly ReceiptV2EventLike[],
  runStatus?: unknown,
): RunReceiptV2 {
  const warnings = new Map<string, number>();
  const ordered = orderedEvents(events, warnings);
  const status = receiptStatus(runStatus, warnings);
  const operationClasses = new Map<string, string>();
  const stages = new Map<string, EffectStage>();
  const usage = new Map<UsagePurpose, UsageAccumulator>();
  const references: ReceiptUsageReferenceV2[] = [];
  const openGates = new Set<string>();

  let requested = 0;
  let completed = 0;
  let failed = 0;
  let blocked = 0;
  let created = 0;
  let revised = 0;
  let promoted = 0;
  let readsCompleted = 0;
  let proposed = 0;
  let approved = 0;
  let rejected = 0;
  let applied = 0;
  let partial = 0;
  let indeterminate = 0;
  let external = 0;
  let internal = 0;
  let unclassified = 0;
  let gatesOpened = 0;
  let gatesResolved = 0;
  let throughSequence = 0;
  let generatedAt = "";

  for (const event of ordered) {
    if (event.sequenceNo >= throughSequence) {
      throughSequence = event.sequenceNo;
      generatedAt = event.createdAt;
    }
    if (event.eventType === null || event.payload === null) continue;
    if (!validPayload(event.eventType, event.payload)) {
      warn(warnings, "malformed_events");
      continue;
    }

    const { eventType, payload } = event;
    if (eventType === EVENT.operationRequested) {
      requested += 1;
    } else if (eventType === EVENT.operationClassified) {
      const operationId = identifier(payload.operation_id);
      const effectClass = enumValue(payload.effect_class, EFFECT_CLASSES);
      if (operationId === null || effectClass === null) {
        warn(warnings, "malformed_events");
      } else {
        operationClasses.set(operationId, effectClass);
      }
    } else if (eventType === EVENT.operationCompleted) {
      const outcome = enumValue(payload.outcome, OPERATION_OUTCOMES);
      if (outcome === null) {
        warn(warnings, "malformed_events");
      } else {
        completed += 1;
        if (outcome === "failed") failed += 1;
        if (outcome === "blocked") blocked += 1;
      }
    } else if (eventType === EVENT.operationFailed) {
      failed += 1;
    } else if (eventType === EVENT.readExecuted) {
      if (compatibilityEventType(eventType) === EVENT.operationCompleted)
        completed += 1;
      readsCompleted += 1;
    } else if (eventType === EVENT.artifactCreated) {
      created += 1;
    } else if (eventType === EVENT.artifactRevised) {
      revised += 1;
    } else if (eventType === EVENT.artifactPromoted) {
      promoted += 1;
    } else if (eventType === EVENT.effectStaged) {
      const stageId = identifier(payload.stage_id);
      if (stageId === null) {
        warn(warnings, "malformed_events");
        continue;
      }
      const scope = canonicalEffectScope(payload, operationClasses);
      const stage = stageFor(stageId, stages);
      stage.proposed = true;
      stage.status = "proposed";
      stage.scope = scope;
      proposed += 1;
      if (scope === "external") external += 1;
      else if (scope === "internal") internal += 1;
      else unclassified += 1;
    } else if (eventType === EVENT.writeStaged) {
      const stageId = identifier(payload.stage_id);
      if (stageId === null) {
        warn(warnings, "malformed_events");
        continue;
      }
      if (compatibilityEventType(eventType) === EVENT.effectStaged) {
        const stage = stageFor(stageId, stages);
        stage.proposed = true;
        stage.status = "proposed";
        stage.scope = "external";
        proposed += 1;
        external += 1;
      }
    } else if (eventType === EVENT.effectDecisionRecorded) {
      const stage = stageFromPayload(payload, stages, warnings);
      const decision = enumValue(payload.decision, EFFECT_DECISIONS);
      if (stage === null || decision === null) {
        warn(warnings, "malformed_events");
        continue;
      }
      ({ approved, rejected } = noteDecision(
        stage,
        decision,
        approved,
        rejected,
      ));
    } else if (eventType === EVENT.decisionRecorded) {
      const stage = stageFromPayload(payload, stages, warnings);
      const decision = enumValue(payload.decision, EFFECT_DECISIONS);
      if (stage === null || decision === null) {
        warn(warnings, "malformed_events");
        continue;
      }
      if (compatibilityEventType(eventType) === EVENT.effectDecisionRecorded) {
        ({ approved, rejected } = noteDecision(
          stage,
          decision,
          approved,
          rejected,
        ));
      }
    } else if (eventType === EVENT.effectClaimed) {
      const stage = stageFromPayload(payload, stages, warnings);
      if (stage === null) warn(warnings, "malformed_events");
      else stage.status = "claimed";
    } else if (
      eventType === EVENT.effectApplied ||
      eventType === EVENT.effectReconciled
    ) {
      const stage = stageFromPayload(payload, stages, warnings);
      const outcome = enumValue(payload.outcome, EFFECT_OUTCOMES);
      if (stage === null || outcome === null) {
        warn(warnings, "malformed_events");
        continue;
      }
      ({ applied, partial, indeterminate } = noteEffectOutcome(
        stage,
        outcome,
        applied,
        partial,
        indeterminate,
      ));
    } else if (eventType === EVENT.writeApplied) {
      const stage = stageFromPayload(payload, stages, warnings);
      const result = enumValue(
        payload.result,
        new Set(["applied", "partial", "failed"]),
      );
      if (stage === null || result === null) {
        warn(warnings, "malformed_events");
        continue;
      }
      if (compatibilityEventType(eventType) === EVENT.effectApplied) {
        if (result === "applied") {
          applied += 1;
          stage.status = "applied";
        } else if (result === "partial") {
          partial += 1;
          stage.status = "partial";
        } else {
          stage.status = "failed";
        }
      }
    } else if (eventType === EVENT.effectIndeterminate) {
      const stage = stageFromPayload(payload, stages, warnings);
      if (stage === null) {
        warn(warnings, "malformed_events");
      } else {
        indeterminate += 1;
        stage.status = "indeterminate";
      }
    } else if (
      eventType === EVENT.gateOpened ||
      eventType === EVENT.gateOpenedV2
    ) {
      const gateId = identifier(payload.gate_id);
      if (gateId === null) {
        warn(warnings, "malformed_events");
        continue;
      }
      gatesOpened += 1;
      openGates.add(gateId);
    } else if (
      eventType === EVENT.gateResolved ||
      eventType === EVENT.gateResolvedV2
    ) {
      const gateId = identifier(payload.gate_id);
      if (gateId === null) {
        warn(warnings, "malformed_events");
        continue;
      }
      gatesResolved += 1;
      if (!openGates.has(gateId)) warn(warnings, "gate_resolved_without_open");
      openGates.delete(gateId);
    } else if (eventType === EVENT.usageRecorded) {
      noteUsage(payload, runId, event.sequenceNo, usage, references, warnings);
    }
  }

  const held = [...stages.values()].filter(
    (stage) => stage.proposed && PENDING_EFFECT_STATUSES.has(stage.status),
  ).length;
  const unresolvedIndeterminate = [...stages.values()].filter(
    (stage) => stage.proposed && stage.status === "indeterminate",
  ).length;
  const missingProposals = [...stages.values()].filter(
    (stage) => !stage.proposed && stage.status !== "unknown",
  ).length;
  if (held > 0) warn(warnings, "effects_held", held);
  if (unresolvedIndeterminate > 0)
    warn(warnings, "effects_indeterminate", unresolvedIndeterminate);
  if (unclassified > 0) warn(warnings, "effects_unclassified", unclassified);
  if (missingProposals > 0)
    warn(warnings, "effects_missing_proposal", missingProposals);
  if (openGates.size > 0) warn(warnings, "gates_pending", openGates.size);
  if (blocked > 0) warn(warnings, "operations_blocked", blocked);

  return {
    run_id: runId,
    status,
    generated_at: generatedAt,
    fold_ref: `${FOLD_PREFIX}${runId}${FOLD_SEPARATOR}${throughSequence}`,
    operations: { requested, completed, failed, blocked },
    artifacts: { created, revised, promoted },
    reads: { completed: readsCompleted },
    effects: {
      proposed,
      approved,
      rejected,
      applied,
      partial,
      held,
      indeterminate,
      external,
      internal,
      unclassified,
    },
    gates: {
      opened: gatesOpened,
      resolved: gatesResolved,
      pending: openGates.size,
    },
    usage: {
      totals_by_purpose: [...usage.entries()]
        .sort(([left], [right]) => left.localeCompare(right))
        .map<ReceiptUsageTotalV2>(([purpose, totals]) => ({
          purpose,
          records: totals.records,
          tokens_in: totals.tokensIn,
          tokens_out: totals.tokensOut,
        })),
      references,
    },
    unresolved_warnings: [...warnings.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([code, count]) => ({ code, count })),
  };
}

/**
 * Return an optional receipt for a host to display. A zero-operation chat run
 * is available only once its caller supplies a terminal run status; the host
 * gets an explicit permanent `shouldAutoOpen: false` either way.
 */
export function projectReceiptV2(
  runId: string,
  events: readonly ReceiptV2EventLike[],
  runStatus?: unknown,
): ReceiptV2Projection {
  const folded = foldReceiptV2(runId, events, runStatus);
  const chatOnly = isChatOnlyReceiptV2(folded);
  const available = !chatOnly || TERMINAL_STATUSES.has(folded.status);
  return {
    receipt: available ? folded : null,
    available,
    chatOnly,
    shouldAutoOpen: false,
  };
}

/** A zero-operation/read/effect/artifact/gate/usage receipt is chat-only. */
export function isChatOnlyReceiptV2(receipt: RunReceiptV2): boolean {
  const { operations, artifacts, reads, effects, gates, usage } = receipt;
  return (
    operations.requested === 0 &&
    operations.completed === 0 &&
    operations.failed === 0 &&
    operations.blocked === 0 &&
    artifacts.created === 0 &&
    artifacts.revised === 0 &&
    artifacts.promoted === 0 &&
    reads.completed === 0 &&
    effects.proposed === 0 &&
    effects.approved === 0 &&
    effects.rejected === 0 &&
    effects.applied === 0 &&
    effects.partial === 0 &&
    effects.indeterminate === 0 &&
    gates.opened === 0 &&
    gates.resolved === 0 &&
    usage.references.length === 0
  );
}

function orderedEvents(
  events: readonly ReceiptV2EventLike[],
  warnings: Map<string, number>,
): OrderedEvent[] {
  const ordered: OrderedEvent[] = [];
  events.forEach((event, index) => {
    const sequenceNo = positiveInt(event.sequence_no);
    if (sequenceNo === null) {
      warn(warnings, "malformed_events");
      return;
    }
    const eventType = knownEventType(event.event_type);
    ordered.push({
      sequenceNo,
      index,
      createdAt: timestamp(event.created_at),
      eventType,
      payload: asRecord(event.payload),
    });
  });
  return ordered.sort(
    (left, right) =>
      left.sequenceNo - right.sequenceNo || left.index - right.index,
  );
}

function validPayload(
  eventType: LedgerEventType,
  payload: Record<string, unknown>,
): boolean {
  if (payload.v !== 1) return false;
  const required = REQUIRED_FIELDS[eventType] ?? [];
  if (!required.every((key) => Object.hasOwn(payload, key))) return false;
  if (eventType === EVENT.effectStaged) {
    return (
      safeOpaqueRef(payload.target_ref) && safeOpaqueRef(payload.proposal_ref)
    );
  }
  if (eventType === EVENT.writeStaged) {
    // The compatibility contract deliberately preserves old proposal refs as
    // unread opaque data; do not reinterpret or dereference their legacy shape.
    return asRecord(payload.target) !== null;
  }
  if (
    eventType === EVENT.artifactCreated ||
    eventType === EVENT.artifactRevised
  ) {
    return safeOpaqueRef(payload.content_ref);
  }
  if (eventType === EVENT.artifactPromoted)
    return safeOpaqueRef(payload.source_ref);
  return true;
}

function canonicalEffectScope(
  payload: Record<string, unknown>,
  operationClasses: ReadonlyMap<string, string>,
): EffectScope {
  const operationId = identifier(payload.operation_id);
  const effectClass =
    enumValue(payload.effect_class, EFFECT_CLASSES) ??
    (operationId === null ? null : (operationClasses.get(operationId) ?? null));
  if (
    effectClass === "external_reversible" ||
    effectClass === "external_destructive"
  )
    return "external";
  if (effectClass === "internal_reversible") return "internal";
  return "unclassified";
}

function stageFor(
  stageId: string,
  stages: Map<string, EffectStage>,
): EffectStage {
  const existing = stages.get(stageId);
  if (existing !== undefined) return existing;
  const stage: EffectStage = {
    proposed: false,
    status: "unknown",
    scope: "unclassified",
  };
  stages.set(stageId, stage);
  return stage;
}

function stageFromPayload(
  payload: Record<string, unknown>,
  stages: Map<string, EffectStage>,
  warnings: Map<string, number>,
): EffectStage | null {
  const stageId = identifier(payload.stage_id);
  if (stageId === null) return null;
  const existing = stages.get(stageId);
  if (existing !== undefined) return existing;
  return stageFor(stageId, stages);
}

function noteDecision(
  stage: EffectStage,
  decision: string,
  approved: number,
  rejected: number,
): { approved: number; rejected: number } {
  if (decision === "approve") {
    stage.status = "approved";
    return { approved: approved + 1, rejected };
  }
  if (decision === "reject") {
    stage.status = "rejected";
    return { approved, rejected: rejected + 1 };
  }
  if (decision === "restore") stage.status = "proposed";
  else if (decision === "cancel") stage.status = "cancelled";
  else stage.status = "held";
  return { approved, rejected };
}

function noteEffectOutcome(
  stage: EffectStage,
  outcome: string,
  applied: number,
  partial: number,
  indeterminate: number,
): { applied: number; partial: number; indeterminate: number } {
  if (outcome === "applied" || outcome === "already_applied") {
    stage.status = "applied";
    return { applied: applied + 1, partial, indeterminate };
  }
  if (outcome === "partial") {
    stage.status = "partial";
    return { applied, partial: partial + 1, indeterminate };
  }
  if (outcome === "indeterminate") {
    stage.status = "indeterminate";
    return { applied, partial, indeterminate: indeterminate + 1 };
  }
  stage.status = outcome as EffectStatus;
  return { applied, partial, indeterminate };
}

function noteUsage(
  payload: Record<string, unknown>,
  runId: string,
  sequenceNo: number,
  usage: Map<UsagePurpose, UsageAccumulator>,
  references: ReceiptUsageReferenceV2[],
  warnings: Map<string, number>,
): void {
  const purpose = enumValue(
    payload.purpose,
    USAGE_PURPOSES,
  ) as UsagePurpose | null;
  const tokensIn = nonnegativeInt(payload.tokens_in);
  const tokensOut = nonnegativeInt(payload.tokens_out);
  if (purpose === null || tokensIn === null || tokensOut === null) {
    warn(warnings, "malformed_events");
    return;
  }
  const totals = usage.get(purpose) ?? {
    records: 0,
    tokensIn: 0,
    tokensOut: 0,
  };
  totals.records += 1;
  totals.tokensIn += tokensIn;
  totals.tokensOut += tokensOut;
  usage.set(purpose, totals);
  try {
    references.push({ ledger_id: formatLedgerId(runId, sequenceNo), purpose });
  } catch {
    warn(warnings, "usage_reference_unavailable");
  }
}

function receiptStatus(
  value: unknown,
  warnings: Map<string, number>,
): ReceiptRunStatusV2 {
  if (value === undefined || value === null) return "unknown";
  if (
    typeof value === "string" &&
    RUN_STATUSES.has(value as ReceiptRunStatusV2)
  )
    return value as ReceiptRunStatusV2;
  warn(warnings, "run_status_unavailable");
  return "unknown";
}

function knownEventType(value: unknown): LedgerEventType | null {
  return typeof value === "string" &&
    (LEDGER_EVENT_TYPES as readonly string[]).includes(value)
    ? (value as LedgerEventType)
    : null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function positiveInt(value: unknown): number | null {
  return typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 1 &&
    value <= MAX_SAFE_INTEGER
    ? value
    : null;
}

function nonnegativeInt(value: unknown): number | null {
  return typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0 &&
    value <= MAX_SAFE_INTEGER
    ? value
    : null;
}

function identifier(value: unknown): string | null {
  return typeof value === "string" && IDENTIFIER.test(value) ? value : null;
}

function timestamp(value: unknown): string {
  return typeof value === "string" && TIMESTAMP.test(value) ? value : "";
}

function enumValue(
  value: unknown,
  allowed: ReadonlySet<string>,
): string | null {
  return typeof value === "string" && allowed.has(value) ? value : null;
}

function safeOpaqueRef(value: unknown): boolean {
  return (
    typeof value === "string" &&
    value.length <= 2048 &&
    !SENSITIVE_TEXT.test(value) &&
    OPAQUE_REF.test(value)
  );
}

function warn(warnings: Map<string, number>, code: string, count = 1): void {
  warnings.set(code, (warnings.get(code) ?? 0) + count);
}
