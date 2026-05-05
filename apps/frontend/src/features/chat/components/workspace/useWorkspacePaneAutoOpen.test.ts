import { describe, expect, it } from "vitest";
import { shouldAutoOpenWorkspacePane } from "./useWorkspacePaneAutoOpen";

describe("shouldAutoOpenWorkspacePane", () => {
  it("stays closed when every count is zero", () => {
    expect(
      shouldAutoOpenWorkspacePane({
        subagentCount: 0,
        sourceCount: 0,
        draftCount: 0,
        pendingApprovalsCount: 0,
      }),
    ).toBe(false);
  });

  it("opens when there are subagents", () => {
    expect(
      shouldAutoOpenWorkspacePane({
        subagentCount: 1,
        sourceCount: 0,
      }),
    ).toBe(true);
  });

  it("opens when there are sources", () => {
    expect(
      shouldAutoOpenWorkspacePane({
        subagentCount: 0,
        sourceCount: 3,
      }),
    ).toBe(true);
  });

  it("opens for drafts or pending approvals", () => {
    expect(
      shouldAutoOpenWorkspacePane({
        subagentCount: 0,
        sourceCount: 0,
        draftCount: 1,
      }),
    ).toBe(true);
    expect(
      shouldAutoOpenWorkspacePane({
        subagentCount: 0,
        sourceCount: 0,
        pendingApprovalsCount: 1,
      }),
    ).toBe(true);
  });
});
