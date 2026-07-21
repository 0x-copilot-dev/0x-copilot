import { describe, expect, it } from "vitest";

import type { LocalModelSummary } from "@0x-copilot/api-types";

import type { FirstRunEngine } from "./firstRun";
import {
  firstRunModelPillLabel,
  pullPercent,
  resolveInstalledTag,
} from "./localModelEngine";

const NAME = "Qwen 3 4B";

function summary(name: string): LocalModelSummary {
  return {
    name,
    size_bytes: 1,
    quantization: null,
    parameter_size: null,
    run_placement: null,
  };
}

describe("firstRunModelPillLabel", () => {
  it("shows the rounded percent while a local model downloads", () => {
    const engine: FirstRunEngine = { kind: "local", modelId: null };
    expect(firstRunModelPillLabel(engine, NAME, 41)).toBe("Qwen 3 4B · 41%");
    expect(firstRunModelPillLabel(engine, NAME, 40.6)).toBe("Qwen 3 4B · 41%");
  });

  it("drops the percent once the local model is ready (100 or null)", () => {
    const engine: FirstRunEngine = { kind: "local", modelId: "hf.co/x:Q8_0" };
    expect(firstRunModelPillLabel(engine, NAME, 100)).toBe("Qwen 3 4B");
    expect(firstRunModelPillLabel(engine, NAME, null)).toBe("Qwen 3 4B");
  });

  it("returns the provider label for a key engine and '' for none", () => {
    const key: FirstRunEngine = {
      kind: "key",
      provider: "anthropic",
      label: "Anthropic",
      dotColor: "#d97757",
      modelId: null,
    };
    expect(firstRunModelPillLabel(key, NAME, null)).toBe("Anthropic");
    expect(firstRunModelPillLabel(null, NAME, null)).toBe("");
  });
});

describe("pullPercent", () => {
  it("uses live bytes_total as the denominator", () => {
    expect(pullPercent(25, 100, 999, false)).toBe(25);
  });

  it("falls back to the size hint until bytes_total arrives", () => {
    expect(pullPercent(50, null, 200, false)).toBe(25);
  });

  it("clamps at 100 and returns 100 on done with no totals", () => {
    expect(pullPercent(300, 100, null, false)).toBe(100);
    expect(pullPercent(null, null, null, true)).toBe(100);
    expect(pullPercent(null, null, null, false)).toBe(0);
  });
});

describe("resolveInstalledTag", () => {
  it("matches an installed tag case-insensitively by repo substring", () => {
    const models = [summary("HF.CO/Qwen/Qwen3-4B-GGUF:Q8_0")];
    expect(resolveInstalledTag(models, "Qwen/Qwen3-4B-GGUF", "Q8_0")).toBe(
      "HF.CO/Qwen/Qwen3-4B-GGUF:Q8_0",
    );
  });

  it("falls back to the literal hf.co tag when nothing matches", () => {
    expect(
      resolveInstalledTag([summary("llama3")], "Qwen/Qwen3-4B-GGUF", "Q8_0"),
    ).toBe("hf.co/Qwen/Qwen3-4B-GGUF:Q8_0");
  });
});
