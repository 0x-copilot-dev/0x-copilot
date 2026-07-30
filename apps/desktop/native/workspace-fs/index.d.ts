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
  /**
   * `false` on the fail-closed stand-in `loadNative()` returns when a production
   * install has no compiled binary on a platform that requires one. Every method
   * then throws `EPERM`, so the caller denies instead of falling back.
   */
  readonly available?: boolean;
  openBeneath(rootReal: string, rel: string, opts: OpenBeneathOptions): number;
}

/**
 * Test seams. Production passes nothing and reads `process`/`__dirname`.
 */
export interface LoadNativeOverrides {
  readonly platform?: string;
  readonly arch?: string;
  readonly env?: Readonly<Record<string, string | undefined>>;
  readonly isPackaged?: boolean;
  readonly resourcesPath?: string;
  readonly dir?: string;
  readonly require?: (id: string) => unknown;
  readonly log?: (message: string) => void;
}

/**
 * Load the compiled addon. NEVER throws — `host-fs.ts` catches around the
 * require, so a throw would be swallowed and would land back on the silent
 * fallback. The three outcomes are therefore all return values:
 *
 *   - a working addon (`available: true`);
 *   - `undefined`, meaning "no binary, and running without one is acceptable
 *     here" — darwin always, or a development posture anywhere;
 *   - a FAIL-CLOSED stand-in (`available: false`) whose `openBeneath` throws
 *     `EPERM`, when a production install lacks the binary on a platform whose
 *     confined read has no other atomic primitive. See ./README.md.
 */
export function loadNative(
  overrides?: LoadNativeOverrides,
): NativeWorkspaceFs | undefined;
