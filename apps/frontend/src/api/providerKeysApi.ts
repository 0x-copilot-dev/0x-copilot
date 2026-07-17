// BYOK provider keys — typed wrappers for the Settings → Provider keys
// section.
//
//   GET    /v1/settings/provider-keys             → ListProviderKeysResponse
//   PUT    /v1/settings/provider-keys/{provider}  → ProviderKeySummary
//   DELETE /v1/settings/provider-keys/{provider}  → 204
//
// Identity is the bearer header — the facade derives (org_id, user_id)
// from the verified session, so no identity query params (same
// convention as `/v1/me/*` in meApi.ts). The plaintext key appears on
// the wire exactly once, in the PUT body; every read returns only the
// masked `key_hint`.

import type {
  ListProviderKeysResponse,
  ProviderKeyProvider,
  ProviderKeySummary,
  PutProviderKeyRequest,
} from "@0x-copilot/api-types";
import { httpJson } from "./http";

export function listProviderKeys(): Promise<ListProviderKeysResponse> {
  return httpJson<ListProviderKeysResponse>(
    "GET",
    "/v1/settings/provider-keys",
  );
}

export function putProviderKey(
  provider: ProviderKeyProvider,
  request: PutProviderKeyRequest,
): Promise<ProviderKeySummary> {
  return httpJson<ProviderKeySummary>(
    "PUT",
    `/v1/settings/provider-keys/${encodeURIComponent(provider)}`,
    request,
  );
}

export async function deleteProviderKey(
  provider: ProviderKeyProvider,
): Promise<void> {
  await httpJson<void>(
    "DELETE",
    `/v1/settings/provider-keys/${encodeURIComponent(provider)}`,
  );
}
