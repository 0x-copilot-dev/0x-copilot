import type { ModelCatalogModel } from "@enterprise-search/api-types";

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

const EFFORT_BY_DEPTH: Record<ThinkingDepth, "low" | "medium" | "high"> = {
  fast: "low",
  balanced: "medium",
  deep: "high",
};

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

/**
 * Layer a depth selection onto a `ModelSelection`-shaped object. Only
 * mutates the `reasoning` field; everything else flows through unchanged.
 *
 * Returns the input as-is when:
 * - depth is undefined,
 * - the existing reasoning shape declares `enabled: false`,
 * - the model does not support reasoning at all.
 *
 * Otherwise returns a new object with `reasoning.effort` overridden.
 * The original `reasoning.summary` and any other fields are preserved.
 */
export function applyDepth<
  T extends { reasoning?: Record<string, unknown> | null },
>(selection: T, depth: ThinkingDepth | undefined): T {
  if (depth === undefined) {
    return selection;
  }
  const reasoning = selection.reasoning ?? null;
  // Respect an explicit opt-out — if the catalog row sets enabled=false we
  // honour it. Anything else (null, missing, enabled=true, or unset)
  // becomes an `enabled: true` block with our effort.
  if (
    reasoning &&
    typeof reasoning === "object" &&
    "enabled" in reasoning &&
    reasoning.enabled === false
  ) {
    return selection;
  }
  return {
    ...selection,
    reasoning: {
      ...(reasoning ?? {}),
      enabled: true,
      effort: EFFORT_BY_DEPTH[depth],
    },
  };
}
