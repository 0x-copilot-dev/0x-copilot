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
