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
}
