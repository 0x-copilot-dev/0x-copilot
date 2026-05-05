import { describe, expect, it } from "vitest";
import type {
  CitationSourceRef,
  RuntimeEventEnvelope,
  SourceEntry,
} from "@enterprise-search/api-types";

import {
  applySourceEvent,
  emptySourceMap,
  seedSourceMap,
  sourcesByCitationCount,
} from "./sourcesReducer";

const RUN_ID = "run_alpha";
const CONVERSATION_ID = "conv_launch";

function citation(
  overrides: Partial<CitationSourceRef> = {},
): CitationSourceRef {
  return {
    citation_id: "c001",
    ordinal: 1,
    source_connector: "notion",
    source_doc_id: "doc_positioning",
    source_url: "https://example.com/notion/doc_positioning",
    title: "Aurora 4.0 — Approved Positioning",
    snippet: "Aurora 4.0 brings agentic search to every desk.",
    freshness_at: null,
    source_tool_call_id: null,
    ...overrides,
  };
}

function ingestedEvent(
  cite: CitationSourceRef,
  overrides: Partial<RuntimeEventEnvelope> = {},
): RuntimeEventEnvelope {
  return {
    event_id: `evt_${cite.citation_id}`,
    run_id: RUN_ID,
    conversation_id: CONVERSATION_ID,
    sequence_no: 1,
    activity_kind: "tool",
    created_at: "2026-05-04T12:00:00Z",
    source: "tool",
    event_type: "source_ingested",
    payload: { citation: cite },
    metadata: {},
    visibility: "user",
    redaction_state: "redacted",
    trace_id: "trace_1",
    ...overrides,
  } as RuntimeEventEnvelope;
}

function entry(overrides: Partial<SourceEntry> = {}): SourceEntry {
  return {
    citation_id: "c001",
    source_connector: "notion",
    source_doc_id: "doc_positioning",
    source_url: "https://example.com/notion/doc_positioning",
    title: "Aurora 4.0 — Approved Positioning",
    snippet: "Aurora 4.0 brings agentic search to every desk.",
    freshness_at: null,
    citation_count: 1,
    last_cited_at: "2026-05-04T12:00:00Z",
    ...overrides,
  };
}

describe("applySourceEvent", () => {
  it("creates a new source on first source_ingested", () => {
    const next = applySourceEvent(emptySourceMap(), ingestedEvent(citation()));
    const row = sourcesByCitationCount(next)[0];
    expect(row.source_doc_id).toBe("doc_positioning");
    expect(row.citation_count).toBe(1);
  });

  it("aggregates count when the same doc is re-cited", () => {
    const first = applySourceEvent(emptySourceMap(), ingestedEvent(citation()));
    const second = applySourceEvent(
      first,
      ingestedEvent(citation({ citation_id: "c002", ordinal: 2 }), {
        sequence_no: 2,
        created_at: "2026-05-04T12:00:05Z",
      }),
    );
    const row = sourcesByCitationCount(second)[0];
    expect(row.citation_count).toBe(2);
    expect(row.last_cited_at).toBe("2026-05-04T12:00:05Z");
    // Latest citation_id wins so chips can resolve through it.
    expect(row.citation_id).toBe("c002");
  });

  it("returns same map identity for non-source events", () => {
    const seeded = seedSourceMap([entry()]);
    const noop = applySourceEvent(seeded, {
      ...ingestedEvent(citation()),
      event_type: "model_delta",
      payload: {},
    } as RuntimeEventEnvelope);
    expect(noop).toBe(seeded);
  });

  it("ranks by citation_count desc then last_cited_at desc", () => {
    let map = emptySourceMap();
    map = applySourceEvent(
      map,
      ingestedEvent(citation({ citation_id: "c001", source_doc_id: "doc_a" }), {
        sequence_no: 1,
        created_at: "2026-05-04T12:00:00Z",
      }),
    );
    map = applySourceEvent(
      map,
      ingestedEvent(
        citation({ citation_id: "c002", source_doc_id: "doc_a", ordinal: 2 }),
        { sequence_no: 2, created_at: "2026-05-04T12:00:01Z" },
      ),
    );
    map = applySourceEvent(
      map,
      ingestedEvent(
        citation({ citation_id: "c003", source_doc_id: "doc_b", ordinal: 3 }),
        { sequence_no: 3, created_at: "2026-05-04T12:00:02Z" },
      ),
    );
    const order = sourcesByCitationCount(map).map((row) => row.source_doc_id);
    expect(order).toEqual(["doc_a", "doc_b"]);
  });
});

describe("seedSourceMap", () => {
  it("keys entries by (connector, doc_id)", () => {
    const seeded = seedSourceMap([
      entry({ source_doc_id: "doc_a" }),
      entry({ source_doc_id: "doc_b" }),
    ]);
    expect(sourcesByCitationCount(seeded)).toHaveLength(2);
  });
});
