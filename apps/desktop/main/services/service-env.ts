import { delimiter, join } from "node:path";

import type { BootSecrets } from "./boot-secrets";
import type { SupervisedServiceName } from "./runtime-paths";

// ONE passthrough allowlist for all three children. Anything not named
// here is stripped from the child environment (children get a curated env,
// never the raw desktop process env). PATH/HOME/etc. are baseline process
// plumbing; the Google OAuth vars are the contract's named passthroughs;
// the provider keys are dev conveniences that BYOK supersedes in-product.
export const ENV_PASSTHROUGH_ALLOWLIST: readonly string[] = [
  // Baseline process plumbing.
  "PATH",
  "HOME",
  "USERPROFILE",
  "SYSTEMROOT",
  "TEMP",
  "TMP",
  "TMPDIR",
  "LANG",
  "LC_ALL",
  // Google sign-in. The distributed app ships a bundled-default "Desktop app"
  // OAuth client (id + secret) — see google-oauth-default.ts, which seeds these
  // two vars into process.env at boot from a gitignored google-oauth.json when
  // the operator has not set them (env override always wins). We forward both
  // faithfully so the backend's build_google_provider can pick its auth_method
  // ("none" with PKCE for a pure Desktop client, "client_secret_post" when a
  // secret is present). The confidential/public decision stays single-sourced
  // in the backend; the credentials never live in git (the repo is public).
  "GOOGLE_OAUTH_CLIENT_ID",
  "GOOGLE_OAUTH_CLIENT_SECRET",
  // Model-provider keys (dev convenience; BYOK covers packaged installs).
  "OPENAI_API_KEY",
  "ANTHROPIC_API_KEY",
  "GOOGLE_API_KEY",
];

export const UVICORN_MODULES: Record<SupervisedServiceName, string> = {
  backend: "backend_app.desktop_app",
  "ai-backend": "runtime_api.app",
  "backend-facade": "backend_facade.app",
};

export const BACKEND_DB_NAME = "atlas_backend";
export const AI_BACKEND_DB_NAME = "atlas_ai";
export const PG_SUPERUSER = "atlas";

/** PYTHONPATH for a staged service dir: `src<sep>site-packages`. */
export function pythonPathValue(pathDelimiter: string = delimiter): string {
  return `src${pathDelimiter}site-packages`;
}

export function databaseUrl(opts: {
  readonly pgPort: number;
  readonly pgPassword: string;
  readonly database: string;
}): string {
  const password = encodeURIComponent(opts.pgPassword);
  return `postgresql://${PG_SUPERUSER}:${password}@127.0.0.1:${opts.pgPort}/${opts.database}`;
}

// The app pools take the bare `postgresql://` URL (psycopg v3 accepts it),
// but scripts/migrate.py runs yoyo, and yoyo resolves the bare scheme to the
// psycopg2 driver — which is NOT installed. yoyo needs the explicit
// `+psycopg` (v3) marker, exactly as tools/desktop-runtime/run-local.mjs
// proves. Same DSN, driver-qualified scheme.
export function migrateDatabaseUrl(opts: {
  readonly pgPort: number;
  readonly pgPassword: string;
  readonly database: string;
}): string {
  const password = encodeURIComponent(opts.pgPassword);
  return `postgresql+psycopg://${PG_SUPERUSER}:${password}@127.0.0.1:${opts.pgPort}/${opts.database}`;
}

// The file-native AI runtime store — conversations, runs, events, and subagent
// traces persist as JSONL folders under userData instead of the Postgres
// `atlas_ai` database — is the DEFAULT desktop store (see AC2b cutover). It is
// the coherent substrate for a single-process desktop app: local-first,
// inspectable on disk, no embedded RDBMS in the agent run path.
// `COPILOT_DESKTOP_FILE_STORE_V1` is now an OVERRIDE, not an opt-in: an explicit
// falsey value pins the legacy Postgres store (the rollback / escape hatch), an
// explicit truthy value forces file, and unset resolves to file. Read ONCE at
// boot from the desktop process env.
//
// Data continuity: a first file boot starts a FRESH store. Conversations
// already written to the `atlas_ai` Postgres DB are preserved on disk but are
// not shown until carried over with `python -m runtime_adapters.migrate` — or
// pin Postgres via COPILOT_DESKTOP_FILE_STORE_V1=0. See
// docs/operations/desktop-file-store-migration.md.
export const AI_FILE_STORE_V1_FLAG = "COPILOT_DESKTOP_FILE_STORE_V1";

const FILE_STORE_V1_TRUTHY = new Set(["1", "true", "yes", "on", "enabled"]);
const FILE_STORE_V1_FALSEY = new Set(["0", "false", "no", "off", "disabled"]);

/** Relative segments of the file store root under userData: `agent-data/v1`. */
export const AI_FILE_STORE_V1_SEGMENTS = ["agent-data", "v1"] as const;

