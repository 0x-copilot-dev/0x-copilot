import type {
  UpdateWorkspaceMfaPolicyRequest,
  WorkspaceMfaPolicy,
} from "@0x-copilot/api-types";
import { httpJson } from "./http";

/**
 * Admin-only — read / write the workspace's MFA enforcement row
 * (PR 8.3). Backend gates on ``admin:users``; non-admins see 403 here
 * and the FE renders the section read-only.
 */

export function getWorkspaceMfaPolicy(): Promise<WorkspaceMfaPolicy> {
  return httpJson<WorkspaceMfaPolicy>("GET", "/v1/workspace/mfa-policy");
}

export function updateWorkspaceMfaPolicy(
  patch: UpdateWorkspaceMfaPolicyRequest,
): Promise<WorkspaceMfaPolicy> {
  return httpJson<WorkspaceMfaPolicy>("PUT", "/v1/workspace/mfa-policy", patch);
}
