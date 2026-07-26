// Canonical workspace-effect → safe Studio review projection (PRD-C3).
//
// The effect ledger deliberately carries opaque target/proposal references.
// This module never renders or exports those references. It folds only the
// allowlisted display and digest fields needed to make an exact, digest-pinned
// approval decision. Incomplete, stale, or inconsistent rows remain visible as
// an honest held surface with no decision capability.

import {
  ARTIFACT_EVENT_TYPES,
  EFFECT_EVENT_TYPES,
  type ArtifactKind,
  type RuntimeEventEnvelope,
} from "@0x-copilot/api-types";

import type { WorkspaceApprovalSnapshot } from "../../ports/WorkspaceStageHostPort";
import {
  safeWorkspaceVirtualPath,
  type WorkspaceStage,
  type WorkspaceStageOperationKind,
  type WorkspaceStageResolution,
  type WorkspaceStageStatus,
} from "../../thread-canvas/workspaceStageProjection";

const [ARTIFACT_CREATED, ARTIFACT_REVISED] = ARTIFACT_EVENT_TYPES;
const [
  EFFECT_STAGED,
  EFFECT_PROJECTION_BOUND,
  EFFECT_REVISED,
  EFFECT_DECISION_RECORDED,
  EFFECT_CLAIMED,
  EFFECT_APPLIED,
  EFFECT_INDETERMINATE,
  EFFECT_RECONCILED,
] = EFFECT_EVENT_TYPES;

const OPAQUE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const SHA256_HEX = /^[a-f0-9]{64}$/iu;

export interface WorkspaceStageArtifactFallback {
  readonly artifactId: string;
  readonly revision: number;
  readonly kind: ArtifactKind;
}

/** Safe, host-actionable review state for one canonical workspace stage. */
export interface WorkspaceStageReview {
  readonly stage: WorkspaceStage;
  /** Null is intentional: the UI must not decide incomplete/stale stage data. */
  readonly snapshot: WorkspaceApprovalSnapshot | null;
  /** Optional server artifact that a web host can download instead of writing locally. */
  readonly artifactFallback: WorkspaceStageArtifactFallback | null;
}

export type WorkspaceStageReviewProjection = ReadonlyMap<
  string,
  WorkspaceStageReview
>;

interface ArtifactSource extends WorkspaceStageArtifactFallback {
  readonly contentRef: string;
}

interface StageAccumulator {
  readonly stageId: string;
  readonly runId: string;
  readonly operation: WorkspaceStageOperationKind;
  readonly title: string;
  readonly mountLabel: string;
  readonly policy: "auto" | "ask" | "require" | "block" | null;
  readonly agentHold: boolean;
  readonly projectionRequired: boolean;
  projectionReady: boolean;
  revision: number | null;
  proposalDigest: string | null;
  targetDigest: string | null;
  virtualPath: string | null;
  proposalContentRef: string | null;
  author: string;
  status: WorkspaceStageStatus;
  resolution: WorkspaceStageResolution | null;
  decisionAvailable: boolean;
  corrupted: boolean;
}

/**
 * Fold only workspace executor stages from the same persisted event stream the
 * canvas lifecycle consumes. No I/O, no optimistic status, and no target or
 * proposal dereference occur here.
 */
