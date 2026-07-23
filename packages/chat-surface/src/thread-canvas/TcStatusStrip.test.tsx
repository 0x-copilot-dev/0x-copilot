import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { TcStatusStrip } from "./TcStatusStrip";

describe("TcStatusStrip", () => {
  it("renders the op line as a status region", () => {
    render(
      <TcStatusStrip
        line={{
          kind: "op",
          text: "read.executed · linear.get_issue · r7f3·042",
          ledgerId: "r7f3·042",
        }}
      />,
    );
    const strip = screen.getByTestId("tc-status-strip");
    expect(strip).toHaveAttribute("role", "status");
    expect(strip).toHaveTextContent(
      "read.executed · linear.get_issue · r7f3·042",
    );
  });

  it("renders idle copy when there is no activity", () => {
    render(<TcStatusStrip line={{ kind: "idle", text: "", ledgerId: null }} />);
    expect(screen.getByTestId("tc-status-strip")).toHaveTextContent(
      "No activity yet",
    );
  });

  it("renders the assembling line", () => {
    render(
      <TcStatusStrip
        line={{
          kind: "assembling",
          text: "surface.created · linear.get_issue · r7f3·001",
          ledgerId: "r7f3·001",
        }}
      />,
    );
    expect(screen.getByTestId("tc-status-strip")).toHaveTextContent(
      "surface.created · linear.get_issue",
    );
  });
});
