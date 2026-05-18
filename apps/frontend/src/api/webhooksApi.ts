// Typed wrappers for the Phase 11 Webhook management surface (sub-route
// of the Connectors destination — sub-PRD §4.10 + §9 HMAC UX).
//
// Surfaces:
//   1. `fetchWebhooks(identity, opts)`               — GET /v1/connectors/webhooks.
//   2. `fetchWebhook(identity, id)`                  — GET /v1/connectors/webhooks/{id}.
//   3. `createWebhook(identity, body)`               — POST /v1/connectors/webhooks
//                                                       (copy-once secret reveal).
//   4. `patchWebhook(identity, id, body)`            — PATCH /v1/connectors/webhooks/{id}.
//   5. `deleteWebhook(identity, id)`                 — DELETE /v1/connectors/webhooks/{id}.
//   6. `rotateWebhookSecret(identity, id)`           — POST /v1/connectors/webhooks/{id}/rotate
//                                                       (copy-once secret reveal).
//   7. `testFireWebhook(identity, id, body)`         — POST /v1/connectors/webhooks/{id}/test-fire.
//
// Secret-handling invariant (connectors-prd §9.2 + charter): plaintext
// secrets come back on the create + rotate envelopes ONLY. Every other
// fetch returns the redacted `Webhook` shape. Callers MUST surface the
// plaintext through a copy-once reveal and never persist it.

import type { Webhook } from "@enterprise-search/api-types";

import type {
  CreateWebhookRequest,
  PatchWebhookRequest,
  TestFireWebhookRequest,
  WebhookCreateResponse,
  WebhookListResponse,
  WebhookRotateResponse,
  WebhookTestFireResponse,
} from "./_connectors-stub";
import type { RequestIdentity } from "./config";
import { httpDelete, httpGet, httpPatchQuery, httpPostQuery } from "./http";

export type {
  CreateWebhookRequest,
  PatchWebhookRequest,
  TestFireWebhookRequest,
  WebhookCreateResponse,
  WebhookListResponse,
  WebhookRotateResponse,
  WebhookTestFireResponse,
};

// ===========================================================================
// LIST + DETAIL
// ===========================================================================

export interface FetchWebhooksOptions {
  readonly after?: string;
  readonly limit?: number;
}

export function fetchWebhooks(
  identity: RequestIdentity,
  options: FetchWebhooksOptions = {},
): Promise<WebhookListResponse> {
  const params: Record<string, string | undefined> = {};
  if (options.after !== undefined) {
    params.after = options.after;
  }
  if (options.limit !== undefined) {
    params.limit = String(options.limit);
  }
  return httpGet<WebhookListResponse>(
    "/v1/connectors/webhooks",
    identity,
    params,
  );
}

export function fetchWebhook(
  identity: RequestIdentity,
  id: string,
): Promise<Webhook> {
  return httpGet<Webhook>(
    `/v1/connectors/webhooks/${encodeURIComponent(id)}`,
    identity,
  );
}

// ===========================================================================
// CREATE — copy-once secret reveal lives on this response
// ===========================================================================

/**
 * POST /v1/connectors/webhooks — register a webhook. For
 * `secret_strategy === "rotating"` the server generates the secret; for
 * `"static"` the caller supplies it. EITHER WAY the response carries
 * `secret_plaintext` exactly once — every subsequent GET returns the
 * redacted `Webhook` shape. The caller MUST present the plaintext
 * through a copy-once-reveal UI and MUST NOT persist it.
 */
export function createWebhook(
  identity: RequestIdentity,
  body: CreateWebhookRequest,
): Promise<WebhookCreateResponse> {
  return httpPostQuery<WebhookCreateResponse>(
    "/v1/connectors/webhooks",
    body,
    identity,
  );
}

// ===========================================================================
// PATCH — non-secret fields only (url / ip_allowlist / status)
// ===========================================================================

export function patchWebhook(
  identity: RequestIdentity,
  id: string,
  body: PatchWebhookRequest,
): Promise<Webhook> {
  return httpPatchQuery<Webhook>(
    `/v1/connectors/webhooks/${encodeURIComponent(id)}`,
    body,
    identity,
  );
}

// ===========================================================================
// DELETE
// ===========================================================================

export function deleteWebhook(
  identity: RequestIdentity,
  id: string,
): Promise<void> {
  return httpDelete(
    `/v1/connectors/webhooks/${encodeURIComponent(id)}`,
    identity,
  );
}

// ===========================================================================
// ROTATE — same copy-once contract as create
// ===========================================================================

/**
 * POST /v1/connectors/webhooks/{id}/rotate — generate a fresh secret. The
 * previous secret remains valid for the 14-day grace window
 * (connectors-prd §9.2) and is surfaced on the response as
 * `grace_secret_plaintext` — null when there is no grace (first rotation
 * or after the previous grace has elapsed). The new plaintext is the
 * `secret_plaintext` field; same copy-once contract as create.
 */
export function rotateWebhookSecret(
  identity: RequestIdentity,
  id: string,
): Promise<WebhookRotateResponse> {
  return httpPostQuery<WebhookRotateResponse>(
    `/v1/connectors/webhooks/${encodeURIComponent(id)}/rotate`,
    {},
    identity,
  );
}

// ===========================================================================
// TEST-FIRE
// ===========================================================================

/**
 * POST /v1/connectors/webhooks/{id}/test-fire — Atlas sends a deterministic
 * test payload to the receiver with the canonical HMAC headers and surfaces
 * the upstream HTTP status. Returns `response_status === null` for
 * transport-level failures (DNS / timeout / connection refused) — callers
 * surface those as "could not reach receiver" rather than "receiver said
 * no".
 */
export function testFireWebhook(
  identity: RequestIdentity,
  id: string,
  body: TestFireWebhookRequest = {},
): Promise<WebhookTestFireResponse> {
  return httpPostQuery<WebhookTestFireResponse>(
    `/v1/connectors/webhooks/${encodeURIComponent(id)}/test-fire`,
    body,
    identity,
  );
}
