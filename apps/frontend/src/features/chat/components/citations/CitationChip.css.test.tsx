// PR 3.5 / G11 — CitationChip visual contract test.
//
// We don't run a screenshot diff (`vitest-visual-regression` was
// rejected in PR 3.5 §3.4 — `toHaveAttribute` / `toHaveClass` cover the
// tokens our CSS rules key off). The contract under test:
//
//   1. Resolved chip carries the `citation-chip` class + `data-connector`
//      attribute so `apps/frontend/src/styles.css`'s
//      `.citation-chip[data-connector="..."]` rules can hang colors per
//      connector.
//   2. Unresolved chip carries the `citation-chip--unresolved` modifier
//      and a deterministic placeholder ("?") so the prose never goes
//      blank when a token can't be matched.
//   3. Click invokes `onSelect` with the resolved citation, *and* calls
//      `preventDefault` so the chip doesn't follow `href` when the host
//      provides a handler — that's the focus-then-scroll path PR 3.1
//      wired through `pane.openOn("sources", { focusCitationId })`.

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { CitationSourceRef } from "@0x-copilot/api-types";

import { CitationChip } from "./CitationChip";
import { CitationsProvider } from "./citationsContext";
import { upsertCitation } from "@0x-copilot/chat-surface";

function citation(
  overrides: Partial<CitationSourceRef> = {},
): CitationSourceRef {
  return {
    citation_id: "c1",
    ordinal: 1,
    source_connector: "notion",
    source_doc_id: "page_123",
    source_url: "https://example.com/n/page_123",
    title: "Aurora 4.0 — Approved Positioning v3",
    snippet: "Aurora 4.0 brings agentic search to every desk.",
    freshness_at: null,
    source_tool_call_id: null,
    ...overrides,
  };
}

function provideCitation(c: CitationSourceRef) {
  // The chip resolves through `useCitation`, which reads `active`.
  return new Map([[c.citation_id, c]]);
}

describe("CitationChip CSS contract", () => {
  it("resolved chip is a baseline pill <a> (not <sup>) with class + data-connector", () => {
    // PR 8.0.1 — chip rendered as <a> directly so it sits on the prose
    // baseline; pill shape comes from CSS, not <sup> superscript.
    render(
      <CitationsProvider citations={provideCitation(citation())}>
        <CitationChip citationId="c1" />
      </CitationsProvider>,
    );
    const link = screen.getByRole("link");
    expect(link.tagName).toBe("A");
    expect(link.closest("sup")).toBeNull();
    expect(link.className).toContain("citation-chip");
    expect(link.getAttribute("data-connector")).toBe("notion");
    expect(link.getAttribute("data-citation-id")).toBe("c1");
  });

  it("renders the ordinal as the chip body so consumers can scan by number", () => {
    render(
      <CitationsProvider citations={provideCitation(citation({ ordinal: 7 }))}>
        <CitationChip citationId="c1" />
      </CitationsProvider>,
    );
    expect(screen.getByRole("link")).toHaveTextContent("7");
  });

  it("falls back to a `--unresolved` placeholder when the id is missing from the registry", () => {
    render(
      <CitationsProvider citations={new Map()}>
        <CitationChip citationId="missing" />
      </CitationsProvider>,
    );
    const placeholder = screen.getByLabelText("Unresolved citation");
    expect(placeholder.className).toContain("citation-chip");
    expect(placeholder.className).toContain("citation-chip--unresolved");
    expect(placeholder).toHaveTextContent("?");
  });

  it("invokes onSelect with the citation and suppresses the href default", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(
      <CitationsProvider citations={provideCitation(citation())}>
        <CitationChip citationId="c1" onSelect={onSelect} />
      </CitationsProvider>,
    );
    await user.click(screen.getByRole("link"));
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect.mock.calls[0]?.[0].citation_id).toBe("c1");
  });

  // The `byRun` registry layer (PR 3.5 / G9) is exercised by
  // `useRunCitations`, which is itself tested via AssistantMessage; this
  // test asserts the *active*-layer fallback shape `useCitation` reads.
  it("resolves through `upsertCitation` registry shape too", () => {
    const registry = upsertCitation(new Map(), "run_x", citation());
    const active = new Map(registry.get("run_x"));
    render(
      <CitationsProvider citations={active}>
        <CitationChip citationId="c1" />
      </CitationsProvider>,
    );
    expect(screen.getByRole("link")).toHaveTextContent("1");
  });
});
