import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LogoMark } from "./LogoMark";

describe("LogoMark", () => {
  it("renders the wordmark by default", () => {
    render(<LogoMark />);
    expect(screen.getByText("assistant-ui")).toBeInTheDocument();
  });
  it("hides the wordmark when compact", () => {
    render(<LogoMark compact />);
    expect(screen.queryByText("assistant-ui")).not.toBeInTheDocument();
  });
});
