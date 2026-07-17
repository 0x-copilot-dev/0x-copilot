import { describe, expect, it } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import type { SourceEntry } from "@0x-copilot/api-types";
import { SourceFavicon } from "@0x-copilot/chat-surface";

function source(overrides: Partial<SourceEntry>): SourceEntry {
  return {
    citation_id: "c1",
    source_connector: "web_search",
    source_doc_id: "doc-1",
    source_url: null,
    title: "Stub",
    snippet: null,
    freshness_at: null,
    citation_count: 1,
    last_cited_at: new Date().toISOString(),
    ...overrides,
  };
}

describe("SourceFavicon", () => {
  it("renders favicon img for a source with a real URL", () => {
    const { container } = render(
      <SourceFavicon
        source={source({ source_url: "https://pypi.org/project/deepagents" })}
      />,
    );
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img?.getAttribute("src")).toContain("favicons");
    expect(img?.getAttribute("src")).toContain("pypi.org");
  });

  it("falls back to AppIcon when source has no URL", () => {
    const { container } = render(
      <SourceFavicon
        source={source({ source_url: null, source_connector: "notion" })}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    // AppIcon renders the brand letter / symbol inside the span.
    expect(container.querySelector(".ui-app-icon")).not.toBeNull();
  });

  it("falls back to AppIcon when source URL is malformed", () => {
    const { container } = render(
      <SourceFavicon source={source({ source_url: "not a url" })} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".ui-app-icon")).not.toBeNull();
  });

  it("falls back to AppIcon when the favicon image errors", () => {
    const { container } = render(
      <SourceFavicon source={source({ source_url: "https://example.com" })} />,
    );
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    fireEvent.error(img!);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".ui-app-icon")).not.toBeNull();
  });
});
