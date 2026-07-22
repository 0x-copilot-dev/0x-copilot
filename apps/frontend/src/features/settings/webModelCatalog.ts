// Model catalog for the web Settings → Model & behavior default-model select
// (D5 web-convergence capstone).
//
// This is the WEB half of the PRD-03 FR-4 "pure catalog→option projection …
// duplicated (not shared) across the two binders, over api-types shapes". It is
// the deliberate lockstep twin of the desktop
// `apps/desktop/renderer/composer/desktopModelCatalog.ts` builder: the same
// curated cloud set, the same key-gating rule (a model is selectable only when
// the user actually holds that provider's BYOK key), the same fail-open when the
// provider probe couldn't run. Kept a separate copy because `apps/frontend` may
// not import from `apps/desktop` (hard service-boundary rule) — the two are
// synchronised by contract, not by a shared module.
//
// Pure data + pure functions; no React, no `fetch`, no globals.

import type { ModelCatalogModel } from "@0x-copilot/api-types";

/** A catalog entry with a UI `disabled` flag (keyless cloud model). */
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

// The curated cloud set — byte-parallel to the desktop builder's
// CURATED_CLOUD_MODELS so both hosts offer the same default-model options.
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

/**
 * Assemble the default-model catalog. `configuredProviders` is the set of BYOK
 * providers the user holds a key for; a curated cloud model is enabled only when
 * its provider is in that set. When the provider-key probe couldn't run
 * (`providersKnown === false`), the curated set fails open (all selectable) so a
 * configured user is never blocked — the run-start credential gate is the
 * backstop if a key is truly missing.
 */
export function buildWebModelCatalog(args: {
  readonly configuredProviders: ReadonlySet<string>;
  readonly providersKnown: boolean;
}): CatalogModel[] {
  const { configuredProviders, providersKnown } = args;
  return CURATED_CLOUD_MODELS.map((entry) => {
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
}
