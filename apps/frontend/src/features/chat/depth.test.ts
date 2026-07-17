import type { ModelCatalogModel } from "@0x-copilot/api-types";
import { describe, expect, it } from "vitest";
import {
  depthLabel,
  depthLabelForModel,
  isThinkingDepth,
  modelSupportsDepth,
  THINKING_DEPTHS,
} from "./depth";

const reasoningModel: ModelCatalogModel = {
  id: "m1",
  provider: "openai",
  model_name: "gpt-5.4-mini",
  name: "GPT-5.4 Mini",
  configured: true,
  supports_streaming: true,
  supports_reasoning: true,
  reasoning: { enabled: true, effort: "medium" },
};

const noReasoningModel: ModelCatalogModel = {
  id: "m2",
  provider: "openai",
  model_name: "gpt-5.4-nano",
  name: "GPT-5.4 Nano",
  configured: true,
  supports_reasoning: false,
  reasoning: null,
};

describe("isThinkingDepth", () => {
  it.each(THINKING_DEPTHS.map((d) => [d]))("accepts %s", (d) => {
    expect(isThinkingDepth(d)).toBe(true);
  });
  it("rejects everything else", () => {
    for (const v of [null, undefined, "", "extra", 42, {}, []]) {
      expect(isThinkingDepth(v)).toBe(false);
    }
  });
});

describe("modelSupportsDepth", () => {
  it("supports reasoning models", () => {
    expect(modelSupportsDepth(reasoningModel)).toBe(true);
  });
  it("does not support models flagged unsupported", () => {
    expect(modelSupportsDepth(noReasoningModel)).toBe(false);
  });
  it("does not support null", () => {
    expect(modelSupportsDepth(null)).toBe(false);
  });
  it("supports models with reasoning shape but no explicit flag", () => {
    expect(
      modelSupportsDepth({
        ...reasoningModel,
        supports_reasoning: undefined,
      }),
    ).toBe(true);
  });
});

// applyDepth was removed in Phase 1 P1-C (chats-canvas-prd §16) — depth
// now flows as a top-level `reasoning_depth` wire field on
// CreateRunRequest. The wire-level contract is tested in
// `apps/frontend/src/api/agentApi.depth.test.ts`.

describe("depthLabelForModel (PR 3.5 / G3)", () => {
  it("falls back to the default label when the model has no reasoning hint", () => {
    expect(depthLabelForModel("fast", noReasoningModel)).toBe(
      depthLabel("fast"),
    );
    expect(depthLabelForModel("balanced", null)).toBe(depthLabel("balanced"));
    expect(depthLabelForModel("deep", undefined)).toBe(depthLabel("deep"));
  });

  it("falls back when depth_label is absent or empty", () => {
    const noLabel: ModelCatalogModel = {
      ...reasoningModel,
      reasoning: { enabled: true, effort: "medium" },
    };
    const emptyLabel: ModelCatalogModel = {
      ...reasoningModel,
      reasoning: { enabled: true, depth_label: "   " },
    };
    expect(depthLabelForModel("balanced", noLabel)).toBe("Balanced");
    expect(depthLabelForModel("balanced", emptyLabel)).toBe("Balanced");
  });

  it("uses the model catalog override when present", () => {
    const research: ModelCatalogModel = {
      ...reasoningModel,
      reasoning: { enabled: true, depth_label: "Thorough" },
    };
    expect(depthLabelForModel("deep", research)).toBe("Thorough");
  });
});
