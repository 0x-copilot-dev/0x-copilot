// Allowlisted first-run (FTUE) IPC channel names.
//
// DEPENDENCY-FREE (string literals only) so it is safe to import from the
// sandboxed preload as well as from main and the renderer — all sides must
// agree on the exact channel set, and there is only one source. Mirrors
// `secure-storage-channels.ts`. Channel string values follow the codebase
// convention: camelCase keys, kebab-case wire values.

export const FIRST_RUN_CHANNELS = {
  /** Renderer → main: `{ workspaceId }` → `{ completed: boolean }`. */
  get: "first-run.get",
  /**
   * Renderer → main: `{ workspaceId, completed: boolean }` → persist the
   * per-workspace first-run completion flag. Written when the user finishes
   * the onboarding gate, sends their first run, or skips to the workspace.
   */
  set: "first-run.set",
} as const;

export type FirstRunChannelName =
  (typeof FIRST_RUN_CHANNELS)[keyof typeof FIRST_RUN_CHANNELS];

export const FIRST_RUN_CHANNEL_VALUES: ReadonlySet<string> = new Set(
  Object.values(FIRST_RUN_CHANNELS),
);

export function isFirstRunChannel(name: string): name is FirstRunChannelName {
  return FIRST_RUN_CHANNEL_VALUES.has(name);
}
