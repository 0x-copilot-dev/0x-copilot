# AI Backend

Python backend workspace for the agent runtime layer.

This backend is the agent-runtime layer of 0xCopilot — the local-first, bring-your-own-key personal AI agent desktop app ("Put your day on autopilot."). It plans and executes multi-step runs across the user's files and connected tools, and pauses for approval before it acts. The service implements the runtime foundation for dynamic tools, skills, MCP loading, context and memory management, subagents, streaming, the narrow FastAPI runtime API, replayable runtime events, and persistence contracts/schema. Read the architecture docs, the relevant technical spec, testing guidance, and engineering rules before changing runtime behavior.

## Workspace Context

`services/ai-backend` is one component inside the larger `0x-copilot` workspace. It is not the whole product.

The workspace is a shipping monorepo of independently deployable components. This service's canonical path is `services/ai-backend`.

Sibling components, all present today:

- `services/backend-facade`: the product-facing API layer that the frontend and desktop app call.
- `services/backend`: core backend for persistence, auth integration, permissions, and jobs.
- `apps/frontend`: the Vite + React web work surface for agent interaction.
- `apps/desktop`: the single Electron client; it supervises an embedded PostgreSQL plus the three Python services from a bundled runtime.
- `apps/website`: the `0xcopilot.tech` marketing site.

This service owns AI orchestration concerns only: Deep Agents runtime, LangGraph execution, LangChain tool wiring, dynamic tool and MCP loading, skills, context/memory management, subagents, streaming, and typed agent contracts. Product API boundaries should flow through `backend-facade`; durable product state and non-agent backend concerns should live in `backend`.

Read the workspace architecture before changing runtime APIs:

- `../../docs/architecture/workspace-topology.md`
- `../../docs/architecture/service-boundaries.md`
- `../../docs/decisions/0001-monorepo-with-deployable-services.md`

## What This Service Does

0xCopilot is a local-first, bring-your-own-key personal AI agent that runs entirely on the user's machine: an embedded PostgreSQL, three Python services, and an Electron shell. The user brings their own OpenAI, Anthropic, or Google key; the agent works across the user's files and connected apps and pauses for approval before it acts.

`ai-backend` is the AI orchestration layer of that product — FastAPI + LangGraph + Deep Agents. It is the piece that plans and executes multi-step runs. Concretely it:

- Plans and drives multi-step runs across the user's files, MCP servers, and connected tools.
- Pauses for approval before the agent takes a consequential action.
- Respects user, connector, document, and action permissions before any capability is visible to the model.
- Dynamically loads tools, skills, MCP servers, memories, and subagents so the agent has the right capability without bloating every prompt.
- Manages long-running work through context compression, summarization, memory, streaming updates, and subagent delegation.

It is not an enterprise-search product and not a multi-tenant SaaS. Tenant auth, billing, and durable product state live in `backend`; the product-facing API is `backend-facade`.

## Stack

- Python
- FastAPI for the narrow runtime HTTP API (apps reach it through `backend-facade`, never directly)
- PostgreSQL-compatible runtime persistence schema, with deterministic in-memory ports for unit tests and local development
- LangChain for LLM integrations, tools, retrievers, and agent building blocks
- LangGraph for stateful agent workflows and graph orchestration
- Deep agents for longer-running research, planning, and multi-step agent behavior
- Pydantic for typed contracts and validation at IO boundaries
- An internal embeddings endpoint powering Library indexing and retrieval, so runs can be grounded in the user's own files and content

## Documentation Workflow

Start here:

- `docs/README.md` for the documentation index
- `docs/architecture/` for current architecture, runtime contracts, package structure, and data flows
- `docs/guides/` and `docs/reference/` for implementation guides and reference material

Each feature should include focused unit tests, edge-case coverage, and Pydantic contracts where data crosses runtime, tool, MCP, memory, subagent, or streaming boundaries.

## Engineering Rules

Engineering rules for this backend live in `CLAUDE.md` (this service) and, mirrored for Cursor, in `../../.cursor/rules/` (files scoped to `services/ai-backend`).

Core rules:

- Architecture first: read `docs/README.md`, the relevant architecture docs, matching technical spec, testing guidance, and rule docs before implementation.
- Pydantic first: validate runtime context, tool specs, MCP descriptors, memory scopes, subagent tasks/results, and stream events with typed contracts.
- Tests required: every feature needs focused unit tests, malformed-input tests, permission-denial tests, external-failure tests, and edge-case coverage.
- Architecture boundaries matter: keep orchestration separate from connector side effects; depend on protocols and ports, not vendor SDKs.
- Least privilege: never expose unauthorized tools, MCP servers, memories, documents, or actions to the model.
- Context discipline: do not pass full conversation history to subagents by default; use compact task summaries and return response plus execution/plan summaries.
- Safe observability: stream useful progress and trace IDs, but redact secrets and oversized payloads before emission.
- Centralized constants: avoid inline repeated keys, method names, and user-facing messages; use nested `Keys` classes and dedicated message or exception classes.
- Class-scoped helpers: keep production helper behavior inside contract, parser, policy, validator, or loader classes instead of module-level helper functions.
- Test mixins: put fake providers, builders, setup helpers, and repeated constants in mixins; concrete test classes should contain only `test_*` unit test methods.

## Local Setup

