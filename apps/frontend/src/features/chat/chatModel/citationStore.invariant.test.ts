// PR 3.5 / G10 — invariant test for the dual citation store.
//
// PR 3.1's spec proposed extending `CitationLookup` with `byRun`/
// `byConversation` layers. The implementation that landed kept two
// reducers — `citationsRegistry` for chip resolution + `sourcesReducer`
// for the SourcesTab — both fed by the same ingestion events.
// Functionally equivalent, but the two stores must agree on the
// per-source fields they share. This test asserts that invariant
// across BOTH event shapes:
//   * `source_ingested` (singular, per-source emitters)
//   * `sources_ingested` (P7 batched variant, emitted by
//     `CitationLedger.register_many`)
//
// If a future PR forks these reducers, this test fails immediately and
// the offending diff is identified by CI before drift can ship.

import { describe, expect, it } from "vitest";
import type {
  CitationSourceRef,
  RuntimeEventEnvelope,
} from "@enterprise-search/api-types";
import { applyCitationEvent } from "./citationReducer";
import { citationsForRun, emptyCitationRegistry } from "./citationsRegistry";
import { applySourceEvent, emptySourceMap } from "./sourcesReducer";

const RUN_ID = "run_invariant_1";
const CONVERSATION_ID = "conv_invariant_1";

function citation(
  overrides: Partial<CitationSourceRef> = {},
): CitationSourceRef {
  return {
    citation_id: "c1",
    ordinal: 1,
    source_connector: "notion",
    source_doc_id: "page_1",
    source_url: "https://example.com/page_1",
    title: "Aurora 4.0 — Approved Positioning v3",
    snippet: "Aurora 4.0 brings agentic search to every desk.",
    freshness_at: "2026-04-29T16:40:00Z",
    source_tool_call_id: "tc_1",
    ...overrides,
  };
}

function event(
  citation: CitationSourceRef,
  sequence_no = 1,
): RuntimeEventEnvelope {
  return {
    event_id: `evt_${sequence_no}`,
    run_id: RUN_ID,
    conversation_id: CONVERSATION_ID,
    sequence_no,
    activity_kind: "tool",
    created_at: "2026-05-04T12:00:00Z",
    event_type: "source_ingested",
    payload: { citation },
  } as RuntimeEventEnvelope;
}

const SHARED_FIELDS = [
  "citation_id",
  "source_connector",
  "source_doc_id",
  "source_url",
  "title",
  "snippet",
  "freshness_at",
] as const;

describe("citation store invariant (PR 3.5 / G10)", () => {
  it.each([
    ["single source", [citation()]],
    [
      "multiple sources, distinct docs",
      [
        citation(),
        citation({
          citation_id: "c2",
          ordinal: 2,
          source_connector: "drive",
          source_doc_id: "drive_a",
          title: "FY26 Q1 GTM plan",
          snippet: "Three-phase rollout.",
        }),
        citation({
          citation_id: "c3",
          ordinal: 3,
          source_connector: "slack",
          source_doc_id: "slack_msg_1",
          title: "#launch-aurora",
          snippet: "embargo lifts on the 21st",
        }),
      ],
    ],
    [
      "duplicate event for same citation_id (replay-safe)",
      [citation(), citation()],
    ],
  ])(
    "%s — both reducers project byte-identical shared fields",
    (_label, citations) => {
      let chipRegistry = emptyCitationRegistry();
      let sourceMap = emptySourceMap();
      citations.forEach((cite, index) => {
        const evt = event(cite, index + 1);
        chipRegistry = applyCitationEvent(chipRegistry, evt);
        sourceMap = applySourceEvent(sourceMap, evt);
      });

      // For every citation that survives in the chip registry, the source
      // map must hold a row whose shared fields match exactly. The source
      // map dedupes on (connector, doc_id) — we look up by walking the
      // values rather than reconstructing the private key (the reducer
      // owns its key shape, the test shouldn't).
      const chipMap = citationsForRun(chipRegistry, RUN_ID);
      const sourceByDoc = new Map(
        [...sourceMap.values()].map((entry) => [
          `${entry.source_connector}/${entry.source_doc_id}`,
          entry,
        ]),
      );
      for (const chip of chipMap.values()) {
        const sourceEntry = sourceByDoc.get(
          `${chip.source_connector}/${chip.source_doc_id}`,
        );
        expect(sourceEntry).toBeDefined();
        for (const field of SHARED_FIELDS) {
          expect(sourceEntry?.[field]).toEqual(chip[field]);
        }
      }
    },
  );

  // P7 — same invariant must hold for the batched event shape.
  it.each([
    ["batched single source", [citation()]],
    [
      "batched multi-source",
      [
        citation(),
        citation({
          citation_id: "c2",
          ordinal: 2,
          source_connector: "drive",
          source_doc_id: "drive_a",
          title: "FY26 Q1 GTM plan",
          snippet: "Three-phase rollout.",
        }),
      ],
    ],
  ])(
    "%s via sources_ingested — both reducers project byte-identical shared fields",
    (_label, citations) => {
      const evt = {
        event_id: "evt_batch_1",
        run_id: RUN_ID,
        conversation_id: CONVERSATION_ID,
        sequence_no: 42,
        activity_kind: "tool",
        created_at: "2026-05-04T12:00:00Z",
        event_type: "sources_ingested",
        payload: { citations },
      } as RuntimeEventEnvelope;
      const chipRegistry = applyCitationEvent(emptyCitationRegistry(), evt);
      const sourceMap = applySourceEvent(emptySourceMap(), evt);

      const chipMap = citationsForRun(chipRegistry, RUN_ID);
      const sourceByDoc = new Map(
        [...sourceMap.values()].map((entry) => [
          `${entry.source_connector}/${entry.source_doc_id}`,
          entry,
        ]),
      );
      for (const chip of chipMap.values()) {
        const sourceEntry = sourceByDoc.get(
          `${chip.source_connector}/${chip.source_doc_id}`,
        );
        expect(sourceEntry).toBeDefined();
        for (const field of SHARED_FIELDS) {
          expect(sourceEntry?.[field]).toEqual(chip[field]);
        }
      }
    },
  );

  it("non-source events don't touch either store", () => {
    const start = { chip: emptyCitationRegistry(), source: emptySourceMap() };
    const stray = {
      ...event(citation(), 1),
      event_type: "model_delta",
      payload: { delta: "hello" },
    } as RuntimeEventEnvelope;
    expect(applyCitationEvent(start.chip, stray)).toBe(start.chip);
    expect(applySourceEvent(start.source, stray)).toBe(start.source);
  });
});
