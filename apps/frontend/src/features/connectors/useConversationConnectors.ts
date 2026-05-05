import type {
  Conversation,
  ConversationConnectorScopes,
} from "@enterprise-search/api-types";
import { useCallback, useEffect, useState } from "react";
import type { RequestIdentity } from "../../api/config";
import { updateConversationConnectorScopes } from "../../api/agentApi";

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

  // Re-seed when switching chats. We trust the snapshot the conversation
  // load already returned — no extra GET.
  useEffect(() => {
    setScopes(conversation?.enabled_connectors ?? {});
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
      } catch (err) {
        setScopes(previous);
        setError(
          err instanceof Error ? err.message : "Could not update connectors",
        );
        throw err;
      } finally {
        setLoading(false);
      }
    },
    [conversation, identity, scopes],
  );

  return { scopes, loading, error, patch };
}
