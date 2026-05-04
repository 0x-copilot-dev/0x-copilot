# Plan: Database, Identity, Token Usage, and Deployment Hardening

## Context

This product is targeted at banks and government orgs and must be deployable in two modes from the same codebase: **multi-tenant SaaS** and **single-tenant on-prem** (both Helm chart and Docker Compose ship the same container image). Three concurrent gaps block that:

1. **No real identity infrastructure.** The system trusts an HMAC bearer token minted _outside_ itself. There is no `users`, `organizations`, `sessions`, `auth_providers`, `mfa_factors`, or `login_attempts` table. There is no login UI. `DEV_AUTH_BYPASS=true` hardcodes `org_123/user_123`. The MCP OAuth/PKCE infrastructure that already exists is for _connector_ OAuth and is not reusable as-is for _user_ SSO. Bank/gov deploys require SAML+SCIM+phishing-resistant MFA; SaaS deploys need Google/OIDC. **User wants both tracks in parallel.**
2. **Token usage is captured but not queryable at scale.** `TokenUsageExtractor` already pulls `input/output/cached_input` from Anthropic/OpenAI/Google responses, and `AssistantPerformanceMetrics` is attached to `RUN_COMPLETED`/`FINAL_RESPONSE` events — but it lives only inside `runtime_events.payload_json_redacted` JSONB. There is no denormalized usage table, no per-LLM-call rows for subagent attribution, no cost calculation, no `/context` or `/usage` commands, and the existing per-tool budget is a prompt hint rather than code-enforced.
3. **DB has confirmed anti-patterns and missing prod controls.** `PostgresMcpStore.put_token` does DELETE-then-INSERT (not atomic). `agent_runs.row_version` and `runtime_memory_items.version` exist but aren't enforced (dead optimistic-lock infra). `services/backend` audit writes are not in the same transaction as the primary write. `services/backend` uses _sync_ psycopg with implicit transactions while `services/ai-backend` uses async — fine, but inconsistent. `ManagedSecretTokenVault` is a `raise RuntimeError` stub. There is no Postgres RLS, no field-level encryption for PII, no SIEM export, no retention sweeper, no migration tooling (raw SQL strings only), no backup/restore drill, no read-replica routing, no statement-level observability.

The plumbing that _is_ there is genuinely solid: 19 ai-backend tables all leading with `org_id`, working event sourcing with `SELECT FOR UPDATE` on `agent_runs` plus monotonic `sequence_no`, an outbox with worker locks, append-only `runtime_audit_log`, `runtime_legal_holds`, `runtime_deletion_evidence`. We're hardening and extending, not rewriting.

The plan is organized into three concurrent **tracks** (~30 PRs total). Each PR ships its functional spec MD before code per the spec-first convention in [services/ai-backend/CLAUDE.md](services/ai-backend/CLAUDE.md). Tracks A and B can be staffed independently; Track C is foundational and must lead. Recommended merge order is at the bottom.

---

## Architecture decisions (apply to all PRs)

- **Identity source of truth lives in `services/backend`** (which already owns auth-adjacent state — token vault, MCP OAuth). `services/backend-facade` exposes the public `/v1/auth/*` surface and proxies to `/internal/v1/auth/*`. Hard service boundary preserved: facade never imports backend's Python.
- **Same bearer-token wire shape, server-issued.** The existing HMAC-SHA256 `payload.signature` token format stays — but it now carries an `sid` claim bound to a server-side `sessions` row. Revocation is instant (DB flip), no JWT rotation needed.
- **Same code, different deployment config.** A single `ENTERPRISE_DEPLOYMENT_PROFILE` env var resolves a typed `DeploymentFeatureToggles` object in each service. Profiles: `saas_multi_tenant`, `single_tenant_managed`, `single_tenant_self_hosted`. Toggles include `dev_auth_bypass_allowed`, `require_kms_token_vault`, `require_field_level_encryption`, `siem_export_required`, `enforce_rls`, `allow_self_signup`, `allow_vendor_telemetry`. Same container image; behavior gated by config.
- **Cost in `BIGINT micro_usd` everywhere.** No floats on the persistence path. `1 USD = 1_000_000 micro`. Pricing snapshotted per-row via `pricing_id` so retroactive price changes never mutate historical cost.
- **`org_id NOT NULL` on every new tenant-scoped table; every compound index leads with `org_id`.** In single-tenant deploys `org_id` always equals the singleton org id — column stays for code uniformity.
- **Shared constants only via `packages/service-contracts`.** No new shared Python package. New shared headers, scope enums, deployment-profile enum values land there as constants.
- **Spec-first per CLAUDE.md.** Every PR ships its spec under `services/<svc>/docs/specs/<topic>/PR-NN-<name>.md` before code, with sections: Purpose · Boundary · Tables · Endpoints · Trust model · Failure semantics · Tenant isolation · Audit · Compliance evidence · Test plan.

---

## Track A — Identity & Access (10 PRs)

User confirmed: ship Google/OIDC and SAML/SCIM **in parallel**. PRs A1–A2 are foundation; A3/A4 (OIDC) and A5/A7 (SAML/SCIM) split here.

### A1 — `feat(backend): user/org/role schema foundation`

- **Spec:** [services/backend/docs/specs/auth/A1-user-org-schema.md](services/backend/docs/specs/auth/A1-user-org-schema.md)
- **Tables:** `organizations`, `users` (CITEXT email; `(org_id, lower(email)) UNIQUE WHERE deleted_at IS NULL`), `organization_members`, `roles` (`org_id NULL` ⇒ system role; seed `admin`/`employee`/`auditor`/`service`), `role_assignments` (append-only with `revoked_at`), `auth_providers` (per-org IdP catalog; `encrypted_client_secret` via TokenVault), `identity_audit_events` (append-only, mirrors `mcp_audit_events` pattern). **Login_attempts table from A8 is pulled forward into this PR** so A3–A7 emit into it from day one.
- **Behavior:** none. Records, repos, in-memory + Postgres adapters only.
- **Critical files:** [services/backend/src/backend_app/migrations.py](services/backend/src/backend_app/migrations.py), [services/backend/src/backend_app/contracts.py](services/backend/src/backend_app/contracts.py), new `services/backend/src/backend_app/identity/store.py`.

