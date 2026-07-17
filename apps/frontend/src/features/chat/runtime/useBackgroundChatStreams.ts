import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";
import { useCallback, useMemo, useRef, useState } from "react";
import type { AgentEventStream } from "../../../api/agentApi";
import {
  applyEventToSlot,
  emptySlot,
  evictColdContent,
  liveConversationIds,
  markRunTerminal,
  type BackgroundSlot,
} from "./backgroundSlots";

/**
 * PR 2.2.1 — Background chat streams.
 *
 * Owns the per-conversation slots Map and the per-run stream registry
 * for chats that are **not** currently visible. The visible chat keeps
 * its state in `ChatScreen`'s useState; on switch-away ChatScreen calls
 * `freezeVisible()` to snapshot it here, and on switch-back `thaw()` to
 * restore it. Stream events arriving for a non-visible run are applied
 * directly to the matching slot via `routeEvent()` so a backgrounded
 * run keeps streaming without the UI being mounted.
 *
 * Stream lifecycle (open / reconnect / close) is owned by the registry
 * here — there is no second `streamRef` in `ChatScreen`. The visible
 * conv's stream is registered by `ChatScreen` via `registerStream()`
 * and looked up by `getStream(runId)`; on terminal events the registry
 * closes and removes the entry, regardless of whether the conv was
 * visible at the moment.
 */

const SLOT_CONTENT_CAP = 8;

interface RunStreamRecord {
  runId: string;
  conversationId: string;
  stream: AgentEventStream;
  reconnectTimer: number | null;
}

export interface BackgroundChatStreams {
  /** Map<convId, slot> for snapshot rendering / debug only — do not mutate. */
  slots: ReadonlyMap<string, BackgroundSlot>;
  /** Set of convIds whose slot has a non-null activeRunId. */
  liveConvIds: ReadonlySet<string>;
  /** Snapshot the visible conversation's state into the Map. */
  freezeVisible(args: {
    conversationId: string;
    snapshot: Omit<BackgroundSlot, "lastVisibleAt">;
  }): void;
  /** Restore a slot for the conversation we're switching to. */
  thaw(conversationId: string): BackgroundSlot | null;
  /** Apply a runtime event to the slot owning `event.run_id`, if any. */
  routeEvent(event: RuntimeEventEnvelope): boolean;
  /** Mark a run terminal in its slot (does not close the stream). */
  markTerminal(runId: string, status: string): void;
  /** Look up the conversation that owns this run. */
  conversationIdForRun(runId: string): string | null;
  /** Register a stream so the registry can close it on terminal events. */
  registerStream(args: {
    runId: string;
    conversationId: string;
    stream: AgentEventStream;
  }): void;
  /** Replace the timer associated with a registered stream's reconnect cycle. */
  setReconnectTimer(runId: string, timer: number | null): void;
  /** Close + drop a stream from the registry. Idempotent. */
  closeStream(runId: string): void;
  /** Bookkeep `runId → user_message_id` on the slot for the conv. */
  rememberUserMessageId(args: {
    conversationId: string;
    runId: string;
    userMessageId: string;
  }): void;
  /** Drop heavyweight slot content for cold conversations (LRU). */
  pruneColdContent(protectedConvIds: ReadonlySet<string>): void;
  /** Hard-clear everything (used on auth context teardown). */
  reset(): void;
}

