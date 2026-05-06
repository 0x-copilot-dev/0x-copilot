import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LogoMark } from "./LogoMark";

describe("LogoMark", () => {
  it("renders the Atlas wordmark by default", () => {
    render(<LogoMark />);
    expect(screen.getByText("Atlas")).toBeInTheDocument();
    expect(screen.getByLabelText("Atlas")).toBeInTheDocument();
  });
  it("hides the wordmark when compact", () => {
    render(<LogoMark compact />);
    expect(screen.queryByText("Atlas")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Atlas")).toBeInTheDocument();
  });
});
