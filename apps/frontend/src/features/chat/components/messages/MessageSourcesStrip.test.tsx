import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { CitationSourceRef } from "@0x-copilot/api-types";
import { MessageSourcesStrip } from "./MessageSourcesStrip";

function citation(
  overrides: Partial<CitationSourceRef> = {},
): CitationSourceRef {
  return {
    citation_id: "c1",
    ordinal: 1,
    source_connector: "notion",
    source_doc_id: "page_123",
    source_url: "https://example.com/x",
    title: "Aurora 4.0 — Approved Positioning v3",
    snippet: "…",
    freshness_at: null,
    source_tool_call_id: null,
    ...overrides,
  };
}

describe("MessageSourcesStrip", () => {
  it("renders nothing when there are no citations", () => {
    const { container } = render(<MessageSourcesStrip citations={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders one chip per citation in ordinal order", () => {
    render(
      <MessageSourcesStrip
        citations={[
          citation({ citation_id: "c2", ordinal: 2, title: "Second" }),
          citation({ citation_id: "c1", ordinal: 1, title: "First" }),
        ]}
      />,
    );
    const chips = screen.getAllByRole("listitem");
    expect(chips).toHaveLength(2);
    expect(chips[0]).toHaveTextContent("First");
    expect(chips[1]).toHaveTextContent("Second");
  });

  it("invokes onSelect with the clicked citation", async () => {
    const onSelect = vi.fn();
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    render(
      <MessageSourcesStrip citations={[citation()]} onSelect={onSelect} />,
    );
    await user.click(
      screen.getByRole("listitem", {
        name: /Open citation 1 — Aurora 4\.0 — Approved Positioning v3/,
      }),
    );
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect.mock.calls[0]?.[0]).toMatchObject({ citation_id: "c1" });
  });
});
