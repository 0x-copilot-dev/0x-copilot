// @vitest-environment node
import { describe, expect, it } from "vitest";

import type { BootSecrets } from "./boot-secrets";
import {
  buildServiceEnv,
  databaseUrl,
  ENV_PASSTHROUGH_ALLOWLIST,
  pythonPathValue,
  UVICORN_MODULES,
  type ServiceEnvInputs,
} from "./service-env";

const SECRETS: BootSecrets = {
  authSecret: "auth-secret-value",
  serviceToken: "service-token-value",
  vaultSecret: "vault-secret-value",
  pgPassword: "pg+password/with=specials",
};

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
    expect(env.DATABASE_URL).toContain("/atlas_backend");
    expect(env.DATABASE_URL).toContain("@127.0.0.1:54321/");
    expect(env.BACKEND_DATABASE_URL).toBe(env.DATABASE_URL);
    expect(env.ENTERPRISE_AUTH_SECRET).toBe(SECRETS.authSecret);
    expect(env.ENTERPRISE_SERVICE_TOKEN).toBe(SECRETS.serviceToken);
    expect(env.MCP_TOKEN_VAULT_BACKEND).toBe("local");
    expect(env.MCP_TOKEN_VAULT_SECRET).toBe(SECRETS.vaultSecret);
    expect(env.PYTHONPATH).toBe("src:site-packages");
    expect(env.PYTHONUNBUFFERED).toBe("1");
  });

  it("passes GOOGLE_OAUTH_CLIENT_ID through when set", () => {
    const env = buildServiceEnv(
      "backend",
      inputs({ GOOGLE_OAUTH_CLIENT_ID: "client-123" }),
    );
    expect(env.GOOGLE_OAUTH_CLIENT_ID).toBe("client-123");
  });
});

describe("buildServiceEnv(ai-backend)", () => {
  it("produces the contract env table with its OWN database", () => {
    const env = buildServiceEnv("ai-backend", inputs());
    expect(env.RUNTIME_ENVIRONMENT).toBe("production");
    expect(env.ENTERPRISE_DEPLOYMENT_PROFILE).toBe("single_user_desktop");
    expect(env.RUNTIME_STORE_BACKEND).toBe("postgres");
    expect(env.DATABASE_URL).toContain("/atlas_ai");
    expect(env.RUNTIME_DATABASE_URL).toBe(env.DATABASE_URL);
    expect(env.RUNTIME_START_IN_PROCESS_WORKER).toBe("true");
    expect(env.RUNTIME_EVENT_BUS_BACKEND).toBe("in_memory");
    expect(env.MCP_BACKEND_REGISTRY_URL).toBe("http://127.0.0.1:8101");
    expect(env.SKILLS_BACKEND_REGISTRY_URL).toBe("http://127.0.0.1:8101");
    expect(env.ENTERPRISE_AUTH_SECRET).toBe(SECRETS.authSecret);
    expect(env.ENTERPRISE_SERVICE_TOKEN).toBe(SECRETS.serviceToken);
    // Backend-only settings do not leak.
    expect(env.MCP_TOKEN_VAULT_SECRET).toBeUndefined();
    expect(env.BACKEND_ENVIRONMENT).toBeUndefined();
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
        ATLAS_FACADE_URL: "http://localhost:9999",
      }),
    );
    expect(env.PATH).toBe("/usr/bin");
    expect(env.HOME).toBe("/Users/me");
    expect(env.SECRET_LEAK).toBeUndefined();
    expect(env.AWS_SECRET_ACCESS_KEY).toBeUndefined();
    expect(env.ATLAS_FACADE_URL).toBeUndefined();
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
    expect(ENV_PASSTHROUGH_ALLOWLIST).toContain("PATH");
  });
});
