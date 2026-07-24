// Model catalog helpers for the desktop composers + Settings model-select.
//
// The cloud model list is NOT hardcoded here: every consumer fetches the one
// backend catalog (GET /v1/agent/models), whose per-item `configured` flag
// already reflects env keys ∪ the user's BYOK keys (the same credential truth
// the run-create gate uses). `mergeCatalog` folds that fetched cloud list
// together with locally-installed Ollama models into the picker shape. This
// replaced the old per-host `CURATED_CLOUD_MODELS` + separate provider-key
// probe, so desktop and web now read the identical source with no drift.

import type { ModelCatalogModel } from "@0x-copilot/api-types";

/** A catalog entry with a UI `disabled` flag (keyless cloud model). */
export type CatalogModel = ModelCatalogModel & { disabled?: boolean };

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
 * Assemble the picker list from the fetched backend catalog + local models.
 * `cloudModels` are `/v1/agent/models` items whose `configured` flag is
 * authoritative (env ∪ BYOK); a cloud model whose provider the user can't run
 * is shown disabled rather than hidden, so the picker is an honest map of what's
 * available. `localModelNames` are always configured.
 */
export function mergeCatalog(args: {
  readonly cloudModels: readonly ModelCatalogModel[];
  readonly localModelNames: readonly string[];
}): CatalogModel[] {
  const cloud: CatalogModel[] = args.cloudModels.map((m) => ({
    ...m,
    disabled: m.configured === false,
  }));
  const local = args.localModelNames.map(localModel);
  return [...cloud, ...local];
}

/**
 * Provider preference order for auto-select among CONFIGURED providers, applied
 * only to USABLE models. The backend catalog puts the deployment default item
 * (gpt-5.4-mini) first, so a naive `models[0]` fallback preselected OpenAI even
 * for a user with only an Anthropic key. This explicit order picks the first
 * provider that actually has a usable model, honouring the user's stated
 * priority: OpenAI > Anthropic > OpenRouter > Gemini(google).
 */
const PROVIDER_PRIORITY: readonly string[] = [
  "openai",
  "anthropic",
  "openrouter",
  "google",
];

/**
 * Pick the default model id. Priority — provider-aware auto-select so that
 * "add a key → the matching model is picked and usable" holds instead of
 * leaving a keyless or wrong-provider default selected:
 *   1. `preferProvider` — the first usable model of a just-added provider
 *      (BYOK: an OpenAI key → an OpenAI model, an Anthropic key → a Claude
 *      model, an OpenRouter key → an OpenRouter model).
 *   2. PROVIDER_PRIORITY (OpenAI > Anthropic > OpenRouter > Gemini): the first
 *      provider with a usable model wins — preferring `defaultModelId` when it
 *      belongs to that provider, else that provider's first usable model. This
 *      is what stops the OpenAI default from being preselected when the user has
 *      no OpenAI key.
 *   3. the first usable (configured, non-disabled) model of ANY provider (covers
 *      local/ollama and any provider outside the priority list).
 *   4. "" — nothing usable yet. NEVER an unusable entry: returning a keyless
 *      `models[0]` is exactly the bug this replaces; the run-start gate is the
 *      backstop for an empty selection.
 * "Usable" = configured AND not disabled.
 */
export function defaultSelectedModelId(
  models: readonly CatalogModel[],
  opts?: {
    readonly preferProvider?: string | null;
    readonly defaultModelId?: string | null;
  },
): string {
  const usable = (m: CatalogModel): boolean =>
    m.configured === true && m.disabled !== true;
  if (opts?.preferProvider) {
    const byProvider = models.find(
      (m) => m.provider === opts.preferProvider && usable(m),
    );
    if (byProvider) return byProvider.id;
  }
  // Walk the provider priority; the first provider with any usable model wins.
  for (const provider of PROVIDER_PRIORITY) {
    const usableForProvider = models.filter(
      (m) => m.provider === provider && usable(m),
    );
    if (usableForProvider.length === 0) continue;
    if (opts?.defaultModelId) {
      const byDefault = usableForProvider.find(
        (m) => m.id === opts.defaultModelId,
      );
      if (byDefault) return byDefault.id;
    }
    return usableForProvider[0].id;
  }
  // No priority provider is configured — fall back to the first usable model of
  // any provider (local/ollama/other). Never an unusable entry.
  const firstUsable = models.find(usable);
  return firstUsable?.id ?? "";
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
