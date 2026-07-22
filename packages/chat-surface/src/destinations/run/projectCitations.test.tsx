// @vitest-environment jsdom
// WC-P6a — projectCitations unit tests (AD-11 / FR-3.3).
//
// The projector is a PURE selector over the canonical run event stream; these
// pin the two-system reduction (`source_ingested`/`sources_ingested`/
// `final_response` → source registry; `citation_made` → `[[N]]` link registry),
// the active/terminal run derivation, and — through the shared `CitationsProvider`
// — that a projected ordinal actually resolves in a rendered chip wrapper.

import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import type {
  CitationLink,
  CitationSourceRef,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";

import {
  CitationsProvider,
  useResolvedOrdinalCitation,
  useCitation,
} from "../../citations/CitationsContext";
import { projectCitations } from "./projectCitations";

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

function citation(
  overrides: Partial<CitationSourceRef> = {},
): CitationSourceRef {
  return {
    citation_id: "c1",
    source_connector: "gdrive",
    source_doc_id: "doc-1",
    source_url: "https://example.com/doc-1",
    title: "Doc One",
    snippet: "a snippet",
    freshness_at: null,
    source_tool_call_id: "call_a",
    ordinal: 1,
    ...overrides,
  } as CitationSourceRef;
}

function sourceIngested(cit: CitationSourceRef): RuntimeEventEnvelope {
  return envelope({
    event_type: "source_ingested",
    payload: { citation: cit },
  });
}

function link(overrides: Partial<CitationLink> = {}): CitationLink {
  return {
    conversation_ordinal: 3,
    message_id: "msg-1",
    prose_offset: 0,
    prose_length: 5,
    source_tool_call_id: "call_a",
    ...overrides,
  };
}

function citationMade(l: CitationLink): RuntimeEventEnvelope {
  return envelope({ event_type: "citation_made", payload: { link: l } });
}

function finalResponse(
  citations: readonly CitationSourceRef[],
): RuntimeEventEnvelope {
  return envelope({
    event_type: "final_response",
    payload: { text: "done", citations },
  });
}

describe("projectCitations", () => {
  it("returns an empty projection for no events", () => {
    const p = projectCitations([]);
    expect(p.citations.size).toBe(0);
    expect(p.byRun.size).toBe(0);
    expect(p.linksByRun.size).toBe(0);
    expect(p.terminalRuns.size).toBe(0);
    expect(p.activeRunId).toBeNull();
  });

  it("folds source_ingested into the per-run + active-run source registries", () => {
    const p = projectCitations([sourceIngested(citation())]);
    expect(p.byRun.get("run-1")?.get("c1")?.title).toBe("Doc One");
    // Active-run flat map resolves the citation_id for `[c<id>]` chips.
    expect(p.citations.get("c1")?.title).toBe("Doc One");
    // A live (non-terminal) run is the active run.
    expect(p.activeRunId).toBe("run-1");
    expect(p.terminalRuns.has("run-1")).toBe(false);
  });

  it("batches sources_ingested and stays idempotent on replay", () => {
    const c = citation();
    const events = [
      envelope({
        event_type: "sources_ingested",
        payload: {
          citations: [c, citation({ citation_id: "c2", ordinal: 2 })],
        },
      }),
      sourceIngested(c), // re-delivery of the same citation_id
    ];
    const p = projectCitations(events);
    expect(p.byRun.get("run-1")?.size).toBe(2);
    expect(p.citations.size).toBe(2);
  });

  it("builds the [[N]] link registry from citation_made", () => {
    const p = projectCitations([
      citationMade(link({ conversation_ordinal: 7 })),
    ]);
    // The link registry is keyed by run; the ordinal is present for run-1.
    expect(p.linksByRun.get("run-1")).toBeDefined();
  });

  it("marks a run terminal on final_response and clears the active run", () => {
    const p = projectCitations([
      sourceIngested(citation()),
      finalResponse([citation()]),
    ]);
    expect(p.terminalRuns.has("run-1")).toBe(true);
    // Sealed → activeRunId null (so the ordinal hook scans every run) …
    expect(p.activeRunId).toBeNull();
    // … but the flat map still resolves the sealed run's citations.
    expect(p.citations.get("c1")?.title).toBe("Doc One");
  });
});

// Chip-resolution wiring: the projection fed to the shared CitationsProvider must
// make an ordinal chip (via `useResolvedOrdinalCitation`) and a `[c<id>]` chip
// (via `useCitation`) resolve — the exact hooks the host chip wrappers call.

function OrdinalProbe({ ordinal }: { ordinal: number }): ReactElement {
  const resolved = useResolvedOrdinalCitation(ordinal);
  return (
    <span data-testid="ordinal-probe">
      {resolved === null ? "unresolved" : resolved.callId}
    </span>
  );
}

function CitationProbe({ id }: { id: string }): ReactElement {
  const cit = useCitation(id);
  return <span data-testid="citation-probe">{cit?.title ?? "unresolved"}</span>;
}

describe("projectCitations → CitationsProvider resolution", () => {
  it("resolves an [[N]] ordinal chip and a [c<id>] chip from the projection", () => {
    const projection = projectCitations([
      sourceIngested(citation()),
      citationMade(
        link({ conversation_ordinal: 3, source_tool_call_id: "call_z" }),
      ),
      finalResponse([citation()]),
    ]);
    render(
      <CitationsProvider
        citations={projection.citations}
        byRun={projection.byRun}
        terminalRuns={projection.terminalRuns}
        linksByRun={projection.linksByRun}
        activeRunId={projection.activeRunId}
      >
        <OrdinalProbe ordinal={3} />
        <CitationProbe id="c1" />
      </CitationsProvider>,
    );
    // Ordinal 3 resolves to its bound tool_call_id (scan-all fallback, since the
    // run sealed → activeRunId null).
    expect(screen.getByTestId("ordinal-probe").textContent).toBe("call_z");
    // Legacy `[c<id>]` chip resolves its title.
    expect(screen.getByTestId("citation-probe").textContent).toBe("Doc One");
  });
});