### A2 — `feat(backend,facade): server-issued sessions and bearer-token binding`

- **Spec:** [services/backend/docs/specs/auth/A2-sessions-internal.md](services/backend/docs/specs/auth/A2-sessions-internal.md), [services/backend-facade/docs/specs/auth/A2-sessions-public.md](services/backend-facade/docs/specs/auth/A2-sessions-public.md)
- **Tables:** `sessions(session_id PK, org_id, user_id, token_hash UNIQUE WHERE revoked_at IS NULL, roles JSONB, permission_scopes JSONB, mfa_satisfied_at NULL, expires_at, revoked_at NULL, last_seen_at, client_ip NULL, user_agent NULL, device_label NULL)`. Token hashed with SHA-256, never plaintext.
- **Behavior:** facade still verifies HMAC signature locally, then calls backend `POST /internal/v1/auth/sessions/touch` per request (request-scoped cache only, no shared state). Old tokens without `sid` claim accepted behind `REQUIRE_SESSION_BINDING=false`; flipped on after A3+ ships.
- **Endpoints:** facade `/v1/auth/session`, `/v1/auth/sessions` (list mine), `DELETE /v1/auth/sessions/{id}` (revoke), `POST /v1/auth/logout`. Backend `/internal/v1/auth/sessions/{create,touch,revoke}`. Bootstrap-only `dev-mint` endpoint.
- **Critical files:** [services/backend-facade/src/backend_facade/auth.py:98-116](services/backend-facade/src/backend_facade/auth.py#L98-L116), [services/backend-facade/src/backend_facade/app.py:48](services/backend-facade/src/backend_facade/app.py#L48), [services/backend/src/backend_app/app.py](services/backend/src/backend_app/app.py).

### A3 — `feat(backend,facade): OIDC SSO (Google + generic)`

- **Spec:** [services/backend/docs/specs/auth/A3-oidc.md](services/backend/docs/specs/auth/A3-oidc.md), [services/backend-facade/docs/specs/auth/A3-oidc-callback.md](services/backend-facade/docs/specs/auth/A3-oidc-callback.md)
- **Tables:** `oidc_authentications` (state machine; `state UNIQUE`; `nonce`, `code_verifier`, `expires_at`, `consumed_at`), `oidc_identities` (`(provider_id, subject) UNIQUE WHERE unlinked_at IS NULL`), `oidc_refresh_tokens` (`encrypted_refresh_token` via TokenVault), `oidc_jwks_cache`.
- **Reuse:** extract a shared `_pkce.py` helper from [services/backend/src/backend_app/mcp_oauth.py](services/backend/src/backend_app/mcp_oauth.py); both MCP OAuth and OIDC import it. Verify ID-token signature against JWKS with rotation.
- **JIT provisioning** gated by `identity_policy.auto_provision_user`. Group-claim → role mapping per provider config.
- **Endpoints:** `GET /v1/auth/oidc/{provider_id}/start`, `GET /v1/auth/oidc/callback`. Backend internal mirrors.

### A4 — `feat(backend): local password auth + bootstrap admin`

- **Spec:** [services/backend/docs/specs/auth/A4-local-password.md](services/backend/docs/specs/auth/A4-local-password.md)
- **Tables:** `local_credentials` (argon2id encoded hash incl. salt/params; `previous_hashes JSONB` for reuse window), `password_policies` (per-org), `password_reset_tokens` (token hash only).
- **Off by default in SaaS for new orgs**, on for the bootstrap admin in single-tenant. Banks set `local_password_enabled=false` via `identity_policy`.
- **Endpoints:** `/v1/auth/login`, `/v1/auth/password/reset/{request,confirm}`, `/v1/auth/password/change`. Constant-time response on unknown email to prevent enumeration.
- **Dep:** `argon2-cffi` in [services/backend/requirements.txt](services/backend/requirements.txt).

### A5 — `feat(backend,facade): SAML 2.0 SSO`

- **Spec:** [services/backend/docs/specs/auth/A5-saml.md](services/backend/docs/specs/auth/A5-saml.md), [services/backend-facade/docs/specs/auth/A5-saml-acs.md](services/backend-facade/docs/specs/auth/A5-saml-acs.md)
- **Tables:** `saml_authentications` (`assertion_id UNIQUE` for replay guard), `saml_identities` (`(provider_id, name_id) UNIQUE WHERE unlinked_at IS NULL`).
- **`auth_providers.config` for SAML:** `idp_entity_id`, `idp_sso_url`, `idp_x509_cert`, `sp_entity_id`, `sp_acs_url`, `attribute_map`, `allow_idp_initiated`, optional `sp_signing_key_ref`/`sp_decryption_key_ref` (vault refs).
- **Endpoints:** facade `/v1/auth/saml/{provider_id}/{start,acs,metadata}`. SP-initiated default; IdP-initiated if `allow_idp_initiated=true`.
- **Dep:** `python3-saml` or `pysaml2`.

### A6 — `feat(backend,facade): MFA (TOTP + WebAuthn)`

- **Spec:** [services/backend/docs/specs/auth/A6-mfa.md](services/backend/docs/specs/auth/A6-mfa.md)
- **Tables:** `mfa_factors`, `totp_secrets` (encrypted via TokenVault, `last_step` replay guard), `webauthn_credentials` (`credential_id_b64 UNIQUE`, COSE public key, `sign_count`), `mfa_challenges` (`nonce UNIQUE`, `expires_at`), `mfa_recovery_codes` (sha256 hashed).
- **Session gating:** when org policy requires MFA, login mints a session with `mfa_satisfied_at=NULL` and `permission_scopes=['mfa:pending']`; protected routes 401 until `POST /v1/auth/mfa/verify`. Step-up: routes can declare `requires_recent_mfa: 5m`.
- **Deps:** `pyotp`, `py_webauthn`.

### A7 — `feat(backend): SCIM 2.0 user/group provisioning`

- **Spec:** [services/backend/docs/specs/auth/A7-scim.md](services/backend/docs/specs/auth/A7-scim.md)
- **Tables:** `scim_tokens` (sha256 hashed bearer; shown once at creation like a GitHub PAT), `scim_external_ids`, `scim_groups`, `scim_group_members`. ALTER `users` ADD `scim_external_id TEXT NULL` with partial unique index.
- **Routes:** `/scim/v2/Users`, `/scim/v2/Groups`, `/scim/v2/{ServiceProviderConfig,Schemas,ResourceTypes}`. Routed through facade for boundary consistency, but with SCIM bearer (org token) instead of user bearer.
- **Bank deploy mode:** `scim_required=true` ⇒ local password disabled, OIDC JIT provisioning rejected with "user not provisioned via SCIM".

### A8 — `feat(backend): account lockout + rate limiting on login attempts`

- **Spec:** [services/backend/docs/specs/auth/A8-lockout.md](services/backend/docs/specs/auth/A8-lockout.md)
- **Tables:** `login_attempts` (table created in A1; populated by A3–A7), `account_lockouts` (single active lockout per user via partial unique index), `lockout_policies` (per-org thresholds).
- **Sliding-window rate limit** per `(org_id, email)` and per `(ip)`; lockout after N failures within window; auto-unlock after cooldown; admin "force unlock" leaves audit row.
- **Two-phase rollout:** ship behind `enforce_lockout=false` for one release for telemetry, then flip true.

### A9 — `feat(frontend): login page, auth context, MFA prompts, session-aware routing`

- **Spec:** [apps/frontend/docs/specs/auth/A9-login-ux.md](apps/frontend/docs/specs/auth/A9-login-ux.md)
- **Routes:** `/login`. New `AuthContext` provider replaces inline identity state in [apps/frontend/src/app/App.tsx:113-146](apps/frontend/src/app/App.tsx#L113-L146). 401 from any API → navigate to `/login` instead of "Loading session...".
- **IdP picker** fetches `GET /v1/auth/providers?org_slug=`; renders enabled buttons (Google / SSO / email). Bank deploys hide signup + reset.
- **Settings → Account:** active sessions list with revoke; "log out other devices".
- **New files:** `apps/frontend/src/api/authApi.ts`, `apps/frontend/src/features/auth/{LoginScreen,MfaPrompt,AuthContext}.tsx`, `apps/frontend/src/features/settings/AccountSessionsPanel.tsx`. Update [packages/api-types/src/index.ts](packages/api-types/src/index.ts).

### A10 — `feat(backend,ai-backend): RBAC enforcement at every route`

- **Spec:** [services/backend/docs/specs/auth/A10-rbac.md](services/backend/docs/specs/auth/A10-rbac.md), [services/ai-backend/docs/specs/auth/A10-rbac.md](services/ai-backend/docs/specs/auth/A10-rbac.md)
- **Scope catalog:** new `packages/service-contracts/src/enterprise_service_contracts/scopes.py` constants: `mcp:read`, `mcp:write`, `skills:read`, `skills:write`, `connectors:auth`, `runtime:use`, `admin:users`, `admin:idp`, `admin:audit_export`, `audit:read`, `mfa:pending`.
- **`RequireScopes(*scopes)` and `RequireRoles(*roles)` FastAPI dependencies** in both services. Annotate every existing route in [services/backend/src/backend_app/app.py](services/backend/src/backend_app/app.py) and [services/ai-backend/src/runtime_api/http/routes.py](services/ai-backend/src/runtime_api/http/routes.py).
- **Default-deny:** static check in CI fails build if any FastAPI route is unannotated.
- **Two-phase rollout:** `RBAC_MODE=audit` (log denies, pass through) → `RBAC_MODE=enforce`. Riskiest auth PR; ships last.

---

## Track B — Token Usage, Metering, Budgets (8 PRs)

### B1 — `feat(ai-backend): denormalized run usage table`

- **Spec:** [services/ai-backend/docs/specs/usage/B1-runtime-run-usage.md](services/ai-backend/docs/specs/usage/B1-runtime-run-usage.md)
- **Table:** `runtime_run_usage(id=run_id PK, org_id, user_id, conversation_id, run_id UNIQUE, model_provider, model_name, input_tokens, output_tokens, cached_input_tokens, total_tokens, chunk_count, first_token_ms, duration_ms, started_at, completed_at, status, schema_version, retention_until, pii_purged_at, created_at)`. Five compound indexes leading with `org_id`. **Retention decoupled from messages:** `pii_purged_at` flag instead of row delete on user-history deletion (preserves billing/audit even after PII purge).
- **Worker write hook** in [services/ai-backend/src/runtime_worker/handlers/run.py:271-287](services/ai-backend/src/runtime_worker/handlers/run.py#L271-L287) (RUN_COMPLETED block); `INSERT ... ON CONFLICT (run_id) DO NOTHING` keyed by `run_id` for idempotency.
- **Backfill script** `services/ai-backend/scripts/usage/backfill_run_usage.py` — operator-run, opt-in, idempotent, reads historical `runtime_events` payloads.

### B2 — `feat(ai-backend): per-step usage events and per-LLM-call usage table`

- **Spec:** [services/ai-backend/docs/specs/usage/B2-per-step-usage.md](services/ai-backend/docs/specs/usage/B2-per-step-usage.md)
- **Table:** `runtime_model_call_usage(id PK, org_id, run_id, conversation_id, parent_event_id, trace_id, task_id NULL, subagent_id NULL, model_provider, model_name, input_tokens, output_tokens, cached_input_tokens, total_tokens, duration_ms, created_at)`. Indexes on `(org_id, run_id, created_at)`, `(org_id, trace_id)`, `(org_id, task_id)`.
- **New event:** `MODEL_CALL_COMPLETED` carrying `AssistantPerformanceMetrics`. Existing `SUBAGENT_COMPLETED` payload gets optional `usage: AssistantSubagentUsageRollup`.
- **`AssistantRunMetrics` refactor:** add `PerCallTokenAccumulator` keyed by AIMessage id so subagent and main-graph calls can be attributed separately. Critical for the `/context` "where did the tokens go" answer.

### B3 — `feat(ai-backend): pricing catalog and cost calculation`

- **Spec:** [services/ai-backend/docs/specs/usage/B3-pricing-and-cost.md](services/ai-backend/docs/specs/usage/B3-pricing-and-cost.md)
- **Table:** `model_pricing(id, provider, model_name, region DEFAULT 'global', effective_from, effective_until, input_per_1m_micro_usd BIGINT, output_per_1m_micro_usd BIGINT, cached_input_per_1m_micro_usd BIGINT, context_window_tokens, pricing_source, pricing_version, created_at)`. Partial unique active index. ALTER `runtime_run_usage` and `runtime_model_call_usage` to add `cost_micro_usd BIGINT`, `pricing_id`, `pricing_version` (all nullable).
- **YAML seeds** under `services/ai-backend/src/agent_runtime/pricing/seeds/{anthropic,openai,google}-2026-q1.yaml`. `seed_pricing.py` operator script. Round-half-to-even at micro-USD boundary.
- **Pricing is versioned and snapshotted per row.** Re-pricing today's model never mutates yesterday's cost.

### B4 — `feat(ai-backend,facade): daily rollups + /v1/usage/* read endpoints`

- **Spec:** [services/ai-backend/docs/specs/usage/B4-aggregation-endpoints.md](services/ai-backend/docs/specs/usage/B4-aggregation-endpoints.md)
- **Tables:** `runtime_usage_daily_user(org_id, user_id, day, model_provider, model_name, runs_count, input_tokens BIGINT, output_tokens BIGINT, cached_input_tokens BIGINT, total_tokens BIGINT, cost_micro_usd BIGINT NULL, refreshed_at, PK(org_id,user_id,day,model_provider,model_name))` and `runtime_usage_daily_org` (adds `distinct_users`). **Tables, not materialized views** — explicit idempotent UPSERTs avoid concurrent-refresh foot-guns.
- **Rollup loop** in `services/ai-backend/src/runtime_worker/usage_rollup_loop.py`. Recomputes last 2 days every N minutes (idempotent); finalized after late-arrival window.
- **Endpoints:** `GET /v1/usage/me?period={today|7d|30d|month}`, `/v1/usage/me/conversations`, `/v1/usage/runs/{run_id}`, `/v1/usage/conversations/{conversation_id}`, `/v1/usage/org` (admin scope, group_by=user|model|conversation). Cold-start fallback: when rollups empty, query `runtime_run_usage` directly with a 30-day cap and a clear log.

### B5 — `feat: /context slash command`

- **Spec:** [services/ai-backend/docs/specs/usage/B5-context-command.md](services/ai-backend/docs/specs/usage/B5-context-command.md)
- **Endpoint:** `GET /v1/agent/conversations/{conversation_id}/context` → `{model: {provider, name, context_window_tokens}, current: {last_run_id, input/output/cached_input/available_tokens, headroom_pct (integer 0..100)}, breakdown: {by_call[], by_subagent[], compression_events[]}}`. Joins `runtime_run_usage` (latest) + `runtime_model_call_usage` (per-call) + `runtime_compression_events` + `model_pricing.context_window_tokens`. Server returns integer percent — UI never derives floats.
- **Frontend:** new `/context` slash command in [apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx](apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx); opens `ContextPanel` side panel (no user message added).

### B6 — `feat: /usage slash command and panel`

- **Spec:** [services/ai-backend/docs/specs/usage/B6-usage-command.md](services/ai-backend/docs/specs/usage/B6-usage-command.md)
- **Frontend only.** Today / 7d / 30d / Month with model + top-conversation breakdown. Cost section hidden when all rows return `cost_micro_usd: null` (single-tenant deploys without pricing). Single `formatMicroUsd(value)` helper — never re-implemented inline.
- **Refactor backlog:** [apps/frontend/src/features/chat/utils/activityDataBuilders.ts:54-75](apps/frontend/src/features/chat/utils/activityDataBuilders.ts#L54-L75) currently hides input/cached tokens; expose them in `metricRows`.

### B7 — `feat(ai-backend): per-org and per-user budget enforcement (atomic CAS)`

- **Spec:** [services/ai-backend/docs/specs/usage/B7-budgets.md](services/ai-backend/docs/specs/usage/B7-budgets.md)
- **Tables:** `usage_budgets(id, org_id, user_id NULL, scope IN ('org','user'), period IN ('day','month'), enforcement IN ('soft','hard'), limit_micro_usd NULL, limit_tokens NULL, status, ...)`, `usage_budget_state(budget_id, period_start, period_end, current_spend_micro_usd, current_spend_tokens, row_version, last_charged_run_id, updated_at, PK(budget_id, period_start))`. Optional `usage_budget_reservations` table (with TTL reaper) for pre-flight reservations to prevent budget overruns under concurrency.
- **Atomicity:** `charge_budget` uses compare-and-swap on `row_version` AND `last_charged_run_id IS DISTINCT FROM $run_id` for idempotency. Pattern proven in `agent_runs.row_version` (this PR finally uses that infrastructure too — see C3).
- **New events:** `BUDGET_WARNING` (soft cap crossed), `RUN_REJECTED` (hard cap denied — distinct from `RUN_FAILED` so UI shows the right message).
- **Pre-run check** in [services/ai-backend/src/runtime_worker/handlers/run.py](services/ai-backend/src/runtime_worker/handlers/run.py) `handle()` top, before status→RUNNING.

### B8 — `feat(ai-backend): code-enforced per-tool token budget`

- **Spec:** [services/ai-backend/docs/specs/usage/B8-tool-budget.md](services/ai-backend/docs/specs/usage/B8-tool-budget.md)
- **Replaces prompt-only budget** at [services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py:41-72](services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py#L41-L72) with code enforcement.
- **Table:** `runtime_tool_budgets(id, org_id NULL=global, tool_name TEXT='*'=all, max_calls_per_run, max_input_tokens_per_call NULL, max_input_tokens_per_run NULL, enforcement, ...)`. The existing `RUNTIME_TOOL_CALL_BUDGET` becomes the seed default row.
- **Middleware** wraps tool execution; checks against [services/ai-backend/src/runtime_worker/tool_call_ledger.py](services/ai-backend/src/runtime_worker/tool_call_ledger.py) (extend with `input_tokens` field). On hard violation, returns `ToolOutcome.REJECTED` with new `ToolErrorCode.TOOL_BUDGET_EXCEEDED` — model sees a safe error and can proceed.

---

## Track C — Deployment Models & DB Hardening (12 PRs)

Sequencing principle: foundational config + tooling first (C1, C2, C4, C3), then defense-in-depth (C5), then production secrets (C6, C7), then operations (C8, C9, C10, C11, C12).

### C1 — `feat(deployment): ENTERPRISE_DEPLOYMENT_PROFILE config`

- **Spec:** [docs/specs/deployment/C1-deployment-profiles.md](docs/specs/deployment/C1-deployment-profiles.md)
- **Per-service module** (no shared package — service boundaries hard): `services/ai-backend/src/agent_runtime/deployment/profile.py`, `services/backend/src/backend_app/deployment_profile.py`, `services/backend-facade/src/backend_facade/deployment_profile.py`. Enum + `DeploymentFeatureToggles` constants mirrored into `packages/service-contracts/src/enterprise_service_contracts/deployment_profile.py`.
- **`DEV_AUTH_BYPASS=true` rejected** for managed/self-hosted profiles even when `FACADE_ENVIRONMENT=development` — closes the existing ambiguity at [services/backend-facade/src/backend_facade/auth.py:76-77](services/backend-facade/src/backend_facade/auth.py#L76-L77).
- **Helm + Compose ship the same image.** Profile-specific config injected via env vars at deploy time. Helm chart and `docker-compose.prod.yml` produced as artifacts in this PR.

### C2 — `chore(persistence): adopt yoyo-migrations for backend and ai-backend`

- **Spec:** [docs/specs/persistence/C2-migration-tooling.md](docs/specs/persistence/C2-migration-tooling.md)
- **Why first:** all of A1–A8, B1–B8, C5–C8 add migrations. Cannot keep growing raw SQL strings in [services/backend/src/backend_app/migrations.py](services/backend/src/backend_app/migrations.py) and [services/ai-backend/src/agent_runtime/persistence/schema/postgres.py](services/ai-backend/src/agent_runtime/persistence/schema/postgres.py).
- **Why yoyo over alembic:** ai-backend uses raw SQL, not SQLAlchemy ORM; yoyo is a thin runner that takes `.sql` files with up/down. Each service gets its own `migrations/` dir; `_yoyo_migration` table is service-local — no cross-service schema sharing.
- **`MANIFEST.lock` checksum file** per service; CI fails if migrations added without entry. Production runs migrations as a deploy step, not on app startup (`BACKEND_MIGRATIONS_AUTO_APPLY=false` in prod).

### C3 — `fix(persistence): atomic upserts + transaction boundaries + optimistic locking`

- **Spec:** [docs/specs/persistence/C3-atomicity-fixes.md](docs/specs/persistence/C3-atomicity-fixes.md)
- **Combines three small fixes** that share the same review surface and don't deserve separate PRs:
  1. **`PostgresMcpStore.put_token` becomes `INSERT ... ON CONFLICT (server_id) DO UPDATE`** with `WHERE org_id = EXCLUDED.org_id` cross-tenant guard. Adds unique index on `mcp_auth_connections(server_id)`.
  2. **Multi-statement service ops wrapped in transactions.** Audit confirmed sites in [services/backend/src/backend_app/service.py](services/backend/src/backend_app/service.py): `create_skill+_audit` (L673–702), `update_skill+_audit` (L722–763), `delete_skill+_audit`, `_ensure_preloaded_skills` (L880+), all MCP CRUD that calls `append_audit`. Refactor store methods to accept optional `conn`; service composes them inside `with conn.transaction()`.
  3. **Enforce dead optimistic-lock columns.** `agent_runs.row_version` and `runtime_memory_items.version` get CAS on UPDATE; new `ConcurrentRunUpdateError` raised on rowcount=0. Worker retries with bounded backoff.

### C4 — `feat(persistence): connection pool tuning, timeouts, and pool metrics`

- **Spec:** [docs/specs/persistence/C4-pool-tuning.md](docs/specs/persistence/C4-pool-tuning.md)
- **Env-driven** `RUNTIME_DB_POOL_{MIN,MAX}_SIZE`, `_ACQUIRE_TIMEOUT_SECONDS`, `_STATEMENT_TIMEOUT_MS` (default 10000), `_LOCK_TIMEOUT_MS` (default 3000), `_IDLE_IN_TXN_TIMEOUT_MS` (default 30000). Mirrored as `BACKEND_DB_*`.
- Currently [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) hardcodes `statement_timeout=10000 lock_timeout=3000`; `services/backend` has no timeouts at all.
- **`application_name = '<service>:<role>'`** server-side so `pg_stat_activity` is greppable per service+role.
- **Prometheus pool metrics:** `db_pool_{size,in_use,waiting}`, `db_pool_acquire_seconds_p50/p99`.

### C5 — `feat(persistence): postgres row-level security for tenant isolation`

- **Spec:** [docs/specs/persistence/C5-rls-tenant-isolation.md](docs/specs/persistence/C5-rls-tenant-isolation.md)
- **Defense-in-depth.** App still does `WHERE org_id = …`; the DB enforces it too. If app code forgets a WHERE clause, the DB refuses cross-tenant reads.
- **All 19 ai-backend tenant-scoped tables + all 6 backend tables** get `ENABLE ROW LEVEL SECURITY` and a `tenant_isolation` policy: `org_id = current_setting('app.current_org_id', true)`.
- **Two roles:** `enterprise_app` (RLS enforced, used by app pools), `enterprise_admin` (BYPASSRLS, used only by yoyo runner with `app.is_migration='on'`).
- **Connection-checkout helper** `_tenant_connection(org_id)` injects `set_config('app.current_org_id', org_id, true)` once per checkout. Worker uses `_role_connection('worker')` for outbox claims (separate `worker_can_read_all` policy).
- **Critical test:** integration test connects as `enterprise_app`, sets org_a context, inserts; switches to org_b context; SELECT/UPDATE/DELETE all return 0 rows for org_a's data.
- **3-stage rollout:** add policies (disabled) → app sets org var (no enforcement) → enable RLS in a separate small PR after shadow-mode validation.

### C6 — `feat(security): managed token vault — KMS adapter framework + AWS KMS`

- **Spec:** [docs/specs/deployment/C6-byok-kms.md](docs/specs/deployment/C6-byok-kms.md)
- **Replaces** the `raise RuntimeError` stub at [services/backend/src/backend_app/token_vault.py:104](services/backend/src/backend_app/token_vault.py#L104) with a real adapter framework. AWS KMS shipped here; **C6a/C6b/C6c follow-ups** for GCP KMS, Azure Key Vault, HashiCorp Vault (same interface, separate small PRs).
- **`MCP_TOKEN_VAULT_BACKEND` ∈ {local, aws_kms, gcp_kms, azure_kv, hashicorp_vault}.** `MCP_TOKEN_VAULT_KMS_KEY_ID` for the CMK reference. Profile enforces non-`local` for managed/self-hosted.
- **ALTER `mcp_auth_connections` ADD `kms_key_id TEXT`** so per-row key tracking enables rotation. Existing Fernet ciphertexts continue to work (legacy XOR fallback path stays).
- **`boto3` under `[kms-aws]` extras** — don't bloat base image.
- **Fail-closed on writes** if KMS unavailable; reads use 5-min cache then fail-closed (cache disabled in self-hosted profile per customer audit policy).
- **Migration helper script** `services/backend/scripts/rotate_token_vault.py` for one-shot cutover.

### C7 — `feat(persistence): field-level encryption for sensitive PII columns`

- **Spec:** [docs/specs/persistence/C7-field-level-encryption.md](docs/specs/persistence/C7-field-level-encryption.md)
- **Envelope encryption with per-row DEKs wrapped by KMS CMK** (so CMK rotation is cheap; only DEK cache invalidation needed).
- **Targeted columns:** `agent_messages.{content_text, content_json, metadata_json}`, `runtime_audit_log.metadata_json_redacted`, `runtime_events.{payload_json_redacted, metadata_json_redacted}`, `runtime_subagent_results.response_text`, `runtime_tool_invocations.{args_json_redacted, result_summary_json_redacted}`, `runtime_memory_items.content_summary`. Plus a new `runtime_context_payload_blobs` side-table for `runtime_context_payloads` blob bodies (keeps the metadata row queryable).
- **Excluded:** ids, timestamps, status enums, FK columns, and indexed columns used in WHERE clauses (`org_id`, `user_id`, `conversation_id`, `run_id`, `trace_id`).
- **AAD = `f"{table}|{column}|{org_id}".encode()`** — prevents ciphertext-swap across columns or tenants.
- **`encryption_version SMALLINT DEFAULT 0`** column on each affected table; reads tolerate both `0` (plaintext) and `1` (envelope-v1) during the cutover.
- **3-phase rollout:** schema + tolerant reads → flip writes to v1 → background backfill → separate PR removing the plaintext-tolerant read path after `min(encryption_version)=1`.
- **DEK cache** scoped per `org_id`, 60s TTL.

### C8 — `feat(persistence): retention sweeper + checkpoint pruning`

- **Spec:** [docs/specs/persistence/C8-retention-sweeper.md](docs/specs/persistence/C8-retention-sweeper.md)
- **Table:** `retention_policies(id, org_id, scope IN ('org','user','conversation','assistant'), resource_id NULL, kind IN ('messages','events','context_payloads','checkpoints','memory_items'), ttl_seconds, ...)` with unique `(org_id, scope, COALESCE(resource_id,''), kind)`.
- **Background job** in `services/ai-backend/src/runtime_worker/jobs/retention_sweeper.py`. Chunked DELETE/tombstone, runs every `RETENTION_SWEEP_INTERVAL_SECONDS` (default 600). Per-table strategies:
  - `runtime_context_payloads`: hard delete where `retention_until < now()` (column already exists, finally enforced).
  - `runtime_checkpoints`: keep latest N per `(thread_id, namespace)` (default 10) + anything in policy window. Currently unbounded growth.
  - `runtime_events`, `agent_messages`: tombstone first; hard delete after 30-day grace. Preserves audit.
- **Respects `runtime_legal_holds`** — no delete for held resources, no `runtime_deletion_evidence` row written for held rows.
- **Records each delete batch** in `runtime_deletion_evidence` + `runtime_audit_log`.
- **Defaults by profile:** SaaS=365d, single_tenant=customer-defined (sweeper no-op without policies seeded).

### C9 — `feat(audit): SIEM export pump (Splunk HEC, Elastic, syslog/CEF, file)`

- **Spec:** [docs/specs/deployment/C9-siem-export.md](docs/specs/deployment/C9-siem-export.md)
- **Outbox-style cursor**, exactly-once delivery via stable composite event id `{org_id}:{event_id}`.
- **Tables in backend schema:** `siem_export_cursors(exporter_name PK, source, last_event_id, last_processed_at)`, `siem_export_dead_letters(...)`.
- **Cross-service read:** new ai-backend route `GET /internal/v1/audit/cursor?after_id=&limit=` (strict service-token auth). Backend's pump consumes both `mcp_audit_events` (local) and ai-backend's `runtime_audit_log` (HTTP). **No cross-service DB reads** (preserves boundary).
- **`SIEM_EXPORT_BACKEND` ∈ {null, splunk_hec, elastic, syslog_cef, file}.** Profile rejects `null` for managed/self-hosted. **`file` exporter** added for air-gapped Compose deployments — writes JSONL to a mounted volume, customer ships it out of band.

### C10 — `feat(persistence): read-replica routing for analytics`

- **Spec:** [docs/specs/persistence/C10-read-replica.md](docs/specs/persistence/C10-read-replica.md)
- **Optional `RUNTIME_DB_READ_REPLICA_URL`.** New `_read_only_connection()` context manager picks replica when present. `@reader` decorator marks methods that go to replica; CI check asserts no `INSERT|UPDATE|DELETE` in `@reader` methods.
- Used by `/v1/usage/*` endpoints (B4) and other analytics reads. Run-status queries stay on primary.
- **Replica health check** with `RUNTIME_DB_READ_REPLICA_MAX_LAG_SECONDS` (default 30) — failover to primary on lag exceed.

### C11 — `feat(observability): pg_stat_statements + slow query metrics`

- **Spec:** [docs/specs/persistence/C11-statement-observability.md](docs/specs/persistence/C11-statement-observability.md)
- Assumes `pg_stat_statements` extension preinstalled by operator (most managed Postgres has it). Migration grants `enterprise_app` SELECT.
- **Per-tenant tagging via `application_name`** with first 8 chars of `sha256(org_id)` (full org_id never leaks to `pg_stat_activity`).
- **Slow-query OTel spans** when statement exceeds `RUNTIME_DB_SLOW_QUERY_MS` (default 500). Test asserts query text in metrics never contains plaintext PII.

### C12 — `docs(persistence): backup/restore documented and tested in CI`

- **Spec:** [docs/specs/persistence/C12-backup-restore.md](docs/specs/persistence/C12-backup-restore.md), runbook [docs/ci-cd/runbooks/postgres-restore.md](docs/ci-cd/runbooks/postgres-restore.md)
- **CI workflow** `.github/workflows/postgres-restore-drill.yml` (manual + weekly): boots Postgres in container, restores from a tiny checked-in `pg_dump` test fixture, runs assertion suite (`SELECT count(*)` per table matches manifest).
- **Per-profile restore runbook:** RDS/CloudSQL/Aurora PITR for SaaS; `pg_basebackup` + WAL archiving for self-hosted Helm; volume snapshot for self-hosted Compose. RPO/RTO targets per profile.
- **Without a passing restore CI run, "backup" is not a control.**

---

## Cross-cutting deliverables

- **Compliance control mapping doc** [docs/security/control-mapping.md](docs/security/control-mapping.md): one row per PR linking the change to the CLAUDE.md compliance section it satisfies.
- **Deployment topology doc** [docs/deployment/profiles.md](docs/deployment/profiles.md): the 3 profiles × the toggle matrix × Helm vs. Compose specifics.
- **Tracking docs** [docs/specs/auth/README.md](docs/specs/auth/README.md), [docs/specs/usage/README.md](docs/specs/usage/README.md), [docs/specs/deployment/README.md](docs/specs/deployment/README.md), [docs/specs/persistence/README.md](docs/specs/persistence/README.md): index of PRs in that track.
- **`packages/service-contracts` extensions:** `scopes.py` (RBAC scope enum, A10), `deployment_profile.py` (profile enum + toggle keys, C1).
- **`packages/api-types` extensions:** `AuthProvider`, `LoginRequest`, `MfaChallenge`, `Session`, `UsageTotals`, `UsageDailyRow`, `UsageMeResponse`, `RunUsageBreakdown`, `ConversationContextResponse`, `BUDGET_WARNING` and `RUN_REJECTED` event variants.

---

## Recommended merge order

Foundation gating: **C1 → C2** must land first (deployment profile + migration tooling) before any other PR adds a migration or branches behavior on profile.

```
Wave 0 (foundation):           C1, C2
Wave 1 (atomicity):            C3, C4
Wave 2 (auth foundation):      A1, A2
Wave 3 (auth + usage parallel):
  Auth-OIDC track:             A3, A4              ← engineer 1
  Auth-SAML/SCIM track:        A5, A7              ← engineer 2
  Usage track:                 B1, B2, B3, B4
  Defense-in-depth:            C5
Wave 4 (auth completion):      A6, A8, A9
Wave 5 (usage UX + budgets):   B5, B6, B7, B8
Wave 6 (security hardening):   C6, C7
Wave 7 (operations):           C8, C9, C10, C11
Wave 8 (RBAC + restore drill): A10, C12
```

Total: 30 PRs. A1, A2, B1–B4, C1–C5 are required for any production deploy. A5/A7/A6, C6/C7 are required for any bank/gov deploy. The rest is operational maturity.

---

## Verification

End-to-end verification per track:

**Track A:**

- `make dev` boots; visiting `http://localhost:5173` with no token → redirected to `/login`.
- Bootstrap admin path: `BOOTSTRAP_ADMIN_EMAIL=admin@example.com make dev` → first-run setup token appears in logs → log in → forced password change → admin dashboard.
- OIDC: configure Google as a provider via admin CLI → click "Sign in with Google" → callback creates user + session → access protected route succeeds.
- SAML: configure a fake IdP fixture → SP-initiated SSO round-trip → `assertion_id UNIQUE` rejects replay.
- MFA: enroll TOTP → org policy `mfa_required=true` → next login session is `mfa:pending` → 401 on `/v1/agent/conversations` until `/v1/auth/mfa/verify`.
- SCIM: provision user via fake Okta SCIM client → user appears in DB → `active=false` PATCH → user `deleted_at` set.
- Lockout: 5 bad-password attempts → `423 Locked` → wait cooldown → 200.
- RBAC: route requiring `admin:users` returns 403 with `audit_kind='denied'` row when missing.

**Track B:**

- Send a message → `/context` slash command shows current input/output/cached tokens + headroom percent.
- Run several conversations → `/usage` shows daily breakdown; cost section visible if pricing seeded.
- Set per-user $1 daily hard budget → next run exceeds → `RUN_REJECTED` event with `safe_error_code='budget_exceeded'`.
- Set per-tool budget of 3 calls → 4th tool call returns `TOOL_BUDGET_EXCEEDED` to the model.
- Restart worker mid-run → idempotent `INSERT ON CONFLICT (run_id)` ensures no double-charge.

**Track C:**

- Set `ENTERPRISE_DEPLOYMENT_PROFILE=single_tenant_managed`, `DEV_AUTH_BYPASS=true` → backend refuses to start.
- RLS test: connect as `enterprise_app`, set `app.current_org_id='org_a'`, insert row; switch to `'org_b'`, SELECT returns 0 rows.
- KMS: configure `MCP_TOKEN_VAULT_BACKEND=aws_kms` against a fake KMS → token round-trip; pull KMS down → writes fail closed; reads serve from cache for 5min then fail.
- Field encryption: write a message; `pg_dump --schema-only` shows ciphertext only in `agent_messages.content_text` after backfill.
- Retention sweeper: set policy `messages: ttl=7d`, age messages 8d, run sweeper → tombstoned; place legal hold → not tombstoned.
- SIEM export: configure Splunk HEC fake → audit events arrive; restart pump mid-batch → cursor resumes.
- Restore drill CI workflow → green.

---

## Critical files (referenced across PRs)

**Identity & Access (Track A):**

- [services/backend/src/backend_app/migrations.py](services/backend/src/backend_app/migrations.py) — superseded by C2 yoyo migrations
- [services/backend/src/backend_app/contracts.py](services/backend/src/backend_app/contracts.py) — extended for identity records
- [services/backend/src/backend_app/mcp_oauth.py](services/backend/src/backend_app/mcp_oauth.py) — extract `_pkce.py` for OIDC reuse
- [services/backend/src/backend_app/token_vault.py](services/backend/src/backend_app/token_vault.py) — vault for refresh tokens, MFA secrets
- [services/backend/src/backend_app/app.py](services/backend/src/backend_app/app.py) — every route gets `RequireScopes` annotation in A10
- [services/backend-facade/src/backend_facade/auth.py:98-116](services/backend-facade/src/backend_facade/auth.py#L98-L116) — extend to honor `sid` claim
- [services/backend-facade/src/backend_facade/app.py:48](services/backend-facade/src/backend_facade/app.py#L48) — replace `/v1/session` with backend-backed lookup
- [apps/frontend/src/app/App.tsx:113-146](apps/frontend/src/app/App.tsx#L113-L146) — refactor to AuthContext
- [apps/frontend/src/api/sessionApi.ts](apps/frontend/src/api/sessionApi.ts), [apps/frontend/src/api/http.ts](apps/frontend/src/api/http.ts)
- [packages/api-types/src/index.ts](packages/api-types/src/index.ts), new `packages/service-contracts/src/enterprise_service_contracts/scopes.py`

**Token Usage (Track B):**

- [services/ai-backend/src/runtime_worker/handlers/run.py](services/ai-backend/src/runtime_worker/handlers/run.py) — every Track B PR except B5/B6 hooks here; RUN_COMPLETED block at L271–287, handle() top at L125 for budgets
- [services/ai-backend/src/runtime_worker/run_metrics.py](services/ai-backend/src/runtime_worker/run_metrics.py) — single source of truth for token extraction; B2 splits into per-call buckets
- [services/ai-backend/src/agent_runtime/persistence/schema/postgres.py](services/ai-backend/src/agent_runtime/persistence/schema/postgres.py) — superseded by C2 migrations
- [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) — repository layer; every backend PR adds methods here
- [services/ai-backend/src/runtime_api/http/routes.py](services/ai-backend/src/runtime_api/http/routes.py) — `/v1/usage/*` and `/v1/budgets/*` namespaces
- [services/ai-backend/src/runtime_api/schemas/events.py:532-551](services/ai-backend/src/runtime_api/schemas/events.py#L532-L551) — extend with `MODEL_CALL_COMPLETED`, subagent usage rollup
- [services/ai-backend/src/runtime_worker/tool_call_ledger.py](services/ai-backend/src/runtime_worker/tool_call_ledger.py) — extend for budget enforcement
- [services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py:41-72](services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py#L41-L72) — prompt suffix references actual configured cap
- [apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx](apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx) — register `/context` and `/usage` slash commands
- [apps/frontend/src/features/chat/utils/activityDataBuilders.ts:54-75](apps/frontend/src/features/chat/utils/activityDataBuilders.ts#L54-L75) — surface input + cached tokens

**Deployment & DB (Track C):**

- [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) — `_DEFAULT_POOL_KWARGS`, `_tenant_connection` helper, `@reader` decorator
- [services/backend/src/backend_app/store.py](services/backend/src/backend_app/store.py) — `PostgresMcpStore.put_token` (C3), connection pool tuning (C4), `_tenant_connection` (C5)
- [services/backend/src/backend_app/service.py](services/backend/src/backend_app/service.py) — wrap (write+audit) sites in transactions (C3); `urlopen` → `httpx`
- [services/backend/src/backend_app/token_vault.py](services/backend/src/backend_app/token_vault.py) — replace `ManagedSecretTokenVault` stub at L104 (C6)
- New per-service `migrations/` dirs (C2)
- New `services/<svc>/src/.../deployment_profile.py` modules (C1)
- New `services/ai-backend/src/runtime_worker/jobs/retention_sweeper.py` (C8) and `services/backend/src/backend_app/siem_export/` (C9)
