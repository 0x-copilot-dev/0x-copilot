// CompactSourceList — the dense source card shared by the Sources rail.
//
// The URL-safety case here moved from `InlineToolResultCard.test.tsx` along
// with the card itself: `safeHttpUrl` is now the one place that decides whether
// a source title becomes an anchor, so this is where that property belongs.

import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CompactSourceList,
  displayUrl,
  safeHttpUrl,
  type CompactSourceItem,
} from "./CompactSourceList";

afterEach(() => {
  cleanup();
});

function item(overrides: Partial<CompactSourceItem> = {}): CompactSourceItem {
  return {
    id: "c1",
    ordinal: 1,
    title: "LangGraph overview",
    subtitle: "docs.langchain.com/langgraph",
    href: "https://docs.langchain.com/langgraph",
    ...overrides,
  };
}

describe("CompactSourceList", () => {
  it("renders one row per source with its ordinal and subtitle", () => {
    render(
      <CompactSourceList
        label="Web search"
        items={[
          item(),
          item({
            id: "c2",
            ordinal: 2,
            title: "IBM",
            subtitle: "ibm.com/think/langgraph",
          }),
        ]}
      />,
    );
    const card = screen.getByTestId("compact-source-list");
    expect(card).toHaveAccessibleName(/2 Web search sources/i);
    expect(within(card).getByText("WEB SEARCH · 2")).toBeInTheDocument();
    expect(within(card).getAllByRole("listitem")).toHaveLength(2);
    expect(within(card).getByText("[1]")).toBeInTheDocument();
    expect(
      within(card).getByText("docs.langchain.com/langgraph"),
    ).toBeInTheDocument();
  });

  it("renders nothing for an empty list rather than an empty bordered card", () => {
    const { container } = render(
      <CompactSourceList label="Web search" items={[]} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("links a title only when the caller supplies a safe URL", () => {
    render(<CompactSourceList label="Web" items={[item()]} />);
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute(
      "href",
      "https://docs.langchain.com/langgraph",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });

  it("never turns an unsafe URL into a link", () => {
    // The safety property that moved here with the card.
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("data:text/html,<script>")).toBeNull();
    expect(safeHttpUrl(null)).toBeNull();
    expect(safeHttpUrl("not a url")).toBeNull();
    render(
      <CompactSourceList
        label="Web"
        items={[item({ href: safeHttpUrl("javascript:alert(1)") })]}
      />,
    );
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("uses a button (not a link) for owner-routed opening", () => {
    const onActivate = vi.fn();
    render(
      <CompactSourceList
        label="Artifacts"
        items={[item({ href: null, ordinal: null, onActivate })]}
      />,
    );
    expect(screen.queryByRole("link")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Open LangGraph/ }));
    expect(onActivate).toHaveBeenCalledOnce();
    expect(
      screen.getByTestId("compact-source-row").getAttribute("data-openable"),
    ).toBe("true");
  });

  it("omits the ordinal when a row is unnumbered", () => {
    render(
      <CompactSourceList
        label="Runtime"
        items={[item({ ordinal: null, href: null })]}
      />,
    );
    expect(screen.queryByText(/^\[\d+\]$/)).toBeNull();
  });

  it("caps a long display URL so one row cannot blow out the panel", () => {
    const long = `https://example.com/${"a".repeat(400)}`;
    expect(displayUrl(long).length).toBeLessThanOrEqual(120);
  });
});
