// @vitest-environment node
import { readFileSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type { BootSecrets } from "./boot-secrets";
import { LocalServiceIdentityRegistry } from "./local-service-identity";
import {
  aiFileStoreV1Root,
  buildServiceEnv,
  databaseUrl,
  ENV_PASSTHROUGH_ALLOWLIST,
  resolveDesktopStudioRuntimeEnv,
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
const REPOSITORY_ROOT = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../../..",
);

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

describe("resolveDesktopStudioRuntimeEnv", () => {
  it("ships artifacts without entering cohort-gated operation enforcement", () => {
    expect(
      resolveDesktopStudioRuntimeEnv({}, { workspaceBrokerEnabled: false }),
    ).toEqual({
      SURFACES_V2: "true",
      ARTIFACT_EFFECTS_V2: "true",
      ARTIFACT_DRAFTS_V2: "true",
      OPERATION_GATEWAY_MODE: "off",
      WORKSPACE_EFFECT_MODE: "off",
    });
  });

  it("keeps workspace enforcement off until operation enforcement is explicit", () => {
    expect(
      resolveDesktopStudioRuntimeEnv({}, { workspaceBrokerEnabled: true }),
    ).toMatchObject({
      OPERATION_GATEWAY_MODE: "off",
      WORKSPACE_EFFECT_MODE: "off",
    });
  });

  it("derives workspace enforcement after an operator explicitly enables the gateway", () => {
    expect(
      resolveDesktopStudioRuntimeEnv(
        { OPERATION_GATEWAY_MODE: "enforce" },
        { workspaceBrokerEnabled: true },
      ),
    ).toMatchObject({
      OPERATION_GATEWAY_MODE: "enforce",
      WORKSPACE_EFFECT_MODE: "enforce",
    });
  });

  it("keeps explicit kill switches local to the desktop boot", () => {
    expect(
      resolveDesktopStudioRuntimeEnv(
        {
          SURFACES_V2: "false",
          ARTIFACT_EFFECTS_V2: "off",
          ARTIFACT_DRAFTS_V2: "true",
          OPERATION_GATEWAY_MODE: "off",
        },
        { workspaceBrokerEnabled: true },
      ),
    ).toEqual({
      SURFACES_V2: "false",
      ARTIFACT_EFFECTS_V2: "false",
      ARTIFACT_DRAFTS_V2: "false",
      OPERATION_GATEWAY_MODE: "off",
      WORKSPACE_EFFECT_MODE: "off",
    });
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
    expect(env.RUNTIME_PROVIDER_CIRCUIT_SNAPSHOT_ENABLED).toBe("true");
    // No Postgres AI-DB env when file is active.
    expect(env.DATABASE_URL).toBeUndefined();
    expect(env.RUNTIME_DATABASE_URL).toBeUndefined();
    expect(env.RUNTIME_MIGRATIONS_AUTO_APPLY).toBeUndefined();
    // Store-agnostic wiring is unchanged.
    expect(env.OTEL_SDK_DISABLED).toBe("true");
    expect(env.RUNTIME_START_IN_PROCESS_WORKER).toBe("true");
    // FTUE prereq: the packaged supervisor enables on-device local models so
    // GET /v1/local-models/status reports enabled:true and the gate card lives.
    expect(env.RUNTIME_ENABLE_LOCAL_MODELS).toBe("true");
    // PRD-P8 D2: desktop is the ONLY deployment allowed to detect and start the
    // user's Ollama binary. Without it the first-run card cannot distinguish
    // "not installed" from "stopped" and Restart Ollama has nothing to call.
    expect(env.RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME).toBe("true");
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
    expect(env.RUNTIME_ENABLE_DESKTOP_BROWSER).toBe("false");
    expect(env.DESKTOP_BROWSER_BROKER_URL).toBeUndefined();
    expect(env.DESKTOP_BROWSER_BROKER_TOKEN).toBeUndefined();
    expect(env.DESKTOP_BROWSER_BROKER_AUDIENCE).toBeUndefined();
    expect(env.RUNTIME_ENABLE_DESKTOP_WORKSPACE).toBe("false");
    expect(env.DESKTOP_WORKSPACE_BROKER_URL).toBeUndefined();
    expect(env.DESKTOP_WORKSPACE_BROKER_TOKEN).toBeUndefined();
    expect(env.DESKTOP_WORKSPACE_BROKER_AUDIENCE).toBeUndefined();
    // Backend-only settings do not leak.
    expect(env.MCP_TOKEN_VAULT_SECRET).toBeUndefined();
    expect(env.BACKEND_ENVIRONMENT).toBeUndefined();
    // Studio is a desktop-owned release lane. The artifact/review lifecycle is
    // available to this child by default. Operation enforcement stays off
    // until an operator also supplies the required E2 cohort policy; otherwise
    // the worker would hide every backend MCP tool after successful OAuth.
    expect(env.SURFACES_V2).toBe("true");
    expect(env.ARTIFACT_EFFECTS_V2).toBe("true");
    expect(env.ARTIFACT_DRAFTS_V2).toBe("true");
    expect(env.OPERATION_GATEWAY_MODE).toBe("off");
    expect(env.WORKSPACE_EFFECT_MODE).toBe("off");
  });

  it("does not infer operation enforcement from a workspace broker alone", () => {
    const env = buildServiceEnv("ai-backend", {
      ...inputs(),
      workspaceBroker: {
        enabled: true,
        baseUrl: "http://127.0.0.1:54322",
        token: "workspace-broker-secret",
        audience: "desktop-capability-broker",
      },
    });

    expect(env.WORKSPACE_EFFECT_MODE).toBe("off");
    expect(env.OPERATION_GATEWAY_MODE).toBe("off");
  });

  it("enables workspace staging when the broker and gateway opt-in are both present", () => {
    const env = buildServiceEnv("ai-backend", {
      ...inputs({ OPERATION_GATEWAY_MODE: "enforce" }),
      workspaceBroker: {
        enabled: true,
        baseUrl: "http://127.0.0.1:54322",
        token: "workspace-broker-secret",
        audience: "desktop-capability-broker",
      },
    });

    expect(env.WORKSPACE_EFFECT_MODE).toBe("enforce");
    expect(env.OPERATION_GATEWAY_MODE).toBe("enforce");
  });

  it("honors a desktop artifact kill switch without constructing invalid settings", () => {
    const env = buildServiceEnv(
      "ai-backend",
      inputs({
        ARTIFACT_EFFECTS_V2: "false",
        ARTIFACT_DRAFTS_V2: "true",
      }),
    );

    expect(env.ARTIFACT_EFFECTS_V2).toBe("false");
    expect(env.ARTIFACT_DRAFTS_V2).toBe("false");
  });

  it("never grants runtime management to the sibling services", () => {
    // The flag authorises a process spawn on the user's machine. Only the
    // ai-backend child (which owns /v1/local-models/*) may carry it; backend
    // and facade must not, so a misread of the shared env can never widen it.
    for (const name of ["backend", "backend-facade"] as const) {
      const env = buildServiceEnv(name, inputs());
      expect(env.RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME).toBeUndefined();
      expect(env.RUNTIME_ENABLE_LOCAL_MODELS).toBeUndefined();
    }
  });

  it("shares only artifact route admission with the facade", () => {
    const hostile = inputs({
      SURFACES_V2: "false",
      ARTIFACT_EFFECTS_V2: "true",
      ARTIFACT_DRAFTS_V2: "true",
      OPERATION_GATEWAY_MODE: "enforce",
      WORKSPACE_EFFECT_MODE: "enforce",
    });
    const backend = buildServiceEnv("backend", hostile);
    expect(backend.SURFACES_V2).toBeUndefined();
    expect(backend.ARTIFACT_EFFECTS_V2).toBeUndefined();
    expect(backend.ARTIFACT_DRAFTS_V2).toBeUndefined();
    expect(backend.OPERATION_GATEWAY_MODE).toBeUndefined();
    expect(backend.WORKSPACE_EFFECT_MODE).toBeUndefined();

    const facade = buildServiceEnv("backend-facade", hostile);
    expect(facade.ARTIFACT_EFFECTS_V2).toBe("true");
    expect(facade.SURFACES_V2).toBeUndefined();
    expect(facade.ARTIFACT_DRAFTS_V2).toBeUndefined();
    expect(facade.OPERATION_GATEWAY_MODE).toBeUndefined();
    expect(facade.WORKSPACE_EFFECT_MODE).toBeUndefined();
  });

  it("keeps facade artifact routes on the same explicit rollback switch", () => {
    const env = buildServiceEnv(
      "backend-facade",
      inputs({ ARTIFACT_EFFECTS_V2: "false" }),
    );

    expect(env.ARTIFACT_EFFECTS_V2).toBe("false");
  });

  it("injects browser broker authority into ai-backend only", () => {
    const withBrowser: ServiceEnvInputs = {
      ...inputs(),
      browserBroker: {
        enabled: true,
        baseUrl: "http://127.0.0.1:54321",
        token: "browser-broker-secret",
        audience: "desktop-browser-broker",
      },
    };
    const ai = buildServiceEnv("ai-backend", withBrowser);
    expect(ai.RUNTIME_ENABLE_DESKTOP_BROWSER).toBe("true");
    expect(ai.DESKTOP_BROWSER_BROKER_URL).toBe("http://127.0.0.1:54321");
    expect(ai.DESKTOP_BROWSER_BROKER_TOKEN).toBe("browser-broker-secret");
    expect(ai.DESKTOP_BROWSER_BROKER_AUDIENCE).toBe("desktop-browser-broker");

    for (const sibling of ["backend", "backend-facade"] as const) {
      const env = buildServiceEnv(sibling, withBrowser);
      expect(env.RUNTIME_ENABLE_DESKTOP_BROWSER).toBeUndefined();
      expect(env.DESKTOP_BROWSER_BROKER_URL).toBeUndefined();
      expect(env.DESKTOP_BROWSER_BROKER_TOKEN).toBeUndefined();
      expect(env.DESKTOP_BROWSER_BROKER_AUDIENCE).toBeUndefined();
    }
    expect(ENV_PASSTHROUGH_ALLOWLIST).not.toContain(
      "DESKTOP_BROWSER_BROKER_TOKEN",
    );
  });

  it("injects the private workspace broker into ai-backend only", () => {
    const withWorkspace: ServiceEnvInputs = {
      ...inputs(),
      workspaceBroker: {
        enabled: true,
        baseUrl: "http://127.0.0.1:54322",
        token: "workspace-broker-secret",
        audience: "desktop-capability-broker",
      },
    };
    const ai = buildServiceEnv("ai-backend", withWorkspace);
    expect(ai.RUNTIME_ENABLE_DESKTOP_WORKSPACE).toBe("true");
    expect(ai.DESKTOP_WORKSPACE_BROKER_URL).toBe("http://127.0.0.1:54322");
    expect(ai.DESKTOP_WORKSPACE_BROKER_TOKEN).toBe("workspace-broker-secret");
    expect(ai.DESKTOP_WORKSPACE_BROKER_AUDIENCE).toBe(
      "desktop-capability-broker",
    );

    for (const sibling of ["backend", "backend-facade"] as const) {
      const env = buildServiceEnv(sibling, withWorkspace);
      expect(env.RUNTIME_ENABLE_DESKTOP_WORKSPACE).toBeUndefined();
      expect(env.DESKTOP_WORKSPACE_BROKER_URL).toBeUndefined();
      expect(env.DESKTOP_WORKSPACE_BROKER_TOKEN).toBeUndefined();
      expect(env.DESKTOP_WORKSPACE_BROKER_AUDIENCE).toBeUndefined();
    }
    expect(ENV_PASSTHROUGH_ALLOWLIST).not.toContain(
      "DESKTOP_WORKSPACE_BROKER_TOKEN",
    );
  });

  it("injects signed C2 bootstrap evidence into ai-backend only", () => {
    const withAttestation: ServiceEnvInputs = {
      ...inputs(),
      workspaceAttestation: {
        publicKey: "spki-public-key",
        payload: "signed-payload",
        signature: "ed25519-signature",
      },
    };
    const ai = buildServiceEnv("ai-backend", withAttestation);
    expect(ai.DESKTOP_WORKSPACE_ATTESTATION_PUBLIC_KEY).toBe("spki-public-key");
    expect(ai.DESKTOP_WORKSPACE_ATTESTATION_PAYLOAD).toBe("signed-payload");
    expect(ai.DESKTOP_WORKSPACE_ATTESTATION_SIGNATURE).toBe(
      "ed25519-signature",
    );

    for (const sibling of ["backend", "backend-facade"] as const) {
      const env = buildServiceEnv(sibling, withAttestation);
      expect(env.DESKTOP_WORKSPACE_ATTESTATION_PUBLIC_KEY).toBeUndefined();
      expect(env.DESKTOP_WORKSPACE_ATTESTATION_PAYLOAD).toBeUndefined();
      expect(env.DESKTOP_WORKSPACE_ATTESTATION_SIGNATURE).toBeUndefined();
    }
    expect(ENV_PASSTHROUGH_ALLOWLIST).not.toContain(
      "DESKTOP_WORKSPACE_ATTESTATION_PAYLOAD",
    );
  });

  it("does not let a hostile process env inject the runtime-management flag", () => {
    // The flag is set by the supervisor, never passed through: it is not on
    // ENV_PASSTHROUGH_ALLOWLIST, so its value is always the supervisor's.
    expect(ENV_PASSTHROUGH_ALLOWLIST).not.toContain(
      "RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME",
    );
    const env = buildServiceEnv(
      "backend-facade",
      inputs({ RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME: "true" }),
    );
    expect(env.RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME).toBeUndefined();
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

describe("buildServiceEnv(ai-backend) store wiring", () => {
  it("selects the file store and sets an absolute root under userData", () => {
    const env = buildServiceEnv("ai-backend", inputs());
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

  it("leaves the backend service on Postgres (the file store is ai-backend only)", () => {
    const env = buildServiceEnv("backend", inputs());
    expect(env.DATABASE_URL).toContain("/atlas_backend");
    expect(env.RUNTIME_FILE_STORE_ROOT).toBeUndefined();
    expect(env.RUNTIME_STORE_BACKEND).toBeUndefined();
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

describe("buildServiceEnv local service identity", () => {
  it("injects only the identity issued for the child and rejects a swap", () => {
    let byte = 0;
    const registry = new LocalServiceIdentityRegistry({
      randomBytes: (size) => Buffer.alloc(size, ++byte),
    });
    const ai = buildServiceEnv("ai-backend", {
      ...inputs(),
      localServiceIdentity: registry.forService("ai-backend"),
    });
    expect(ai.DESKTOP_LOCAL_SERVICE_IDENTITY).toBe("ai-backend");
    expect(ai.DESKTOP_LOCAL_SERVICE_AUDIENCE).toBe("desktop-local:ai-backend");
    expect(ai.DESKTOP_LOCAL_SERVICE_CREDENTIAL).toBeUndefined();
    expect(() =>
      buildServiceEnv("ai-backend", {
        ...inputs(),
        localServiceIdentity: registry.forService("backend"),
      }),
    ).toThrow(/does not match/i);
  });

  it("does not accept ambient process attempts to set local authority identity", () => {
    const env = buildServiceEnv(
      "backend",
      inputs({
        DESKTOP_LOCAL_SERVICE_IDENTITY: "ai-backend",
        DESKTOP_LOCAL_SERVICE_CREDENTIAL: "stolen",
        DESKTOP_LOCAL_SERVICE_AUDIENCE: "desktop-local:ai-backend",
      }),
    );
    expect(env.DESKTOP_LOCAL_SERVICE_IDENTITY).toBeUndefined();
    expect(env.DESKTOP_LOCAL_SERVICE_CREDENTIAL).toBeUndefined();
    expect(env.DESKTOP_LOCAL_SERVICE_AUDIENCE).toBeUndefined();
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

describe("COPILOT_HOME reaches the supervised services", () => {
  // The FS-F live journey provisioned `.tmp/<conversation_id>/` under
  // ~/.0xcopilot even though the app was launched with an explicit
  // COPILOT_HOME. Cause: the variable was never in the passthrough allowlist,
  // so `agent_scratch.copilot_home()` always took its ~/.0xcopilot default —
  // the tree the caller asked for stayed empty while a second one filled up.
  //
  // Asserted through the real buildServiceEnv, not by inspecting the allowlist
  // constant: a test that reads the list would pass on a list nothing consults,
  // which is the shape that hid this.
  it("forwards an explicitly set COPILOT_HOME", () => {
    const env = buildServiceEnv(
      "ai-backend",
      inputs({ COPILOT_HOME: "/tmp/probe-home" }),
    );
    expect(env.COPILOT_HOME).toBe("/tmp/probe-home");
  });

  it("omits it when unset, so the Python default still applies", () => {
    const env = buildServiceEnv("ai-backend", inputs({}));
    expect(env.COPILOT_HOME).toBeUndefined();
  });
});

describe("resolveDesktopStudioRuntimeEnv — the desktop's own E2 cohort", () => {
  // `enforce` is cohort-gated and `RolloutCohortRule` requires an exact
  // org/user selector, so before this the enforced workspace lane was
  // unsatisfiable on desktop: it denied every run, and the tombstone it
  // returned refused READS as well as writes.
  const principal = { orgId: "org_abc123", userId: "usr_def456" };

  it("names this install's principal for every capability the lane needs", () => {
    const env = resolveDesktopStudioRuntimeEnv(
      { OPERATION_GATEWAY_MODE: "enforce" },
      {
        workspaceBrokerEnabled: true,
        localPrincipal: principal,
        packaged: true,
      },
    );

    const rules = JSON.parse(env.E2_ROLLOUT_COHORTS_JSON ?? "[]");
    // The union of what `_workspace_effect_backend_for_run` demands and what
    // `_build_mcp_operation_gateway_services` demands — the latter's absence
    // tombstones the workspace lane too, so naming fewer denies invisibly.
    expect(rules.map((r: { capability: string }) => r.capability)).toEqual([
      "operation_gateway",
      "mcp_gateway",
      "effect_stager",
      "effect_commit",
      "workspace_overlay",
      "workspace_commit",
    ]);
    expect(rules[0]).toMatchObject({
      org_id: "org_abc123",
      user_id: "usr_def456",
    });
  });

  it("also turns each capability's MODE on, without which the rule is dead", () => {
    // `RolloutCohortPolicy.admit` returns GLOBAL_OFF when a capability's mode
    // is OFF, BEFORE it consults any rule. Setting only
    // `OPERATION_GATEWAY_MODE=enforce` marked one capability explicitly
    // controlled — flipping the whole group from legacy passthrough to cohort
    // admission — while the other five stayed off and denied unconditionally.
    // The live symptom was indistinguishable from a missing cohort.
    const env = resolveDesktopStudioRuntimeEnv(
      { OPERATION_GATEWAY_MODE: "enforce" },
      {
        workspaceBrokerEnabled: true,
        localPrincipal: principal,
        packaged: true,
      },
    );

    expect(env.MCP_GATEWAY_MODE).toBe("enforce");
    expect(env.EFFECT_STAGER_MODE).toBe("enforce");
    expect(env.EFFECT_COMMIT_MODE).toBe("enforce");
    expect(env.WORKSPACE_OVERLAY_MODE).toBe("enforce");
    expect(env.WORKSPACE_COMMIT_MODE).toBe("enforce");
  });

  it("never emits modes without the cohort that admits somebody", () => {
    // Modes alone are WORSE than nothing: they mark capabilities controlled
    // with no one admitted, which denies where legacy passthrough allowed.
    const env = resolveDesktopStudioRuntimeEnv(
      { OPERATION_GATEWAY_MODE: "enforce" },
      { workspaceBrokerEnabled: true },
    );

    expect(env.E2_ROLLOUT_COHORTS_JSON).toBeUndefined();
    expect(env.WORKSPACE_OVERLAY_MODE).toBeUndefined();
    expect(env.WORKSPACE_COMMIT_MODE).toBeUndefined();
  });

  it("emits nothing at all when the install has no principal yet", () => {
    // The honest first-run state before one is minted. The lane then degrades
    // to read-only and SAYS so, rather than half-enabling itself.
    const env = resolveDesktopStudioRuntimeEnv(
      { OPERATION_GATEWAY_MODE: "enforce" },
      { workspaceBrokerEnabled: true },
    );
    expect(env.E2_ROLLOUT_COHORTS_JSON).toBeUndefined();
  });

  it("emits nothing on an UNPACKAGED build, which cannot attest C2", () => {
    // The startup validator refuses `WORKSPACE_COMMIT_MODE=enforce` without
    // native attestation, so requesting it on a CLI install turned a graceful
    // read-only degradation into "Application startup failed. Exiting."
    const env = resolveDesktopStudioRuntimeEnv(
      { OPERATION_GATEWAY_MODE: "enforce" },
      { workspaceBrokerEnabled: true, localPrincipal: principal },
    );
    expect(env.E2_ROLLOUT_COHORTS_JSON).toBeUndefined();
    expect(env.WORKSPACE_COMMIT_MODE).toBeUndefined();
  });

  it("emits nothing outside enforce, so the shipped default is unchanged", () => {
    const env = resolveDesktopStudioRuntimeEnv(
      {},
      {
        workspaceBrokerEnabled: true,
        localPrincipal: principal,
        packaged: true,
      },
    );
    expect(env.WORKSPACE_EFFECT_MODE).not.toBe("enforce");
    expect(env.E2_ROLLOUT_COHORTS_JSON).toBeUndefined();
  });

  it("refuses a blank id rather than writing an unmatchable rule", () => {
    // A rule naming "" matches no verified subject, so the lane would deny for
    // a reason indistinguishable from having no policy at all.
    const env = resolveDesktopStudioRuntimeEnv(
      { OPERATION_GATEWAY_MODE: "enforce" },
      {
        workspaceBrokerEnabled: true,
        localPrincipal: { orgId: "", userId: "usr_def456" },
      },
    );
    expect(env.E2_ROLLOUT_COHORTS_JSON).toBeUndefined();
  });
});
