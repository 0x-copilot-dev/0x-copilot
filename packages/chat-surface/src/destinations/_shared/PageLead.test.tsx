// PageLead — the design `.pg-lead` intro paragraph (PRD-G FR-G.1).

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PageLead } from "./PageLead";

describe("<PageLead>", () => {
  it("renders a paragraph with the `.pg-lead` class + muted 12px style", () => {
    render(<PageLead>Everything the agent has done.</PageLead>);
    const el = screen.getByTestId("page-lead");
    expect(el.tagName).toBe("P");
    expect(el).toHaveClass("pg-lead");
    expect(el).toHaveTextContent("Everything the agent has done.");
    expect(el.style.fontSize).toBe("var(--font-size-xs)");
    expect(el.style.color).toBe("var(--color-text-muted)");
    expect(el.style.maxWidth).toBe("72ch");
  });

  it("renders inline nodes (e.g. a retention link) as children", () => {
    render(
      <PageLead>
        Everything the agent has done. <a href="#/x">Retention</a>
      </PageLead>,
    );
    expect(screen.getByRole("link", { name: "Retention" })).toBeInTheDocument();
  });

  it("passes through extra props + merges a className", () => {
    render(
      <PageLead className="extra" data-x="1">
        Lead
      </PageLead>,
    );
    const el = screen.getByTestId("page-lead");
    expect(el).toHaveClass("pg-lead");
    expect(el).toHaveClass("extra");
    expect(el).toHaveAttribute("data-x", "1");
  });
});
