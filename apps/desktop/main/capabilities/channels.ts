// Allowlisted capability IPC channel names (AC5 slice 1).
//
// This module is DEPENDENCY-FREE (string literals only) so it is safe to
// import from the sandboxed preload as well as from main — both sides must
// agree on the exact channel set, and there is only one source. It mirrors
// the role `@0x-copilot/chat-transport`'s CHANNELS plays for the transport /
// auth channels; those live in that shared package, but this app-local
// capability surface is owned by the desktop app and stays here.
//
// Channel string values follow the codebase convention: camelCase keys,
// kebab-case wire values (matching `transport.session-snapshot`,
// `auth.sign-in-google`, etc.). Never hardcode the string values elsewhere —
// import `CAPABILITY_CHANNELS`.

export const CAPABILITY_CHANNELS = {
  /** Renderer → main: open the native folder picker and mint a grant. */
  requestFolderGrant: "capability.request-folder-grant",
  /** Renderer → main: list grants (renderer-safe view — no host paths). */
  listGrants: "capability.list-grants",
  /** Renderer → main: revoke a grant by id. */
  revokeGrant: "capability.revoke-grant",
  /**
   * Renderer → main: turn per-workspace SHELL EXECUTION on/off for one grant
   * (PRD-shell-execution §7.3). The FIFTH capability channel, and the only one
   * that mutates an existing grant — before it, `GrantStore` had `create` and
   * `revoke` and nothing else, so the renderer had no way to reach a change of
   * this kind at all.
   *
   * It carries a decision, never an execution. Nothing on this channel runs a
   * command, and the broker gains no execute verb; main records what the user
   * chose and the runtime reads it back off the active-grant snapshot.
   */
  setGrantShellEnabled: "capability.set-grant-shell-enabled",
  /** Renderer → main: decide one digest-pinned workspace stage revision. */
  decideWorkspaceApproval: "capability.decide-workspace-approval",
} as const;

export type CapabilityChannelName =
  (typeof CAPABILITY_CHANNELS)[keyof typeof CAPABILITY_CHANNELS];

export const CAPABILITY_CHANNEL_VALUES: ReadonlySet<string> = new Set(
  Object.values(CAPABILITY_CHANNELS),
);

export function isCapabilityChannel(
  name: string,
): name is CapabilityChannelName {
  return CAPABILITY_CHANNEL_VALUES.has(name);
}
