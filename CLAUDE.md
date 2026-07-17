# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Workspace Layout

Monorepo with independently deployable components. Each Python service owns its own Python 3.13 `.venv`, `requirements.txt`, `pyproject.toml`, `Dockerfile`, tests, and deploy path. The frontend and desktop apps share the npm workspace (`apps/*`, `packages/*`). Implemented paths only:

- `services/ai-backend` — agent runtime (FastAPI + LangGraph + Deep Agents). Modules: `agent_runtime/` (domain), `runtime_api/` (HTTP/SSE), `runtime_worker/` (queued run executor), `runtime_adapters/` (in-memory + postgres stores).
- `services/backend` — core backend (`backend_app/`): MCP registration, OAuth state, token vault, user skills, audit events, identity (dev IdP, Google OAuth, SIWE, BYOK provider keys).
- `services/backend-facade` — product-facing API (`backend_facade/`); proxies `/v1/*` to `backend` and `ai-backend`. **Apps must call only the facade.**
- `apps/frontend` — Vite + React web surface.
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

Path-scoped rules live in hierarchical `CLAUDE.md` files and load automatically when you touch files in that subtree:

- [services/ai-backend/CLAUDE.md](services/ai-backend/CLAUDE.md) — AI backend engineering + Python/Pydantic standards
- [services/ai-backend/tests/CLAUDE.md](services/ai-backend/tests/CLAUDE.md) — unit testing rules
- [services/ai-backend/docs/CLAUDE.md](services/ai-backend/docs/CLAUDE.md) — spec-first workflow
- [services/backend/CLAUDE.md](services/backend/CLAUDE.md), [services/backend-facade/CLAUDE.md](services/backend-facade/CLAUDE.md) — backend services
- [apps/frontend/CLAUDE.md](apps/frontend/CLAUDE.md) — frontend app
- [packages/design-system/CLAUDE.md](packages/design-system/CLAUDE.md) — design system producer
- [packages/api-types/CLAUDE.md](packages/api-types/CLAUDE.md) — public contract stewardship

`.cursor/rules/*.mdc` mirrors are kept for Cursor users. Treat the `CLAUDE.md` files as authoritative when they disagree.

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

## CI/CD & Docker

- CI is path-filtered — unrelated apps/services should not rebuild on unrelated changes.
- Every deployable backend service: own `requirements.txt`, service-local Python 3.13 `.venv`, `Dockerfile`, image, deploy path.
- Every deployable frontend app: own package manifest, lockfile-managed deps, `Dockerfile`, image, deploy path.
- Dockerfiles install only the owning component's runtime deps plus explicitly allowed shared package build inputs. Builds are reproducible and scoped to the service being built.
- PR CI must not require production secrets or live third-party services.
- Production deploys require GitHub Environments with manual approval.
- Never commit secrets, real `.env` files, tokens, certificates, or production credentials.

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
