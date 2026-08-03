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

import { describe, expect, it } from "vitest";
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
              kind: "confirmation",
              title: "Create an issue in Parth-test",
              summary: null,
              target: "linear",
              resolved: false,
              resolvedLabel: null,
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
});
