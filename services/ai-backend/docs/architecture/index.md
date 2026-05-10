# ai-backend — Architecture audit

A snapshot of how the ai-backend service is organized today (May 2026), produced by clustering every live module by responsibility, drawing both top-level and within-cluster PlantUML diagrams, and tracing the four most common request flows end-to-end.

The intended audience is anyone who needs to locate or change behavior in the service: a new contributor reading code for the first time, a reviewer trying to remember where a port lives, or anyone debugging a streaming run.

## Reading order

1. **[01-system-overview.puml](01-system-overview.puml)** — start here. Eight clusters, their inbound/outbound edges, and the external systems they depend on.
2. The cluster diagram you care about (table below).
3. The matching flow diagram if you're tracing runtime behavior.

All `.puml` files are standard PlantUML — render with the VS Code PlantUML extension, IntelliJ's PlantUML plugin, or `plantuml -tsvg <file>`.

### See also (existing prose docs)

These earlier narrative docs in this folder predate the diagram audit and stay authoritative for their respective topics — read them alongside the diagrams:

- [system-overview.md](system-overview.md) — high-level prose system overview.
- [package-structure.md](package-structure.md) — installable `src` layout and package boundaries.
- [runtime-contracts.md](runtime-contracts.md) — Pydantic contract policy at every IO boundary.
- [data-flow.md](data-flow.md) — request lifecycle in narrative form.

## Cluster summary

| #       | Cluster            | Path(s)                                                                                                                                                                                                                                                    | Responsibility                                                                                                                                                                          | Diagram                                                                                      |
| ------- | ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| **C1**  | API Edge           | [runtime_api/](../../src/runtime_api/)                                                                                                                                                                                                                     | FastAPI app, identity/RBAC/auth, HTTP routes, SSE streaming, Pydantic schemas                                                                                                           | [02-runtime-api.puml](02-runtime-api.puml)                                                   |
| **C2**  | Worker             | [runtime_worker/](../../src/runtime_worker/)                                                                                                                                                                                                               | Queue-claim loop, run/cancel/approval handlers, streaming executor, background jobs                                                                                                     | [03-runtime-worker.puml](03-runtime-worker.puml)                                             |
| **C3**  | Adapters           | [runtime_adapters/](../../src/runtime_adapters/)                                                                                                                                                                                                           | In-memory + Postgres implementations of every port; `RuntimeAdapterFactory` selection on `RUNTIME_STORE_BACKEND`                                                                        | [07-adapters.puml](07-adapters.puml)                                                         |
| **C4**  | Runtime Services   | [agent_runtime/api/](../../src/agent_runtime/api/)                                                                                                                                                                                                         | `RuntimeApiService` coordinator (2.4k LOC), `RuntimeEventProducer`, `PresentationGenerator`, domain services (Draft/Share/Fork/Workspace/Usage/MCP-discovery), resolvers, notifications | [05-runtime-services.puml](05-runtime-services.puml)                                         |
| **C5**  | Agent Runtime Core | [agent_runtime/execution/](../../src/agent_runtime/execution/) + [prompts/](../../src/agent_runtime/prompts/)                                                                                                                                              | `create_agent_runtime` factory, Deep Agents + LangGraph builder, provider stream adapters, citation pipeline, system-prompt assembly                                                    | [08-execution-prompts.puml](08-execution-prompts.puml)                                       |
| **C6**  | Capabilities       | [agent_runtime/capabilities/](../../src/agent_runtime/capabilities/)                                                                                                                                                                                       | Tools registry+loader+builtins, MCP registry+middleware+permissions, Skills, citations, draft backend, budget guards                                                                    | [04-capabilities.puml](04-capabilities.puml)                                                 |
| **C7**  | Delegation         | [agent_runtime/delegation/subagents/](../../src/agent_runtime/delegation/subagents/)                                                                                                                                                                       | `DynamicSubagentCatalog`, contracts, handoff tool, runner                                                                                                                               | [09-delegation.puml](09-delegation.puml)                                                     |
| **C8a** | State + Memory     | [agent_runtime/persistence/](../../src/agent_runtime/persistence/), [context/memory/](../../src/agent_runtime/context/memory/)                                                                                                                             | Persistence ports + record types + schema; memory scopes/policies/route plans                                                                                                           | [06-persistence.puml](06-persistence.puml), [10-context-memory.puml](10-context-memory.puml) |
| **C8b** | Cross-cutting      | [observability/](../../src/agent_runtime/observability/), [budgets/](../../src/agent_runtime/budgets/), [pricing/](../../src/agent_runtime/pricing/), [retention/](../../src/agent_runtime/retention/), [deployment/](../../src/agent_runtime/deployment/) | Tracing/redaction/logging, budget charging, cost calculation, retention policy resolution, deployment profile + feature toggles                                                         | [11-cross-cutting.puml](11-cross-cutting.puml)                                               |

