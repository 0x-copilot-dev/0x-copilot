// AC8 agentic browser — supervised worker lifecycle (Electron-main owned).
//
// Playwright + Chromium run in a SEPARATE Node child (the browser worker),
// never in the renderer, preload, or the Electron main thread. This supervisor
// owns that child's lifecycle, mirroring the Python-service precedent
// (`services/python-service.ts`) and adding the two browser-specific gates:
//
//   - HEALTH: after spawn, the worker must report healthy (a broker ping) within
//     a timeout, else it is treated as a crash and restarted.
//   - VERSION PIN: the worker reports its bundled Chromium build id; a mismatch
//     against the pinned `expectedVersion` is FATAL (never run a random browser
//     binary) and surfaces `browser_unavailable`.
//
// Crashes restart with exponential backoff; too many crashes in the window is a
// terminal `FatalBrowserWorker` and the capability reports `browser_unavailable`
// (the local MCP card is withdrawn). Teardown escalates SIGTERM -> SIGKILL and
// asks the injected `killTree` to reap Chromium/crashpad/proxy descendants.
// Every OS touchpoint (spawn, timers, clock, health probe) is injected so unit
// tests drive the whole state machine with fakes.

export class FatalBrowserWorker extends Error {
  readonly crashCount: number;
  readonly windowMs: number;
  constructor(crashCount: number, windowMs: number) {
    super(
      `browser worker crashed ${crashCount} times within ${Math.round(
        windowMs / 1000,
      )}s — giving up`,
    );
    this.name = "FatalBrowserWorker";
    this.crashCount = crashCount;
    this.windowMs = windowMs;
  }
}

export interface WorkerChildStdio {
  on(event: "data", listener: (chunk: Buffer | string) => void): unknown;
}

export interface WorkerChildLike {
  readonly pid?: number | undefined;
  readonly stdout: WorkerChildStdio | null;
  readonly stderr: WorkerChildStdio | null;
  on(
    event: "exit",
    listener: (code: number | null, signal: string | null) => void,
  ): unknown;
  on(event: "error", listener: (err: Error) => void): unknown;
  kill(signal?: NodeJS.Signals): boolean;
}

export type WorkerSpawnFn = () => WorkerChildLike;

export interface WorkerHealth {
  readonly healthy: boolean;
  readonly version: string;
}

export interface SupervisorTimers {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

export type BrowserWorkerState =
  | "idle"
  | "starting"
  | "running"
  | "restarting"
  | "unavailable"
  | "stopping"
  | "stopped";

export interface BrowserWorkerSupervisorConfig {
  readonly spawn: WorkerSpawnFn;
  /** Probe the worker's broker for health + reported browser version. */
  readonly probeHealth: (child: WorkerChildLike) => Promise<WorkerHealth>;
  /** Pinned Chromium build id the worker MUST report. */
  readonly expectedVersion: string;
  readonly onStateChange?: (state: BrowserWorkerState, reason?: string) => void;
  readonly log?: (line: string) => void;
  /** Reap Chromium/crashpad/proxy descendants of `pid`. Best-effort. */
  readonly killTree?: (pid: number) => void;
  readonly timers?: SupervisorTimers;
  readonly now?: () => number;
  readonly crashLimit?: number;
  readonly crashWindowMs?: number;
  readonly backoffBaseMs?: number;
  readonly backoffCapMs?: number;
  readonly killTimeoutMs?: number;
}

const DEFAULT_CRASH_LIMIT = 5;
const DEFAULT_CRASH_WINDOW_MS = 5 * 60 * 1000;
const DEFAULT_BACKOFF_BASE_MS = 1000;
const DEFAULT_BACKOFF_CAP_MS = 30_000;

/** 1s, 2s, 4s, 8s, 16s, 30s, ... capped. */
export function computeBackoffDelayMs(
  attempt: number,
  baseMs: number = DEFAULT_BACKOFF_BASE_MS,
  capMs: number = DEFAULT_BACKOFF_CAP_MS,
): number {
  const bounded = Math.max(1, Math.floor(attempt));
  return Math.min(baseMs * 2 ** (bounded - 1), capMs);
}

const defaultTimers: SupervisorTimers = {
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (h) => clearTimeout(h as NodeJS.Timeout),
};

export class BrowserWorkerSupervisor {
  readonly #cfg: BrowserWorkerSupervisorConfig;
  readonly #timers: SupervisorTimers;
  readonly #now: () => number;
  #child: WorkerChildLike | null = null;
  #state: BrowserWorkerState = "idle";
  #stopping = false;
  #restartHandle: unknown | null = null;
  #crashTimes: number[] = [];
  #exitWaiters: Array<() => void> = [];
  #version: string | null = null;

