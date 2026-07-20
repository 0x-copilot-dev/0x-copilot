---
id: build-deploy
title: Build, CI/CD, Deploy & Website
kind: cluster
paths: [.github, deploy, infra, tools, tests, apps/website]
loc: 12000
languages: [yaml, python, javascript, astro, shell, makefile]
---

# Cluster: Build, CI/CD, Deploy & Website

## Purpose

This cluster is the repo's scaffolding: everything that turns source into tested artifacts, signed images, tenant deploys, a shipped desktop app, and a published marketing site. It comprises 15 GitHub Actions workflows (`.github/workflows/`), the multi-tenant deploy machinery (`deploy/` — tenant registry + schema, six promotion/verification scripts, branch-protection ruleset source), the self-host distribution (`deploy/self-host/` — one-node compose + installer), the local dev stack (root `Makefile`, `docker-compose.dev.yml`, `infra/docker/dev-gateway.conf`), repo governance (root `package.json` npm workspace, `pyrightconfig.json`, `tsconfig.base.json`, `.pre-commit-config.yaml`, `.github/dependabot.yml`, `CODEOWNERS`), five policy-lint AST checkers in `tools/check_*.py` with their tests, the restore-drill fixture (`tests/fixtures/postgres-restore/`), a Grafana dashboard (`infra/dashboards/`), and the Astro marketing site (`apps/website/`, ~2.6k LOC) with its GitHub Pages deploy.

The design intent (per CLAUDE.md §CI/CD) is per-component, path-filtered CI: each deployable service/app has its own workflow, its own SBOM, its own image, and PR CI never needs production secrets. Release is a separate lane: `release-images.yml` builds digest-pinned GHCR images with cosign keyless signatures, provenance attestations, and Trivy CRITICAL gating, then emits a `deployment-manifest.json`; `deploy.yml` promotes a verified manifest to a tenant via GitHub Environments, staged-rollout policy (canary → early → general), OIDC federation into the tenant cloud, and orchestrator invocation (argo/helm/flux), recording an audit event into the backend. `release-desktop.yml` builds per-arch native desktop installers using the desktop-distribution cluster's staging tooling.

The cluster also owns the compliance-facing controls that live in repo scaffolding rather than product code: gitleaks secret scanning, pip-audit/npm-audit, the weekly `postgres-restore-drill` (C12 — proves restore actually works against real migrations + a seed fixture), migration-manifest checksums (C2), and the pre-commit structural checks (audit-in-transaction C3, reader no-write C10, single-LLM-tracker TU-1, no-content-logging).

`tools/cli/`, `tools/cli-testing/`, and `tools/desktop-runtime/` physically live under an assigned path but are the desktop-distribution cluster's subject matter; they are inventoried here only as build surfaces (`ci-cli.yml`, `release-desktop.yml`, `make desktop-install` all invoke them).

## Public Interface

Surfaces other components/humans consume:

**CI workflows (triggered by GitHub events):**

- `ci-backend` / `ci-backend-facade` / `ci-ai-backend` — pytest + pip-audit + SBOM per service; backend/facade additionally verify `requirements.txt` is compiled with hashes from `requirements.in` (.github/workflows/ci-backend.yml:48-54); backend + ai-backend run the A10 RBAC route-scope check (ci-backend.yml:63-68, ci-ai-backend.yml:58-63).
- `ci-frontend` — typecheck api-types/design-system/frontend + `vite build` + npm audit + workspace SBOM (.github/workflows/ci-frontend.yml:47-60).
- `ci-desktop` — desktop typecheck + the ~830-test vitest suite, path filter includes every workspace package desktop imports (.github/workflows/ci-desktop.yml:15-25).
- `ci-cli` — 3-OS syntax check + dependency-free smoke + npm-pack manifest check for `tools/cli` (.github/workflows/ci-cli.yml:29-53).
- `ci-repo` — pre-commit on the diff range, gitleaks secret scan, tenants.yml schema lint (.github/workflows/ci-repo.yml:22-74).
- `postgres-restore-drill` — weekly cron + migration-path-filtered PR trigger; applies both services' migrations to postgres:16, loads `tests/fixtures/postgres-restore/seed.sql`, runs each service's `scripts/restore_smoke.py` (.github/workflows/postgres-restore-drill.yml:102-125).

**Release/deploy workflows:**

