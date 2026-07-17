import { delimiter } from "node:path";

import type { BootSecrets } from "./boot-secrets";
import type { SupervisedServiceName } from "./runtime-paths";

// ONE passthrough allowlist for all three children. Anything not named
// here is stripped from the child environment (children get a curated env,
// never the raw desktop process env). PATH/HOME/etc. are baseline process
// plumbing; GOOGLE_OAUTH_CLIENT_ID is the contract's named passthrough;
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
  // Contract passthrough.
  "GOOGLE_OAUTH_CLIENT_ID",
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

export interface ServiceEnvInputs {
  readonly secrets: BootSecrets;
  readonly pgPort: number;
  readonly backendPort: number;
  readonly aiBackendPort: number;
  readonly facadePort: number;
  /** Source env the passthrough allowlist filters (process.env). */
  readonly processEnv: Readonly<Record<string, string | undefined>>;
  /** Injectable for path-separator tests; defaults to the host's. */
  readonly pathDelimiter?: string;
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
      break;
    }
    case "ai-backend": {
      const dbUrl = databaseUrl({
        pgPort: inputs.pgPort,
        pgPassword: inputs.secrets.pgPassword,
        database: AI_BACKEND_DB_NAME,
      });
      env.RUNTIME_ENVIRONMENT = "production";
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
      env.RUNTIME_START_IN_PROCESS_WORKER = "true";
      env.RUNTIME_EVENT_BUS_BACKEND = "in_memory";
      env.MCP_BACKEND_REGISTRY_URL = backendUrl;
      env.SKILLS_BACKEND_REGISTRY_URL = backendUrl;
      // Passed for parity with the proven run-local.mjs boot; harmless if the
      // ai-backend audit path does not read it.
      env.AUDIT_HMAC_KEY = inputs.secrets.auditHmacKey;
      break;
    }
    case "backend-facade": {
      env.FACADE_ENVIRONMENT = "production";
      env.BACKEND_URL = backendUrl;
      env.AI_BACKEND_URL = aiBackendUrl;
      break;
    }
  }
  return env;
}
