# System Map

Module-to-file-to-responsibility reference. Use this when you need to find where something lives
or verify which module is authorised to import which.

See also: [01-request-lifecycle.md](01-request-lifecycle.md) for how these modules cooperate at runtime.

---

## Top-level processes

| Process      | Entry point                  | When it runs                                                                |
| ------------ | ---------------------------- | --------------------------------------------------------------------------- |
| HTTP API     | `runtime_api/app.py`         | Always; serves conversations, runs, SSE, approvals                          |
| Queue worker | `runtime_worker/__main__.py` | Separate process (or in-process via `RUNTIME_START_IN_PROCESS_WORKER=true`) |

---

## Module map

### `agent_runtime/` — pure domain

No FastAPI, no HTTP routing, no worker loop. Everything here is importable by both
`runtime_api` and `runtime_worker` without circular dependency.

| Sub-module                     | Key files                                                                                                 | Owns                                                                                                                                                                                    |
| ------------------------------ | --------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `api/`                         | `service.py`, `events.py`, `run_coordinator.py`, `conversation_coordinator.py`, `ports.py`                | Presentation/service layer: `RuntimeApiService` coordinator, `RuntimeEventProducer`, domain service classes. All HTTP-to-domain translation happens here.                               |
| `api/`                         | `workspace_coordinator.py`, `workspace_feed_service.py`, `workspace_defaults_service.py`                  | Workspace feeds, defaults, membership resolution                                                                                                                                        |
| `api/`                         | `draft_service.py`, `share_service.py`, `mcp_discovery_service.py`                                        | Draft, share, MCP discovery domain services                                                                                                                                             |
| `api/`                         | `approval_coordinator.py`                                                                                 | Approval lifecycle: create, resolve, forward                                                                                                                                            |
| `api/`                         | `usage_service.py`                                                                                        | Context command + usage rollup query surfaces                                                                                                                                           |
| `api/`                         | `presentation.py`, `presentation_templates.py`                                                            | Event presentation projection                                                                                                                                                           |
| `api/`                         | `notifications.py`                                                                                        | Inbox push notification routing                                                                                                                                                         |
| `execution/`                   | `factory.py`                                                                                              | `acreate_agent_runtime` — builds the LangGraph harness; loads all tools, MCP servers, skills, memory into a `RuntimeHarness`                                                            |
| `execution/`                   | `deep_agent_builder.py`, `graph.py`                                                                       | LangGraph graph definition; Deep Agents builder pattern                                                                                                                                 |
| `execution/`                   | `runtime.py`                                                                                              | `ainvoke_runtime`, `astream_runtime`, `astream_runtime_resume` — thin wrappers over the compiled graph                                                                                  |
| `execution/`                   | `contracts.py`, `models.py`                                                                               | `AgentRuntimeContext`, `RuntimeDependencies`, `StreamEventSource`, `StreamEventType`                                                                                                    |
| `execution/`                   | `provider_kwargs.py`                                                                                      | Resolve provider-specific kwargs (reasoning, grounding, store=False) from `ModelConfig`                                                                                                 |
| `execution/providers/`         | `anthropic_stream_adapter.py`, `openai_responses_stream_adapter.py`, `gemini_grounding_stream_adapter.py` | Provider-specific stream adapters; normalise chunks into `StreamEvent`                                                                                                                  |
| `execution/providers/`         | `citation_pipeline.py`, `citation_extraction.py`                                                          | Intercept provider grounding/citation annotations, feed into `CitationLedger`                                                                                                           |
| `capabilities/tools/`          | `registry.py`, `loader.py`, `cards.py`, `permissions.py`                                                  | Dynamic tool registry; `ToolPermissionChecker`; `ToolLoader` resolves card → callable                                                                                                   |
| `capabilities/tools/builtin/`  | `ask_a_question.py`, `load_tool.py`, `suggest_mcp_connector.py`                                           | Three built-in tools; each is a self-contained async callable                                                                                                                           |
| `capabilities/mcp/`            | `registry.py`, `loader.py`, `client.py`                                                                   | Dynamic MCP registry; `DynamicMcpRegistry` queries `backend` at run-start; `McpClient` dispatches RPC                                                                                   |
| `capabilities/mcp/middleware/` | `call_tool.py`, `auth_mcp.py`, `cite_mcp.py`, `dynamic_loader.py`                                         | MCP middleware chain: permission gate, auth interrupt, citation projection                                                                                                              |
| `capabilities/skills/`         | `manifest.py`, `sources.py`, `policy.py`, `middleware.py`                                                 | Skill bundle loading from `backend`; `SkillPolicyGate`; skill middleware injects into system prompt                                                                                     |
| `capabilities/`                | `citations.py`                                                                                            | `CitationLedger` — per-run idempotent citation registry, single seam for all paths                                                                                                      |
| `capabilities/`                | `citation_resolver.py`                                                                                    | `CitationResolver` — watches streamed text for `[[N]]` markers; emits `citation_made` events                                                                                            |
| `capabilities/`                | `citation_projection.py`                                                                                  | `CitationProjector` — shared shape extractor; maps tool result shapes to `SourceRef`                                                                                                    |
| `capabilities/`                | `conversation_ordinals.py`                                                                                | `ConversationOrdinalAllocator` — monotonic ordinals per conversation, persisted idempotently                                                                                            |
| `capabilities/`                | `auth_gate.py`, `retrying_tool.py`, `tool_budget_guard.py`, `tool_budget_middleware.py`                   | Cross-cutting tool wrappers: auth gate, retry, per-run tool budget                                                                                                                      |
| `capabilities/backends/`       | `draft_backend.py`                                                                                        | `DraftBackend` — draft CRUD and send validation                                                                                                                                         |
| `context/memory/`              | `backends.py`, `policy.py`, `token_budget.py`, `summarization.py`, `contracts.py`                         | Memory scopes, access policy, token budget computation, summarisation                                                                                                                   |
| `delegation/subagents/`        | `runner.py`, `handoff.py`, `atlas_task_tool.py`, `definitions.py`                                         | Subagent fleet lifecycle; `AtlasTaskTool` — model-facing delegation primitive                                                                                                           |
| `persistence/`                 | `ports.py`                                                                                                | Higher-level port protocols: `DraftStorePort`, `CitationStorePort`, `SubagentStorePort`, `SourceStorePort`, `ConversationToolOrdinalStorePort`, `ShareStorePort`, `CheckpointStorePort` |
| `persistence/records/`         | `common.py`, `runs.py`, `citations.py`, `drafts.py`, `subagents.py`, …                                    | Pydantic record types for every persisted entity                                                                                                                                        |
| `persistence/`                 | `schema/postgres.py`, `schema/migrate.py`                                                                 | Postgres DDL and migration runner                                                                                                                                                       |
| `persistence/`                 | `encryption.py`, `_aws_kms_client.py`                                                                     | Field-level encryption for sensitive columns (KMS-backed)                                                                                                                               |
| `observability/`               | `redactor.py`, `tracing.py`, `otel.py`, `logging.py`, `usage_recorder.py`, `token_usage.py`               | OTEL tracing, structured logging, payload redaction, usage recording                                                                                                                    |
| `budgets/`                     | `enforcer.py`, `charger.py`, `estimator.py`, `reservations.py`                                            | Token budget enforcement, CAS-safe charging, per-run reservation                                                                                                                        |
| `pricing/`                     | `catalog.py`, `calculator.py`, `refresh_loop.py`                                                          | Model pricing catalog (micro-USD); `CostCalculator`; background refresh from LiteLLM                                                                                                    |
| `prompts/`                     | `runtime.py`, `tools.py`                                                                                  | System prompt assembly; tool-description injection                                                                                                                                      |
| `retention/`                   | `policy_resolver.py`                                                                                      | Resolve retention policies from workspace config                                                                                                                                        |
| `deployment/`                  | `profile.py`                                                                                              | Deployment profile (feature flags resolved from env)                                                                                                                                    |

