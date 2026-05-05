// Workspace API surface (PR 4.2 + earlier).
//
// PR 3.3 introduced the per-user member lookup; PR 4.2 adds:
//   - workspace branding read/patch
//   - admin members directory + role change + soft remove
//   - invitations (admin mint / list / revoke + public accept)
//   - billing read-only digest

import type {
  AcceptInvitationResponse,
  BillingDigest,
  CreateInvitationRequest,
  CreateInvitationResponse,
  Invitation,
  InvitationListResponse,
  Member,
  MemberListResponse,
  UpdateMemberRequest,
  UpdateWorkspaceSettingsRequest,
  WorkspaceSettings,
} from "@enterprise-search/api-types";
import type { RequestIdentity } from "./config";
import {
  assertOk,
  correlationHeaders,
  httpDelete,
  httpGet,
  httpPatchQuery,
  httpPostQuery,
  jsonHeaders,
} from "./http";

// ---------------------------------------------------------------------------
// PR 3.3 — per-member lookup (kept; the route is still a tightly-scoped TODO).
// ---------------------------------------------------------------------------

export interface WorkspaceMemberResponse {
  user_id: string;
  display_name: string;
  email?: string | null;
  handle?: string | null;
}

export function getWorkspaceMember(
  userId: string,
  identity: RequestIdentity,
): Promise<WorkspaceMemberResponse> {
  return httpGet<WorkspaceMemberResponse>(
    `/v1/workspace/members/${encodeURIComponent(userId)}`,
    identity,
  );
}

// ---------------------------------------------------------------------------
// PR 4.2 — workspace branding
// ---------------------------------------------------------------------------

export function getWorkspace(
  identity: RequestIdentity,
): Promise<WorkspaceSettings> {
  return httpGet<WorkspaceSettings>("/v1/workspace", identity);
}

export function patchWorkspace(
  body: UpdateWorkspaceSettingsRequest,
  identity: RequestIdentity,
): Promise<WorkspaceSettings> {
  return httpPatchQuery<WorkspaceSettings>("/v1/workspace", body, identity);
}

// ---------------------------------------------------------------------------
// PR 4.2 — members directory
// ---------------------------------------------------------------------------

export function listWorkspaceMembers(
  identity: RequestIdentity,
  options?: { include_removed?: boolean; role?: string },
): Promise<MemberListResponse> {
  const extra: Record<string, string | undefined> = {};
  if (options?.include_removed) extra.include_removed = "true";
  if (options?.role) extra.role = options.role;
  return httpGet<MemberListResponse>("/v1/workspace/members", identity, extra);
}

export function patchWorkspaceMember(
  memberUserId: string,
  body: UpdateMemberRequest,
  identity: RequestIdentity,
): Promise<Member> {
  return httpPatchQuery<Member>(
    `/v1/workspace/members/${encodeURIComponent(memberUserId)}`,
    body,
    identity,
  );
}

export function removeWorkspaceMember(
  memberUserId: string,
  identity: RequestIdentity,
): Promise<void> {
  return httpDelete(
    `/v1/workspace/members/${encodeURIComponent(memberUserId)}`,
    identity,
  );
}

// ---------------------------------------------------------------------------
// PR 4.2 — invitations
// ---------------------------------------------------------------------------

export function createInvitation(
  body: CreateInvitationRequest,
  identity: RequestIdentity,
): Promise<CreateInvitationResponse> {
  return httpPostQuery<CreateInvitationResponse>(
    "/v1/workspace/invitations",
    body,
    identity,
  );
}

export function listInvitations(
  identity: RequestIdentity,
): Promise<InvitationListResponse> {
  return httpGet<InvitationListResponse>("/v1/workspace/invitations", identity);
}

export function revokeInvitation(
  inviteId: string,
  identity: RequestIdentity,
): Promise<void> {
  return httpDelete(
    `/v1/workspace/invitations/${encodeURIComponent(inviteId)}`,
    identity,
  );
}

/** Public, no-auth accept. Only the token rides the URL. */
export async function acceptInvitation(
  token: string,
): Promise<AcceptInvitationResponse> {
  const response = await fetch(
    `/v1/auth/invitations/${encodeURIComponent(token)}/accept`,
    {
      method: "POST",
      headers: { ...jsonHeaders(), ...correlationHeaders() },
      body: "",
    },
  );
  await assertOk(response);
  return (await response.json()) as AcceptInvitationResponse;
}

// ---------------------------------------------------------------------------
// PR 4.2 — billing digest
// ---------------------------------------------------------------------------

export function getBillingDigest(
  identity: RequestIdentity,
): Promise<BillingDigest> {
  return httpGet<BillingDigest>("/v1/workspace/billing", identity);
}

// Re-export the typed shapes that callers commonly want one-deep.
export type {
  AcceptInvitationResponse,
  BillingDigest,
  CreateInvitationRequest,
  CreateInvitationResponse,
  Invitation,
  InvitationListResponse,
  Member,
  MemberListResponse,
  UpdateMemberRequest,
  UpdateWorkspaceSettingsRequest,
  WorkspaceSettings,
};