  constructor(cfg: BrowserWorkerSupervisorConfig) {
    this.#cfg = cfg;
    this.#timers = cfg.timers ?? defaultTimers;
    this.#now = cfg.now ?? Date.now;
  }

  get state(): BrowserWorkerState {
    return this.#state;
  }

  isHealthy(): boolean {
    return this.#state === "running";
  }

  get reportedVersion(): string | null {
    return this.#version;
  }

  /**
   * Spawn the worker and gate readiness on health + version pin. Resolves once
   * the worker is running (or throws `FatalBrowserWorker` on a version mismatch
   * or immediate spawn failure). A later crash restarts asynchronously.
   */
  async start(): Promise<void> {
    if (this.#state !== "idle") {
      throw new Error(`browser supervisor.start() in state "${this.#state}"`);
    }
    await this.#spawnAndGate();
  }

  async #spawnAndGate(): Promise<void> {
    this.#setState("starting");
    const child = this.#cfg.spawn();
    this.#child = child;
    this.#wireChild(child);

    let health: WorkerHealth;
    try {
      health = await this.#cfg.probeHealth(child);
    } catch {
      this.#log("health probe failed");
      this.#onChildGone("health probe failed");
      return;
    }
    if (!health.healthy) {
      this.#onChildGone("worker reported unhealthy");
      return;
    }
    if (health.version !== this.#cfg.expectedVersion) {
      // Never run a browser binary that is not the pinned build.
      this.#version = health.version;
      this.#markUnavailable("version_mismatch");
      throw new FatalBrowserWorker(0, 0);
    }
    this.#version = health.version;
    this.#setState("running");
  }

  #wireChild(child: WorkerChildLike): void {
    let handled = false;
    child.stdout?.on("data", (c) => this.#log(c.toString().trimEnd()));
    child.stderr?.on("data", (c) => this.#log(c.toString().trimEnd()));
    child.on("exit", (code, signal) => {
      if (handled) return;
      handled = true;
      this.#onChildGone(
        `exited (code=${String(code)}, signal=${String(signal)})`,
      );
    });
    child.on("error", (err) => {
      if (handled) return;
      handled = true;
      this.#onChildGone(`spawn error: ${err.message}`);
    });
  }

  #onChildGone(reason: string): void {
    const pid = this.#child?.pid;
    this.#child = null;
    const waiters = this.#exitWaiters;
    this.#exitWaiters = [];
    for (const resolve of waiters) resolve();
    if (pid !== undefined) this.#cfg.killTree?.(pid);
    if (this.#stopping) {
      this.#setState("stopped", reason);
      return;
    }
    const now = this.#now();
    const windowMs = this.#cfg.crashWindowMs ?? DEFAULT_CRASH_WINDOW_MS;
    const limit = this.#cfg.crashLimit ?? DEFAULT_CRASH_LIMIT;
    this.#crashTimes = this.#crashTimes.filter((t) => now - t < windowMs);
    this.#crashTimes.push(now);
    if (this.#crashTimes.length >= limit) {
      this.#markUnavailable("crash_loop");
      return;
    }
    const attempt = this.#crashTimes.length;
    const delayMs = computeBackoffDelayMs(
      attempt,
      this.#cfg.backoffBaseMs,
      this.#cfg.backoffCapMs,
    );
    this.#setState("restarting", reason);
    this.#restartHandle = this.#timers.setTimeout(() => {
      this.#restartHandle = null;
      if (this.#stopping) return;
      void this.#spawnAndGate();
    }, delayMs);
  }

  #markUnavailable(reason: string): void {
    this.#state = "unavailable";
    this.#cfg.onStateChange?.("unavailable", reason);
  }

  /** Stop the worker: SIGTERM, escalate to SIGKILL, reap descendants. */
  async stop(): Promise<void> {
    this.#stopping = true;
    if (this.#restartHandle !== null) {
      this.#timers.clearTimeout(this.#restartHandle);
      this.#restartHandle = null;
    }
    const child = this.#child;
    if (child === null) {
      this.#setState("stopped");
      return;
    }
    this.#setState("stopping");
    const exited = new Promise<void>((resolve) =>
      this.#exitWaiters.push(resolve),
    );
    child.kill("SIGTERM");
    const escalate = this.#timers.setTimeout(
      () => child.kill("SIGKILL"),
      this.#cfg.killTimeoutMs ?? 5000,
    );
    await exited;
    this.#timers.clearTimeout(escalate);
  }

  #setState(state: BrowserWorkerState, reason?: string): void {
    this.#state = state;
    this.#cfg.onStateChange?.(state, reason);
  }

  #log(line: string): void {
    if (line.length > 0) this.#cfg.log?.(`[browser-worker] ${line}`);
  }
}
