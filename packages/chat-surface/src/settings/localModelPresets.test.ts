import { describe, expect, it } from "vitest";

import { LOCAL_MODEL_PRESETS, QWEN3_4B_PRESET } from "./localModelPresets";

describe("QWEN3_4B_PRESET", () => {
  it("pins the real Qwen3-4B GGUF repo, quant, and verified byte size", () => {
    expect(QWEN3_4B_PRESET.repo).toBe("Qwen/Qwen3-4B-GGUF");
    expect(QWEN3_4B_PRESET.quant).toBe("Q8_0");
    expect(QWEN3_4B_PRESET.name).toBe("Qwen 3 4B");
    expect(QWEN3_4B_PRESET.parameterSize).toBe("4B");
    // Verified Hugging Face Q8_0 byte count (Qwen3-4B-Q8_0.gguf).
    expect(QWEN3_4B_PRESET.sizeBytes).toBe(4_280_404_704);
  });

  it("resolves to a valid Ollama HF pull tag", () => {
    expect(`hf.co/${QWEN3_4B_PRESET.repo}:${QWEN3_4B_PRESET.quant}`).toBe(
      "hf.co/Qwen/Qwen3-4B-GGUF:Q8_0",
    );
  });

  it("is the sole entry in the curated catalog", () => {
    expect(LOCAL_MODEL_PRESETS).toHaveLength(1);
    expect(LOCAL_MODEL_PRESETS[0]).toBe(QWEN3_4B_PRESET);
  });
});
