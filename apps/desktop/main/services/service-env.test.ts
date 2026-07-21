// @vitest-environment node
import { isAbsolute, join } from "node:path";

import { describe, expect, it } from "vitest";

import type { BootSecrets } from "./boot-secrets";
import {
  aiFileStoreV1Root,
  AI_FILE_STORE_V1_FLAG,
  buildServiceEnv,
  databaseUrl,
  ENV_PASSTHROUGH_ALLOWLIST,
  resolveAiStoreBackend,
  migrateDatabaseUrl,
  pythonPathValue,
  UVICORN_MODULES,
  type ServiceEnvInputs,
} from "./service-env";

const SECRETS: BootSecrets = {
  authSecret: "auth-secret-value",
  serviceToken: "service-token-value",
  vaultSecret: "vault-secret-value",
  pgPassword: "pg+password/with=specials",
  auditHmacKey: "audit-hmac-key-value",
};

const USER_DATA_DIR = "/Users/test/Library/Application Support/0xCopilot";

function inputs(
  processEnv: Record<string, string | undefined> = {},
): ServiceEnvInputs {
  return {
    secrets: SECRETS,
    pgPort: 54_321,
    backendPort: 8101,
    aiBackendPort: 8001,
    facadePort: 8201,
    processEnv,
    userDataDir: USER_DATA_DIR,
    pathDelimiter: ":",
  };
}

describe("pythonPathValue", () => {
  it("joins src and site-packages with the platform delimiter", () => {
    expect(pythonPathValue(":")).toBe("src:site-packages");
    expect(pythonPathValue(";")).toBe("src;site-packages");
  });
});

describe("databaseUrl", () => {
  it("URL-encodes the password", () => {
    const url = databaseUrl({
      pgPort: 5555,
      pgPassword: "p@ss/w:rd",
      database: "atlas_backend",
    });
    expect(url).toBe(
      "postgresql://atlas:p%40ss%2Fw%3Ard@127.0.0.1:5555/atlas_backend",
    );
  });
});

describe("migrateDatabaseUrl", () => {
  it("uses the +psycopg driver marker (yoyo has no psycopg2)", () => {
    const url = migrateDatabaseUrl({
      pgPort: 5555,
      pgPassword: "p@ss/w:rd",
      database: "atlas_ai",
    });
    expect(url).toBe(
      "postgresql+psycopg://atlas:p%40ss%2Fw%3Ard@127.0.0.1:5555/atlas_ai",
    );
  });
});

describe("uvicorn modules", () => {
  it("matches the resource contract", () => {
    expect(UVICORN_MODULES).toEqual({
      backend: "backend_app.desktop_app",
      "ai-backend": "runtime_api.app",
      "backend-facade": "backend_facade.app",
    });
  });
});

describe("buildServiceEnv(backend)", () => {
  it("produces the contract env table", () => {
    const env = buildServiceEnv("backend", inputs());
    expect(env.BACKEND_ENVIRONMENT).toBe("production");
    expect(env.ENTERPRISE_DEPLOYMENT_PROFILE).toBe("single_user_desktop");
    expect(env.DATABASE_URL).toBe(
      "postgresql://atlas:pg%2Bpassword%2Fwith%3Dspecials@127.0.0.1:54321/atlas_backend",
    );
    // The app pool takes the bare scheme; yoyo needs the +psycopg marker.
    expect(env.BACKEND_DATABASE_URL).toBe(
      "postgresql+psycopg://atlas:pg%2Bpassword%2Fwith%3Dspecials@127.0.0.1:54321/atlas_backend",
    );
    expect(env.ENTERPRISE_AUTH_SECRET).toBe(SECRETS.authSecret);
    expect(env.ENTERPRISE_SERVICE_TOKEN).toBe(SECRETS.serviceToken);
    expect(env.MCP_TOKEN_VAULT_BACKEND).toBe("local");
    expect(env.MCP_TOKEN_VAULT_SECRET).toBe(SECRETS.vaultSecret);
    // desktop_app.py requires AUDIT_HMAC_KEY (audit chain fails closed).
    expect(env.AUDIT_HMAC_KEY).toBe(SECRETS.auditHmacKey);
    expect(env.PYTHONPATH).toBe("src:site-packages");
    expect(env.PYTHONUNBUFFERED).toBe("1");
    // Desktop has no OTel collector; the kill switch is required in production.
    expect(env.OTEL_SDK_DISABLED).toBe("true");
  });

  it("passes GOOGLE_OAUTH_CLIENT_ID through when set", () => {
    const env = buildServiceEnv(
      "backend",
      inputs({ GOOGLE_OAUTH_CLIENT_ID: "client-123" }),
    );
    expect(env.GOOGLE_OAUTH_CLIENT_ID).toBe("client-123");
  });

  it("forwards GOOGLE_OAUTH_CLIENT_SECRET when set (Web-client operator)", () => {
    const env = buildServiceEnv(
      "backend",
      inputs({
        GOOGLE_OAUTH_CLIENT_ID: "client-123",
        GOOGLE_OAUTH_CLIENT_SECRET: "secret-abc",
      }),
    );
    expect(env.GOOGLE_OAUTH_CLIENT_SECRET).toBe("secret-abc");
  });

  it("omits GOOGLE_OAUTH_CLIENT_SECRET when unset (Desktop-app client, PKCE)", () => {
    const env = buildServiceEnv(
      "backend",
      inputs({ GOOGLE_OAUTH_CLIENT_ID: "client-123" }),
    );
    expect("GOOGLE_OAUTH_CLIENT_SECRET" in env).toBe(false);
  });
});

