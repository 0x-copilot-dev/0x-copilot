// Developer-tokens data seam (DESIGN-SPEC §4 Developer tokens · PRD PR-5.9).
//
// Local CLI tokens let the `copilot` CLI authenticate on-device. The plaintext
// secret is minted by the server and returned EXACTLY ONCE (CreateApiKeyResponse
// carries `plaintext`; the server stores only the hash) — the page reveals it a
// single time, then it lives in the OS keychain. Every list read carries only
// the masked `key_prefix`, never the secret.
//
// The page depends on this PORT, not on `Transport` directly, so token minting /
// revocation is a host concern the substrate injects (keeping the page trivially
// testable with a mock port and honest about where secrets live).
//
// `createDeveloperTokensPort(transport)` is the default Transport-backed adapter
// against the facade `/v1/me/api-keys` routes (the personal-token surface —
// workspace/admin tokens are a team-profile concern, out of scope for the solo
// Advanced group). Tests and alternative substrates pass their own port.
//
// Substrate-agnostic: no bare `fetch`/`window` — the adapter only builds
// `TypedRequest` objects and calls the injected `Transport.request()`.

import type {
  ApiKeyListResponse,
  ApiKeySummary,
  CreateApiKeyRequest,
  CreateApiKeyResponse,
} from "@0x-copilot/api-types";

import type { Transport } from "../../ports/Transport";

// ---------------------------------------------------------------------------
// Port — the host-callback seam the page depends on.
// ---------------------------------------------------------------------------

export interface DeveloperTokensPort {
  /** `GET /v1/me/api-keys` — masked summaries only (no plaintext). */
  list(signal?: AbortSignal): Promise<readonly ApiKeySummary[]>;
  /**
   * `POST /v1/me/api-keys` — mints a token and returns the plaintext ONCE
   * (the server stores only the hash). The page shows it a single time.
   */
  create(label: string, signal?: AbortSignal): Promise<CreateApiKeyResponse>;
  /** `DELETE /v1/me/api-keys/{id}` — revoke a token. */
  revoke(id: string, signal?: AbortSignal): Promise<void>;
}

/**
 * Default `DeveloperTokensPort` backed by the injected `Transport`. Builds typed
 * facade requests against the personal-token routes; the plaintext appears only
 * in the create response and is never re-fetched.
 */
export function createDeveloperTokensPort(
  transport: Transport,
): DeveloperTokensPort {
  return {
    async list(signal) {
      const res = await transport.request<ApiKeyListResponse>({
        method: "GET",
        path: "/v1/me/api-keys",
        signal,
      });
      return res.keys;
    },
    create(label, signal) {
      const body: CreateApiKeyRequest = { label };
      return transport.request<CreateApiKeyResponse>({
        method: "POST",
        path: "/v1/me/api-keys",
        body,
        signal,
      });
    },
    async revoke(id, signal) {
      await transport.request<void>({
        method: "DELETE",
        path: `/v1/me/api-keys/${encodeURIComponent(id)}`,
        signal,
      });
    },
  };
}

// ---------------------------------------------------------------------------
// Presentation helpers (shared by the page + its tests).
// ---------------------------------------------------------------------------

/** The masked token identity shown in the list (`key_prefix` + ellipsis). */
export function maskDeveloperToken(summary: ApiKeySummary): string {
  return `${summary.key_prefix}…`;
}

/** Human "last used" label; unused tokens read "Never used". */
export function lastUsedLabel(summary: ApiKeySummary): string {
  if (summary.last_used_at === null) return "Never used";
  const date = new Date(summary.last_used_at);
  return Number.isNaN(date.getTime())
    ? summary.last_used_at
    : `Last used ${date.toLocaleDateString()}`;
}
