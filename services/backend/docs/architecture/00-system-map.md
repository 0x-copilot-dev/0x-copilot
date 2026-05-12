# System Map — backend

Module-to-file-to-responsibility reference. Use this to find where something lives or
verify which module owns which domain.

See also: [01-request-lifecycle.md](01-request-lifecycle.md) for how modules cooperate at runtime.

---

## Top-level process

Single FastAPI process: `backend_app/app.py`. No separate worker process.
Background jobs (session sweeper) run as FastAPI `lifespan` tasks.

---

## Module map

### `backend_app/` — root domain modules

| File                    | Owns                                                                                                                                                                                                         |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `app.py`                | FastAPI app assembly; route registration; startup/shutdown lifespan                                                                                                                                          |
| `auth.py`               | `BackendServiceAuthenticator` — verifies `X-Enterprise-Service-Token`; extracts `ScopedIdentity` from service headers; dev-query fallback                                                                    |
| `contracts.py`          | All top-level Pydantic domain records: `McpServerRecord`, `McpAuthSessionRecord`, `SkillRecord`, `AuditEventRecord`, `TokenEnvelope`                                                                         |
| `service.py`            | Domain orchestration: MCP registry CRUD, MCP OAuth flow, skill registry CRUD, internal MCP sessions, JSON-RPC proxy                                                                                          |
| `store.py`              | Store interface + `InMemoryMcpStore` / `PostgresMcpStore`, `InMemorySkillStore` / `PostgresSkillStore`, `InMemoryAuthSessionStore` / `PostgresAuthSessionStore`, `InMemoryAuditStore` / `PostgresAuditStore` |
| `token_vault.py`        | `TokenVault` interface + `LocalTokenVault` (Fernet, dev-only) + `AwsKmsTokenVault` (production). Selected by `MCP_TOKEN_VAULT_BACKEND`.                                                                      |
| `mcp_catalog.py`        | Static catalog of well-known MCP servers (`CatalogEntry` with slug, URL, auth_mode, brand metadata, default_scopes). Seed IDs are deterministic.                                                             |
| `mcp_oauth.py`          | MCP OAuth flow: discovery, dynamic client registration (DCR), authorization URL, token exchange, refresh                                                                                                     |
| `audit_reader.py`       | `AuditReader` — unified read surface across 4 audit event streams; opaque base64-JSON cursor; read-only                                                                                                      |
| `deployment_profile.py` | `DeploymentProfile` + `DeploymentFeatureToggles` — safety defaults per deployment class (dev, SaaS, bank, government)                                                                                        |
| `migrations.py`         | Schema constants pulled from SQL migration files                                                                                                                                                             |

---

### `backend_app/identity/` — identity and authentication (A1–A10)

