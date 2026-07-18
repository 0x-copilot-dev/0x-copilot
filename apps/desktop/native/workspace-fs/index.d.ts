// Type surface for the workspace-fs native addon loader (index.cjs).

export interface OpenBeneathOptions {
  /** Open the target as a directory (`O_DIRECTORY` / `FILE_DIRECTORY_FILE`). */
  readonly directory: boolean;
  /** Open read+write instead of read-only (used by the write-path parent pin). */
  readonly write?: boolean;
}

/**
 * Handle-relative, root-confined, reparse/symlink-refusing open. Returns an OS
 * file descriptor (usable by `node:fs`) for the target named by `rel` resolved
 * strictly beneath `rootReal`. Throws an `Error` whose `.code` is a POSIX-style
 * errno name (`ELOOP` / `EXDEV` / `ENOENT` / `ENOTDIR` / `EISDIR` / `EACCES` /
 * `EPERM` / `ENOSYS` / `EIO`) on refusal. `ENOSYS` means the kernel lacks the
 * primitive (e.g. pre-5.6 Linux without openat2) — the caller should fall back.
 */
export interface NativeWorkspaceFs {
  readonly platform: NodeJS.Platform;
  openBeneath(rootReal: string, rel: string, opts: OpenBeneathOptions): number;
}

/**
 * Load the compiled addon, or return `undefined` when no binary is available
 * for this platform/ABI. Never throws.
 */
export function loadNative(): NativeWorkspaceFs | undefined;
