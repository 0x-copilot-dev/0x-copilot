import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import {
  NotificationCenterProvider,
  useNotificationCenter,
  useNotify,
} from "./NotificationCenterProvider";

function wrapper({ children }: { children: ReactNode }): ReactNode {
  return <NotificationCenterProvider>{children}</NotificationCenterProvider>;
}

describe("NotificationCenterProvider", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("enqueues and dismisses; notify returns an id", () => {
    const { result } = renderHook(() => useNotificationCenter(), { wrapper });

    let id = "";
    act(() => {
      id = result.current.notify({ tone: "error", title: "Boom" });
    });
    expect(id).not.toBe("");
    expect(result.current.notifications).toHaveLength(1);
    expect(result.current.notifications[0]).toMatchObject({
      tone: "error",
      title: "Boom",
    });

    act(() => result.current.dismiss(id));
    expect(result.current.notifications).toHaveLength(0);
  });

  it("auto-dismisses success/info but keeps errors sticky", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useNotificationCenter(), { wrapper });

    act(() => {
      result.current.notify({ tone: "success", title: "Saved" });
      result.current.notify({ tone: "error", title: "Failed" });
    });
    expect(result.current.notifications).toHaveLength(2);

    act(() => vi.advanceTimersByTime(4000));
    // success auto-dismissed; the error stays until dismissed/actioned.
    expect(result.current.notifications).toHaveLength(1);
    expect(result.current.notifications[0].tone).toBe("error");
  });

  it("honors an explicit durationMs and null (never auto-dismiss)", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useNotificationCenter(), { wrapper });

    act(() => {
      result.current.notify({
        tone: "error",
        title: "Sticky-ish",
        durationMs: 1000,
      });
      result.current.notify({
        tone: "info",
        title: "Forever",
        durationMs: null,
      });
    });
    act(() => vi.advanceTimersByTime(1000));
    expect(result.current.notifications).toHaveLength(1);
    expect(result.current.notifications[0].title).toBe("Forever");
  });

  it("useNotify without a provider is a safe no-op", () => {
    const { result } = renderHook(() => useNotify());
    let id = "sentinel";
    act(() => {
      id = result.current({ tone: "error", title: "no provider" });
    });
    expect(id).toBe("");
  });
});
