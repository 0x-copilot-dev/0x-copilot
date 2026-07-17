// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import {
  computeBackoffDelayMs,
  FatalCrashLoop,
  PythonService,
  type ChildLike,
  type PythonServiceConfig,
  type ServiceTimers,
} from "./python-service";

class FakeChild implements ChildLike {
  pid = 4242;
  readonly killSignals: string[] = [];
  readonly #listeners = new Map<string, Array<(...args: never[]) => void>>();
  stdout = {
    on: (event: "data", cb: (chunk: Buffer | string) => void): void => {
      this.#push(`stdout:${event}`, cb as never);
    },
  };
  stderr = {
    on: (event: "data", cb: (chunk: Buffer | string) => void): void => {
      this.#push(`stderr:${event}`, cb as never);
    },
  };

  on(event: string, cb: (...args: never[]) => void): void {
    this.#push(event, cb);
  }

  kill(signal?: NodeJS.Signals): boolean {
    this.killSignals.push(signal ?? "SIGTERM");
    return true;
  }

  emitStdout(chunk: string): void {
    for (const cb of this.#listeners.get("stdout:data") ?? []) {
      (cb as (chunk: string) => void)(chunk);
    }
  }

  emitExit(code: number | null, signal: string | null = null): void {
    for (const cb of this.#listeners.get("exit") ?? []) {
      (cb as (code: number | null, signal: string | null) => void)(
        code,
        signal,
      );
    }
  }

  emitError(err: Error): void {
    for (const cb of this.#listeners.get("error") ?? []) {
      (cb as (err: Error) => void)(err);
    }
  }

  #push(event: string, cb: (...args: never[]) => void): void {
    const arr = this.#listeners.get(event) ?? [];
    arr.push(cb);
    this.#listeners.set(event, arr);
  }
}

interface ScheduledTimer {
  fn: () => void;
  ms: number;
  cancelled: boolean;
  fired?: boolean;
}

class FakeTimers implements ServiceTimers {
  readonly scheduled: ScheduledTimer[] = [];

  setTimeout(fn: () => void, ms: number): unknown {
    const handle: ScheduledTimer = { fn, ms, cancelled: false };
    this.scheduled.push(handle);
    return handle;
  }

  clearTimeout(handle: unknown): void {
    (handle as ScheduledTimer).cancelled = true;
  }

  /** Fire the most recently scheduled pending timer. */
  fireLast(): void {
    const pending = this.scheduled.filter(
      (t) => !t.cancelled && t.fired !== true,
    );
    const last = pending.at(-1);
    if (!last) throw new Error("no pending timer");
    last.fired = true;
    last.fn();
  }

  pendingDelays(): number[] {
    return this.scheduled
      .filter((t) => !t.cancelled && t.fired !== true)
      .map((t) => t.ms);
  }
}

interface Harness {
  service: PythonService;
  children: FakeChild[];
  timers: FakeTimers;
  logLines: string[];
  onFatal: ReturnType<typeof vi.fn>;
  restarts: Array<{ attempt: number; delayMs: number }>;
  clock: { now: number };
}

function makeHarness(config: Partial<PythonServiceConfig> = {}): Harness {
  const children: FakeChild[] = [];
  const timers = new FakeTimers();
  const logLines: string[] = [];
  const onFatal = vi.fn();
  const restarts: Array<{ attempt: number; delayMs: number }> = [];
  const clock = { now: 1_000_000 };
  const service = new PythonService({
    name: "backend",
    command: "/rt/python/bin/python3",
    args: ["-m", "uvicorn", "backend_app.desktop_app:app"],
    cwd: "/rt/services/backend",
    env: { PYTHONPATH: "src:site-packages" },
    spawnFn: () => {
      const child = new FakeChild();
      children.push(child);
      return child;
    },
    log: {
      write: (chunk) => {
        logLines.push(chunk);
      },
    },
    onFatal,
    onRestartScheduled: (info) => {
      restarts.push(info);
    },
    timers,
    now: () => clock.now,
    ...config,
  });
  return { service, children, timers, logLines, onFatal, restarts, clock };
}

describe("computeBackoffDelayMs", () => {
  it("follows the 1s,2s,4s,8s,16s,30s.. schedule with a 30s cap", () => {
    expect(computeBackoffDelayMs(1)).toBe(1000);
    expect(computeBackoffDelayMs(2)).toBe(2000);
    expect(computeBackoffDelayMs(3)).toBe(4000);
    expect(computeBackoffDelayMs(4)).toBe(8000);
    expect(computeBackoffDelayMs(5)).toBe(16_000);
    expect(computeBackoffDelayMs(6)).toBe(30_000);
    expect(computeBackoffDelayMs(7)).toBe(30_000);
    expect(computeBackoffDelayMs(100)).toBe(30_000);
  });
});

