# Enterprise Search

Enterprise Search is the workspace for a broader enterprise work surface: one product that helps executives and employees search, understand, and act across company systems such as Slack, Google Workspace, Atlassian, internal APIs, MCP servers, and enterprise knowledge stores.

This is one GitHub monorepo with multiple deployable components. The runtime architecture is microservice-style: each service owns its API, Docker image, local dependency environment, tests, and deployment path.

The workspace now includes initial deployable scaffolding for `apps/frontend`, `services/backend-facade`, `services/backend`, `services/ai-backend`, `packages/api-types`, and `packages/design-system`.

## Current And Target Repository Layout

Implemented paths are present today. Planned paths describe the target
architecture and should not be imported from or referenced by builds until they
exist.

```text
enterprise-search/
  apps/
    frontend/        # implemented
    mac/             # planned
    windows/         # planned
  services/
    backend-facade/  # implemented
    backend/         # implemented
    ai-backend/      # implemented
  packages/
    api-types/       # implemented
    design-system/   # implemented
    shared-config/   # planned
  infra/
    docker/
    compose.yaml
  docs/
    architecture/
    ci-cd/
    decisions/
  .cursor/
    rules/
  .github/
    workflows/
```

## Monorepo, Microservice Runtime

Monorepo and microservices are separate decisions. This repo should keep related product code together while allowing services to deploy independently.

- Monorepo: one GitHub repository, one PR can update app, API contract, service, and docs together.
- Microservice-style runtime: backend services are independently built, tested, containerized, and deployed.
- Shared packages: stable contracts and cross-cutting primitives only, not a place to hide business ownership.

## Components

- `services/ai-backend`: implemented AI orchestration backend for Deep Agents, LangGraph, LangChain tools, dynamic MCP loading, skills, context/memory management, subagents, streaming, and retrieval orchestration.
- `services/backend-facade`: implemented product-facing API surface that frontend and native apps call. It hides internal service topology.
- `services/backend`: implemented core backend slice for MCP registration, OAuth state, token storage, user skills, and audit events. Tenant auth, permissions, billing/admin workflows, broader product persistence, and operational jobs remain target backend responsibilities.
- `apps/frontend`: implemented web work surface for enterprise search, agent interaction, source review, workflow execution, and admin views.
- `apps/windows`: planned Windows desktop client for desktop workflows and enterprise distribution.
- `apps/mac`: planned macOS desktop client for executive workflows, desktop search, and notifications.
- `packages/api-types`: implemented shared API schemas and contract types.
- `packages/design-system`: implemented shared design tokens and UI primitives for web.
- `packages/shared-config`: planned shared lint, formatting, TypeScript, Python, and CI config where appropriate.

## System Direction

The product should feel like a trusted operating layer for enterprise work, not a simple keyword search box. The user should not need to know which system owns the data or which tool must be called. The platform should route requests through the right backend, respect permissions, stream progress, and return grounded, traceable answers.

## Service Boundaries

- Apps call `backend-facade`, not internal services directly.
- `backend-facade` owns product-facing APIs, request aggregation, response shaping, and app-compatible streaming surfaces.
- `backend` currently owns MCP registration, OAuth/token state, user skills, and audit events. It is the target home for tenants, auth integration, permissions, product persistence, admin workflows, and jobs.
- `ai-backend` owns agent orchestration, tools, skills, MCP, memory, subagents, streaming events, and retrieval orchestration.
- Shared packages hold stable contracts and generated clients. They should not contain hidden business logic that makes ownership ambiguous.

## Docker And CI/CD Direction

Each deployable component should have its own Docker image:

- `ghcr.io/<org>/enterprise-search-backend-facade`
- `ghcr.io/<org>/enterprise-search-backend`
- `ghcr.io/<org>/agent-runtime-backend`
- `ghcr.io/<org>/enterprise-search-frontend`

Each deployable component also owns its local dependency environment:

- `services/backend`: service-local Python 3.13 `.venv`, `requirements.txt`, `pyproject.toml`, and `Dockerfile`; its Docker build uses the repo root as context for constants-only service contracts.
- `services/backend-facade`: service-local Python 3.13 `.venv`, `requirements.txt`, `pyproject.toml`, and `Dockerfile`; its Docker build uses the repo root as context for constants-only service contracts.
- `services/ai-backend`: service-local Python 3.13 `.venv`, `requirements.txt`, `pyproject.toml`, and `Dockerfile`; its Docker build uses the repo root as context for constants-only service contracts.
- `apps/frontend`: npm workspace dependency environment with its own `package.json`, Vite config, and `Dockerfile`; it must not use a Python service venv.

