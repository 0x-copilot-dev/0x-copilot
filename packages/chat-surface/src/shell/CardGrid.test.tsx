import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CardGrid } from "./CardGrid";

describe("<CardGrid>", () => {
  it("renders its children inside a grid container", () => {
    render(
      <CardGrid>
        <div data-testid="child-a">A</div>
        <div data-testid="child-b">B</div>
      </CardGrid>,
    );
    expect(screen.getByTestId("card-grid")).toBeInTheDocument();
    expect(screen.getByTestId("child-a")).toBeInTheDocument();
    expect(screen.getByTestId("child-b")).toBeInTheDocument();
  });

  it("applies the configured min card width via grid-template-columns", () => {
    render(
      <CardGrid minCardWidth={300}>
        <div>x</div>
      </CardGrid>,
    );
    const grid = screen.getByTestId("card-grid");
    expect(grid).toHaveAttribute("data-min-card-width", "300");
    const style = grid.getAttribute("style") ?? "";
    expect(style).toContain("300px");
  });

  it("renders as a region landmark when ariaLabel is supplied", () => {
    render(
      <CardGrid ariaLabel="Pinned chats">
        <div>x</div>
      </CardGrid>,
    );
    expect(
      screen.getByRole("region", { name: "Pinned chats" }),
    ).toBeInTheDocument();
  });

  it("defaults to the auto-fill responsive grid (DoD 8)", () => {
    render(
      <CardGrid>
        <div>x</div>
      </CardGrid>,
    );
    const grid = screen.getByTestId("card-grid");
    expect(grid).toHaveAttribute("data-variant", "auto-fill");
    expect(grid.style.gridTemplateColumns).toBe(
      "repeat(auto-fill, minmax(260px, 1fr))",
    );
  });

  it("emits the .ui-grid3 recipe class and no inline gridTemplateColumns when variant='grid3' (DoD 8)", () => {
    render(
      <CardGrid variant="grid3">
        <div>x</div>
      </CardGrid>,
    );
    const grid = screen.getByTestId("card-grid");
    expect(grid).toHaveClass("ui-grid3");
    expect(grid).toHaveAttribute("data-variant", "grid3");
    // The 3→1 collapse lives in the kit media query, so nothing is inlined.
    expect(grid.style.gridTemplateColumns).toBe("");
  });
});
