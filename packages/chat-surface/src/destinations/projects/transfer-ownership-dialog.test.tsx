import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ProjectMember } from "./ProjectMembersTab";
import { TransferOwnershipDialog } from "./transfer-ownership-dialog";

const CANDIDATES: ReadonlyArray<ProjectMember> = [
  {
    userId: "user-owner",
    displayName: "Sarah Chen",
    role: "owner",
    joinedAt: new Date().toISOString(),
  },
  {
    userId: "user-marcus",
    displayName: "Marcus Wells",
    email: "marcus@acme.com",
    role: "editor",
    joinedAt: new Date().toISOString(),
  },
  {
    userId: "user-priya",
    displayName: "Priya Singh",
    role: "viewer",
    joinedAt: new Date().toISOString(),
  },
];

describe("TransferOwnershipDialog", () => {
  it("renders nothing when closed", () => {
    render(
      <TransferOwnershipDialog
        open={false}
        onClose={() => {}}
        projectName="Q4 sales push"
        currentOwnerUserId="user-owner"
        candidates={CANDIDATES}
        onTransfer={() => Promise.resolve()}
      />,
    );
    expect(
      screen.queryByTestId("transfer-ownership-dialog"),
    ).not.toBeInTheDocument();
  });

  it("renders a warning pill, warning text, and disables submit until both gates pass", () => {
    render(
      <TransferOwnershipDialog
        open={true}
        onClose={() => {}}
        projectName="Q4 sales push"
        currentOwnerUserId="user-owner"
        candidates={CANDIDATES}
        onTransfer={() => Promise.resolve()}
      />,
    );
    expect(
      screen.getByTestId("transfer-ownership-warning-pill"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("transfer-ownership-warning")).toHaveTextContent(
      "Q4 sales push",
    );
    const confirm = screen.getByTestId(
      "transfer-ownership-confirm",
    ) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
  });

  it("excludes the current owner from the candidate list", () => {
    render(
      <TransferOwnershipDialog
        open={true}
        onClose={() => {}}
        projectName="Q4 sales push"
        currentOwnerUserId="user-owner"
        candidates={CANDIDATES}
        onTransfer={() => Promise.resolve()}
      />,
    );
    const select = screen.getByTestId(
      "transfer-ownership-candidate",
    ) as HTMLSelectElement;
    const optionValues = Array.from(select.options).map((o) => o.value);
    expect(optionValues).toContain("user-marcus");
    expect(optionValues).toContain("user-priya");
    expect(optionValues).not.toContain("user-owner");
  });

  it("requires both a candidate AND the exact project name to enable submit", async () => {
    render(
      <TransferOwnershipDialog
        open={true}
        onClose={() => {}}
        projectName="Q4 sales push"
        currentOwnerUserId="user-owner"
        candidates={CANDIDATES}
        onTransfer={() => Promise.resolve()}
      />,
    );
    const user = userEvent.setup();
    const select = screen.getByTestId(
      "transfer-ownership-candidate",
    ) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "user-marcus" } });
    const confirm = screen.getByTestId(
      "transfer-ownership-confirm",
    ) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);

    const input = screen.getByTestId("transfer-ownership-confirm-input");
    await user.type(input, "wrong name");
    expect(confirm.disabled).toBe(true);

    await user.clear(input);
    await user.type(input, "Q4 sales push");
    expect(confirm.disabled).toBe(false);
  });

  it("calls onTransfer with the selected user id and closes on success", async () => {
    const onTransfer = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(
      <TransferOwnershipDialog
        open={true}
        onClose={onClose}
        projectName="Q4 sales push"
        currentOwnerUserId="user-owner"
        candidates={CANDIDATES}
        onTransfer={onTransfer}
      />,
    );
    const user = userEvent.setup();
    fireEvent.change(screen.getByTestId("transfer-ownership-candidate"), {
      target: { value: "user-marcus" },
    });
    await user.type(
      screen.getByTestId("transfer-ownership-confirm-input"),
      "Q4 sales push",
    );
    await user.click(screen.getByTestId("transfer-ownership-confirm"));
    expect(onTransfer).toHaveBeenCalledWith("user-marcus");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("surfaces an inline error when onTransfer rejects", async () => {
    const onTransfer = vi
      .fn()
      .mockRejectedValue(new Error("recipient no longer a member"));
    const onClose = vi.fn();
    render(
      <TransferOwnershipDialog
        open={true}
        onClose={onClose}
        projectName="Q4 sales push"
        currentOwnerUserId="user-owner"
        candidates={CANDIDATES}
        onTransfer={onTransfer}
      />,
    );
    const user = userEvent.setup();
    fireEvent.change(screen.getByTestId("transfer-ownership-candidate"), {
      target: { value: "user-marcus" },
    });
    await user.type(
      screen.getByTestId("transfer-ownership-confirm-input"),
      "Q4 sales push",
    );
    await user.click(screen.getByTestId("transfer-ownership-confirm"));
    expect(
      await screen.findByTestId("transfer-ownership-error"),
    ).toHaveTextContent("recipient no longer a member");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("shows the no-candidates hint when only the owner is in the list", () => {
    render(
      <TransferOwnershipDialog
        open={true}
        onClose={() => {}}
        projectName="Q4 sales push"
        currentOwnerUserId="user-owner"
        candidates={[CANDIDATES[0]!]}
        onTransfer={() => Promise.resolve()}
      />,
    );
    expect(
      screen.getByTestId("transfer-ownership-no-candidates"),
    ).toBeInTheDocument();
  });

  it("clicking the backdrop closes the dialog", async () => {
    const onClose = vi.fn();
    render(
      <TransferOwnershipDialog
        open={true}
        onClose={onClose}
        projectName="Q4 sales push"
        currentOwnerUserId="user-owner"
        candidates={CANDIDATES}
        onTransfer={() => Promise.resolve()}
      />,
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId("transfer-ownership-dialog"));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Cancel button closes the dialog without calling onTransfer", () => {
    const onTransfer = vi.fn();
    const onClose = vi.fn();
    render(
      <TransferOwnershipDialog
        open={true}
        onClose={onClose}
        projectName="Q4 sales push"
        currentOwnerUserId="user-owner"
        candidates={CANDIDATES}
        onTransfer={onTransfer}
      />,
    );
    fireEvent.click(screen.getByTestId("transfer-ownership-cancel"));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onTransfer).not.toHaveBeenCalled();
  });
});
