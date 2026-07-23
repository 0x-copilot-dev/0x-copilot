import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { TcApproveBar, approveBarMicrocopy } from "./TcApproveBar";
import type {
  LedgerStageRevision,
  LedgerStagedWrite,
} from "./ledgerProjection";

function revision(rev: number, author: "agent" | "user"): LedgerStageRevision {
  return {
    rev,
    author,
    proposalRef: `draft://d1/v${rev}`,
    diffRef: `draft://d1/v${rev - 1}..v${rev}`,
    authorshipSpans: [],
    seq: rev,
    ledgerId: `rrun1·00${rev}`,
  };
}

function stage(overrides: Partial<LedgerStagedWrite> = {}): LedgerStagedWrite {
  const revisions = overrides.revisions ?? [revision(1, "agent")];
  const latestRev = overrides.latestRev ?? revisions[revisions.length - 1].rev;
  return {
    stageId: "stage_1",
    surfaceId: "surf_1",
    draftId: "d1",
    target: { connector: "gmail", op: "send" },
    latestRev,
    approvedRev: null,
    status: "staged",
    revisions,
    decisions: [],
    createdSeq: 2,
    lastSeq: 3,
    ledgerId: "rrun1·002",
    latestRevision: revisions[revisions.length - 1],
    applyResult: null,
    applyFailureCode: null,
    ...overrides,
  };
}

describe("TcApproveBar", () => {
  it("pins the exact WYSIWYG microcopy with the latest rev", () => {
    render(
      <TcApproveBar
        stage={stage({ latestRev: 1 })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onRestore={vi.fn()}
      />,
    );
    expect(screen.getByTestId("tc-approve-bar-copy")).toHaveTextContent(
      "Exactly this draft — rev 1 — is what sends.",
    );
    expect(approveBarMicrocopy(1)).toBe(
      "Exactly this draft — rev 1 — is what sends.",
    );
  });

  it("re-pins to the new rev after an edit bumps latestRev", () => {
    const two = stage({
      latestRev: 2,
      revisions: [revision(1, "agent"), revision(2, "user")],
    });
    render(
      <TcApproveBar
        stage={two}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onRestore={vi.fn()}
      />,
    );
    expect(screen.getByTestId("tc-approve-bar-copy")).toHaveTextContent(
      "Exactly this draft — rev 2 — is what sends.",
    );
    expect(screen.getByTestId("tc-approve-bar-approve")).toHaveTextContent(
      "Approve rev 2",
    );
  });

  it("approve fires the host callback with the pinned rev", () => {
    const onApprove = vi.fn();
    render(
      <TcApproveBar
        stage={stage({ latestRev: 2 })}
        onApprove={onApprove}
        onReject={vi.fn()}
        onRestore={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-approve-bar-approve"));
    expect(onApprove).toHaveBeenCalledWith("stage_1", 2);
  });

  it("swaps Approve/Reject for Restore when rejected", () => {
    const onRestore = vi.fn();
    render(
      <TcApproveBar
        stage={stage({ status: "rejected" })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onRestore={onRestore}
      />,
    );
    expect(screen.queryByTestId("tc-approve-bar-approve")).toBeNull();
    expect(screen.getByTestId("tc-approve-bar-copy")).toHaveTextContent(
      "rejected",
    );
    fireEvent.click(screen.getByTestId("tc-approve-bar-restore"));
    expect(onRestore).toHaveBeenCalledWith("stage_1");
  });

  it("shows the ledger-id chip and disables approve once decided", () => {
    render(
      <TcApproveBar
        stage={stage({ status: "approved", approvedRev: 1 })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onRestore={vi.fn()}
      />,
    );
    expect(screen.getByTestId("tc-approve-bar-ledger-id")).toHaveTextContent(
      "rrun1·002",
    );
    expect(screen.getByTestId("tc-approve-bar-approve")).toBeDisabled();
    expect(screen.getByTestId("tc-approve-bar-approve")).toHaveTextContent(
      "Approved",
    );
  });
});
