// The canvas half of "one row asks, the canvas shows".
//
// The regression worth naming: before this card existed, a parked WRITE
// rendered `TcGateCard` on the canvas — the OAuth connect card — because both
// gate kinds ride the same `gate.opened` event and a write gate reports
// `auth_state: insufficient`. The user saw "More access needed" above a
// **Connect** button for a connector that was already connected, while the
// actual decision sat in the chat column.

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { TcWriteGateCard } from "./TcWriteGateCard";

function card(overrides: Partial<Parameters<typeof TcWriteGateCard>[0]> = {}) {
  const props = {
    title: "Create an issue in Parth-test",
    connector: "linear",
    params: [
      { label: "title", value: "Flaky MCP reconnect after sleep" },
      { label: "team", value: "Parth-test" },
    ],
    ledgerId: "r7f3·142",
    irreversible: false,
    onApprove: vi.fn(),
    onDecline: vi.fn(),
    ...overrides,
  };
  render(<TcWriteGateCard {...props} />);
  return props;
}

describe("TcWriteGateCard — the detail the row does not carry", () => {
  it("shows the payload the reviewer has to judge", () => {
    card();
    const params = screen.getByTestId("tc-write-gate-card-params");
    expect(params.textContent).toContain("title");
    expect(params.textContent).toContain("Flaky MCP reconnect after sleep");
    expect(params.textContent).toContain("Parth-test");
  });

  it("anchors the decision to the ledger id it will be recorded under", () => {
    card();
    expect(screen.getByTestId("tc-write-gate-card-ledger-id").textContent).toBe(
      "r7f3·142",
    );
  });

  it("offers Approve and Decline — never Connect", () => {
    // The whole point: this connector IS connected. A Connect button here is
    // the bug the card replaces.
    card();
    expect(screen.getByTestId("tc-write-gate-card-approve")).toBeTruthy();
    expect(screen.getByTestId("tc-write-gate-card-decline")).toBeTruthy();
    expect(screen.queryByText("Connect")).toBeNull();
    expect(screen.queryByText("More access needed")).toBeNull();
  });

  it("resolves through the standard decision handlers", () => {
    const props = card();
    screen.getByTestId("tc-write-gate-card-approve").click();
    expect(props.onApprove).toHaveBeenCalledTimes(1);
    screen.getByTestId("tc-write-gate-card-decline").click();
    expect(props.onDecline).toHaveBeenCalledTimes(1);
  });
});

describe("TcWriteGateCard — reversibility", () => {
  it("states the reversible case as a fact, not a caution", () => {
    card();
    expect(
      screen.getByTestId("tc-write-gate-card-reversibility").textContent,
    ).toBe("You can undo this from the connector if it's wrong.");
  });

  it("says plainly when it cannot be undone", () => {
    card({ irreversible: true });
    expect(
      screen.getByTestId("tc-write-gate-card-reversibility").textContent,
    ).toBe("This cannot be undone from here.");
    expect(
      screen.getByTestId("tc-write-gate-card").getAttribute("data-risk"),
    ).toBe("high");
  });
});

describe("TcWriteGateCard — degraded input", () => {
  it("renders without a params frame when the approval carried none", () => {
    // The gate can open before its approval projection lands. Blanking the
    // surface somebody is waiting on would be worse than a card with no table.
    card({ params: [] });
    expect(screen.queryByTestId("tc-write-gate-card-params")).toBeNull();
    expect(screen.getByTestId("tc-write-gate-card-title")).toBeTruthy();
  });

  it("drops the connector from the eyebrow rather than printing a separator", () => {
    card({ connector: null });
    expect(screen.getByTestId("tc-write-gate-card-eyebrow").textContent).toBe(
      "Waiting on you",
    );
  });

  it("renders hostile argument values as text", () => {
    const hostile = "<img src=x onerror=alert(1)>";
    card({ params: [{ label: "title", value: hostile }] });
    const params = screen.getByTestId("tc-write-gate-card-params");
    expect(params.textContent).toContain(hostile);
    expect(params.querySelector("img")).toBeNull();
  });
});
