// Private packaged-browser resolver.
//
// The browser executable is an Electron-main concern. The renderer, preload,
// AI service, and broker protocol never receive this path. Native staging
// writes a small manifest beside the copied Chromium revision; main validates
// the pin and canonical containment before the worker can be spawned.

import { readFileSync, realpathSync, statSync } from "node:fs";
import { isAbsolute, join, relative, resolve, sep } from "node:path";

import { z } from "zod";

import { PINNED_CHROMIUM_VERSION, PINNED_PLAYWRIGHT_VERSION } from "./protocol";

export const BROWSER_RUNTIME_MANIFEST = "browser-manifest.json";
export const BROWSER_RUNTIME_SCHEMA_VERSION = 1 as const;

const BrowserRuntimeManifestSchema = z
  .object({
    schema_version: z.literal(BROWSER_RUNTIME_SCHEMA_VERSION),
    platform: z.enum(["darwin", "win32"]),
    arch: z.enum(["arm64", "x64"]),
    playwright_version: z.string().min(1),
    chromium_revision: z.string().regex(/^\d+$/u),
    chromium_version: z.string().min(1),
    executable: z.string().min(1),
  })
  .strict();

export interface BrowserRuntimeConfig {
  readonly runtimeRoot: string;
  readonly platform?: NodeJS.Platform;
  readonly arch?: NodeJS.Architecture;
  /**
   * Main-owned development/operator override. It is never forwarded to the AI
   * service or renderer and must resolve to an existing regular file.
   */
  readonly executableOverride?: string | undefined;
}

export class BrowserRuntimeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "BrowserRuntimeError";
  }
}

export function resolveBrowserExecutablePath(
  config: BrowserRuntimeConfig,
): string {
  const override = config.executableOverride?.trim();
  if (override !== undefined && override !== "") {
    return existingRegularFile(override, "browser executable override");
  }

  const platform = config.platform ?? process.platform;
  const arch = config.arch ?? process.arch;
  if (
    (platform !== "darwin" && platform !== "win32") ||
    (arch !== "arm64" && arch !== "x64")
  ) {
    throw new BrowserRuntimeError(
      "browser runtime is unavailable for this platform",
    );
  }

  const browserRoot = join(config.runtimeRoot, "browser");
  let decoded: unknown;
  try {
    decoded = JSON.parse(
      readFileSync(join(browserRoot, BROWSER_RUNTIME_MANIFEST), "utf8"),
    );
  } catch {
    throw new BrowserRuntimeError("staged browser manifest is unavailable");
  }
  const parsed = BrowserRuntimeManifestSchema.safeParse(decoded);
  if (!parsed.success) {
    throw new BrowserRuntimeError("staged browser manifest is invalid");
  }
  const manifest = parsed.data;
  if (manifest.platform !== platform || manifest.arch !== arch) {
    throw new BrowserRuntimeError(
      "staged browser target does not match this desktop",
    );
  }
  if (
    manifest.playwright_version !== PINNED_PLAYWRIGHT_VERSION ||
    manifest.chromium_version !== PINNED_CHROMIUM_VERSION
  ) {
    throw new BrowserRuntimeError("staged browser version pin does not match");
  }

  const segments = manifest.executable.replaceAll("\\", "/").split("/");
  if (
    isAbsolute(manifest.executable) ||
    segments.some(
      (segment) => segment === "" || segment === "." || segment === "..",
    )
  ) {
    throw new BrowserRuntimeError("staged browser executable path is invalid");
  }

  let canonicalRoot: string;
  let executable: string;
  try {
    canonicalRoot = realpathSync(browserRoot);
    executable = realpathSync(resolve(browserRoot, ...segments));
  } catch {
    throw new BrowserRuntimeError("staged browser executable is unavailable");
  }
  const fromRoot = relative(canonicalRoot, executable);
  if (
    fromRoot === "" ||
    fromRoot === ".." ||
    fromRoot.startsWith(`..${sep}`) ||
    isAbsolute(fromRoot)
  ) {
    throw new BrowserRuntimeError(
      "staged browser executable escaped its runtime",
    );
  }
  return existingRegularFile(executable, "staged browser executable");
}

function existingRegularFile(file: string, label: string): string {
  try {
    const canonical = realpathSync(file);
    if (!statSync(canonical).isFile()) {
      throw new BrowserRuntimeError(`${label} is not a regular file`);
    }
    return canonical;
  } catch (err) {
    if (err instanceof BrowserRuntimeError) throw err;
    throw new BrowserRuntimeError(`${label} is unavailable`);
  }
}
