import type {
  ApiKeyListResponse,
  CreateApiKeyRequest,
  CreateApiKeyResponse,
  NotificationPreferencesResponse,
  PrivacySettingsResponse,
  ToolUsePolicyResponse,
  UpdateNotificationPreferencesRequest,
  UpdatePrivacySettingsRequest,
  UpdateToolUsePolicyRequest,
  UpdateUserPreferencesRequest,
  UpdateUserProfileRequest,
  UserPreferences,
  UserProfile,
  WorkspaceListResponse,
} from "@enterprise-search/api-types";
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

// PR B1 / 8.0.3d — tool-use policy (per-user override).
export function getMyToolUsePolicy(): Promise<ToolUsePolicyResponse> {
  return httpJson<ToolUsePolicyResponse>("GET", "/v1/me/policies/tool-use");
}

export function updateMyToolUsePolicy(
  patch: UpdateToolUsePolicyRequest,
): Promise<ToolUsePolicyResponse> {
  return httpJson<ToolUsePolicyResponse>(
    "PUT",
    "/v1/me/policies/tool-use",
    patch,
  );
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
