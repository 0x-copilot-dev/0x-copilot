# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Product direction: desktop-first, web deprecated

**`apps/desktop` is the product.** `apps/frontend` (the web surface) is
**deprecated** — keep it building, do not invest in it. When a change could land
in either, it lands in desktop.

Practically, for anything you are asked to build:

- Default to the desktop app. Do not add web-only features, web-only routes, or
  web-specific UI polish unless explicitly asked.
- Shared behaviour belongs in `packages/chat-surface` (the single-source-of-truth
  interaction layer both hosts mount), not in `apps/frontend`.
- Web changes are still legitimate for keeping it green — build fixes, dependency
  bumps, shared-package fallout, security fixes. Nothing here says let it break.
- Do not delete the web app or its CI. Deprecated is not removed, and both hosts
  still mount the same shared surface.

## Workspace Layout

Monorepo with independently deployable components. Each Python service owns its own Python 3.13 `.venv`, `requirements.txt`, `pyproject.toml`, `Dockerfile`, tests, and deploy path. The frontend and desktop apps share the npm workspace (`apps/*`, `packages/*`). Implemented paths only:

- `services/ai-backend` — agent runtime (FastAPI + LangGraph + Deep Agents). Modules: `agent_runtime/` (domain), `runtime_api/` (HTTP/SSE), `runtime_worker/` (queued run executor), `runtime_adapters/` (in-memory + postgres stores).
- `services/backend` — core backend (`backend_app/`): MCP registration, OAuth state, token vault, user skills, audit events, identity (dev IdP, Google OAuth, SIWE, BYOK provider keys).
- `services/backend-facade` — product-facing API (`backend_facade/`); proxies `/v1/*` to `backend` and `ai-backend`. **Apps must call only the facade.**
- `apps/frontend` — Vite + React web surface. **DEPRECATED** (see above); keep green, do not extend.
- `apps/desktop` — Electron client (`@0x-copilot/desktop`); supervises an embedded PostgreSQL + the three Python services from a bundled runtime. Staging/boot tooling lives in `tools/desktop-runtime/`.
- `apps/website` — `0xcopilot.tech` marketing site (Astro), deployed to GitHub Pages.
- `packages/api-types` — TypeScript contracts for app-facing payloads.
- `packages/design-system` — React primitives + tokens.
- `packages/chat-surface` — framework-agnostic chat UI surface.
- `packages/chat-transport` — transport client for runs / events / streaming.
- `packages/surface-renderers` — renderers for agent output surfaces.
- `packages/audit-chain` — tamper-evident audit-chain primitives (shared Python + TS).
- `packages/service-contracts` — constants-only Python package shared across services via `PYTHONPATH`.

`packages/shared-config` is planned — do not import from it until it exists.

## Commands

Setup (creates one `.venv` per Python service plus `node_modules`):

```bash
make setup
make setup-hooks   # install pre-commit
```

Run the full local stack (backend on :8100, ai-backend on :8000, facade on :8200, frontend on :5173; UI proxies `/v1/*` to facade):

```bash
make dev
```

