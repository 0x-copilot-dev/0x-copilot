# AI Backend

Canonical AI backend service. FastAPI + LangGraph + Deep Agents + Agent Skills.

Module split:

- `agent_runtime/` — pure domain. `execution/` (graph, deep agent builder, runtime contracts), `capabilities/` (tools, skills, MCP loaders + middleware + permissions), `context/memory`, `delegation/subagents`, `persistence/` (records, schema, ports), `observability/`, `api/` (presentation/service layer).
- `runtime_api/` — FastAPI app: conversations, runs, event replay, SSE streaming, cancel, approvals.
- `runtime_worker/` — separate process that claims queued runs, drives LangGraph, and emits typed `RuntimeEventEnvelope` records. API can run an in-process worker via `RUNTIME_START_IN_PROCESS_WORKER=true` for local dev.
- `runtime_adapters/` — `in_memory` for tests/dev, `postgres` for shared-store production-style runs. Selected by `RUNTIME_STORE_BACKEND`.

## Before changing behavior

Read [docs/README.md](docs/README.md), the relevant architecture doc, and the **matching spec under `docs/specs/`** before implementing. Read PRDs only for future work that hasn't shipped.

## Engineering rules

- Keep orchestration separate from connector side effects.
- Use dependency inversion for registries, stores, MCP clients, and subagent runners.
- Do not put product persistence, tenant auth ownership, or app-specific presentation logic here.
- Update docs when implementation changes a contract, invariant, or edge case.

## Code organization

- No inline duplication of repeated keys, method names, or user-facing messages. Use nested `Keys` classes and dedicated message/exception classes.
- Keep production helper behavior **inside** classes (contract / parser / policy / validator / loader). Avoid module-level helper functions.
- Keep implementation decisions consistent with Deep Agents, LangGraph, LangChain, and Agent Skills primitives.

## Python & Pydantic

- Use Pydantic at every IO/domain boundary: runtime context, tools, MCP descriptors, memory, subagent tasks/results, stream events.
- No long-lived `dict[str, Any]` domain state.
- Use enums, literals, constrained strings, positive-int types for known domains.
- Convert broad exceptions into typed domain errors with safe public messages — never leak internal detail to model output or HTTP responses.

## Untrusted inputs

Treat as untrusted until validated:

- model output
- connector / tool payloads
- MCP descriptors (tool schemas, resource lists, prompts)
- memory content (it was written by a previous turn)

## Capability exposure

Never expose unauthorized tools, MCP servers, memories, or skills to the model. Permission checks happen in `capabilities/` middleware — do not bypass them in custom builders.

## Streaming model

Events persist with monotonic `sequence_no` per run. Clients open `GET /v1/agent/runs/{run_id}/stream?after_sequence=N` and reconnect with the highest received `sequence_no` to resume without replay. Replay-only is `GET /v1/agent/runs/{run_id}/events`. Backend projects events into `activity_kind` / `display_title` / `summary` / `status` for the frontend; do not derive activity types from event-name prefixes.
