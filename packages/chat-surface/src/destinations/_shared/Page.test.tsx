// Page tests (PRD-10 D4 / DoD 5).
//
// Pins the design `.pg` geometry (960px column, 20px 24px 40px padding) AND the
// G6 decision that `Page` is LEFT-aligned: no `margin: 0 auto`. A future edit
// that re-centres the column (copying the old live app) fails these assertions.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Page } from "./Page";

describe("<Page>", () => {
  it("renders the design column geometry (DoD 5)", () => {
    render(
      <Page>
        <div>content</div>
      </Page>,
    );
    const page = screen.getByTestId("page");
    expect(page.style.maxWidth).toBe("960px");
    expect(page.style.padding).toBe("20px 24px 40px");
  });

  it("is LEFT-aligned — no margin: 0 auto (G6)", () => {
    render(
      <Page>
        <div>content</div>
      </Page>,
    );
    const page = screen.getByTestId("page");
    // The whole point of G6: a future edit cannot quietly re-centre the column.
    expect(page.style.margin).toBe("");
    expect(page.style.marginLeft).toBe("");
    expect(page.style.marginRight).toBe("");
  });

  it("carries the data-page attribute so a hand-rolled shell fails a swap test", () => {
    render(
      <Page>
        <div>content</div>
      </Page>,
    );
    expect(screen.getByTestId("page")).toHaveAttribute("data-page");
  });

  it("merges caller style over the shell geometry and renders children", () => {
    render(
      <Page style={{ display: "flex", gap: 16 }}>
        <div data-testid="child">content</div>
      </Page>,
    );
    const page = screen.getByTestId("page");
    expect(page.style.display).toBe("flex");
    // Caller layout does not clobber the shell geometry.
    expect(page.style.maxWidth).toBe("960px");
    expect(screen.getByTestId("child")).toBeInTheDocument();
  });
});
