import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import {
  autoOpenTab,
  shouldAutoOpenWorkspacePane,
  useWorkspacePaneAutoOpenSignal,
} from "./useWorkspacePaneAutoOpen";

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

describe("autoOpenTab", () => {
  it("returns null when nothing has data", () => {
    expect(autoOpenTab({ subagentCount: 0, sourceCount: 0 })).toBeNull();
  });

  it("prefers running subagents over sources", () => {
    expect(autoOpenTab({ subagentCount: 1, sourceCount: 4 })).toBe("agents");
  });

  it("falls back to sources, drafts, approvals in priority order", () => {
    expect(autoOpenTab({ subagentCount: 0, sourceCount: 1 })).toBe("sources");
    expect(
      autoOpenTab({ subagentCount: 0, sourceCount: 0, draftCount: 1 }),
    ).toBe("draft");
    expect(
      autoOpenTab({
        subagentCount: 0,
        sourceCount: 0,
        pendingApprovalsCount: 1,
      }),
    ).toBe("approvals");
  });
});

describe("useWorkspacePaneAutoOpenSignal", () => {
  it("fires onAutoOpen exactly once per conversation visit", () => {
    const onAutoOpen = vi.fn();
    const { rerender } = renderHook(
      ({ sourceCount }: { sourceCount: number }) =>
        useWorkspacePaneAutoOpenSignal({
          conversationId: "conv-1",
          subagentCount: 0,
          sourceCount,
          onAutoOpen,
        }),
      { initialProps: { sourceCount: 0 } },
    );
    expect(onAutoOpen).not.toHaveBeenCalled();
    rerender({ sourceCount: 1 });
    expect(onAutoOpen).toHaveBeenCalledTimes(1);
    expect(onAutoOpen).toHaveBeenLastCalledWith("sources");
    rerender({ sourceCount: 5 });
    expect(onAutoOpen).toHaveBeenCalledTimes(1);
  });

  it("does nothing while suppressed", () => {
    const onAutoOpen = vi.fn();
    const { rerender } = renderHook(
      ({ suppressed }: { suppressed: boolean }) =>
        useWorkspacePaneAutoOpenSignal({
          conversationId: "conv-1",
          subagentCount: 0,
          sourceCount: 4,
          suppressed,
          onAutoOpen,
        }),
      { initialProps: { suppressed: true } },
    );
    expect(onAutoOpen).not.toHaveBeenCalled();
    rerender({ suppressed: false });
    expect(onAutoOpen).toHaveBeenCalledTimes(1);
  });

  it("fires again when switching to a fresh conversation", () => {
    const onAutoOpen = vi.fn();
    const { rerender } = renderHook(
      ({ id, count }: { id: string; count: number }) =>
        useWorkspacePaneAutoOpenSignal({
          conversationId: id,
          subagentCount: 0,
          sourceCount: count,
          onAutoOpen,
        }),
      { initialProps: { id: "conv-1", count: 1 } },
    );
    expect(onAutoOpen).toHaveBeenCalledTimes(1);
    rerender({ id: "conv-1", count: 4 });
    expect(onAutoOpen).toHaveBeenCalledTimes(1);
    rerender({ id: "conv-2", count: 2 });
    expect(onAutoOpen).toHaveBeenCalledTimes(2);
    expect(onAutoOpen.mock.calls[1]?.[0]).toBe("sources");
  });
});
