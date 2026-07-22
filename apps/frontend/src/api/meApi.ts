import type {
  ApiKeyListResponse,
  CreateApiKeyRequest,
  CreateApiKeyResponse,
  LinkWalletResult,
  NotificationPreferencesResponse,
  PrivacySettingsResponse,
  UpdateNotificationPreferencesRequest,
  UpdatePrivacySettingsRequest,
  UpdateUserPreferencesRequest,
  UpdateUserProfileRequest,
  UserPreferences,
  UserProfile,
  WorkspaceListResponse,
} from "@0x-copilot/api-types";
import { httpJson } from "./http";

/**
 * Caller-scoped reads + writes under `/v1/me/*` and `/v1/workspace/api-keys/*`.
 *
 * Identity is the bearer header — the facade derives the user from the
 * verified session, so no body / params for identity. Every endpoint
 * here goes through `httpJson` so bearer + correlation + 401 plumbing
 * stays a single behaviour shared with the rest of the api/* modules.
 */

export function listMyWorkspaces(): Promise<WorkspaceListResponse> {
  return httpJson<WorkspaceListResponse>("GET", "/v1/me/workspaces");
}

/**
 * Account-linking (PRD FR-L2): start the authenticated link-Google flow.
 * Returns the IdP `auth_url` to navigate the browser to; the flow completes
 * on the public /v1/auth/oidc/callback whose link intent is recovered
 * server-side from the state row.
 */
export function startGoogleLink(
  redirectUri: string,
  returnTo?: string,
  options?: { confirmMerge?: boolean },
): Promise<{ auth_url: string; state: string }> {
  return httpJson("POST", "/v1/me/identities/google/link/start", {
    redirect_uri: redirectUri,
    return_to: returnTo ?? null,
    // FR-U2: explicit consent that a Google account owned by another
    // 0xCopilot account should merge into this one. Recorded server-side on
    // the state row; never defaulted to true.
    confirm_merge: options?.confirmMerge ?? false,
  });
}

/**
 * Link a wallet to the current account (PRD FR-L1). The SIWE `message` +
 * `signature` prove control; the survivor account comes from the bearer.
 * A wallet owned by another account throws `TransportHttpError` with
 * `code === "merge_required"` (409) unless `confirmMerge` is set — the
 * FR-U2 consent the caller passes only after the user confirms the merge.
 * On a confirmed merge the result `status` is `"merged"`.
 */
export function linkWallet(
  message: string,
  signature: string,
  confirmMerge = false,
): Promise<LinkWalletResult> {
  return httpJson<LinkWalletResult>("POST", "/v1/me/identities/wallet", {
    message,
    signature,
    confirm_merge: confirmMerge,
  });
}

/** Unlink a linked sign-in identity (PRD FR-L5). 409 when it is the last one. */
export function unlinkIdentity(
  kind: "wallet" | "oidc",
  id: string,
): Promise<void> {
  return httpJson("DELETE", `/v1/me/identities/${kind}/${id}`);
}

export function getMyProfile(): Promise<UserProfile> {
  return httpJson<UserProfile>("GET", "/v1/me/profile");
}

export function updateMyProfile(
  patch: UpdateUserProfileRequest,
): Promise<UserProfile> {
  return httpJson<UserProfile>("PUT", "/v1/me/profile", patch);
}

export function getMyPreferences(): Promise<UserPreferences> {
  return httpJson<UserPreferences>("GET", "/v1/me/preferences");
}

export function updateMyPreferences(
  patch: UpdateUserPreferencesRequest,
): Promise<UserPreferences> {
  return httpJson<UserPreferences>("PUT", "/v1/me/preferences", patch);
}

// PR B2 / 8.0.3f — privacy & data settings (per-user override).
export function getMyPrivacySettings(): Promise<PrivacySettingsResponse> {
  return httpJson<PrivacySettingsResponse>("GET", "/v1/me/policies/privacy");
}

export function updateMyPrivacySettings(
  patch: UpdatePrivacySettingsRequest,
): Promise<PrivacySettingsResponse> {
  return httpJson<PrivacySettingsResponse>(
    "PUT",
    "/v1/me/policies/privacy",
    patch,
  );
}

// PR B4 / 8.0.3e — typed notification preferences + quiet hours.
export function getMyNotificationPreferences(): Promise<NotificationPreferencesResponse> {
  return httpJson<NotificationPreferencesResponse>(
    "GET",
    "/v1/me/notifications",
  );
}

export function updateMyNotificationPreferences(
  patch: UpdateNotificationPreferencesRequest,
): Promise<NotificationPreferencesResponse> {
  return httpJson<NotificationPreferencesResponse>(
    "PUT",
    "/v1/me/notifications",
    patch,
  );
}

// PR B3 / 8.0.3g — personal API keys (atlas_pk_*).
export function listMyApiKeys(): Promise<ApiKeyListResponse> {
  return httpJson<ApiKeyListResponse>("GET", "/v1/me/api-keys");
}

export function createMyApiKey(
  request: CreateApiKeyRequest,
): Promise<CreateApiKeyResponse> {
  return httpJson<CreateApiKeyResponse>("POST", "/v1/me/api-keys", request);
}

export async function revokeMyApiKey(apiKeyId: string): Promise<void> {
  await httpJson<void>(
    "DELETE",
    `/v1/me/api-keys/${encodeURIComponent(apiKeyId)}`,
  );
}

export function rotateMyApiKey(
  apiKeyId: string,
): Promise<CreateApiKeyResponse> {
  return httpJson<CreateApiKeyResponse>(
    "POST",
    `/v1/me/api-keys/${encodeURIComponent(apiKeyId)}/rotate`,
  );
}

// PR 8.3 — workspace-issued admin tokens. Same wire shape as the
// personal endpoints; backend gates on ``admin:users``.

export function listWorkspaceApiKeys(): Promise<ApiKeyListResponse> {
  return httpJson<ApiKeyListResponse>("GET", "/v1/workspace/api-keys");
}

export function createWorkspaceApiKey(
  request: CreateApiKeyRequest,
): Promise<CreateApiKeyResponse> {
  return httpJson<CreateApiKeyResponse>(
    "POST",
    "/v1/workspace/api-keys",
    request,
  );
}

export async function revokeWorkspaceApiKey(apiKeyId: string): Promise<void> {
  await httpJson<void>(
    "DELETE",
    `/v1/workspace/api-keys/${encodeURIComponent(apiKeyId)}`,
  );
}

export function rotateWorkspaceApiKey(
  apiKeyId: string,
): Promise<CreateApiKeyResponse> {
  return httpJson<CreateApiKeyResponse>(
    "POST",
    `/v1/workspace/api-keys/${encodeURIComponent(apiKeyId)}/rotate`,
  );
}
