// The ask is one row; the detail is on the canvas.
//
// The safety property worth pinning is the one the compact form BUYS: an
// irreversible write has no approve button in the feed at all. That is only
// expressible once the row is small enough for the choice of button to be the
// loudest thing on it — the old twelve-line card offered Approve next to a
// paragraph nobody reads while a run is streaming.

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { TcWriteGateRow } from "./TcWriteGateRow";

function row(overrides: Partial<Parameters<typeof TcWriteGateRow>[0]> = {}) {
  const props = {
    title: "Create an issue in Parth-test",
    connector: "linear",
    irreversible: false,
    onApprove: vi.fn(),
    onDecline: vi.fn(),
    onReview: vi.fn(),
    ...overrides,
  };
  render(<TcWriteGateRow {...props} />);
  return props;
}

describe("TcWriteGateRow — a reversible write", () => {
  it("asks in one line: what, where, and two buttons", () => {
    row();
    expect(screen.getByTestId("tc-write-gate-title").textContent).toBe(
      "Create an issue in Parth-test",
    );
    expect(screen.getByTestId("tc-write-gate-connector").textContent).toBe(
      "linear",
    );
    expect(screen.getByTestId("tc-write-gate-approve")).toBeTruthy();
    expect(screen.getByTestId("tc-write-gate-decline")).toBeTruthy();
  });

  it("approves and declines through the standard decision handlers", () => {
    // Approval rides `decision` on the /decision POST, never the free text the
    // question card would have collected — the gate only borrows the
    // ask_a_question WIRE shape, it is not a question.
    const props = row();
    screen.getByTestId("tc-write-gate-approve").click();
    expect(props.onApprove).toHaveBeenCalledTimes(1);
    screen.getByTestId("tc-write-gate-decline").click();
    expect(props.onDecline).toHaveBeenCalledTimes(1);
  });

  it("omits the connector label rather than printing an empty one", () => {
    row({ connector: null });
    expect(screen.queryByTestId("tc-write-gate-connector")).toBeNull();
  });
});

describe("TcWriteGateRow — an irreversible write", () => {
  it("offers no approve button — the canvas is the only way through", () => {
    const props = row({ irreversible: true });

    expect(screen.queryByTestId("tc-write-gate-approve")).toBeNull();
    screen.getByTestId("tc-write-gate-review").click();
    expect(props.onReview).toHaveBeenCalledTimes(1);
    expect(props.onApprove).not.toHaveBeenCalled();
  });

  it("still lets it be declined in one click", () => {
    // Declining is safe by definition. Making somebody open a canvas to say no
    // would push them toward leaving it parked instead.
    const props = row({ irreversible: true });
    screen.getByTestId("tc-write-gate-decline").click();
    expect(props.onDecline).toHaveBeenCalledTimes(1);
  });

  it("marks the risk on the row itself, not with a coloured panel", () => {
    row({ irreversible: true });
    expect(
      screen.getByTestId("tc-write-gate-row").getAttribute("data-risk"),
    ).toBe("high");
  });
});

describe("TcWriteGateRow — untrusted text", () => {
  it("renders a hostile title as a text node", () => {
    const hostile = "<img src=x onerror=alert(1)>";
    row({ title: hostile });
    const title = screen.getByTestId("tc-write-gate-title");
    expect(title.textContent).toBe(hostile);
    expect(title.querySelector("img")).toBeNull();
  });

  it("disables both actions while a decision is in flight", () => {
    row({ busy: true });
    expect(
      screen.getByTestId("tc-write-gate-approve").hasAttribute("disabled"),
    ).toBe(true);
    expect(
      screen.getByTestId("tc-write-gate-decline").hasAttribute("disabled"),
    ).toBe(true);
  });
});

// The dead-control regression. `onReviewWriteGate` had no producer, so this
// button did nothing — and for an IRREVERSIBLE write it is the PRIMARY action,
// with Approve deliberately withheld until the payload has been seen. The
// safety design that refuses a blind approval had become a refusal to allow any
// approval: those gates could only be declined.
describe("TcWriteGateRow — Review must actually do something", () => {
  it("calls onReview for an irreversible write, where it is the only way forward", () => {
    const onReview = vi.fn();
    render(
      <TcWriteGateRow
        title="Delete the staging index"
        connector="elastic"
        irreversible
        onApprove={vi.fn()}
        onDecline={vi.fn()}
        onReview={onReview}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /review/i }));
    expect(onReview).toHaveBeenCalledTimes(1);
  });
});

