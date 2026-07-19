// Model catalog for the desktop Run composer's model picker.
//
// Mirrors the web ChatScreen's model-list assembly: a curated cloud set +
// locally-installed Ollama models, with each cloud model marked `configured`
// (selectable) only when the user actually has that provider's BYOK key. Models
// the user can't run are shown disabled rather than hidden, so the picker is an
// honest map of what's available — the same shape the reference (Cursor/Claude)
// picker uses. The Fast/Balanced/Deep depth grid is intentionally not part of
// this (the composer mounts with `depthVisible={false}`).
//
// OQ4 (PRD): the curated cloud list is a static default here, as on web. If a
// real `/v1/...` model catalog lands, swap `CURATED_CLOUD_MODELS` for a fetch.

import type { ModelCatalogModel } from "@0x-copilot/api-types";

export type CatalogModel = ModelCatalogModel & { disabled?: boolean };

/** Provider ids that back a curated model (match `ProviderKeySummary.provider`). */
type CuratedProvider = "openai" | "anthropic" | "gemini";

interface CuratedEntry {
  readonly id: string;
  readonly provider: CuratedProvider;
  readonly model_name: string;
  readonly name: string;
  readonly description: string;
  readonly reasoning?: ModelCatalogModel["reasoning"];
}

const CURATED_CLOUD_MODELS: readonly CuratedEntry[] = [
  {
    id: "gpt-5.4-mini",
    provider: "openai",
    model_name: "gpt-5.4-mini",
    name: "GPT-5.4 Mini",
    description: "Fast OpenAI model",
    reasoning: { enabled: true, effort: "medium", summary: "auto" },
  },
  {
    id: "anthropic/claude-sonnet-4-6",
    provider: "anthropic",
    model_name: "claude-sonnet-4-6",
    name: "Claude Sonnet 4.6",
    description: "Balanced Anthropic model",
    reasoning: { enabled: true, effort: "medium", summary: "auto" },
  },
  {
    id: "anthropic/claude-haiku-4-5",
    provider: "anthropic",
    model_name: "claude-haiku-4-5",
    name: "Claude Haiku 4.5",
    description: "Fast Anthropic model",
  },
  {
    id: "google-ai-studio/gemini-3-flash",
    provider: "gemini",
    model_name: "gemini-3-flash",
    name: "Gemini 3 Flash",
    description: "Google long-context model",
  },
];

/** A locally-installed Ollama model, mapped to a catalog entry. */
function localModel(name: string): CatalogModel {
  return {
    id: name,
    provider: "ollama",
    model_name: name,
    name,
    description: "Local model",
    configured: true,
    supports_streaming: true,
  };
}

/**
 * Assemble the picker list. `configuredProviders` is the set of BYOK providers
 * the user has a key for; a curated cloud model is `configured`/enabled only
 * when its provider is in that set. `localModelNames` are always configured.
 * When the provider-key probe couldn't run (`providersKnown === false`), the
 * curated set fails open (all selectable) so a configured user is never blocked
 * — the run-start error path is the backstop if a key is truly missing.
 */
export function buildModelCatalog(args: {
  readonly configuredProviders: ReadonlySet<string>;
  readonly providersKnown: boolean;
  readonly localModelNames: readonly string[];
}): CatalogModel[] {
  const { configuredProviders, providersKnown, localModelNames } = args;
  const cloud: CatalogModel[] = CURATED_CLOUD_MODELS.map((entry) => {
    const configured =
      !providersKnown || configuredProviders.has(entry.provider);
    return {
      id: entry.id,
      provider: entry.provider,
      model_name: entry.model_name,
      name: entry.name,
      description: entry.description,
      configured,
      supports_streaming: true,
      ...(entry.reasoning ? { reasoning: entry.reasoning } : {}),
      disabled: !configured,
    };
  });
  const local = localModelNames.map(localModel);
  return [...cloud, ...local];
}

/** First selectable (configured, non-disabled) model, else the first entry. */
export function defaultSelectedModelId(
  models: readonly CatalogModel[],
): string {
  const usable = models.find((m) => m.configured && m.disabled !== true);
  return (usable ?? models[0])?.id ?? "";
}

/** Wire `model` selection for a run-create body, resolved from the picked id. */
export interface ModelSelectionWire {
  readonly provider?: string;
  readonly model_name?: string;
  readonly reasoning?: ModelCatalogModel["reasoning"] | null;
}

export function modelSelectionForId(
  models: readonly CatalogModel[],
  id: string,
): ModelSelectionWire | null {
  if (id === "") {
    return null;
  }
  const model = models.find((m) => m.id === id);
  if (!model) {
    // Unknown id (e.g. a just-added custom slug not yet in the list) — send the
    // bare model name so the runtime can still resolve it.
    return { model_name: id };
  }
  return {
    provider: model.provider,
    model_name: model.model_name,
    reasoning: model.reasoning ?? null,
  };
}
