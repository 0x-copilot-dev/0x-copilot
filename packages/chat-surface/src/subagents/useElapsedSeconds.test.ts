// FR-1.30 — proves the prefixed-`setInterval`→bare-`setInterval` rewrite kept
// the 5000 ms elapsed cadence byte-identical after the hoist into
// chat-surface. Fake timers replace the global `setInterval`/`clearInterval`
// the hook now references without the browser-object prefix.

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useElapsedSeconds } from "./useElapsedSeconds";

describe("useElapsedSeconds", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-07T10:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("ticks every 5000 ms while active", () => {
    const { result } = renderHook(() => useElapsedSeconds(true, null));
    expect(result.current).toBe(0);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(result.current).toBe(5);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(result.current).toBe(10);
  });

  it("does not tick when inactive", () => {
    const { result } = renderHook(() => useElapsedSeconds(false, null));
    expect(result.current).toBe(0);
    act(() => {
      vi.advanceTimersByTime(20000);
    });
    expect(result.current).toBe(0);
  });

  it("computes elapsed from startedAt when provided", () => {
    // Started 8 s before the (faked) mount time.
    const started = new Date("2026-05-07T09:59:52Z").toISOString();
    const { result } = renderHook(() => useElapsedSeconds(true, started));
    expect(result.current).toBe(8);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(result.current).toBe(13);
  });
});
