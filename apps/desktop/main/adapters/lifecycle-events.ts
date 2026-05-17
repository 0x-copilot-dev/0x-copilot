import { dirname } from "node:path";

// PRD §9.5.2 / §9.5.4. Phase 6C audit log: append-only JSON Lines at
// {userData}/audit/adapter-lifecycle.log. Shares the file with 6D's
// broken-mark primitive so SIEM exports see one timeline per adapter.
// Implementation uses fs.appendFile exclusively — never writeFile,
// truncate, or unlink — so historical records cannot be rewritten.

export type LifecycleEventKind =
  | "requested"
  | "generated"
  | "validated"
  | "installed"
  | "render-error"
  | "marked-broken"
  | "regen-queued"
  | "lifecycle-exhausted";

export interface LifecycleAuditEntry {
  readonly ts: number;
  readonly kind: LifecycleEventKind;
  readonly scheme: string;
  readonly version: number;
  readonly detail?: string;
}

export interface LifecycleEventsFs {
  appendFile(path: string, data: string): Promise<void>;
  mkdir(path: string, opts: { recursive: true }): Promise<string | undefined>;
  readFile(path: string, encoding: "utf8"): Promise<string>;
}

export interface LifecycleEventsDeps {
  readonly logPath: string;
  readonly fs: LifecycleEventsFs;
}

export interface ReadLifecycleEventsOpts {
  readonly scheme?: string;
  readonly limit?: number;
  readonly kind?: LifecycleEventKind;
}

function isMissingFileError(err: unknown): boolean {
  if (err === null || typeof err !== "object") return false;
  const code = (err as { code?: unknown }).code;
  return code === "ENOENT";
}

function isLifecycleAuditEntry(value: unknown): value is LifecycleAuditEntry {
  if (value === null || typeof value !== "object") return false;
  const o = value as Record<string, unknown>;
  if (typeof o.ts !== "number") return false;
  if (typeof o.kind !== "string") return false;
  if (typeof o.scheme !== "string") return false;
  if (typeof o.version !== "number") return false;
  if (o.detail !== undefined && typeof o.detail !== "string") return false;
  return true;
}

export async function appendLifecycleEvent(
  entry: LifecycleAuditEntry,
  deps: LifecycleEventsDeps,
): Promise<void> {
  const line = `${JSON.stringify(entry)}\n`;
  await deps.fs.mkdir(dirname(deps.logPath), { recursive: true });
  await deps.fs.appendFile(deps.logPath, line);
}

export async function readLifecycleEvents(
  opts: ReadLifecycleEventsOpts,
  deps: LifecycleEventsDeps,
): Promise<readonly LifecycleAuditEntry[]> {
  let raw: string;
  try {
    raw = await deps.fs.readFile(deps.logPath, "utf8");
  } catch (err) {
    if (isMissingFileError(err)) return [];
    throw err;
  }

  const out: LifecycleAuditEntry[] = [];
  for (const lineRaw of raw.split("\n")) {
    if (lineRaw.length === 0) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(lineRaw);
    } catch {
      continue;
    }
    if (!isLifecycleAuditEntry(parsed)) continue;
    if (opts.scheme !== undefined && parsed.scheme !== opts.scheme) continue;
    if (opts.kind !== undefined && parsed.kind !== opts.kind) continue;
    out.push(parsed);
  }

  if (opts.limit !== undefined && out.length > opts.limit) {
    return out.slice(out.length - opts.limit);
  }
  return out;
}
