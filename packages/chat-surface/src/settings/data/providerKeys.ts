// Provider-keys data seam (DESIGN-SPEC §4 Provider keys · PRD PR-5.4).
//
// The plaintext BYOK key is NEVER held or persisted inside chat-surface. It
// travels through the injected `ProviderKeysPort` exactly once — in the PUT
// body of `save()` — and every read carries only the masked `key_hint` (wire
// contract: packages/api-types/src/providerKeys.ts, "do not add a reveal
// field"). The page depends on the PORT, not on `Transport` directly, so key
// storage / validation is a host concern the substrate injects. This keeps the
// page trivially testable (mock the port) and honest about where secrets live.
//
// `createProviderKeysPort(transport)` is the default Transport-backed adapter
// against the facade `/v1/settings/provider-keys` routes. Tests and alternative
// substrates pass their own `ProviderKeysPort`.
//
// Substrate-agnostic: no bare `fetch`/`window` — the adapter only builds
// `TypedRequest` objects and calls the injected `Transport.request()`.

import type {
  ListProviderKeysResponse,
  ProviderKeySummary,
  PutProviderKeyRequest,
  UpdateWorkspaceDefaultsRequest,
  ValidateProviderKeyRequest,
  ValidateProviderKeyResponse,
  WorkspaceDefaultsResponse,
} from "@0x-copilot/api-types";

import type { Transport } from "../../ports/Transport";

// ---------------------------------------------------------------------------
// Provider catalog — DESIGN-SPEC §4 provider set + per-provider default-model
// options for the Add-key flow's step 3. Data-driven so a host can extend or
// override it (e.g. once a self-hosted OpenAI-compatible endpoint is added).
// ---------------------------------------------------------------------------

export interface ProviderCatalogEntry {
  /** Provider slug — the `/v1/settings/provider-keys/{id}` path segment. */
  readonly id: string;
  readonly label: string;
  /** Masked-input placeholder for the Add flow (e.g. "sk-…"). */
  readonly placeholder: string;
  /** Documented key prefix for the client-side format check (see providerKeys.ts). */
  readonly keyPrefix?: string;
  /** Per-provider default-model options offered at step 3 of the Add flow. */
  readonly models: readonly string[];
  /**
   * True when the shipped `ProviderKeyProvider` union + facade back this slug
   * (openai / anthropic / google / openrouter). Groq & xAI are OpenAI-wire
   * compatible but NOT yet in the union — the default Transport adapter will
   * send the slug and the facade 422s until the union+facade widen (PRD §5.5,
   * flagged gap #5). They are surfaced under the "OpenAI-compatible endpoint"
   * affordance so the UI never silently pretends they persist.
   */
  readonly contractBacked: boolean;
  /**
   * True when the provider is in the catalog for discoverability but the
   * backend `ProviderName` enum + `live_validator` do not accept it yet, so a
   * `save()` would 422 (PRD-F FR-F.6, gap #5). The page renders the row but
   * disables "Add key" so no CTA ever dead-ends in a 422. Widen the backend
   * enum + validator + the DB CHECK migration to flip a provider off this flag.
   */
  readonly comingSoon?: boolean;
}

// Model lists are catalog defaults (DESIGN-SPEC §4 "per-provider MODELS"),
// not load-bearing — the picked default is a client-side view concern until
// the summary contract carries a model field (PRD §5.5 drift).
export const PROVIDER_CATALOG: readonly ProviderCatalogEntry[] = [
  {
    id: "anthropic",
    label: "Anthropic",
    placeholder: "sk-ant-…",
    keyPrefix: "sk-ant-",
    contractBacked: true,
    models: ["claude-opus-4", "claude-sonnet-4", "claude-haiku-4"],
  },
  {
    id: "openai",
    label: "OpenAI",
    placeholder: "sk-…",
    keyPrefix: "sk-",
    contractBacked: true,
    models: ["gpt-4o", "gpt-4o-mini", "o3"],
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    placeholder: "sk-or-v1-…",
    keyPrefix: "sk-or-",
    contractBacked: true,
    models: [
      "anthropic/claude-opus-4",
      "openai/gpt-4o",
      "meta-llama/llama-3.1-70b-instruct",
    ],
  },
  {
    id: "google",
    label: "Google AI",
    placeholder: "AIza…",
    keyPrefix: "AIza",
    contractBacked: true,
    models: ["gemini-2.5-pro", "gemini-2.5-flash"],
  },
  {
    id: "groq",
    label: "Groq",
    placeholder: "gsk_…",
    keyPrefix: "gsk_",
    contractBacked: false,
    comingSoon: true,
    models: ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
  },
  {
    id: "xai",
    label: "xAI",
    placeholder: "xai-…",
    keyPrefix: "xai-",
    contractBacked: false,
    comingSoon: true,
    models: ["grok-4", "grok-3-mini"],
  },
];

export function providerCatalogEntry(
  slug: string,
  catalog: readonly ProviderCatalogEntry[] = PROVIDER_CATALOG,
): ProviderCatalogEntry | undefined {
  return catalog.find((entry) => entry.id === slug);
}

// ---------------------------------------------------------------------------
// Validation — the Add-flow's step-2 gate.
// ---------------------------------------------------------------------------

export interface ProviderKeyValidation {
  readonly ok: boolean;
  /** Human-readable reason shown as `role="alert"` when `ok` is false. */
  readonly error?: string;
  /** Model options the validator learned (else the modal falls back to catalog). */
  readonly models?: readonly string[];
}

