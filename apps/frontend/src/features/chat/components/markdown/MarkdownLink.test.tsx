import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownLink, isExternalHref } from "./MarkdownLink";

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

describe("MarkdownLink", () => {
  it("opens external links in a new tab with noreferrer", () => {
    render(<MarkdownLink href="https://example.com">Example</MarkdownLink>);
    const link = screen.getByRole("link");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noreferrer");
  });
  it("does not force target/rel for relative links", () => {
    render(<MarkdownLink href="/local">Local</MarkdownLink>);
    const link = screen.getByRole("link");
    expect(link.getAttribute("target")).toBeNull();
    expect(link.getAttribute("rel")).toBeNull();
  });
});
