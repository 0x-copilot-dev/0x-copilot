// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import {
  BrowserWorkerSupervisor,
  computeBackoffDelayMs,
  FatalBrowserWorker,
  type WorkerChildLike,
  type WorkerHealth,
} from "./browser-supervisor";

type ExitListener = (code: number | null, signal: string | null) => void;

class FakeChild implements WorkerChildLike {
  readonly pid = 4242;
  readonly stdout = null;
  readonly stderr = null;
  readonly kills: string[] = [];
  #exit: ExitListener | null = null;
  on(event: string, listener: (...args: never[]) => void): this {
    if (event === "exit") {
      this.#exit = listener as unknown as ExitListener;
    }
    return this;
  }
  kill(signal?: NodeJS.Signals): boolean {
    this.kills.push(signal ?? "SIGTERM");
    return true;
  }
  emitExit(): void {
    this.#exit?.(1, null);
  }
}

interface Scheduled {
  fn: () => void;
  ms: number;
}

function fakeTimers() {
  const pending: Scheduled[] = [];
  return {
    timers: {
      setTimeout: (fn: () => void, ms: number) => {
        const entry = { fn, ms };
        pending.push(entry);
        return entry;
      },
      clearTimeout: (handle: unknown) => {
        const idx = pending.indexOf(handle as Scheduled);
        if (idx >= 0) pending.splice(idx, 1);
      },
    },
    runAll(): void {
      const batch = pending.splice(0, pending.length);
      for (const e of batch) e.fn();
    },
  };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe("BrowserWorkerSupervisor", () => {
  it("spawns and reaches running when healthy with the pinned version", async () => {
    const child = new FakeChild();
    const sup = new BrowserWorkerSupervisor({
      spawn: () => child,
      probeHealth: async (): Promise<WorkerHealth> => ({
        healthy: true,
        version: "chromium-9",
      }),
      expectedVersion: "chromium-9",
    });
    await sup.start();
    expect(sup.state).toBe("running");
    expect(sup.isHealthy()).toBe(true);
    expect(sup.reportedVersion).toBe("chromium-9");
  });

  it("treats a version mismatch as fatal and unavailable (never runs unpinned)", async () => {
    const sup = new BrowserWorkerSupervisor({
      spawn: () => new FakeChild(),
      probeHealth: async () => ({ healthy: true, version: "chromium-EVIL" }),
      expectedVersion: "chromium-9",
    });
    await expect(sup.start()).rejects.toBeInstanceOf(FatalBrowserWorker);
    expect(sup.state).toBe("unavailable");
  });

  it("restarts with backoff after an unhealthy probe, then recovers", async () => {
    const timers = fakeTimers();
    let attempt = 0;
    const sup = new BrowserWorkerSupervisor({
      spawn: () => new FakeChild(),
      probeHealth: async () => {
        attempt += 1;
        return attempt === 1
          ? { healthy: false, version: "chromium-9" }
          : { healthy: true, version: "chromium-9" };
      },
      expectedVersion: "chromium-9",
      timers: timers.timers,
      now: () => 0,
    });
    await sup.start();
    await flush();
    expect(sup.state).toBe("restarting");
    timers.runAll();
    await flush();
    expect(sup.state).toBe("running");
    expect(attempt).toBe(2);
  });

  it("goes unavailable after the crash-loop threshold", async () => {
    const timers = fakeTimers();
    const sup = new BrowserWorkerSupervisor({
      spawn: () => new FakeChild(),
      probeHealth: async () => ({ healthy: false, version: "chromium-9" }),
      expectedVersion: "chromium-9",
      timers: timers.timers,
      now: () => 0,
      crashLimit: 3,
    });
    await sup.start();
    for (let i = 0; i < 5; i += 1) {
      await flush();
      timers.runAll();
    }
    await flush();
    expect(sup.state).toBe("unavailable");
  });

  it("escalates SIGTERM to SIGKILL and reaps the process tree on stop", async () => {
    const timers = fakeTimers();
    const child = new FakeChild();
    const killTree = vi.fn();
    const sup = new BrowserWorkerSupervisor({
      spawn: () => child,
      probeHealth: async () => ({ healthy: true, version: "chromium-9" }),
      expectedVersion: "chromium-9",
      timers: timers.timers,
      killTree,
    });
    await sup.start();
    const stopP = sup.stop();
    expect(child.kills).toContain("SIGTERM");
    timers.runAll(); // fire the SIGKILL escalation
    child.emitExit(); // child finally exits
    await stopP;
    expect(child.kills).toContain("SIGKILL");
    expect(killTree).toHaveBeenCalledWith(4242);
    expect(sup.state).toBe("stopped");
  });
});

describe("computeBackoffDelayMs", () => {
  it("doubles and caps", () => {
    expect(computeBackoffDelayMs(1)).toBe(1000);
    expect(computeBackoffDelayMs(2)).toBe(2000);
    expect(computeBackoffDelayMs(3)).toBe(4000);
    expect(computeBackoffDelayMs(20)).toBe(30_000);
  });
});
