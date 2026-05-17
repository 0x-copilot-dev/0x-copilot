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
});
