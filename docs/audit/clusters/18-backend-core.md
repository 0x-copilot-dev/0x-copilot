---
id: backend-core
title: Backend — Core chassis (app factory, store, contracts, token vault, MCP OAuth, auth)
kind: cluster
paths: [services/backend/src/backend_app/*.py (top-level)]
loc: ~9861
languages: [python]
audit_date: 2026-07-20
---

# Cluster: Backend Core chassis

## Purpose

The 14 top-level modules of `services/backend/src/backend_app/` are the security-critical
chassis that every backend feature package (identity, product destinations, platform)
is wired through. They provide: the FastAPI **app factory** (`app.py`) that composes the
entire service; the **Pydantic contract library** (`contracts.py`) for MCP, skills, deploy
audit, and the full identity domain; the shared **store + transaction + tamper-evident
audit-chain layer** (`store.py`); the **MCP registry / OAuth orchestration service**
(`service.py`); the encrypted **TokenVault** adapter framework (`token_vault.py` +
`token_vault_metrics.py`); the **MCP OAuth 2.1 client** (`mcp_oauth.py`) + static
**connector catalog** (`mcp_catalog.py`); the service-to-service **bearer/auth helper**
(`auth.py`); the **deployment-profile loader** (`deployment_profile.py`); the unified
**audit reader** (`audit_reader.py`); the desktop **composition root** (`desktop_app.py`);
and the **migration SQL shim** (`migrations.py`).

## Public Interface

- `create_app(...) -> FastAPI` — the app factory; ~70 keyword params, wires ~30 subsystems
  (`app.py:409-2030`). Module `__getattr__` lazily builds the default SaaS `app` for uvicorn
  (`app.py:2033-2048`).
- `DesktopComposer.create_desktop_app(env) -> FastAPI` — `single_user_desktop` composition
  root; lazy `app` via `__getattr__` (`desktop_app.py:238-284`).
- Store/transaction API: `PostgresConnectionPool.shared/close_shared/transaction`
  (`store.py:128-222`); `PostgresMcpStore` / `PostgresSkillStore` / `InMemory*Store` with
  `transaction(org_id=)`, `create/update/get/list/delete_server|skill`, `put_token`,
  `append_audit` / `append_skill_audit`, `list_*_audit_events` (`store.py:225-1400`);
  `CrossTenantWriteError` (`store.py:27-38`).
- Service API: `McpRegistryService` (create/install/delete/update/start_auth/complete_auth/
  proxy_internal_rpc/list_internal_cards) (`service.py:190-878`); `SkillRegistryService`
  (`service.py:881-1188`); `ToolCatalogService` (`service.py:1191-1247`);
  `DeployAuditService.record` (`service.py:1325-1365`).
- TokenVault API: `TokenVault.encrypt/decrypt/key_id_for`; `LocalTokenVault`,
  `ManagedSecretTokenVault`, `AwsKmsTokenVault`; `TokenVaultFactory.create(profile=)`
  (`token_vault.py:60-517`).
- Auth helper: `BackendServiceAuthenticator.scoped_identity` /
  `internal_scoped_identity`, `ScopedIdentity` (`auth.py:18-116`).
- MCP OAuth endpoints (via `app.py` routes): `/v1/mcp/servers*`, `/v1/mcp/catalog`,
  `/v1/mcp/servers/{id}/auth/{start,skip}`, `/v1/mcp/oauth/callback`,
  `/internal/v1/mcp/*`, `/internal/v1/skills/*`, `/internal/v1/audit/deploy`
  (`app.py:743-1324`). OAuth client: `RemoteMcpOAuthClient.authorization/exchange_code/
  refresh_token/discover` (`mcp_oauth.py:114-518`).
- Deployment profile: `DeploymentProfileLoader.load`, `resolve_or_exit`, `log_profile`
  (`deployment_profile.py:60-197`).
- Audit reader: `AuditReader.list(org_id, filters, cursor, limit) -> AuditPage`,
  `AuditCursor.encode/decode` (`audit_reader.py:146-451`).

## Internal Structure

| file | ~LOC | responsibility |
|---|---|---|
| `contracts.py` | 2571 | Pydantic contract library: MCP registry, skills, deploy audit, tamper-chain records, **and the entire identity domain** (org/user/role/OIDC/password/lockout/MFA/SAML/SCIM/magic-link/SIWE). `Validators.validate_public_mcp_url` SSRF guard. |
| `app.py` | 2048 | FastAPI app factory `create_app`; ~70 kwargs, ~30 subsystem wirings, all MCP/skill/deploy routes, plus lazy default `app`. |
| `store.py` | 1400 | `PostgresConnectionPool`; in-memory + Postgres MCP/skill/deploy stores; `_AuditChain` signing, advisory-lock audit append, RLS session-var stamping, cross-tenant guards. |
| `service.py` | 1365 | `McpRegistryService` (registration + OAuth + RPC proxy + refresh), `SkillRegistryService` (+ preloaded seeding + SKILL.md parser), `ToolCatalogService`, `DeployAuditService`. |
| `token_vault.py` | 536 | TokenVault interface + `LocalTokenVault` (Fernet, legacy-XOR read), `ManagedSecretTokenVault`/`AwsKmsTokenVault` (kms_v1 envelope), decrypt cache, `TokenVaultFactory` policy. |
| `mcp_oauth.py` | 518 | OAuth 2.1 discovery (RFC 8414/9728), dynamic client registration (RFC 7591), PKCE authorize URL, code/refresh token exchange via `urlopen`. |
| `audit_reader.py` | 450 | Unified read across the four audit streams; fan-out, merge by `created_at` desc, opaque base64-JSON cursor, degrade-on-failure. |
| `desktop_app.py` | 284 | `single_user_desktop` composition root; validates env, forces profile, wires every Postgres adapter, derives API-key pepper, local Fernet vault. |
| `mcp_catalog.py` | 227 | Static `DEFAULT_CATALOG` of 13 well-known remote MCP servers + brand metadata; `CatalogEntry`, `catalog_by_slug`. |
| `deployment_profile.py` | 196 | Resolve/validate `ENTERPRISE_DEPLOYMENT_PROFILE`; per-profile feature toggles; fail-closed-at-boot. |
| `auth.py` | 116 | `BackendServiceAuthenticator` — verifies `ENTERPRISE_SERVICE_TOKEN`, forwards trusted org/user/roles/scopes headers, dev query fallback. |
| `token_vault_metrics.py` | 111 | OTel recorder for vault encrypt/decrypt/cache; no-op when OTel absent. |
| `migrations.py` | 38 | Thin shim exposing SQL constants read from `services/backend/migrations/*.sql` (canonical runner is `db/migrate.py`). |
| `__init__.py` | 1 | Package docstring only. |

## Dependencies

**Outbound:** `copilot_service_contracts` (headers, scopes, deployment_profile),
`copilot_audit_chain` (`AuditChainSigner`), `cryptography` (Fernet), optional `boto3`
(AWS KMS), optional `opentelemetry`, `psycopg`/`psycopg_pool`, `pyyaml` (SKILL.md),
FastAPI/pydantic. `app.py`/`desktop_app.py` import the entire feature-package surface
(`backend_app.identity.*`, all destination packages, `routes.*`).

**Inbound (who depends on the chassis):**
- `contracts.py` ← **63 importers** (god-module). 27 are `backend_app.identity.*` modules
  importing their *own* domain records (org/user/role/OIDC/MFA/SAML/SCIM/SIWE) from core.
- `auth.py` ← **59 importers** (`ScopedIdentity` is the identity primitive for every route).
- `token_vault.py` ← 11 (MCP OAuth, provider keys, webhooks, MFA, OIDC, desktop).
- `store.py` ← 5; `service.py` ← 4; `deployment_profile.py` ← 3; `mcp_oauth.py` ← 2;
  `mcp_catalog.py` ← 2; `audit_reader.py` ← 1 (`routes/audit_list.py`).
- `create_app` ← only `desktop_app.py` + uvicorn default. Cross-cluster consumers:
  backend-identity, backend-product, backend-platform, backend-facade (HTTP only).

## Data Owned

- **Token vault rows** (`mcp_auth_connections`): encrypted access/refresh tokens + per-row
  `kms_key_id`; ciphertext envelopes `gAAAAA…` (Fernet) or `kms_v1:<b64_key>:<b64_blob>`
  (`token_vault.py:275-315`). Written atomically via `put_token` `INSERT … ON CONFLICT …
  WHERE org_id = EXCLUDED.org_id` cross-tenant guard (`store.py:572-623`).
- **Audit chains** (`mcp_audit_events`, `skill_audit_events`) — append-only, per-(table,org)
  advisory-locked, HMAC-signed hash chain (`store.py:658-730, 1141-1215`). `deploy_audit_events`
  is **in-memory only** — no table in `migrations/`, no Postgres adapter (see F2).
- **MCP registry** (`mcp_servers`, `mcp_auth_sessions`); **skills** (`skills`).
- **Migrations**: SQL owned in `services/backend/migrations/`; `migrations.py` is a read
  shim; canonical apply path is `db/migrate.py` (`MigrationRunner`, yoyo-backed).

## Key Flows

- **App boot (SaaS default):** `create_app` → resolve deployment profile (`resolve_or_exit`) →
  session/OIDC/MFA/SAML/SCIM/password/magic-link/SIWE auth block (gated on
  `ENTERPRISE_AUTH_SECRET`) → Settings/BYOK/API-keys → all destination services → return app.
- **App boot (desktop):** `DesktopComposer` validates 5 required env vars, forces
  `single_user_desktop`, wires every Postgres adapter + local Fernet vault + derived
  API-key pepper, disables magic-link → `create_app(**kwargs)`.
- **MCP OAuth:** `start_auth` → `RemoteMcpOAuthClient.authorization` (discover metadata via
  `urlopen`, DCR if supported, PKCE challenge) → provider redirect → `/v1/mcp/oauth/callback`
  (public, `state` is trust anchor) → `complete_auth` exchanges code, encrypts tokens via
  vault, atomic `put_token` + audit, all inside one store transaction.
- **Internal RPC proxy:** `proxy_internal_rpc` → `_require_valid_token` (refresh if expiring)
  → decrypt access token → `urlopen` POST JSON-RPC to `record.url` with Bearer.
- **Audit read:** `AuditReader.list` fans out to 4 stores, merges newest-first, encodes cursor.

## Test Posture

~22 top-level chassis tests plus curated smokes. Directly on this cluster:
`test_token_vault.py`, `test_deployment_profile.py`, `test_desktop_app.py`,
`test_mcp_catalog_install.py`, `test_mcp_registry.py`, `test_mcp_api_flow.py`,
`test_audit_chain.py`, `test_audit_chain_compat.py`, `test_audit_deploy_api.py`,
`test_audit_list.py`, `test_audit_export.py`, `test_atomicity.py`, `test_oauth_no_leak.py`,
`test_tenant_isolation_skills_mcp.py`, `test_migration_runner.py`, `test_tool_catalog_route.py`.
Coverage of tenant isolation, atomicity, audit-chain integrity, token-leak prevention, and
vault backend policy is genuinely strong. **Gap:** the audit-list pagination test
(`test_audit_list.py:197-233`) seeds events with *inverted* timestamps (`now - index`), so
`seq` is inversely correlated with `created_at` — which is the opposite of production and
masks the forward-cursor pagination bug (F1). No test exercises deploy-audit durability
across process restart (F2) or the production API-key-pepper fallback (F3).

## Health Assessment

**FUNCTIONAL BUT AT RISK.** The chassis is comprehensive and, in most places, security-conscious:
tenant-scoped writes with explicit cross-tenant guards, RLS session-var stamping ahead of
policy rollout, an HMAC-signed append-only audit chain with advisory locking, a fail-closed
KMS-capable token vault, atomic token upsert, and an SSRF allow-guard on MCP URLs. Those
primitives are undermined by a small number of high-value defects: a **confirmed audit-list
pagination correctness bug masked by a misleading test** (F1), a **deploy-audit durability
gap** (in-memory only, no table — a compliance control that is not evidenced as persistent, F2),
and an **API-key pepper that fails open in production** (F3, inconsistent with the vault's
fail-closed posture). Structurally, two god-modules concentrate risk: `create_app`
(~1620 lines, ~70 params) and `contracts.py` (2571 lines, ~1600 of them the identity domain
that belongs to `backend_app.identity`). Targeted remediation of F1–F3 and a split of the two
god-modules would move this to healthy; the security model itself is sound.

## Findings

**F1 — [correctness | high | confirmed]** Audit-list backward pagination is broken for
real-world data. The unified feed sorts newest-first (`created_at` desc), but each stream is
paged with a **forward** cursor `after_seq` (`seq > cursor`) and `_advance_cursor` moves the
cursor to `max(seq)` seen (`audit_reader.py:185-200, 436-450`; store filter `store.py:327,333`).
When `seq` is positively correlated with `created_at` (the production case — `created_at`
defaults to `now()` at insert, `contracts.py:820`), page 1 returns the newest `limit` rows
(highest seq), the cursor jumps to the max seq, and page 2 (`seq > max`) is **empty** —
older rows are unreachable. The passing test only works because it seeds inverted timestamps
(`test_audit_list.py:200-207`, `when = now - index`), making seq inversely correlated.
_evidence: audit_reader.py:185-200,436-450; store.py:322-334; test_audit_list.py:197-233._
_suggestion: page by `created_at`/seq **descending** with a `before`-style cursor per stream (as identity already does), and cover with a positively-correlated-timestamp test._

**F2 — [risk | high | confirmed]** Deploy audit is in-memory only. `DeployAuditService`
defaults to `InMemoryDeployAuditStore` (`service.py:1333-1334`; `app.py:490`), there is **no
`deploy_audit_events` table** in `services/backend/migrations/` and **no Postgres adapter**
anywhere — the class docstring itself flags this as a known gap (`store.py:1315-1324`), and
the desktop composer lists it as an accepted gap (`desktop_app.py:157`). Deploy-approval audit
rows are lost on restart and are not exportable to a customer SIEM, so the deploy-audit control
is not durable. _evidence: store.py:1315-1367; service.py:1333-1334; app.py:490; migrations/ (no table)._ _suggestion: ship a Postgres-backed deploy-audit store + migration before claiming the CI/CD deploy-audit control complete._

**F3 — [risk | high | confirmed]** Personal-API-key HMAC pepper fails **open** in production.
When neither `api_key_pepper` nor `BACKEND_API_KEY_PEPPER` (≥16 bytes) is provided, `create_app`
silently substitutes the hardcoded, publicly-known constant `b"dev-only-pepper-NOT-FOR-PROD!"`
with **no `BACKEND_ENVIRONMENT` guard** (`app.py:1489-1499`). This is the SaaS/self-host default
composition root; a prod operator who forgets the env var gets a defense-in-depth control
silently disabled — inconsistent with `TokenVault`/email-dispatcher which fail **closed** in
production (`token_vault.py:478-482`; `app.py:355-364`). _evidence: app.py:1489-1499._
_suggestion: raise under `BACKEND_ENVIRONMENT=production` when the resolved pepper is < 16 bytes, mirroring `_assert_email_dispatcher_safe_for_environment`._

**F4 — [complexity | high | confirmed]** `create_app` is a ~1620-line god-factory
(`app.py:409-2030`) with ~70 keyword parameters, wiring ~30 subsystems inline and using ~10
mid-function `import` statements (`app.py:1350,1370,1388,1405,1480,1766,1937,1980,…`) to dodge
import cycles. It cannot be unit-tested in isolation, every new destination edits the same
function, and the parameter list is the de-facto injection surface for the whole service.
_evidence: app.py:409-2030._ _suggestion: extract per-domain composer functions (auth block, settings block, destinations block) that each return wired routers/services._

**F5 — [ssot-violation | high | confirmed]** `contracts.py` is a 2571-line god-module whose
lines ~940-2560 (~1600 lines) are the **identity domain** (Organization/User/Member/Role/
OIDC/Password/Lockout/MFA/SAML/SCIM/MagicLink/SIWE records), not core MCP/skill/deploy
contracts. 27 `backend_app.identity.*` modules import their own domain models back out of the
shared core file, and 63 modules total depend on it — a single edit hotspot and a layering
inversion (the identity package does not own its own contracts). _evidence: contracts.py:940-2560; 27 identity importers of backend_app.contracts._ _suggestion: move identity records into `backend_app/identity/contracts.py`; keep core contracts to MCP/skills/deploy/tamper-chain._

**F6 — [duplication | medium | confirmed]** Three near-identical audit paths. The
advisory-lock → head SELECT → sign → INSERT append is duplicated between
`PostgresMcpStore.append_audit` (`store.py:658-730`) and
`PostgresSkillStore.append_skill_audit` (`store.py:1141-1215`); the `_sign_{mcp,skill,deploy}_audit`
helpers (`store.py:382-402, 990-1012, 1370-1400`) and the three `list_*_audit_events`
filter/sort bodies are copy-variants; `_json_list/_json_object/_datetime/_connect/
_connect_or_inherit` are duplicated verbatim across both Postgres stores. _evidence: store.py:658-730,1141-1215,382-402,990-1012,1291-1312._ _suggestion: extract a shared `_PgAuditChainWriter` and a Postgres-store base mixin._

**F7 — [risk | medium | plausible]** SSRF residual on MCP discovery + RPC proxy.
`validate_public_mcp_url` blocks only IP-**literal** private/reserved hosts and a small
localhost set (`contracts.py:104-139`); a hostname that *resolves* to a private/link-local
address (e.g. a DNS name pointing at 169.254.169.254 or 10.x) passes the guard. The guard runs
at registration, while `mcp_oauth._fetch_first_json` (`mcp_oauth.py:448-455`) and
`McpRegistryService._post_remote_mcp_rpc` (`service.py:761-786`) re-resolve DNS via `urlopen`
at fetch time — a TOCTOU / DNS-rebind window where the backend makes the request from its own
network position. _evidence: contracts.py:104-139; mcp_oauth.py:438-455; service.py:761-786._
_suggestion: resolve+pin the IP and re-check it against the private/reserved blocklist at fetch time; refuse redirects into private ranges._

**F8 — [risk | medium | confirmed]** Silent broad `except Exception` degradation hides
misconfiguration at boot. `_default_token_vault` swallows **all** exceptions and returns `None`
→ OIDC/MFA/provider-keys routes are silently omitted (`app.py:324-327`); the same pattern
covers `_default_saml_verifier` (`app.py:377-380`), the connector catalog (`app.py:1622-1625`),
and the desktop profile catalog (`app.py:1678-1681`). A production secret typo yields missing
auth routes (401/404), not a fail-closed boot error. _evidence: app.py:324-327,377-380,1622-1625,1678-1681._ _suggestion: narrow the except to the expected missing-secret/missing-dep errors and fail closed under `BACKEND_ENVIRONMENT=production`._

**F9 — [risk | medium | confirmed]** Hardcoded world-readable `/tmp` storage defaults for
tier-2 artifacts. The adapter-registry source bytes default to `/tmp/atlas-adapter-registry`
(`app.py:1958-1963`) and library blobs to `/tmp/atlas-library-blobs` (`app.py:1986-1995`).
If a production composer omits the storage injection, user-uploaded adapter source and library
blobs land in a shared, world-traversable temp directory. _evidence: app.py:1958-1963,1986-1995._
_suggestion: require an explicit data dir under `BACKEND_ENVIRONMENT=production`; do not default to `/tmp`._

**F10 — [inconsistency | medium | confirmed]** `DEV_AUTH_BYPASS` is documented as removed
("no longer exists", root `CLAUDE.md`) yet the deployment profile still models a
`dev_auth_bypass_allowed` toggle and `_enforce_consistency` still reads the `DEV_AUTH_BYPASS`
env var (`deployment_profile.py:36,166-172`). The toggle is now overloaded to mean "dev mint
allowed" (`app.py:403`; `desktop_app.py:186`), so one field carries two meanings and a dead env
check remains. _evidence: deployment_profile.py:33-45,159-172; app.py:400-404; desktop_app.py:182-187._ _suggestion: rename the toggle to `dev_mint_allowed` and delete the `_enforce_consistency` `DEV_AUTH_BYPASS` branch._

**F11 — [refactor | low | confirmed]** The composition root reaches into private attributes
across module boundaries repeatedly under `# noqa: SLF001`: `projects_service._membership_port`
(reused 5×: `app.py:1822,1838,1863,1875,1908,1930`), `session_service._auth_secret`
(`app.py:679`), and it monkey-patches `routines_service._project_allowlist_lookup`
(`app.py:1808`). This coupling is invisible to the type checker and breaks silently on refactor.
_evidence: app.py:679,1808,1822,1838,1863,1875,1908,1930._ _suggestion: expose public `membership_port`/`auth_secret` accessors and a public setter for the allowlist bridge._

**F12 — [efficiency | low | confirmed]** `AuditChainSigner.from_env(...)` is reconstructed on
**every** audit append in both Postgres stores — re-reading env and re-deriving the signing key
per write (`store.py:661,1147`) — whereas the in-memory stores cache one signer per `_AuditChain`
(`store.py:104-107`). Per-append signer construction is wasted work on the hot audit path.
_evidence: store.py:661,1147 vs 104-107._ _suggestion: build the signer once per store instance (constructor) and reuse it._
