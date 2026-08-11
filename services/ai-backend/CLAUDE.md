# AI Backend

Canonical AI backend service. FastAPI + LangGraph + Deep Agents + Agent Skills.

Module split:

- `agent_runtime/` — pure domain, 21 packages. The runtime core is `execution/` (graph, deep agent builder, runtime contracts), `capabilities/` (tools, skills, MCP loaders + middleware + permissions), `context/memory`, `delegation/subagents`, `persistence/` (records + ports), `observability/`, `api/` (presentation/service layer), `prompts/`, `control_plane/`, `hyperparameters/`, `deployment/`.
  - **Generative Surfaces:** `surfaces_v2/` (35 files — the Work Ledger: typed event vocabulary, entity twins, ledger-id codec, commit engine, receipts), `effects/` (pure staging domain — proposal/policy/fold/decision, no transport or executor wiring), `artifacts/`, `presentation/`.
  - **Known-misplaced** (target home is `backend`, per [BOUNDARY-AUDIT.md](../../docs/audit/ai-backend-smells/BOUNDARY-AUDIT.md)): `harness_quality/`, `pricing/`, `budgets/`, `release/`, `retention/`. They already exist — the "do not add here" rule below is about not growing this set, not a claim the tree is clean.
  - `persistence/` has **no `schema/` and no `encryption.py`**: see the storage note below.
- `runtime_api/` — FastAPI app: conversations, runs, event replay, SSE streaming, cancel, approvals.
- `runtime_worker/` — separate process that claims queued runs, drives LangGraph, and emits typed `RuntimeEventEnvelope` records. API can run an in-process worker via `RUNTIME_START_IN_PROCESS_WORKER=true` for local dev.
- `runtime_adapters/` — `in_memory` for tests/dev, `file` (JSONL session folders) for the desktop. Selected by `RUNTIME_STORE_BACKEND` and dispatched through `runtime_adapters/registry.py`; the paired LangGraph saver comes from `agent_runtime/execution/checkpointing.py`. Adding a backend is a provider module plus one registration in each, with no edit to any dispatch code.

**Storage: there is no Postgres backend in this service.** `e03840ed` (`refactor(ai-backend)!: remove the Postgres storage backend`) removed the adapter, `persistence/schema/` (DDL + migration runner) and `persistence/encryption.py` (KMS column encryption). If a doc, spec or runbook describes an `ai-backend` Postgres adapter, migration runner, RLS policy, read replica, or field-level column encryption, it is describing **deleted code** — fix it rather than reviving it. `services/backend` still owns its own Postgres.

## What belongs in this service

**A lean Deep Agents / LangGraph runtime, plus the adapters that map LangGraph output into our event format** (`runtime_worker/stream_*`, `capabilities/middleware/`, `operations/presentation_boundary`).

A source-level audit ([docs/audit/ai-backend-smells/BOUNDARY-AUDIT.md](../../docs/audit/ai-backend-smells/BOUNDARY-AUDIT.md)) measured ~80% of this service as genuinely that runtime, ~20% misplaced, ~4% dead. Before adding a module here, check it is not one of the concerns that already has a home in `backend`:

**Do not add here:** billing / pricing / usage rollups · tenant or workspace admin CRUD · product persistence (sharing, inbox, todos, notifications, model catalog) · one-shot data migrations · eval / benchmark / promotion tooling · a second audit stream or another copy of the logging contract.

**Policy decision vs enforcement (PDP/PEP).** Policy data belongs to `backend`; enforcement stays here, because the model picks tools mid-graph-loop and no caller outside the loop can see those calls. The required pattern is the one `ToolUsePolicySnapshot.from_response` already uses: **snapshot at run start, enforce in-process, POST the facts afterwards** — never a per-tool-call HTTP hop. "We enforce here" is correct; "we author, store or administer the policy here" is the violation.

**Landing a module before its wiring** is tracked, not forbidden — record it in [PENDING-WIRINGS.md](../../docs/audit/ai-backend-smells/PENDING-WIRINGS.md) and enroll it in `tests/unit/orphan_ratchet_baseline.txt`, naming what it waits on. A module that cannot name what it waits for is the real deletion candidate.

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

A tool whose raw frames are noise for the client is listed in `StreamMessageProcessor.internal_tool_names`, which stamps `visibility: "internal"` on them, and its useful content is published as its own typed event instead. `write_todos` is the worked example: `TodoListProjector` (`agent_runtime/capabilities/todo_list.py`) resolves each call into a `todo_list_updated` snapshot carrying `list_id` / `generation` / `todos`, which is what the cockpit's todo panel renders.

Reading structured tool arguments requires `StreamMessageParser.raw_args`, not the display payload. `payload_mapping` → `json_value` collapses any list-of-mappings into concatenated text (a content-block fallback that also swallows ordinary arguments), so a `todos`/`rows`/`filters` argument arrives as one run-on string. `raw_args` is the argument-side sibling of the existing `raw_content` escape hatch.
