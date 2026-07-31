// Feature gate for the desktop capability subsystem (AC5 / G4).
//
// The loopback capability broker plus the folder-picker / grant IPC channels are
// how the agent reaches the real host filesystem. The subsystem is ON BY
// DEFAULT: with it off, the agent's `ls` fell through to the agent-memory
// virtual filesystem, which answered a real folder with an empty listing and a
// green tick — the user was told their Downloads folder was empty. A capability
// that silently degrades into a wrong answer is worse than one that is present
// and asks.
//
// DEFAULT-ON IS NOT DEFAULT-ACCESS. This is the sentence to read twice, because
// the next reader will assume the flag is the permission. It is not:
//
//   * This flag decides whether the SUBSYSTEM BOOTS — whether the broker binds a
//     loopback port and whether the folder-grant IPC channels exist at all. It
//     is the affordance: the "Attach folder" row, the picker, the grant store.
//   * A GRANT decides what is READABLE. Every filesystem op resolves a
//     `grantId` to a folder the user chose in the native picker, and the broker
//     answers `403 grant_required` for anything else (see
//     `broker.ts#handleFs` → `#resolveGrant`, and the no-grants test in
//     feature-gate.test.ts). A freshly booted subsystem with no grants exposes
//     exactly nothing.
//
// So enabling this by default hands over no bytes; it makes the ASK possible.
// A path outside granted scope becomes a question the user answers, instead of
// an empty listing the agent believes.
//
// The OPT-OUT still wins, and still fails closed. An operator who sets the flag
// to `0` / `false` / `off` gets a build where the broker never binds and (because
// `main/index.ts` leaves `capabilityService` null) the capability IPC channels
// are never registered, so every capability call fails closed. A value we cannot
// parse is treated the same way — an unreadable setting is not consent to
// enable — but it is reported (see `reason`) rather than swallowed, because
// "quietly did the other thing" is the failure mode this whole subsystem exists
// to remove.

export const DESKTOP_FILESYSTEM_FLAG = "RUNTIME_ENABLE_DESKTOP_FILESYSTEM";

const TRUTHY = new Set(["1", "true", "yes", "on", "enabled"]);
const FALSY = new Set(["0", "false", "no", "off", "disabled"]);

/** Which of the four readings of the flag produced the decision. */
export type DesktopFilesystemGateSource =
  /** Flag absent or empty — the default (enabled) applies. */
  | "default"
  /** Operator set a recognized truthy value. */
  | "explicit-on"
  /** Operator set a recognized falsy value — the honoured opt-out. */
  | "explicit-off"
  /** Operator set something unreadable — treated as an opt-out, and reported. */
  | "unrecognized";

export interface DesktopFilesystemGate {
  readonly enabled: boolean;
  readonly source: DesktopFilesystemGateSource;
  /** One loggable line naming the decision AND why; main prints it at boot. */
  readonly reason: string;
}

/**
 * Resolve the desktop filesystem gate off the supplied environment map
 * (injectable so it is testable without mutating `process.env`).
 *
 * Prefer this over {@link isDesktopFilesystemEnabled} at the boot site: the
 * `reason` is what keeps a disabled subsystem from looking like a bug, and an
 * unrecognized value from looking like a decision.
 */
export function resolveDesktopFilesystemGate(
  env: Record<string, string | undefined>,
): DesktopFilesystemGate {
  const raw = env[DESKTOP_FILESYSTEM_FLAG];
  // Unset and empty are the same signal. `FOO=` in an env file, and a compose
  // passthrough of a variable the host never set, both arrive as "" — neither
  // carries an opt-out, so both take the default.
  if (raw === undefined || raw.trim() === "") {
    return {
      enabled: true,
      source: "default",
      reason:
        "enabled by default (no grants exist yet: nothing is readable until " +
        `the user grants a folder; set ${DESKTOP_FILESYSTEM_FLAG}=0 to opt out)`,
    };
  }
  const value = raw.trim().toLowerCase();
  if (TRUTHY.has(value)) {
    return {
      enabled: true,
      source: "explicit-on",
      reason: `enabled by ${DESKTOP_FILESYSTEM_FLAG}=${raw.trim()}`,
    };
  }
  if (FALSY.has(value)) {
    return {
      enabled: false,
      source: "explicit-off",
      reason: `disabled by ${DESKTOP_FILESYSTEM_FLAG}=${raw.trim()}`,
    };
  }
  return {
    enabled: false,
    source: "unrecognized",
    reason:
      `disabled: ${DESKTOP_FILESYSTEM_FLAG} is set to an unrecognized value ` +
      `(use 1/true/on to enable, 0/false/off to disable)`,
  };
}

/**
 * Whether the desktop filesystem capability subsystem is enabled. Enabled
 * unless the `RUNTIME_ENABLE_DESKTOP_FILESYSTEM` flag explicitly opts out (or
 * carries a value that cannot be read as either answer).
 *
 * Being enabled grants NO filesystem access on its own — see the module header.
 */
export function isDesktopFilesystemEnabled(
  env: Record<string, string | undefined>,
): boolean {
  return resolveDesktopFilesystemGate(env).enabled;
}