describe("buildServiceEnv(ai-backend)", () => {
  it("defaults to the file-native store with no relational DB env", () => {
    const env = buildServiceEnv("ai-backend", inputs());
    expect(env.RUNTIME_ENVIRONMENT).toBe("production");
    expect(env.ENTERPRISE_DEPLOYMENT_PROFILE).toBe("single_user_desktop");
    // File-native is the DEFAULT desktop store (AC2b cutover).
    expect(env.RUNTIME_STORE_BACKEND).toBe("file");
    expect(env.RUNTIME_FILE_STORE_ROOT).toBe(
      join(USER_DATA_DIR, "agent-data", "v1"),
    );
    // No Postgres AI-DB env when file is active.
    expect(env.DATABASE_URL).toBeUndefined();
    expect(env.RUNTIME_DATABASE_URL).toBeUndefined();
    expect(env.RUNTIME_MIGRATIONS_AUTO_APPLY).toBeUndefined();
    // Store-agnostic wiring is unchanged.
    expect(env.OTEL_SDK_DISABLED).toBe("true");
    expect(env.RUNTIME_START_IN_PROCESS_WORKER).toBe("true");
    expect(env.RUNTIME_EVENT_BUS_BACKEND).toBe("in_memory");
    expect(env.MCP_BACKEND_REGISTRY_URL).toBe("http://127.0.0.1:8101");
    expect(env.SKILLS_BACKEND_REGISTRY_URL).toBe("http://127.0.0.1:8101");
    // BYOK lane: the user-policies resolver needs the backend base URL (with
    // ENTERPRISE_SERVICE_TOKEN) or stored provider keys never reach runs.
    expect(env.BACKEND_BASE_URL).toBe("http://127.0.0.1:8101");
    expect(env.RUNTIME_MODEL_CATALOG_CACHE_DIR).toBe(
      join(USER_DATA_DIR, "model-catalog"),
    );
    expect(env.ENTERPRISE_AUTH_SECRET).toBe(SECRETS.authSecret);
    expect(env.ENTERPRISE_SERVICE_TOKEN).toBe(SECRETS.serviceToken);
    expect(env.AUDIT_HMAC_KEY).toBe(SECRETS.auditHmacKey);
    // Backend-only settings do not leak.
    expect(env.MCP_TOKEN_VAULT_SECRET).toBeUndefined();
    expect(env.BACKEND_ENVIRONMENT).toBeUndefined();
  });

  it("pins the Postgres store (full DB contract) on explicit opt-out", () => {
    const env = buildServiceEnv(
      "ai-backend",
      inputs({ [AI_FILE_STORE_V1_FLAG]: "0" }),
    );
    expect(env.RUNTIME_STORE_BACKEND).toBe("postgres");
    expect(env.DATABASE_URL).toContain("postgresql://");
    expect(env.DATABASE_URL).toContain("/atlas_ai");
    // yoyo migrate URL uses the +psycopg driver marker.
    expect(env.RUNTIME_DATABASE_URL).toContain("postgresql+psycopg://");
    expect(env.RUNTIME_DATABASE_URL).toContain("/atlas_ai");
    // Auto-apply is off: migrations.ts owns the apply with the +psycopg URL.
    expect(env.RUNTIME_MIGRATIONS_AUTO_APPLY).toBe("false");
    expect(env.RUNTIME_FILE_STORE_ROOT).toBeUndefined();
  });
});

