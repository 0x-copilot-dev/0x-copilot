import { dirname, join } from "node:path";

// On-disk persistence of validated tier-2 adapter sources. Filename layout
// matches 6A's loader.adapterFilePath so a reload-from-disk via
// loadAdapterSource works without translation.

export interface InstallerFs {
  writeFile(path: string, data: string): Promise<void>;
  mkdir(path: string, opts: { recursive: true }): Promise<string | undefined>;
  unlink(path: string): Promise<void>;
}

export interface InstallerDeps {
  readonly fs: InstallerFs;
}

export interface PersistAdapterSourceOpts {
  readonly adapterDir: string;
  readonly scheme: string;
  readonly version: number;
  readonly source: string;
}

export interface UninstallAdapterFileOpts {
  readonly adapterDir: string;
  readonly scheme: string;
  readonly version: number;
}

function adapterFilename(scheme: string, version: number): string {
  return `${scheme}-v${version}.js`;
}

export function adapterFilePath(opts: {
  readonly adapterDir: string;
  readonly scheme: string;
  readonly version: number;
}): string {
  return join(opts.adapterDir, adapterFilename(opts.scheme, opts.version));
}

export async function persistAdapterSource(
  opts: PersistAdapterSourceOpts,
  deps: InstallerDeps,
): Promise<void> {
  const filePath = adapterFilePath({
    adapterDir: opts.adapterDir,
    scheme: opts.scheme,
    version: opts.version,
  });
  await deps.fs.mkdir(dirname(filePath), { recursive: true });
  await deps.fs.writeFile(filePath, opts.source);
}

function isMissingFileError(err: unknown): boolean {
  if (err === null || typeof err !== "object") return false;
  const code = (err as { code?: unknown }).code;
  return code === "ENOENT";
}

export async function uninstallAdapterFile(
  opts: UninstallAdapterFileOpts,
  deps: InstallerDeps,
): Promise<void> {
  try {
    await deps.fs.unlink(adapterFilePath(opts));
  } catch (err) {
    if (isMissingFileError(err)) return;
    throw err;
  }
}
