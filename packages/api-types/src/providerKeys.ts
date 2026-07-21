// BYOK provider keys (Phase 2 — bring-your-own-key) — wire contract.
//
// Public facade routes (user bearer, RBAC scope RUNTIME_USE, scoped per
// (org_id, user_id) derived from the verified session — no identity
// params on the wire):
//
//   GET    /v1/settings/provider-keys             → ListProviderKeysResponse
//   PUT    /v1/settings/provider-keys/{provider}  → ProviderKeySummary
//   DELETE /v1/settings/provider-keys/{provider}  → 204
//
// Security invariant: the server NEVER returns the plaintext key after
// the PUT round-trip — reads carry only `key_hint` (the last 4
// characters). The plaintext travels exactly once, in the PUT body,
// and is encrypted at rest server-side. Do not add a "reveal" field to
// these shapes.
//
// Validation split: the server 422s an unknown provider slug and 400s
// an obviously-wrong key format (openai keys start `sk-`, anthropic
// `sk-ant-`, openrouter `sk-or-`, google `AIza`; unknown-but-plausible
// values of length >= 20 are accepted permissively). The frontend
// surfaces those errors verbatim rather than duplicating the rules
// client-side.

/**
 * Model providers a workspace user can bring their own key for.
 *
 * `openrouter` is an OpenAI-wire-compatible gateway (300+ models via
 * `vendor/model` slugs); the runtime routes it through the OpenAI client
 * with a fixed base URL and the Responses API disabled.
 */
export type ProviderKeyProvider =
  | "openai"
  | "anthropic"
  | "google"
  | "openrouter";

/**
 * One stored key, as returned by list and PUT. `key_hint` is a masked
 * suffix (e.g. `"…1234"` — last 4 chars only), never the plaintext.
 */
export interface ProviderKeySummary {
  readonly provider: ProviderKeyProvider;
  readonly key_hint: string;
  /** ISO-8601 timestamp of the last PUT for this provider. */
  readonly updated_at: string;
  /**
   * The model to seed the row's default-model chip with — the server's
   * single-source projection of the default model chosen for this provider
   * key. ADDITIVE superset (PRD-F PR-F.5): absent/`null` on older servers and
   * on keys stored without a model, in which case clients fall back to their
   * own model-chip hint. Never key material.
   */
  readonly default_model?: string | null;
}

/** `GET /v1/settings/provider-keys` response. */
export interface ListProviderKeysResponse {
  readonly keys: readonly ProviderKeySummary[];
}

/**
 * Body for `PUT /v1/settings/provider-keys/{provider}`. The only place
 * the plaintext key ever appears on the wire.
 */
export interface PutProviderKeyRequest {
  readonly api_key: string;
  /**
   * Optional default model to persist alongside the key so the server can
   * project it back on `ProviderKeySummary.default_model`. ADDITIVE
   * (PRD-F PR-F.5): older clients omit it and the stored default is
   * preserved on rotation; never key material.
   */
  readonly default_model?: string | null;
}

/**
 * `PUT /v1/settings/provider-keys/{provider}` response: the stored
 * summary plus an optional live-check note. `"passed"` = the provider
 * accepted the key; `"skipped_unreachable"` = the provider couldn't be
 * reached and the key was stored anyway (offline-friendly). Absent when
 * live validation is disabled — the legacy three-field shape.
 */
export interface PutProviderKeyResponse extends ProviderKeySummary {
  readonly live_check?: "passed" | "skipped_unreachable";
}

/**
 * Body for `POST /v1/settings/provider-keys/{provider}/validate` — the
 * live probe. The key feeds exactly one outbound provider call and is
 * never stored, audited, or echoed.
 */
export interface ValidateProviderKeyRequest {
  readonly api_key: string;
}

/**
 * Tri-state live verdict — discriminate on `valid`:
 * `true` → `models` lists the ids this key can reach (may legitimately
 * be empty where the authenticated probe isn't a model listing);
 * `false` → the provider rejected the key (`reason: "invalid_key"`);
 * `null` → the check couldn't run (`reason: "provider_unreachable"`),
 * which is NOT a failure verdict. All three keys are always present.
 */
export interface ValidateProviderKeyResponse {
  readonly valid: boolean | null;
  readonly models: readonly string[] | null;
  readonly reason: "invalid_key" | "provider_unreachable" | null;
}
