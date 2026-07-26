import { render, screen, within } from "@testing-library/react";
import type { CitationSourceRef } from "@0x-copilot/api-types";
import { describe, expect, it } from "vitest";

import type { ToolCallEntry } from "./eventProjector";
import { InlineToolResultCard } from "./InlineToolResultCard";

function toolCall(overrides: Partial<ToolCallEntry> = {}): ToolCallEntry {
  return {
    createdAtMs: 0,
    id: "web-search-1",
    result: { source_count: 2 },
    sequenceNo: 2,
    status: "complete",
    title: "Search the web",
    toolName: "web.search",
    ...overrides,
  };
}

function source(overrides: Partial<CitationSourceRef> = {}): CitationSourceRef {
  return {
    citation_id: "citation-1",
    freshness_at: null,
    ordinal: 1,
    snippet: null,
    source_connector: "web",
    source_doc_id: "doc-1",
    source_tool_call_id: "web-search-1",
    source_url: "https://status.example.com/incidents/0128",
    title: "Status page — Checkout latency incident",
    ...overrides,
  };
}

describe("InlineToolResultCard", () => {
  it("renders only backend-linked citations as an inline chat source result", () => {
    render(
      <InlineToolResultCard
        toolCall={toolCall()}
        citations={[
          source({ citation_id: "citation-2", ordinal: 2, title: "Second" }),
          source({
            citation_id: "other",
            ordinal: 3,
            source_tool_call_id: "other-call",
          }),
          source({ citation_id: "citation-1", ordinal: 1, title: "First" }),
        ]}
      />,
    );

    const card = screen.getByTestId("tc-inline-web-sources");
    expect(card).toHaveAccessibleName(/2 sources returned by search the web/i);
    expect(within(card).getByText("First")).toBeInTheDocument();
    expect(within(card).getByText("Second")).toBeInTheDocument();
    expect(within(card).queryByText("other")).not.toBeInTheDocument();
    expect(within(card).getAllByRole("listitem")[0]).toHaveTextContent("First");
    expect(
      within(card).getByRole("link", { name: /open source 1/i }),
    ).toHaveAttribute("href", "https://status.example.com/incidents/0128");
  });

  it("does not turn untrusted source URLs into links", () => {
    render(
      <InlineToolResultCard
        toolCall={toolCall()}
        citations={[source({ source_url: "javascript:alert(1)" })]}
      />,
    );

    const card = screen.getByTestId("tc-inline-web-sources");
    expect(within(card).queryByRole("link")).not.toBeInTheDocument();
    expect(card).toHaveTextContent("web");
  });

  it("renders CSV facts, bounded metrics, and an escaped static preview", () => {
    render(
      <InlineToolResultCard
        toolCall={toolCall({
          args: { path: "/private/forecast_q1.csv" },
          id: "csv-read-1",
          result: {
            bytes: 62_464,
            columns: 9,
            metrics: [
              { label: "Pipeline", value: "$4.1M" },
              { label: "At risk", value: 18 },
            ],
            preview_rows: [
              {
                region: "EMEA",
                stage: "<strong>Review</strong>",
                amount: 120000,
              },
              { region: "NA", stage: "Closed", amount: 220000 },
            ],
            rows: 742,
          },
          title: "Read forecast_q1.csv",
          toolName: "fs.read",
        })}
        citations={[]}
      />,
    );

    const card = screen.getByTestId("tc-inline-csv-summary");
    expect(card).toHaveAccessibleName("CSV summary for forecast_q1.csv");
    expect(card).toHaveTextContent("742 rows · 9 columns · 61 KB");
    expect(card).toHaveTextContent("PIPELINE");
    expect(card).toHaveTextContent("$4.1M");
    expect(card).toHaveTextContent("PREVIEW · 2 rows");
    expect(card.querySelector("strong")).toBeNull();
    expect(card).toHaveTextContent("<strong>Review</strong>");
  });

  it("does not guess a CSV summary from a file-like tool call without explicit facts", () => {
    const { container } = render(
      <InlineToolResultCard
        toolCall={toolCall({
          args: { path: "/private/forecast_q1.csv" },
          id: "csv-read-1",
          result: { rows: 742 },
          toolName: "fs.read",
        })}
        citations={[]}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});
