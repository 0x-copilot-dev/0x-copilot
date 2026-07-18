import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LogoMark } from "./LogoMark";

describe("LogoMark", () => {
  it("renders the Copilot wordmark by default", () => {
    render(<LogoMark />);
    expect(screen.getByText("Copilot")).toBeInTheDocument();
    expect(screen.getByLabelText("Copilot")).toBeInTheDocument();
  });
  it("hides the wordmark when compact", () => {
    render(<LogoMark compact />);
    expect(screen.queryByText("Copilot")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Copilot")).toBeInTheDocument();
  });
});
