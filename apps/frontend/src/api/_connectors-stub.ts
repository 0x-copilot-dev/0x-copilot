// Local stub for the Phase 11 Connectors webhook lifecycle wire types
// that haven't yet been re-exported through `@enterprise-search/api-types`'s
// index barrel.
//
// The canonical declarations live in `packages/api-types/src/connectors.ts`
// — `Webhook` itself is already re-exported through the package index, but
// the management envelopes (`WebhookCreateResponse`, `WebhookRotateResponse`,
// `WebhookListResponse`, `WebhookTestFireResponse`, plus the small
// `WebhookSecretStrategy` / `WebhookHmacAlgo` / `WebhookStatus` unions) are
// not. This stub mirrors them 1:1 so this PR's webhook API + UI can land
// without touching api-types (P11-C charter "Do not touch api-types").
//
// TODO(merge): once the package index re-exports the webhook lifecycle
// types, delete this file and swap every `_connectors-stub` import for
// `@enterprise-search/api-types`.

import type { TenantId, TriggerId } from "@enterprise-search/api-types";
import type { Webhook } from "@enterprise-search/api-types";

export type { Webhook, TenantId, TriggerId };

// Mirror of api-types/src/connectors.ts §webhook unions. Single shape; do
// not re-derive — when this stub is deleted the imports flip to the package.
export type WebhookSecretStrategy = "rotating" | "static";
export type WebhookHmacAlgo = "hmac-sha256";
export type WebhookStatus = "active" | "paused";

/**
 * `POST /v1/connectors/webhooks` (and `POST .../rotate` for rotate) returns
 * the plaintext secret EXACTLY ONCE. The `Webhook` body in the response is
 * the same redacted shape every other GET returns; the plaintext lives on
 * the response envelope only.
 */
export interface WebhookCreateResponse {
  readonly webhook: Webhook;
  readonly secret_plaintext: string;
}

export interface WebhookRotateResponse {
  readonly webhook: Webhook;
  readonly secret_plaintext: string;
  /** Previous secret remains valid for the 14-day grace window
   *  (connectors-prd §9.2). Null when there's no grace. */
  readonly grace_secret_plaintext: string | null;
}

export interface WebhookListResponse {
  readonly items: ReadonlyArray<Webhook>;
  readonly next_cursor: string | null;
}

export interface WebhookTestFireResponse {
  readonly response_status: number | null;
  readonly response_ok: boolean;
  readonly error?: string;
}

/**
 * Body of `POST /v1/connectors/webhooks`. Servers default everything the
 * caller omits — strategy + algo + IP allowlist + status come from
 * tenant-policy defaults when the wizard does not override them.
 */
export interface CreateWebhookRequest {
  readonly url: string;
  readonly secret_strategy?: WebhookSecretStrategy;
  readonly hmac_algo?: WebhookHmacAlgo;
  readonly ip_allowlist?: ReadonlyArray<string>;
  /** Caller-supplied secret. Required when `secret_strategy === "static"`
   *  (the server generates one for `"rotating"`). */
  readonly secret_plaintext?: string;
}

/**
 * Body of `PATCH /v1/connectors/webhooks/{id}`. Every field is optional;
 * unset fields are left unchanged server-side.
 */
export interface PatchWebhookRequest {
  readonly url?: string;
  readonly ip_allowlist?: ReadonlyArray<string>;
  readonly status?: WebhookStatus;
}

/**
 * Body of `POST /v1/connectors/webhooks/{id}/test-fire`. Empty for v1 —
 * the server constructs the payload deterministically from the webhook's
 * registration so receivers can hardcode-match.
 */
export interface TestFireWebhookRequest {
  /** Reserved for forward-compat; ignored server-side in v1. */
  readonly note?: string;
}
