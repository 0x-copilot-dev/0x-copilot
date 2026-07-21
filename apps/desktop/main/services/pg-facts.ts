import { type CommandRunner } from "./exec";
import { AI_BACKEND_DB_NAME, PG_SUPERUSER } from "./service-env";

// Impure probe: does the Postgres `atlas_ai` database hold conversation history
// that a first file boot would otherwise strand? Runs a tiny read-only query
// through the staged python + psycopg (the zonky bundle ships no psql), mirroring
// postgres.ts's ENSURE_DB path: the password rides PGPASSWORD (never argv), the
// db name is a fixed constant, and the source is read-only (no writes, no DDL).
//
// A missing `agent_conversations` table (a fresh install whose AI schema has
// never been migrated) is a clean "no data" — the common case, correctly
// skipping the migration. Any real failure (unreachable server, bad output)
// THROWS so the caller's fail-safe path can serve Postgres rather than guess.

const PROBE_SCRIPT = `
import sys
import psycopg

conninfo = sys.argv[1]
with psycopg.connect(conninfo, autocommit=True) as conn:
    reg = conn.execute(
        "SELECT to_regclass('public.agent_conversations')"
    ).fetchone()
    if reg is None or reg[0] is None:
        print("EMPTY")  # fresh install: AI schema never migrated
    else:
        row = conn.execute(
            "SELECT 1 FROM agent_conversations LIMIT 1"
        ).fetchone()
        print("HASDATA" if row is not None else "EMPTY")
`;

export interface PostgresAiRowsProbeOptions {
  readonly pythonBin: string;
  /** PYTHONPATH that makes `import psycopg` resolve (a service's site-packages). */
  readonly pythonSitePackages: string;
  readonly pgPort: number;
  readonly pgPassword: string;
  readonly runner: CommandRunner;
}

export class PostgresProbeError extends Error {
  constructor(detail: string) {
    super(`postgres atlas_ai probe failed: ${detail}`);
    this.name = "PostgresProbeError";
  }
}

/**
 * True when `atlas_ai` holds at least one conversation row; false for an empty
 * or never-migrated database. Throws {@link PostgresProbeError} on any real
 * failure so the caller falls back to the Postgres store (never a wrong "empty").
 */
export async function postgresAiStoreHasRows(
  options: PostgresAiRowsProbeOptions,
): Promise<boolean> {
  // Connect to the maintenance-visible atlas_ai DB as the superuser; PGPASSWORD
  // keeps the secret out of argv (exactly as postgres.ts ensureDatabase does).
  const conninfo = `postgresql://${PG_SUPERUSER}@127.0.0.1:${options.pgPort}/${AI_BACKEND_DB_NAME}`;
  const result = await options.runner(
    options.pythonBin,
    ["-c", PROBE_SCRIPT, conninfo],
    {
      env: {
        PYTHONPATH: options.pythonSitePackages,
        PYTHONDONTWRITEBYTECODE: "1",
        PGPASSWORD: options.pgPassword,
      },
    },
  );
  if (result.code !== 0) {
    throw new PostgresProbeError(
      `exit ${String(result.code)}: ${result.stderr.trim() || result.stdout.trim()}`,
    );
  }
  const verdict = result.stdout.trim().split(/\r?\n/u).at(-1)?.trim();
  if (verdict === "HASDATA") return true;
  if (verdict === "EMPTY") return false;
  throw new PostgresProbeError(
    `unexpected probe output: ${JSON.stringify(verdict)}`,
  );
}