describe("PythonService", () => {
  it("spawns the uvicorn child once on start and pipes output to the log", () => {
    const h = makeHarness();
    h.service.start();
    h.service.start(); // idempotent while running
    expect(h.children).toHaveLength(1);
    h.children[0]!.emitStdout("INFO: started\n");
    expect(h.logLines.join("")).toContain("INFO: started");
    expect(h.service.isRunning()).toBe(true);
  });

  it("restarts after a crash with escalating backoff delays", () => {
    const h = makeHarness();
    h.service.start();

    h.children[0]!.emitExit(1);
    expect(h.restarts).toEqual([{ attempt: 1, delayMs: 1000 }]);
    h.timers.fireLast();
    expect(h.children).toHaveLength(2);

    h.children[1]!.emitExit(1);
    expect(h.restarts.at(-1)).toEqual({ attempt: 2, delayMs: 2000 });
    h.timers.fireLast();

    h.children[2]!.emitExit(1);
    expect(h.restarts.at(-1)).toEqual({ attempt: 3, delayMs: 4000 });
    h.timers.fireLast();

    h.children[3]!.emitExit(1);
    expect(h.restarts.at(-1)).toEqual({ attempt: 4, delayMs: 8000 });
    expect(h.onFatal).not.toHaveBeenCalled();
  });

  it("declares FatalCrashLoop on the 5th crash inside the 5-minute window", () => {
    const h = makeHarness();
    h.service.start();
    for (let i = 0; i < 4; i += 1) {
      h.children.at(-1)!.emitExit(1);
      h.clock.now += 1000;
      h.timers.fireLast();
    }
    expect(h.onFatal).not.toHaveBeenCalled();
    // 5th crash within the window -> fatal, no further restart scheduled.
    h.children.at(-1)!.emitExit(1);
    expect(h.onFatal).toHaveBeenCalledTimes(1);
    const err = h.onFatal.mock.calls[0]![0] as FatalCrashLoop;
    expect(err).toBeInstanceOf(FatalCrashLoop);
    expect(err.service).toBe("backend");
    expect(err.crashCount).toBe(5);
    expect(h.restarts).toHaveLength(4);
    expect(h.children).toHaveLength(5);
  });

  it("does NOT go fatal when crashes are spread beyond the 5-minute window", () => {
    const h = makeHarness();
    h.service.start();
    for (let i = 0; i < 8; i += 1) {
      h.children.at(-1)!.emitExit(1);
      // Six minutes between crashes -> the window keeps pruning.
      h.clock.now += 6 * 60 * 1000;
      h.timers.fireLast();
    }
    expect(h.onFatal).not.toHaveBeenCalled();
    // Window pruning also resets the backoff to the base delay.
    expect(h.restarts.at(-1)!.delayMs).toBe(1000);
  });

  it("treats a spawn error like a crash", () => {
    const h = makeHarness();
    h.service.start();
    h.children[0]!.emitError(new Error("ENOENT: python3 not found"));
    expect(h.restarts).toEqual([{ attempt: 1, delayMs: 1000 }]);
    expect(h.logLines.join("")).toContain("ENOENT: python3 not found");
  });

  it("stop() SIGTERMs the child, resolves on exit, and never restarts", async () => {
    const h = makeHarness();
    h.service.start();
    const child = h.children[0]!;
    const stopPromise = h.service.stop();
    expect(child.killSignals).toEqual(["SIGTERM"]);
    child.emitExit(0, "SIGTERM");
    await stopPromise;
    expect(h.service.isRunning()).toBe(false);
    // The pending SIGKILL escalation timer was cancelled and no restart
    // was scheduled.
    expect(h.timers.pendingDelays()).toEqual([]);
    expect(h.restarts).toHaveLength(0);
  });

  it("stop() escalates to SIGKILL when the child ignores SIGTERM", async () => {
    const h = makeHarness({ killTimeoutMs: 500 });
    h.service.start();
    const child = h.children[0]!;
    const stopPromise = h.service.stop();
    h.timers.fireLast(); // the escalation timer
    expect(child.killSignals).toEqual(["SIGTERM", "SIGKILL"]);
    child.emitExit(null, "SIGKILL");
    await stopPromise;
  });

  it("stop() cancels a pending restart from an earlier crash", async () => {
    const h = makeHarness();
    h.service.start();
    h.children[0]!.emitExit(1); // restart scheduled at +1s
    await h.service.stop(); // no live child; must cancel the timer
    expect(h.timers.pendingDelays()).toEqual([]);
    expect(h.children).toHaveLength(1); // restart never fired
  });

  it("start() is a no-op after a fatal crash loop", () => {
    const h = makeHarness({ crashLimit: 1 });
    h.service.start();
    h.children[0]!.emitExit(1);
    expect(h.onFatal).toHaveBeenCalledTimes(1);
    h.service.start();
    expect(h.children).toHaveLength(1);
  });
});
