// PR 3.2 — archive-merge + mutation helpers for the Draft tab.
//
// Wraps PR 1.3's drafts registry and API client:
//   - `listDrafts(conversationId)` seeds the conversation slice.
//   - `applyDraftUpdatedEvent` (live SSE) upserts higher versions.
//   - `patchDraft / sendDraft / discardDraft` round-trip the mutations
//     and fold the response back into the registry.
//
// The hook deliberately exposes the **mutations** (not just data) so the
// Draft tab stays presentational. ChatScreen lifts the registry; this
// hook is the bridge.

import type {
  Draft,
  DraftDiscardRequest,
  DraftPatchRequest,
  DraftSendRequest,
  DraftSendResponse,
} from "@enterprise-search/api-types";
import { useCallback, useEffect, useState } from "react";

import {
  discardDraft as discardDraftApi,
  listDrafts as listDraftsApi,
  patchDraft as patchDraftApi,
  sendDraft as sendDraftApi,
} from "../../../../api/agentApi";
import type { RequestIdentity } from "../../../../api/config";
import {
  draftsForConversation,
  draftsByCreatedAt,
  emptyDraftRegistry,
  seedDrafts,
  upsertDraft,
  type DraftRegistryByConversation,
} from "../../chatModel/draftsRegistry";

export interface DraftsState {
  registry: DraftRegistryByConversation;
  setRegistry: (
    next:
      | DraftRegistryByConversation
      | ((current: DraftRegistryByConversation) => DraftRegistryByConversation),
  ) => void;
  drafts: readonly Draft[];
  /** Latest draft by `created_at` — what the Draft tab focuses on. */
  latest: Draft | null;
  loading: boolean;
  error: string | null;
  /** Edit a draft title + content. Returns the new version on success. */
  patch: (draftId: string, request: DraftPatchRequest) => Promise<Draft>;
  /** Send a draft via the connector approval flow. */
  send: (
    draftId: string,
    request: DraftSendRequest,
  ) => Promise<DraftSendResponse>;
  /** Soft-delete a draft. */
  discard: (draftId: string, request: DraftDiscardRequest) => Promise<Draft>;
}

export function useDrafts(
  conversationId: string | null,
  identity: RequestIdentity | null,
): DraftsState {
  const [registry, setRegistry] =
    useState<DraftRegistryByConversation>(emptyDraftRegistry);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (conversationId === null || identity === null) {
      setError(null);
      return undefined;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    void listDraftsApi(conversationId, identity)
      .then((response) => {
        if (cancelled) {
          return;
        }
        setRegistry((current) =>
          seedDrafts(current, conversationId, response.drafts),
        );
      })
      .catch((err: unknown) => {
        if (cancelled) {
          return;
        }
        setError(err instanceof Error ? err.message : "Could not load drafts");
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, identity]);

  const patch = useCallback<DraftsState["patch"]>(
    async (draftId, request) => {
      if (identity === null) {
        throw new Error("Not signed in");
      }
      const next = await patchDraftApi(draftId, request, identity);
      setRegistry((current) => upsertDraft(current, next));
      return next;
    },
    [identity],
  );

  const send = useCallback<DraftsState["send"]>(
    async (draftId, request) => {
      if (identity === null) {
        throw new Error("Not signed in");
      }
      const response = await sendDraftApi(draftId, request, identity);
      setRegistry((current) => upsertDraft(current, response.draft));
      return response;
    },
    [identity],
  );

  const discard = useCallback<DraftsState["discard"]>(
    async (draftId, request) => {
      if (identity === null) {
        throw new Error("Not signed in");
      }
      const next = await discardDraftApi(draftId, request, identity);
      setRegistry((current) => upsertDraft(current, next));
      return next;
    },
    [identity],
  );

  const drafts = draftsByCreatedAt(
    draftsForConversation(registry, conversationId),
  );
  const latest =
    drafts.length === 0 ? null : (drafts[drafts.length - 1] ?? null);

  return {
    registry,
    setRegistry,
    drafts,
    latest,
    loading,
    error,
    patch,
    send,
    discard,
  };
}