- `release-images` — on main push (path-filtered) builds the 4 images from the service Dockerfiles, pushes to `ghcr.io/<owner>/0x-copilot-*`, cosign-signs, attests, Trivy-gates, and uploads `deployment-manifest.json` (schema `0x-copilot/deployment-manifest/v2`, release-images.yml:289-308) — the contract consumed by `deploy.yml` and pinnable by self-host `IMAGE_TAG`.
- `deploy` (`workflow_call`/`workflow_dispatch` with `tenant_id`, `environment`, `release_sha`, `force_deploy`) — the parameterized promotion pipeline; `deploy-staging.yml` / `deploy-production.yml` are thin wrappers (deploy.yml:9-46).
- `release-desktop` — `v*` tag → build/sign/notarize/publish desktop installers + electron-updater feeds; `workflow_dispatch` → unsigned dry-run artifacts (release-desktop.yml:20-24,158-165).
- `deploy-website` — main-push path-filtered Astro build published to the `0x-copilot-dev.github.io` repo via deploy key (deploy-website.yml:69-79).
- `apply-branch-protection` — manual dispatch that diffs/applies `deploy/branch-protection.json` as a GitHub ruleset (currently broken — see F1).

**CLI commands (operator-facing):**

- `make setup | setup-hooks | dev | dev-bearer [PERSONA=] | docker-dev | docker-dev-down | desktop-install | desktop-uninstall | prod | test` (Makefile:17-30).
- `deploy/scripts/*.py` — `validate_tenants.py`, `load_tenant.py <id> <env>`, `verify_release.py --manifest --owner --tenant`, `check_staged_rollout.py`, `invoke_orchestrator.py`, `post_audit.py` (each is argparse-documented; consumed by deploy.yml steps).
- `tools/check_migration_manifest.py [--write]`, `tools/check_route_scopes.py --service {backend|ai-backend|all}`, plus the three pre-commit-wired checkers (see `.pre-commit-config.yaml:82-125`).
- `deploy/self-host/install.sh` — curl-pipe installer; env overrides `OXC_REPO_RAW`, `OXC_INSTALL_DIR`, `OXC_GATEWAY_PORT`, `OXC_SIWE_ORIGIN` (install.sh:14-15,71-72).

**Env-var contracts owned/propagated here:** the dev-stack contract (`ENTERPRISE_AUTH_SECRET`/`ENTERPRISE_SERVICE_TOKEN`/`MCP_TOKEN_VAULT_PROVIDER`/`BACKEND_ENVIRONMENT` etc., Makefile:92-115); the self-host `.env` contract (`GHCR_NAMESPACE`, `IMAGE_TAG`, `GATEWAY_PORT`, `AUDIT_HMAC_KEY`, `SIWE_ORIGIN`, BYOK keys — deploy/self-host/.env.example:10-58); deploy secrets (`ORCHESTRATOR_ENDPOINT` via per-tenant `endpoint_secret_ref`, `ORCHESTRATOR_TOKEN`, `TENANT_DEPLOY_ROLE_ARN`, `BACKEND_URL`, `ENTERPRISE_SERVICE_TOKEN` — deploy.yml:158-227); website `SITE_BASE`/`SITE_ORIGIN` (apps/website/astro.config.mjs:18-21).

## Internal Structure