export function useBackgroundChatStreams(): BackgroundChatStreams {
  const [slots, setSlots] = useState<ReadonlyMap<string, BackgroundSlot>>(
    () => new Map(),
  );
  const streamsRef = useRef(new Map<string, RunStreamRecord>());

  const conversationIdForRun = useCallback(
    (runId: string): string | null =>
      streamsRef.current.get(runId)?.conversationId ?? null,
    [],
  );

  const freezeVisible = useCallback<BackgroundChatStreams["freezeVisible"]>(
    ({ conversationId, snapshot }) => {
      setSlots((current) => {
        const next = new Map(current);
        next.set(conversationId, { ...snapshot, lastVisibleAt: Date.now() });
        return next;
      });
    },
    [],
  );

  const thaw = useCallback<BackgroundChatStreams["thaw"]>(
    (conversationId) => slots.get(conversationId) ?? null,
    [slots],
  );

  const routeEvent = useCallback<BackgroundChatStreams["routeEvent"]>(
    (event) => {
      const ownerConvId = streamsRef.current.get(event.run_id)?.conversationId;
      if (!ownerConvId) {
        return false;
      }
      setSlots((current) => {
        const slot = current.get(ownerConvId) ?? emptySlot();
        const next = new Map(current);
        next.set(ownerConvId, applyEventToSlot(slot, event));
        return next;
      });
      return true;
    },
    [],
  );

  const markTerminal = useCallback<BackgroundChatStreams["markTerminal"]>(
    (runId, status) => {
      const ownerConvId = streamsRef.current.get(runId)?.conversationId ?? null;
      if (!ownerConvId) {
        return;
      }
      setSlots((current) => {
        const slot = current.get(ownerConvId);
        if (!slot) {
          return current;
        }
        const next = new Map(current);
        next.set(ownerConvId, markRunTerminal(slot, runId, status));
        return next;
      });
    },
    [],
  );

  const registerStream = useCallback<BackgroundChatStreams["registerStream"]>(
    ({ runId, conversationId, stream }) => {
      const existing = streamsRef.current.get(runId);
      if (existing) {
        // Replace the underlying stream (e.g. reconnect after onError),
        // keep the reconnectTimer slot so the caller can clear it.
        existing.stream.close();
        existing.stream = stream;
        existing.conversationId = conversationId;
        return;
      }
      streamsRef.current.set(runId, {
        runId,
        conversationId,
        stream,
        reconnectTimer: null,
      });
    },
    [],
  );

  const setReconnectTimer = useCallback<
    BackgroundChatStreams["setReconnectTimer"]
  >((runId, timer) => {
    const record = streamsRef.current.get(runId);
    if (!record) {
      return;
    }
    if (record.reconnectTimer !== null) {
      window.clearTimeout(record.reconnectTimer);
    }
    record.reconnectTimer = timer;
  }, []);

  const closeStream = useCallback<BackgroundChatStreams["closeStream"]>(
    (runId) => {
      const record = streamsRef.current.get(runId);
      if (!record) {
        return;
      }
      if (record.reconnectTimer !== null) {
        window.clearTimeout(record.reconnectTimer);
      }
      record.stream.close();
      streamsRef.current.delete(runId);
    },
    [],
  );

  const rememberUserMessageId = useCallback<
    BackgroundChatStreams["rememberUserMessageId"]
  >(({ conversationId, runId, userMessageId }) => {
    setSlots((current) => {
      const slot = current.get(conversationId) ?? emptySlot();
      if (slot.userMessageIdByRunId.get(runId) === userMessageId) {
        return current;
      }
      const userMessageIdByRunId = new Map(slot.userMessageIdByRunId);
      userMessageIdByRunId.set(runId, userMessageId);
      const next = new Map(current);
      next.set(conversationId, { ...slot, userMessageIdByRunId });
      return next;
    });
  }, []);

  const pruneColdContent = useCallback<
    BackgroundChatStreams["pruneColdContent"]
  >((protectedConvIds) => {
    setSlots((current) =>
      evictColdContent(current, SLOT_CONTENT_CAP, protectedConvIds),
    );
  }, []);

  const reset = useCallback<BackgroundChatStreams["reset"]>(() => {
    for (const record of streamsRef.current.values()) {
      if (record.reconnectTimer !== null) {
        window.clearTimeout(record.reconnectTimer);
      }
      record.stream.close();
    }
    streamsRef.current.clear();
    setSlots(new Map());
  }, []);

  const liveConvIds = useMemo(() => liveConversationIds(slots), [slots]);

  return useMemo(
    () => ({
      slots,
      liveConvIds,
      freezeVisible,
      thaw,
      routeEvent,
      markTerminal,
      conversationIdForRun,
      registerStream,
      setReconnectTimer,
      closeStream,
      rememberUserMessageId,
      pruneColdContent,
      reset,
    }),
    [
      slots,
      liveConvIds,
      freezeVisible,
      thaw,
      routeEvent,
      markTerminal,
      conversationIdForRun,
      registerStream,
      setReconnectTimer,
      closeStream,
      rememberUserMessageId,
      pruneColdContent,
      reset,
    ],
  );
}