export function projectWorkspaceStageLifecycle(
  events: readonly RuntimeEventEnvelope[],
): WorkspaceStageReviewProjection {
  const artifactsByContentRef = new Map<string, ArtifactSource>();
  const artifactKindsById = new Map<string, ArtifactKind>();
  const stages = new Map<string, StageAccumulator>();

  for (const event of ordered(events)) {
    const payload = record(event.payload);
    const eventType = String(event.event_type);

    if (eventType === ARTIFACT_CREATED || eventType === ARTIFACT_REVISED) {
      const source = artifactSource(payload, artifactKindsById);
      if (source !== null) {
        artifactsByContentRef.set(source.contentRef, source);
        artifactKindsById.set(source.artifactId, source.kind);
      }
      continue;
    }

    if (eventType === EFFECT_STAGED) {
      stageFromStaged(event, payload, stages);
      continue;
    }

    const stageId = opaqueId(payload.stage_id);
    if (stageId === null) continue;
    const stage = stages.get(stageId);
    if (stage === undefined || stage.runId !== event.run_id) continue;

    switch (eventType) {
      case EFFECT_PROJECTION_BOUND:
        applyProjectionBinding(stage, payload);
        break;
      case EFFECT_REVISED:
        applyRevision(stage, payload);
        break;
      case EFFECT_DECISION_RECORDED:
        applyDecision(stage, payload);
        break;
      case EFFECT_CLAIMED:
        applyClaim(stage, payload);
        break;
      case EFFECT_APPLIED:
        applyOutcome(stage, payload);
        break;
      case EFFECT_INDETERMINATE:
        if (matchesRevision(stage, payload)) {
          stage.status = "held";
          stage.resolution = { state: "indeterminate" };
          stage.decisionAvailable = false;
        }
        break;
      case EFFECT_RECONCILED:
        applyOutcome(stage, payload);
        break;
      default:
        break;
    }
  }

  const projection = new Map<string, WorkspaceStageReview>();
  for (const stage of stages.values()) {
    const fallback =
      stage.proposalContentRef === null
        ? null
        : (artifactsByContentRef.get(stage.proposalContentRef) ?? null);
    const snapshot = snapshotFor(stage);
    projection.set(
      stage.stageId,
      Object.freeze({
        stage: Object.freeze({
          stageId: stage.stageId,
          title: stage.title,
          operation: { kind: stage.operation },
          target: {
            mountLabel: stage.mountLabel,
            // The display component validates again. Empty is a safe sentinel
            // that renders as "Virtual target unavailable", never as a path.
            virtualPath: stage.virtualPath ?? "",
          },
          revision: stage.revision ?? 0,
          author: stage.author,
          status: stage.status,
          ...(stage.resolution === null
            ? {}
            : { resolution: stage.resolution }),
          decisionAvailable: snapshot !== null && stage.decisionAvailable,
          restoreAvailable: false,
          editAvailable: fallback !== null,
        }),
        snapshot,
        artifactFallback:
          fallback === null
            ? null
            : Object.freeze({
                artifactId: fallback.artifactId,
                revision: fallback.revision,
                kind: fallback.kind,
              }),
      }),
    );
  }
  return projection;
}

function stageFromStaged(
  event: RuntimeEventEnvelope,
  payload: Record<string, unknown>,
  stages: Map<string, StageAccumulator>,
): void {
  if (payload.executor !== "workspace") return;
  const stageId = opaqueId(payload.stage_id);
  const runId = opaqueId(event.run_id);
  if (stageId === null || runId === null || stages.has(stageId)) return;

  const policy = effectPolicy(payload.policy);
  const operation = workspaceOperation(payload.op);
  const virtualPath = safeWorkspaceVirtualPath(text(payload.display_target));
  const corrupted =
    policy === null ||
    operation === "unknown" ||
    virtualPath === null ||
    sha256(payload.proposal_digest) === null ||
    sha256(payload.target_digest) === null;
  const agentHold = payload.agent_hold === true;
  const projectionRequired = payload.projection_required === true;
  const decisionAvailable =
    !corrupted &&
    !agentHold &&
    !projectionRequired &&
    (policy === "ask" || policy === "require");
  const heldByPolicy = agentHold || policy === "block";

  stages.set(stageId, {
    stageId,
    runId,
    operation,
    title: stageTitle(operation),
    mountLabel: mountLabel(payload.display_target),
    policy,
    agentHold,
    projectionRequired,
    projectionReady: !projectionRequired,
    revision: 1,
    proposalDigest: sha256(payload.proposal_digest),
    targetDigest: sha256(payload.target_digest),
    virtualPath,
    proposalContentRef: text(payload.proposal_content_ref),
    author: actorLabel(payload.author_actor),
    status: corrupted || heldByPolicy ? "held" : "staged",
    resolution: corrupted ? detailsUnavailable() : null,
    decisionAvailable,
    corrupted,
  });
}