/**
 * Resolve the desktop ai-backend store backend from the process env. File-native
 * is the DEFAULT; Postgres is opt-out. Deterministic and pure so BOTH consumers
 * (buildServiceEnv's store branch and the supervisor's migration-skip gate)
 * resolve identically off the same env — they can never diverge. Recognition is
 * case- and space-tolerant:
 *   - COPILOT_DESKTOP_FILE_STORE_V1 in {0,false,no,off,disabled} -> "postgres"
 *   - COPILOT_DESKTOP_FILE_STORE_V1 in {1,true,yes,on,enabled}   -> "file"
 *   - unset / empty / unrecognized                               -> "file"
 * Injectable env map so it is testable without mutating `process.env`.
 */
export function resolveAiStoreBackend(
  env: Readonly<Record<string, string | undefined>>,
): "file" | "postgres" {
  const raw = env[AI_FILE_STORE_V1_FLAG];
  if (raw !== undefined) {
    const normalized = raw.trim().toLowerCase();
    if (FILE_STORE_V1_FALSEY.has(normalized)) return "postgres";
    if (FILE_STORE_V1_TRUTHY.has(normalized)) return "file";
  }
  return "file";
}

/**
 * Canonical absolute root for the file-native AI runtime store, derived from
 * Electron's `app.getPath("userData")`: `<userData>/agent-data/v1`. The runtime
 * adapter provisions this tree itself on open (dirs `0o700`, files `0o600` — see
 * runtime_adapters/file/_paths.py `ensure_scaffold`), so the supervisor does NOT
 * pre-create it.
 */
export function aiFileStoreV1Root(userDataDir: string): string {
  return join(userDataDir, ...AI_FILE_STORE_V1_SEGMENTS);
}

export interface ServiceEnvInputs {
  readonly secrets: BootSecrets;
  readonly pgPort: number;
  readonly backendPort: number;
  readonly aiBackendPort: number;
  readonly facadePort: number;
  /** Source env the passthrough allowlist filters (process.env). */
  readonly processEnv: Readonly<Record<string, string | undefined>>;
  /** app.getPath("userData") — used to derive the file store root. */
  readonly userDataDir: string;
  /**
   * Built frontend web dir (wallet.html + assets/). When set, the facade serves
   * the SIWE wallet page from here. Optional so unit tests without a staged web
   * dir still build a valid env.
   */
  readonly webDir?: string;
  /** Injectable for path-separator tests; defaults to the host's. */
  readonly pathDelimiter?: string;
  /**
   * Force the ai-backend store backend for THIS boot, bypassing
   * `resolveAiStoreBackend(processEnv)`. The desktop supervisor sets it after
   * the first-file-boot migration gate has run: `"file"` once the carry-over
   * import has succeeded (or when a fresh/empty install stays on file), and
   * `"postgres"` as the FAIL-SAFE fallback when the migration could not be
   * trusted (verify mismatch / any error) — so a failed import serves the
   * still-authoritative Postgres store this boot instead of an empty app.
   * `undefined` (the default) preserves the env-resolved behaviour, so every
   * other caller and the existing tests are unaffected.
   */
  readonly storeBackendOverride?: "file" | "postgres";
}

