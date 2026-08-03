// The Approvals panel stacks two lists with DIFFERENT scopes, and for a while
// it told the reader both that there were two things to approve and that there
// was nothing to approve — at the same time, six lines apart.
//
// A live packaged run put cross-run gate cards ("approve destructive save_issue
// on linear", twice) directly above this tab's "No pending approvals in this
// conversation." Both statements were true — the gates belonged to other chats
// — and neither list said which scope it spoke for, so together they read as a
// straight contradiction.
//
// These pin the scope labelling, because the empty state is the half that looks
// most obviously correct in isolation and is exactly the half that misleads.

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { ApprovalsTab } from "./ApprovalsTab";
import type { ApprovalsQueueProjection } from "./types";

const EMPTY: ApprovalsQueueProjection = { pending: [], recent: [] };

describe("ApprovalsTab — whose approvals is this list?", () => {
  it("speaks for the whole panel when it is the only list", () => {
    render(<ApprovalsTab queue={EMPTY} />);

    expect(
      screen.getByTestId("workspace-approvals-tab-empty").textContent,
    ).toBe("No pending approvals in this conversation.");
    expect(screen.queryByTestId("workspace-approvals-group")).toBeNull();
  });

  it("names its scope and narrows its empty copy when a sibling list is up", () => {
    render(<ApprovalsTab queue={EMPTY} groupLabel="This conversation" />);

    expect(screen.getByTestId("workspace-approvals-group").textContent).toBe(
      "This conversation",
    );
    // "Nothing waiting HERE" — under a heading that says where "here" is. The
    // unqualified sentence would still be true and would still read as a denial
    // of the cards rendered above it.
    expect(
      screen.getByTestId("workspace-approvals-tab-empty").textContent,
    ).toContain("Nothing waiting here.");
  });

  it("keeps the heading when it does have items, so the pair stays symmetric", () => {
    render(
      <ApprovalsTab
        groupLabel="This conversation"
        queue={{
          pending: [
            {
              approvalId: "a1",
              messageId: "m1",
              runId: "run_1",
              approvalKind: "ask_a_question",
              title: "Create an issue in Parth-test",
              summary: null,
              target: "linear",
              resolved: false,
              resolvedAt: null,
            },
          ],
          recent: [],
        }}
      />,
    );

    expect(screen.getByTestId("workspace-approvals-group").textContent).toBe(
      "This conversation",
    );
    expect(screen.getByTestId("workspace-approvals-tab")).toBeTruthy();
  });

  it("decides a pending row in place, without a trip to the transcript", () => {
    // The panel that COUNTS the parked write can now resolve it. Before this
    // the only affordance was "jump to the card in the thread", which is a
    // detour when the row already says what the write is.
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(
      <ApprovalsTab
        onApprove={onApprove}
        onReject={onReject}
        queue={{ pending: [pendingItem()], recent: [] }}
      />,
    );

    screen.getByTestId("approvals-row-approve").click();
    expect(onApprove).toHaveBeenCalledWith("a1");
    screen.getByTestId("approvals-row-decline").click();
    expect(onReject).toHaveBeenCalledWith("a1");
  });

  it("a resolved row shows when it settled, not buttons to settle it again", () => {
    render(
      <ApprovalsTab
        onApprove={() => {}}
        queue={{
          pending: [],
          recent: [
            {
              ...pendingItem(),
              resolved: true,
              resolvedAt: new Date(0).toISOString(),
            },
          ],
        }}
      />,
    );

    expect(screen.queryByTestId("approvals-row-approve")).toBeNull();
  });

  it("omits the buttons entirely when no decision handler is supplied", () => {
    render(<ApprovalsTab queue={{ pending: [pendingItem()], recent: [] }} />);
    expect(screen.queryByTestId("approvals-row-approve")).toBeNull();
  });
});

function pendingItem() {
  return {
    approvalId: "a1",
    messageId: "m1",
    runId: "run_1",
    approvalKind: "ask_a_question" as const,
    title: "Create an issue in Parth-test",
    summary: "Allow Linear to run save_issue?",
    target: "linear",
    resolved: false,
    resolvedAt: null,
  };
}
