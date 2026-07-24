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
 * Pick the default model id. Priority — provider-aware auto-select so that
 * "add a key → the matching model is picked and usable" holds instead of
 * leaving a keyless or wrong-provider default selected:
 *   1. `preferProvider` — the first usable model of a just-added provider
 *      (BYOK: an OpenAI key → an OpenAI model, an Anthropic key → a Claude
 *      model, an OpenRouter key → an OpenRouter model).
 *   2. `defaultModelId` — the backend catalog's `default_model_id` when it is
 *      itself usable (env ∪ BYOK configured).
 *   3. the first usable (configured, non-disabled) model.
 *   4. the first entry (nothing usable yet — the run-start gate is the backstop).
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
  if (opts?.defaultModelId) {
    const byDefault = models.find(
      (m) => m.id === opts.defaultModelId && usable(m),
    );
    if (byDefault) return byDefault.id;
  }
  const firstUsable = models.find(usable);
  return (firstUsable ?? models[0])?.id ?? "";
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