function applyRevision(
  stage: StageAccumulator,
  payload: Record<string, unknown>,
): void {
  const revision = positiveInt(payload.revision);
  const proposalDigest = sha256(payload.proposal_digest);
  const targetDigest =
    payload.target_digest === undefined
      ? stage.targetDigest
      : sha256(payload.target_digest);
  const nextTarget =
    payload.display_target === undefined
      ? stage.virtualPath
      : safeWorkspaceVirtualPath(text(payload.display_target));
  if (
    revision === null ||
    stage.revision === null ||
    revision !== stage.revision + 1 ||
    proposalDigest === null ||
    targetDigest === null ||
    nextTarget === null ||
    stage.corrupted
  ) {
    markDetailsUnavailable(stage);
    return;
  }

  // A canonical effect stage target is immutable across revisions. If a
  // malformed row tries to repin it, keep the card visible but never decide it.
  if (targetDigest !== stage.targetDigest || nextTarget !== stage.virtualPath) {
    markDetailsUnavailable(stage);
    return;
  }

  stage.revision = revision;
  stage.proposalDigest = proposalDigest;
  stage.projectionReady = !stage.projectionRequired;
  stage.proposalContentRef =
    payload.proposal_content_ref === undefined
      ? stage.proposalContentRef
      : text(payload.proposal_content_ref);
  stage.author = actorLabel(payload.author_actor);
  stage.status =
    stage.agentHold || stage.policy === "block" ? "held" : "staged";
  stage.resolution = null;
  stage.decisionAvailable =
    !stage.projectionRequired &&
    (stage.policy === "ask" || stage.policy === "require");
}

function applyProjectionBinding(
  stage: StageAccumulator,
  payload: Record<string, unknown>,
): void {
  if (!stage.projectionRequired || !matchesDecisionSnapshot(stage, payload)) {
    markDetailsUnavailable(stage);
    return;
  }
  const projectionRef = text(payload.projection_ref);
  if (projectionRef === null || !isProjectionRef(projectionRef)) {
    markDetailsUnavailable(stage);
    return;
  }
  stage.projectionReady = true;
  stage.decisionAvailable =
    !stage.agentHold &&
    (stage.policy === "ask" || stage.policy === "require") &&
    stage.status === "staged";
}

function applyDecision(
  stage: StageAccumulator,
  payload: Record<string, unknown>,
): void {
  if (!matchesDecisionSnapshot(stage, payload)) {
    markDetailsUnavailable(stage);
    return;
  }
  switch (payload.decision) {
    case "approve":
      stage.status = "approved";
      stage.resolution = null;
      stage.decisionAvailable = false;
      break;
    case "reject":
      stage.status = "rejected";
      stage.resolution = null;
      stage.decisionAvailable = false;
      break;
    default:
      // Canonical workspace UI does not invent restore/cancel behavior. A
      // missing or unsupported decision remains a visible, non-actionable hold.
      markDetailsUnavailable(stage);
      break;
  }
}

function applyClaim(
  stage: StageAccumulator,
  payload: Record<string, unknown>,
): void {
  if (!matchesRevision(stage, payload)) {
    markDetailsUnavailable(stage);
    return;
  }
  if (stage.status !== "approved") markDetailsUnavailable(stage);
}

function applyOutcome(
  stage: StageAccumulator,
  payload: Record<string, unknown>,
): void {
  if (!matchesRevision(stage, payload)) {
    markDetailsUnavailable(stage);
    return;
  }
  switch (payload.outcome) {
    case "applied":
    case "already_applied":
      stage.status = "applied";
      stage.resolution = null;
      stage.decisionAvailable = false;
      break;
    case "precondition_drift":
      stage.status = "held";
      stage.resolution = { state: "precondition_drift" };
      stage.decisionAvailable = false;
      break;
    case "indeterminate":
      stage.status = "held";
      stage.resolution = { state: "indeterminate" };
      stage.decisionAvailable = false;
      break;
    case "cancelled":
      stage.status = "rejected";
      stage.resolution = null;
      stage.decisionAvailable = false;
      break;
    case "failed":
    case "partial":
      stage.status = "failed";
      stage.resolution = { state: "details_unavailable" };
      stage.decisionAvailable = false;
      break;
    default:
      markDetailsUnavailable(stage);
      break;
  }
}

function matchesDecisionSnapshot(
  stage: StageAccumulator,
  payload: Record<string, unknown>,
): boolean {
  return (
    matchesRevision(stage, payload) &&
    sha256(payload.proposal_digest) === stage.proposalDigest &&
    sha256(payload.target_digest) === stage.targetDigest
  );
}

function matchesRevision(
  stage: StageAccumulator,
  payload: Record<string, unknown>,
): boolean {
  return !stage.corrupted && positiveInt(payload.revision) === stage.revision;
}

