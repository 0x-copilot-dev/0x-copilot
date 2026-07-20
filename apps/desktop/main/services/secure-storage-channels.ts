// Allowlisted secure-storage IPC channel names.
//
// DEPENDENCY-FREE (string literals only) so it is safe to import from the
// sandboxed preload as well as from main and the renderer — all sides must
// agree on the exact channel set, and there is only one source. Mirrors
// `capabilities/channels.ts`. Channel string values follow the codebase
// convention: camelCase keys, kebab-case wire values.

export const SECURE_STORAGE_CHANNELS = {
  /** Renderer → main: read `{ mode, keychainAvailable }`. */
  get: "secure-storage.get",
  /**
   * Renderer → main: `{ enabled: boolean }` → migrate boot secrets to/from
   * keychain encryption and persist the policy. Enabling triggers the macOS
   * keychain prompt HERE — the one user-initiated moment it belongs to.
   */
  set: "secure-storage.set",
} as const;

export type SecureStorageChannelName =
  (typeof SECURE_STORAGE_CHANNELS)[keyof typeof SECURE_STORAGE_CHANNELS];

export const SECURE_STORAGE_CHANNEL_VALUES: ReadonlySet<string> = new Set(
  Object.values(SECURE_STORAGE_CHANNELS),
);

export function isSecureStorageChannel(
  name: string,
): name is SecureStorageChannelName {
  return SECURE_STORAGE_CHANNEL_VALUES.has(name);
}
