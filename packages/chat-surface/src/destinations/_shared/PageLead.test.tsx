// PageLead — the design lead intro paragraph (PRD-G FR-G.1).
//
// The mock's `.pg-lead` class carried NO CSS in the shipped app (PRD-13 deleted
// the vestigial hook); the geometry below — muted 12px, loose line-height, 72ch
// cap — is the real, token-driven contract, so these tests assert the computed
// inline style, not a decorative attribute.

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PageLead } from "./PageLead";

describe("<PageLead>", () => {
  it("renders a muted 12px paragraph with the token-driven style contract", () => {
    render(<PageLead>Everything the agent has done.</PageLead>);
    const el = screen.getByTestId("page-lead");
    expect(el.tagName).toBe("P");
    expect(el).toHaveTextContent("Everything the agent has done.");
    expect(el.style.fontSize).toBe("var(--font-size-xs)");
    expect(el.style.color).toBe("var(--color-text-muted)");
    expect(el.style.maxWidth).toBe("72ch");
  });

  it("pins the design's lead geometry", () => {
    // The design's `.pg-lead` is `max-width:72ch; line-height:1.6`
    // (design-kit/app-v3/copilot.css:1556-1562). We ship the loose rung
    // (`--line-height-loose`, a PRD-01 delta from 1.6→1.7, recorded here so the
    // class removal is provably style-neutral). Assert the inline contract…
    render(<PageLead>Lead</PageLead>);
    const el = screen.getByTestId("page-lead");
    expect(el.style.maxWidth).toBe("72ch");
    expect(el.style.lineHeight).toBe("var(--line-height-loose)");

    // …then resolve the token against the design-system SoT the browser reads,
    // so a redefinition off-value fails here. Resolve this test file's dir
    // across runtimes (vitest does not always expose a file:// import.meta.url).
    const here =
      typeof import.meta.dirname === "string"
        ? import.meta.dirname
        : dirname(fileURLToPath(import.meta.url));
    const stylesCss = readFileSync(
      resolve(here, "../../../../design-system/src/styles.css"),
      "utf-8",
    );
    const match = stylesCss.match(/--line-height-loose:\s*([\d.]+)\s*;/);
    expect(match?.[1]).toBe("1.7");
  });

  it("renders inline nodes (e.g. a retention link) as children", () => {
    render(
      <PageLead>
        Everything the agent has done. <a href="#/x">Retention</a>
      </PageLead>,
    );
    expect(screen.getByRole("link", { name: "Retention" })).toBeInTheDocument();
  });

  it("forwards className from the caller", () => {
    render(
      <PageLead className="x" data-x="1">
        Lead
      </PageLead>,
    );
    const el = screen.getByTestId("page-lead");
    expect(el).toHaveClass("x");
    expect(el).toHaveAttribute("data-x", "1");
  });
});
