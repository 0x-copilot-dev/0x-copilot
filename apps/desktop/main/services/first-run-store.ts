// First-run (FTUE) completion store — records, per account, whether the
// onboarding gate has been completed (setup finished, first run sent, or
// skipped). Persisted chmod-600 as JSON under userData/settings with NO
// OS-keychain involvement: it is a UX flag, not a secret. Modeled on
// `secure-storage-policy.ts`.
//
// Keyed by an opaque `accountKey` derived in main from the VERIFIED session's
// `claims.sub` (see AuthService.accountKey) — NOT the renderer-supplied
// workspaceId, which is caller-controlled and defaults to one shared value on
// desktop. So two accounts on one install each see their own first run. A
// missing/unreadable/garbage file → "not completed", so onboarding shows — the
// safe default is to never skip onboarding on a bad read (the flag persists
// again on the next exit).

import { mkdirSync, readFileSync, writeFileSync, chmodSync } from "node:fs";
import { dirname, join } from "node:path";

const STORE_RELATIVE_PATH = ["settings", "first-run.json"] as const;
const STORE_VERSION = 1;

export interface FirstRunFsSync {
  readFileSync(path: string): Buffer;
  writeFileSync(path: string, data: string, options?: { mode?: number }): void;
  mkdirSync(path: string, options: { recursive: boolean }): unknown;
  chmodSync(path: string, mode: number): void;
}

const NODE_FS_SYNC: FirstRunFsSync = {
  readFileSync: (path) => readFileSync(path),
  writeFileSync: (path, data, options) => writeFileSync(path, data, options),
  mkdirSync: (path, options) => mkdirSync(path, options),
  chmodSync: (path, mode) => chmodSync(path, mode),
};

export function firstRunStorePath(userDataDir: string): string {
  return join(userDataDir, ...STORE_RELATIVE_PATH);
}

/** completed: accountKey → ISO timestamp of completion. */
interface FirstRunFile {
  version: number;
  completed: Record<string, string>;
}

function readFile(userDataDir: string, fs: FirstRunFsSync): FirstRunFile {
  try {
    const raw = fs.readFileSync(firstRunStorePath(userDataDir));
    const parsed: unknown = JSON.parse(raw.toString("utf-8"));
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      typeof (parsed as Record<string, unknown>).completed === "object" &&
      (parsed as Record<string, unknown>).completed !== null
    ) {
      const completed = (parsed as { completed: Record<string, unknown> })
        .completed;
      // Keep only string-valued entries — a garbage value must not read as a
      // completed account (it would silently skip onboarding).
      const clean: Record<string, string> = {};
      for (const [key, value] of Object.entries(completed)) {
        if (typeof value === "string") clean[key] = value;
      }
      return { version: STORE_VERSION, completed: clean };
    }
  } catch {
    // Missing or unreadable → nothing completed (onboarding shows).
  }
  return { version: STORE_VERSION, completed: {} };
}

export function loadFirstRunComplete(
  userDataDir: string,
  accountKey: string,
  fs: FirstRunFsSync = NODE_FS_SYNC,
): boolean {
  const file = readFile(userDataDir, fs);
  return Object.prototype.hasOwnProperty.call(file.completed, accountKey);
}

export function saveFirstRunComplete(
  userDataDir: string,
  accountKey: string,
  completed: boolean,
  fs: FirstRunFsSync = NODE_FS_SYNC,
): void {
  const file = readFile(userDataDir, fs);
  if (completed) {
    file.completed[accountKey] = new Date().toISOString();
  } else {
    delete file.completed[accountKey];
  }
  const path = firstRunStorePath(userDataDir);
  fs.mkdirSync(dirname(path), { recursive: true });
  fs.writeFileSync(
    path,
    JSON.stringify({ version: STORE_VERSION, completed: file.completed }) +
      "\n",
    { mode: 0o600 },
  );
  // writeFile mode is ignored when the file pre-exists; enforce anyway.
  fs.chmodSync(path, 0o600);
}
