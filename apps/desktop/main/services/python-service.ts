// Spawns and babysits ONE uvicorn child:
//   <python> -m uvicorn <module>:app --host 127.0.0.1 --port <p>
// with cwd at the staged service dir. Crashes restart with exponential
// backoff (1s, 2s, 4s, ... capped at 30s); >= 5 crashes inside a 5-minute
// window is a FatalCrashLoop surfaced to the supervisor. stdout/stderr
// stream into a rotating log sink. Every OS touchpoint (spawn, timers,
// clock) is injected so tests drive the whole lifecycle with fakes.

export class FatalCrashLoop extends Error {
  readonly service: string;
  readonly crashCount: number;
  readonly windowMs: number;

  constructor(service: string, crashCount: number, windowMs: number) {
    super(
      `${service} crashed ${crashCount} times within ${Math.round(
        windowMs / 1000,
      )}s — giving up`,
    );
    this.name = "FatalCrashLoop";
    this.service = service;
    this.crashCount = crashCount;
    this.windowMs = windowMs;
  }
}

export interface ChildStdio {
  on(event: "data", listener: (chunk: Buffer | string) => void): unknown;
}

export interface ChildLike {
  readonly pid?: number | undefined;
  readonly stdout: ChildStdio | null;
  readonly stderr: ChildStdio | null;
  on(
    event: "exit",
    listener: (code: number | null, signal: string | null) => void,
  ): unknown;
  on(event: "error", listener: (err: Error) => void): unknown;
  kill(signal?: NodeJS.Signals): boolean;
}

export type SpawnFn = (
  command: string,
  args: readonly string[],
  options: {
    cwd: string;
    env: Record<string, string>;
    stdio: ["ignore", "pipe", "pipe"];
  },
) => ChildLike;

export interface LogSink {
  write(chunk: string): void;
}

export interface ServiceTimers {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

export interface PythonServiceConfig {
  readonly name: string;
  readonly command: string;
  readonly args: readonly string[];
  readonly cwd: string;
  readonly env: Record<string, string>;
  readonly spawnFn: SpawnFn;
  readonly log: LogSink;
  /** Crash-loop terminal notification (supervisor -> fatal boot screen). */
  readonly onFatal: (err: FatalCrashLoop) => void;
  readonly onRestartScheduled?: (info: {
    attempt: number;
    delayMs: number;
  }) => void;
  readonly timers?: ServiceTimers;
  readonly now?: () => number;
  readonly crashLimit?: number;
  readonly crashWindowMs?: number;
  readonly backoffBaseMs?: number;
  readonly backoffCapMs?: number;
  /** SIGTERM -> SIGKILL escalation delay on stop(). */
  readonly killTimeoutMs?: number;
}

export const DEFAULT_CRASH_LIMIT = 5;
export const DEFAULT_CRASH_WINDOW_MS = 5 * 60 * 1000;
export const DEFAULT_BACKOFF_BASE_MS = 1000;
export const DEFAULT_BACKOFF_CAP_MS = 30_000;

/** 1s, 2s, 4s, 8s, 16s, 30s, 30s, ... for attempt = 1, 2, 3, ... */
export function computeBackoffDelayMs(
  attempt: number,
  baseMs: number = DEFAULT_BACKOFF_BASE_MS,
  capMs: number = DEFAULT_BACKOFF_CAP_MS,
): number {
  const bounded = Math.max(1, Math.floor(attempt));
  return Math.min(baseMs * 2 ** (bounded - 1), capMs);
}

const defaultTimers: ServiceTimers = {
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (handle) => {
    clearTimeout(handle as NodeJS.Timeout);
  },
};

export class PythonService {
  readonly #config: PythonServiceConfig;
  readonly #timers: ServiceTimers;
  readonly #now: () => number;
  #child: ChildLike | null = null;
  #stopping = false;
  #fatal = false;
  #restartHandle: unknown | null = null;
  #crashTimes: number[] = [];
  #exitWaiters: Array<() => void> = [];

  constructor(config: PythonServiceConfig) {
    this.#config = config;
    this.#timers = config.timers ?? defaultTimers;
    this.#now = config.now ?? Date.now;
  }

  start(): void {
    if (this.#child !== null || this.#stopping || this.#fatal) return;
    this.#spawnChild();
  }

  isRunning(): boolean {
    return this.#child !== null;
  }

  async stop(): Promise<void> {
    this.#stopping = true;
    if (this.#restartHandle !== null) {
      this.#timers.clearTimeout(this.#restartHandle);
      this.#restartHandle = null;
    }
    const child = this.#child;
    if (child === null) return;
    const exited = new Promise<void>((resolve) => {
      this.#exitWaiters.push(resolve);
    });
    child.kill("SIGTERM");
    const killTimeoutMs = this.#config.killTimeoutMs ?? 5000;
    const escalate = this.#timers.setTimeout(() => {
      child.kill("SIGKILL");
    }, killTimeoutMs);
    await exited;
    this.#timers.clearTimeout(escalate);
  }

  #spawnChild(): void {
    const { name, command, args, cwd, env, spawnFn, log } = this.#config;
    log.write(`[supervisor] starting ${name}: ${command} ${args.join(" ")}\n`);
    const child = spawnFn(command, args, {
      cwd,
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    this.#child = child;
    let exitHandled = false;
    child.stdout?.on("data", (chunk) => {
      log.write(chunk.toString());
    });
    child.stderr?.on("data", (chunk) => {
      log.write(chunk.toString());
    });
    child.on("exit", (code, signal) => {
      if (exitHandled) return;
      exitHandled = true;
      this.#onChildGone(
        `exited (code=${String(code)}, signal=${String(signal)})`,
      );
    });
    child.on("error", (err) => {
      // Spawn failure (e.g. missing python binary): "exit" may never fire.
      if (exitHandled) return;
      exitHandled = true;
      this.#onChildGone(`spawn error: ${err.message}`);
    });
  }

  #onChildGone(reason: string): void {
    const { name, log } = this.#config;
    this.#child = null;
    const waiters = this.#exitWaiters;
    this.#exitWaiters = [];
    for (const resolve of waiters) resolve();
    if (this.#stopping) {
      log.write(`[supervisor] ${name} stopped: ${reason}\n`);
      return;
    }
    log.write(`[supervisor] ${name} ${reason}\n`);
    const now = this.#now();
    const windowMs = this.#config.crashWindowMs ?? DEFAULT_CRASH_WINDOW_MS;
    const limit = this.#config.crashLimit ?? DEFAULT_CRASH_LIMIT;
    this.#crashTimes = this.#crashTimes.filter((t) => now - t < windowMs);
    this.#crashTimes.push(now);
    if (this.#crashTimes.length >= limit) {
      this.#fatal = true;
      const err = new FatalCrashLoop(name, this.#crashTimes.length, windowMs);
      log.write(`[supervisor] ${err.message}\n`);
      this.#config.onFatal(err);
      return;
    }
    const attempt = this.#crashTimes.length;
    const delayMs = computeBackoffDelayMs(
      attempt,
      this.#config.backoffBaseMs,
      this.#config.backoffCapMs,
    );
    log.write(`[supervisor] restarting ${name} in ${delayMs}ms\n`);
    this.#config.onRestartScheduled?.({ attempt, delayMs });
    this.#restartHandle = this.#timers.setTimeout(() => {
      this.#restartHandle = null;
      if (this.#stopping || this.#fatal) return;
      this.#spawnChild();
    }, delayMs);
  }
}
