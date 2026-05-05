import type { ModelCatalogModel } from "@enterprise-search/api-types";
import { describe, expect, it } from "vitest";
import {
  applyDepth,
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

const optedOutModel: ModelCatalogModel = {
  ...reasoningModel,
  reasoning: { enabled: false },
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

describe("applyDepth", () => {
  it("is a no-op when depth is undefined", () => {
    const sel = { provider: "openai", reasoning: { enabled: true } };
    expect(applyDepth(sel, undefined)).toBe(sel);
  });

  it("preserves opt-out when reasoning.enabled === false", () => {
    const sel = { reasoning: optedOutModel.reasoning };
    expect(applyDepth(sel, "deep")).toBe(sel);
  });

  it("maps fast → low / balanced → medium / deep → high", () => {
    expect(applyDepth({ reasoning: null }, "fast")).toEqual({
      reasoning: { enabled: true, effort: "low" },
    });
    expect(applyDepth({ reasoning: null }, "balanced")).toEqual({
      reasoning: { enabled: true, effort: "medium" },
    });
    expect(applyDepth({ reasoning: null }, "deep")).toEqual({
      reasoning: { enabled: true, effort: "high" },
    });
  });

  it("preserves unrelated reasoning fields", () => {
    const sel = {
      reasoning: { enabled: true, summary: "auto", custom: "x" },
    };
    expect(applyDepth(sel, "deep").reasoning).toMatchObject({
      enabled: true,
      effort: "high",
      summary: "auto",
      custom: "x",
    });
  });

  it("preserves other selection fields", () => {
    const sel = { provider: "openai", model_name: "gpt-5.4", reasoning: null };
    const next = applyDepth(sel, "fast");
    expect(next.provider).toBe("openai");
    expect(next.model_name).toBe("gpt-5.4");
  });
});

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
