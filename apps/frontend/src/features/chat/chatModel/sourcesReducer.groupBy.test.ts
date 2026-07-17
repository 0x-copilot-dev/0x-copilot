import { describe, expect, it } from "vitest";
import type { SourceEntry } from "@0x-copilot/api-types";
import { groupSourcesByConnector } from "./sourcesReducer";

function source(overrides: Partial<SourceEntry>): SourceEntry {
  return {
    citation_id: "c1",
    source_connector: "web_search",
    source_doc_id: "doc-1",
    source_url: "https://example.com",
    title: "T",
    snippet: null,
    freshness_at: null,
    citation_count: 1,
    last_cited_at: "2026-05-06T12:00:00Z",
    ...overrides,
  };
}

describe("groupSourcesByConnector", () => {
  it("groups by connector and sorts sections by total citation count desc", () => {
    const ordered = [
      source({
        citation_id: "c1",
        source_connector: "web_search",
        citation_count: 1,
      }),
      source({
        citation_id: "c2",
        source_connector: "web_search",
        citation_count: 1,
      }),
      source({
        citation_id: "c3",
        source_connector: "web_search",
        citation_count: 1,
      }),
      source({
        citation_id: "c4",
        source_connector: "notion",
        citation_count: 2,
      }),
      source({
        citation_id: "c5",
        source_connector: "drive",
        citation_count: 1,
      }),
    ];
    const groups = groupSourcesByConnector(ordered);
    expect(groups.map((g) => g.connector)).toEqual([
      "web_search",
      "notion",
      "drive",
    ]);
    expect(groups[0].total).toBe(3);
    expect(groups[1].total).toBe(2);
    expect(groups[2].total).toBe(1);
  });

  it("ties on total break alphabetically", () => {
    const ordered = [
      source({
        citation_id: "a",
        source_connector: "drive",
        citation_count: 1,
      }),
      source({
        citation_id: "b",
        source_connector: "notion",
        citation_count: 1,
      }),
    ];
    const groups = groupSourcesByConnector(ordered);
    expect(groups.map((g) => g.connector)).toEqual(["drive", "notion"]);
  });

  it("preserves the input row order within each section", () => {
    const ordered = [
      source({
        citation_id: "first",
        source_connector: "web_search",
        citation_count: 5,
      }),
      source({
        citation_id: "second",
        source_connector: "web_search",
        citation_count: 3,
      }),
    ];
    const [group] = groupSourcesByConnector(ordered);
    expect(group.rows.map((r) => r.citation_id)).toEqual(["first", "second"]);
  });
});
