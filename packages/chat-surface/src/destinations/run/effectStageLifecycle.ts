// Safe, client-side read model for generic MCP A4 stages.
//
// This is intentionally separate from `workspaceStageLifecycle`: workspace
// stages require host-specific receipt/permit semantics, while the ordinary
// external MCP path can be actioned through the owner-scoped server route.
// Neither projection ever exposes target/proposal references or material.

import {
  EFFECT_EVENT_TYPES,
  type RuntimeEventEnvelope,
} from "@0x-copilot/api-types";

const [
  EFFECT_STAGED,
  ,
  EFFECT_REVISED,
  EFFECT_DECISION_RECORDED,
  EFFECT_CLAIMED,
  EFFECT_APPLIED,
  EFFECT_INDETERMINATE,
  EFFECT_RECONCILED,
] = EFFECT_EVENT_TYPES;

const OPAQUE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const SHA256_HEX = /^[a-f0-9]{64}$/iu;

export type McpEffectStageStatus =
  | "awaiting_review"
  | "approved"
  | "rejected"
  | "executing"
  | "applied"
  | "held";

export interface McpEffectStageReview {
  readonly stageId: string;
  readonly title: string;
  readonly revision: number;
  readonly proposalDigest: string;
  readonly targetDigest: string;
  readonly status: McpEffectStageStatus;
  readonly canDecide: boolean;
}

interface Accumulator {
  readonly stageId: string;
  title: string;
  revision: number;
  proposalDigest: string;
  targetDigest: string;
  policy: "ask" | "require" | "auto" | "block" | null;
  agentHold: boolean;
  status: McpEffectStageStatus;
  valid: boolean;
}

/**
 * Fold only standard MCP stages from the run event stream.
 *
 * A malformed/incomplete stage remains visible as held but does not produce an
 * actionable snapshot. The server is still authoritative, and validates the
 * exact revision/digest pair again before it appends a decision.
 */
export function projectMcpEffectStages(
  events: readonly RuntimeEventEnvelope[],
): ReadonlyMap<string, McpEffectStageReview> {
  const stages = new Map<string, Accumulator>();
  for (const event of ordered(events)) {
    const payload = record(event.payload);
    const type = String(event.event_type);
    if (type === EFFECT_STAGED) {
      stageFromStaged(payload, stages);
      continue;
    }
    const stageId = opaqueId(payload.stage_id);
    if (stageId === null) continue;
    const stage = stages.get(stageId);
    if (stage === undefined) continue;

    switch (type) {
      case EFFECT_REVISED:
        applyRevision(stage, payload);
        break;
      case EFFECT_DECISION_RECORDED:
        applyDecision(stage, payload);
        break;
      case EFFECT_CLAIMED:
        if (matchesSnapshot(stage, payload)) stage.status = "executing";
        break;
      case EFFECT_APPLIED:
      case EFFECT_RECONCILED:
        if (matchesSnapshot(stage, payload)) stage.status = "applied";
        break;
      case EFFECT_INDETERMINATE:
        if (matchesSnapshot(stage, payload)) stage.status = "held";
        break;
      default:
        break;
    }
  }

  const result = new Map<string, McpEffectStageReview>();
  for (const stage of stages.values()) {
    result.set(
      stage.stageId,
      Object.freeze({
        stageId: stage.stageId,
        title: stage.title,
        revision: stage.revision,
        proposalDigest: stage.proposalDigest,
        targetDigest: stage.targetDigest,
        status: stage.status,
        canDecide:
          stage.valid &&
          !stage.agentHold &&
          (stage.policy === "ask" || stage.policy === "require") &&
          stage.status === "awaiting_review",
      }),
    );
  }
  return result;
}

function stageFromStaged(
  payload: Record<string, unknown>,
  stages: Map<string, Accumulator>,
): void {
  if (payload.executor !== "mcp") return;
  const stageId = opaqueId(payload.stage_id);
  if (stageId === null || stages.has(stageId)) return;
  const proposalDigest = sha256(payload.proposal_digest);
  const targetDigest = sha256(payload.target_digest);
  const policy = effectPolicy(payload.policy);
  const title = text(payload.display_target);
  const valid =
    proposalDigest !== null &&
    targetDigest !== null &&
    policy !== null &&
    title !== null;
  stages.set(stageId, {
    stageId,
    title: title ?? "Proposed change",
    revision: 1,
    proposalDigest: proposalDigest ?? "",
    targetDigest: targetDigest ?? "",
    policy,
    agentHold: payload.agent_hold === true,
    status: valid ? "awaiting_review" : "held",
    valid,
  });
}

function applyRevision(
  stage: Accumulator,
  payload: Record<string, unknown>,
): void {
  const revision = positiveInt(payload.revision);
  const proposalDigest = sha256(payload.proposal_digest);
  const targetDigest = sha256(payload.target_digest);
  const title = text(payload.display_target);
  if (
    revision === null ||
    revision !== stage.revision + 1 ||
    proposalDigest === null ||
    targetDigest === null ||
    targetDigest !== stage.targetDigest ||
    title === null ||
    title !== stage.title
  ) {
    stage.valid = false;
    stage.status = "held";
    return;
  }
  stage.revision = revision;
  stage.proposalDigest = proposalDigest;
  stage.title = title;
  stage.status = stage.agentHold ? "held" : "awaiting_review";
}

function applyDecision(
  stage: Accumulator,
  payload: Record<string, unknown>,
): void {
  if (!matchesSnapshot(stage, payload)) {
    stage.valid = false;
    stage.status = "held";
    return;
  }
  if (payload.decision === "approve") stage.status = "approved";
  else if (payload.decision === "reject") stage.status = "rejected";
  else {
    stage.valid = false;
    stage.status = "held";
  }
}

function matchesSnapshot(
  stage: Accumulator,
  payload: Record<string, unknown>,
): boolean {
  return (
    positiveInt(payload.revision) === stage.revision &&
    sha256(payload.proposal_digest) === stage.proposalDigest &&
    sha256(payload.target_digest) === stage.targetDigest
  );
}

function ordered(
  events: readonly RuntimeEventEnvelope[],
): readonly RuntimeEventEnvelope[] {
  return [...events].sort(
    (left, right) => left.sequence_no - right.sequence_no,
  );
}
function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}
function opaqueId(value: unknown): string | null {
  return typeof value === "string" && OPAQUE_ID.test(value) ? value : null;
}
function positiveInt(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0
    ? value
    : null;
}
function sha256(value: unknown): string | null {
  return typeof value === "string" && SHA256_HEX.test(value) ? value : null;
}
function effectPolicy(
  value: unknown,
): "ask" | "require" | "auto" | "block" | null {
  return value === "ask" ||
    value === "require" ||
    value === "auto" ||
    value === "block"
    ? value
    : null;
}