## External boundaries

ai-backend does not talk to apps directly. The trust model is:

- **Inbound** — only `backend-facade` calls `runtime_api/`. Identity is asserted through `ENTERPRISE_SERVICE_TOKEN` plus `x-enterprise-org-id` / `x-enterprise-user-id` headers, validated by [`RuntimeServiceAuthenticator`](../../src/runtime_api/auth.py).
- **Sibling service** — `backend` is called over HTTP for: workspace membership lookups, MCP catalog suggestions, MCP RPC proxy + OAuth token state. ai-backend never imports `backend`'s Python.
- **LLM providers** — Anthropic, OpenAI, and Google, accessed through provider-specific stream adapters in [execution/providers/](../../src/agent_runtime/execution/providers/).
- **MCP servers** — third-party connectors. Calls are proxied through `backend`'s RPC, which holds the OAuth tokens.
- **Postgres** — accessed only by adapters in [runtime_adapters/postgres/](../../src/runtime_adapters/postgres/).

## Key contracts

### `RuntimeEventEnvelope`

Defined in [runtime_api/schemas/events.py:983](../../src/runtime_api/schemas/events.py). Every event the worker writes and every event the SSE adapter delivers is one of these. Inherits the shared base (`_RuntimeEventBase`, line 879) — fields:

| Field                                                                                      | Type                         | Notes                                                                                                                              |
| ------------------------------------------------------------------------------------------ | ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `event_protocol_version`                                                                   | `PositiveInt` (1)            | Pinned by `Values.EVENT_PROTOCOL_VERSION`.                                                                                         |
| `event_id`                                                                                 | `str` (uuid hex)             | Stable per event; used as `parent_event_id` by children.                                                                           |
| `sequence_no`                                                                              | `PositiveInt`                | **Monotonic per `run_id`.** SSE clients resume with `?after_sequence=N`.                                                           |
| `created_at`                                                                               | `datetime` (UTC)             | Adapter-assigned.                                                                                                                  |
| `run_id`, `conversation_id`, `trace_id`                                                    | `str`                        | Required, normalized via `ValueNormalizer.normalize_id`.                                                                           |
| `source`                                                                                   | `StreamEventSource`          | One of `main_agent`, `subagent`, `tool`, `mcp`, `summarization`, `system`, `runtime`, `model`.                                     |
| `event_type`                                                                               | `RuntimeApiEventType`        | ~45 values; see groups below.                                                                                                      |
| `parent_event_id`, `span_id`, `parent_span_id`, `parent_task_id`, `task_id`, `subagent_id` | `str?`                       | Optional ids for trace tree / subagent linkage.                                                                                    |
| `display_title`, `summary`, `status`                                                       | `str?`                       | Filled by `RuntimeEventPresentationProjector.presentation_fields`.                                                                 |
| `activity_kind`                                                                            | `RuntimeActivityKind?`       | Timeline bucket: `run`, `message`, `tool`, `subagent`, `reasoning`, `approval`, `event`, `draft`, `note`, `heartbeat`, `mcp_auth`. |
| `visibility`                                                                               | `RuntimeEventVisibility`     | Defaults to `USER`; can be `INTERNAL` or `AUDIT`.                                                                                  |
| `redaction_state`                                                                          | `RuntimeEventRedactionState` | Defaults to `REDACTED`.                                                                                                            |
| `presentation`                                                                             | `RuntimeEventPresentation?`  | LLM-polished card metadata (title/status_label/kind/summary/result_preview).                                                       |
| `payload`, `metadata`                                                                      | `JsonObject`                 | Validated through `ObservabilityRedactor.redact_json_object`.                                                                      |

### `RuntimeApiEventType` — major event groups