The virtual environment lives inside this service folder at `services/ai-backend/.venv`.
When your shell is already in `services/ai-backend`, use:

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
```

Install project dependencies into that local environment:

```bash
python -m pip install -r requirements.txt
```

Configure provider keys and runtime defaults from the service-local env template:

```bash
cp env_example .env
```

Set at least one provider key in `.env` before submitting simple run requests:

```bash
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
RUNTIME_DEFAULT_PROVIDER=openai
RUNTIME_DEFAULT_MODEL=gpt-5.4-mini
RUNTIME_DEFAULT_TEMPERATURE=0
RUNTIME_DEFAULT_TIMEOUT_SECONDS=180
RUNTIME_MAX_RETRIES=2
RUNTIME_MAX_PARALLEL_RUNS=4
RUNTIME_MAX_PARALLEL_SUBAGENTS=4
RUNTIME_STORE_BACKEND=in_memory
DATABASE_URL=
RUNTIME_WORKER_POLL_INTERVAL_SECONDS=1
RUNTIME_WORKER_LOCK_SECONDS=60
RUNTIME_START_IN_PROCESS_WORKER=true
# P4 Stage 2 — worker-side MODEL_DELTA coalesce window (ms). 0 disabled.
# Recommended 50 once measured on staging.
RUNTIME_DELTA_COALESCE_WINDOW_MS=0
RUNTIME_DELTA_COALESCE_MAX_CHUNKS=64
# P2 — SSE bus. Default is ``auto`` (resolves to ``postgres`` when
# DATABASE_URL is configured, else ``in_memory``). Dev pins ``in_memory``
# here for single-process runs; ``postgres`` enables LISTEN/NOTIFY for
# cross-process SSE wakeups (drops p50 SSE delivery from ~1s to ~50ms in
# multi-process deploys; needs DATABASE_URL configured).
RUNTIME_EVENT_BUS_BACKEND=in_memory
```

Run requests should not include API keys. Provider credentials are loaded by
`RuntimeSettings.load()` from `env_example`, `.env`, and process environment.

Example run request body:

```json
{
  "conversation_id": "conversation_123",
  "org_id": "org_123",
  "user_id": "user_123",
  "user_input": "Find launch risks.",
  "model": {
    "provider": "openai",
    "model_name": "gpt-5.4-mini"
  },
  "request_context": {
    "roles": ["employee"],
    "permission_scopes": ["docs:read"],
    "connector_scopes": {
      "google-drive": ["docs:read"]
    },
    "trace_metadata": {
      "surface": "local-dev"
    }
  }
}
```

Run tests with the same service-local environment:

```bash
.venv/bin/python -m pytest
```

## Running The API

For local in-memory debugging, run the FastAPI app and worker in one process:

```bash
RUNTIME_STORE_BACKEND=in_memory \
RUNTIME_START_IN_PROCESS_WORKER=true \
PYTHONPATH=src .venv/bin/python -m uvicorn runtime_api.app:app --host 127.0.0.1 --port 8000
```

This mode does not require Docker or Postgres. Submitted runs are claimed by the
in-process worker and the SSE stream stays open until the run reaches a terminal
state. Model provider chunks are emitted as `model_delta` events, followed by
`final_response` and `run_completed`. Use this mode when debugging locally.

Stream a run from:

```text
GET /v1/agent/runs/{run_id}/stream?after_sequence=0&org_id=org_123&user_id=user_123
```

The event stream is Server-Sent Events. Provider chunks from OpenAI, Anthropic,
Gemini, or any LangChain-compatible streaming model appear in `payload.delta`:

```text
event: runtime_event
id: 3
data: {"event_type":"model_delta","source":"model","payload":{"delta":" Hello","message":" Hello"}}
```

For API-only development without executing queued runs in-process, disable the
worker:

```bash
RUNTIME_START_IN_PROCESS_WORKER=false \
PYTHONPATH=src .venv/bin/python -m uvicorn runtime_api.app:app --reload --host 127.0.0.1 --port 8000
```

For production-style serving, use Gunicorn to supervise multiple Uvicorn worker
processes:

```bash
PYTHONPATH=src gunicorn runtime_api.app:app \
  -k uvicorn.workers.UvicornWorker \
  --workers ${WEB_CONCURRENCY:-4} \
  --bind 0.0.0.0:${PORT:-8000}
```

Gunicorn worker count controls HTTP process parallelism. Runtime execution
parallelism is configured separately with `RUNTIME_MAX_PARALLEL_RUNS` for queued
AI work.

## Production-Style Local Execution

For separate API and worker processes, use Postgres as the shared runtime store:

```bash
cp env_example .env
# set OPENAI_API_KEY in .env
docker compose up --build
```

The API container starts with Gunicorn and Uvicorn workers. The worker container
runs:

```bash
python -m runtime_worker
```

Both processes use:

```bash
RUNTIME_STORE_BACKEND=postgres
DATABASE_URL=postgresql://ai_backend:ai_backend@postgres:5432/ai_backend
```

The schema is bootstrapped from the application on startup. After submitting a
run, stream live and replayed events from:

```text
GET /v1/agent/runs/{run_id}/stream?after_sequence=0&org_id=org_123&user_id=user_123
```

For non-streaming replay, use:

```text
GET /v1/agent/runs/{run_id}/events?after_sequence=0&org_id=org_123&user_id=user_123
```

When the worker runs a streaming-capable model, the stream includes `model_delta`
events as provider chunks arrive, then `final_response` and `run_completed`.

## Intended Direction

This backend hosts the AI orchestration layer for 0xCopilot's local-first desktop agent: the runtime that reaches the user's files and connected apps through MCP servers and tool adapters, always behind an approval gate. It currently provides the typed harness and fake-driven tests needed to add those adapters deliberately.