---

### `runtime_api/` — FastAPI application

Imports from `agent_runtime/` only. No direct imports from `runtime_worker/` or `runtime_adapters/`.

| Sub-module | Key files                                                                     | Owns                                                                               |
| ---------- | ----------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Root       | `app.py`, `auth.py`, `identity.py`, `rbac.py`                                 | FastAPI app, JWT verification, RBAC dependency injection                           |
| `http/`    | `routes.py`                                                                   | Main router: conversations, runs, events, SSE, approvals, usage, budgets           |
| `http/`    | `drafts.py`, `share_routes.py`, `share_fork_routes.py`, `self_fork_routes.py` | Draft and share routes                                                             |
| `http/`    | `workspace.py`, `workspace_data_routes.py`, `workspace_defaults_routes.py`    | Workspace routes                                                                   |
| `http/`    | `retention_routes.py`, `audit_list_routes.py`                                 | Admin-facing routes                                                                |
| `schemas/` | `events.py`                                                                   | `RuntimeEventEnvelope`, `RuntimeEventPresentationProjector`, `RuntimeApiEventType` |
| `schemas/` | `runs.py`, `conversations.py`, `approvals.py`, `commands.py`, `usage.py`, …   | All HTTP request/response Pydantic shapes                                          |
| `sse/`     | `adapter.py`                                                                  | `RuntimeSseAdapter` — replays events + bus notification loop                       |
| `sse/`     | `event_bus.py`, `postgres_event_bus.py`, `inbox_bus.py`                       | In-memory and Postgres LISTEN/NOTIFY event buses                                   |

