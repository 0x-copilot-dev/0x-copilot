import type { UpdateStatusPayload } from "@0x-copilot/chat-transport";

// electron-updater wrapper. Design constraints (see apps/desktop task spec):
//   * Only active in a packaged, signed build with update metadata present.
//     Unsigned/dev builds NO-OP: no network, no crashes.
//   * Download in the background (autoDownload) but INSTALL ONLY ON QUIT
//     (autoInstallOnAppQuit) — never mid-run, so schema migrations never run
//     under the previous version.
//   * Check on ready + every 4h.
//   * Surface lifecycle to the renderer via CHANNELS.updateStatus.
// All electron/electron-updater touchpoints are injected so the unit test
// drives the whole flow with a fake autoUpdater.

/** Minimal surface of electron-updater's `autoUpdater` that we depend on. */
export interface AutoUpdaterLike {
  autoDownload: boolean;
  autoInstallOnAppQuit: boolean;
  on(event: "checking-for-update", listener: () => void): unknown;
  on(
    event: "update-available",
    listener: (info: UpdateInfoLike) => void,
  ): unknown;
  on(
    event: "update-not-available",
    listener: (info: UpdateInfoLike) => void,
  ): unknown;
  on(
    event: "update-downloaded",
    listener: (info: UpdateInfoLike) => void,
  ): unknown;
  on(event: "error", listener: (err: Error) => void): unknown;
  checkForUpdates(): Promise<unknown>;
}

export interface UpdateInfoLike {
  readonly version?: string;
}

export interface IntervalHandle {
  unref?(): void;
}

export interface AutoUpdateDeps {
  readonly autoUpdater: AutoUpdaterLike;
  /** app.isPackaged. */
  readonly isPackaged: boolean;
  /**
   * Whether electron-builder wrote update metadata (app-update.yml) into the
   * bundle. Absent on a bare `--dir`/dev build → the updater no-ops.
   */
  readonly hasUpdateConfig: boolean;
  /** Push an UpdateStatusPayload to the renderer (CHANNELS.updateStatus). */
  readonly emit: (status: UpdateStatusPayload) => void;
  readonly log?: (message: string) => void;
  readonly intervalMs?: number;
  readonly setInterval?: (fn: () => void, ms: number) => IntervalHandle;
  readonly clearInterval?: (handle: IntervalHandle) => void;
}

export interface AutoUpdateHandle {
  /** Run a single check now (also called on the 4h interval). */
  checkNow(): Promise<void>;
  /** Stop the periodic check. */
  stop(): void;
}

const FOUR_HOURS_MS = 4 * 60 * 60 * 1000;

const NOOP_HANDLE: AutoUpdateHandle = {
  checkNow: () => Promise.resolve(),
  stop: () => {},
};

export function initAutoUpdate(deps: AutoUpdateDeps): AutoUpdateHandle {
  const log = deps.log ?? (() => {});

  // No-op on anything that is not a packaged build carrying update metadata.
  // This is the unsigned/dev guard: never touch the network, never throw.
  if (!deps.isPackaged || !deps.hasUpdateConfig) {
    log(
      `auto-update disabled (isPackaged=${deps.isPackaged}, hasUpdateConfig=${deps.hasUpdateConfig})`,
    );
    return NOOP_HANDLE;
  }

  const { autoUpdater, emit } = deps;
  // Download in the background; install strictly on quit so a running app is
  // never swapped underneath itself (protects in-flight schema migrations).
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;

  autoUpdater.on("checking-for-update", () => {
    emit({ kind: "checking" });
  });
  autoUpdater.on("update-available", (info) => {
    log(`update available: ${info.version ?? "?"}`);
    emit({ kind: "available", version: info.version });
  });
  autoUpdater.on("update-not-available", () => {
    emit({ kind: "not-available" });
  });
  autoUpdater.on("update-downloaded", (info) => {
    log(`update downloaded: ${info.version ?? "?"} (installs on quit)`);
    emit({ kind: "downloaded", version: info.version });
  });
  autoUpdater.on("error", (err) => {
    // Signature/metadata/network failures land here. Surface, never crash.
    log(`auto-update error: ${err.message}`);
    emit({ kind: "error", message: err.message });
  });

  const checkNow = async (): Promise<void> => {
    try {
      await autoUpdater.checkForUpdates();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      log(`checkForUpdates failed: ${message}`);
      emit({ kind: "error", message });
    }
  };

  const scheduleFn = deps.setInterval ?? defaultSetInterval;
  const clearFn = deps.clearInterval ?? defaultClearInterval;
  const handle = scheduleFn(() => {
    void checkNow();
  }, deps.intervalMs ?? FOUR_HOURS_MS);
  // Do not keep the event loop alive on this timer alone.
  handle.unref?.();

  // Kick off the first check on the next tick so wiring finishes first.
  void checkNow();

  return {
    checkNow,
    stop: () => {
      clearFn(handle);
    },
  };
}

function defaultSetInterval(fn: () => void, ms: number): IntervalHandle {
  return setInterval(fn, ms);
}

function defaultClearInterval(handle: IntervalHandle): void {
  clearInterval(handle as ReturnType<typeof setInterval>);
}