function snapshotFor(
  stage: StageAccumulator,
): WorkspaceApprovalSnapshot | null {
  if (
    stage.corrupted ||
    !stage.projectionReady ||
    !stage.decisionAvailable ||
    stage.status !== "staged" ||
    stage.revision === null ||
    stage.proposalDigest === null ||
    stage.targetDigest === null ||
    stage.virtualPath === null
  ) {
    return null;
  }
  return Object.freeze({
    runId: stage.runId,
    stageId: stage.stageId,
    revision: stage.revision,
    proposalDigest: stage.proposalDigest,
    targetDigest: stage.targetDigest,
  });
}

function markDetailsUnavailable(stage: StageAccumulator): void {
  stage.corrupted = true;
  stage.status = "held";
  stage.resolution = detailsUnavailable();
  stage.decisionAvailable = false;
}

function detailsUnavailable(): WorkspaceStageResolution {
  return { state: "details_unavailable" };
}

function artifactSource(
  payload: Record<string, unknown>,
  knownKinds: ReadonlyMap<string, ArtifactKind>,
): ArtifactSource | null {
  const artifactId = opaqueId(payload.artifact_id);
  const revision = positiveInt(payload.revision);
  const contentRef = text(payload.content_ref);
  const kind =
    artifactKind(payload.kind) ??
    (artifactId === null ? null : (knownKinds.get(artifactId) ?? null));
  if (
    artifactId === null ||
    revision === null ||
    contentRef === null ||
    kind === null
  ) {
    return null;
  }
  return { artifactId, revision, contentRef, kind };
}

function artifactKind(value: unknown): ArtifactKind | null {
  return value === "code" ||
    value === "document" ||
    value === "dataset" ||
    value === "file"
    ? value
    : null;
}

function effectPolicy(
  value: unknown,
): "auto" | "ask" | "require" | "block" | null {
  return value === "auto" ||
    value === "ask" ||
    value === "require" ||
    value === "block"
    ? value
    : null;
}

function workspaceOperation(value: unknown): WorkspaceStageOperationKind {
  switch (value) {
    case "create":
    case "create_file":
      return "create";
    case "replace":
    case "write":
    case "write_file":
    case "edit":
    case "edit_file":
      return "replace";
    case "delete":
    case "trash":
    case "purge":
      return "delete";
    case "move":
      return "move";
    case "mkdir":
      return "mkdir";
    default:
      return "unknown";
  }
}

function stageTitle(operation: WorkspaceStageOperationKind): string {
  switch (operation) {
    case "create":
      return "Create workspace file";
    case "replace":
      return "Update workspace file";
    case "delete":
      return "Remove workspace item";
    case "move":
      return "Move workspace item";
    case "mkdir":
      return "Create workspace folder";
    case "unknown":
      return "Workspace change";
  }
}

function mountLabel(displayTarget: unknown): string {
  const candidate = text(displayTarget);
  if (candidate === null || safeWorkspaceVirtualPath(candidate) !== null) {
    return "Workspace";
  }
  // Display target is server-projected wording, but still route it through a
  // strict path/token check before allowing it onto the card.
  if (looksUnsafeDisplay(candidate)) return "Workspace";
  return candidate.slice(0, 120);
}

function actorLabel(value: unknown): string {
  switch (value) {
    case "user":
      return "You";
    case "policy":
      return "Policy";
    case "system":
      return "System";
    default:
      return "Agent";
  }
}

function ordered(
  events: readonly RuntimeEventEnvelope[],
): readonly RuntimeEventEnvelope[] {
  return [...events].sort(
    (left, right) =>
      sequence(left) - sequence(right) ||
      left.event_id.localeCompare(right.event_id),
  );
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function opaqueId(value: unknown): string | null {
  return typeof value === "string" && OPAQUE_ID.test(value) ? value : null;
}

function sha256(value: unknown): string | null {
  return typeof value === "string" && SHA256_HEX.test(value) ? value : null;
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

function looksUnsafeDisplay(value: string): boolean {
  return /(?:file:|~[\\/]|[A-Za-z]:[\\/]|\\\\|\/(?:Users|home|private|var|tmp|Volumes|etc|usr|opt|root|mnt|Library|System)\/|(?:permit(?:[_\s-]?token|Token)?|prepared(?:[_\s-]?ref|Ref)|(?:target|proposal|content|precondition)(?:[_\s-]?(?:ref|id)|Ref|Id)|physical(?:[_\s-]?path|Path)?)\s*[:=])/i.test(
    value,
  );
}

function isProjectionRef(value: string): boolean {
  return /^workspace-overlay:\/\/runs\/[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\/versions\/[1-9][0-9]*$/u.test(
    value,
  );
}