- **Run lifecycle** — `RUN_QUEUED`, `RUN_STARTED`, `RUN_CANCELLING`, `RUN_CANCELLED`, `RUN_COMPLETED`, `RUN_FAILED`, `RUN_REJECTED`
- **Model** — `MODEL_DELTA`, `FINAL_RESPONSE`, `MODEL_CALL_STARTED`, `MODEL_CALL_COMPLETED`, `REASONING_SUMMARY`, `REASONING_SUMMARY_DELTA`
- **Tool** — `TOOL_CALL`, `TOOL_CALL_STARTED`, `TOOL_CALL_DELTA`, `TOOL_RESULT`, `TOOL_CALL_COMPLETED`
- **Subagent** — `SUBAGENT_UPDATE`, `SUBAGENT_STARTED`, `SUBAGENT_PROGRESS`, `SUBAGENT_COMPLETED`, `SUBAGENT_PAUSED`, `SUBAGENT_RESUMED`, `SUBAGENT_FLEET_STARTED`, `SUBAGENT_FLEET_FINISHED`
- **Approval** — `APPROVAL_REQUESTED`, `APPROVAL_RESOLVED`, `APPROVAL_FORWARDED`, `APPROVAL_UNDO_REQUESTED`
- **Citation / Draft** — `SOURCE_INGESTED`, `CITATION_MADE`, `DRAFT_UPDATED`
- **MCP** — `MCP_AUTH_REQUIRED`
- **Metadata / system** — `PRESENTATION_UPDATED`, `COMPRESSION_NOTE`, `HEARTBEAT`, `OBSERVATION`, `ERROR`, `BUDGET_WARNING`

`StreamingExecutor.action_interrupt_events = { APPROVAL_REQUESTED, MCP_AUTH_REQUIRED }` — these short-circuit the streaming loop into a paused state.

### Port catalog

The base trio lives in [agent_runtime/api/ports.py](../../src/agent_runtime/api/ports.py) (sync) + [async_ports.py](../../src/agent_runtime/api/async_ports.py):

- `PersistencePort` — conversations, messages, runs, approvals, usage, budget rows, retention policies.
- `EventStorePort` — `append_event`, `list_events_after`, `get_latest_sequence`, `set_run_latest_sequence`.
- `RuntimeQueuePort` — `claim_next`, `mark_complete`, `mark_retry`, `mark_dead_letter`.

The higher-level surfaces live in [persistence/ports.py](../../src/agent_runtime/persistence/ports.py): `MemoryMetadataPort`, `PayloadStoragePort`, `CheckpointStorePort`, `DraftStorePort`, `SubagentStorePort`, `SourceStorePort`, `CitationStorePort`, `ConversationToolOrdinalStorePort`, `ShareStorePort`. Each is a `typing.Protocol`; both `runtime_adapters/in_memory/*` and `runtime_adapters/postgres/*` satisfy them.

### Adapter selection

| `RUNTIME_STORE_BACKEND` | Factory method                              | Backing                                                       |
| ----------------------- | ------------------------------------------- | ------------------------------------------------------------- |
| `in_memory`             | `RuntimeAdapterFactory.from_settings`       | `InMemoryRuntimeApiStore` (sync, single-process)              |
| `in_memory_async`       | `RuntimeAdapterFactory.async_from_settings` | `AsyncInMemoryRuntimeApiStore` (thin wrapper, no real awaits) |
| `postgres`              | `RuntimeAdapterFactory.async_from_settings` | `PostgresRuntimeApiStore` (async only; needs `DATABASE_URL`)  |

## Per-cluster pointers

### C1 — API Edge

[02-runtime-api.puml](02-runtime-api.puml) · code at [runtime_api/](../../src/runtime_api/)

- The FastAPI app is composed by [`RuntimeApiAppFactory`](../../src/runtime_api/app.py); the lifespan opens the async store, optionally starts an in-process worker (`RUNTIME_START_IN_PROCESS_WORKER=true`), and tears them down in reverse on shutdown.
- Routers are split per feature: the main one is [http/routes.py](../../src/runtime_api/http/routes.py); separate modules handle drafts, retention, share/share-fork/self-fork, workspace data + defaults, audit list, internal endpoints. Health lives in [routes/health.py](../../src/runtime_api/routes/health.py).
- SSE has two surfaces: per-run streaming via [sse/adapter.py](../../src/runtime_api/sse/adapter.py) and per-user inbox streaming via [sse/inbox_adapter.py](../../src/runtime_api/sse/inbox_adapter.py); each has a paired event bus for push notifications.
- All Pydantic contracts are in [schemas/](../../src/runtime_api/schemas/); `events.py` is the canonical place for `RuntimeEventEnvelope` and the projector.

