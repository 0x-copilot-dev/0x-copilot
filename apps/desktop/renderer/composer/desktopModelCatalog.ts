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
 * Which size rung an AUTO-selected model should land on, best first.
 *
 * `tier` is the backend's rung within a provider's general-purpose ladder
 * (`agent_runtime/api/model_tiers.py`): `small` the cheap one, `medium` the
 * middle, `big` the flagship. A row with NO tier is off that ladder — the
 * specialty and max-reasoning lines (`claude-fable`, `gpt-pro`), which are the
 * DEAREST rows a provider publishes — so it ranks last and can only be
 * auto-selected when nothing else is usable.
 *
 * The bug this fixes: the pick was `usableForProvider[0]`, i.e. catalog order,
 * which for an Anthropic-only user meant "Claude Fable 5" — the most expensive
 * model in the list — silently reselected on every fresh mount. A default
 * should cost the user the least surprise, not the most money; an explicit pick
 * (remembered per chat) is how anyone gets the flagship.
 *
 * WHY THIS IS PER-PROVIDER: "the everyday rung" is a fact about a vendor's
 * lineup, not a universal one, and the two disagree today. Anthropic's everyday
 * model is Sonnet (`medium`), one rung above Haiku. OpenAI's 5.6 line is
 * luna/terra/sol, and the everyday model is Luna — the `small` rung, and ~10x
 * cheaper than Terra on output. Encoding one global order forces one of the two
 * to be wrong, so the order is looked up per provider with
 * {@link DEFAULT_TIER_ORDER} covering everyone not named here.
 */
type Tier = NonNullable<CatalogModel["tier"]>;

const DEFAULT_TIER_ORDER: readonly Tier[] = ["medium", "small", "big"];

const TIER_ORDER_BY_PROVIDER: Readonly<Record<string, readonly Tier[]>> = {
  // Luna is the 5.6 line's everyday model and the deployment default
  // (`RUNTIME_DEFAULT_MODEL`), so a fresh OpenAI key opens on it, not on Terra.
  openai: ["small", "medium", "big"],
};

function tierOrderFor(provider: string | null | undefined): readonly Tier[] {
  return (
    (provider !== null && provider !== undefined
      ? TIER_ORDER_BY_PROVIDER[provider]
      : undefined) ?? DEFAULT_TIER_ORDER
  );
}

/** Rank of a model's tier for auto-selection; off-ladder rows sort last. */
function tierRank(model: CatalogModel, order: readonly Tier[]): number {
  const tier = model.tier ?? null;
  const index = tier === null ? -1 : order.indexOf(tier);
  return index === -1 ? order.length : index;
}

/**
 * The best auto-pick from an already-filtered (usable) list: the preferred rung
 * first, then lower output cost, then catalog order. Each row is ranked against
 * ITS OWN provider's order, so this stays correct on the mixed-provider list the
 * last fallback passes in. Cost is the tiebreak WITHIN a rung, and an unpriced
 * row (the offline LiteLLM fallback publishes no costs) sorts after a priced one
 * — so a catalog with no tier or cost data at all degrades to the previous
 * behaviour, first-usable-in-catalog-order.
 */
function bestAutoSelect(
  models: readonly CatalogModel[],
): CatalogModel | undefined {
  let best: CatalogModel | undefined;
  let bestRank = Number.POSITIVE_INFINITY;
  let bestCost = Number.POSITIVE_INFINITY;
  for (const model of models) {
    const rank = tierRank(model, tierOrderFor(model.provider));
    const cost = model.output_cost_per_mtok ?? Number.POSITIVE_INFINITY;
    if (rank < bestRank || (rank === bestRank && cost < bestCost)) {
      best = model;
      bestRank = rank;
      bestCost = cost;
    }
  }
  return best;
}

/**
 * Pick the default model id. Priority — provider-aware auto-select so that
 * "add a key → the matching model is picked and usable" holds instead of
 * leaving a keyless or wrong-provider default selected:
 *   1. `preferProvider` — a just-added provider's model (BYOK: an OpenAI key →
 *      an OpenAI model, an Anthropic key → a Claude model, an OpenRouter key →
 *      an OpenRouter model).
 *   2. PROVIDER_PRIORITY (OpenAI > Anthropic > OpenRouter > Gemini): the first
 *      provider with a usable model wins — preferring `defaultModelId` when it
 *      belongs to that provider, else that provider's best auto-pick. This is
 *      what stops the OpenAI default from being preselected when the user has no
 *      OpenAI key.
 *   3. the best usable (configured, non-disabled) model of ANY provider (covers
 *      local/ollama and any provider outside the priority list).
 *   4. "" — nothing usable yet. NEVER an unusable entry: returning a keyless
 *      `models[0]` is exactly the bug this replaces; the run-start gate is the
 *      backstop for an empty selection.
 * "Usable" = configured AND not disabled. "Best" is {@link bestAutoSelect} —
 * the provider's preferred rung first (see {@link TIER_ORDER_BY_PROVIDER}),
 * never an off-ladder specialty row; this is a DEFAULT, so it must not open on
 * the priciest model a provider sells.
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
    const byProvider = bestAutoSelect(
      models.filter((m) => m.provider === opts.preferProvider && usable(m)),
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
    // Always resolves — the list is non-empty by the guard above. Falling
    // through rather than asserting keeps a surprise here degrading to the next
    // provider instead of a crash.
    const best = bestAutoSelect(usableForProvider);
    if (best !== undefined) return best.id;
  }
  // No priority provider is configured — fall back to the best usable model of
  // any provider (local/ollama/other). Never an unusable entry.
  return bestAutoSelect(models.filter(usable))?.id ?? "";
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
