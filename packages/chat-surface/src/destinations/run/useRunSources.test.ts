// @vitest-environment jsdom
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { describe, expect, it } from "vitest";

import type {
  AgentRunStatus,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";
import type {
  Session,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import { TransportProvider } from "../../providers/TransportProvider";
import { useRunSources } from "./useRunSources";

function citation(docId: string, connector = "gdrive") {
  return {
    citation_id: `cit-${docId}`,
    source_connector: connector,
    source_doc_id: docId,
    source_url: null,
    title: docId,
    snippet: null,
    freshness_at: null,
    source_tool_call_id: null,
    ordinal: 1,
  };
}

function sourceEvent(docId: string, seq: number): RuntimeEventEnvelope {
  return {
    event_id: `e${seq}`,
    sequence_no: seq,
    event_type: "source_ingested",
    created_at: new Date(1_700_000_000_000 + seq * 1000).toISOString(),
    payload: { citation: citation(docId) },
  } as unknown as RuntimeEventEnvelope;
}

function runtimeEvent(
  eventType: string,
  seq: number,
  payload: Record<string, unknown>,
): RuntimeEventEnvelope {
  return {
    event_id: `e${seq}`,
    run_id: "r1",
    sequence_no: seq,
    event_type: eventType,
    created_at: new Date(1_700_000_000_000 + seq * 1000).toISOString(),
    payload,
  } as unknown as RuntimeEventEnvelope;
}

// WC-P6c — a cited tool call (citation_made → tool_call/tool_result) that the
// CitationProjector didn't recognise as a source.
function citedToolCallEvents(callId: string): RuntimeEventEnvelope[] {
  return [
    runtimeEvent("tool_call", 1, {
      call_id: callId,
      tool_name: "web_search",
      args: { query: "acme" },
    }),
    runtimeEvent("tool_result", 2, {
      call_id: callId,
      tool_name: "web_search",
      summary: "one result",
    }),
    runtimeEvent("citation_made", 3, {
      link: {
        conversation_ordinal: 1,
        message_id: "m1",
        prose_offset: 0,
        prose_length: 5,
        source_tool_call_id: callId,
      },
    }),
  ];
}

function seededSource(docId: string, count = 1) {
  return {
    ...citation(docId),
    citation_count: count,
    last_cited_at: new Date(1_699_000_000_000).toISOString(),
  };
}

/** Transport whose GET /sources response is read live from `sourcesRef`. */
function makeTransport(sourcesRef: { current: unknown[] }): Transport {
  return {
    request: (async (req: TypedRequest) =>
      typeof req.path === "string" && req.path.endsWith("/sources")
        ? {
            conversation_id: "c",
            run_id: null,
            sources: sourcesRef.current,
            truncated: false,
          }
        : {}) as Transport["request"],
    subscribeServerSentEvents: () => ({ close: () => undefined }),
    getSession: (): Session => ({ bearer: null }),
    capabilities: (): TransportCapabilities => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
}

function wrapper(transport: Transport) {
  return ({ children }: { children: ReactNode }) =>
    createElement(TransportProvider, { transport, children });
}

describe("useRunSources", () => {
  it("merges live source events on top of the persisted seed", async () => {
    const sourcesRef = { current: [seededSource("a"), seededSource("b")] };
    const events = [sourceEvent("c", 1)];
    const { result } = renderHook(
      () =>
        useRunSources({
          conversationId: "c",
          runId: "r1",
          runStatus: "running",
          events,
        }),
      { wrapper: wrapper(makeTransport(sourcesRef)) },
    );
    await waitFor(() => expect(result.current.sources.size).toBe(3));
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("bumps citation_count when a live event re-cites a seeded doc", async () => {
    const sourcesRef = { current: [seededSource("a", 1)] };
    const events = [sourceEvent("a", 1)];
    const { result } = renderHook(
      () =>
        useRunSources({
          conversationId: "c",
          runId: "r1",
          runStatus: "running",
          events,
        }),
      { wrapper: wrapper(makeTransport(sourcesRef)) },
    );
    await waitFor(() =>
      expect(result.current.sources.get("gdrive a")?.citation_count).toBe(2),
    );
    expect(result.current.sources.size).toBe(1);
  });

  it("drops the live fold once the run settles — no double count", async () => {
    const sourcesRef: { current: unknown[] } = { current: [] };
    const events = [sourceEvent("a", 1)];
    const { result, rerender } = renderHook(
      (props: { runStatus: AgentRunStatus }) =>
        useRunSources({
          conversationId: "c",
          runId: "r1",
          runStatus: props.runStatus,
          events,
        }),
      {
        wrapper: wrapper(makeTransport(sourcesRef)),
        initialProps: { runStatus: "running" as AgentRunStatus },
      },
    );
    // Seed empty + live "a" → one source, cited once.
    await waitFor(() =>
      expect(result.current.sources.get("gdrive a")?.citation_count).toBe(1),
    );

    // Run completes: "a" is now persisted with count 1.
    sourcesRef.current = [seededSource("a", 1)];
    rerender({ runStatus: "completed" });

    await waitFor(() => {
      // Settled → seed is authoritative (count 1), NOT seed(1) + live(1) = 2.
      expect(result.current.sources.get("gdrive a")?.citation_count).toBe(1);
      expect(result.current.sources.size).toBe(1);
    });
  });

  it("folds a cited tool call (citation_made) into Sources — and keeps it after settle", async () => {
    const sourcesRef: { current: unknown[] } = { current: [] };
    const events = citedToolCallEvents("call_x");
    const { result, rerender } = renderHook(
      (props: { runStatus: AgentRunStatus }) =>
        useRunSources({
          conversationId: "c",
          runId: "r1",
          runStatus: props.runStatus,
          events,
        }),
      {
        wrapper: wrapper(makeTransport(sourcesRef)),
        initialProps: { runStatus: "running" as AgentRunStatus },
      },
    );
    // Cited tool call surfaces as a synthetic tool-source row (GET /sources is
    // empty — the projector didn't recognise it).
    await waitFor(() => {
      const row = result.current.sources.get("web tool-call:call_x");
      expect(row?.snippet).toBe("one result");
      expect(row?.citation_count).toBe(1);
    });

    // Run completes: the row must SURVIVE settle (it is never in GET /sources).
    rerender({ runStatus: "completed" });
    await waitFor(() => {
      expect(result.current.sources.get("web tool-call:call_x")?.snippet).toBe(
        "one result",
      );
    });
  });
});