| File                         | Owns                                                                                                                                                               |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `store.py`                   | User, org, role, auth-provider, and identity-audit persistence (`InMemoryIdentityStore` / `PostgresIdentityStore`). All queries scope by `org_id`.                 |
| `rbac.py`                    | A10 RBAC enforcement: `RequireScopes(...)` and `RequireRoles(...)` FastAPI dependency factories; `public_route()` marker; `RBAC_MODE` env switch (audit / enforce) |
| `sessions.py`                | Session lifecycle service: create, touch, revoke, list, expire                                                                                                     |
| `session_store.py`           | Session row persistence + background sweep scheduler                                                                                                               |
| `session_sweeper.py`         | Lifespan task: expire stale sessions                                                                                                                               |
| `oidc.py`                    | A3 OIDC/OAuth2: dynamic discovery, PKCE flow, token exchange, ID token validation (JWKS), JIT user provisioning                                                    |
| `oidc_store.py`              | OIDC state + token row persistence                                                                                                                                 |
| `saml.py`                    | A5 SAML 2.0 SSO: assertion parsing, replay defense, JIT provisioning, attribute mapping                                                                            |
| `saml_store.py`              | SAML assertion + replay-defense persistence                                                                                                                        |
| `_saml_lib.py`               | Pluggable SAML verifier abstraction                                                                                                                                |
| `scim.py`                    | A7 SCIM 2.0: user CRUD, group CRUD, token management, group→role sync                                                                                              |
| `scim_store.py`              | SCIM user/group/token persistence                                                                                                                                  |
| `scim_filter.py`             | Hand-rolled SCIM filter parser                                                                                                                                     |
| `scim_serializer.py`         | SCIM JSON serialisation (SCIM 2.0 wire format)                                                                                                                     |
| `passwords.py`               | A4 local password auth: Argon2 hashing, verification, reset flow                                                                                                   |
| `password_store.py`          | Password hash persistence                                                                                                                                          |
| `mfa.py`                     | MFA service: TOTP setup/verify, WebAuthn registration/assertion                                                                                                    |
| `mfa_store.py`               | MFA factor persistence                                                                                                                                             |
| `lockout.py`                 | Account lockout service: increment attempt count, check threshold, unlock                                                                                          |
| `lockout_store.py`           | Lockout state persistence                                                                                                                                          |
| `jwks.py`                    | JWKS fetcher for OIDC ID token validation                                                                                                                          |
| `me_store.py`                | User profile and preferences persistence                                                                                                                           |
| `invitations.py`             | Invitation service: create, verify, accept, revoke                                                                                                                 |
| `invitation_store.py`        | Invitation row persistence                                                                                                                                         |
| `login_email_first.py`       | Email-first login flow state machine                                                                                                                               |
| `login_email_first_store.py` | Email-first flow state persistence                                                                                                                                 |
| `avatar_store.py`            | User avatar blob storage                                                                                                                                           |
| `email_dispatcher.py`        | `EmailDispatcher` protocol — implemented by the deployment's email adapter                                                                                         |
| `_pkce.py`                   | PKCE verifier generation and challenge                                                                                                                             |

---

### `backend_app/api_keys/` — API key auth (B3)

| File       | Owns                                                                                                                                          |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `auth.py`  | Bearer token auth via `atlas_pk_*` prefix lookup + secret hash verification                                                                   |
| `store.py` | API key row CRUD: mint, list, revoke, rotate. Fields: `key_prefix`, `secret_hash`, `scopes`, `kind` (personal / workspace), `rotated_from_id` |

---

### `backend_app/routes/` — HTTP route handlers (25+ routers)

Each file registers one route group via `router.include_router()`. Mapping:

