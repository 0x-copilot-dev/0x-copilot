import type {
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
