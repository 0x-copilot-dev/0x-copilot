import type {
  Conversation,
  ConversationConnectorScopes,
} from "@enterprise-search/api-types";
import { usePresenceSignal } from "@enterprise-search/chat-surface";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  getConversation,
  updateConversationConnectorScopes,
} from "../../api/agentApi";
import type { RequestIdentity } from "../../api/config";
import { errorMessage } from "../../utils/errors";
import { useWorkspaceConnectorsChanged } from "./invalidation";

/**
 * PR 1.2 — single source of truth for the per-chat connector scope popover.
 *
 * Seeded from the conversation row (already part of the conversation load
 * round-trip). Calls `PATCH /v1/agent/conversations/{id}/connectors` and
 * applies optimistic UI: the popover flips immediately, rolls back on a
 * 4xx. The chat-level toggle never affects an in-flight run — the worker
 * builds capabilities from the run's frozen runtime context. The next run
 * picks up the new scope.
 */
export interface ConversationConnectorScopeState {
  scopes: ConversationConnectorScopes;
  loading: boolean;
  error: string | null;
  /**
   * RFC 7396 merge-patch: send only what changed. `null` pauses, an array
   * activates with the listed scopes, omission leaves the stored value
   * untouched.
   */
  patch: (delta: ConversationConnectorScopes) => Promise<void>;
}

export function useConversationConnectors(
  conversation: Conversation | null,
  identity: RequestIdentity | null,
): ConversationConnectorScopeState {
  const [scopes, setScopes] = useState<ConversationConnectorScopes>(
    conversation?.enabled_connectors ?? {},
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // PR 1.2.1 — track the freshest connectors_updated_at we've observed
  // so the visibilitychange refetch can decide whether to apply server
  // state. We compare against this rather than against the prop so the
  // PATCH-success branch (which doesn't re-thread the prop) still
  // suppresses an immediately-following refetch.
  const lastUpdatedAtRef = useRef<string | null>(
    conversation?.connectors_updated_at ?? null,
  );
  // Mirror `loading` into a ref so the visibility listener can read the
  // current value without re-binding when it changes (a re-bind would
  // tear down and re-add the listener on every PATCH).
  const loadingRef = useRef(loading);
  loadingRef.current = loading;

  // Re-seed when switching chats. We trust the snapshot the conversation
  // load already returned — no extra GET.
  useEffect(() => {
    setScopes(conversation?.enabled_connectors ?? {});
    lastUpdatedAtRef.current = conversation?.connectors_updated_at ?? null;
    setError(null);
  }, [conversation?.conversation_id, conversation?.enabled_connectors]);

  const patch = useCallback(
    async (delta: ConversationConnectorScopes): Promise<void> => {
      if (conversation === null || identity === null) {
        return;
      }
      const previous = scopes;
      setLoading(true);
      setError(null);
      // Optimistic merge — local view of the same merge-patch the server
      // is about to apply. Server response replaces this on success.
      setScopes({ ...previous, ...delta });
      try {
        const response = await updateConversationConnectorScopes(
          conversation.conversation_id,
          { scopes: delta },
          identity,
        );
        setScopes(response.scopes);
        if (response.updated_at) {
          lastUpdatedAtRef.current = response.updated_at;
        }
      } catch (err) {
        setScopes(previous);
        setError(errorMessage(err, "Could not update connectors"));
        throw err;
      } finally {
        setLoading(false);
      }
    },
    [conversation, identity, scopes],
  );

  // Refetch the conversation and apply server-side scopes if the
  // server's connectors_updated_at is strictly newer than what we
  // last observed. Skipped while a PATCH is in flight (loading guard)
  // so the optimistic flip is never clobbered by a stale GET landing
  // first. Used by both the visibility-change reconciliation and the
  // workspace-connectors invalidation channel.
  const reconcileFromServer = useCallback(() => {
    if (conversation === null || identity === null || loadingRef.current) {
      return;
    }
    const conversationId = conversation.conversation_id;
    void getConversation(conversationId, identity)
      .then((fresh) => {
        const serverUpdated = fresh.connectors_updated_at ?? null;
        const localUpdated = lastUpdatedAtRef.current;
        // Strictly-newer comparison: equal timestamps mean no work,
        // older server state means our optimistic UI is ahead and
        // shouldn't be overwritten.
        if (serverUpdated && (!localUpdated || serverUpdated > localUpdated)) {
          setScopes(fresh.enabled_connectors ?? {});
          lastUpdatedAtRef.current = serverUpdated;
        }
      })
      .catch(() => {
        // Reconciliation failures are silent — the user already has
        // working state and connectivity issues surface elsewhere.
      });
  }, [conversation?.conversation_id, identity]);

  // PR 1.2.1 — multi-tab reconciliation. Refetch on tab visibility.
  // Subscribed via the PresenceSignal port — on web that's backed by
  // `document.visibilityState`, on desktop by VS Code's window focus
  // events. No direct `document` reference here.
  const presence = usePresenceSignal();
  useEffect(() => {
    if (conversation === null || identity === null) {
      return undefined;
    }
    return presence.subscribe((state) => {
      if (state !== "visible") {
        return;
      }
      reconcileFromServer();
    });
  }, [conversation?.conversation_id, identity, presence, reconcileFromServer]);

  // Workspace-level connector changes (admin disables/installs a
  // server elsewhere in the tab) invalidate this hook's notion of
  // "what's available." Refetch the conversation so the popover
  // reflects the post-change scope and we don't keep offering tools
  // from a connector the admin just disabled.
  useWorkspaceConnectorsChanged(reconcileFromServer);

  return { scopes, loading, error, patch };
}
