import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import type { PendingDiff } from "@enterprise-search/chat-surface";

import { EmailDiffOverlay } from "./EmailDiffOverlay";

const DIFF: PendingDiff = {
  diffId: "diff-1",
  provenance: "DRAFTED FROM SALESFORCE + Q4 SHEET",
  title: "Locked-price block sourced from MSA §3.2.",
  description: "Approve to send when ready.",
  regionAnchorId: "pending-block",
};

describe("EmailDiffOverlay", () => {
  it("renders the diff title and provenance", () => {
    render(
      <EmailDiffOverlay
        diff={DIFF}
        state="pending"
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );
    expect(screen.getByText(DIFF.title)).toBeInTheDocument();
    expect(screen.getByText(DIFF.provenance)).toBeInTheDocument();
  });

  it("exposes the diff id via data-diff-id", () => {
    render(
      <EmailDiffOverlay
        diff={DIFF}
        state="pending"
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );
    expect(screen.getByTestId("email-diff-overlay")).toHaveAttribute(
      "data-diff-id",
      DIFF.diffId,
    );
  });

  it("forwards Approve button clicks", () => {
    const onApprove = vi.fn();
    render(
      <EmailDiffOverlay
        diff={DIFF}
        state="pending"
        onApprove={onApprove}
        onReject={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-inline-diff-approve"));
    expect(onApprove).toHaveBeenCalledTimes(1);
  });

  it("forwards Reject button clicks", () => {
    const onReject = vi.fn();
    render(
      <EmailDiffOverlay
        diff={DIFF}
        state="pending"
        onApprove={() => {}}
        onReject={onReject}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-inline-diff-reject"));
    expect(onReject).toHaveBeenCalledTimes(1);
  });

  it("hides Approve/Reject in non-pending states", () => {
    render(
      <EmailDiffOverlay
        diff={DIFF}
        state="streaming"
        progressPercent={50}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );
    expect(
      screen.queryByTestId("tc-inline-diff-approve"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("tc-inline-diff-reject"),
    ).not.toBeInTheDocument();
  });
});
