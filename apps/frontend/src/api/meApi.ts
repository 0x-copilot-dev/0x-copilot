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
import { assertOk, correlationHeaders, jsonHeaders } from "./http";

/**
 * Caller-scoped reads + writes under `/v1/me/*`.
 *
 * Identity is the bearer header — the facade derives the user from the
 * verified session, so no body / params for identity. PR 4.1 adds the
 * profile + preferences sidecars; PR 2.2 already wired the workspaces
 * endpoint.
 */
export async function listMyWorkspaces(): Promise<WorkspaceListResponse> {
  const response = await fetch("/v1/me/workspaces", {
    headers: correlationHeaders(),
  });
  await assertOk(response);
  return (await response.json()) as WorkspaceListResponse;
}

export async function getMyProfile(): Promise<UserProfile> {
  const response = await fetch("/v1/me/profile", {
    headers: correlationHeaders(),
  });
  await assertOk(response);
  return (await response.json()) as UserProfile;
}

export async function updateMyProfile(
  patch: UpdateUserProfileRequest,
): Promise<UserProfile> {
  const response = await fetch("/v1/me/profile", {
    method: "PUT",
    headers: jsonHeaders(),
    body: JSON.stringify(patch),
  });
  await assertOk(response);
  return (await response.json()) as UserProfile;
}

export async function getMyPreferences(): Promise<UserPreferences> {
  const response = await fetch("/v1/me/preferences", {
    headers: correlationHeaders(),
  });
  await assertOk(response);
  return (await response.json()) as UserPreferences;
}

export async function updateMyPreferences(
  patch: UpdateUserPreferencesRequest,
): Promise<UserPreferences> {
  const response = await fetch("/v1/me/preferences", {
    method: "PUT",
    headers: jsonHeaders(),
    body: JSON.stringify(patch),
  });
  await assertOk(response);
  return (await response.json()) as UserPreferences;
}

// PR B1 / 8.0.3d — tool-use policy (per-user override).
export async function getMyToolUsePolicy(): Promise<ToolUsePolicyResponse> {
  const response = await fetch("/v1/me/policies/tool-use", {
    headers: correlationHeaders(),
  });
  await assertOk(response);
  return (await response.json()) as ToolUsePolicyResponse;
}

export async function updateMyToolUsePolicy(
  patch: UpdateToolUsePolicyRequest,
): Promise<ToolUsePolicyResponse> {
  const response = await fetch("/v1/me/policies/tool-use", {
    method: "PUT",
    headers: jsonHeaders(),
    body: JSON.stringify(patch),
  });
  await assertOk(response);
  return (await response.json()) as ToolUsePolicyResponse;
}

// PR B2 / 8.0.3f — privacy & data settings (per-user override).
export async function getMyPrivacySettings(): Promise<PrivacySettingsResponse> {
  const response = await fetch("/v1/me/policies/privacy", {
    headers: correlationHeaders(),
  });
  await assertOk(response);
  return (await response.json()) as PrivacySettingsResponse;
}

export async function updateMyPrivacySettings(
  patch: UpdatePrivacySettingsRequest,
): Promise<PrivacySettingsResponse> {
  const response = await fetch("/v1/me/policies/privacy", {
    method: "PUT",
    headers: jsonHeaders(),
    body: JSON.stringify(patch),
  });
  await assertOk(response);
  return (await response.json()) as PrivacySettingsResponse;
}

// PR B4 / 8.0.3e — typed notification preferences + quiet hours.
export async function getMyNotificationPreferences(): Promise<NotificationPreferencesResponse> {
  const response = await fetch("/v1/me/notifications", {
    headers: correlationHeaders(),
  });
  await assertOk(response);
  return (await response.json()) as NotificationPreferencesResponse;
}

export async function updateMyNotificationPreferences(
  patch: UpdateNotificationPreferencesRequest,
): Promise<NotificationPreferencesResponse> {
  const response = await fetch("/v1/me/notifications", {
    method: "PUT",
    headers: jsonHeaders(),
    body: JSON.stringify(patch),
  });
  await assertOk(response);
  return (await response.json()) as NotificationPreferencesResponse;
}

// PR B3 / 8.0.3g — personal API keys (atlas_pk_*).
export async function listMyApiKeys(): Promise<ApiKeyListResponse> {
  const response = await fetch("/v1/me/api-keys", {
    headers: correlationHeaders(),
  });
  await assertOk(response);
  return (await response.json()) as ApiKeyListResponse;
}

export async function createMyApiKey(
  request: CreateApiKeyRequest,
): Promise<CreateApiKeyResponse> {
  const response = await fetch("/v1/me/api-keys", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(request),
  });
  await assertOk(response);
  return (await response.json()) as CreateApiKeyResponse;
}

export async function revokeMyApiKey(apiKeyId: string): Promise<void> {
  const response = await fetch(
    `/v1/me/api-keys/${encodeURIComponent(apiKeyId)}`,
    {
      method: "DELETE",
      headers: correlationHeaders(),
    },
  );
  await assertOk(response);
}

export async function rotateMyApiKey(
  apiKeyId: string,
): Promise<CreateApiKeyResponse> {
  const response = await fetch(
    `/v1/me/api-keys/${encodeURIComponent(apiKeyId)}/rotate`,
    {
      method: "POST",
      headers: correlationHeaders(),
    },
  );
  await assertOk(response);
  return (await response.json()) as CreateApiKeyResponse;
}
