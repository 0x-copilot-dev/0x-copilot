import type { ModelCatalogModel } from "@0x-copilot/api-types";

/**
 * Thinking depth is the user-facing handle for reasoning effort.
 *
 * The runtime already accepts `reasoning.effort` per call (see
 * `services/ai-backend/src/agent_runtime/execution/models.py`); this module
 * is the only place that knows how depth maps onto effort and is the only
 * place a model selection gets that override applied. Mid-run depth changes
 * never alter the active run — the run snapshot freezes its ModelConfig.
 */
export type ThinkingDepth = "fast" | "balanced" | "deep";

export const THINKING_DEPTHS: readonly ThinkingDepth[] = [
  "fast",
  "balanced",
  "deep",
];

const DEPTH_LABEL: Record<ThinkingDepth, string> = {
  fast: "Fast",
  balanced: "Balanced",
  deep: "Deep",
};

const DEPTH_DESCRIPTION: Record<ThinkingDepth, string> = {
  fast: "Snappy answers — minimal reasoning.",
  balanced: "Default — reasons about the prompt before answering.",
  deep: "Thorough — extra reasoning at the cost of latency.",
};

export const DEFAULT_THINKING_DEPTH: ThinkingDepth = "balanced";

export function isThinkingDepth(value: unknown): value is ThinkingDepth {
  return value === "fast" || value === "balanced" || value === "deep";
}

export function depthLabel(depth: ThinkingDepth): string {
  return DEPTH_LABEL[depth];
}

/**
 * PR 3.5 / G3 — depth label that prefers the model catalog's
 * `reasoning.depth_label` when present (e.g. a "Research" model can
 * advertise "Light" / "Standard" / "Thorough" instead of the global
 * Fast / Balanced / Deep wording). Falls back to the FE's default
 * label when the field is absent or empty.
 */
export function depthLabelForModel(
  depth: ThinkingDepth,
  model: ModelCatalogModel | null | undefined,
): string {
  const override = model?.reasoning?.depth_label;
  return typeof override === "string" && override.trim().length > 0
    ? override
    : DEPTH_LABEL[depth];
}

export function depthDescription(depth: ThinkingDepth): string {
  return DEPTH_DESCRIPTION[depth];
}

export function modelSupportsDepth(model: ModelCatalogModel | null): boolean {
  if (!model) {
    return false;
  }
  if (model.supports_reasoning === false) {
    return false;
  }
  // Either an explicit `supports_reasoning: true` or a non-null `reasoning`
  // shape — both indicate the runtime can take an effort override.
  return Boolean(model.supports_reasoning) || model.reasoning != null;
}

// Phase 1 (chats-canvas-prd §16): the `applyDepth(model, depth)` helper
// was a workaround from before `CreateRunRequest.reasoning_depth` landed
// as a top-level wire field. It baked depth into the model selection's
// `reasoning.effort` slot at the frontend. Depth now flows at the wire
// level (see `apps/frontend/src/api/agentApi.ts:createRun()`), so the
// helper has no callers. Removed deliberately — keep the single source
// of truth at the wire field.
