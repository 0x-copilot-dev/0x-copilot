// Countdown timer hook for the consent-card undo window (PR-1.6). Moved
// down with the hook from apps/frontend; the same assertions run from
// chat-surface and prove the FR-1.30 `window.set/clearInterval` → bare
// `set/clearInterval` rewrite preserved the 1000 ms tick byte-for-byte.

import { renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useUndoCountdown } from "./useUndoCountdown";

describe("useUndoCountdown", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-07T19:30:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns expired immediately when undoUntil is null", () => {
    const { result } = renderHook(() => useUndoCountdown(null));
    expect(result.current.expired).toBe(true);
    expect(result.current.secondsRemaining).toBe(0);
  });

  it("ticks down once per second", () => {
    const undoUntil = new Date("2026-05-07T19:30:10Z"); // +10s
    const { result } = renderHook(() => useUndoCountdown(undoUntil));
    expect(result.current.secondsRemaining).toBe(10);
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(result.current.secondsRemaining).toBe(7);
    expect(result.current.expired).toBe(false);
  });

  it("expires when the wall clock crosses undoUntil", () => {
    const undoUntil = new Date("2026-05-07T19:30:02Z"); // +2s
    const { result } = renderHook(() => useUndoCountdown(undoUntil));
    expect(result.current.expired).toBe(false);
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(result.current.expired).toBe(true);
    expect(result.current.secondsRemaining).toBe(0);
  });

  it("clears the interval on unmount", () => {
    // Spy on the bare global (via globalThis, not the substrate-banned
    // `window`) that the neutralized hook now calls.
    const clearSpy = vi.spyOn(globalThis, "clearInterval");
    const undoUntil = new Date("2026-05-07T19:31:00Z");
    const { unmount } = renderHook(() => useUndoCountdown(undoUntil));
    unmount();
    expect(clearSpy).toHaveBeenCalled();
  });
});
