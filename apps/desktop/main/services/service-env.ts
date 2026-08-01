import { delimiter, join } from "node:path";

import type { BootSecrets } from "./boot-secrets";
import { resolveLocalPrincipal } from "./local-principal";
import {
  LOCAL_SERVICE_IDENTITY_ENV,
  LOCAL_SERVICE_IDENTITY_PROTOCOL,
  type LocalServiceIdentity,
} from "./local-service-identity";
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
  // Where the app keeps its own data. The Python side resolves it in
  // `agent_scratch.copilot_home()`, defaulting to ~/.0xcopilot when unset —
  // and it was never forwarded, so the supervised services ALWAYS took the
  // default. A run launched against an explicit COPILOT_HOME provisioned its
  // `.tmp/<conversation_id>/` scratch under ~/.0xcopilot instead: the tree the
  // caller asked for stayed empty while a second one filled up elsewhere.
  // Caught by the FS-F live journey, which looked where it had launched.
  "COPILOT_HOME",
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
  // Optional desktop Code Mode feature. The CLI injects these only after its
  // pinned native wheel has been installed and verified in ai-backend.
  "RUNTIME_ENABLE_MONTY",
  "RUNTIME_INTERPRETER_PROVIDER",
  "RUNTIME_MONTY_LIMIT_PROFILE",
  // Preview connectors (Gmail / Google Drive / Outlook). The backend gates
  // these OFF unless this is exactly "true", and until now nothing forwarded
  // it — so the three preview rows were unreachable in every desktop build
  // regardless of what the operator set. Forwarding it does not turn them on;
  // it only makes the existing switch reachable, and the backend still fails
  // closed on a tenant-template profile even with preview enabled.
  "DESKTOP_CONNECTORS_ALLOW_PREVIEW",
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

/**
 * Resolve the release posture for Studio's artifact and local-workspace
 * lifecycle. These are *supervisor-owned* settings: the desktop process may
 * take an explicit operator kill-switch from its launch environment. The
 * ai-backend receives the complete policy; the facade receives only the
 * matching artifact-route admission bit. They must never join the global
 * passthrough allowlist, which is shared by all three services.
 *
 * Desktop ships the review-first artifact lane on by default. An explicit
 * falsey artifact setting is the rollback path. Workspace effects additionally
 * require the main-owned workspace broker; without it the agent can still make
 * reviewable artifacts but cannot stage a host filesystem mutation.
 */
export function resolveDesktopStudioRuntimeEnv(
  processEnv: Readonly<Record<string, string | undefined>>,
  opts: {
    readonly workspaceBrokerEnabled: boolean;
    /**
     * The install's OWN principal, persisted beside the identity store it
     * names. Present ⇒ this deployment can name its single user in a cohort
     * rule, which is the only way `enforce` is satisfiable on desktop.
     */
    readonly localPrincipal?: {
      readonly orgId: string;
      readonly userId: string;
    };
    /** `app.isPackaged`. Unpackaged builds cannot attest C2 — see `cohortPolicy`. */
    readonly packaged?: boolean;
  },
): Readonly<Record<string, string>> {
  const readBoolean = (name: string, defaultValue: boolean): boolean => {
    const raw = processEnv[name]?.trim().toLowerCase();
    if (raw === undefined || raw === "") return defaultValue;
    return new Set(["1", "true", "yes", "on", "enabled"]).has(raw);
  };
  const readMode = (name: string, defaultValue: string): string => {
    const raw = processEnv[name]?.trim().toLowerCase();
    return raw === undefined || raw === "" ? defaultValue : raw;
  };

  const artifactEffects = readBoolean("ARTIFACT_EFFECTS_V2", true);
  // Never pass an invalid drafts-without-repository combination to the child.
  // `ARTIFACT_EFFECTS_V2=false` is the single explicit rollback switch.
  const artifactDrafts =
    artifactEffects && readBoolean("ARTIFACT_DRAFTS_V2", true);
  // E2 `enforce` is cohort-gated. Desktop cannot safely invent an org/user
  // cohort before the signed-in principal exists, so defaulting to enforce made
  // the worker deny the backend MCP registry while the backend still completed
  // OAuth. The model then saw connector suggestions but no `call_mcp_tool`.
  // Compatibility mode still uses the canonical gateway when its durable
  // dependencies are present; an operator may opt into enforce together with a
  // real E2 cohort policy.
  const operationGateway = readMode("OPERATION_GATEWAY_MODE", "off");
  const workspaceEffect = readMode(
    "WORKSPACE_EFFECT_MODE",
    opts.workspaceBrokerEnabled && operationGateway === "enforce"
      ? "enforce"
      : "off",
  );

  return Object.freeze({
    SURFACES_V2: readBoolean("SURFACES_V2", true) ? "true" : "false",
    ARTIFACT_EFFECTS_V2: artifactEffects ? "true" : "false",
    ARTIFACT_DRAFTS_V2: artifactDrafts ? "true" : "false",
    OPERATION_GATEWAY_MODE: operationGateway,
    WORKSPACE_EFFECT_MODE: workspaceEffect,
    ...cohortPolicy(
      workspaceEffect,
      opts.localPrincipal,
      opts.packaged === true,
    ),
  });
}