// Builds the FULL child environment for one supervised service: filtered
// passthrough + the contract env table + PYTHONPATH. The same env is used
// for the service's migrate.py gate so migrations and the app always see
// identical configuration.
export function buildServiceEnv(
  name: SupervisedServiceName,
  inputs: ServiceEnvInputs,
): Record<string, string> {
  const env: Record<string, string> = {};
  for (const key of ENV_PASSTHROUGH_ALLOWLIST) {
    const value = inputs.processEnv[key];
    if (value !== undefined && value !== "") {
      env[key] = value;
    }
  }
  env.PYTHONPATH = pythonPathValue(inputs.pathDelimiter);
  env.PYTHONUNBUFFERED = "1";
  // OTel kill switch: the desktop runs on a laptop with no collector. Without
  // it, ai-backend's TelemetryBootstrap fails closed under *_ENVIRONMENT=production
  // ("OTEL_EXPORTER_OTLP_ENDPOINT must be set in production").
  env.OTEL_SDK_DISABLED = "true";
  env.ENTERPRISE_DEPLOYMENT_PROFILE = "single_user_desktop";
  env.ENTERPRISE_AUTH_SECRET = inputs.secrets.authSecret;
  env.ENTERPRISE_SERVICE_TOKEN = inputs.secrets.serviceToken;

  const backendUrl = `http://127.0.0.1:${inputs.backendPort}`;
  const aiBackendUrl = `http://127.0.0.1:${inputs.aiBackendPort}`;

  switch (name) {
    case "backend": {
      const dbUrl = databaseUrl({
        pgPort: inputs.pgPort,
        pgPassword: inputs.secrets.pgPassword,
        database: BACKEND_DB_NAME,
      });
      env.BACKEND_ENVIRONMENT = "production";
      env.DATABASE_URL = dbUrl;
      // scripts/migrate.py runs yoyo, which needs the +psycopg driver marker.
      env.BACKEND_DATABASE_URL = migrateDatabaseUrl({
        pgPort: inputs.pgPort,
        pgPassword: inputs.secrets.pgPassword,
        database: BACKEND_DB_NAME,
      });
      env.MCP_TOKEN_VAULT_BACKEND = "local";
      env.MCP_TOKEN_VAULT_SECRET = inputs.secrets.vaultSecret;
      // desktop_app.py REQUIRES this (audit chain fails closed without it).
      env.AUDIT_HMAC_KEY = inputs.secrets.auditHmacKey;
      // Pin SIWE's expected origin to the facade origin — the wallet page is
      // served BY the facade (see FACADE_WEB_DIST_DIR) and derives its SIWE
      // message domain from window.location, so expected_origin must match it.
      // Without this the backend defaults to magic_link_base_url (localhost:5173)
      // and every desktop wallet sign-in fails domain_mismatch.
      env.SIWE_ORIGIN = `http://127.0.0.1:${inputs.facadePort}`;
      // Account-merge runtime leg (PRD account-linking §6.4): the backend's
      // merge saga calls ai-backend's /internal/v1/admin/account-merge over
      // HTTP. Without this URL the saga fails CLOSED at its runtime
      // checkpoint (UnconfiguredRuntimeMergeClient) instead of silently
      // skipping the ai-backend re-key.
      env.AI_BACKEND_URL = aiBackendUrl;
      break;
    }
    case "ai-backend": {
      env.RUNTIME_ENVIRONMENT = "production";
      // The override wins when the supervisor has resolved the effective backend
      // for this boot (post-migration gate); otherwise fall back to the pure
      // env resolution so buildServiceEnv and the supervisor stay single-sourced.
      const aiBackend =
        inputs.storeBackendOverride ?? resolveAiStoreBackend(inputs.processEnv);
      if (aiBackend === "file") {
        // OPT-IN file-native store (JSONL folders under userData) instead of
        // the Postgres `atlas_ai` DB. No relational DB env is set, so the
        // ai-backend migration gate is skipped in desktop-supervisor.ts.
        // ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop is already set
        // above; the runtime factory requires it for the file backend. Starts a
        // FRESH store — existing Postgres conversations are NOT carried over
        // until a Postgres->file migration exists.
        env.RUNTIME_STORE_BACKEND = "file";
        env.RUNTIME_FILE_STORE_ROOT = aiFileStoreV1Root(inputs.userDataDir);
      } else {
        // DEFAULT: Postgres AI store — byte-identical to prior boots.
        const dbUrl = databaseUrl({
          pgPort: inputs.pgPort,
          pgPassword: inputs.secrets.pgPassword,
          database: AI_BACKEND_DB_NAME,
        });
        env.RUNTIME_STORE_BACKEND = "postgres";
        env.DATABASE_URL = dbUrl;
        // scripts/migrate.py runs yoyo, which needs the +psycopg driver marker.
        env.RUNTIME_DATABASE_URL = migrateDatabaseUrl({
          pgPort: inputs.pgPort,
          pgPassword: inputs.secrets.pgPassword,
          database: AI_BACKEND_DB_NAME,
        });
        // Migrations are a dedicated boot step (migrations.ts). Without this the
        // store's startup auto-apply would re-enter yoyo with the bare
        // postgresql:// DATABASE_URL and crash on the missing psycopg2 driver.
        env.RUNTIME_MIGRATIONS_AUTO_APPLY = "false";
      }
      env.RUNTIME_START_IN_PROCESS_WORKER = "true";
      env.RUNTIME_EVENT_BUS_BACKEND = "in_memory";
      env.MCP_BACKEND_REGISTRY_URL = backendUrl;
      env.SKILLS_BACKEND_REGISTRY_URL = backendUrl;
      // BYOK lane: UserPoliciesResolverFactory activates the runtime-policies
      // fetch (decrypted provider keys included) only when BOTH
      // BACKEND_BASE_URL and ENTERPRISE_SERVICE_TOKEN are set. Without this
      // line it silently degrades to the Null resolver and every BYOK run
      // fails at create with "Missing API key for model provider …".
      env.BACKEND_BASE_URL = backendUrl;
      // models.dev catalog disk cache (PR-A): lets the live model catalog
      // survive offline boots with the last successful fetch. Unset would
      // silently drop to the vendored snapshot tier — fine, but staler.
      env.RUNTIME_MODEL_CATALOG_CACHE_DIR = join(
        inputs.userDataDir,
        "model-catalog",
      );
      // Passed for parity with the proven run-local.mjs boot; harmless if the
      // ai-backend audit path does not read it.
      env.AUDIT_HMAC_KEY = inputs.secrets.auditHmacKey;
      break;
    }
    case "backend-facade": {
      env.FACADE_ENVIRONMENT = "production";
      env.BACKEND_URL = backendUrl;
      env.AI_BACKEND_URL = aiBackendUrl;
      // Serve the built SIWE wallet page (wallet.html + assets/) from the staged
      // web dir, same-origin with /v1/auth/siwe/*. Empty when unstaged → no route.
      if (inputs.webDir !== undefined && inputs.webDir !== "") {
        env.FACADE_WEB_DIST_DIR = inputs.webDir;
      }
      break;
    }
  }
  return env;
}
