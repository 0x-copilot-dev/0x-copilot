# 0xCopilot

**An agent workspace that runs entirely on your machine — your keys, your data, your model.**

[![ci](https://github.com/0x-copilot-dev/0x-copilot/actions/workflows/ci-cli.yml/badge.svg)](https://github.com/0x-copilot-dev/0x-copilot/actions/workflows/ci-cli.yml)
[![npm](https://img.shields.io/npm/v/@0x-copilot/cli?logo=npm&color=cb3837&label=%400x-copilot%2Fcli)](https://www.npmjs.com/package/@0x-copilot/cli)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey)](tools/cli#platforms)
[![local-first](https://img.shields.io/badge/local--first-BYOK-6f42c1)](#)

0xCopilot takes on real, multi-step work across your apps and finishes it — on your machine, on your API key, on whatever model you pick. No cloud to trust, no seat to buy, no one holding your data but you.

<!-- screenshot placeholder -->

- **Local-first.** The desktop app bundles its own Python runtime and PostgreSQL and runs the whole stack behind a strict-CSP Electron shell. Nothing leaves your machine except the model API calls you configure.
- **BYOK.** Bring your own OpenAI, Anthropic, or Google Gemini key. Keys are stored per-user, encrypted at rest, and never travel in request bodies.
- **Open source.** One monorepo, independently deployable services, self-hostable in one line.

---

## Install (desktop)

Install the `copilot` CLI with npm or Bun — no DMG, no installer, no admin rights:

```bash
npm install -g @0x-copilot/cli   # or: bun add -g @0x-copilot/cli
```

Then, anywhere:

```bash
copilot
```

The first run stages a pinned, checksum-verified local runtime (Python + PostgreSQL + the app's services) and opens the app; every run after is instant. Because the runtime is fetched by your package manager rather than a browser, **macOS Gatekeeper and Windows SmartScreen never flag it** — no "unidentified developer" click-through — and on macOS the bundled binaries are ad-hoc signed at install time so they run on Apple Silicon without an Apple Developer certificate. macOS (Apple Silicon + Intel) and Windows x64. CLI internals: [tools/cli](tools/cli).

### Quickstart

1. **Install & run** — `npm install -g @0x-copilot/cli`, then `copilot`. First run shows a boot screen while it stages and starts the embedded services.
2. **Sign in** — **Connect a wallet** (MetaMask, Rabby, or any [EIP-6963](https://eips.ethereum.org/EIPS/eip-6963) wallet; chains: Ethereum 1, Base 8453, Arbitrum One 42161, Robinhood Chain 4663) or **Continue with Google**.
3. **Add a model key** — **Settings → AI & data → Provider keys** and paste your **OpenAI**, **Anthropic**, or **Google Gemini** key (encrypted at rest, used only for your runs).

Manage it: `copilot doctor` (diagnose) · `copilot uninstall` (remove runtime + data) · `npm rm -g @0x-copilot/cli` (remove the command). Prefer a signed DMG/installer? That's a future channel gated on signing certificates — see [desktop-app.md §10](docs/architecture/desktop-app.md).

---

## Self-host in one line

If you'd rather run the web stack on your own host, one command brings up PostgreSQL 17, the four service images, and an nginx gateway:

```bash
curl -fsSL https://raw.githubusercontent.com/0x-copilot-dev/0x-copilot/main/deploy/self-host/install.sh | bash
```

By default the gateway is published on port `8090`. You need **Docker** (with Compose) and a **host or domain** to serve from. The images are pulled from GitHub Container Registry (`ghcr.io/0x-copilot-dev/0x-copilot-{backend,backend-facade,ai-backend,frontend}`); if those packages are private for your org you'll need to `docker login ghcr.io` first.

Full configuration (env vars, TLS/domain, Google OAuth, wallet login) is in [`deploy/self-host/README.md`](deploy/self-host/README.md). To turn on sign-in providers, see:

- [Google OAuth setup](docs/deployment/google-oauth-setup.md)
- [Wallet login (SIWE)](docs/deployment/wallet-login.md)

---

## Community

Questions, ideas, or want to help build? Join us on **[Discord](https://discord.gg/NhCv7zDkmX)**.

---

## Develop

Prerequisites: Node.js + npm, Python 3.13, and (for the Docker paths) Docker.

```bash
make setup         # one .venv per Python service + node_modules
make setup-hooks   # install pre-commit (ruff, ruff-format, prettier)
```

### Run the web stack locally

```bash
make dev
```

Starts `backend` on `:8100`, `ai-backend` on `:8000`, `backend-facade` on `:8200`, and `frontend` on `:5173`. Open <http://127.0.0.1:5173>; the Vite dev server proxies `/v1/*` to the facade. Every app — browser, curl, Postman, native — must call the **facade** at `:8200`, never `:8100`/`:8000` directly.

One-URL Docker dev stack (frontend + facade + backend + ai-backend behind a gateway at <http://127.0.0.1:8080>):

```bash
OPENAI_API_KEY=$OPENAI_API_KEY make docker-dev
make docker-dev-down
```

### Run the desktop app

Plain `npm run dev --workspace @0x-copilot/desktop` launches the Electron shell against a mock transport (or `COPILOT_FACADE_URL` if you point it at a running facade) — no bundled services.

To exercise the full packaged boot (embedded PostgreSQL + the three Python services under supervision), stage the self-contained runtime once, then launch against it:

```bash
# 1. Stage bundled CPython 3.13 + PostgreSQL 17 + the three services
#    (output lands in apps/desktop/resources/runtime/, gitignored).
#    Use your host's platform/arch — e.g. an Apple Silicon mac:
node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64

# 2. Launch the Electron app against the staged runtime. Setting
#    COPILOT_RUNTIME_DIR turns on the service supervisor.
COPILOT_RUNTIME_DIR="$PWD/apps/desktop/resources" \
  npm run dev --workspace @0x-copilot/desktop
```

See [`apps/desktop/README.md`](apps/desktop/README.md) for the supervisor boot contract and [`apps/desktop/SMOKE.md`](apps/desktop/SMOKE.md) for the manual smoke checklist. Runtime-staging details are in [`tools/desktop-runtime/README.md`](tools/desktop-runtime/README.md).

### Tests

`make test` runs a curated cross-service smoke subset. For a service's full suite or a single test, use that service's own `.venv`:

```bash
# Full suite for one service
cd services/ai-backend && .venv/bin/python -m pytest

# Single file / single test
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/agent/test_runtime_factory.py
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/agent/test_runtime_factory.py::TestName::test_method
```

Frontend / desktop / TS:

```bash
npm run typecheck --workspace @0x-copilot/frontend
npm run build --workspace @0x-copilot/frontend
npm run test --workspace @0x-copilot/desktop
```

---

## Architecture

One GitHub monorepo, microservice-style runtime: each service owns its API, Docker image, local dependency environment, tests, and deploy path. **Service boundaries are hard** — no deployable component imports another's `src/`. Cross-component integration is HTTP, generated contracts (`packages/api-types`), or constants-only (`packages/service-contracts`).

```text
0x-copilot/
  apps/
    desktop/          # Electron shell — bundled runtime, supervised boot, strict CSP
    frontend/         # Vite + React web surface
    website/          # 0xcopilot.tech marketing site (Astro)
  packages/
    api-types/        # TypeScript contracts for app-facing payloads
    design-system/    # React primitives + tokens
    chat-surface/     # framework-agnostic chat UI surface
    chat-transport/   # transport client for runs/events/streaming
    surface-renderers/# renderers for agent output surfaces
    audit-chain/      # tamper-evident audit chain primitives
    service-contracts/# constants-only Python package shared via PYTHONPATH
  services/
    backend/          # MCP registration, OAuth state, token vault, skills, audit, identity
    backend-facade/   # product-facing API — the ONLY surface apps may call
    ai-backend/       # agent runtime (FastAPI + LangGraph + Deep Agents)
  tools/
    desktop-runtime/  # stages + boots the self-contained desktop runtime
  deploy/             # self-host + tenant/rollout tooling
  docs/               # architecture, deployment, roadmap, security
```

**Request path:** browser → Vite proxy (or nginx ingress in prod) → `backend-facade:8200` → either `backend:8100` (MCP / skills / OAuth / identity) or `ai-backend:8000` (conversations, runs, events, approvals). The facade never exposes `/internal/v1/*`; the backend's `/internal/v1/*` is consumed only by `ai-backend`.

**Published images:** `ghcr.io/0x-copilot-dev/0x-copilot-{backend,backend-facade,ai-backend,frontend}`.

Deeper reading:

- [`docs/architecture/workspace-topology.md`](docs/architecture/workspace-topology.md)
- [`docs/architecture/service-boundaries.md`](docs/architecture/service-boundaries.md)
- [`services/ai-backend/README.md`](services/ai-backend/README.md) and [`services/ai-backend/docs/README.md`](services/ai-backend/docs/README.md)
- [`docs/dev-testing.md`](docs/dev-testing.md) — curl, Postman, and the dev IdP for non-browser callers.

---

## Auth & keys

0xCopilot supports four ways in, plus bring-your-own model keys:

- **Dev IdP (development only).** `make dev` uses a signed bearer minted by `POST /v1/dev/identity/mint`, registered only when `BACKEND_ENVIRONMENT=development`. The bearer is signed with `ENTERPRISE_AUTH_SECRET` and verified by the same path production uses — there is no `DEV_AUTH_BYPASS` shortcut. Mint one for curl with `make dev-bearer` (see [API testing](#api-testing-curl-postman)).
- **Google OAuth.** Set `GOOGLE_OAUTH_CLIENT_ID` (and `GOOGLE_OAUTH_CLIENT_SECRET` for web clients) and the login screen shows **Continue with Google**. Facade routes: `GET /v1/auth/providers`, `GET /v1/auth/oidc/google/start`, `GET /v1/auth/oidc/callback`. Step-by-step: [Google OAuth setup](docs/deployment/google-oauth-setup.md).
- **Wallet login (SIWE).** Sign-In-With-Ethereum via any EIP-6963 wallet. Facade routes: `POST /v1/auth/siwe/nonce`, `POST /v1/auth/siwe/verify`. Chain allowlist defaults to Ethereum, Base, Arbitrum One, and Robinhood Chain (`4663`), tunable via `SIWE_ALLOWED_CHAIN_IDS`. Details: [Wallet login (SIWE)](docs/deployment/wallet-login.md).
- **BYOK provider keys.** Per-user OpenAI / Anthropic / Google Gemini keys, encrypted at rest via `TokenVault`, managed in **Settings → AI & data → Provider keys** (`/v1/settings/provider-keys`). Plaintext never appears in any response, log, or audit row — only a `key_hint`.

Service-to-service calls (facade ↔ backend, ai-backend ↔ backend) use `ENTERPRISE_SERVICE_TOKEN` plus `x-enterprise-org-id` / `x-enterprise-user-id` headers derived from the verified user bearer. Treat caller-supplied identity, role, scope, and tenant as untrusted unless derived from a verified session or token. Production fails closed without `ENTERPRISE_AUTH_SECRET` and `ENTERPRISE_SERVICE_TOKEN`.

Do not hardcode bearers, JWTs, or service tokens in source, Dockerfiles, README examples, or committed `.env` files. Mint a fresh bearer per session when you need one.

---

## API testing (curl, Postman)

For non-browser callers, mint a dev bearer once and reuse it. All callers must hit the **facade** at `:8200`.

```bash
export TOKEN=$(make dev-bearer)                       # default: sarah_acme
export TOKEN=$(make dev-bearer PERSONA=marcus_admin)  # admin variant

curl -sS http://127.0.0.1:8200/v1/me/profile \
  -H "Authorization: Bearer $TOKEN" | jq
```

Full recipes (conversations, runs, SSE streaming, MCP install, per-chat connector scope PATCH) and Postman setup live in [`docs/dev-testing.md`](docs/dev-testing.md).

### Smoke test

After `make dev` is up, this should produce a streaming run:

```bash
TOKEN=$(make dev-bearer)
BASE=http://127.0.0.1:8200

CONV=$(curl -sS -X POST $BASE/v1/agent/conversations \
  -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"title":"Local smoke"}' | jq -r .conversation_id)

curl -sS -X POST $BASE/v1/agent/runs \
  -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d "{\"conversation_id\":\"$CONV\",\"user_input\":\"Hi\"}" | jq .stream_url
```

You can also send `Hi` from the UI composer at <http://127.0.0.1:5173>.

---

## Streaming runtime events

The chat UI opens `/v1/agent/runs/{run_id}/stream` through the facade. The AI backend persists ordered `RuntimeEventEnvelope` records with a monotonic `sequence_no` per run before replaying or streaming them as `runtime_event` SSE frames. Clients track the highest `sequence_no` and reconnect with `?after_sequence=N` so a paused stream resumes without replay. Replay-only is `GET /v1/agent/runs/{run_id}/events`. Use the backend's projected `activity_kind` / `display_title` / `summary` / `status` fields — do not derive activity types from event-name prefixes.

---

## Production build

`make prod` validates required secrets and refuses to register the dev IdP routes when `BACKEND_ENVIRONMENT != development`:

```bash
ENTERPRISE_AUTH_SECRET=... \
ENTERPRISE_SERVICE_TOKEN=... \
MCP_TOKEN_VAULT_SECRET=... \
OPENAI_API_KEY=... \
make prod
```

Deploy the built images with your production orchestrator and managed secret store. The backend production runtime requires a persistent MCP registry store and a managed token-vault adapter before it can serve production traffic.

---

## Repo rules

- **Service boundaries are hard.** No deployable component imports another's `src/`. Cross-component integration is HTTP, generated contracts (`packages/api-types`), or constants-only (`packages/service-contracts`). Never add a sibling service to `PYTHONPATH`, never reuse another service's `.venv`, never use relative imports across deployable boundaries.
- **Apps call the facade only** — never `backend` or `ai-backend` directly.
- Don't put AI orchestration in `backend-facade`; don't put tenant auth, billing, or product persistence in `ai-backend`.
- Each deployable component owns its dependency environment and Dockerfile. Python services use a service-local `.venv` + `requirements.txt`; the web/desktop apps use the npm workspace.
- Don't create shared packages just to avoid small duplication — share only stable contracts and truly cross-cutting primitives.
- Don't commit secrets, real `.env` files, tokens, certificates, or production credentials.

Path-scoped engineering rules live in hierarchical `CLAUDE.md` files that load when you touch that subtree (`services/*/CLAUDE.md`, `apps/frontend/CLAUDE.md`, `packages/*/CLAUDE.md`).

---

## License

[MIT](LICENSE) © 0xCopilot
