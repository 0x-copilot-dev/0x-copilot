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

// The standby (no-active-run) composer calls `defaultSelectedModelId` with NO
// `preferProvider`, so the provider-priority walk is the only thing standing
// between the user and the backend's default OpenAI item — which the catalog
// puts FIRST. The live bug: a user with only an Anthropic key saw "GPT-5.4
// Mini" preselected because the fallback returned `models[0]` (the keyless
// default) instead of the one model they could actually run.
describe("defaultSelectedModelId — provider priority among configured", () => {
  const openaiDefault = (configured: boolean): CatalogModel => ({
    id: "gpt-5.4-mini",
    provider: "openai",
    model_name: "gpt-5.4-mini",
    name: "GPT-5.4 Mini",
    configured,
    supports_streaming: true,
    disabled: !configured,
  });
  const claude = (configured: boolean): CatalogModel => ({
    id: "claude-sonnet-4-5",
    provider: "anthropic",
    model_name: "claude-sonnet-4-5",
    name: "Claude Sonnet 4.5",
    configured,
    supports_streaming: true,
    disabled: !configured,
  });
  const openrouter = (configured: boolean): CatalogModel => ({
    id: "openrouter/auto",
    provider: "openrouter",
    model_name: "openrouter/auto",
    name: "OpenRouter Auto",
    configured,
    supports_streaming: true,
    disabled: !configured,
  });
  const gemini = (configured: boolean): CatalogModel => ({
    id: "gemini-2.5-pro",
    provider: "google",
    model_name: "gemini-2.5-pro",
    name: "Gemini 2.5 Pro",
    configured,
    supports_streaming: true,
    disabled: !configured,
  });

  it("never preselects the keyless OpenAI default; picks Claude when only Anthropic is configured", () => {
    // Backend default (gpt-5.4-mini) is FIRST but keyless; only Anthropic has a
    // key. The old `firstUsable ?? models[0]` could surface the OpenAI default.
    const models = [openaiDefault(false), claude(true), openrouter(false)];
    expect(defaultSelectedModelId(models)).toBe("claude-sonnet-4-5");
  });

  it("respects OpenAI > Anthropic > OpenRouter > Gemini when several are configured", () => {
    const all = [
      openaiDefault(true),
      claude(true),
      openrouter(true),
      gemini(true),
    ];
    expect(defaultSelectedModelId(all)).toBe("gpt-5.4-mini");
    // Drop OpenAI's key → Anthropic wins.
    expect(
      defaultSelectedModelId([
        openaiDefault(false),
        claude(true),
        openrouter(true),
        gemini(true),
      ]),
    ).toBe("claude-sonnet-4-5");
    // Only OpenRouter + Gemini configured → OpenRouter wins.
    expect(
      defaultSelectedModelId([
        openaiDefault(false),
        claude(false),
        openrouter(true),
        gemini(true),
      ]),
    ).toBe("openrouter/auto");
    // Only Gemini configured → Gemini.
    expect(
      defaultSelectedModelId([
        openaiDefault(false),
        claude(false),
        openrouter(false),
        gemini(true),
      ]),
    ).toBe("gemini-2.5-pro");
  });

  it("preferProvider still wins over the priority order", () => {
    const models = [openaiDefault(true), claude(true)];
    expect(
      defaultSelectedModelId(models, { preferProvider: "anthropic" }),
    ).toBe("claude-sonnet-4-5");
  });

  it("falls back to a usable non-priority provider (local) when no priority provider is configured", () => {
    const models = mergeCatalog({
      cloudModels: [openaiDefault(false), claude(false)],
      localModelNames: ["qwen3:4b"],
    });
    expect(defaultSelectedModelId(models)).toBe("qwen3:4b");
  });

  it('returns "" — never an unusable entry — when nothing is configured', () => {
    const models = [openaiDefault(false), claude(false), openrouter(false)];
    expect(defaultSelectedModelId(models)).toBe("");
  });

  it("prefers defaultModelId within the winning priority provider", () => {
    const models: CatalogModel[] = [
      openaiDefault(true),
      {
        id: "gpt-5",
        provider: "openai",
        model_name: "gpt-5",
        name: "GPT-5",
        configured: true,
        supports_streaming: true,
      },
      claude(true),
    ];
    // OpenAI wins the priority; the default_model_id selects WITHIN it.
    expect(defaultSelectedModelId(models, { defaultModelId: "gpt-5" })).toBe(
      "gpt-5",
    );
  });
});