// Expanding in place. The safety rule this must preserve is an ORDERING rule,
// not a location rule: an approve control for an irreversible write must not be
// reachable in ONE click from the collapsed row. Approving after the payload
// has rendered is the thing the old design was protecting; refusing it there
// too is what turned Review into a dead end.
describe("TcWriteGateRow — expand in place", () => {
  const PARAMS = [
    { label: "repo", value: "Parth-test" },
    { label: "title", value: "Flaky MCP reconnect" },
  ];

  function row(over: Record<string, unknown> = {}) {
    return render(
      <TcWriteGateRow
        title="Delete the staging index"
        connector="elastic"
        irreversible
        params={PARAMS}
        ledgerId="r7f3·142"
        onApprove={vi.fn()}
        onDecline={vi.fn()}
        onReview={vi.fn()}
        {...over}
      />,
    );
  }

  it("shows no payload until it is expanded", () => {
    row();
    expect(screen.queryByTestId("tc-write-gate-body")).toBeNull();
    expect(screen.queryByTestId("tc-write-gate-body-params")).toBeNull();
  });

  it("reveals the params, the reversibility line and the audit anchor", () => {
    row();
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(
      screen.getByTestId("tc-write-gate-body-params").textContent,
    ).toContain("Parth-test");
    expect(
      screen.getByTestId("tc-write-gate-body-reversibility").textContent,
    ).toBe("This cannot be undone from here.");
    expect(screen.getByTestId("tc-write-gate-body-ledger-id").textContent).toBe(
      "r7f3·142",
    );
  });

  it("omits the audit anchor rather than guessing when there is no ledger row", () => {
    row({ ledgerId: undefined });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.queryByTestId("tc-write-gate-body-ledger-id")).toBeNull();
  });

  // The ordering rule, both halves.
  it("offers no approve control at all while collapsed", () => {
    row();
    expect(screen.queryByTestId("tc-write-gate-approve")).toBeNull();
    expect(screen.queryByTestId("tc-write-gate-body-approve")).toBeNull();
  });

  it("offers approve in the body once the payload is on screen", () => {
    const onApprove = vi.fn();
    row({ onApprove });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    fireEvent.click(screen.getByTestId("tc-write-gate-body-approve"));
    expect(onApprove).toHaveBeenCalledTimes(1);
    // Still never one click from the collapsed row.
    expect(screen.queryByTestId("tc-write-gate-approve")).toBeNull();
  });

  // A gate can open before its approval projection lands, so an empty frame is
  // reachable — and approving over one IS the blind approval the rule forbids.
  it("withholds approve when expanding reveals an empty payload", () => {
    row({ params: [] });
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.getByTestId("tc-write-gate-body")).toBeTruthy();
    expect(screen.queryByTestId("tc-write-gate-body-approve")).toBeNull();
    expect(screen.queryByTestId("tc-write-gate-approve")).toBeNull();
  });

  it("keeps decline one click in every state", () => {
    const onDecline = vi.fn();
    row({ onDecline });
    fireEvent.click(screen.getByTestId("tc-write-gate-decline"));
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    fireEvent.click(screen.getByTestId("tc-write-gate-decline"));
    expect(onDecline).toHaveBeenCalledTimes(2);
  });

  it("toggles without needing a host — the dead-control shape cannot return", () => {
    render(
      <TcWriteGateRow
        title="Delete the staging index"
        connector="elastic"
        irreversible
        params={PARAMS}
        onApprove={vi.fn()}
        onDecline={vi.fn()}
        onReview={() => {
          throw new Error("host threw");
        }}
      />,
    );
    // Even a host that explodes must not stop the row from opening. Written
    // against a THROWING host rather than a missing one because the original
    // regression was a callback that silently did nothing — a row whose
    // behaviour is contingent on the host is the shape to keep out, in any of
    // its forms.
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.getByTestId("tc-write-gate-body")).toBeTruthy();
  });

  it("lets a reversible write be inspected without giving up its one-click approve", () => {
    const onApprove = vi.fn();
    row({ irreversible: false, onApprove });
    expect(screen.getByTestId("tc-write-gate-approve")).toBeTruthy();
    fireEvent.click(screen.getByTestId("tc-write-gate-review"));
    expect(screen.getByTestId("tc-write-gate-body-params")).toBeTruthy();
    fireEvent.click(screen.getByTestId("tc-write-gate-approve"));
    expect(onApprove).toHaveBeenCalledTimes(1);
  });
});