Do not run or test one service with another service's `.venv`. Create the target service's `.venv` from its own `requirements.txt` before running that component locally.

Starting CI/CD model:

- CI on every PR: lint, typecheck, unit tests, builds, and Docker build validation for changed components.
- Path-filtered workflows so unrelated apps/services do not rebuild unnecessarily.
- CD after merge to `main`: build and push service images to GitHub Container Registry.
- Staging deploy from `main`.
- Production deploy through GitHub Environments with manual approval.
- Desktop apps use platform-specific pipelines later: macOS runners for Mac builds, and Windows runners for Windows builds.

## Current Status

The workspace now includes initial scaffolding for `apps/frontend`, `services/backend-facade`, `services/backend`, `services/ai-backend`, `packages/api-types`, and `packages/design-system`.

## Development Setup

Use the root `Makefile` for the default workflow:

```bash
cd enterprise-search
make setup
```

`make setup` installs npm dependencies and creates one virtual environment per
Python service. Do not reuse a sibling service `.venv`.

Equivalent manual setup:

```bash
cd enterprise-search
npm install

cd services/backend
python3.13 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

cd ../backend-facade
python3.13 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

cd ../ai-backend
python3.13 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
cp env_example .env
```

Set at least one model provider key in `services/ai-backend/.env` or in your
shell before sending chat messages:

```bash
OPENAI_API_KEY=...
# or ANTHROPIC_API_KEY=...
# or GOOGLE_API_KEY=...
```

The local Python commands include `packages/service-contracts/src` on
`PYTHONPATH` because the services share constants from that package. Docker
images install the package during build.

## Run Locally

Run the local end-to-end stack with one command:

```bash
cd enterprise-search
make dev
```

This starts:

- `services/backend` on `http://127.0.0.1:8100`
- `services/ai-backend` on `http://127.0.0.1:8000`
- `services/backend-facade` on `http://127.0.0.1:8200`
- `apps/frontend` on `http://127.0.0.1:5173`

Open `http://127.0.0.1:5173`. The Vite dev server proxies `/v1/*` to
`backend-facade`.

To bind to a different interface, use `BIND_HOST`, for example
`BIND_HOST=0.0.0.0 make dev`. The Makefile intentionally avoids the generic
`HOST` variable because some shells set it to non-network values.

Manual process commands, if you want separate terminals:

```bash
cd services/backend
BACKEND_ENVIRONMENT=development \
MCP_TOKEN_VAULT_PROVIDER=local \
PYTHONPATH=src:../../packages/service-contracts/src \
.venv/bin/python -m uvicorn backend_app.app:app --host 127.0.0.1 --port 8100
```

```bash
cd services/ai-backend
RUNTIME_ENVIRONMENT=development \
RUNTIME_STORE_BACKEND=in_memory \
RUNTIME_START_IN_PROCESS_WORKER=true \
MCP_BACKEND_REGISTRY_URL=http://127.0.0.1:8100 \
SKILLS_BACKEND_REGISTRY_URL=http://127.0.0.1:8100 \
PYTHONPATH=src:../../packages/service-contracts/src \
.venv/bin/python -m uvicorn runtime_api.app:app --host 127.0.0.1 --port 8000
```

```bash
cd services/backend-facade
FACADE_ENVIRONMENT=development \
DEV_AUTH_BYPASS=true \
FACADE_DEV_ORG_ID=org_123 \
FACADE_DEV_USER_ID=user_123 \
BACKEND_URL=http://127.0.0.1:8100 \
AI_BACKEND_URL=http://127.0.0.1:8000 \
PYTHONPATH=src:../../packages/service-contracts/src \
.venv/bin/python -m uvicorn backend_facade.app:app --host 127.0.0.1 --port 8200
```

```bash
cd enterprise-search
npm run dev --workspace @enterprise-search/frontend -- --host 127.0.0.1
```

## Auth In Development

Local browser auth bypass is explicit. It only works when both conditions are
true:

- `FACADE_ENVIRONMENT=development`
- `DEV_AUTH_BYPASS=true`

