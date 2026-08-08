// Models-catalog data seam (PR-3D Settings → Models).
//
// The Models page curates which catalog models appear in the composer picker.
// It reads the live catalog (`GET /v1/agent/models`, each item carrying a
// server-computed `enabled` flag from the workspace's curation) and writes the
// curation back onto the workspace defaults (`PUT /v1/agent/workspace/defaults`
// — a full-document replace, so the port read-merge-writes).
//
// Substrate-agnostic: no bare `fetch`/`window` — the adapter only builds typed
// requests and calls the injected `Transport.request()`.

import type {
  ModelCatalogModel,
  ModelCatalogResponse,
  UpdateWorkspaceDefaultsRequest,
  WorkspaceDefaultsResponse,
} from "@0x-copilot/api-types";

import type { Transport } from "../../ports/Transport";

export type CatalogModel = ModelCatalogModel;

/**
 * The host-callback seam the Models page depends on. `list` returns the
 * catalog (with per-item `enabled`); `setEnabled` persists the curation.
 * `null` clears curation back to the newest-per-provider default.
 */
export interface ModelsPort {
  /** `GET /v1/agent/models` — catalog with per-item enabled flags. */
  list(signal?: AbortSignal): Promise<readonly CatalogModel[]>;
  /**
   * Persist the enabled set as the workspace curation. `enabledIds` is the
   * full list of ids the workspace wants enabled; `null` restores the default
   * heuristic. Returns the refreshed catalog so the page reflects the server's
   * recomputed flags (local + default models stay enabled regardless).
   */
  setEnabled(
    enabledIds: readonly string[] | null,
    signal?: AbortSignal,
  ): Promise<readonly CatalogModel[]>;
}

/** Default `ModelsPort` backed by the injected `Transport`. */
export function createModelsPort(transport: Transport): ModelsPort {
  async function list(signal?: AbortSignal): Promise<readonly CatalogModel[]> {
    const res = await transport.request<ModelCatalogResponse>({
      method: "GET",
      path: "/v1/agent/models",
      signal,
    });
    return res.models;
  }
  return {
    list,
    async setEnabled(enabledIds, signal) {
      // Full-document replace: read current defaults, swap only enabled_models.
      const current = await transport.request<WorkspaceDefaultsResponse>({
        method: "GET",
        path: "/v1/agent/workspace/defaults",
        signal,
      });
      const body: UpdateWorkspaceDefaultsRequest = {
        default_model: current.default_model,
        default_connectors: current.default_connectors,
        retention_days: current.retention_days,
        behavior_overrides: current.behavior_overrides,
        enabled_models: enabledIds === null ? null : [...enabledIds],
      };
      await transport.request<WorkspaceDefaultsResponse>({
        method: "PUT",
        path: "/v1/agent/workspace/defaults",
        body,
        signal,
      });
      return list(signal);
    },
  };
}

// ---------------------------------------------------------------------------
// Pure view helpers (tested directly).
// ---------------------------------------------------------------------------

/** Provider display label — a stable, human-readable name per slug. */
export function providerLabel(provider: string): string {
  const known: Record<string, string> = {
    openai: "OpenAI",
    anthropic: "Anthropic",
    gemini: "Google Gemini",
    google: "Google Gemini",
    openrouter: "OpenRouter",
    virtuals: "Virtuals",
    groq: "Groq",
    xai: "xAI",
    ollama: "Local · Ollama",
  };
  return known[provider] ?? provider;
}

export interface ModelGroup<T extends ModelCatalogModel = CatalogModel> {
  readonly provider: string;
  readonly label: string;
  readonly models: readonly T[];
}

const PROVIDER_ORDER = [
  "openai",
  "anthropic",
  "gemini",
  "google",
  "openrouter",
  "groq",
  "xai",
  "ollama",
];

