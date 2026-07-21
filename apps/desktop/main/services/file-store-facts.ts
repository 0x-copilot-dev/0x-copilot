import { join } from "node:path";

// Impure probe: does the file-native store root already hold conversation data?
// This is the data-safety gate for the first-file-boot migration — the carry-over
// must NEVER write into a non-empty file store. The layout is (see
// services/ai-backend/src/runtime_adapters/file/_paths.py):
//
//   <root>/workspaces/<ws-key>/sessions/<conv-key>/conversation.json
//
// A conversation exists iff some workspace's `sessions/` directory has at least
// one entry. We deliberately treat "any session entry" as data (not "a valid
// conversation.json") so the probe errs toward has-data — the SAFE direction: a
// false has-data merely strands history (recoverable), while a false empty would
// let the migration run against real data.

export interface ReaddirFs {
  /** List directory entry names; rejects with ENOENT when the dir is absent. */
  readdir(path: string): Promise<readonly string[]>;
}

function isEnoent(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    (err as { code: unknown }).code === "ENOENT"
  );
}

/** ENOENT -> [] (absent dir is empty); any other error propagates (fail-safe). */
async function readdirSafe(
  fs: ReaddirFs,
  path: string,
): Promise<readonly string[]> {
  try {
    return await fs.readdir(path);
  } catch (err) {
    if (isEnoent(err)) return [];
    throw err;
  }
}

/**
 * True when the file store root holds at least one conversation session.
 *
 * A non-ENOENT filesystem error propagates so the caller's fail-safe path can
 * serve the Postgres store rather than guessing the store is empty.
 */
export async function fileStoreHasConversations(
  root: string,
  fs: ReaddirFs,
): Promise<boolean> {
  const workspacesDir = join(root, "workspaces");
  const workspaces = await readdirSafe(fs, workspacesDir);
  for (const ws of workspaces) {
    const sessions = await readdirSafe(fs, join(workspacesDir, ws, "sessions"));
    if (sessions.length > 0) return true;
  }
  return false;
}
