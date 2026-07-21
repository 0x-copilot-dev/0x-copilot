// useRunTranscript — the Run cockpit's chat transcript, as a projection.
//
// The cockpit reads ONE event source (useRunSession.events, FR-3.3), yet the
// chat used to render a stale one-time GET and drop the streamed reply entirely.
// This hook is the missing binder: it composes the durable transcript from the
// two honest sources and hands TcChat a ready message list (TcChat stays
// presentational).
//
// WHY TWO SOURCES — the run stream carries no user_message event and RESETS per
// run (events = only the active run's frames, from seq 0). So:
//   - persisted history (GET /messages) owns user turns + all completed replies;
//   - the live projection (projectChatMessages) owns ONLY the active run's
//     in-flight assistant reply.
//
// THE STATE MACHINE — history goes stale across run boundaries and while a reply
// streams, so it is re-seeded (a) on conversation/run change and (b) when the
// run reaches a terminal state (its reply is now persisted). Between "run
// completed" and "history refetched" the live overlay is KEPT so the reply never
// blinks out; a content-dedupe drops the overlay once history already carries an
// identical finished reply (e.g. opening an already-completed run). Optimistic
// user echo on send is a deliberate follow-up — a new user turn currently
// appears on the run-start re-seed.

import { useEffect, useMemo, useState } from "react";

import type {
  AgentRunStatus,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";

import { useTransport } from "../../providers/TransportProvider";
import {
  fetchConversationMessages,
  type TcChatMessage,
} from "../../thread-canvas/TcChat";
import { projectChatMessages } from "./chatProjection";

// A run is still producing output in these states; anything else is terminal.
// Mirrors useRunSession's NON_TERMINAL_STATUSES (kept local to avoid a
// cross-module coupling on an internal const).
const ACTIVE_RUN_STATUSES: ReadonlySet<AgentRunStatus> = new Set([
  "queued",
  "running",
  "waiting_for_approval",
  "cancelling",
]);

export interface UseRunTranscriptOptions {
  readonly conversationId: string;
  /** Active run id (null = no run bound yet). Drives the re-seed boundary. */
  readonly runId: string | null;
  /** The run's own status (from useRunSession.runStatus). */
  readonly runStatus: AgentRunStatus | null;
  /** The single run event array (useRunSession.events). */
  readonly events: readonly RuntimeEventEnvelope[];
}

export interface UseRunTranscriptResult {
  /** History ⊕ live in-flight reply, ready for TcChat. */
  readonly messages: readonly TcChatMessage[];
}

/** Concatenated text of a message's text parts (reasoning excluded). */
function messageText(message: TcChatMessage): string {
  return message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("");
}

/** True while any part is still streaming. */
function isStreaming(message: TcChatMessage): boolean {
  return message.parts.some((part) => part.status?.type === "running");
}

export function useRunTranscript(
  options: UseRunTranscriptOptions,
): UseRunTranscriptResult {
  const { conversationId, runId, runStatus, events } = options;
  const transport = useTransport();

  const [history, setHistory] = useState<readonly TcChatMessage[]>([]);
  // The runId whose TERMINAL re-seed has landed. While this !== runId we still
  // overlay the live reply, so it never blinks out mid-settle.
  const [settledRunId, setSettledRunId] = useState<string | null>(null);

  // Seed / re-seed on conversation or run change — a new run means the prior
  // run's reply is persisted and a fresh user turn exists.
  useEffect(() => {
    let cancelled = false;
    fetchConversationMessages(transport, conversationId)
      .then((messages) => {
        if (!cancelled) setHistory(messages);
      })
      .catch(() => {
        if (!cancelled) setHistory([]);
      });
    return () => {
      cancelled = true;
    };
  }, [transport, conversationId, runId]);

  // On terminal, re-seed so history absorbs the just-persisted reply, then mark
  // the run settled so the live overlay can drop without a duplicate.
  useEffect(() => {
    if (
      runId === null ||
      runStatus === null ||
      ACTIVE_RUN_STATUSES.has(runStatus)
    ) {
      return;
    }
    let cancelled = false;
    fetchConversationMessages(transport, conversationId)
      .then((messages) => {
        if (cancelled) return;
        setHistory(messages);
        setSettledRunId(runId);
      })
      .catch(() => {
        // Leave the live overlay in place if the settle fetch fails.
      });
    return () => {
      cancelled = true;
    };
  }, [transport, conversationId, runId, runStatus]);

  const live = useMemo(() => projectChatMessages(events), [events]);

  const messages = useMemo<readonly TcChatMessage[]>(() => {
    const overlay = runId !== null && settledRunId !== runId;
    if (!overlay || live.length === 0) {
      return history;
    }
    const liveMessage = live[0];
    const last = history[history.length - 1];
    // Already persisted (opening a completed run, or a race where the terminal
    // re-seed beat the overlay drop): a finished reply whose text history's last
    // assistant message already carries — don't double it.
    const alreadyPersisted =
      last !== undefined &&
      last.role === "assistant" &&
      !isStreaming(liveMessage) &&
      messageText(liveMessage) !== "" &&
      messageText(last) === messageText(liveMessage);
    return alreadyPersisted ? history : [...history, ...live];
  }, [history, live, runId, settledRunId]);

  return { messages };
}
