import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Crumb } from "./Crumb";

describe("Crumb", () => {
  it("renders workspace and folder with a separator", () => {
    render(<Crumb workspace="Acme" folder="Launches" />);
    expect(screen.getByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("Launches")).toBeInTheDocument();
    expect(screen.getByText("›")).toBeInTheDocument();
  });

  it("renders just the workspace when no folder", () => {
    render(<Crumb workspace="Acme" folder={null} />);
    expect(screen.getByText("Acme")).toBeInTheDocument();
    expect(screen.queryByText("›")).not.toBeInTheDocument();
  });

  it("renders nothing when both parts missing", () => {
    const { container } = render(<Crumb workspace={null} folder={null} />);
    expect(container.firstChild).toBeNull();
  });
});
