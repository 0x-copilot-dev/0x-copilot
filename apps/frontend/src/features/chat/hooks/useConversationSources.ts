// PR 1.5 — hook backing the Workspace pane Sources tab.
//
// Seeds from `GET /v1/agent/conversations/{cid}/sources` on conversation
// open and projects every subsequent `source_ingested` envelope through
// the existing pure reducer in `chatModel/sourcesReducer.ts`. The hook
// owns the conversation-wide aggregate; PR 1.1's
// `chatModel/citationsRegistry` continues to own the per-run map used
// inline in the chat thread.

import { useEffect, useReducer } from "react";

import type {
  RuntimeEventEnvelope,
  SourceEntry,
} from "@enterprise-search/api-types";

import { listSources } from "../../../api/agentApi";
import type { RequestIdentity } from "../../../api/config";
import {
  applySourceEvent,
  emptySourceMap,
  seedSourceMap,
  sourcesByCitationCount,
  type SourceEntryMap,
} from "../chatModel/sourcesReducer";

export type ConversationSourcesState = {
  byKey: SourceEntryMap;
  entries: readonly SourceEntry[];
  loading: boolean;
  error: string | null;
};

type Action =
  | { kind: "loading" }
  | { kind: "seed"; entries: readonly SourceEntry[] }
  | { kind: "live"; event: RuntimeEventEnvelope }
  | { kind: "error"; message: string }
  | { kind: "reset" };

const INITIAL: ConversationSourcesState = {
  byKey: emptySourceMap(),
  entries: [],
  loading: false,
  error: null,
};

function reducer(
  state: ConversationSourcesState,
  action: Action,
): ConversationSourcesState {
  switch (action.kind) {
    case "loading":
      return { ...state, loading: true, error: null };
    case "seed": {
      const byKey = seedSourceMap(action.entries);
      return {
        loading: false,
        error: null,
        byKey,
        entries: sourcesByCitationCount(byKey),
      };
    }
    case "live": {
      const byKey = applySourceEvent(state.byKey, action.event);
      if (byKey === state.byKey) {
        return state;
      }
      return {
        ...state,
        byKey,
        entries: sourcesByCitationCount(byKey),
      };
    }
    case "error":
      return { ...state, loading: false, error: action.message };
    case "reset":
      return INITIAL;
  }
}

export function useConversationSources(opts: {
  conversationId: string | null;
  identity: RequestIdentity;
  liveEvent: RuntimeEventEnvelope | null;
}): ConversationSourcesState {
  const [state, dispatch] = useReducer(reducer, INITIAL);

  useEffect(() => {
    if (opts.conversationId === null) {
      dispatch({ kind: "reset" });
      return;
    }
    let cancelled = false;
    dispatch({ kind: "loading" });
    listSources(opts.conversationId, opts.identity, { limit: 200 })
      .then((response) => {
        if (cancelled) {
          return;
        }
        dispatch({ kind: "seed", entries: response.sources });
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
    : "Could not load citation sources.";
}
