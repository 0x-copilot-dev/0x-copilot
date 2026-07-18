// AC8 agentic browser — feature gate.
//
// The supervised browser worker, the loopback broker, and the local browser MCP
// card are ALL opt-in behind a single environment flag, read ONCE at boot.
// When unset or not explicitly truthy, `main/index.ts` never builds the
// subsystem, the worker never spawns, the broker never binds, and the local MCP
// card is absent — every browser call fails closed. Mirrors the AC5 filesystem
// gate parsing (`capabilities/feature-gate.ts`).

export const DESKTOP_BROWSER_FLAG = "RUNTIME_ENABLE_DESKTOP_BROWSER";

const TRUTHY = new Set(["1", "true", "yes", "on", "enabled"]);

/**
 * Whether the desktop agentic-browser subsystem is enabled. Reads
 * `RUNTIME_ENABLE_DESKTOP_BROWSER` off the supplied env map (injectable so it
 * is testable without mutating `process.env`). Fails closed.
 */
export function isDesktopBrowserEnabled(
  env: Record<string, string | undefined>,
): boolean {
  const raw = env[DESKTOP_BROWSER_FLAG];
  if (raw === undefined) return false;
  return TRUTHY.has(raw.trim().toLowerCase());
}
