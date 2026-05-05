import { render, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  ApprovalFocusProvider,
  useApprovalFocus,
} from "./ApprovalFocusContext";

describe("ApprovalFocusProvider", () => {
  it("approveTopmost returns false when nothing is registered", () => {
    const { result } = renderHook(() => useApprovalFocus(), {
      wrapper: ({ children }) => (
        <ApprovalFocusProvider>{children}</ApprovalFocusProvider>
      ),
    });
    expect(result.current.approveTopmost()).toBe(false);
  });

  it("invokes the most recently registered approval", () => {
    const { result } = renderHook(() => useApprovalFocus(), {
      wrapper: ({ children }) => (
        <ApprovalFocusProvider>{children}</ApprovalFocusProvider>
      ),
    });
    const first = vi.fn();
    const second = vi.fn();
    result.current.register({ approvalId: "a", approve: first });
    result.current.register({ approvalId: "b", approve: second });
    expect(result.current.approveTopmost()).toBe(true);
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("unregister removes the entry", () => {
    const { result } = renderHook(() => useApprovalFocus(), {
      wrapper: ({ children }) => (
        <ApprovalFocusProvider>{children}</ApprovalFocusProvider>
      ),
    });
    const handler = vi.fn();
    result.current.register({ approvalId: "a", approve: handler });
    result.current.unregister("a");
    expect(result.current.approveTopmost()).toBe(false);
  });

  it("re-registering moves the entry to the bottom of insertion order", () => {
    const { result } = renderHook(() => useApprovalFocus(), {
      wrapper: ({ children }) => (
        <ApprovalFocusProvider>{children}</ApprovalFocusProvider>
      ),
    });
    const a = vi.fn();
    const b = vi.fn();
    result.current.register({ approvalId: "a", approve: a });
    result.current.register({ approvalId: "b", approve: b });
    result.current.register({ approvalId: "a", approve: a });
    result.current.approveTopmost();
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).not.toHaveBeenCalled();
  });

  it("returns a no-op shape when provider is missing", () => {
    function Probe(): null {
      const api = useApprovalFocus();
      expect(api.size()).toBe(0);
      expect(api.approveTopmost()).toBe(false);
      api.register({ approvalId: "no-op", approve: () => {} });
      api.unregister("no-op");
      return null;
    }
    render(<Probe />);
  });
});
