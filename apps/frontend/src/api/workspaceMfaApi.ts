import type {
  UpdateWorkspaceMfaPolicyRequest,
  WorkspaceMfaPolicy,
} from "@enterprise-search/api-types";
import { assertOk, correlationHeaders, jsonHeaders } from "./http";

/**
 * Admin-only — read / write the workspace's MFA enforcement row
 * (PR 8.3). Backend gates on ``admin:users``; non-admins see 403 here
 * and the FE renders the section read-only.
 */

export async function getWorkspaceMfaPolicy(): Promise<WorkspaceMfaPolicy> {
  const response = await fetch("/v1/workspace/mfa-policy", {
    headers: correlationHeaders(),
  });
  await assertOk(response);
  return (await response.json()) as WorkspaceMfaPolicy;
}

export async function updateWorkspaceMfaPolicy(
  patch: UpdateWorkspaceMfaPolicyRequest,
): Promise<WorkspaceMfaPolicy> {
  const response = await fetch("/v1/workspace/mfa-policy", {
    method: "PUT",
    headers: jsonHeaders(),
    body: JSON.stringify(patch),
  });
  await assertOk(response);
  return (await response.json()) as WorkspaceMfaPolicy;
}