In that mode, the facade uses `FACADE_DEV_ORG_ID` and `FACADE_DEV_USER_ID` as the
local identity. Do not hardcode JWTs or service tokens in source, Dockerfiles,
README examples, or committed `.env` files.

Leave `ENTERPRISE_SERVICE_TOKEN` unset for the default local stack. That keeps
service-to-service calls in local query/body-scope mode. To test service-auth
behavior, set `ENTERPRISE_SERVICE_TOKEN` in all relevant service environments;
internal callers must also send `x-enterprise-org-id` and
`x-enterprise-user-id`.

Production and staging must not use `DEV_AUTH_BYPASS`. In production, missing
`ENTERPRISE_AUTH_SECRET` or `ENTERPRISE_SERVICE_TOKEN` fails closed.

## Smoke Test

After the four local processes are running, this should produce an assistant
answer:

```bash
python3 - <<'PY'
import json
import urllib.request

base = "http://127.0.0.1:8200"

def request(method, path, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))

conversation = request("POST", "/v1/agent/conversations", {"title": "Local smoke"})
run = request(
    "POST",
    "/v1/agent/runs",
    {"conversation_id": conversation["conversation_id"], "user_input": "Hi"},
)
print(run["stream_url"])
PY
```

You can also send `Hi` from the UI composer.

## Streaming Runtime Events

The local chat UI opens `/v1/agent/runs/{run_id}/stream` through
`backend-facade`. The AI backend persists ordered `RuntimeEventEnvelope` records
before replaying or streaming them as `runtime_event` SSE frames. Browser clients
track the highest `sequence_no` per run and reconnect with `after_sequence` so a
paused stream can resume without replaying already-rendered events.

## Docker Development

Build and run the full local stack through Docker:

```bash
cd enterprise-search
OPENAI_API_KEY=$OPENAI_API_KEY make docker-dev
```

Open `http://127.0.0.1:8080`. The `dev-gateway` container serves the frontend and
proxies `/v1/*` to `backend-facade`, matching the route shape the browser uses.

Useful Docker checks:

```bash
curl http://127.0.0.1:8080/v1/session
curl http://127.0.0.1:8200/v1/mcp/servers
docker compose -f docker-compose.dev.yml logs -f ai-backend
```

Stop the stack:

```bash
make docker-dev-down
```

The older `services/ai-backend/docker-compose.yml` is scoped to production-style
AI API and worker execution with Postgres. Use `docker-compose.dev.yml` when you
want frontend, facade, backend, and AI backend together.

## Production Build

`make prod` builds production artifacts and refuses to run with dev auth enabled
or missing required secrets:

```bash
cd enterprise-search
ENTERPRISE_AUTH_SECRET=... \
ENTERPRISE_SERVICE_TOKEN=... \
MCP_TOKEN_VAULT_SECRET=... \
OPENAI_API_KEY=... \
make prod
```

`make prod` does not enable `DEV_AUTH_BYPASS` and does not hardcode a JWT or
service token. Deploy the built images with your production orchestrator and
managed secret store. The backend production runtime still requires a persistent
MCP registry store and managed token-vault adapter before it can serve production
traffic.

Start there for architecture details:

- `apps/README.md`
- `packages/README.md`
- `services/ai-backend/README.md`
- `services/ai-backend/docs/README.md`
- `docs/architecture/workspace-topology.md`
- `docs/architecture/service-boundaries.md`

## Repo Rules

- Keep service boundaries clear. Do not put frontend, facade, core backend, or native app concerns into `services/ai-backend`.
- Prefer stable APIs and generated clients between components over direct cross-service imports.
- Do not import implementation code across `apps/*` or `services/*`. Cross-component integration must use HTTP APIs, queues/events, constants-only service contracts, or generated contracts from `packages/api-types`.
- Do not add a sibling service directory to `PYTHONPATH` or use relative imports to reach another deployable component.
- Each deployable component owns its dependency environment and Dockerfile:
  - Python services use a service-local `.venv`, `requirements.txt`, and `Dockerfile`.
  - The web frontend uses its own npm workspace environment, `package.json`/`package-lock.json`, and `Dockerfile`.
- Document responsibilities before implementation when introducing a new component.
- Treat permissions, auth context, and tenant boundaries as cross-cutting product requirements.
- Every implementation should include focused unit tests and edge-case coverage appropriate to its component.
- Do not create shared packages just to avoid a small amount of duplication; share only stable contracts and truly cross-cutting primitives.