/**
 * Capabilities the enforced workspace lane admits for this install's own user.
 *
 * The exact union the runtime demands: `_workspace_effect_backend_for_run`
 * requires the last five, and `_build_mcp_operation_gateway_services` — whose
 * absence tombstones the workspace lane too — additionally requires
 * `mcp_gateway`. Naming fewer produces a lane that denies for a reason nobody
 * can see from here.
 */
const DESKTOP_COHORT_CAPABILITIES = Object.freeze([
  "operation_gateway",
  "mcp_gateway",
  "effect_stager",
  "effect_commit",
  "workspace_overlay",
  "workspace_commit",
] as const);

/**
 * The per-capability MODE variable each of those reads.
 *
 * A cohort rule is necessary but NOT sufficient: `RolloutCohortPolicy.admit`
 * returns `GLOBAL_OFF` when `modes.mode_for(capability)` is OFF, and it does so
 * BEFORE consulting any rule. Setting only `OPERATION_GATEWAY_MODE=enforce`
 * therefore marked that one capability explicitly controlled — which switches
 * the whole dependency group from legacy passthrough to cohort admission —
 * while the other five stayed OFF and denied unconditionally. The lane could
 * never open, and the reason looked identical to a missing cohort.
 */
const CAPABILITY_MODE_ENV = Object.freeze({
  operation_gateway: "OPERATION_GATEWAY_MODE",
  mcp_gateway: "MCP_GATEWAY_MODE",
  effect_stager: "EFFECT_STAGER_MODE",
  effect_commit: "EFFECT_COMMIT_MODE",
  workspace_overlay: "WORKSPACE_OVERLAY_MODE",
  workspace_commit: "WORKSPACE_COMMIT_MODE",
} as const satisfies Record<
  (typeof DESKTOP_COHORT_CAPABILITIES)[number],
  string
>);

/**
 * `E2_ROLLOUT_COHORTS_JSON` naming this install's own principal, or nothing.
 *
 * WHY THIS IS NOT SELF-DEALING. E2 cohorts are a FLEET staged-rollout tool:
 * `RolloutCohortRule` requires an exact org/user/device selector, and the
 * subject is built only from a run record's verified identity. A hosted
 * operator names a subset of their users; a single-user desktop has exactly one
 * user, and the deployment provisioning its own tenant is the same act as any
 * operator writing their own cohort file. It is deployment configuration, not a
 * caller asserting who it is — the runtime still matches against the VERIFIED
 * identity on the run, so a wrong id here fails closed rather than admitting a
 * stranger.
 *
 * Emitted only under `enforce`, so the compatibility lane's behaviour — and
 * every hosted image — is byte-identical.
 *
 * Absent principal ⇒ absent policy ⇒ the lane degrades to read-only and says so
 * (`workspace_effect.tombstone rollout_admission_denied+degraded_to_read_only`).
 * That is the honest first-run state on an install that has not yet minted one,
 * never a silent half-enabled lane.
 */
