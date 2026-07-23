// ChatsArchiveRoute — host binder for the Phase 4 Chats destination
// (desktop redesign · PR-4.3).
//
// Source: docs/plan/desktop-redesign/phase-4/PRD.md §3 (US-4.1),
// FR-4.7 / FR-4.8 / FR-4.9, §7 (PR-4.3).
//
// The pure-presentation `<ChatsArchive>` lives once in
// `@0x-copilot/chat-surface`; this route is the web app's BINDER that
//   1. fetches `/v1/agent/conversations` (incl. archived) via `chatsApi`
//      and buckets it into the pinned / recent / archived read model,
//   2. drives the 4-state machine by feeding the destination a
//      `SectionResult<ChatsArchive> | null` (`null` = loading),
//   3. wires the navigation callbacks the destination cannot own:
//        * onReopen(id)  → open the Run cockpit for that conversation,
//        * onNewChat()   → create a fresh conversation, then open Run on it,
//        * onRetry()     → refetch.
//
// NAVIGATION SEAM (for the IA-fold pass, PR-4.11): reopen/new-chat both
// funnel through a single host-supplied `onOpenRun(conversationId)`
// callback. The route deliberately does NOT reach into the app Router
// itself — there is no `run`-with-conversation `AppRoute` yet (Phase 3),
// and PR-4.11 owns dispatch/wiring. Keeping ONE injected callback lets
// IA-fold bind it to the app's run navigation (`ArtifactRoute.run` on the
// desktop substrate / the `run` destination on web) in exactly one place,
// consistent with how the PRD reaches host screens via callbacks. This
// route is NOT mounted in App.tsx here (scope: apps/frontend, PR-4.3); the
// dispatch entry lands in PR-4.11.

import { useCallback, useEffect, useState, type ReactElement } from "react";

import type { ConversationId } from "@0x-copilot/api-types";
import { ChatsArchive, useChatsArchive } from "@0x-copilot/chat-surface";

import { createConversation } from "../../api/agentApi";
import type { RequestIdentity } from "../../api/config";
import { errorMessage } from "../../utils/errors";
import { migrateLegacyPins } from "./migrateLegacyPins";

export interface ChatsArchiveRouteProps {
  readonly identity: RequestIdentity;
  /**
   * Host navigation seam (FR-4.7 / FR-4.8). Reopen passes an existing
   * conversation id; New chat passes the freshly-created conversation's
   * id. IA-fold (PR-4.11) wires this to the app's run navigation.
   */
  readonly onOpenRun: (conversationId: ConversationId) => void;
}

const rootStyle = {
  height: "100%",
  width: "100%",
  minHeight: 0,
  display: "flex",
  flexDirection: "column" as const,
};

const bannerStyle = {
  flex: "0 0 auto",
  margin: "12px 12px 0",
  padding: "10px 12px",
  border: "1px solid var(--color-border-strong, #2a2a2c)",
  borderRadius: "var(--radius-sm, 6px)",
  backgroundColor: "var(--color-surface, #161617)",
  color: "var(--color-text, #ededee)",
  fontSize: 13,
};

const surfaceStyle = {
  flex: "1 1 auto",
  minHeight: 0,
};

export function ChatsArchiveRoute({
  identity,
  onOpenRun,
}: ChatsArchiveRouteProps): ReactElement {
  // PRD-09 D1 — the shared controller owns the whole read/write model (three
  // bucket-scoped cursored fetches, the SSE live tail, pin/archive, load-more).
  // The route keeps ONLY navigation + the New-chat error banner.
  const { archive, hasMore, onLoadMore, onTogglePin, onToggleArchive, retry } =
    useChatsArchive();
  // Non-fatal New-chat failure surfaces as a banner without wiping the
  // archive (the list keeps rendering, mirroring ProjectsRoute's
  // pendingError pattern).
  const [newChatError, setNewChatError] = useState<string | null>(null);

  // PRD-09 D2 — one-shot, bounded replay of the retired localStorage pins onto
  // the real endpoint, so nobody loses their pins. Idempotent + fire-and-forget.
  useEffect(() => {
    void migrateLegacyPins(identity.userId ?? null, identity);
  }, [identity]);

  const handleNewChat = useCallback(async () => {
    setNewChatError(null);
    try {
      // "Open Run on a fresh conversation" (FR-4.8): create the row, then
      // hand its id to the host navigation seam.
      const conversation = await createConversation(identity);
      onOpenRun(conversation.conversation_id as ConversationId);
    } catch (error: unknown) {
      setNewChatError(errorMessage(error, "Could not start a new chat."));
    }
  }, [identity, onOpenRun]);

  return (
    <div data-testid="chats-archive-route" style={rootStyle}>
      {newChatError !== null ? (
        <div
          role="alert"
          data-testid="chats-archive-route-error"
          style={bannerStyle}
        >
          {newChatError}
        </div>
      ) : null}
      <div style={surfaceStyle}>
        <ChatsArchive
          archive={archive}
          onReopen={onOpenRun}
          onNewChat={() => {
            void handleNewChat();
          }}
          onRetry={retry}
          onTogglePin={onTogglePin}
          onToggleArchive={onToggleArchive}
          onLoadMore={onLoadMore}
          hasMore={hasMore}
        />
      </div>
    </div>
  );
}