| File                      | Prefix                                   | Auth                                                 |
| ------------------------- | ---------------------------------------- | ---------------------------------------------------- |
| `health.py`               | `/healthz`, `/readyz`, `/v1/health`      | public                                               |
| `mcp_*.py`                | `/v1/mcp/*`                              | `RequireScopes(mcp:read / mcp:write)`                |
| `api_keys.py`             | `/v1/api-keys/*`                         | session or api-key bearer                            |
| `sessions.py`             | `/internal/v1/auth/sessions*`            | service token                                        |
| `oidc.py`                 | `/internal/v1/auth/oidc/*`               | mixed (login = public; admin = service token)        |
| `saml.py`                 | `/internal/v1/auth/saml/*`               | public (assertion endpoint) + service token (config) |
| `passwords.py`            | `/internal/v1/auth/passwords/*`          | session                                              |
| `mfa.py`                  | `/internal/v1/auth/mfa/*`                | session + `mfa:pending` scope                        |
| `me.py`                   | `/internal/v1/auth/me`                   | service token (reads identity from headers)          |
| `me_avatar.py`            | `/internal/v1/auth/me/avatar`            | service token                                        |
| `me_mfa.py`               | `/internal/v1/auth/me/mfa`               | service token                                        |
| `me_preferences.py`       | `/internal/v1/auth/me/preferences`       | service token                                        |
| `me_profile.py`           | `/internal/v1/auth/me/profile`           | service token                                        |
| `members.py`              | `/internal/v1/auth/members/*`            | `RequireScopes(admin:users)`                         |
| `invitations.py`          | `/internal/v1/auth/invitations/*`        | `RequireScopes(admin:users)`                         |
| `lockouts.py`             | `/internal/v1/auth/lockouts/*`           | `RequireScopes(admin:users)`                         |
| `scim.py`                 | `/internal/v1/auth/scim/*`               | SCIM bearer token                                    |
| `workspace.py`            | `/internal/v1/auth/workspace/*`          | service token                                        |
| `workspace_mfa_policy.py` | `/internal/v1/auth/workspace/mfa-policy` | `RequireScopes(admin:users)`                         |
| `login_email_first.py`    | `/internal/v1/auth/login-email-first/*`  | public                                               |
| `audit_list.py`           | `/internal/v1/audit`                     | `RequireScopes(audit:read)`                          |
| `audit_export.py`         | `/internal/v1/audit/export`              | `RequireScopes(admin:audit_export)`                  |
| `siem.py`                 | `/internal/v1/siem/*`                    | `RequireScopes(admin:siem)`                          |
| `tool_use_policies.py`    | `/internal/v1/tool-use-policies/*`       | service token                                        |
| `runtime_policies.py`     | `/internal/v1/runtime/policies/*`        | service token                                        |
| `privacy.py`              | `/internal/v1/auth/privacy/*`            | service token                                        |
| `notifications.py`        | `/internal/v1/auth/notifications/*`      | service token                                        |
| `billing.py`              | `/internal/v1/billing/*`                 | service token (stub routes)                          |

---

### `backend_app/dev_idp/` — dev identity issuer (W0.1)

Only registered when `BACKEND_ENVIRONMENT=development`.

| File          | Owns                                                 |
| ------------- | ---------------------------------------------------- |
| `personas.py` | `DevPersona`, `PersonaDirectory` — dev test accounts |
| `_sign.py`    | HMAC bearer signing                                  |
| `routes.py`   | `POST /v1/dev/identity/mint`, `GET /v1/dev/personas` |

---

### `backend_app/db/` — database

| File              | Owns                                                 |
| ----------------- | ---------------------------------------------------- |
| `migrate.py`      | yoyo migration runner; reads `migrations/` SQL files |
| `pool_metrics.py` | asyncpg pool metrics (Prometheus)                    |

---

### `backend_app/siem_export/` — SIEM export (C9)

| File            | Owns                                                                 |
| --------------- | -------------------------------------------------------------------- |
| `interface.py`  | `SiemExporter` protocol + event type definitions                     |
| `exporters.py`  | Elastic, Splunk HEC, Syslog CEF, File, Null exporter implementations |
| `normalizer.py` | Event normalisation to CEF/JSON                                      |
| `pump.py`       | Cursor-tracked pump with dead-letter table                           |

---

### `backend_app/notifications/`, `policies/`, `privacy/`

| Module                   | Owns                                               |
| ------------------------ | -------------------------------------------------- |
| `notifications/store.py` | Notification preferences + quiet hours persistence |
| `policies/store.py`      | Tool-use policy rows                               |
| `privacy/store.py`       | Data residency region + privacy settings           |

---

## Import rules

- `backend_app/` must not import from `ai-backend/` or `backend-facade/`. HTTP only.
- `routes/` depends on `service.py`, `store.py`, `auth.py`, `identity/`.
- `service.py` depends on `store.py` and `token_vault.py` — not on `routes/`.
- `identity/` sub-modules are self-contained; `identity/store.py` is the only persistence port.
- Never leak internal store details (SQL, raw rows) across module boundaries.

---

## Settings

`backend_app/` reads env vars directly via `os.getenv()` and a thin settings class.
See [reference/env-vars.md](../reference/env-vars.md) for the complete list.
