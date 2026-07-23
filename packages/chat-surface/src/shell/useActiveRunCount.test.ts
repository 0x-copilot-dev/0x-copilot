// useActiveRunCount — the rail Run-badge source (PRD-12 D1 / DoD 5).

import {
  Session,
  UnauthorizedError,
  type Transport,
  type TransportCapabilities,
} from "@0x-copilot/chat-transport";
import { act, renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  type PresenceSignal,
  type PresenceState,
} from "../presence/presence-signal";
import { PresenceSignalProvider } from "../providers/PresenceSignalProvider";
import { TransportProvider } from "../providers/TransportProvider";

import {
  RunActivityBusProvider,
  useRunActivityBus,
  type RunActivityBus,
} from "./runActivityBus";
import { useActiveRunCount } from "./useActiveRunCount";

const CAPS: TransportCapabilities = {
  substrate: "web",
  nativeSecretStorage: false,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

class FakePresence implements PresenceSignal {
  state: PresenceState = "visible";
  private readonly handlers = new Set<(s: PresenceState) => void>();
  current(): PresenceState {
    return this.state;
  }
  subscribe(handler: (s: PresenceState) => void): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }
  set(next: PresenceState): void {
    this.state = next;
    for (const handler of this.handlers) handler(next);
  }
}

function makeTransport(request: Transport["request"]): Transport {
  return {
    request,
    subscribeServerSentEvents: () => ({ close: () => {} }),
    getSession: (): Session => ({ bearer: null }),
    capabilities: () => CAPS,
  };
}

function renderCount(transport: Transport, presence: PresenceSignal) {
  return renderHook(
    () => ({ count: useActiveRunCount(), bus: useRunActivityBus() }),
    {
      wrapper: ({ children }: { children: ReactNode }) =>
        createElement(TransportProvider, {
          transport,
          children: createElement(PresenceSignalProvider, {
            signal: presence,
            children: createElement(RunActivityBusProvider, null, children),
          }),
        }),
    },
  );
}

// Flush pending promise microtasks under fake timers.
async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useActiveRunCount", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts at 0 and reflects the first successful fetch", async () => {
    const request = vi.fn().mockResolvedValue({ active_run_count: 7 });
    const { result } = renderCount(makeTransport(request), new FakePresence());
    // Initial synchronous value is 0 (badge dark until the first count lands).
    expect(result.current.count).toBe(0);
    await flush();
    expect(result.current.count).toBe(7);
  });

  it("sets the count to 0 on an UnauthorizedError (expired session goes dark, not frozen)", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce({ active_run_count: 5 })
      .mockRejectedValueOnce(new UnauthorizedError());
    const { result } = renderCount(makeTransport(request), new FakePresence());
    await flush();
    expect(result.current.count).toBe(5);

    act(() => {
      result.current.bus.publish();
    });
    await act(async () => {
      vi.advanceTimersByTime(250);
    });
    await flush();
    expect(result.current.count).toBe(0);
  });

  it("keeps the last known count on a generic transport error", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce({ active_run_count: 4 })
      .mockRejectedValueOnce(new Error("network blip"));
    const { result } = renderCount(makeTransport(request), new FakePresence());
    await flush();
    expect(result.current.count).toBe(4);

    act(() => {
      result.current.bus.publish();
    });
    await act(async () => {
      vi.advanceTimersByTime(250);
    });
    await flush();
    // A transient blip must NOT blank a real count.
    expect(result.current.count).toBe(4);
  });

  it("does not run the 30s interval while presence reports hidden", async () => {
    const presence = new FakePresence();
    presence.state = "hidden";
    const request = vi.fn().mockResolvedValue({ active_run_count: 2 });
    renderCount(makeTransport(request), presence);
    await flush();
    // Only the mount fetch; the visible-only poll never started.
    expect(request).toHaveBeenCalledTimes(1);
    act(() => {
      vi.advanceTimersByTime(120_000); // four poll windows
    });
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("revalidates exactly once after the 250ms debounce on a bus publish (coalescing bursts)", async () => {
    const request = vi.fn().mockResolvedValue({ active_run_count: 1 });
    const { result } = renderCount(makeTransport(request), new FakePresence());
    await flush();
    const baseline = request.mock.calls.length; // the mount fetch

    act(() => {
      result.current.bus.publish();
      result.current.bus.publish(); // a burst coalesces to one refetch
    });
    act(() => {
      vi.advanceTimersByTime(249);
    });
    expect(request.mock.calls.length).toBe(baseline); // not yet
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(request.mock.calls.length).toBe(baseline + 1); // exactly one
  });

  it("catches up immediately when presence goes hidden→visible", async () => {
    const presence = new FakePresence();
    const request = vi.fn().mockResolvedValue({ active_run_count: 3 });
    renderCount(makeTransport(request), presence);
    await flush();
    const baseline = request.mock.calls.length;
    act(() => {
      presence.set("hidden");
    });
    act(() => {
      presence.set("visible");
    });
    // The hidden→visible transition triggers an immediate revalidation.
    expect(request.mock.calls.length).toBe(baseline + 1);
  });
});