---

### `runtime_worker/` — Worker process

Imports from `agent_runtime/` and `runtime_api/schemas/` only.

| Sub-module  | Key files                                                                         | Owns                                                                              |
| ----------- | --------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Root        | `__main__.py`, `loop.py`                                                          | Claim loop: poll queue, dispatch by command type, heartbeat                       |
| `handlers/` | `run.py`                                                                          | `RuntimeRunHandler` — sets up harness, drives `StreamingExecutor`, charges budget |
| `handlers/` | `cancel.py`                                                                       | `RuntimeCancelHandler` — marks run CANCELLED, emits terminal event                |
| `handlers/` | `approval.py`                                                                     | `RuntimeApprovalHandler` — loads checkpoint, resumes `StreamingExecutor`          |
| Root        | `streaming_executor.py`                                                           | `StreamingExecutor` — main async stream loop over LangGraph output                |
| Root        | `stream_events.py`                                                                | `StreamOrchestrator` — maps stream chunks to `RuntimeEventProducer` calls         |
| Root        | `stream_messages.py`, `stream_parts.py`, `stream_tools.py`, `stream_subagents.py` | Channel-specific parsers for message, part, tool, subagent chunks                 |
| Root        | `delta_coalescer.py`, `tool_call_ledger.py`, `tool_observations.py`               | Coalesce MODEL_DELTA chunks; track tool calls for dedup and retry                 |
| Root        | `approval_recognisers.py`                                                         | Pattern-match stream chunks → `ApprovalParam` records                             |
| Root        | `run_metrics.py`                                                                  | `AssistantRunMetrics` — accumulate per-run token counts across model calls        |
| `jobs/`     | `approval_expiry_sweeper.py`                                                      | Background job: expire stale approval rows                                        |
| `jobs/`     | `retention_sweeper.py`                                                            | Background job: sweep data past retention deadline                                |
| `jobs/`     | `retention_backfill.py`                                                           | One-time backfill job: populate `retention_until` on existing rows                |
| `jobs/`     | `encrypt_existing_columns.py`                                                     | One-time encryption migration job                                                 |
| Root        | `usage_rollup_loop.py`                                                            | Background loop: aggregate `RuntimeModelCallUsageRecord` → daily rollup           |
| Root        | `audit.py`, `dependencies.py`                                                     | Worker-level audit emission; `DefaultRuntimeDependenciesFactory` wiring           |

---

### `runtime_adapters/` — Concrete adapter implementations

| Sub-module   | Key files                                                        | Owns                                                                                 |
| ------------ | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Root         | `factory.py`, `base.py`                                          | Adapter selection by `RUNTIME_STORE_BACKEND`; shared base types                      |
| `in_memory/` | `runtime_api_store.py`, `draft_store.py`, `citation_store.py`, … | Async in-memory implementations of every port — used in tests and single-process dev |
| `postgres/`  | `runtime_api_store.py`, `draft_store.py`, `citation_store.py`, … | Async Postgres implementations using asyncpg connection pools                        |

---

## Import rules (hard)

```
runtime_api/       → may import agent_runtime/
runtime_worker/    → may import agent_runtime/, runtime_api/schemas/
runtime_adapters/  → may import agent_runtime/persistence/ports, runtime_api/schemas/
agent_runtime/     → must not import runtime_api/, runtime_worker/, runtime_adapters/
```

Cross-service imports (into `backend/`, `backend-facade/`, `apps/`) are **never** allowed.
Use HTTP or `packages/service-contracts` constants only.

---

## Settings and config

`agent_runtime/settings.py` — `RuntimeSettings` (Pydantic `BaseSettings`).
All environment variables are resolved here. See [reference/env-vars.md](../reference/env-vars.md)
for the complete list.