### C2 — Worker

[03-runtime-worker.puml](03-runtime-worker.puml) · code at [runtime_worker/](../../src/runtime_worker/)

- Worker process entry: [`__main__.py`](../../src/runtime_worker/__main__.py).
- Claim loop: [loop.py](../../src/runtime_worker/loop.py). Dispatches by `command_type` to `RuntimeRunHandler`, `RuntimeCancelHandler`, or `RuntimeApprovalHandler`. Sync ports are bridged to async via `runtime_adapters.async_wrappers` so the loop is uniformly async inside.
- Streaming pipeline: [`StreamingExecutor`](../../src/runtime_worker/streaming_executor.py) drives the LangGraph stream, fans chunks through `StreamOrchestrator` ([stream_events.py](../../src/runtime_worker/stream_events.py)) plus per-channel handlers in `stream_messages.py` / `stream_parts.py` / `stream_subagents.py` / `stream_tools.py`. `ToolCallLedger`, `ToolObservations`, and `ApprovalRecognisers` track derived state.
- Background jobs: approval-expiry sweeper, retention sweeper, encrypt-existing-columns one-shot migration, plus the `UsageRollupLoop`.

### C3 — Adapters

[07-adapters.puml](07-adapters.puml) · code at [runtime_adapters/](../../src/runtime_adapters/)

- Selection lives in [factory.py](../../src/runtime_adapters/factory.py); the dataclasses `RuntimePorts` / `AsyncRuntimePorts` carry the composed ports.
- Postgres path uses one shared connection pool with role-tagged `application_name` (`api` / `worker`) so `pg_stat_activity` is greppable. `PostgresDraftStore` / `PostgresShareStore` / `PostgresConversationToolOrdinalStore` all reuse the parent pool + `FieldCodec`.
- The event-store hazard fixes (per-run sequence_no monotonicity, `SELECT … FOR UPDATE` on `agent_runs` before append, `UNIQUE(run_id, sequence_no)`) are documented inline in `PostgresRuntimeApiStore`'s docstring — read them before changing the append path.

### C4 — Runtime Services

[05-runtime-services.puml](05-runtime-services.puml) · code at [agent_runtime/api/](../../src/agent_runtime/api/)

- Coordinator: [`RuntimeApiService`](../../src/agent_runtime/api/service.py) — 2,464 LOC. The one place runs/conversations/approvals are mediated. Both the API and the worker depend on it (and on its async port pairs).
- Event production + presentation: [`RuntimeEventProducer`](../../src/agent_runtime/api/events.py) attaches a synchronous preliminary presentation, persists, notifies, then optionally spawns an async LLM polish task that emits a follow-up `PRESENTATION_UPDATED` event patching only body fields (title/status_label/kind are frozen by the lifecycle).
- Domain services are mostly thin wrappers over persistence ports plus `RuntimeEventProducer` for any user-visible side effects (drafts, shares, forks, workspace feeds, usage, MCP discovery suggestions).
- Resolvers (`MembershipResolver`, `UserPoliciesResolver`, `SuggestibleConnectorsResolver`) are pluggable; production wires the HTTP-backed variants when the trusted-backend lane is configured (`BACKEND_BASE_URL` + `ENTERPRISE_SERVICE_TOKEN`).

### C5 — Agent Runtime Core

[08-execution-prompts.puml](08-execution-prompts.puml) · code at [agent_runtime/execution/](../../src/agent_runtime/execution/) + [prompts/](../../src/agent_runtime/prompts/)

- The orchestrator is [factory.py](../../src/agent_runtime/execution/factory.py)'s `create_agent_runtime` — 602 LOC. It validates context, resolves authorized capabilities, assembles the system prompt, applies workspace + user policy model kwargs, and hands off to the Deep Agents builder.
- Provider streaming is encapsulated per-provider in [providers/](../../src/agent_runtime/execution/providers/); the `CitationStreamPipeline` taps the same stream and forwards extractions into the citation ledger.
- The system prompt is composed from `DEFAULT_INSTRUCTIONS` in [prompts/runtime.py](../../src/agent_runtime/prompts/runtime.py) plus dynamic cards (MCP servers, skills, suggested connectors).

### C6 — Capabilities

[04-capabilities.puml](04-capabilities.puml) · code at [agent_runtime/capabilities/](../../src/agent_runtime/capabilities/)

