// PR 3.2 — useWorkspacePaneState contract.

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useWorkspacePaneState } from "./useWorkspacePaneState";

describe("useWorkspacePaneState", () => {
  it("starts closed on the default tab", () => {
    const { result } = renderHook(() =>
      useWorkspacePaneState({ conversationId: "c1" }),
    );
    expect(result.current.open).toBe(false);
    expect(result.current.activeTab).toBe("sources");
  });

  it("openOn sets open + active tab + focus opts", () => {
    const { result } = renderHook(() =>
      useWorkspacePaneState({ conversationId: "c1" }),
    );
    act(() => {
      result.current.openOn("agents", { focusSubagentTaskId: "task-1" });
    });
    expect(result.current.open).toBe(true);
    expect(result.current.activeTab).toBe("agents");
    expect(result.current.focus.subagentTaskId).toBe("task-1");
  });

  it("close('manual') poisons auto-open memory for the conversation", () => {
    const { result } = renderHook(() =>
      useWorkspacePaneState({ conversationId: "c1", initialOpen: true }),
    );
    expect(result.current.isAutoOpenSuppressed("c1")).toBe(false);
    act(() => result.current.close("manual"));
    expect(result.current.open).toBe(false);
    expect(result.current.isAutoOpenSuppressed("c1")).toBe(true);
  });

  it("close('viewport') does NOT poison auto-open memory", () => {
    const { result } = renderHook(() =>
      useWorkspacePaneState({ conversationId: "c1", initialOpen: true }),
    );
    act(() => result.current.close("viewport"));
    expect(result.current.open).toBe(false);
    expect(result.current.isAutoOpenSuppressed("c1")).toBe(false);
  });

  it("re-opening via openOn clears the manual-close memory", () => {
    const { result } = renderHook(() =>
      useWorkspacePaneState({ conversationId: "c1", initialOpen: true }),
    );
    act(() => result.current.close("manual"));
    expect(result.current.isAutoOpenSuppressed("c1")).toBe(true);
    act(() => result.current.openOn("draft"));
    expect(result.current.isAutoOpenSuppressed("c1")).toBe(false);
    expect(result.current.activeTab).toBe("draft");
  });

  it("toggle closes are manual; toggle opens clear suppression", () => {
    const { result } = renderHook(() =>
      useWorkspacePaneState({ conversationId: "c1", initialOpen: true }),
    );
    act(() => result.current.toggle());
    expect(result.current.isAutoOpenSuppressed("c1")).toBe(true);
    act(() => result.current.toggle());
    expect(result.current.isAutoOpenSuppressed("c1")).toBe(false);
  });

  it("manual-close memory is per-conversation", () => {
    const { result, rerender } = renderHook(
      ({ id }: { id: string }) =>
        useWorkspacePaneState({ conversationId: id, initialOpen: true }),
      { initialProps: { id: "c1" } },
    );
    act(() => result.current.close("manual"));
    expect(result.current.isAutoOpenSuppressed("c1")).toBe(true);
    rerender({ id: "c2" });
    expect(result.current.isAutoOpenSuppressed("c2")).toBe(false);
    expect(result.current.isAutoOpenSuppressed("c1")).toBe(true);
  });

  it("setActiveTab swaps without changing open/closed", () => {
    const { result } = renderHook(() =>
      useWorkspacePaneState({ conversationId: "c1", initialOpen: true }),
    );
    act(() => result.current.setActiveTab("approvals"));
    expect(result.current.open).toBe(true);
    expect(result.current.activeTab).toBe("approvals");
  });
});
