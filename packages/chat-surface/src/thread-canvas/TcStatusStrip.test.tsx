import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { TcStatusStrip } from "./TcStatusStrip";

describe("TcStatusStrip", () => {
  it("renders the shaping line as a status region while assembling", () => {
    render(<TcStatusStrip line={{ kind: "assembling" }} />);
    const strip = screen.getByTestId("tc-status-strip");
    expect(strip).toHaveAttribute("role", "status");
    expect(strip).toHaveTextContent("Shaping…");
  });

  it("renders NOTHING when idle — no wrapper, not merely no text", () => {
    // The root carries a `borderTop`, so an empty-but-mounted strip would still
    // paint a rule across the canvas and reserve its padding. Asserting on the
    // container rather than the text is what makes that distinction testable.
    const { container } = render(<TcStatusStrip line={{ kind: "idle" }} />);
    expect(screen.queryByTestId("tc-status-strip")).toBeNull();
    expect(container).toBeEmptyDOMElement();
  });

  it("never prints a ledger event type or a ledger id", () => {
    // Guards the regression this change exists to remove: the strip used to
    // read `view.derived · incidents.list_incidents · r252·010`.
    render(<TcStatusStrip line={{ kind: "assembling" }} />);
    const text = screen.getByTestId("tc-status-strip").textContent ?? "";
    expect(text).not.toMatch(/view\.derived|surface\.created|read\.executed/);
    expect(text).not.toMatch(/·\s*r[0-9a-f]{3}·\d+/i);
  });
});
