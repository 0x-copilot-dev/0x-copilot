import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { TcInlineDiff, type InlineDiffState } from "./TcInlineDiff";

const ALL_STATES: readonly InlineDiffState[] = [
  "idle",
  "streaming",
  "pending",
  "accepted",
  "rejected",
];

describe("TcInlineDiff", () => {
  it.each(ALL_STATES)("renders the title in state %s", (state) => {
    render(<TcInlineDiff state={state} title="A pending change" />);
    expect(screen.getByText("A pending change")).toBeInTheDocument();
  });

  it.each(ALL_STATES)(
    "exposes state via data-state attribute (%s)",
    (state) => {
      render(<TcInlineDiff state={state} title="x" />);
      const card = screen.getByRole("group");
      expect(card).toHaveAttribute("data-state", state);
    },
  );

  it("only renders Approve/Reject buttons in the pending state", () => {
    for (const state of ALL_STATES) {
      const { unmount } = render(
        <TcInlineDiff
          state={state}
          title="x"
          onApprove={() => {}}
          onReject={() => {}}
        />,
      );
      const approve = screen.queryByTestId("tc-inline-diff-approve");
      const reject = screen.queryByTestId("tc-inline-diff-reject");
      if (state === "pending") {
        expect(approve).toBeInTheDocument();
        expect(reject).toBeInTheDocument();
      } else {
        expect(approve).not.toBeInTheDocument();
        expect(reject).not.toBeInTheDocument();
      }
      unmount();
    }
  });

  it("fires onApprove and onReject on click", () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(
      <TcInlineDiff
        state="pending"
        title="x"
        onApprove={onApprove}
        onReject={onReject}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-inline-diff-approve"));
    expect(onApprove).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("tc-inline-diff-reject"));
    expect(onReject).toHaveBeenCalledTimes(1);
  });

  it("renders the progress percent in the streaming pill", () => {
    render(<TcInlineDiff state="streaming" title="x" progressPercent={64} />);
    expect(screen.getByTestId("tc-inline-diff-pill")).toHaveTextContent(
      "STREAMING · 64%",
    );
  });

  it("renders the provenance label when provided", () => {
    render(
      <TcInlineDiff
        state="pending"
        title="x"
        provenance="DRAFTED FROM SALESFORCE"
      />,
    );
    expect(screen.getByTestId("tc-inline-diff-provenance")).toHaveTextContent(
      "DRAFTED FROM SALESFORCE",
    );
  });

  it("uses custom button labels when provided", () => {
    render(
      <TcInlineDiff
        state="pending"
        title="x"
        approveLabel="Approve & send"
        rejectLabel="Discard"
      />,
    );
    expect(screen.getByTestId("tc-inline-diff-approve")).toHaveTextContent(
      "Approve & send",
    );
    expect(screen.getByTestId("tc-inline-diff-reject")).toHaveTextContent(
      "Discard",
    );
  });
});
