// Feature gate for the desktop capability subsystem (AC5 / G4).
//
// The loopback capability broker plus the folder-picker / grant IPC channels
// grant the agent host-filesystem reach. They must NOT start on every boot:
// the whole subsystem is opt-in behind a single environment flag, read ONCE at
// boot. When the flag is unset or not explicitly truthy, `main/index.ts` never
// builds the subsystem, so the broker does not bind and the capability IPC
// channels are never registered — every capability call fails closed.
//
// Fail-closed parsing: only an explicit truthy value enables the subsystem.
// Anything else — unset, empty, "0", "false", "off", or an unrecognized value
// — leaves it OFF.

export const DESKTOP_FILESYSTEM_FLAG = "RUNTIME_ENABLE_DESKTOP_FILESYSTEM";

const TRUTHY = new Set(["1", "true", "yes", "on", "enabled"]);

/**
 * Whether the desktop filesystem capability subsystem is enabled. Reads the
 * `RUNTIME_ENABLE_DESKTOP_FILESYSTEM` flag off the supplied environment map
 * (injectable so it is testable without mutating `process.env`). Fails closed.
 */
export function isDesktopFilesystemEnabled(
  env: Record<string, string | undefined>,
): boolean {
  const raw = env[DESKTOP_FILESYSTEM_FLAG];
  if (raw === undefined) return false;
  return TRUTHY.has(raw.trim().toLowerCase());
}