describe("buildServiceEnv(ai-backend) with storeBackendOverride", () => {
  it("forces the file store even when the env resolves to Postgres", () => {
    // Env opts OUT of file, but the supervisor has resolved the effective
    // backend to file (post-migration) — the override must win.
    const env = buildServiceEnv("ai-backend", {
      ...inputs({ [AI_FILE_STORE_V1_FLAG]: "0" }),
      storeBackendOverride: "file",
    });
    expect(env.RUNTIME_STORE_BACKEND).toBe("file");
    expect(env.RUNTIME_FILE_STORE_ROOT).toBe(
      join(USER_DATA_DIR, "agent-data", "v1"),
    );
    expect(env.DATABASE_URL).toBeUndefined();
    expect(env.RUNTIME_DATABASE_URL).toBeUndefined();
  });

  it("forces the Postgres store (fail-safe fallback) even when the env resolves to file", () => {
    // Env resolves to file (the default), but the migration could not be
    // trusted, so the supervisor forces Postgres for this boot.
    const env = buildServiceEnv("ai-backend", {
      ...inputs({ [AI_FILE_STORE_V1_FLAG]: "1" }),
      storeBackendOverride: "postgres",
    });
    expect(env.RUNTIME_STORE_BACKEND).toBe("postgres");
    expect(env.DATABASE_URL).toContain("/atlas_ai");
    expect(env.RUNTIME_DATABASE_URL).toContain("postgresql+psycopg://");
    expect(env.RUNTIME_MIGRATIONS_AUTO_APPLY).toBe("false");
    expect(env.RUNTIME_FILE_STORE_ROOT).toBeUndefined();
  });

  it("is ignored for non-ai-backend services", () => {
    const env = buildServiceEnv("backend", {
      ...inputs(),
      storeBackendOverride: "postgres",
    });
    expect(env.DATABASE_URL).toContain("/atlas_backend");
    expect(env.RUNTIME_STORE_BACKEND).toBeUndefined();
  });

  it("undefined preserves the env-resolved behaviour", () => {
    const env = buildServiceEnv("ai-backend", {
      ...inputs(),
      storeBackendOverride: undefined,
    });
    expect(env.RUNTIME_STORE_BACKEND).toBe("file");
  });
});

describe("resolveAiStoreBackend", () => {
  it("defaults to the file store for unset/empty/unrecognized values", () => {
    expect(resolveAiStoreBackend({})).toBe("file");
    for (const raw of ["", " ", "nope", "2", "maybe"]) {
      expect(resolveAiStoreBackend({ [AI_FILE_STORE_V1_FLAG]: raw })).toBe(
        "file",
      );
    }
  });

  it("resolves an explicit truthy value to the file store", () => {
    for (const raw of ["1", "true", "TRUE", " yes ", "On", "enabled"]) {
      expect(resolveAiStoreBackend({ [AI_FILE_STORE_V1_FLAG]: raw })).toBe(
        "file",
      );
    }
  });

  it("pins Postgres only on an explicit falsey value (rollback hatch)", () => {
    for (const raw of ["0", "false", "FALSE", " off ", "no", "disabled"]) {
      expect(resolveAiStoreBackend({ [AI_FILE_STORE_V1_FLAG]: raw })).toBe(
        "postgres",
      );
    }
  });
});

describe("aiFileStoreV1Root", () => {
  it("derives <userData>/agent-data/v1 as an absolute path", () => {
    const root = aiFileStoreV1Root(USER_DATA_DIR);
    expect(root).toBe(join(USER_DATA_DIR, "agent-data", "v1"));
    expect(isAbsolute(root)).toBe(true);
    expect(root.startsWith(USER_DATA_DIR)).toBe(true);
  });
});

