import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { astAllowlistScan, type AstViolation } from "./ast-allowlist";

export type LoadResult =
  | { readonly ok: true; readonly source: string }
  | {
      readonly ok: false;
      readonly reason: "file-error";
      readonly detail: string;
    }
  | {
      readonly ok: false;
      readonly reason: "ast-violation";
      readonly violations: readonly AstViolation[];
    };

export interface LoadAdapterSourceOptions {
  readonly adapterDir: string;
  readonly scheme: string;
  readonly version: number;
}

function adapterFilename(scheme: string, version: number): string {
  return `${scheme}-v${version}.js`;
}

export function adapterFilePath(opts: LoadAdapterSourceOptions): string {
  return join(opts.adapterDir, adapterFilename(opts.scheme, opts.version));
}

export async function loadAdapterSource(
  opts: LoadAdapterSourceOptions,
): Promise<LoadResult> {
  const filePath = adapterFilePath(opts);
  let source: string;
  try {
    source = await readFile(filePath, "utf-8");
  } catch (err) {
    return {
      ok: false,
      reason: "file-error",
      detail: err instanceof Error ? err.message : String(err),
    };
  }
  const scan = astAllowlistScan(source);
  if (!scan.ok) {
    return {
      ok: false,
      reason: "ast-violation",
      violations: scan.violations,
    };
  }
  return { ok: true, source };
}
