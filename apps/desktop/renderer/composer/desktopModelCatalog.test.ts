import { describe, expect, it } from "vitest";

import {
  defaultSelectedModelId,
  mergeCatalog,
  type CatalogModel,
} from "./desktopModelCatalog";

// A catalog shaped like the real `/v1/agent/models` after a user adds ONE BYOK
// key (here: OpenAI). Only the OpenAI rows are `configured`; every other
// provider is a keyless row the picker shows disabled.
function catalogWithOpenAiKey(): CatalogModel[] {
  return mergeCatalog({
    cloudModels: [
      {
        id: "gpt-5.4-mini",
        provider: "openai",
        model_name: "gpt-5.4-mini",
        name: "GPT-5.4 Mini",
        configured: true,
        supports_streaming: true,
      },
      {
        id: "gpt-5",
        provider: "openai",
        model_name: "gpt-5",
        name: "GPT-5",
        configured: true,
        supports_streaming: true,
      },
      {
        id: "claude-haiku-4-5",
        provider: "anthropic",
        model_name: "claude-haiku-4-5",
        name: "Claude Haiku 4.5",
        configured: false,
        supports_streaming: true,
      },
      {
        id: "openrouter/x",
        provider: "openrouter",
        model_name: "openrouter/x",
        name: "OR X",
        configured: true,
        supports_streaming: true,
      },
    ],
    localModelNames: [],
  });
}

describe("defaultSelectedModelId — provider-aware auto-select", () => {
  it("prefers a just-added provider's first usable model", () => {
    // The regression: without preferProvider the first *globally* usable model
    // was an OpenRouter row, so adding an OpenAI key still selected OpenRouter.
    const models = catalogWithOpenAiKey();
    expect(defaultSelectedModelId(models, { preferProvider: "openai" })).toBe(
      "gpt-5.4-mini",
    );
    expect(
      defaultSelectedModelId(models, { preferProvider: "openrouter" }),
    ).toBe("openrouter/x");
  });

  it("skips a preferred provider whose rows are all keyless (disabled)", () => {
    const models = catalogWithOpenAiKey();
    // Anthropic has no key here → fall through to the backend default / first usable.
    expect(
      defaultSelectedModelId(models, {
        preferProvider: "anthropic",
        defaultModelId: "gpt-5.4-mini",
      }),
    ).toBe("gpt-5.4-mini");
  });

  it("honors the backend default_model_id when it is usable", () => {
    const models = catalogWithOpenAiKey();
    expect(defaultSelectedModelId(models, { defaultModelId: "gpt-5" })).toBe(
      "gpt-5",
    );
  });

  it("falls back to the first usable model, never a disabled one", () => {
    const models = catalogWithOpenAiKey();
    const picked = defaultSelectedModelId(models);
    const row = models.find((m) => m.id === picked);
    expect(row?.configured).toBe(true);
    expect(row?.disabled).not.toBe(true);
  });

  it("ignores an unusable default_model_id", () => {
    const models = catalogWithOpenAiKey();
    // claude-haiku is in the list but keyless → must not be selected.
    const picked = defaultSelectedModelId(models, {
      defaultModelId: "claude-haiku-4-5",
    });
    expect(picked).not.toBe("claude-haiku-4-5");
    expect(models.find((m) => m.id === picked)?.configured).toBe(true);
  });
});
