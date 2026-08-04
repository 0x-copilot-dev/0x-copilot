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

  it("never auto-picks the most expensive row when the provider has a ladder", () => {
    // The reported bug, in catalog order: an Anthropic-only user opened on
    // "Claude Fable 5" — the creative-writing line, which is BOTH first
    // alphabetically and the dearest model Anthropic sells. It is off the size
    // ladder (`tier: null`), so it must never be the auto-pick while a real rung
    // is usable. The mid rung (Sonnet) is the default; Fable stays one click
    // away in the pill.
    const anthropic: CatalogModel[] = [
      {
        id: "claude-fable-5",
        provider: "anthropic",
        model_name: "claude-fable-5",
        name: "Claude Fable 5",
        configured: true,
        supports_streaming: true,
        tier: null,
        output_cost_per_mtok: 90,
      },
      {
        id: "claude-haiku-4-5",
        provider: "anthropic",
        model_name: "claude-haiku-4-5",
        name: "Claude Haiku 4.5",
        configured: true,
        supports_streaming: true,
        tier: "small",
        output_cost_per_mtok: 5,
      },
      {
        id: "claude-opus-5",
        provider: "anthropic",
        model_name: "claude-opus-5",
        name: "Claude Opus 5",
        configured: true,
        supports_streaming: true,
        tier: "big",
        output_cost_per_mtok: 75,
      },
      {
        id: "claude-sonnet-5",
        provider: "anthropic",
        model_name: "claude-sonnet-5",
        name: "Claude Sonnet 5",
        configured: true,
        supports_streaming: true,
        tier: "medium",
        output_cost_per_mtok: 15,
      },
    ];
    expect(defaultSelectedModelId(anthropic)).toBe("claude-sonnet-5");
    // Same ranking on the just-added-key path.
    expect(
      defaultSelectedModelId(anthropic, { preferProvider: "anthropic" }),
    ).toBe("claude-sonnet-5");
    // …and on the no-priority-provider fallback (here: an unranked provider).
    expect(
      defaultSelectedModelId(
        anthropic.map((m) => ({ ...m, provider: "someone-else" })),
      ),
    ).toBe("claude-sonnet-5");
  });

  // The preferred rung is per-provider, because the vendors disagree about
  // which one is "the everyday model". These two lineups are the real ladders
  // the backend derives from models.dev (`gpt-luna`/`gpt-terra`/`gpt-sol` and
  // `claude-haiku`/`claude-sonnet`/`claude-opus`), so one global tier order
  // cannot satisfy both.
  const openai56 = (): CatalogModel[] => [
    {
      id: "gpt-5.6-luna",
      provider: "openai",
      model_name: "gpt-5.6-luna",
      name: "GPT-5.6 Luna",
      configured: true,
      supports_streaming: true,
      tier: "small",
      output_cost_per_mtok: 1.2,
    },
    {
      id: "gpt-5.6-terra",
      provider: "openai",
      model_name: "gpt-5.6-terra",
      name: "GPT-5.6 Terra",
      configured: true,
      supports_streaming: true,
      tier: "medium",
      output_cost_per_mtok: 12,
    },
    {
      id: "gpt-5.6",
      provider: "openai",
      model_name: "gpt-5.6",
      name: "GPT-5.6",
      configured: true,
      supports_streaming: true,
      tier: "big",
      output_cost_per_mtok: 30,
    },
  ];

  const anthropicLadder = (): CatalogModel[] => [
    {
      id: "claude-haiku-4-5",
      provider: "anthropic",
      model_name: "claude-haiku-4-5",
      name: "Claude Haiku 4.5",
      configured: true,
      supports_streaming: true,
      tier: "small",
      output_cost_per_mtok: 5,
    },
    {
      id: "claude-sonnet-5",
      provider: "anthropic",
      model_name: "claude-sonnet-5",
      name: "Claude Sonnet 5",
      configured: true,
      supports_streaming: true,
      tier: "medium",
      output_cost_per_mtok: 10,
    },
    {
      id: "claude-opus-5",
      provider: "anthropic",
      model_name: "claude-opus-5",
      name: "Claude Opus 5",
      configured: true,
      supports_streaming: true,
      tier: "big",
      output_cost_per_mtok: 25,
    },
  ];

  it("opens a fresh OpenAI key on Luna, not the mid rung", () => {
    // OpenAI's 5.6 line is luna/terra/sol; Luna is the everyday model and the
    // deployment default (RUNTIME_DEFAULT_MODEL), at ~1/10th Terra's output
    // cost. The generic mid-rung-first order would pick Terra here.
    const models = openai56();
    expect(defaultSelectedModelId(models, { preferProvider: "openai" })).toBe(
      "gpt-5.6-luna",
    );
    expect(defaultSelectedModelId(models)).toBe("gpt-5.6-luna");
  });

  it("keeps Anthropic on the mid rung while OpenAI prefers the small one", () => {
    // The whole reason the order is per-provider: same shaped ladders, and the
    // correct default sits on a different rung in each.
    expect(
      defaultSelectedModelId(anthropicLadder(), {
        preferProvider: "anthropic",
      }),
    ).toBe("claude-sonnet-5");
    expect(
      defaultSelectedModelId(openai56(), { preferProvider: "openai" }),
    ).toBe("gpt-5.6-luna");
  });

  it("ranks each row against its own provider's order on a mixed list", () => {
    // The last fallback ranks a mixed-provider list. Each row must be scored by
    // ITS provider's preferred rung, so an Anthropic row is not judged by
    // OpenAI's small-first order (which would hand the pick to Haiku).
    const mixed = [...anthropicLadder(), ...openai56()];
    // OpenAI wins on PROVIDER_PRIORITY, and within OpenAI the small rung wins.
    expect(defaultSelectedModelId(mixed)).toBe("gpt-5.6-luna");
    // With OpenAI unusable, Anthropic still resolves to its OWN preferred rung.
    const anthropicOnly = mixed.map((m) =>
      m.provider === "openai" ? { ...m, configured: false } : m,
    );
    expect(defaultSelectedModelId(anthropicOnly)).toBe("claude-sonnet-5");
  });

  it("falls to the small rung when the provider ships no mid one", () => {
    const models: CatalogModel[] = [
      {
        id: "specialty",
        provider: "anthropic",
        model_name: "specialty",
        name: "Specialty",
        configured: true,
        supports_streaming: true,
        output_cost_per_mtok: 120,
      },
      {
        id: "flagship",
        provider: "anthropic",
        model_name: "flagship",
        name: "Flagship",
        configured: true,
        supports_streaming: true,
        tier: "big",
        output_cost_per_mtok: 75,
      },
      {
        id: "cheap",
        provider: "anthropic",
        model_name: "cheap",
        name: "Cheap",
        configured: true,
        supports_streaming: true,
        tier: "small",
        output_cost_per_mtok: 5,
      },
    ];
    expect(defaultSelectedModelId(models)).toBe("cheap");
  });

  it("auto-picks an off-ladder row only when nothing on the ladder is usable", () => {
    const models: CatalogModel[] = [
      {
        id: "claude-opus-5",
        provider: "anthropic",
        model_name: "claude-opus-5",
        name: "Claude Opus 5",
        configured: false,
        disabled: true,
        supports_streaming: true,
        tier: "big",
      },
      {
        id: "claude-fable-5",
        provider: "anthropic",
        model_name: "claude-fable-5",
        name: "Claude Fable 5",
        configured: true,
        supports_streaming: true,
        tier: null,
      },
    ];
    expect(defaultSelectedModelId(models)).toBe("claude-fable-5");
  });

  it("breaks a same-rung tie on output cost, and keeps catalog order when unpriced", () => {
    const rung = (id: string, cost?: number): CatalogModel => ({
      id,
      provider: "openai",
      model_name: id,
      name: id,
      configured: true,
      supports_streaming: true,
      tier: "medium",
      output_cost_per_mtok: cost,
    });
    expect(defaultSelectedModelId([rung("dear", 30), rung("cheap", 8)])).toBe(
      "cheap",
    );
    // A priced row beats an unpriced one; with neither priced, catalog order
    // stands (the offline LiteLLM fallback publishes no costs at all).
    expect(defaultSelectedModelId([rung("unpriced"), rung("priced", 30)])).toBe(
      "priced",
    );
    expect(defaultSelectedModelId([rung("first"), rung("second")])).toBe(
      "first",
    );
  });

  it("still lets the backend default_model_id win inside the winning provider", () => {
    // The deployment's declared default is an explicit choice; tier ranking is
    // only the tiebreak for when there is none.
    const models: CatalogModel[] = [
      {
        id: "gpt-mid",
        provider: "openai",
        model_name: "gpt-mid",
        name: "GPT Mid",
        configured: true,
        supports_streaming: true,
        tier: "medium",
      },
      {
        id: "gpt-flagship",
        provider: "openai",
        model_name: "gpt-flagship",
        name: "GPT Flagship",
        configured: true,
        supports_streaming: true,
        tier: "big",
      },
    ];
    expect(
      defaultSelectedModelId(models, { defaultModelId: "gpt-flagship" }),
    ).toBe("gpt-flagship");
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
