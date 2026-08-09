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
  /**
   * True for the ONE generic "any OpenAI-compatible endpoint" entry (decision
   * D-2, slug `openai_compatible`). The Add flow captures a user-supplied Base
   * URL + Label before the key, and the port carries them to
   * `PUT/validate` so the run routes to that endpoint. Not a normal Add row —
   * it is reached via the "Another provider" affordance.
   */
  readonly isCustom?: boolean;
}

// Per-provider fallback model lists (DESIGN-SPEC §4 "per-provider MODELS"),
// used ONLY when the live `/v1/models` probe can't reach the provider or no
// server-backed `validate` is wired (the first-run port omits it). They are
// load-bearing in that case: `AddProviderKeyModal` preselects `models[0]`, and
// that pick is persisted twice — as the key's `default_model` column and as the
// workspace default runs resolve. So ORDER MATTERS — lead each list with the
// model a fresh key should open on, not the flagship.
export const PROVIDER_CATALOG: readonly ProviderCatalogEntry[] = [
  {
    id: "virtuals",
    label: "Virtuals",
    // No documented key prefix, so no `keyPrefix`: the client check stays
    // length-only and the backend exempts this slug from its prefix gate too.
    // Asserting a format we don't know would reject valid keys.
    placeholder: "paste your Virtuals key",
    contractBacked: true,
    // A gateway, so — like OpenRouter — one strong model per major vendor
    // rather than a size ladder. Everyday model first: this list seeds the
    // key's `default_model`, so it must not lead with the flagship.
    models: [
      "anthropic-claude-sonnet-5",
      "openai-gpt-56-luna",
      "moonshotai-kimi-k3",
    ],
  },
  {
    id: "anthropic",
    label: "Anthropic",
    placeholder: "sk-ant-…",
    keyPrefix: "sk-ant-",
    contractBacked: true,
    models: ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"],
  },
  {
    id: "openai",
    label: "OpenAI",
    placeholder: "sk-…",
    keyPrefix: "sk-",
    contractBacked: true,
    models: ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6"],
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    placeholder: "sk-or-v1-…",
    keyPrefix: "sk-or-",
    contractBacked: true,
    // A gateway, so the list offers one strong model per major vendor rather
    // than a size ladder. It mirrors the native picks above (Sonnet, Luna) plus
    // an open-weights option.
    models: [
      "anthropic/claude-sonnet-5",
      "openai/gpt-5.6-luna",
      "meta-llama/llama-4-maverick",
    ],
  },
  {
    id: "google",
    label: "Google AI",
    placeholder: "AIza…",
    keyPrefix: "AIza",
    contractBacked: true,
    // Flash is Gemini's everyday rung, so it leads. The `gemini-pro` line is
    // preview-only at present — `gemini-3.1-pro-preview` is its newest
    // non-deprecated general model, and is what the backend ladder resolves.
    models: [
      "gemini-3.6-flash",
      "gemini-3.5-flash-lite",
      "gemini-3.1-pro-preview",
    ],
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

/**
 * The generic custom OpenAI-compatible endpoint entry (decision D-2). Kept OUT
 * of `PROVIDER_CATALOG` (it is not a fixed provider with a known key prefix or
 * model list) and surfaced only via the "Another provider" affordance. Its
 * `models` are empty — the Add flow offers the endpoint's probed models, or a
 * free-text entry when the probe returns none. No `keyPrefix`: a custom gateway
 * may legitimately issue an `sk-…` token, so the client format check stays
 * length-only (the backend also relaxes the prefix gate for this slug).
 */
export const CUSTOM_ENDPOINT_ENTRY: ProviderCatalogEntry = {
  id: "openai_compatible",
  label: "Custom endpoint",
  placeholder: "sk-… or any bearer token",
  contractBacked: true,
  isCustom: true,
  models: [],
};

export function providerCatalogEntry(
  slug: string,
  catalog: readonly ProviderCatalogEntry[] = PROVIDER_CATALOG,
): ProviderCatalogEntry | undefined {
  if (slug === CUSTOM_ENDPOINT_ENTRY.id) return CUSTOM_ENDPOINT_ENTRY;
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
// Provider inference — which provider does a pasted key belong to?
// ---------------------------------------------------------------------------

/**
 * Key prefixes, LONGEST FIRST. Order is load-bearing, not cosmetic: `sk-` is a
 * prefix of both `sk-ant-` and `sk-or-`, so a shortest-first walk would call
 * every Anthropic and OpenRouter key an OpenAI one.
 *
 * Mirrors `_KNOWN_PREFIXES` in
 * services/backend/src/backend_app/provider_keys/service.py. The server is the
 * authority — this copy exists so the UI can show a verdict before the round
 * trip, and the two must be changed together.
 *
 * `acp-` (Virtuals) is not published in Virtuals' documentation. It is good
 * enough to INFER a provider from a key and deliberately not used to reject
 * one: a Virtuals key of some other shape falls through to
 * {@link detectProviderFromKey} returning `null`, which the UI turns into a
 * "choose a provider" step rather than an error.
 */
export const PROVIDER_KEY_PREFIXES: readonly (readonly [string, string])[] = [
  ["anthropic", "sk-ant-"],
  ["openrouter", "sk-or-"],
  ["openai", "sk-"],
  ["google", "AIza"],
  ["virtuals", "acp-"],
];

/** Below this, a key is treated as still being typed rather than unrecognised. */
export const MIN_PLAUSIBLE_KEY_LENGTH = 20;

/**
 * The provider slug a key most likely belongs to, or `null` when nothing
 * matches.
 *
 * Pure and synchronous — it reads a prefix, never the network. `null` is a
 * legitimate answer, not a failure: it means "ask the user", which is exactly
 * what the first-run form does with it.
 *
 * Callers must NOT run this per keystroke. `sk-` matches before `sk-ant-` is
 * fully typed, so a live verdict flips from OpenAI to Anthropic under the
 * cursor. Resolve on paste, on blur, or behind a debounce.
 */
export function detectProviderFromKey(apiKey: string): string | null {
  const trimmed = apiKey.trim();
  for (const [provider, prefix] of PROVIDER_KEY_PREFIXES) {
    if (trimmed.startsWith(prefix)) return provider;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Error copy — the ONE place a backend reason code becomes a sentence.
// ---------------------------------------------------------------------------
//
// The provider-keys routes answer a rejected key with a machine-readable reason
// code and nothing else: `HTTPException(400, "api_key_rejected_by_provider")`,
// because the detail field must never carry key material, a URL, a host, or a
// resolved IP (see services/backend/src/backend_app/provider_keys/
// {routes,service}.py and ssrf_guard.py). FastAPI serialises that as
// `{"detail": "<code>"}`, every host's HTTP layer lifts `detail` into
// `Error.message`, and both add-key surfaces rendered `err.message` verbatim —
// so a mistyped key told the user, literally, `api_key_rejected_by_provider`.
//
// Translating a code is therefore CLIENT work, and it belongs here rather than
// in either surface: `KeyForm` (the first-run gate and the composer's ModelPill)
// and `AddProviderKeyModal` (Settings) hit the same routes and must not drift
// into two vocabularies for one backend.
//
// Unmapped codes fall through to the raw string ON PURPOSE. A code added
// backend-side then degrades to exactly today's behaviour — ugly but truthful
// and greppable — rather than to a wrong sentence or an empty alert.

/**
 * Display context for {@link providerKeyErrorMessage}.
 *
 * Deliberately NOT the api key: no edit to the copy table can then interpolate
 * key material into something rendered on screen. `detectedProvider` is the
 * slug {@link detectProviderFromKey} returns, which this module resolves to a
 * label itself.
 */
export interface ProviderKeyErrorContext {
  /** Label of the provider the key is being stored under, e.g. "OpenAI". */
  readonly providerLabel?: string;
  /** Slug the pasted key's prefix points at — pass `detectProviderFromKey(key)`. */
  readonly detectedProvider?: string | null;
}

/** The detected provider's label, unless it is the one already selected. */
function otherProviderLabel(context: ProviderKeyErrorContext): string | null {
  const slug = context.detectedProvider;
  if (slug === undefined || slug === null || slug === "") return null;
  const label = providerCatalogEntry(slug)?.label;
  // Detected === selected would read "belongs to OpenAI … paste your OpenAI
  // key", which is nonsense the reader cannot act on.
  if (label === undefined || label === context.providerLabel) return null;
  return label;
}

// Keyed by the EXACT `detail` string the backend raises. `base_url_rejected`
// arrives suffixed with an `SsrfBlockReason` value, so each of the six is its
// own key — an unknown seventh falls through to the raw code, which is the
// documented degradation.
const PROVIDER_KEY_ERROR_COPY: Readonly<
  Record<string, (context: ProviderKeyErrorContext) => string>
> = {
  // routes.py — PUT, after the live provider probe came back INVALID_KEY.
  api_key_rejected_by_provider: ({ providerLabel }) =>
    providerLabel !== undefined && providerLabel !== ""
      ? `${providerLabel} rejected that key. Check you copied the whole value, and that the key is still active.`
      : "The provider rejected that key. Check you copied the whole value, and that the key is still active.",

  // service.py — validate_api_key_format, before anything leaves the process.
  api_key_too_short: () =>
    "That key looks too short. Check you pasted the whole value.",
  api_key_too_long: () =>
    "That key looks too long. Paste just the key, with nothing around it.",
  api_key_contains_whitespace: () =>
    "That key has a space or line break in it. Paste it again on its own.",
  // Phrased without an article so it reads correctly for every label
  // ("Google AI", "Groq", "Virtuals"), and without naming a UI affordance so it
  // fits the first-run form and the Settings per-provider row alike.
  api_key_provider_mismatch: (context) => {
    const selected =
      context.providerLabel !== undefined && context.providerLabel !== ""
        ? context.providerLabel
        : null;
    const detected = otherProviderLabel(context);
    if (detected !== null && selected !== null) {
      return `That key looks like it belongs to ${detected}. Add it under ${detected}, or paste your ${selected} key here.`;
    }
    if (selected !== null) {
      return `That key carries another provider's prefix. Add it under the provider it belongs to, or paste your ${selected} key here.`;
    }
    return "That key looks like it belongs to a different provider. Add it under that provider, or paste a key for this one.";
  },

  // routes.py — _require_custom_base_url, the decision D-2 custom endpoint.
  base_url_required: () =>
    "Enter the Base URL of your OpenAI-compatible endpoint.",
  custom_endpoint_unavailable: () =>
    "Custom endpoints are not available in this build. Choose one of the listed providers instead.",

  // ssrf_guard.py — SsrfBlockReason, one entry per value.
  "base_url_rejected:unsupported_scheme": () =>
    "That Base URL uses an address type we cannot call. Use an https:// address.",
  "base_url_rejected:https_required": () =>
    "That Base URL must start with https://.",
  "base_url_rejected:credentials_in_url": () =>
    "Take the username and password out of that Base URL — put the token in the API key field instead.",
  "base_url_rejected:missing_host": () =>
    "That Base URL has no host. Use the full address, e.g. https://my-host/v1.",
  "base_url_rejected:blocked_address": () =>
    "That Base URL points at a private or internal address, which is not allowed.",
  "base_url_rejected:unresolvable_host": () =>
    "That Base URL's host could not be found. Check it for typos.",
};

/**
 * Turn a backend provider-keys reason code into a sentence saying what
 * happened and what to do. An unrecognised code is returned unchanged — see
 * the section note above for why that degradation is deliberate.
 */
export function providerKeyErrorMessage(
  code: string,
  context: ProviderKeyErrorContext = {},
): string {
  const trimmed = code.trim();
  const copy = Object.prototype.hasOwnProperty.call(
    PROVIDER_KEY_ERROR_COPY,
    trimmed,
  )
    ? PROVIDER_KEY_ERROR_COPY[trimmed]
    : undefined;
  return copy !== undefined ? copy(context) : trimmed;
}

/**
 * The add-key surfaces' rejection handler: pull the message off a thrown
 * value, map it through {@link providerKeyErrorMessage}, and fall back to
 * `fallback` only when the throw carried no message at all.
 *
 * Non-code messages (a proxy timeout, "Failed to fetch") pass through
 * unchanged, exactly as before this mapping existed.
 */
export function describeProviderKeyError(
  err: unknown,
  fallback: string,
  context: ProviderKeyErrorContext = {},
): string {
  if (err instanceof Error && err.message) {
    return providerKeyErrorMessage(err.message, context);
  }
  if (typeof err === "string" && err) {
    return providerKeyErrorMessage(err, context);
  }
  return fallback;
}

// ---------------------------------------------------------------------------
// Port — the host-callback seam the page depends on.
// ---------------------------------------------------------------------------

/**
 * Options for {@link ProviderKeysPort.save}. `defaultModel` persists the
 * per-provider default (PR-F.5). `baseUrl` + `label` carry the custom
 * OpenAI-compatible endpoint (decision D-2) — set only for the
 * `openai_compatible` slug. All are optional and additive.
 */
export interface SaveProviderKeyOptions {
  readonly defaultModel?: string | null;
  readonly baseUrl?: string | null;
  readonly label?: string | null;
  readonly signal?: AbortSignal;
}

/** Options for {@link ProviderKeysPort.validate}. `baseUrl` is the custom
 * endpoint's probe target (decision D-2); set only for `openai_compatible`. */
export interface ValidateProviderKeyOptions {
  readonly baseUrl?: string | null;
  readonly signal?: AbortSignal;
}

export interface ProviderKeysPort {
  /** `GET /v1/settings/provider-keys` — masked summaries only. */
  list(signal?: AbortSignal): Promise<readonly ProviderKeySummary[]>;
  /**
   * `PUT /v1/settings/provider-keys/{provider}` — stores the plaintext key
   * exactly once (PUT body) and returns the masked summary. The plaintext is
   * never returned or logged. `options.defaultModel` persists the per-provider
   * `default_model` column (PR-F.5); `options.baseUrl`/`options.label` carry the
   * custom OpenAI-compatible endpoint (D-2). An omitted `defaultModel`/`baseUrl`/
   * `label` (or `null`/`""`) leaves the stored value untouched — a rotation
   * preserves the existing pick.
   */
  save(
    provider: string,
    apiKey: string,
    options?: SaveProviderKeyOptions,
  ): Promise<ProviderKeySummary>;
  /** `DELETE /v1/settings/provider-keys/{provider}`. */
  remove(provider: string, signal?: AbortSignal): Promise<void>;
  /**
   * Optional live validation. When absent, the modal uses
   * `checkProviderKeyFormat` (the default Transport adapter ships no validate
   * endpoint, so it omits this — validation is the format check, and the real
   * server check happens on `save`). `options.baseUrl` is the custom endpoint's
   * probe target for the `openai_compatible` slug (D-2).
   */
  validate?(
    provider: string,
    apiKey: string,
    options?: ValidateProviderKeyOptions,
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
    save(provider, apiKey, options) {
      // Plaintext travels exactly once, in this PUT body. `default_model`,
      // `base_url` and `label` are display-safe (never key material). Only
      // non-empty values are sent so a rotation preserves the stored ones.
      const body: PutProviderKeyRequest = { api_key: apiKey };
      const defaultModel = options?.defaultModel;
      if (
        defaultModel !== undefined &&
        defaultModel !== null &&
        defaultModel !== ""
      ) {
        (body as { default_model?: string }).default_model = defaultModel;
      }
      const baseUrl = options?.baseUrl;
      if (baseUrl !== undefined && baseUrl !== null && baseUrl !== "") {
        (body as { base_url?: string }).base_url = baseUrl;
      }
      const label = options?.label;
      if (label !== undefined && label !== null && label !== "") {
        (body as { label?: string }).label = label;
      }
      return transport.request<ProviderKeySummary>({
        method: "PUT",
        path: `/v1/settings/provider-keys/${encodeURIComponent(provider)}`,
        body,
        signal: options?.signal,
      });
    },
    async remove(provider, signal) {
      await transport.request<void>({
        method: "DELETE",
        path: `/v1/settings/provider-keys/${encodeURIComponent(provider)}`,
        signal,
      });
    },
    async validate(provider, apiKey, options) {
      // Live probe (PRD-F FR-F.4): the key feeds exactly one outbound call
      // and is never stored/echoed. Map the tri-state wire verdict onto the
      // modal's `ProviderKeyValidation`:
      //   valid === true  → advance, offering the real model ids.
      //   valid === false → invalid_key → bounce to step 1 with an alert.
      //   valid === null  → couldn't reach the provider; NOT a failure — let
      //                     the flow continue (offline-friendly, save is the
      //                     backstop), falling back to the catalog models.
      const body: ValidateProviderKeyRequest = { api_key: apiKey };
      if (
        options?.baseUrl !== undefined &&
        options.baseUrl !== null &&
        options.baseUrl !== ""
      ) {
        (body as { base_url?: string }).base_url = options.baseUrl;
      }
      const res = await transport.request<ValidateProviderKeyResponse>({
        method: "POST",
        path: `/v1/settings/provider-keys/${encodeURIComponent(provider)}/validate`,
        body,
        signal: options?.signal,
      });
      if (res.valid === true) {
        return {
          ok: true,
          models: res.models ?? undefined,
        };
      }
      if (res.valid === false) {
        // Same condition the PUT reports as `api_key_rejected_by_provider`,
        // just reached through the probe route instead — so it must say the
        // same words. Two sentences for one rejection is the drift the copy
        // table above exists to prevent.
        return {
          ok: false,
          error: providerKeyErrorMessage("api_key_rejected_by_provider", {
            providerLabel: providerCatalogEntry(provider)?.label,
          }),
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