/**
 * Group catalog models by provider for display. Providers are ordered by a
 * stable priority (the common cloud providers first, local last), then any
 * unknown providers alphabetically; models keep their catalog order (already
 * newest-first from the server). Generic so callers keep any extra fields
 * (e.g. the composer's per-row `disabled`).
 */
export function groupModelsByProvider<T extends ModelCatalogModel>(
  models: readonly T[],
): readonly ModelGroup<T>[] {
  const byProvider = new Map<string, T[]>();
  for (const model of models) {
    const bucket = byProvider.get(model.provider);
    if (bucket) bucket.push(model);
    else byProvider.set(model.provider, [model]);
  }
  const rank = (provider: string): number => {
    const index = PROVIDER_ORDER.indexOf(provider);
    return index === -1 ? PROVIDER_ORDER.length : index;
  };
  return [...byProvider.keys()]
    .sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
    .map((provider) => ({
      provider,
      label: providerLabel(provider),
      models: sortModelsByReleaseDate(byProvider.get(provider) ?? []),
    }));
}

/**
 * Newest release first, so Settings → Models reads as a timeline rather than an
 * alphabetical jumble that files `gpt-3.5-turbo` above `gpt-5.6`.
 *
 * A model with NO release date sorts last, never first: absent means "the
 * source didn't say" (the offline LiteLLM fallback publishes no release dates
 * at all), and guessing "very old" would bury a brand-new model while guessing
 * "very new" would float every unknown to the top. Ties fall back to name so
 * the order is stable across renders.
 */
export function sortModelsByReleaseDate<T extends ModelCatalogModel>(
  models: readonly T[],
): T[] {
  return [...models].sort((a, b) => {
    const left = a.release_date ?? "";
    const right = b.release_date ?? "";
    if (left !== right) {
      if (left === "") return 1;
      if (right === "") return -1;
      return right.localeCompare(left);
    }
    return a.name.localeCompare(b.name);
  });
}

/** Filter models by a case-insensitive query over id / name / provider. */
export function filterModels<T extends ModelCatalogModel>(
  models: readonly T[],
  query: string,
): readonly T[] {
  const q = query.trim().toLowerCase();
  if (q === "") return models;
  return models.filter(
    (model) =>
      model.id.toLowerCase().includes(q) ||
      model.name.toLowerCase().includes(q) ||
      model.provider.toLowerCase().includes(q),
  );
}

/** Compact price label from a per-1M-token input cost. */
export function priceLabel(model: CatalogModel): string | null {
  const input = model.input_cost_per_mtok;
  if (input === null || input === undefined) return null;
  if (input === 0) return "Free";
  return `$${input.toFixed(2)}/M in`;
}

/**
 * Release month, e.g. "Jul 2026". Month precision on purpose: the exact day is
 * noise for choosing a model, and the full ISO date is already the sort key.
 * Returns `null` when the source published no date, so the row simply omits it
 * rather than showing an "unknown" placeholder on every LiteLLM-fallback row.
 */
export function releaseLabel(model: CatalogModel): string | null {
  const raw = model.release_date;
  if (raw === null || raw === undefined || raw === "") return null;
  // Parse the ISO parts directly — `new Date("2026-07-24")` is UTC-midnight and
  // renders as the PREVIOUS month for anyone west of Greenwich on the 1st.
  const match = /^(\d{4})-(\d{2})/.exec(raw);
  if (match === null) return null;
  const monthIndex = Number(match[2]) - 1;
  if (monthIndex < 0 || monthIndex > 11) return null;
  return `${MONTHS[monthIndex]} ${match[1]}`;
}

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

/** Compact context-window label, e.g. "128K ctx". */
export function contextLabel(model: CatalogModel): string | null {
  const ctx = model.context_window;
  if (ctx === null || ctx === undefined) return null;
  if (ctx >= 1_000_000) return `${Math.round(ctx / 1_000_000)}M ctx`;
  if (ctx >= 1_000) return `${Math.round(ctx / 1_000)}K ctx`;
  return `${ctx} ctx`;
}
