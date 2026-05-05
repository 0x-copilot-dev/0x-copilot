import { describe, expect, it } from "vitest";
import type {
  CitationSourceRef,
  RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import { applyCitationEvent, buildCitationRegistry } from "./citationReducer";
import {
  citationsByOrdinal,
  citationsForRun,
  emptyCitationRegistry,
} from "./citationsRegistry";

const RUN_ID = "run_cite_1";
const CONVERSATION_ID = "conv_cite_1";

function citation(
  overrides: Partial<CitationSourceRef> = {},
): CitationSourceRef {
  return {
    citation_id: "c1",
    ordinal: 1,
    source_connector: "notion",
    source_doc_id: "page_123",
    source_url: "https://example.com/notion/page_123",
    title: "Aurora 4.0 — Approved Positioning v3",
    snippet: "Aurora 4.0 brings agentic search to every desk.",
    freshness_at: null,
    source_tool_call_id: null,
    ...overrides,
  };
}

function event(
  overrides: Partial<RuntimeEventEnvelope> &
    Pick<RuntimeEventEnvelope, "event_type" | "payload">,
): RuntimeEventEnvelope {
  return {
    event_id: "evt_1",
    run_id: RUN_ID,
    conversation_id: CONVERSATION_ID,
    sequence_no: 1,
    activity_kind: "tool",
    created_at: "2026-05-04T12:00:00Z",
    ...overrides,
  } as RuntimeEventEnvelope;
}

describe("applyCitationEvent", () => {
  it("appends a citation on source_ingested", () => {
    const next = applyCitationEvent(
      emptyCitationRegistry(),
      event({
        event_type: "source_ingested",
        payload: { citation: citation() },
      }),
    );
    expect(citationsForRun(next, RUN_ID).get("c1")).toEqual(citation());
  });

  it("is idempotent on the citation_id (replay-safe)", () => {
    const first = applyCitationEvent(
      emptyCitationRegistry(),
      event({
        event_type: "source_ingested",
        payload: { citation: citation() },
      }),
    );
    const second = applyCitationEvent(
      first,
      event({
        event_type: "source_ingested",
        sequence_no: 2,
        payload: { citation: citation() },
      }),
    );
    // Same map identity returned when nothing changes — guards against
    // unnecessary React re-renders.
    expect(second).toBe(first);
  });

  it("seeds from final_response.citations when running standalone", () => {
    const sealed = [
      citation(),
      citation({ citation_id: "c2", ordinal: 2, source_doc_id: "drive_456" }),
    ];
    const next = applyCitationEvent(
      emptyCitationRegistry(),
      event({
        event_type: "final_response",
        payload: { message: "ok", citations: sealed },
      }),
    );
    expect(citationsByOrdinal(citationsForRun(next, RUN_ID))).toEqual(sealed);
  });

  it("ignores unrelated event types", () => {
    const start = emptyCitationRegistry();
    const next = applyCitationEvent(
      start,
      event({ event_type: "model_delta", payload: { delta: "hello" } }),
    );
    expect(next).toBe(start);
  });
});

describe("buildCitationRegistry", () => {
  it("rebuilds from a sequence of replayed events", () => {
    const events: RuntimeEventEnvelope[] = [
      event({
        event_type: "source_ingested",
        sequence_no: 1,
        payload: { citation: citation() },
      }),
      event({
        event_type: "source_ingested",
        sequence_no: 2,
        payload: {
          citation: citation({
            citation_id: "c2",
            ordinal: 2,
            source_doc_id: "drive_456",
            title: "FY26 Q1 GTM plan",
          }),
        },
      }),
    ];
    const registry = buildCitationRegistry(events);
    expect(citationsByOrdinal(citationsForRun(registry, RUN_ID))).toEqual([
      citation(),
      citation({
        citation_id: "c2",
        ordinal: 2,
        source_doc_id: "drive_456",
        title: "FY26 Q1 GTM plan",
      }),
    ]);
  });
});