| module/group | files | ~LOC | responsibility |
|---|---|---:|---|
| per-component CI | .github/workflows/ci-{backend,backend-facade,ai-backend,frontend,desktop,cli,repo}.yml | 490 | path-filtered test/audit/SBOM per deployable + repo-wide lint/secrets/tenants-lint |
| release-images | .github/workflows/release-images.yml | 315 | 4-image GHCR build matrix, cosign/attest/Trivy, deployment-manifest.json assembly |
| tenant deploy pipeline | .github/workflows/deploy.yml, deploy-staging.yml, deploy-production.yml | 314 | parameterized per-tenant promotion: verify → staged-rollout gate → OIDC federate → orchestrate → audit |
| deploy scripts | deploy/scripts/{validate_tenants,load_tenant,verify_release,check_staged_rollout,invoke_orchestrator,post_audit}.py | 838 | registry validation, cosign/attestation/residency verification, rollout policy, argo/helm/flux dispatch, audit POST |
| tenant registry | deploy/tenants.yml, deploy/tenants.schema.json | 128 | authoritative (currently empty) tenant list + JSON-schema contract |
| branch protection | deploy/branch-protection.json, .github/workflows/apply-branch-protection.yml | 143 | ruleset source-of-truth + (broken) apply workflow |
| restore drill | .github/workflows/postgres-restore-drill.yml, tests/fixtures/postgres-restore/{seed.sql,manifest.yaml} | 372 | C12 weekly restore proof against real migrations + seeded counts |
| desktop release | .github/workflows/release-desktop.yml | 182 | 3-leg native matrix, secret-gated signing/notarization, tag-publish vs dispatch-dry-run |
| website deploy | .github/workflows/deploy-website.yml | 79 | Astro build + dist sanity + link check + Pages publish to external repo |
| self-host | deploy/self-host/{docker-compose.prod.yml,install.sh,.env.example,docker-compose.local-build.yml,README.md} | 714 | single-node GHCR stack: postgres 17 + migrate jobs + 4 services + nginx gateway; idempotent installer |
| dev stack | Makefile, docker-compose.dev.yml, infra/docker/dev-gateway.conf | 276 | local 4-process stack with dev IdP secrets; docker variant behind nginx :8080 |
| repo governance | package.json, package-lock.json, tsconfig.base.json, pyrightconfig.json, .pre-commit-config.yaml, .github/dependabot.yml, CODEOWNERS, .dockerignore, .gitignore | ~460 (excl. lock) | npm workspace roots, type/lint config, dep-update policy, ownership |
| policy lints | tools/check_{route_scopes,migration_manifest,audit_in_transaction,reader_methods,llm_provider_imports}.py | 890 | AST/static guards: A10 RBAC coverage, C2 manifest drift, C3 audit-txn atomicity, C10 reader no-write, TU-1 single LLM tracker |
| policy lint tests | tools/test_check_*.py (5) | 549 | pytest suites for the five checkers (not wired into any CI job) |
| website app | apps/website/src/{pages/{index,token,docs}.astro, layouts/Base.astro, components/Nav.astro, styles/site.css}, astro.config.mjs, package.json, tsconfig.json, public/* | ~2,450 | 3-page static marketing site ("put your day on autopilot", $CPILOT token page, docs) |
| website tooling | apps/website/scripts/{check-links,capture,film}.mjs | 199 | deploy-base link verifier; Playwright screenshot/screen-recording helpers |
| dashboards | infra/dashboards/db-statement-perf.json | 33 | Grafana C11 statement-performance panels (prometheus `db_statement_*` metrics) |
| desktop-distribution tenants (inventoried only) | tools/cli/** (~1,850), tools/cli-testing/** (~990), tools/desktop-runtime/** (~1,520) | 4,360 | `copilot` npm launcher CLI, Playwright live-smoke harness, runtime staging — audited in desktop-distribution |

Architecture notes. The CI system is deliberately federated: no mono-CI job; each workflow owns one component with a hand-maintained path filter that must mirror that component's true dependency graph (this is where the drift lives — F3). Actions are SHA-pinned everywhere except `deploy-website.yml`. Release and deploy are decoupled through the `deployment-manifest.json` artifact: `deploy.yml` never rebuilds, it re-verifies (cosign + `gh attestation verify` + digest-pinning + registry residency) before promoting, which is a genuinely strong supply-chain posture on paper. The deploy pipeline's tenant model (registry → GitHub Environment naming convention → per-tenant secrets resolved by `endpoint_secret_ref`) is enforced by `validate_tenants.py` in both `ci-repo` and `deploy.yml` itself. The policy lints are split across two enforcement planes: pre-commit (C3/C10/TU-1/content-logging — .pre-commit-config.yaml:82-125, re-run in CI by `ci-repo`) and per-service CI (A10 route scopes); C2 (migration manifest) is enforced only at service boot, not in CI (F7).

## Dependencies

### Outbound

| target | kind | what | evidence |
|---|---|---|---|
| backend-platform | build | ci-backend runs full `services/backend` pytest + RBAC scan; release-images builds its Dockerfile | .github/workflows/ci-backend.yml:59-68; release-images.yml:52-53 |
| ai-runtime-execution | build | ci-ai-backend runs full `services/ai-backend` pytest; release-images builds its Dockerfile | .github/workflows/ci-ai-backend.yml:54-56; release-images.yml:56-57 |
| ai-runtime-api | import | check_route_scopes imports `runtime_api.app:RuntimeApiAppFactory.create_app` (and `backend_app.app:create_app`) to enumerate live routes | tools/check_route_scopes.py:59-65,118-131 |
| ai-runtime-persistence | build | restore drill applies `services/ai-backend/migrations` via yoyo and runs its restore_smoke.py; @reader no-write lint scans its src | .github/workflows/postgres-restore-drill.yml:107-125; .pre-commit-config.yaml:96-108 |
| backend-facade | build | ci-backend-facade pytest + hash-verified requirements; release-images builds its Dockerfile | .github/workflows/ci-backend-facade.yml:45-58; release-images.yml:54-55 |
| frontend-web | build | ci-frontend typecheck+build; release-images builds apps/frontend/Dockerfile | .github/workflows/ci-frontend.yml:47-52; release-images.yml:58-59 |
| desktop-app | build | ci-desktop typecheck + vitest; release-desktop builds bundle + electron-builder installers | .github/workflows/ci-desktop.yml:67-75; release-desktop.yml:97-98,158-165 |
| desktop-distribution | spawn | release-desktop runs `tools/desktop-runtime/stage.mjs`; ci-cli checks `tools/cli`; make desktop-install packs the CLI | .github/workflows/release-desktop.yml:103-104; ci-cli.yml:46-53; Makefile:139-145 |
| shared-packages | build | CI installs `packages/service-contracts` + `packages/audit-chain` into service envs; Dockerfiles COPY them | .github/workflows/ci-backend.yml:44-45; services/backend/Dockerfile:11-16 |
| backend-platform | http | post_audit.py POSTs deploy audit events to `/internal/v1/audit/deploy` with service-token headers | deploy/scripts/post_audit.py:86-96 |
| external:github | http | GHCR push/pull, `gh api` run/artifact resolution, attestations, gitleaks token, Pages publish, GitHub Environments | release-images.yml:69-73,202-254; deploy.yml:88-118; deploy-website.yml:69-79 |
| external:sigstore | http | cosign keyless sign at release; cosign verify at deploy | release-images.yml:115-120; deploy/scripts/verify_release.py:113-123 |
| external:aws | http | OIDC federation into tenant cloud (`configure-aws-credentials`) | .github/workflows/deploy.yml:158-167 |
| external:kubernetes | http/spawn | orchestrator dispatch: argo REST PATCH+sync, `helm upgrade --atomic`, `flux reconcile` | deploy/scripts/invoke_orchestrator.py:56-208 |
| external:postgres | db | restore-drill postgres:16 service; self-host postgres:17; drill applies migrations + seeds | postgres-restore-drill.yml:52-77; deploy/self-host/docker-compose.prod.yml:55-72 |
| external:openai / external:anthropic | env | dev/docker/self-host stacks pass provider keys through to ai-backend (`OPENAI_API_KEY` etc.) | Makefile:62-70; docker-compose.dev.yml:26-28; docker-compose.prod.yml:175-178 |
| external:ollama | env | self-host optional local models (`OLLAMA_BASE_URL`, `RUNTIME_ENABLE_LOCAL_MODELS`) | deploy/self-host/docker-compose.prod.yml:179-184 |
| external:apple | http | notarytool notarization during release-desktop (App Store Connect API key) | .github/workflows/release-desktop.yml:124-133 |
| docs-corpus | file | workflows cite runbooks/specs as their contract (multi-tenant-deploy.md, desktop-release.md, db-migrations runbook) | deploy.yml:4; release-desktop.yml:16; docs/ci-cd/runbooks/db-migrations.md:74-78 |

### Inbound

- **Every other cluster** consumes this one passively: merges are gated by ci-* workflows; images consumed by self-host and tenant deploys are produced by release-images.
- **Self-host operators** consume `install.sh` + `docker-compose.prod.yml` + GHCR images (deploy/self-host/README.md).
- **backend-platform's migration runners** consume the `MANIFEST.lock` format defined by `tools/check_migration_manifest.py` (services/backend/src/backend_app/db/migrate.py:105,152 reference the tool by name).
- **Desktop supervisor & self-host migrate jobs** consume `scripts/migrate.py` shipped by the Dockerfiles this cluster builds (services/backend/Dockerfile:19-24).
- **electron-updater in shipped desktop apps** consumes the `latest*.yml` feeds published by release-desktop (release-desktop.yml:155-181).
- **Marketing visitors** consume the Pages site (0x-copilot-dev.github.io).

## Data Owned

- `deploy/tenants.yml` — authoritative tenant registry (currently `tenants: []`) + `deploy/tenants.schema.json` contract.
- `deploy/branch-protection.json` — intended source of truth for the main-branch ruleset.
- `deployment-manifest.json` (workflow artifact, schema `0x-copilot/deployment-manifest/v2`) + per-image `*.image-ref.txt` fragments + `deployment-record-<tenant>-<env>-<sha>` enriched manifests + `sbom-*` CycloneDX artifacts — all GitHub Actions artifacts, retention per repo defaults (deploy record: enriched manifest only; the durable audit lives in backend via post_audit.py).
- `tests/fixtures/postgres-restore/{seed.sql,manifest.yaml}` — restore-drill fixture + expected per-table counts.
- `tools/*/MANIFEST.lock` format (defined here, files live in each service's `migrations/`).
- Self-host install state: `~/0x-copilot/{docker-compose.yml,.env}` + `pgdata` volume (generated by install.sh:50-103; secrets generated once, never clobbered).
- Env contracts: dev secrets defaults `DEV_AUTH_SECRET=dev-only-not-for-prod`, `DEV_SERVICE_TOKEN=dev-only-service-token` (Makefile:75-76); self-host `.env` keys (see Public Interface).
- Website static assets under `apps/website/public/` (favicons, og-cover, app-run.png) and `CNAME.example` (deliberately unpublished until DNS is live — deploy-website.yml:47-59).

## Key Flows

**1. PR → merge (quality gates).** Dev pushes PR → `ci-repo` always runs (pre-commit over the diff range, gitleaks unless dependabot-authored, tenants lint — ci-repo.yml:36-74) → path-matched component workflows run their suite (e.g. ci-backend.yml:41-68: install with `--require-hashes`, verify pip-compile reproducibility, pip-audit, pytest, A10 route-scope check, SBOM). Note: nothing runs frontend/chat-surface vitest suites (F2), and packages not in a filter can dodge their consumers' CI (F3).

**2. main → signed images → manifest.** Push to main touching release paths → `release-images` matrix builds each Dockerfile at repo context (release-images.yml:84-97) → cosign keyless sign + provenance attestation (private-repo carve-out at :104) → Trivy CRITICAL gate / HIGH triage (:128-149) → `deployment-manifest` job resolves each component's SBOM artifact for this commit via `gh api` (tolerating path-filtered CI that never ran — :202-254) → uploads `deployment-manifest.json`.

**3. Tenant promotion.** Operator dispatches `deploy-production` (tenant_id) → reusable `deploy.yml` runs inside GitHub Environment `tenant-<id>-production` (approvers/wait timers) → validates registry, loads tenant, resolves release SHA (default latest green release-images run — deploy.yml:88-103) → downloads manifest → `verify_release.py` (digest-pinned + cosign identity regex + gh attestation + residency allowlist) → staged-rollout gate (guaranteed-fail as wired for early/general — F4) → OIDC federate → `invoke_orchestrator.py` (argo PATCH+sync+health-poll / helm --atomic / flux reconcile) → `post_audit.py` POSTs to backend `/internal/v1/audit/deploy` → enriched manifest re-uploaded as the deployment record.

**4. Self-host install.** `curl … install.sh | bash` → preflight docker/compose/openssl → fetch `docker-compose.prod.yml` → generate `.env` once with `openssl rand` secrets (install.sh:62-103) → pull GHCR images → boot: postgres 17 (+`atlas_ai` via initdb config) → one-shot `backend-migrate`/`ai-backend-migrate` containers run `scripts/migrate.py apply` → backend (desktop_app composition root, profile `single_user_desktop`), ai-backend API + separate worker sharing postgres LISTEN/NOTIFY, facade, SPA, nginx gateway → installer polls `/v1/health` through the gateway.

**5. Restore drill (C12).** Weekly cron or migration-touching PR → clean postgres:16 → yoyo-apply both services' full migration chains → load seed.sql → each service's `restore_smoke.py` compares COUNT(*) per table against `manifest.yaml` → failure emits operator guidance (postgres-restore-drill.yml:127-137). This is the only CI surface that executes the migrations end-to-end.

**6. Website publish.** Push to main touching `apps/website/**` → `npm ci` (whole workspace) → `SITE_BASE=/ astro build` → dist sanity (required files present, CNAME absent) → `check-links.mjs` boots a local static server and fetches every asset each emitted page actually references (apps/website/scripts/check-links.mjs) → `peaceiris/actions-gh-pages` force-orphan pushes `dist/` to the org Pages repo with a deploy key.

## Test Posture

- **Policy lints are well-tested locally:** all five `tools/check_*.py` have dedicated pytest suites (tools/test_check_*.py, 549 LOC) covering violation/pass/edge cases — but **no CI job or Make target ever runs them** (grep of `.github` + Makefile finds zero references); they only run if a developer happens to invoke pytest inside `tools/`.
- **Deploy scripts have zero tests.** `verify_release.py`, `check_staged_rollout.py`, `invoke_orchestrator.py`, `post_audit.py`, `load_tenant.py`, `validate_tenants.py` — no unit tests anywhere; their only "test" is a real deploy. The staged-rollout bug (F4) is exactly the kind of thing a 20-line unit test would have caught.
- **Workflows themselves are untested/unlintable:** no actionlint hook in pre-commit; the `apply-branch-protection.yml` inline-Python SyntaxError (F1) would have been caught by any smoke execution.
- **Restore drill is the strongest control here** — real migrations, real seed, explicit expected counts, weekly schedule + migration-path PR trigger.
- **JS test suites:** ci-desktop runs desktop's suite; **ci-frontend runs none** despite 157 frontend + 186 chat-surface test files existing (F2). Website has no PR CI at all (build correctness only proven at deploy time on main).
- **Makefile `test`** is an honest curated smoke (8 real files, all verified present) but is a third, hand-maintained list of "what matters" alongside the CI workflows.

## Health Assessment

**Strengths.** For a young repo this is an unusually mature CI/CD design: SHA-pinned actions with least-privilege `permissions:` blocks, per-component SBOMs, hash-pinned requirements for two of three services, keyless signing + provenance + verification-before-promotion, digest-pinned deploys, GitHub-Environment-gated production, a real restore drill, structured secret generation in self-host, and thoughtful inline documentation of *why* (the Trivy SARIF severity trap, the dependabot gitleaks carve-out, the CNAME gate). The deployment-manifest contract cleanly decouples build from promote.

**Weaknesses.** The deploy pipeline's most safety-critical parts are the least exercised: the branch-protection applier has never successfully run (it cannot — SyntaxError), the staged-rollout gate cannot pass for non-canary tiers, and none of the deploy scripts have tests. The hand-maintained path filters have already drifted from the real dependency graph (frontend↔chat-surface, images↔audit-chain). A large fraction of the JS test estate (frontend, chat-surface, chat-transport, surface-renderers) runs on no CI surface at all — the SSOT interaction layer for both apps is effectively untested in CI. Version skew is creeping in: images on Python 3.14 vs 3.13 everywhere else, pyright pinned to 3.11, one unpinned workflow.

**Risks.** (1) A chat-surface regression merging silently and shipping in the next unrelated release-images run; (2) production promotions normalizing `force_deploy=true` because the staged gate can never pass, making the second-approver bypass the *default* path; (3) branch protection existing only as an aspirational JSON file — with `bypass_actors: []` and required contexts that would deadlock path-filtered PRs if it were ever applied; (4) supply-chain asymmetry concentrating exactly on the service with the largest dependency surface (ai-backend, LangChain stack, no hash pinning).

## Findings

F1. **[risk | high | high]** `apply-branch-protection.yml` can never run — inline Python uses top-level `return`. The heredoc script uses `return` at module level in four places; Python raises `SyntaxError: 'return' outside function` at compile time, so every invocation (including dry-run) fails before doing anything. Branch protection therefore cannot be applied or even diffed via the documented path, and the ruleset in git is unenforced aspiration. Evidence: .github/workflows/apply-branch-protection.yml:54,60,74,84; deploy/branch-protection.json:2. Suggestion: wrap the script body in `main()` (or convert to a checked-in `deploy/scripts/apply_branch_protection.py` covered by a pytest smoke), and add an actionlint/py-compile pre-commit hook for inline workflow scripts.

F2. **[risk | high | high]** CI runs no tests for frontend, chat-surface, chat-transport, or surface-renderers. `ci-frontend` only typechecks and builds (ci-frontend.yml:47-52); the only `npm run test` in any workflow is desktop's (ci-desktop.yml:75). 157 test files in `apps/frontend/src` and 186 in `packages/chat-surface/src` (plus chat-transport/surface-renderers suites) never execute in CI, despite chat-surface being the declared SSOT interaction layer for both web and desktop. Evidence: .github/workflows/ci-frontend.yml:33-66; packages/chat-surface/package.json:11; apps/frontend/package.json:10. Suggestion: add `npm run test --workspace` steps for frontend + the three packages to ci-frontend (or a ci-packages workflow with matching path filters).

F3. **[inconsistency | high | high]** CI/release path filters have drifted from the real dependency graph. (a) `ci-frontend` triggers only on `apps/frontend`, `packages/api-types`, `packages/design-system` (ci-frontend.yml:4-12) — but frontend source imports `@0x-copilot/chat-surface` 169× and `@0x-copilot/chat-transport` 3×, so a chat-surface change that breaks the frontend build/typecheck merges without ci-frontend running. (b) `release-images` path filter omits `packages/audit-chain` (release-images.yml:13-26) even though the backend and ai-backend Dockerfiles `pip install ./packages/audit-chain` (services/backend/Dockerfile:12-16) — an audit-chain-only change publishes no new images, so `latest` and the next deploy manifest silently exclude it. (c) Neither filter covers `packages/surface-renderers` for the frontend build chain. Suggestion: derive filters from each component's manifest (or add the missing three paths now, plus a comment convention as in ci-desktop.yml:8-11).

F4. **[risk | high | high]** The production staged-rollout gate is guaranteed to fail for every non-canary tenant. `deploy.yml` synthesizes prior deploys with `tier: "unknown", environment: "unknown"` (deploy.yml:145-147), but `check_staged_rollout.py` only counts records where `tier == upstream and environment == "production"` (deploy/scripts/check_staged_rollout.py:61-68) — "unknown" never matches, so `early`/`general` promotions always exit 1 and can only proceed with `force_deploy=true`, institutionalizing the bypass the policy exists to prevent. The comment acknowledges the missing audit query API but the wiring makes the gate strictly worse than a no-op (it trains operators to force). Suggestion: until the durable audit query exists, have deploy.yml persist tier/environment into the run (e.g. name or an artifact) and reconstruct real values, or downgrade the step to warn-only so `--force` stays exceptional.

F5. **[inconsistency | medium | high]** Supply-chain rigor is asymmetric across the three services: backend and facade use `requirements.in` → pip-compile with `--generate-hashes`, CI reproducibility checks, and `--require-hashes` installs in both CI and Dockerfiles (ci-backend.yml:46-54, services/backend/Dockerfile:16), while ai-backend — the largest dependency surface (LangChain/DeepAgents stack) — has no `requirements.in`, no hash pinning, and plain `pip install -r` in CI and its Dockerfile (ci-ai-backend.yml:41-46, services/ai-backend/Dockerfile:17). Suggestion: bring ai-backend onto the same pip-compile + hashes lane (dependabot already manages it per-directory).

F6. **[inconsistency | medium | high]** Python version skew between what is tested and what ships. All CI, venvs, and the desktop runtime pin Python 3.13 (ci-backend.yml:37; tools/desktop-runtime/manifest.json "3.13.14"), but all three service images run `python:3.14-slim-bookworm` (services/backend/Dockerfile:1, services/backend-facade/Dockerfile:1, services/ai-backend/Dockerfile:1) — production runs an interpreter minor no test suite exercises. `pyrightconfig.json:11` additionally says `"pythonVersion": "3.11"`. Suggestion: pin images to `python:3.13-slim` (or move CI to 3.14) and fix pyright to 3.13.

F7. **[ssot-violation | medium | high]** Migration-manifest logic is triplicated and the documented CI gate does not exist. `tools/check_migration_manifest.py` duplicates parse/digest/render logic that also lives in `services/backend/src/backend_app/db/migrate.py:99-160` and `services/ai-backend/src/agent_runtime/persistence/schema/migrate.py` (both even hard-code the tool's header text), and docs claim it runs in CI ("fails build on mismatch" — docs/roadmap/02-c2-migration-tooling.md:92,147; docs/decisions/0002-migration-tooling.md:39), but no workflow, hook, or Make target invokes it — drift is caught only at service boot. Suggestion: add a `check_migration_manifest.py` step to ci-backend/ci-ai-backend (cheap, no deps) and consider making the tool shell out to a single per-service implementation or vice versa.

F8. **[risk | medium | medium]** `deploy/branch-protection.json` would deadlock merges if ever applied. Its `required_status_checks` (branch-protection.json:36-47) name the path-filtered workflows (`ci-backend / test-and-audit`, `ci-frontend / build-and-audit`, …) — for any PR whose paths don't trigger those workflows the contexts never report and the PR blocks forever; additionally all three backend workflows use the identical job name `test-and-audit`, so the `<workflow> / <job>` context strings may not match GitHub's actual check-run names. `bypass_actors: []` leaves no escape hatch. Suggestion: either drop path filters on required workflows in favor of in-workflow change detection (so they always report), or required-check only `ci-repo` jobs; verify context strings against a live PR before applying.

F9. **[inconsistency | low | high]** DEV_AUTH_BYPASS: docs vs build config. CLAUDE.md states "`DEV_AUTH_BYPASS` no longer exists", yet `docker-compose.dev.yml:40` sets `DEV_AUTH_BYPASS: "true"` (+ `FACADE_DEV_ORG_ID`/`FACADE_DEV_USER_ID`) for the facade, `Makefile:167-170` still guards against it in `check-prod-env`, and `services/backend-facade/src/backend_facade/deployment_profile.py:190` still honors it under the dev profile. The docker-dev stack thus runs a different auth posture than `make dev` (real dev-IdP bearers). Suggestion: either migrate docker-dev to the dev-IdP mint flow and delete the bypass, or fix CLAUDE.md.

F10. **[duplication | low | high]** The nginx gateway config exists twice and has already drifted. `infra/docker/dev-gateway.conf` and the inline `gateway_nginx` config in `deploy/self-host/docker-compose.prod.yml:30-52` are self-described mirrors, but prod adds `proxy_read_timeout 3600s` while dev doesn't — so docker-dev SSE streams idle >60s die at nginx's default read timeout, a bug class the prod copy explicitly guards against. Suggestion: add the timeout to dev-gateway.conf; note the compose `configs:` inline mechanism prevents true single-sourcing, so at minimum cross-reference both with a "change both" marker.

F11. **[inconsistency | low | high]** `deploy-website.yml` is the only workflow using floating action tags (`actions/checkout@v4`, `setup-node@v4`, `peaceiris/actions-gh-pages@v4` — deploy-website.yml:28-70) while every other workflow SHA-pins; the third-party Pages action holds a cross-repo deploy key, making it the worst place for a mutable tag. `release-desktop.yml:73-75` also pins older checkout/setup-node SHAs than the rest. Suggestion: SHA-pin all three and let dependabot's github-actions ecosystem manage bumps.

F12. **[dead-code | low | medium]** The five `tools/test_check_*.py` suites (549 LOC) have no runner: no CI workflow, Make target, or pre-commit hook executes them, so regressions in the policy checkers themselves go undetected (the checkers gate other people's code but nothing gates the checkers). Suggestion: run `pytest tools/` inside ci-repo (needs only stdlib + pytest).

F13. **[inconsistency | low | medium]** apps/website has no PR CI: no workflow typechecks or builds the site on PRs (`deploy-website` runs only on main push), and the `typecheck` script uses `astro check` without declaring `@astrojs/check` (apps/website/package.json:12-18). A PR that breaks the Astro build merges green and fails at deploy time. Suggestion: add a tiny path-filtered ci-website build job (or fold a build step into ci-repo on `apps/website/**`).

F14. **[inconsistency | low | low]** Legacy env-var names in dev configs: `Makefile:96` and `docker-compose.dev.yml:8` use `MCP_TOKEN_VAULT_PROVIDER` which `token_vault.py` treats as a deprecated alias for `MCP_TOKEN_VAULT_BACKEND` (services/backend/src/backend_app/token_vault.py:416-454); self-host already uses the new name. Suggestion: rename in both dev configs before the alias is removed.