- Three subsystems share the same shape (registry → permission policy → loader → middleware): tools, MCP, skills.
- Permission gates run on every list (filters cards before the model sees them) and again on every call (defense in depth). Never bypass `CapabilityAuthGate` in custom builders.
- The citation system is fully owned here: `CitationLedger` is the only insert path, `ConversationOrdinalAllocator` is the only place ordinals are assigned, both backed by their respective ports.
- The draft filesystem backend in [backends/draft_backend.py](../../src/agent_runtime/capabilities/backends/draft_backend.py) routes Deep Agents `/drafts/` writes through the versioned `DraftStorePort`.

### C7 — Delegation

[09-delegation.puml](09-delegation.puml) · code at [agent_runtime/delegation/subagents/](../../src/agent_runtime/delegation/subagents/)

- `SubagentDefinition` carries its own `model_profile` and `fs_permissions` — subagents can run with different (often smaller) models than the supervisor. `factory.py` translates `fs_permissions` into Deep Agents' `FilesystemPermission` rules.
- Subagent lifecycle events (`SUBAGENT_STARTED`, `_PROGRESS`, `_COMPLETED`, plus the fleet-level pair) are emitted with `parent_task_id` linking back to the supervisor's tool call. `SubagentStorePort` is a read-only projection over those events.

### C8a — Persistence + Memory

[06-persistence.puml](06-persistence.puml) · [10-context-memory.puml](10-context-memory.puml)

- Every persistence port is a `typing.Protocol`; never `import` an adapter directly outside `runtime_adapters/`. Records are grouped by domain in [persistence/records/](../../src/agent_runtime/persistence/records/); shared enums + `PersistenceValueNormalizer` live in `common.py`.
- Memory has three scopes (USER / AGENT / ORGANIZATION), each tenant-isolated through `MemoryScope.for_*` factories. Default policy ([context/memory/policy.py](../../src/agent_runtime/context/memory/policy.py)) forbids any actor except `APPLICATION` from writing `/policies/*`.
- The compression strategy enum (`INLINE` / `OFFLOAD` / `SUMMARIZE` / `FALLBACK_SUMMARY`) is decided at runtime per turn; offloads land in `ContextPayloadRecord` and surface to the user via `COMPRESSION_NOTE` events.

### C8b — Cross-cutting

[11-cross-cutting.puml](11-cross-cutting.puml)

- Redaction is enforced on every event at field-validation time (`payload`, `metadata` validators call `ObservabilityRedactor.redact_json_object`). Sensitive-key + sensitive-value patterns live in [observability/constants.py](../../src/agent_runtime/observability/constants.py).
- Budgets: `BudgetCharger.charge_run` ([budgets/charger.py](../../src/agent_runtime/budgets/charger.py)) is CAS-based and idempotent — safe to retry without double-charging. Pricing is integer-only micro-USD with banker's rounding ([pricing/calculator.py](../../src/agent_runtime/pricing/calculator.py)).
- Retention specificity walk: `CONVERSATION > ASSISTANT > USER > ORG > default` ([retention/policy_resolver.py](../../src/agent_runtime/retention/policy_resolver.py)).
- The deployment profile resolver decides feature toggles at startup (`enforce_rls`, `require_kms_token_vault`, `require_field_level_encryption`, `siem_export_required`, `default_retention_days`, `dev_auth_bypass_allowed`, etc.) and refuses to start in production without the right secrets.

## Flow walk-throughs

