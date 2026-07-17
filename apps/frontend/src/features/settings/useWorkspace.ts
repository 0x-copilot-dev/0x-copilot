// PR 4.2 — data hooks for the Settings → "Workspace" group.
//
// Three small hooks, all single-fetch + manual refresh in the same shape as
// `useWorkspaceDefaults` (PR 3.5). No global cache layer; the Settings page
// hydrates once and refreshes only on user-driven mutations.
//
// Each hook delegates the fetch / loading / error / cancellation plumbing
// to `useRecord` (see api/useResource.ts) and layers its own mutators
// on top. Field names (`workspace`, `members`, `invitations`, `digest`)
// are kept distinct from `data` so the call sites in Settings read
// naturally.

import { useCallback } from "react";
import type {
  BillingDigest,
  CreateInvitationRequest,
  CreateInvitationResponse,
  Invitation,
  Member,
  UpdateMemberRequest,
  UpdateWorkspaceSettingsRequest,
  WorkspaceSettings,
} from "@0x-copilot/api-types";

import {
  createInvitation,
  getBillingDigest,
  getWorkspace,
  listInvitations,
  listWorkspaceMembers,
  patchWorkspace,
  patchWorkspaceMember,
  removeWorkspaceMember,
  revokeInvitation,
} from "../../api/workspaceApi";
import type { RequestIdentity } from "../../api/config";
import { useRecord } from "../../api/useResource";
import { errorMessage } from "../../utils/errors";

// ---------------------------------------------------------------------------
// Workspace branding
// ---------------------------------------------------------------------------

export interface UseWorkspaceResult {
  workspace: WorkspaceSettings | null;
  loading: boolean;
  error: string | null;
  save: (body: UpdateWorkspaceSettingsRequest) => Promise<void>;
  refresh: () => Promise<void>;
}

export function useWorkspace(identity: RequestIdentity): UseWorkspaceResult {
  const fetcher = useCallback(() => getWorkspace(identity), [identity]);
  const { data, loading, error, refresh, setData } = useRecord(
    fetcher,
    "Could not load workspace",
  );

  const save = useCallback(
    async (body: UpdateWorkspaceSettingsRequest): Promise<void> => {
      const previous = data;
      // Optimistic — same shape as useWorkspaceDefaults.
      if (previous) {
        setData({
          ...previous,
          display_name: body.display_name ?? previous.display_name,
          slug: body.slug ?? previous.slug,
          metadata: body.metadata
            ? mergeMetadata(previous.metadata, body.metadata)
            : previous.metadata,
        });
      }
      try {
        const updated = await patchWorkspace(body, identity);
        setData(updated);
      } catch (err) {
        setData(previous);
        // Rethrow so the caller can show its own error UI; setData has
        // already rolled the optimistic write back. The hook's `error`
        // surface is reserved for load failures.
        throw new Error(errorMessage(err, "Could not save workspace"));
      }
    },
    [identity, data, setData],
  );

  return { workspace: data, loading, error, save, refresh };
}

// ---------------------------------------------------------------------------
// Members directory
// ---------------------------------------------------------------------------

export interface UseWorkspaceMembersResult {
  members: Member[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  changeRole: (
    userId: string,
    role: UpdateMemberRequest["role"],
  ) => Promise<void>;
  remove: (userId: string) => Promise<void>;
}

export function useWorkspaceMembers(
  identity: RequestIdentity,
): UseWorkspaceMembersResult {
  const fetcher = useCallback(
    () => listWorkspaceMembers(identity).then((r) => r.members),
    [identity],
  );
  const { data, loading, error, refresh, setData } = useRecord(
    fetcher,
    "Could not load members",
  );

  const changeRole = useCallback(
    async (
      userId: string,
      role: UpdateMemberRequest["role"],
    ): Promise<void> => {
      try {
        const updated = await patchWorkspaceMember(userId, { role }, identity);
        setData((current) =>
          (current ?? []).map((m) => (m.user_id === userId ? updated : m)),
        );
      } catch (err) {
        throw new Error(errorMessage(err, "Could not update role"));
      }
    },
    [identity, setData],
  );

  const remove = useCallback(
    async (userId: string): Promise<void> => {
      const previous = data;
      setData((current) => (current ?? []).filter((m) => m.user_id !== userId));
      try {
        await removeWorkspaceMember(userId, identity);
      } catch (err) {
        setData(previous);
        throw new Error(errorMessage(err, "Could not remove member"));
      }
    },
    [identity, data, setData],
  );

  return {
    members: data ?? [],
    loading,
    error,
    refresh,
    changeRole,
    remove,
  };
}

// ---------------------------------------------------------------------------
// Invitations
// ---------------------------------------------------------------------------

export interface UseInvitationsResult {
  invitations: Invitation[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  create: (body: CreateInvitationRequest) => Promise<CreateInvitationResponse>;
  revoke: (inviteId: string) => Promise<void>;
}

export function useInvitations(
  identity: RequestIdentity,
): UseInvitationsResult {
  const fetcher = useCallback(
    () => listInvitations(identity).then((r) => r.invitations),
    [identity],
  );
  const { data, loading, error, refresh, setData } = useRecord(
    fetcher,
    "Could not load invitations",
  );

  const create = useCallback(
    async (
      body: CreateInvitationRequest,
    ): Promise<CreateInvitationResponse> => {
      try {
        const response = await createInvitation(body, identity);
        setData((current) => [
          {
            invite_id: response.invite_id,
            email: response.email,
            role: response.role,
            token_prefix: response.token_prefix,
            created_by: response.created_by,
            created_at: response.created_at,
            expires_at: response.expires_at,
          },
          ...(current ?? []),
        ]);
        return response;
      } catch (err) {
        throw new Error(errorMessage(err, "Could not create invitation"));
      }
    },
    [identity, setData],
  );

  const revoke = useCallback(
    async (inviteId: string): Promise<void> => {
      const previous = data;
      setData((current) =>
        (current ?? []).filter((i) => i.invite_id !== inviteId),
      );
      try {
        await revokeInvitation(inviteId, identity);
      } catch (err) {
        setData(previous);
        throw new Error(errorMessage(err, "Could not revoke invitation"));
      }
    },
    [identity, data, setData],
  );

  return {
    invitations: data ?? [],
    loading,
    error,
    refresh,
    create,
    revoke,
  };
}

// ---------------------------------------------------------------------------
// Billing digest
// ---------------------------------------------------------------------------

export interface UseBillingResult {
  digest: BillingDigest | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useBilling(identity: RequestIdentity): UseBillingResult {
  const fetcher = useCallback(() => getBillingDigest(identity), [identity]);
  const { data, loading, error, refresh } = useRecord(
    fetcher,
    "Could not load billing",
  );
  return { digest: data, loading, error, refresh };
}

function mergeMetadata(
  previous: Record<string, unknown>,
  patch: Record<string, unknown>,
): WorkspaceSettings["metadata"] {
  const next: Record<string, unknown> = { ...previous };
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) {
      delete next[key];
    } else {
      next[key] = value;
    }
  }
  return next as WorkspaceSettings["metadata"];
}
