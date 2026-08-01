// The install's own principal, remembered across launches.
//
// WHY IT EXISTS. `enforce` is E2-cohort-gated, `RolloutCohortRule` requires an
// EXACT org/user selector, and the cohort subject is built only from a run
// record's verified identity. The desktop supervisor resolves service env at
// process start — before sign-in — so it could never name the principal, and
// the enforced workspace lane denied every run. The tombstone it returned
// refuses READS as well as writes, so turning enforce on made an attached
// folder unreadable.
//
// ADOPT, NEVER MINT. Records are keyed by org/user. An install that already has
// conversations under a principal would have its entire history orphaned by
// fresh ids — the app would look wiped. So this only ever reports a principal
// the install ALREADY has; it never invents one. A fresh install therefore has
// no principal on its first boot, which is honest: the lane degrades to
// read-only and logs `rollout_admission_denied+degraded_to_read_only` rather
// than half-enabling itself behind a rule that matches nobody.
//
// WHY userData AND NOT `~/.0xcopilot`. The ids are only meaningful if they match
// what lands on run records, and those live under `<userData>/agent-data`.
// `~/.0xcopilot` is the INSTALL root, shared across profiles — the journeys
// already isolate userData while sharing it — so an id cached there would name
// a user absent from this profile's store, and admission would fail silently.
// The cache must share a lifetime with the store it describes.

import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";

/** The single local principal this install's records are keyed by. */
export interface LocalPrincipal {
  readonly orgId: string;
  readonly userId: string;
}

/** Where the resolved principal is cached, beside the store it describes. */
const CACHE_FILE = "local-principal.json";

/** The file store's state directory, where run/approval records accumulate. */
const STORE_STATE_DIR = join("agent-data", "v1");

/**
 * `RolloutCohortRule` validates its selectors against this shape. An id that
 * fails it produces a rule the runtime rejects at startup, which would take the
 * whole service down rather than merely disabling a lane — so a malformed
 * cached value is treated as absent.
 */
const IDENTIFIER = /^[A-Za-z0-9_.:-]{1,128}$/;

function valid(
  principal: Partial<LocalPrincipal> | null,
): principal is LocalPrincipal {
  return (
    principal !== null &&
    typeof principal.orgId === "string" &&
    typeof principal.userId === "string" &&
    IDENTIFIER.test(principal.orgId) &&
    IDENTIFIER.test(principal.userId)
  );
}

function readCache(userDataDir: string): LocalPrincipal | undefined {
  try {
    const parsed = JSON.parse(
      readFileSync(join(userDataDir, CACHE_FILE), "utf8"),
    );
    return valid(parsed)
      ? { orgId: parsed.orgId, userId: parsed.userId }
      : undefined;
  } catch {
    // Absent or unreadable is the ordinary first-boot state, not an error.
    return undefined;
  }
}

function writeCache(userDataDir: string, principal: LocalPrincipal): void {
  try {
    mkdirSync(userDataDir, { recursive: true });
    writeFileSync(
      join(userDataDir, CACHE_FILE),
      `${JSON.stringify(principal, null, 2)}\n`,
      { encoding: "utf8", mode: 0o600 },
    );
  } catch {
    // A cache that cannot be written costs a rescan next boot, never a boot.
  }
}

/** Every `*.jsonl` under a directory tree, bounded so a large store cannot stall boot. */
function jsonlFiles(root: string, budget: number): string[] {
  const found: string[] = [];
  const pending = [root];
  while (pending.length > 0 && found.length < budget) {
    const dir = pending.pop() as string;
    let names: string[];
    try {
      names = readdirSync(dir);
    } catch {
      continue;
    }
    for (const name of names) {
      const full = join(dir, name);
      try {
        if (statSync(full).isDirectory()) pending.push(full);
        else if (name.endsWith(".jsonl")) found.push(full);
      } catch {
        // A file that vanished mid-scan is simply skipped.
      }
    }
  }
  return found;
}

/**
 * The principal this install's existing records are keyed by, if any.
 *
 * Reads the FILE store, which is the desktop default (`RUNTIME_STORE_BACKEND=file`).
 * An install still on Postgres yields nothing here and keeps the honest
 * read-only degradation until it migrates — better than guessing an id and
 * having every admission silently miss.
 */
function adoptFromStore(userDataDir: string): LocalPrincipal | undefined {
  const root = join(userDataDir, STORE_STATE_DIR);
  if (!existsSync(root)) return undefined;
  for (const file of jsonlFiles(root, 256)) {
    let lines: string[];
    try {
      lines = readFileSync(file, "utf8").split("\n");
    } catch {
      continue;
    }
    for (const line of lines) {
      if (line.trim() === "") continue;
      try {
        const row = JSON.parse(line);
        const record = row?.record ?? row;
        const candidate = { orgId: record?.org_id, userId: record?.user_id };
        if (valid(candidate)) return candidate;
      } catch {
        // One malformed line must not abandon the scan.
      }
    }
  }
  return undefined;
}

/**
 * Resolve this install's principal for the E2 cohort rule, or `undefined`.
 *
 * Cache first, then the store; a store hit is cached so later boots cost one
 * small read instead of a tree walk. Never throws and never mints — the caller
 * treats `undefined` as "no cohort policy this boot", which degrades the
 * workspace lane to read-only with a logged reason.
 */
export function resolveLocalPrincipal(
  userDataDir: string,
): LocalPrincipal | undefined {
  const cached = readCache(userDataDir);
  if (cached !== undefined) return cached;
  const adopted = adoptFromStore(userDataDir);
  if (adopted !== undefined) writeCache(userDataDir, adopted);
  return adopted;
}
