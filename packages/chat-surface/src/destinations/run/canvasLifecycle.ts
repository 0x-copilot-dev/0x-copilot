// Pure Canvas lifecycle projection (PRD-B3).
//
// This is the client twin of `agent_runtime.presentation.lifecycle`. It folds
// the persisted SSE/replay envelopes directly, rather than carrying a second
// React lifecycle flag. Tabs identify subjects, never untrusted titles.

import {
  ARTIFACT_EVENT_TYPES,
  EFFECT_EVENT_TYPES,
  GATE_V2_EVENT_TYPES,
  OPERATION_EVENT_TYPES,
  type RuntimeEventEnvelope,
} from "@0x-copilot/api-types";

export type CanvasLifecycleState =
  | "assembling"
  | "presenting"
  | "chat_only"
  | "parked"
  | "failed"
  | "complete_empty";

export type CanvasSubjectKind = "artifact" | "surface" | "effect" | "receipt";

export interface CanvasLifecycleSubject {
  readonly key: string;
  readonly kind: CanvasSubjectKind;
  readonly subjectId: string;
  readonly title: string;
  readonly revision: number | null;
  readonly lastSeq: number;
  readonly priority: number;
  readonly rendererHint: string | null;
}

export interface CanvasLifecycleProjection {
  readonly lifecycle: CanvasLifecycleState;
  readonly tabs: readonly CanvasLifecycleSubject[];
  readonly activeSubjectKey: string | null;
  readonly pendingSubjectKeys: readonly string[];
  readonly terminalReceipt: CanvasLifecycleSubject | null;
  readonly activityCount: number;
  readonly failure: string | null;
  readonly hasFinalResponse: boolean;
  readonly terminal: boolean;
}

interface SubjectAccumulator {
  key: string;
  kind: CanvasSubjectKind;
  subjectId: string;
  title: string;
  revision: number | null;
  lastSeq: number;
  priority: number;
  rendererHint: string | null;
}

const [
  OPERATION_REQUESTED,
  OPERATION_CLASSIFIED,
  OPERATION_COMPLETED,
  OPERATION_FAILED,
] = OPERATION_EVENT_TYPES;
const [ARTIFACT_CREATED, ARTIFACT_REVISED, , ARTIFACT_PRESENTATION_DECIDED] =
  ARTIFACT_EVENT_TYPES;
const [
  EFFECT_STAGED,
  ,
  EFFECT_REVISED,
  EFFECT_DECISION_RECORDED,
  ,
  EFFECT_APPLIED,
  EFFECT_INDETERMINATE,
  EFFECT_RECONCILED,
] = EFFECT_EVENT_TYPES;
const [GATE_OPENED_V2, GATE_RESOLVED_V2] = GATE_V2_EVENT_TYPES;

const ACTIVITY_EVENTS = new Set<string>([
  OPERATION_REQUESTED,
  OPERATION_CLASSIFIED,
  OPERATION_COMPLETED,
  OPERATION_FAILED,
  "read.executed",
  "tool_result",
  "tool_call_started",
]);

/**
 * Fold real RuntimeEventEnvelope shapes into the whole canvas lifecycle.
 * Artifact creation is intentionally only a candidate: an explicit
 * `artifact.presentation_decided {decision: canvas}` is required to create a
 * tab, so a durable hidden/chat-card artifact never leaks into Studio.
 */
