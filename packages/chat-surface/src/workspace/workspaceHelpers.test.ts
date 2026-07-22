// WC-P6c — foldCitedToolSources unit tests.
//
// `citation_made` links carry only an ordinal → source_tool_call_id pointer, so a
// Sources row is projected from the cited tool invocation (tool_call + tool_result
// earlier in the SAME stream). These pin the projection + the merge discipline the
// Sources tab relies on.

import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope, SourceEntry } from "@0x-copilot/api-types";

import {
  emptySourceMap,
  foldCitedToolSources,
  seedSourceMap,
} from "./workspaceHelpers";

let seq = 0;

function envelope(
  overrides: Partial<RuntimeEventEnvelope> & {
    event_type: RuntimeEventEnvelope["event_type"];
  },
): RuntimeEventEnvelope {
  seq += 1;
  return {
    event_id: `e-${seq}`,
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: seq,
    activity_kind: "tool",
    payload: {},
    created_at: new Date(1_716_000_000_000 + seq * 1000).toISOString(),
    ...overrides,
  } as RuntimeEventEnvelope;
}

function toolCall(
  callId: string,
  toolName: string,
  args: Record<string, unknown> = {},
): RuntimeEventEnvelope {
  return envelope({
    event_type: "tool_call",
    payload: { call_id: callId, tool_name: toolName, args },
  });
}

function toolResult(
  callId: string,
  toolName: string,
  payload: Record<string, unknown> = {},
): RuntimeEventEnvelope {
  return envelope({
    event_type: "tool_result",
    payload: { call_id: callId, tool_name: toolName, ...payload },
  });
}

function citationMade(callId: string, ordinal = 1): RuntimeEventEnvelope {
  return envelope({
    event_type: "citation_made",
    payload: {
      link: {
        conversation_ordinal: ordinal,
        message_id: "msg-1",
        prose_offset: 0,
        prose_length: 5,
        source_tool_call_id: callId,
      },
    },
  });
}

describe("foldCitedToolSources", () => {
  it("returns base unchanged (same reference) when there are no citation_made events", () => {
    const base = emptySourceMap();
    expect(foldCitedToolSources(base, [toolCall("c1", "web_search")])).toBe(
      base,
    );
  });

  it("projects a cited tool call into a synthetic Source row", () => {
    const out = foldCitedToolSources(emptySourceMap(), [
      toolCall("c1", "web_search", { query: "acme pricing" }),
      toolResult("c1", "web_search", { summary: "Found 3 results" }),
      citationMade("c1"),
    ]);
    const row = out.get("web tool-call:c1");
    expect(row).toBeDefined();
    expect(row?.source_connector).toBe("web");
    expect(row?.title).toBe("web_search — acme pricing");
    expect(row?.snippet).toBe("Found 3 results");
    expect(row?.citation_count).toBe(1);
  });

  it("aggregates citation_count across multiple chips pointing at one call", () => {
    const out = foldCitedToolSources(emptySourceMap(), [
      toolCall("c1", "web_search"),
      citationMade("c1", 1),
      citationMade("c1", 2),
    ]);
    expect(out.get("web tool-call:c1")?.citation_count).toBe(2);
  });

  it("unwraps an MCP call_tool wrapper to the real server/tool", () => {
    const out = foldCitedToolSources(emptySourceMap(), [
      toolCall("c1", "call_tool", {
        server_name: "Linear",
        tool_name: "list_issues",
        arguments: { query: "open bugs" },
      }),
      citationMade("c1"),
    ]);
    const row = out.get("linear tool-call:c1");
    expect(row?.source_connector).toBe("linear");
    expect(row?.title).toBe("Linear.list_issues — open bugs");
  });

  it("skips a hallucinated ordinal (empty source_tool_call_id)", () => {
    const base = emptySourceMap();
    expect(foldCitedToolSources(base, [citationMade("")])).toBe(base);
  });

  it("marks a failed tool call in the snippet", () => {
    const out = foldCitedToolSources(emptySourceMap(), [
      toolCall("c1", "web_search"),
      toolResult("c1", "web_search", { status: "failed" }),
      citationMade("c1"),
    ]);
    expect(out.get("web tool-call:c1")?.snippet).toBe("(tool call failed)");
  });

  it("keeps a pre-existing (richer source_ingested) row on key collision", () => {
    const seeded: SourceEntry = {
      citation_id: "tool:c1",
      source_connector: "web",
      source_doc_id: "tool-call:c1",
      source_url: "https://example.com",
      title: "Richer row",
      snippet: "richer",
      freshness_at: null,
      citation_count: 5,
      last_cited_at: new Date(1_699_000_000_000).toISOString(),
    };
    const base = seedSourceMap([seeded]);
    const out = foldCitedToolSources(base, [
      toolCall("c1", "web_search"),
      citationMade("c1"),
    ]);
    // The base row wins — not overwritten by the synthetic projection.
    expect(out.get("web tool-call:c1")?.title).toBe("Richer row");
    expect(out.get("web tool-call:c1")?.citation_count).toBe(5);
  });
});