describe("buildServiceEnv(ai-backend) with COPILOT_DESKTOP_FILE_STORE_V1", () => {
  it("selects the file store and sets an absolute root under userData", () => {
    const env = buildServiceEnv(
      "ai-backend",
      inputs({ [AI_FILE_STORE_V1_FLAG]: "1" }),
    );
    expect(env.RUNTIME_STORE_BACKEND).toBe("file");
    expect(env.RUNTIME_FILE_STORE_ROOT).toBe(
      join(USER_DATA_DIR, "agent-data", "v1"),
    );
    expect(isAbsolute(env.RUNTIME_FILE_STORE_ROOT)).toBe(true);
    expect(env.RUNTIME_FILE_STORE_ROOT.startsWith(USER_DATA_DIR)).toBe(true);
    // The file store rides the in-process worker; profile is required by the
    // runtime factory for the file backend.
    expect(env.RUNTIME_START_IN_PROCESS_WORKER).toBe("true");
    expect(env.ENTERPRISE_DEPLOYMENT_PROFILE).toBe("single_user_desktop");
    expect(env.RUNTIME_EVENT_BUS_BACKEND).toBe("in_memory");
    // Store-agnostic wiring is preserved.
    expect(env.MCP_BACKEND_REGISTRY_URL).toBe("http://127.0.0.1:8101");
    expect(env.SKILLS_BACKEND_REGISTRY_URL).toBe("http://127.0.0.1:8101");
    expect(env.BACKEND_BASE_URL).toBe("http://127.0.0.1:8101");
    expect(env.RUNTIME_MODEL_CATALOG_CACHE_DIR).toBe(
      join(USER_DATA_DIR, "model-catalog"),
    );
    expect(env.AUDIT_HMAC_KEY).toBe(SECRETS.auditHmacKey);
    // No Postgres AI-DB env in file mode.
    expect(env.DATABASE_URL).toBeUndefined();
    expect(env.RUNTIME_DATABASE_URL).toBeUndefined();
    expect(env.RUNTIME_MIGRATIONS_AUTO_APPLY).toBeUndefined();
  });

  it("leaves the backend service on Postgres (flag scopes ai-backend only)", () => {
    const env = buildServiceEnv(
      "backend",
      inputs({ [AI_FILE_STORE_V1_FLAG]: "1" }),
    );
    expect(env.DATABASE_URL).toContain("/atlas_backend");
    expect(env.RUNTIME_FILE_STORE_ROOT).toBeUndefined();
    expect(env.RUNTIME_STORE_BACKEND).toBeUndefined();
  });

  it("pins Postgres for explicit falsey flag values (rollback hatch)", () => {
    for (const raw of ["0", "false", "off", "no", "disabled"]) {
      const env = buildServiceEnv(
        "ai-backend",
        inputs({ [AI_FILE_STORE_V1_FLAG]: raw }),
      );
      expect(env.RUNTIME_STORE_BACKEND).toBe("postgres");
      expect(env.DATABASE_URL).toContain("/atlas_ai");
      expect(env.RUNTIME_FILE_STORE_ROOT).toBeUndefined();
    }
  });
});

describe("buildServiceEnv(backend-facade)", () => {
  it("produces the contract env table with sibling URLs and no DB", () => {
    const env = buildServiceEnv("backend-facade", inputs());
    expect(env.FACADE_ENVIRONMENT).toBe("production");
    expect(env.ENTERPRISE_DEPLOYMENT_PROFILE).toBe("single_user_desktop");
    expect(env.BACKEND_URL).toBe("http://127.0.0.1:8101");
    expect(env.AI_BACKEND_URL).toBe("http://127.0.0.1:8001");
    expect(env.DATABASE_URL).toBeUndefined();
    expect(env.MCP_TOKEN_VAULT_SECRET).toBeUndefined();
  });
});

describe("passthrough allowlist", () => {
  it("strips anything not on the single allowlist", () => {
    const env = buildServiceEnv(
      "backend",
      inputs({
        PATH: "/usr/bin",
        HOME: "/Users/me",
        SECRET_LEAK: "nope",
        AWS_SECRET_ACCESS_KEY: "nope",
        COPILOT_FACADE_URL: "http://localhost:9999",
      }),
    );
    expect(env.PATH).toBe("/usr/bin");
    expect(env.HOME).toBe("/Users/me");
    expect(env.SECRET_LEAK).toBeUndefined();
    expect(env.AWS_SECRET_ACCESS_KEY).toBeUndefined();
    expect(env.COPILOT_FACADE_URL).toBeUndefined();
  });

  it("skips allowlisted keys that are unset or empty", () => {
    const env = buildServiceEnv(
      "backend",
      inputs({ GOOGLE_OAUTH_CLIENT_ID: "" }),
    );
    expect("GOOGLE_OAUTH_CLIENT_ID" in env).toBe(false);
  });

  it("is a single shared list that includes the contract passthrough", () => {
    expect(ENV_PASSTHROUGH_ALLOWLIST).toContain("GOOGLE_OAUTH_CLIENT_ID");
    expect(ENV_PASSTHROUGH_ALLOWLIST).toContain("GOOGLE_OAUTH_CLIENT_SECRET");
    expect(ENV_PASSTHROUGH_ALLOWLIST).toContain("PATH");
  });
});