export function projectCanvasLifecycle(
  events: readonly RuntimeEventEnvelope[],
): CanvasLifecycleProjection {
  const subjects = new Map<string, SubjectAccumulator>();
  const artifactKeys = new Map<string, string>();
  const artifactCandidates = new Map<string, SubjectAccumulator>();
  const artifactDecisions = new Map<string, string>();
  const surfaceKeys = new Map<string, string>();
  const effectKeys = new Map<string, string>();
  const pendingStages = new Set<string>();
  const openGates = new Set<string>();
  let terminalReceipt: CanvasLifecycleSubject | null = null;
  let activityCount = 0;
  let hasFinalResponse = false;
  let terminal = false;
  let terminalStatus: string | null = null;
  let failure: string | null = null;

  for (const event of [...events].sort((a, b) => sequence(a) - sequence(b))) {
    const type = String(event.event_type);
    const payload = record(event.payload);
    const seq = sequence(event);
    if (ACTIVITY_EVENTS.has(type)) activityCount += 1;
    if (type === "final_response") hasFinalResponse = true;
    if (type === OPERATION_FAILED || type === "run_failed") {
      failure = safeFailure(payload) ?? failure ?? "This run could not finish.";
    }
    if (type === "tool_result" && isFailed(payload)) {
      failure = safeFailure(payload) ?? failure ?? "This operation failed.";
    }
    if (type === "run_completed") {
      terminal = true;
      terminalStatus = text(payload.status);
    } else if (
      type === "run_failed" ||
      type === "run_cancelled" ||
      type === "run_timed_out"
    ) {
      terminal = true;
      terminalStatus = type.slice("run_".length);
    }

    if (type === ARTIFACT_CREATED) {
      const artifactId = text(payload.artifact_id);
      const kind = text(payload.kind) ?? "file";
      const revision = positiveInt(payload.revision);
      if (artifactId === null || revision === null) continue;
      const key = `artifact:${artifactId}`;
      artifactKeys.set(artifactId, key);
      const candidate: SubjectAccumulator = {
        key,
        kind: "artifact",
        subjectId: artifactId,
        title: `${kind} artifact`,
        revision,
        lastSeq: seq,
        priority: 300,
        rendererHint: `artifact-${kind}`,
      };
      artifactCandidates.set(artifactId, candidate);
      if (artifactDecisions.get(artifactId) === "canvas")
        subjects.set(key, candidate);
      continue;
    }
    if (type === ARTIFACT_REVISED) {
      const artifactId = text(payload.artifact_id);
      const revision = positiveInt(payload.revision);
      if (artifactId === null || revision === null) continue;
      const key = artifactKeys.get(artifactId);
      const candidate = artifactCandidates.get(artifactId);
      if (candidate !== undefined) {
        candidate.revision = revision;
        candidate.lastSeq = seq;
      }
      if (key !== undefined) {
        const subject = subjects.get(key);
        if (subject !== undefined) {
          subject.revision = revision;
          subject.lastSeq = seq;
        }
      }
      continue;
    }
    if (type === ARTIFACT_PRESENTATION_DECIDED) {
      const artifactId = text(payload.artifact_id);
      const decision = text(payload.decision);
      if (artifactId === null || decision === null) continue;
      artifactDecisions.set(artifactId, decision);
      const key = artifactKeys.get(artifactId);
      if (key !== undefined) {
        const candidate = artifactCandidates.get(artifactId);
        if (decision === "canvas" && candidate !== undefined)
          subjects.set(key, candidate);
        else if (decision !== "canvas") subjects.delete(key);
      }
      continue;
    }
    if (type === "surface.created") {
      const surfaceId = text(payload.surface_id);
      if (surfaceId === null) continue;
      const rendererHint = text(payload.kind) ?? "raw";
      const key = `surface:${surfaceId}`;
      surfaceKeys.set(surfaceId, key);
      // A receipt remains rail/export material until the user opens it. It
      // must not create or steal the active Studio tab by merely arriving last.
      if (rendererHint === "receipt") continue;
      subjects.set(key, {
        key,
        kind: "surface",
        subjectId: surfaceId,
        title: text(payload.title) ?? "Untitled result",
        revision: null,
        lastSeq: seq,
        priority: 200,
        rendererHint,
      });
      continue;
    }
    if (type === "view.derived") {
      const key = surfaceKeys.get(text(payload.surface_id) ?? "");
      const subject = key === undefined ? undefined : subjects.get(key);
      if (subject !== undefined) subject.lastSeq = seq;
      continue;
    }
    if (type === "write.staged" || type === EFFECT_STAGED) {
      const stageId = text(payload.stage_id);
      if (stageId === null) continue;
      const key = `effect:${stageId}`;
      effectKeys.set(stageId, key);
      pendingStages.add(key);
      subjects.set(key, {
        key,
        kind: "effect",
        subjectId: stageId,
        title: text(payload.display_target) ?? "Proposed change",
        revision: positiveInt(payload.revision),
        lastSeq: seq,
        priority: 400,
        rendererHint: "effect-stage",
      });
      continue;
    }
    if (type === EFFECT_REVISED) {
      const key = effectKeys.get(text(payload.stage_id) ?? "");
      const subject = key === undefined ? undefined : subjects.get(key);
      if (subject !== undefined) {
        const revision = positiveInt(payload.revision);
        if (revision !== null) subject.revision = revision;
        const title = text(payload.display_target);
        if (title !== null) subject.title = title;
        subject.lastSeq = seq;
      }
      continue;
    }
    if (type === "decision.recorded" || type === EFFECT_DECISION_RECORDED) {
      const key = effectKeys.get(text(payload.stage_id) ?? "");
      if (
        key !== undefined &&
        (text(payload.decision) === "reject" ||
          text(payload.decision) === "cancel")
      ) {
        // A rejected/cancelled proposal is still reviewable, but no longer
        // waits for a decision and therefore cannot keep Studio parked.
        pendingStages.delete(key);
        const subject = subjects.get(key);
        if (subject !== undefined) subject.lastSeq = seq;
      }
      continue;
    }
    if (
      type === EFFECT_APPLIED ||
      type === EFFECT_INDETERMINATE ||
      type === EFFECT_RECONCILED ||
      type === "write.applied"
    ) {
      const key = effectKeys.get(text(payload.stage_id) ?? "");
      if (key !== undefined) {
        pendingStages.delete(key);
        const subject = subjects.get(key);
        if (subject !== undefined) subject.lastSeq = seq;
      }
      continue;
    }
    if (type === "gate.opened" || type === GATE_OPENED_V2) {
      const gateId = text(payload.gate_id);
      if (gateId !== null) openGates.add(gateId);
      continue;
    }
    if (type === "gate.resolved" || type === GATE_RESOLVED_V2) {
      const gateId = text(payload.gate_id);
      if (gateId !== null) openGates.delete(gateId);
      continue;
    }
    if (type === "receipt.emitted") {
      const surfaceId = text(payload.surface_id);
      if (surfaceId !== null) {
        const key = surfaceKeys.get(surfaceId) ?? `receipt:${surfaceId}`;
        const existing = subjects.get(key);
        terminalReceipt =
          existing === undefined
            ? {
                key,
                kind: "receipt",
                subjectId: surfaceId,
                title: "Run receipt",
                revision: null,
                lastSeq: seq,
                priority: 10,
                rendererHint: "receipt",
              }
            : freeze(existing);
      }
    }
  }

  const tabs = [...subjects.values()]
    .sort(
      (left, right) =>
        right.priority - left.priority ||
        right.lastSeq - left.lastSeq ||
        left.key.localeCompare(right.key),
    )
    .map(freeze);
  const active =
    tabs.find((subject) => subject.kind !== "receipt") ?? tabs[0] ?? null;
  const pendingSubjectKeys = [
    ...pendingStages,
    ...[...openGates].sort().map((gateId) => `gate:${gateId}`),
  ].sort();
  const lifecycle = lifecycleState({
    terminal,
    terminalStatus,
    hasFinalResponse,
    hasSubject: tabs.length > 0,
    hasPending: pendingSubjectKeys.length > 0,
    failure,
  });
  return {
    lifecycle,
    tabs,
    activeSubjectKey: active?.key ?? null,
    pendingSubjectKeys,
    terminalReceipt,
    activityCount,
    failure,
    hasFinalResponse,
    terminal,
  };
}

function lifecycleState(input: {
  readonly terminal: boolean;
  readonly terminalStatus: string | null;
  readonly hasFinalResponse: boolean;
  readonly hasSubject: boolean;
  readonly hasPending: boolean;
  readonly failure: string | null;
}): CanvasLifecycleState {
  if (input.hasPending) return "parked";
  if (input.hasSubject) return "presenting";
  if (!input.terminal) return "assembling";
  if (input.failure !== null || input.terminalStatus === "failed")
    return "failed";
  return input.hasFinalResponse ? "chat_only" : "complete_empty";
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}
function positiveInt(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0
    ? value
    : null;
}
function sequence(event: RuntimeEventEnvelope): number {
  return positiveInt(event.sequence_no) ?? 0;
}
function isFailed(payload: Record<string, unknown>): boolean {
  return text(payload.status) === "failed" || text(payload.status) === "error";
}
function safeFailure(payload: Record<string, unknown>): string | null {
  for (const key of ["safe_message", "error_message", "failure_code"]) {
    const value = text(payload[key]);
    if (value !== null) return value.slice(0, 240);
  }
  return null;
}
function freeze(subject: SubjectAccumulator): CanvasLifecycleSubject {
  return { ...subject };
}
