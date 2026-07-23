import { renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { RunActivityBusProvider, useRunActivityBus } from "./runActivityBus";

function withProvider({ children }: { children: ReactNode }) {
  return createElement(RunActivityBusProvider, null, children);
}

describe("runActivityBus", () => {
  it("fans a publish out to every subscriber", () => {
    const { result } = renderHook(() => useRunActivityBus(), {
      wrapper: withProvider,
    });
    let a = 0;
    let b = 0;
    result.current.subscribe(() => {
      a += 1;
    });
    result.current.subscribe(() => {
      b += 1;
    });
    result.current.publish();
    expect(a).toBe(1);
    expect(b).toBe(1);
    result.current.publish();
    expect(a).toBe(2);
    expect(b).toBe(2);
  });

  it("stops calling a handler after it unsubscribes", () => {
    const { result } = renderHook(() => useRunActivityBus(), {
      wrapper: withProvider,
    });
    let calls = 0;
    const unsubscribe = result.current.subscribe(() => {
      calls += 1;
    });
    result.current.publish();
    expect(calls).toBe(1);
    unsubscribe();
    result.current.publish();
    expect(calls).toBe(1); // no further calls
  });

  it("returns a stable bus identity across renders (no subscriber churn)", () => {
    const { result, rerender } = renderHook(() => useRunActivityBus(), {
      wrapper: withProvider,
    });
    const first = result.current;
    rerender();
    expect(result.current).toBe(first);
  });

  it("falls back to an inert no-op bus with NO provider (publish/subscribe never throw)", () => {
    const { result } = renderHook(() => useRunActivityBus());
    // No provider mounted → the inert bus. Neither call throws, and publishing
    // never invokes a handler (subscribe is a no-op that returns a no-op).
    let called = false;
    const unsubscribe = result.current.subscribe(() => {
      called = true;
    });
    expect(() => result.current.publish()).not.toThrow();
    expect(called).toBe(false);
    expect(() => unsubscribe()).not.toThrow();
  });
});