| Flow                                       | Diagram                                            | Worth knowing                                                                                                                                                                                                                                                                        |
| ------------------------------------------ | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Core** — Single-turn (no tools)          | [f1-single-turn.puml](f1-single-turn.puml)         | Two HTTP calls: `POST /runs` (returns 202) and `GET /runs/{id}/stream`. SSE waits for the worker to claim and append.                                                                                                                                                                |
| **Core** — Multi-turn with built-in tool   | [f2-multi-turn-tool.puml](f2-multi-turn-tool.puml) | One run, multiple model turns. Each tool call emits a `TOOL_CALL` + `TOOL_RESULT` pair; the async polish task may patch presentation later via `PRESENTATION_UPDATED`. `BudgetCharger.charge_run` runs post-completion.                                                              |
| **Core** — SSE resume after disconnect     | [f3-sse-resume.puml](f3-sse-resume.puml)           | Client reconnects with `?after_sequence=N`. Adapter replays gap, then alternates `event_bus.wait(timeout=2s)` and replay. `follow=false` returns one synthetic heartbeat instead of tailing.                                                                                         |
| **Core** — Run cancellation                | [f4-cancel.puml](f4-cancel.puml)                   | Cancel is async — the cancel command is enqueued just like a run. The active run handler observes `status=CANCELLED` cooperatively on its next loop tick; an in-flight model chunk may still emit one `MODEL_DELTA` before it stops.                                                 |
| **Citations** — multi-MCP + subagent + web | [f5-citations.puml](f5-citations.puml)             | One conversation-scoped `CitationLedger` + `ConversationOrdinalAllocator` shared across MCP middleware, subagents, and provider grounding. `final_response.citations` is sealed by `CitationStorePort.list_for_run` so resume after a worker crash rebuilds the registry exactly.    |
| **Reasoning / "thinking"**                 | [f6-thinking.puml](f6-thinking.puml)               | `ModelReasoningConfig` is per-run; provider adapters emit `REASONING_SUMMARY_DELTA` → `REASONING_SUMMARY` before the visible answer. Anthropic `display=OMITTED` skips emitting reasoning events to the client; the tokens still bill via a separate column in `ModelPricingRecord`. |
| **MCP — adding a server** (3 paths)        | [f7-mcp-add.puml](f7-mcp-add.puml)                 | Registry mutation lives in `backend`, not ai-backend. Three paths (JSON, catalog, custom UI) converge on the same `ServerRecord`. ai-backend re-reads the registry on every `create_agent_runtime` — no cache, no worker restart needed.                                             |
| **MCP — calling unauthed + in-chat auth**  | [f8-mcp-auth.puml](f8-mcp-auth.puml)               | `MCP_AUTH_REQUIRED` is in `StreamingExecutor.action_interrupt_events` → run pauses on an approval row. The user's "Connect" click triggers OAuth in `backend`; an `APPROVAL_RESOLVED` command resumes the run via `RuntimeApprovalHandler`.                                          |
| **Usage / token metrics**                  | [f9-usage-metrics.puml](f9-usage-metrics.puml)     | Two surfaces: in-chat `/context` (single conversation, server-computed `headroom_pct`) and `/v1/usage/*` (per-user / per-org / per-connector rollups). Costs are stamped at write time by `CostCalculator`, so pricing changes never rewrite history.                                |

Each sequence diagram is faithful to the code as of May 2026; if you change a flow, update the matching `.puml`.

## Appendix A — Deprecated / empty modules

The following top-level directories under `agent_runtime/` exist but contain only `__pycache__` (no `.py` files). They are remnants from before the runtime was reorganized into `capabilities/` / `delegation/` / `context/memory/` / `execution/`. Do not add code to them; do not import from them.

| Empty directory            | Live equivalent                                                                             |
| -------------------------- | ------------------------------------------------------------------------------------------- |
| `agent_runtime/agent/`     | `agent_runtime/execution/` (graph + builder) and `agent_runtime/capabilities/` (middleware) |
| `agent_runtime/mcp/`       | `agent_runtime/capabilities/mcp/`                                                           |
| `agent_runtime/memory/`    | `agent_runtime/context/memory/`                                                             |
| `agent_runtime/skills/`    | `agent_runtime/capabilities/skills/`                                                        |
| `agent_runtime/subagents/` | `agent_runtime/delegation/subagents/`                                                       |
| `agent_runtime/tools/`     | `agent_runtime/capabilities/tools/`                                                         |

Verified empty by `find services/ai-backend/src/agent_runtime/{agent,mcp,memory,skills,subagents,tools} -name "*.py"` returning no hits.

## Appendix B — Out of scope

This audit covers ai-backend only. The following are shown only as external actors:

- `apps/frontend/` — the React app, calls only the facade.
- `services/backend-facade/` — proxies `/v1/*` from apps to ai-backend; no AI orchestration lives there.
- `services/backend/` — owns MCP registry, OAuth state, token vault, user skills, audit events. ai-backend reaches it over HTTP only.

For cross-service architecture see the top-level [README.md](../../../README.md) and the per-service `CLAUDE.md` files.

## Maintenance

If you change a contract (`RuntimeEventEnvelope`, a port, `RuntimeApiEventType`), update the matching `.puml` and the relevant section above in the same PR. The diagrams are hand-maintained — there's no auto-generation step — so they are only useful while they stay in sync.
