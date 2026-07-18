// Anchor dispatcher contract (FR-1.3). The dispatcher routes the citation
// remark plugin's rewritten hrefs to the host-injected chip renderers and
// everything else to a plain <a>. These assertions moved down with the
// component from apps/frontend and gained the chip-dispatch cases the web
// wrapper's context made awkward to exercise there.

import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { createMarkdownLink, isExternalHref } from "./MarkdownLink";

function StubCitationChip({
  citationId,
}: {
  citationId: string;
}): ReactElement {
  return <span data-testid="citation-chip" data-citation-id={citationId} />;
}

function StubOrdinalCitationChip({
  conversationOrdinal,
}: {
  conversationOrdinal: number;
}): ReactElement {
  return (
    <span
      data-testid="ordinal-chip"
      data-ordinal={String(conversationOrdinal)}
    />
  );
}

const MarkdownLink = createMarkdownLink({
  CitationChip: StubCitationChip,
  OrdinalCitationChip: StubOrdinalCitationChip,
});

describe("isExternalHref", () => {
  it("returns true for http(s) urls", () => {
    expect(isExternalHref("https://example.com")).toBe(true);
    expect(isExternalHref("http://example.com")).toBe(true);
  });
  it("returns false for relative or unknown urls", () => {
    expect(isExternalHref(undefined)).toBe(false);
    expect(isExternalHref("/local")).toBe(false);
    expect(isExternalHref("mailto:hi@x.com")).toBe(false);
  });
});

describe("createMarkdownLink dispatcher", () => {
  it("routes #cite-ord:<n> hrefs to the injected OrdinalCitationChip", () => {
    render(<MarkdownLink href="#cite-ord:3">[[3]]</MarkdownLink>);
    const chip = screen.getByTestId("ordinal-chip");
    expect(chip.getAttribute("data-ordinal")).toBe("3");
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("routes #cite:<id> hrefs to the injected CitationChip", () => {
    render(<MarkdownLink href="#cite:cX">[cX]</MarkdownLink>);
    const chip = screen.getByTestId("citation-chip");
    expect(chip.getAttribute("data-citation-id")).toBe("cX");
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("opens external links in a new tab with noreferrer", () => {
    render(<MarkdownLink href="https://example.com">Example</MarkdownLink>);
    const link = screen.getByRole("link");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noreferrer");
  });

  it("compacts a raw-URL label via markdownLinkLabel", () => {
    render(
      <MarkdownLink href="https://example.com/foo">
        https://example.com/foo
      </MarkdownLink>,
    );
    expect(screen.getByRole("link").textContent).toBe("example.com/foo");
  });

  it("does not force target/rel for relative links", () => {
    render(<MarkdownLink href="/local">Local</MarkdownLink>);
    const link = screen.getByRole("link");
    expect(link.getAttribute("target")).toBeNull();
    expect(link.getAttribute("rel")).toBeNull();
  });
});