function cohortPolicy(
  workspaceEffect: string,
  principal: { readonly orgId: string; readonly userId: string } | undefined,
  packaged: boolean,
): Readonly<Record<string, string>> {
  if (workspaceEffect !== "enforce" || principal === undefined) return {};
  if (principal.orgId === "" || principal.userId === "") return {};
  // NEVER emit a mode the runtime will refuse to boot under. The startup
  // validator rejects `WORKSPACE_COMMIT_MODE=enforce` without C2 native
  // attestation, which an UNPACKAGED build cannot produce — so emitting these
  // on a CLI install turns a graceful read-only degradation into
  // "Application startup failed. Exiting." A capability that cannot be
  // satisfied must not be requested.
  if (!packaged) return {};
  const modes: Record<string, string> = {};
  for (const capability of DESKTOP_COHORT_CAPABILITIES) {
    modes[CAPABILITY_MODE_ENV[capability]] = "enforce";
  }
  // Modes and cohort are emitted TOGETHER, never separately. Modes alone mark
  // capabilities explicitly controlled with nobody admitted — strictly worse
  // than legacy passthrough. A cohort alone names a principal for lanes that
  // are globally off. Only the pair opens the lane, so only the pair ships.
  return {
    ...modes,
    E2_ROLLOUT_COHORTS_JSON: JSON.stringify(
      DESKTOP_COHORT_CAPABILITIES.map((capability) => ({
        capability,
        org_id: principal.orgId,
        user_id: principal.userId,
      })),
    ),
  };
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
  /** `app.isPackaged` — gates capabilities an unpackaged build cannot attest. */
  readonly packaged?: boolean;
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
  /**
   * Electron-main browser broker authority for the supervised ai-backend only.
   * Kept outside the global passthrough allowlist so its bearer can never enter
   * backend or backend-facade child environments.
   */
  readonly browserBroker?: {
    readonly enabled: boolean;
    readonly baseUrl?: string;
    readonly token?: string;
    /** Fixed local broker audience; never supplied by the child. */
    readonly audience?: string;
  };
  /**
   * Electron-main workspace authority for the supervised ai-backend only.
   * This is intentionally a separate, private capability from the browser
   * broker: it authenticates the worker to the host-session bootstrap but
   * never gives a renderer, facade, or backend process a workspace permit.
   */
  readonly workspaceBroker?: {
    readonly enabled: boolean;
    readonly baseUrl?: string;
    readonly token?: string;
    /** Fixed local broker audience; never supplied by the child. */
    readonly audience?: string;
  };
  /**
   * Per-boot public-key attestation issued by Electron main. It is supplied
   * only to the supervised ai-backend, never to the facade, backend, preload,
   * or renderer. The private signing key remains in Electron main.
   */
  readonly workspaceAttestation?: {
    readonly publicKey?: string;
    readonly payload?: string;
    readonly signature?: string;
  };
  /**
   * Per-boot, non-secret identity for THIS child. It is deliberately distinct
   * from ENTERPRISE_SERVICE_TOKEN, which remains a compatibility credential
   * for the existing service mesh until its private-transport replacement
   * lands. Local broker credentials are injected separately, only into the
   * intended broker channel, and are bound to this service identity.
   */
  readonly localServiceIdentity?: LocalServiceIdentity;
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
  // Preview connectors ON by default in the desktop app; the passthrough above
  // still lets an operator set "false" to turn them off.
  //
  // The flag was written for a multi-tenant deployment where an admin decides
  // what their users may reach. On a single-user desktop the user IS the
  // operator, so defaulting it off meant the only person who could flip it was
  // also the person who would have to discover an undocumented env var to do
  // it — which is indistinguishable from the feature not existing. Gmail and
  // Drive point at documented Google endpoints and now authorize with the
  // app's own OAuth client, so there is nothing left for the gate to protect
  // against.
  env.DESKTOP_CONNECTORS_ALLOW_PREVIEW =
    inputs.processEnv.DESKTOP_CONNECTORS_ALLOW_PREVIEW?.trim() || "true";
  // OTel kill switch: the desktop runs on a laptop with no collector. Without
  // it, ai-backend's TelemetryBootstrap fails closed under *_ENVIRONMENT=production
  // ("OTEL_EXPORTER_OTLP_ENDPOINT must be set in production").
  env.OTEL_SDK_DISABLED = "true";
  env.ENTERPRISE_DEPLOYMENT_PROFILE = "single_user_desktop";
  env.ENTERPRISE_AUTH_SECRET = inputs.secrets.authSecret;
  // Compatibility lane only: sibling HTTP services still consume this shared
  // token. It is no longer valid evidence for a main-owned local authority.
  env.ENTERPRISE_SERVICE_TOKEN = inputs.secrets.serviceToken;
  const localIdentity = inputs.localServiceIdentity;
  if (localIdentity !== undefined) {
    if (localIdentity.service !== name) {
      throw new Error("desktop local service identity does not match child");
    }
    env[LOCAL_SERVICE_IDENTITY_ENV.service] = localIdentity.service;
    env[LOCAL_SERVICE_IDENTITY_ENV.audience] = localIdentity.audience;
    env[LOCAL_SERVICE_IDENTITY_ENV.protocol] = LOCAL_SERVICE_IDENTITY_PROTOCOL;
  }

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
      // The Desktop Studio policy is calculated once in Electron main's
      // supervised environment and injected only here. Keeping this outside
      // ENV_PASSTHROUGH_ALLOWLIST prevents accidental widening to backend or
      // facade children, while preserving explicit per-boot kill switches.
      Object.assign(
        env,
        resolveDesktopStudioRuntimeEnv(inputs.processEnv, {
          workspaceBrokerEnabled: inputs.workspaceBroker?.enabled === true,
          // The install's own principal, when it has one. Absent on a fresh
          // install's first boot — see `resolveLocalPrincipal`: it ADOPTS an
          // existing id and never mints, because fresh ids would orphan the
          // conversations an existing install keys by org/user.
          localPrincipal: resolveLocalPrincipal(inputs.userDataDir),
          packaged: inputs.packaged === true,
        }),
      );
      const browser = inputs.browserBroker;
      if (
        browser?.enabled === true &&
        browser.baseUrl !== undefined &&
        browser.baseUrl !== "" &&
        browser.token !== undefined &&
        browser.token !== "" &&
        browser.audience !== undefined &&
        browser.audience !== ""
      ) {
        env.RUNTIME_ENABLE_DESKTOP_BROWSER = "true";
        env.DESKTOP_BROWSER_BROKER_URL = browser.baseUrl;
        env.DESKTOP_BROWSER_BROKER_TOKEN = browser.token;
        env.DESKTOP_BROWSER_BROKER_AUDIENCE = browser.audience;
      } else {
        env.RUNTIME_ENABLE_DESKTOP_BROWSER = "false";
      }
      const workspace = inputs.workspaceBroker;
      if (
        workspace?.enabled === true &&
        workspace.baseUrl !== undefined &&
        workspace.baseUrl !== "" &&
        workspace.token !== undefined &&
        workspace.token !== "" &&
        workspace.audience !== undefined &&
        workspace.audience !== ""
      ) {
        env.RUNTIME_ENABLE_DESKTOP_WORKSPACE = "true";
        env.DESKTOP_WORKSPACE_BROKER_URL = workspace.baseUrl;
        env.DESKTOP_WORKSPACE_BROKER_TOKEN = workspace.token;
        env.DESKTOP_WORKSPACE_BROKER_AUDIENCE = workspace.audience;
      } else {
        env.RUNTIME_ENABLE_DESKTOP_WORKSPACE = "false";
      }
      const workspaceAttestation = inputs.workspaceAttestation;
      if (
        workspaceAttestation?.publicKey !== undefined &&
        workspaceAttestation.publicKey !== "" &&
        workspaceAttestation.payload !== undefined &&
        workspaceAttestation.payload !== "" &&
        workspaceAttestation.signature !== undefined &&
        workspaceAttestation.signature !== ""
      ) {
        env.DESKTOP_WORKSPACE_ATTESTATION_PUBLIC_KEY =
          workspaceAttestation.publicKey;
        env.DESKTOP_WORKSPACE_ATTESTATION_PAYLOAD =
          workspaceAttestation.payload;
        env.DESKTOP_WORKSPACE_ATTESTATION_SIGNATURE =
          workspaceAttestation.signature;
      }
      // The override wins when the supervisor has resolved the effective backend
      // for this boot (post-migration gate); otherwise fall back to the pure
      // env resolution so buildServiceEnv and the supervisor stay single-sourced.
      const aiBackend =
        inputs.storeBackendOverride ?? resolveAiStoreBackend(inputs.processEnv);
      if (aiBackend === "file") {
        // DEFAULT file-native store (JSONL folders under userData) instead of
        // the legacy Postgres `atlas_ai` DB. No relational DB env is set, so
        // the ai-backend migration gate is skipped in desktop-supervisor.ts.
        // ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop is already set
        // above; the runtime factory requires it for the file backend. The
        // desktop supervisor's verified carry-over gate resolves the effective
        // backend before this environment is built; a failed carry-over instead
        // supplies the explicit Postgres override below.
        env.RUNTIME_STORE_BACKEND = "file";
        env.RUNTIME_FILE_STORE_ROOT = aiFileStoreV1Root(inputs.userDataDir);
        // Optional, bounded process-local persistence for provider circuit
        // health. This is intentionally desktop/file-store only: it restores
        // opaque endpoint/credential fingerprints after a local restart, never
        // secrets, and does not introduce a shared database/daemon contract.
        env.RUNTIME_PROVIDER_CIRCUIT_SNAPSHOT_ENABLED = "true";
      } else {
        // Explicit rollback or one-boot migration fallback: preserve the
        // legacy Postgres AI store rather than starting an empty file store.
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
      // Enable the on-device local-models capability (/v1/local-models/*) for
      // the PACKAGED desktop build. The ai-backend Pydantic default is False
      // (settings.py:141) so web / self-host-cloud images stay off — this is a
      // supervisor-scoped opt-in, mirroring the staging boot
      // (tools/desktop-runtime/run-local.mjs). Without it a shipped desktop
      // reports enabled:false and the FTUE gate's "Download the local model"
      // card is dead. Kept a string ("true") like every other child env value.
      env.RUNTIME_ENABLE_LOCAL_MODELS = "true";
      // PRD-P8 D2 — authorise ai-backend to DETECT the Ollama binary on this
      // machine and START it (POST /v1/local-models/runtime/start). Desktop is
      // the only deployment that may: the service runs as a child of the user's
      // own app, on the user's own filesystem, against a loopback Ollama.
      // Containerised self-host leaves it false (its OLLAMA_BASE_URL points at
      // host.docker.internal — it cannot see, let alone spawn, a host binary),
      // and therefore honestly reports runtime_state:"unknown" rather than
      // guessing. Without this the first-run card can never tell "Ollama not
      // installed" from "Ollama stopped", and "Restart Ollama" has nothing to
      // call. The ai-backend Pydantic default stays false (fail-safe); this is
      // a supervisor-scoped opt-in, mirrored in tools/desktop-runtime/run-local.mjs.
      env.RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME = "true";
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
      // Facade route admission and ai-backend capability admission are one
      // release contract. If this bit is omitted here, publish_artifact can
      // succeed in the worker while the renderer receives a facade-local 404
      // when it tries to hydrate the resulting surface.
      env.ARTIFACT_EFFECTS_V2 = resolveDesktopStudioRuntimeEnv(
        inputs.processEnv,
        {
          workspaceBrokerEnabled: inputs.workspaceBroker?.enabled === true,
        },
      ).ARTIFACT_EFFECTS_V2;
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
