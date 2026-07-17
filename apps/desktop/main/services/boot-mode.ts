// Small pure helpers for main/index.ts wiring, split out so they are
// unit-testable without loading the electron module graph.

export interface BootModeInputs {
  readonly isPackaged: boolean;
  readonly env: Readonly<Record<string, string | undefined>>;
}

// Supervise only when running packaged OR a staged dev runtime is named
// via COPILOT_RUNTIME_DIR. Plain `npm run dev` keeps today's behavior:
// no supervisor, COPILOT_FACADE_URL (or MockTransport) drives the app.
export function shouldSupervise(inputs: BootModeInputs): boolean {
  if (inputs.isPackaged) return true;
  const override = inputs.env.COPILOT_RUNTIME_DIR;
  return override !== undefined && override !== "";
}

export interface SingleInstanceAppLike {
  requestSingleInstanceLock(): boolean;
  quit(): void;
  on(event: "second-instance", listener: () => void): unknown;
}

// requestSingleInstanceLock gate: the second instance quits immediately;
// the first instance re-focuses its window when another launch is
// attempted. Returns true when this process holds the lock.
export function installSingleInstance(
  app: SingleInstanceAppLike,
  focusExistingWindow: () => void,
): boolean {
  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return false;
  }
  app.on("second-instance", () => {
    focusExistingWindow();
  });
  return true;
}