Docker dev stack (one URL at http://127.0.0.1:8080):

```bash
OPENAI_API_KEY=$OPENAI_API_KEY make docker-dev
make docker-dev-down
```

Desktop app. Plain `npm run dev --workspace @0x-copilot/desktop` runs the Electron shell against MockTransport (or `COPILOT_FACADE_URL`). To exercise the supervised packaged boot (embedded PostgreSQL + the three services), stage the runtime once, then set `COPILOT_RUNTIME_DIR`:

```bash
node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64   # match your host
COPILOT_RUNTIME_DIR="$PWD/apps/desktop/resources" npm run dev --workspace @0x-copilot/desktop
```

Details: `apps/desktop/README.md` (supervisor boot contract), `apps/desktop/SMOKE.md`, `tools/desktop-runtime/README.md`.

Self-host (web stack via Docker + GHCR images):

```bash
curl -fsSL https://raw.githubusercontent.com/0x-copilot-dev/0x-copilot/main/deploy/self-host/install.sh | bash
```

See `deploy/self-host/README.md`, `docs/deployment/google-oauth-setup.md`, and `docs/deployment/wallet-login.md`.

Production build (validates required secrets, refuses to register the dev IdP routes when `BACKEND_ENVIRONMENT != development`):

```bash
ENTERPRISE_AUTH_SECRET=... ENTERPRISE_SERVICE_TOKEN=... MCP_TOKEN_VAULT_SECRET=... OPENAI_API_KEY=... make prod
```

Curated cross-service smoke tests (`make test`) run a small subset. To run a service's full suite or a single test, use that service's own `.venv`:

```bash
# Full suite for one service
cd services/ai-backend && .venv/bin/python -m pytest

# Single test file
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/agent/test_runtime_factory.py

# Single test
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/agent/test_runtime_factory.py::TestName::test_method
```

Frontend / TS:

```bash
npm run dev --workspace @0x-copilot/frontend
npm run typecheck --workspace @0x-copilot/frontend
npm run build --workspace @0x-copilot/frontend
npm run typecheck --workspace @0x-copilot/api-types
```

Lint/format runs through pre-commit (ruff + ruff-format for Python, prettier for JS/TS/CSS/MD/YAML).

Hitting the API from curl or Postman in dev:

```bash
export TOKEN=$(make dev-bearer)                       # default: sarah_acme
export TOKEN=$(make dev-bearer PERSONA=marcus_admin)  # admin variant
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8200/v1/me/profile
```

`docs/dev-testing.md` has full recipes (conversations, runs, SSE streaming, MCP catalog/install, per-chat connector scope PATCH) and Postman setup. Always call the **facade** at `:8200` — never `:8100`/`:8000` directly, even in dev.

## Architecture

**Service boundaries are hard.** No deployable component imports another's `src/`. Cross-component integration is HTTP, generated contracts (`packages/api-types`), or constants-only (`packages/service-contracts`). Never add a sibling service to `PYTHONPATH`, never reuse another service's `.venv`, never use relative imports across deployable boundaries.

**Request path:** browser → Vite proxy (or nginx ingress in prod) → `backend-facade:8200` → either `backend:8100` (MCP / skills / OAuth) or `ai-backend:8000` (conversations, runs, events, approvals). Facade does not expose `/internal/v1/*`. Backend's `/internal/v1/*` is consumed only by `ai-backend` (MCP cards, client sessions, RPC proxy, skill bundles).

**AI backend runtime split:**

- `agent_runtime/` — pure domain. `execution/` (graph, deep agent builder, runtime contracts), `capabilities/` (tools, skills, MCP loaders + middleware + permissions), `context/memory`, `delegation/subagents`, `persistence/` (records, schema, ports), `observability/`, `api/` (presentation/service layer for the runtime API).
- `runtime_api/` — FastAPI app exposing conversations, runs, event replay, SSE streaming, cancel, approvals.
- `runtime_worker/` — separate process that claims queued runs, drives the LangGraph execution, and emits typed `RuntimeEventEnvelope` records (`model_delta`, `final_response`, `run_completed`, tool/subagent/stream events). The API can also start an in-process worker via `RUNTIME_START_IN_PROCESS_WORKER=true` for local dev.
- `runtime_adapters/` — `in_memory` for tests/dev, `postgres` for shared-store production-style runs. Selected by `RUNTIME_STORE_BACKEND`.

**Streaming model:** events are persisted with monotonic `sequence_no` per run. Clients open `GET /v1/agent/runs/{run_id}/stream?after_sequence=N` and reconnect with the highest received `sequence_no` to resume without replay. Replay-only is `GET /v1/agent/runs/{run_id}/events`. Backend projects events into `activity_kind`/`display_title`/`summary`/`status` for the frontend; do not derive activity types from event-name prefixes.

**Auth in dev (W0.1 dev IdP):** `DEV_AUTH_BYPASS` no longer exists. Dev sessions go through a real signed bearer minted by `POST /v1/dev/identity/mint` (only registered when `BACKEND_ENVIRONMENT=development`). The frontend's `AuthContext` auto-mints on 401 via `_devEnsureBearer` for the active persona (`enterprise.dev.persona_slug` in localStorage; default `sarah_acme`). The bearer is signed with `ENTERPRISE_AUTH_SECRET` and verified by the same path production uses — no separate bypass code. `make dev-bearer PERSONA=...` mints one for curl. Production fails closed if `ENTERPRISE_AUTH_SECRET` or `ENTERPRISE_SERVICE_TOKEN` is missing. With `ENTERPRISE_SERVICE_TOKEN` set, internal callers must also send `x-enterprise-org-id` and `x-enterprise-user-id`. Treat caller-supplied identity/role/scope/tenant as untrusted unless derived from a verified session/token.

**End-user auth (real sign-in, dev IdP unchanged):**

- **Google OAuth** — deployment-global provider, enabled when `GOOGLE_OAUTH_CLIENT_ID` is set (`GOOGLE_OAUTH_CLIENT_SECRET` for web clients; desktop is PKCE-only). Backend `backend_app/identity/google.py`; facade `/v1/auth/providers`, `/v1/auth/oidc/google/start`, `/v1/auth/oidc/callback`; frontend `LoginScreen` "Continue with Google" (renders only when `/v1/auth/providers` advertises `google`). Setup: `docs/deployment/google-oauth-setup.md`.
- **SIWE wallet login** — Sign-In-with-Ethereum (EIP-4361) via EIP-6963 wallets. Backend `backend_app/identity/siwe.py`; facade `/v1/auth/siwe/{nonce,verify}`; frontend `features/auth/WalletSignIn.tsx`. Chain allowlist `SIWE_ALLOWED_CHAIN_IDS` (default `1,8453,42161,4663` = Ethereum, Base, Arbitrum One, Robinhood Chain); origin `SIWE_ORIGIN` must match the serving origin. The EIP-4361 message template is **duplicated byte-identically** in `apps/frontend/src/features/auth/siweMessage.ts` and `services/backend/src/backend_app/identity/siwe.py` — change both together. Setup: `docs/deployment/wallet-login.md`.
- **BYOK provider keys** — per-user OpenAI / Anthropic / Google Gemini keys, encrypted at rest via `TokenVault`. Backend `backend_app/provider_keys/` (`/v1/settings/provider-keys`); frontend Settings → AI & data → `ProviderKeys.tsx`. Responses carry only a `key_hint`; plaintext never appears in logs or audit rows.

**MCP OAuth:** discovery + dynamic client registration when supported; per-server pre-registered client fields (`client_id`, `client_secret`, `scope`, `authorization_endpoint`, `token_endpoint`) when not. Secrets stored via `TokenVault` (local for dev only — production must inject a managed adapter and a persistent MCP registry store).

## Engineering Rules

Path-scoped rules live in hierarchical `AGENTS.md` files and load automatically when you touch files in that subtree:

- [services/ai-backend/AGENTS.md](services/ai-backend/AGENTS.md) — AI backend engineering + Python/Pydantic standards
- [services/ai-backend/tests/AGENTS.md](services/ai-backend/tests/AGENTS.md) — unit testing rules
- [services/ai-backend/docs/AGENTS.md](services/ai-backend/docs/AGENTS.md) — spec-first workflow
- [services/backend/AGENTS.md](services/backend/AGENTS.md), [services/backend-facade/AGENTS.md](services/backend-facade/AGENTS.md) — backend services
- [apps/frontend/AGENTS.md](apps/frontend/AGENTS.md) — frontend app
- [packages/design-system/AGENTS.md](packages/design-system/AGENTS.md) — design system producer
- [packages/api-types/AGENTS.md](packages/api-types/AGENTS.md) — public contract stewardship

`.cursor/rules/*.mdc` mirrors are kept for Cursor users. Treat the `AGENTS.md` files as authoritative when they disagree.

## Service Boundaries

Hard rule: no deployable component imports another's `src/`. This is non-negotiable for `apps/*` and `services/*`.

- Cross-component integration: HTTP, generated contracts ([packages/api-types](packages/api-types)), or constants-only ([packages/service-contracts](packages/service-contracts)).
- Apps call `backend-facade` only — never `backend` or `ai-backend` directly.
- `backend-facade` may call `backend` and `ai-backend` over HTTP, but must not import their Python modules.
- Don't put AI orchestration in `backend-facade`. Don't put tenant auth, billing, or product persistence in `ai-backend`.
- Never add a sibling component to `PYTHONPATH`, never reuse another service's `.venv`, never use relative imports across deployable boundaries.
- `backend` currently owns MCP registration, OAuth/token state, user skills, audit events. Tenants, IdP integration, permissions, product persistence, admin workflows, and jobs are its target home.
- `packages/shared-config` is planned — do not import from it until it exists.
- Add or update a service-boundary doc before creating a new service or shared package.

### What `ai-backend` is (state it positively, or it accretes)

**A lean Deep Agents / LangGraph runtime, plus the adapters that map LangGraph output
into our event format.** That adapter layer is `runtime_worker/stream_*`,
`capabilities/middleware/`, and `operations/presentation_boundary` — it belongs here by
design, not by accident.

A source-level audit ([docs/audit/ai-backend-smells/BOUNDARY-AUDIT.md](docs/audit/ai-backend-smells/BOUNDARY-AUDIT.md))
found ~80% of the service genuinely is that runtime, ~20% is misplaced, ~4% is dead.
The rule below is what keeps the 20% from growing.

**Do not add to `ai-backend`:** billing / pricing / usage rollups · tenant or workspace
admin CRUD · product persistence (sharing, inbox, todos, notifications, model catalog) ·
one-shot data migrations · eval / benchmark / promotion tooling · a second audit stream
or another copy of the logging contract. Each of those already has a home in `backend`.

### Policy decision vs policy enforcement (PDP/PEP) — the rule that is easy to get wrong

**Policy data belongs to `backend`; policy _enforcement_ stays in the runtime.** The
model chooses tools mid-graph-loop — the facade sees one "start a run" call and never
sees the resulting tool calls, so the enforcement point cannot live outside the loop.

The required pattern, already implemented by `ToolUsePolicySnapshot.from_response`:
**snapshot the policy once at run start, enforce in-process, POST the facts afterwards.**
Never put a per-call HTTP hop on the tool path — it is a network round-trip on the
hottest path in the system. "We enforce here" is correct; "we author, store or
administer the policy here" is the violation.

## Branching & Releases

**Open PRs against `dev`, never `main`.** `main` moves only via `promote-to-main.yml`
(manual dispatch, fast-forward) and the version-bump commit `release-cli.yml` writes.
Full detail: [docs/ci-cd/branching-and-release.md](docs/ci-cd/branching-and-release.md).

```
feature ──PR──▶ dev ──promote-to-main.yml──▶ main ──release-cli.yml──▶ npm
```

**The working clone tracks `dev`, not `main`.** Branch from `dev`, PR into `dev`:

```bash
git checkout dev && git pull
git checkout -b feat/your-change
gh pr create --base dev
```

Never `git checkout main` to "get the latest" — `main` is a release pointer and
is normally _behind_ `dev`, so it is the stale one between promotions. It also
lags on repo hygiene: a fix merged to `dev` (a `.gitignore` rule, a CI gate) is
simply absent from a `main` checkout until the next promotion.

Promotion and publishing are both manual dispatches, dry-run by default:

```bash
gh workflow run promote-to-main.yml -r dev -f dry_run=false   # dev -> main
gh workflow run release-cli.yml -r main -f bump=auto -f dry_run=false
```

Promotion is a **fast-forward**, not a merge and not a squash: `main` ends up
byte-identical to `dev`, same commits and same SHAs. Squashing is deliberately
avoided because the changelog is built from the individual Conventional Commits
inside each PR.

Merging needs write access, held by two collaborators. Their own PRs need no
review; everyone else's need 2 approvals plus CODEOWNERS.

Releases are manual-dispatch and dry-run by default. Versioning is pre-1.0: a
**breaking change bumps MINOR** (`0.1.4 → 0.2.0`), everything else bumps PATCH,
because npm resolves `^0.1.4` as `>=0.1.4 <0.2.0` — the minor digit is what
actually breaks consumers before 1.0. Never hand-edit `tools/cli/package.json`'s
version or `tools/cli/CHANGELOG.md`; `tools/cli_release.py` owns both and derives
them from Conventional Commit subjects. Publishing uses npm Trusted Publishing
(OIDC), so there is no npm token — and the **workflow filename is part of the
trust record**, so renaming `release-cli.yml` breaks publishing.

### Check which account `gh` is using before any PR or merge

Run `gh auth status` first. It must report **`0x-copilot-dev`**. That account holds
repo admin and is a ruleset bypass actor; the machine's default `gh` login is a
different user, so an agent that skips this check acts as the wrong identity and
gets confusing permission failures.

The credentials live in a repo-local config directory selected by `GH_CONFIG_DIR`:

```bash
export GH_CONFIG_DIR="$PWD/.gh-cli-0x-copilot-dev"   # from the repo root
gh auth status                                        # must say 0x-copilot-dev
```

Two traps:

- **Git worktrees do not inherit it.** `$PWD` inside `.claude/worktrees/<id>/` is
  not the repo root, and the config directory only exists in the main checkout —
  so `gh` silently falls back to the default account. Point `GH_CONFIG_DIR` at the
  **main checkout's** absolute path, not a relative one.
- **`.gh-cli-*/hosts.yml` holds a live OAuth token and this repository is
  public.** The pattern `.gh-cli-*/` is gitignored; never remove that entry, never
  commit such a directory, and never print the file's contents.

## CI/CD & Docker

- CI is path-filtered — unrelated apps/services should not rebuild on unrelated changes.
- Every deployable backend service: own `requirements.txt`, service-local Python 3.13 `.venv`, `Dockerfile`, image, deploy path.
- Every deployable frontend app: own package manifest, lockfile-managed deps, `Dockerfile`, image, deploy path.
- Dockerfiles install only the owning component's runtime deps plus explicitly allowed shared package build inputs. Builds are reproducible and scoped to the service being built.
- PR CI must not require production secrets or live third-party services.
- Production deploys require GitHub Environments with manual approval.
- Never commit secrets, real `.env` files, tokens, certificates, or production credentials.

### CI rules that have already cost us a day

Each of these shipped, broke something silently, and is now guarded by a test in
`tools/test_apply_branch_protection.py`. If one of those tests fails it is telling
you something true — fix the cause, do not relax the test.

1. **A required status check must be unconditional.** GitHub reports a required
   check that never starts as _pending_, not skipped — so adding `paths:` to a
   required workflow wedges every PR whose diff misses those paths
   (`mergeStateStatus: BLOCKED`, waiting on a job that will never begin). If you
   make a check required, delete its `paths:` filter. `ci-repo` and `ci-gates`
   are unconditional for exactly this reason.
2. **Workflow logic belongs in `tools/` with a test, not in a YAML heredoc.** A
   heredoc is invisible to ruff, pytest and review. `apply-branch-protection.yml`
   carried a module-level `return`, raised `SyntaxError` on every dispatch, and
   left `main` unprotected for months. Note that `ast.parse` accepts that code —
   `compile()` is what catches it.
3. **Never interpolate `${{ }}` inside an embedded script.** A `type: boolean`
   input substitutes as lowercase `true`, which is a `NameError` in Python, and
   any value pasted into program text is a script-injection vector. Pass values
   through `env:` and read `os.environ`.
4. **Required-check contexts are bare job names** (`lint-and-secrets`), with the
   matrix suffix where applicable (`cli (ubuntu-latest)`). The `workflow / job`
   form the PR UI displays matches nothing. Verify with
   `gh api repos/OWNER/REPO/commits/SHA/check-runs --jq '.check_runs[].name'`.
5. **`administration` is not a valid `permissions:` key.** It makes the whole
   workflow unparseable (HTTP 422 on dispatch). Repository administration cannot
   be granted to `GITHUB_TOKEN` at all; ruleset writes need a fine-grained PAT.
6. **This repo is owned by a user, not an org.** Two consequences that look like
   bugs: `CODEOWNERS` cannot reference `@org/team` handles (they resolve to
   nobody), and rulesets cannot use `actor_type: Integration` bypass actors. That
   is why `main` carries fewer rules than `dev` — read the `$model` block in
   `deploy/branch-protection.json` before changing it.
7. **A dormant workflow rots.** Path-filtered drills that rarely trigger were
   found broken the first time they ran in months — one had never installed
   `pytest`, another booted without a required secret. If you touch a drill,
   confirm it actually ran and passed; a gate that cannot start reports nothing.
8. **Don't merge red, and don't trust `gh pr merge --auto` to wait.** With no
   branch protection requiring a check, `--auto` merges immediately. Poll the
   checks and merge deliberately.

## Compliance Reviews

When reviewing for bank, government, or other regulated buyers:

- A control counts as implemented only when code, config, tests, and docs all support it. Architecture intent is not enough.
- Separate product controls from deployment controls. TLS, WAF, KMS, SIEM, backup, private networking, branch protection are deployment controls — mark "not evidenced in repo" unless deploy config or runbook is present.
- Treat caller-supplied identity, role, scope, tenant, org, and user values as untrusted unless derived from a verified session, token, mTLS identity, or IdP claim.
- For every sensitive workflow, answer: who can do it, who approved it, what changed, where it is logged, how long it is retained, and how it is deleted.
- For retention and deletion, verify: conversations, messages, runs, events, outbox rows, payload refs, memory, checkpoints, approvals, tool invocations, MCP tokens, skills, audit records.
- Never mark audit logging complete if the adapter is no-op, in-memory only, mutable without controls, or not exportable to customer SIEM.
- Require tests for tenant isolation, unauthorized access, deletion cascades, retention expiry, audit immutability, redaction, legal hold.
- Findings include: severity, confidence, evidence paths, exploit/compliance impact, concrete remediation. Separate confirmed gaps from deployment assumptions.

## Conventions Worth Knowing

- Python 3.13 everywhere. Services share constants from `packages/service-contracts/src` via `PYTHONPATH`; Docker installs the package during build.
- Provider keys (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY`) live in `services/ai-backend/.env` for local dev; never in run-request bodies.
- The older `services/ai-backend/docker-compose.yml` is a production-style API+worker+Postgres compose. Use `docker-compose.dev.yml` (root) for end-to-end local Docker.
- Don't create shared packages for small duplication — share only stable contracts and truly cross-cutting primitives.
- Don't commit secrets, real `.env` files, tokens, certificates, or production credentials.
- `packages/chat-surface` is the **single-source-of-truth interaction layer** for the desktop redesign: both `apps/frontend` (web) and `apps/desktop` (Electron) mount the same `ChatShell` + destinations + Run cockpit + Settings + ⌘K palette, and bind data through their OWN host adapters (web `features/*/Route.tsx`, desktop `renderer/destinationBinders.tsx`) — no `apps/*→apps/*` imports. The package is substrate-agnostic (ports only; bare `window`/`fetch`/`localStorage` are eslint-banned). Tokens are the v2 "quiet" set in `packages/design-system/src/styles.css`. See `docs/plan/desktop-redesign/DEV-GUIDE.md` for the architecture map + extension recipes, and `packages/chat-surface/AGENTS.md` for the SSOT pattern.
