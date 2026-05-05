// PR 4.2 — data hooks for the Settings → "Workspace" group.
//
// Three small hooks, all single-fetch + manual refresh in the same shape as
// `useWorkspaceDefaults` (PR 3.5). No global cache layer; the Settings page
// hydrates once and refreshes only on user-driven mutations.

import { useCallback, useEffect, useState } from "react";
import type {
  BillingDigest,
  CreateInvitationRequest,
  CreateInvitationResponse,
  Invitation,
  Member,
  UpdateMemberRequest,
  UpdateWorkspaceSettingsRequest,
  WorkspaceSettings,
} from "@enterprise-search/api-types";
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
  const [workspace, setWorkspace] = useState<WorkspaceSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const data = await getWorkspace(identity);
      setWorkspace(data);
    } catch (err) {
      setError(toMessage(err, "Could not load workspace"));
    } finally {
      setLoading(false);
    }
  }, [identity]);

  useEffect(() => {
    let cancelled = false;
    void load().catch(() => {
      if (cancelled) return;
    });
    return () => {
      cancelled = true;
    };
  }, [load]);

  const save = useCallback(
    async (body: UpdateWorkspaceSettingsRequest): Promise<void> => {
      const previous = workspace;
      // Optimistic — same shape as useWorkspaceDefaults.
      if (previous) {
        setWorkspace({
          ...previous,
          display_name: body.display_name ?? previous.display_name,
          slug: body.slug ?? previous.slug,
          metadata: body.metadata
            ? mergeMetadata(previous.metadata, body.metadata)
            : previous.metadata,
        });
      }
      setError(null);
      try {
        const updated = await patchWorkspace(body, identity);
        setWorkspace(updated);
      } catch (err) {
        setWorkspace(previous);
        setError(toMessage(err, "Could not save workspace"));
        throw err;
      }
    },
    [identity, workspace],
  );

  return { workspace, loading, error, save, refresh: load };
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
  const [members, setMembers] = useState<Member[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const response = await listWorkspaceMembers(identity);
      setMembers(response.members);
    } catch (err) {
      setError(toMessage(err, "Could not load members"));
    } finally {
      setLoading(false);
    }
  }, [identity]);

  useEffect(() => {
    void load();
  }, [load]);

  const changeRole = useCallback(
    async (
      userId: string,
      role: UpdateMemberRequest["role"],
    ): Promise<void> => {
      try {
        const updated = await patchWorkspaceMember(userId, { role }, identity);
        setMembers((current) =>
          current.map((m) => (m.user_id === userId ? updated : m)),
        );
      } catch (err) {
        setError(toMessage(err, "Could not update role"));
        throw err;
      }
    },
    [identity],
  );

  const remove = useCallback(
    async (userId: string): Promise<void> => {
      const previous = members;
      setMembers((current) => current.filter((m) => m.user_id !== userId));
      try {
        await removeWorkspaceMember(userId, identity);
      } catch (err) {
        setMembers(previous);
        setError(toMessage(err, "Could not remove member"));
        throw err;
      }
    },
    [identity, members],
  );

  return { members, loading, error, refresh: load, changeRole, remove };
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
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const response = await listInvitations(identity);
      setInvitations(response.invitations);
    } catch (err) {
      setError(toMessage(err, "Could not load invitations"));
    } finally {
      setLoading(false);
    }
  }, [identity]);

  useEffect(() => {
    void load();
  }, [load]);

  const create = useCallback(
    async (
      body: CreateInvitationRequest,
    ): Promise<CreateInvitationResponse> => {
      try {
        const response = await createInvitation(body, identity);
        setInvitations((current) => [
          {
            invite_id: response.invite_id,
            email: response.email,
            role: response.role,
            token_prefix: response.token_prefix,
            created_by: response.created_by,
            created_at: response.created_at,
            expires_at: response.expires_at,
          },
          ...current,
        ]);
        return response;
      } catch (err) {
        setError(toMessage(err, "Could not create invitation"));
        throw err;
      }
    },
    [identity],
  );

  const revoke = useCallback(
    async (inviteId: string): Promise<void> => {
      const previous = invitations;
      setInvitations((current) =>
        current.filter((i) => i.invite_id !== inviteId),
      );
      try {
        await revokeInvitation(inviteId, identity);
      } catch (err) {
        setInvitations(previous);
        setError(toMessage(err, "Could not revoke invitation"));
        throw err;
      }
    },
    [identity, invitations],
  );

  return { invitations, loading, error, refresh: load, create, revoke };
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
  const [digest, setDigest] = useState<BillingDigest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const data = await getBillingDigest(identity);
      setDigest(data);
    } catch (err) {
      setError(toMessage(err, "Could not load billing"));
    } finally {
      setLoading(false);
    }
  }, [identity]);

  useEffect(() => {
    void load();
  }, [load]);

  return { digest, loading, error, refresh: load };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function toMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  return fallback;
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