/**
 * Pure, client-side format check mirroring the documented server rules
 * (packages/api-types/src/providerKeys.ts): a known prefix if the catalog
 * declares one, otherwise a permissive length check (>= 20). This is the
 * modal's DEFAULT step-2 validation; a host may inject a server-backed
 * `validate` on the port for a live round-trip. It NEVER stores the key.
 */
export function checkProviderKeyFormat(
  entry: ProviderCatalogEntry,
  apiKey: string,
): ProviderKeyValidation {
  const trimmed = apiKey.trim();
  if (trimmed.length === 0) {
    return { ok: false, error: "Enter a key to continue." };
  }
  if (entry.keyPrefix !== undefined && !trimmed.startsWith(entry.keyPrefix)) {
    return {
      ok: false,
      error: `${entry.label} keys start with "${entry.keyPrefix}".`,
    };
  }
  if (trimmed.length < 20) {
    return {
      ok: false,
      error: "That key looks too short — check you pasted the whole value.",
    };
  }
  return { ok: true, models: entry.models };
}

// ---------------------------------------------------------------------------
// Port — the host-callback seam the page depends on.
// ---------------------------------------------------------------------------

export interface ProviderKeysPort {
  /** `GET /v1/settings/provider-keys` — masked summaries only. */
  list(signal?: AbortSignal): Promise<readonly ProviderKeySummary[]>;
  /**
   * `PUT /v1/settings/provider-keys/{provider}` — stores the plaintext key
   * exactly once (PUT body) and returns the masked summary. The plaintext is
   * never returned or logged.
   */
  save(
    provider: string,
    apiKey: string,
    signal?: AbortSignal,
  ): Promise<ProviderKeySummary>;
  /** `DELETE /v1/settings/provider-keys/{provider}`. */
  remove(provider: string, signal?: AbortSignal): Promise<void>;
  /**
   * Optional live validation. When absent, the modal uses
   * `checkProviderKeyFormat` (the default Transport adapter ships no validate
   * endpoint, so it omits this — validation is the format check, and the real
   * server check happens on `save`).
   */
  validate?(
    provider: string,
    apiKey: string,
    signal?: AbortSignal,
  ): Promise<ProviderKeyValidation>;
  /**
   * Persist the Add-flow's step-3 model pick as the workspace default model
   * so runs actually use it (`PUT /v1/agent/workspace/defaults`). Optional:
   * when a host omits it the pick stays a view-only chip, exactly as before.
   */
  saveDefaultModel?(
    provider: string,
    modelName: string,
    signal?: AbortSignal,
  ): Promise<void>;
}

/**
 * Default `ProviderKeysPort` backed by the injected `Transport`. Builds typed
 * facade requests; the plaintext key appears exactly once, in the PUT body.
 */
export function createProviderKeysPort(transport: Transport): ProviderKeysPort {
  return {
    async list(signal) {
      const res = await transport.request<ListProviderKeysResponse>({
        method: "GET",
        path: "/v1/settings/provider-keys",
        signal,
      });
      return res.keys;
    },
    save(provider, apiKey, signal) {
      const body: PutProviderKeyRequest = { api_key: apiKey };
      return transport.request<ProviderKeySummary>({
        method: "PUT",
        path: `/v1/settings/provider-keys/${encodeURIComponent(provider)}`,
        body,
        signal,
      });
    },
    async remove(provider, signal) {
      await transport.request<void>({
        method: "DELETE",
        path: `/v1/settings/provider-keys/${encodeURIComponent(provider)}`,
        signal,
      });
    },
    async validate(provider, apiKey, signal) {
      // Live probe (PRD-F FR-F.4): the key feeds exactly one outbound call
      // and is never stored/echoed. Map the tri-state wire verdict onto the
      // modal's `ProviderKeyValidation`:
      //   valid === true  → advance, offering the real model ids.
      //   valid === false → invalid_key → bounce to step 1 with an alert.
      //   valid === null  → couldn't reach the provider; NOT a failure — let
      //                     the flow continue (offline-friendly, save is the
      //                     backstop), falling back to the catalog models.
      const body: ValidateProviderKeyRequest = { api_key: apiKey };
      const res = await transport.request<ValidateProviderKeyResponse>({
        method: "POST",
        path: `/v1/settings/provider-keys/${encodeURIComponent(provider)}/validate`,
        body,
        signal,
      });
      if (res.valid === true) {
        return {
          ok: true,
          models: res.models ?? undefined,
        };
      }
      if (res.valid === false) {
        return {
          ok: false,
          error:
            "That key was rejected by the provider — check you pasted the whole value.",
        };
      }
      // provider_unreachable: verify skipped, not failed. Continue with the
      // catalog's model list (the modal falls back when `models` is absent).
      return { ok: true };
    },
    async saveDefaultModel(provider, modelName, signal) {
      // The key store speaks `google` (ProviderName); the runtime's model
      // resolver speaks `gemini`. Mirror the backend ProviderKeysParser
      // normalization so the persisted default matches what runs resolve.
      const runtimeProvider = provider === "google" ? "gemini" : provider;
      // The PUT is a full-document replace, so read-merge-write: only
      // `default_model` changes; connectors/retention/behavior ride along.
      const current = await transport.request<WorkspaceDefaultsResponse>({
        method: "GET",
        path: "/v1/agent/workspace/defaults",
        signal,
      });
      const body: UpdateWorkspaceDefaultsRequest = {
        default_model: { provider: runtimeProvider, model_name: modelName },
        default_connectors: current.default_connectors,
        retention_days: current.retention_days,
        behavior_overrides: current.behavior_overrides,
      };
      await transport.request<WorkspaceDefaultsResponse>({
        method: "PUT",
        path: "/v1/agent/workspace/defaults",
        body,
        signal,
      });
    },
  };
}
