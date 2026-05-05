// PR 1.5 — hook backing the Workspace pane Agents tab.
//
// Seeds from `GET /v1/agent/conversations/{cid}/subagents` on conversation
// open and projects every subsequent `SUBAGENT_*` envelope through the
// existing pure reducer in `chatModel/subagentReducer.ts`. The hook is
// deliberately stateless across conversation switches — switching seeds
// from scratch, mirroring how `ChatScreen` already reloads messages.

import { useEffect, useReducer } from "react";

import type {
  RuntimeEventEnvelope,
  SubagentEntry,
} from "@enterprise-search/api-types";

import { listSubagents } from "../../../api/agentApi";
import type { RequestIdentity } from "../../../api/config";
import {
  applySubagentEvent,
  emptySubagentMap,
  seedSubagentMap,
  subagentsByRecency,
  type SubagentSnapshotMap,
} from "../chatModel/subagentReducer";

export type ConversationSubagentsState = {
  byTaskId: SubagentSnapshotMap;
  entries: readonly SubagentEntry[];
  loading: boolean;
  error: string | null;
};

type Action =
  | { kind: "loading" }
  | { kind: "seed"; entries: readonly SubagentEntry[] }
  | { kind: "live"; event: RuntimeEventEnvelope }
  | { kind: "error"; message: string }
  | { kind: "reset" };

const INITIAL: ConversationSubagentsState = {
  byTaskId: emptySubagentMap(),
  entries: [],
  loading: false,
  error: null,
};

function reducer(
  state: ConversationSubagentsState,
  action: Action,
): ConversationSubagentsState {
  switch (action.kind) {
    case "loading":
      return { ...state, loading: true, error: null };
    case "seed": {
      const byTaskId = seedSubagentMap(action.entries);
      return {
        loading: false,
        error: null,
        byTaskId,
        entries: subagentsByRecency(byTaskId),
      };
    }
    case "live": {
      const byTaskId = applySubagentEvent(state.byTaskId, action.event);
      if (byTaskId === state.byTaskId) {
        return state;
      }
      return {
        ...state,
        byTaskId,
        entries: subagentsByRecency(byTaskId),
      };
    }
    case "error":
      return { ...state, loading: false, error: action.message };
    case "reset":
      return INITIAL;
  }
}

export function useConversationSubagents(opts: {
  conversationId: string | null;
  identity: RequestIdentity;
  liveEvent: RuntimeEventEnvelope | null;
}): ConversationSubagentsState {
  const [state, dispatch] = useReducer(reducer, INITIAL);

  useEffect(() => {
    if (opts.conversationId === null) {
      dispatch({ kind: "reset" });
      return;
    }
    let cancelled = false;
    dispatch({ kind: "loading" });
    listSubagents(opts.conversationId, opts.identity, {
      status: "recent",
      limit: 50,
    })
      .then((response) => {
        if (cancelled) {
          return;
        }
        dispatch({ kind: "seed", entries: response.subagents });
      })
      .catch((err: unknown) => {
        if (cancelled) {
          return;
        }
        dispatch({ kind: "error", message: messageFor(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [opts.conversationId, opts.identity]);

  useEffect(() => {
    if (opts.liveEvent === null) {
      return;
    }
    dispatch({ kind: "live", event: opts.liveEvent });
  }, [opts.liveEvent]);

  return state;
}

function messageFor(err: unknown): string {
  return err instanceof Error
    ? err.message
    : "Could not load subagent activity.";
}
