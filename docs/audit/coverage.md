---
id: coverage
kind: report
date: 2026-07-20
auditor: coverage-auditor
---

# Coverage Audit — cluster claim map vs. tracked files

## Method

- Enumerated `git ls-files` at worktree HEAD (`worktree-arch-audit-v2`): **3,251 tracked files**. Excluded the 46 files under `docs/audit/` (this audit's own output). **3,205 files classified.**
- Classified each file by **longest-prefix match** against the 17-cluster claim map (a claim matches as an exact file or as a directory prefix). Every path matched at most one cluster; longest prefix wins (e.g. `packages/chat-surface/src/settings/...` → chat-surface-destinations, not chat-surface-core).
- Applied the agreed **test-attribution rule**: test directories count toward the cluster owning the code under test (nearest claim). Concretely:
  - `services/ai-backend/tests/unit/<pkg>/<sub>/...` mapped to `services/ai-backend/src/<pkg>/<sub>` (walking up until a claim matched), with legacy mirror aliases `agent→execution`, `mcp→capabilities/mcp`, `memory→context/memory`, `skills→capabilities/skills`, `subagents→delegation/subagents`, `tools→capabilities/tools` (verified against the actual src layout — there is no `src/agent_runtime/agent/`, etc.).
  - `services/ai-backend/tests/contract/sandbox/` → ai-runtime-capabilities (`capabilities/sandbox`); `tests/integration/persistence/` → ai-runtime-persistence; `tests/integration/citations/` → ai-runtime-capabilities (citation code lives at `src/agent_runtime/capabilities/citation_*.py`).
  - `services/backend/tests/identity/` → backend-identity; `tests/unit/<module>/` → the cluster claiming `src/backend_app/<module>`; flat `tests/test_<name>.py` mapped by an explicit filename→module table (e.g. `test_audit_export.py` → `backend_app/routes/audit_export.py` → backend-product; `test_home_*.py` → `backend_app/home` → backend-product).
  - `packages/chat-surface/vitest.config.ts` → chat-surface-core (nearest claim: `src/test`).
  - **A test whose subject is itself unclaimed stays an orphan** (e.g. `test_token_vault.py` tests unclaimed `backend_app/token_vault.py`). This keeps the orphan report honest.
- LOC = `wc -l`-equivalent over source-text extensions only (py/ts/tsx/js/css/html/md/json/yml/toml/sql/sh/astro/puml/Dockerfile/Makefile/etc.); binaries, images, fonts, and `package-lock.json` counted as files but 0 LOC.
- Classifier script preserved at scratchpad `coverage_final.py`; raw result JSON at `final_result.json` (session scratchpad, not committed).

**Result: 2,897 files (708,449 LOC, 95.1%) are claimed by exactly one cluster; 308 files (36,690 LOC, 4.9%) are orphans** — claimed by no cluster even after test attribution.

## Per-cluster coverage

| Cluster | Files (direct) | + attributed tests | Files (total) | ~LOC | % of repo LOC |
|---|---:|---:|---:|---:|---:|
| frontend-web | 545 | 0 | 545 | 106,276 | 14.3% |
| docs-corpus | 325 | 0 | 325 | 88,898 | 11.9% |
| chat-surface-destinations | 269 | 0 | 269 | 79,939 | 10.7% |
| backend-product | 128 | 88 | 216 | 68,335 | 9.2% |
| ai-runtime-execution | 65 | 50 | 115 | 56,102 | 7.5% |
| ai-runtime-api | 100 | 73 | 173 | 48,259 | 6.5% |
| desktop-app | 206 | 0 | 206 | 38,004 | 5.1% |
| chat-surface-core | 225 | 1 | 226 | 37,950 | 5.1% |
| ai-runtime-persistence | 80 | 53 | 133 | 35,241 | 4.7% |
| ai-runtime-capabilities | 88 | 80 | 168 | 33,541 | 4.5% |
| backend-identity | 59 | 34 | 93 | 31,147 | 4.2% |
| ai-runtime-worker | 35 | 43 | 78 | 29,395 | 3.9% |
| backend-facade | 88 | 0 | 88 | 19,568 | 2.6% |
| shared-packages | 133 | 0 | 133 | 19,019 | 2.6% |
| build-deploy | 76 | 0 | 76 | 8,405 | 1.1% |
| desktop-distribution | 28 | 0 | 28 | 4,187 | 0.6% |
| backend-platform | 18 | 7 | 25 | 4,183 | 0.6% |
| **claimed total** | **2,468** | **429** | **2,897** | **708,449** | **95.1%** |
| **ORPHANS** | — | — | **308** | **36,690** | **4.9%** |

Notes on cluster hygiene observed while classifying:

- Every claimed prefix in the map matched at least one tracked file — no dead claims.
- No file matched two clusters at equal prefix length — the longest-prefix rule was never ambiguous in practice.
- `backend_app/routes/` (claimed by backend-product) actually contains mostly identity/workspace/audit route modules (`oidc.py`, `siwe.py`, `saml.py`, `scim.py`, `mfa.py`, `sessions.py`, `passwords.py`, `audit_export.py`, `siem.py` — see `services/backend/src/backend_app/routes/`), so backend-product absorbs a lot of identity-adjacent surface via that one claim. Flagging for the cluster owners; classification followed the map as given.

## Orphans — 308 files, 36,690 LOC, grouped

The headline: **the claim map covers `src/` subtrees but not the service chassis around them.** Both Python services' root metadata, migrations, ops scripts, per-service docs, and top-level `src` modules are unclaimed. One group is genuinely significant source (backend_app top-level, ~9.9k LOC including the token vault and MCP OAuth); the rest is chassis, docs, and a little tracked junk.

### services/backend/src — 14 files, 9,861 LOC — **most significant orphan group**

Top-level `backend_app` modules claimed by no cluster; these include the app wiring and several security-critical modules (`app.py` 2,048 LOC; `store.py` 1,400; `service.py` 1,365; `token_vault.py` 536; `mcp_oauth.py` 518):

- services/backend/src/backend_app/\_\_init\_\_.py
- services/backend/src/backend_app/app.py — FastAPI app assembly / router registration
- services/backend/src/backend_app/audit_reader.py — audit event read path
- services/backend/src/backend_app/auth.py — bearer verification / caller identity
- services/backend/src/backend_app/contracts.py — shared request/response models
- services/backend/src/backend_app/deployment_profile.py — ENTERPRISE_DEPLOYMENT_PROFILE gating
- services/backend/src/backend_app/desktop_app.py — desktop-host integration surface
- services/backend/src/backend_app/mcp_catalog.py — MCP catalog
- services/backend/src/backend_app/mcp_oauth.py — MCP OAuth flows
- services/backend/src/backend_app/migrations.py — migration runner
- services/backend/src/backend_app/service.py — core service layer (MCP/skills)
- services/backend/src/backend_app/store.py — persistence store
- services/backend/src/backend_app/token_vault.py — secret encryption vault
- services/backend/src/backend_app/token_vault_metrics.py

Guess: this is the pre-modularization core of `backend_app` (MCP registration, token vault, audit, app wiring) that predates the per-feature subpackages. It needs an owning cluster — probably split across backend-platform (app/store/migrations), backend-identity (auth/token_vault), and backend-product (mcp_catalog/mcp_oauth/service).

### services/backend/tests — 22 files, 3,228 LOC

Tests whose subjects are the unclaimed top-level modules above (kept as orphans per the rule), plus shared fixtures:

- services/backend/tests/fixtures/sqlite_migrations/0001_create_widgets.rollback.sql
- services/backend/tests/fixtures/sqlite_migrations/0001_create_widgets.sql
- services/backend/tests/fixtures/sqlite_migrations/0002_add_widget_color.rollback.sql
- services/backend/tests/fixtures/sqlite_migrations/0002_add_widget_color.sql
- services/backend/tests/integration/\_\_init\_\_.py
- services/backend/tests/test_atomicity.py — tests store.py transactionality
- services/backend/tests/test_audit_chain.py
- services/backend/tests/test_audit_chain_compat.py
- services/backend/tests/test_audit_deploy_api.py
- services/backend/tests/test_backfill_notification_preferences.py — tests scripts/ (also unclaimed)
- services/backend/tests/test_deployment_profile.py
- services/backend/tests/test_desktop_app.py
- services/backend/tests/test_mcp_api_flow.py
- services/backend/tests/test_mcp_catalog_install.py
- services/backend/tests/test_mcp_registry.py
- services/backend/tests/test_migration_runner.py
- services/backend/tests/test_oauth_no_leak.py
- services/backend/tests/test_skills_api_flow.py
- services/backend/tests/test_skills_registry.py
- services/backend/tests/test_tenant_isolation_skills_mcp.py
- services/backend/tests/test_token_vault.py
- services/backend/tests/unit/\_\_init\_\_.py

### services/ai-backend/src — 8 files, 786 LOC

- services/ai-backend/src/agent_runtime/\_\_init\_\_.py
- services/ai-backend/src/agent_runtime/settings.py — runtime settings (env parsing)
- services/ai-backend/src/agent_runtime/validation.py — shared validation helpers
- services/ai-backend/src/agent_runtime.egg-info/PKG-INFO ← **build artifact tracked in git**
- services/ai-backend/src/agent_runtime.egg-info/SOURCES.txt ← ″
- services/ai-backend/src/agent_runtime.egg-info/dependency_links.txt ← ″
- services/ai-backend/src/agent_runtime.egg-info/requires.txt ← ″
- services/ai-backend/src/agent_runtime.egg-info/top_level.txt ← ″

Guess: `settings.py`/`validation.py` are package-top cross-cutting modules — nearest owner is ai-runtime-execution (or a deliberate "runtime-core" claim). The `agent_runtime.egg-info/` directory should be deleted and gitignored.

### services/ai-backend/tests — 20 files, 3,008 LOC

Cross-cutting / service-level tests with no single owning cluster:

- services/ai-backend/tests/CLAUDE.md
- services/ai-backend/tests/\_\_init\_\_.py
- services/ai-backend/tests/contract/\_\_init\_\_.py
- services/ai-backend/tests/integration/\_\_init\_\_.py
- services/ai-backend/tests/integration/test_draft_flow.py
- services/ai-backend/tests/integration/test_sources_replay_parity.py
- services/ai-backend/tests/integration/test_subagent_failure_reconciliation.py
- services/ai-backend/tests/integration/test_tool_exception_surfaces_to_llm.py
- services/ai-backend/tests/test_conversation_connector_inheritance.py
- services/ai-backend/tests/test_inbox_producer.py
- services/ai-backend/tests/test_run_inbox_routing.py
- services/ai-backend/tests/unit/\_\_init\_\_.py
- services/ai-backend/tests/unit/agent_runtime/\_\_init\_\_.py
- services/ai-backend/tests/unit/agent_runtime/test_import_boundaries.py — service-boundary lint test
- services/ai-backend/tests/unit/agent_runtime/test_prd_acceptance.py
- services/ai-backend/tests/unit/agent_runtime/test_provider_kwargs.py
- services/ai-backend/tests/unit/agent_runtime/test_runtime_settings.py — tests unclaimed settings.py
- services/ai-backend/tests/unit/conftest.py — shared fixtures for the whole unit tree
- services/ai-backend/tests/unit/fakes.py — shared fakes
- services/ai-backend/tests/unit/scripts/test_count_unencrypted_rows.py — tests unclaimed scripts/

### services/ai-backend/migrations — 67 files, 2,636 LOC

Numbered SQL forward/rollback pairs + `MANIFEST.lock` + `staged/`. Guess: schema migrations for runtime persistence → natural owner ai-runtime-persistence. (Full list is `services/ai-backend/migrations/00NN_*.sql` + `.rollback.sql`, 0001–0033, `MANIFEST.lock`, `staged/README.md`, `staged/0034_prompt_supersedence.*`.)

### services/backend/migrations — 73 files, 2,476 LOC

Same shape for the core backend (0001–0036 pairs + `MANIFEST.lock` + `staged/`). Guess: natural owner backend-platform (db).

### services/ai-backend/docs — 47 files, 5,955 LOC

Per-service docs corpus: `docs/architecture/*.md` (00–04 + 11 cluster `.puml` + 9 flow `.puml` diagrams), `docs/features/*.md` (13), `docs/guides/*` (4), `docs/reference/*` (3), plus `docs/CLAUDE.md` (the spec-first workflow rules) and `docs/README.md`. Guess: the repo-root `docs` claim in docs-corpus was probably intended to cover these; nearest owner is docs-corpus or the respective ai-runtime clusters.

### services/backend/docs — 17 files, 2,593 LOC

Same shape: `docs/architecture/00-03`, `docs/features/*` (7: api-keys, audit, identity-auth, mcp-registry, notifications, policies, skills), `docs/guides/*` (2), `docs/reference/*` (3: env-vars, internal-api, public-api), `docs/README.md`. Guess: docs-corpus.

### services/ai-backend (service root) — 12 files, 865 LOC

Service chassis + two pieces of tracked junk:

- services/ai-backend/.coverage ← **binary coverage database tracked in git** (junk)
- services/ai-backend/.env.example
- services/ai-backend/.gitignore
- services/ai-backend/CLAUDE.md
- services/ai-backend/Dockerfile
- services/ai-backend/Oops.rej ← **stray patch-reject file tracked in git** (junk)
- services/ai-backend/README.md
- services/ai-backend/docker-compose.yml — production-style API+worker+PG compose
- services/ai-backend/env_example — duplicate of .env.example?
- services/ai-backend/langgraph.json
- services/ai-backend/pyproject.toml
- services/ai-backend/requirements.txt

Guess: chassis → build-deploy or a per-service "meta" claim; `.coverage`, `Oops.rej`, and the `.env.example`/`env_example` duplication warrant cleanup.

### services/backend (service root) — 11 files, 2,086 LOC

- services/backend/.dockerignore
- services/backend/.env.example
- services/backend/ARCHITECTURE.md
- services/backend/CLAUDE.md
- services/backend/Dockerfile
- services/backend/README.md
- services/backend/TESTING.md
- services/backend/dev_personas.yaml — dev IdP persona seed data
- services/backend/pyproject.toml
- services/backend/requirements.in
- services/backend/requirements.txt

Guess: service chassis (docs-corpus for the .md files, build-deploy for the rest).

### services/ai-backend/scripts — 5 files, 708 LOC

- services/ai-backend/scripts/count_unencrypted_rows.py — encryption-coverage ops check
- services/ai-backend/scripts/migrate.py — migration runner entrypoint
- services/ai-backend/scripts/pricing/refresh_litellm_data.py — pricing data refresh
- services/ai-backend/scripts/restore_smoke.py — backup-restore smoke
- services/ai-backend/scripts/usage/seed_pricing.py — pricing seed

Guess: ops/maintenance scripts; nearest owners ai-runtime-persistence (migrate/restore/count) and ai-runtime-execution (pricing).

### services/backend/scripts — 4 files, 680 LOC

- services/backend/scripts/backfill_notification_preferences.py
- services/backend/scripts/migrate.py
- services/backend/scripts/restore_smoke.py
- services/backend/scripts/rotate_token_vault.py — vault key rotation (security-relevant)

Guess: ops scripts; nearest owners backend-platform (migrate/restore), backend-product (backfill), and whoever owns token_vault.py.

### services/ai-backend/skills — 2 files, 95 LOC

- services/ai-backend/skills/search-subagent-logs/SKILL.md
- services/ai-backend/skills/web-search-discipline/SKILL.md

Guess: runtime-served agent skill bundles → ai-runtime-capabilities (skills loader).

### services/ai-backend/config — 1 file, 85 LOC

- services/ai-backend/config/pricing_overrides.yaml

Guess: pricing override data → ai-runtime-execution (pricing).

### packages/chat-surface — 5 files, 1,628 LOC

Package plumbing that sits above the core/destinations split:

- packages/chat-surface/CLAUDE.md — the SSOT-pattern rules doc
- packages/chat-surface/eslint.config.js — enforces the substrate-agnostic bans (window/fetch/localStorage)
- packages/chat-surface/package.json
- packages/chat-surface/src/index.ts — 1,269-line barrel exporting the whole package surface
- packages/chat-surface/tsconfig.json

Guess: chat-surface-core (it claims 19 of the 21 src subdirs; `vitest.config.ts` was already attributed there by the test-config rule).

## Junk findings (should not be tracked at all)

| File | Problem |
|---|---|
| services/ai-backend/.coverage | Binary pytest-cov database committed to git |
| services/ai-backend/Oops.rej | Stray patch-reject file committed to git |
| services/ai-backend/src/agent_runtime.egg-info/ (5 files) | setuptools build artifact committed to git |
| services/ai-backend/env_example | Apparent duplicate of `.env.example` |

## Bottom line

- **No cluster claims overlap, and no claimed prefix is dead.** The map is internally consistent.
- **95.1% of repo LOC is covered.** The uncovered 4.9% is dominated by service chassis (root metadata, migrations, scripts, per-service docs) that the map structurally ignores — a systematic gap, not scattered misses.
- **One real source gap:** the 14 top-level `backend_app` modules (~9.9k LOC) including `token_vault.py`, `mcp_oauth.py`, `auth.py`, `app.py`, `store.py` — security-critical code that currently no audit cluster owns, along with the 22 backend tests that cover it. If the audit reviews clusters as scoped, this code gets reviewed by nobody. Recommend assigning these files explicitly before the backend-* cluster reviews run.
