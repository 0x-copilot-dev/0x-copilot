# ai-backend — Refactor Audit

A targeted audit of the ai-backend service against four stated concerns plus general architectural smells. Built from the cluster diagrams ([01](01-system-overview.puml)–[11](11-cross-cutting.puml)), the flow diagrams ([f1](f1-single-turn.puml)–[f9](f9-usage-metrics.puml)), and the architecture index. **No source files were read.** Every finding is a hypothesis derived from the documented design; every claim should be verified in code before any refactor is committed.

The audit is opinionated. It is not a pull request. Its job is to surface where engineering effort can be redirected to fewer, simpler files; where standard libraries replace home-grown systems; and where user-visible latency is being burned for no benefit. The complementary job — protecting load-bearing behavior — is captured in the [Behaviors that must be preserved](#behaviors-that-must-be-preserved) section. Read both before touching anything.

---

## Executive summary

| Concern                                              | Verdict   | Worst single instance                                            |
| ---------------------------------------------------- | --------- | ---------------------------------------------------------------- |
| Bespoke code we don't need                           | Confirmed | The sync + async port pair (3 layers for what should be 1)       |
| Edge-case vs system-level                            | Confirmed | 9+ persistence ports + 17+ record types per domain object        |
| Not using available libraries                        | Confirmed | Provider stream adapters + pricing catalog (LiteLLM solves both) |
| Latency / unnecessary LLM calls / missed parallelism | Confirmed | LLM polish per event + ~1s SSE delivery in production            |

The two highest-impact changes — neither structural — are:

1. **Stop running an LLM per event** ([PresentationGenerator polish](#11-presentationgenerator-polish-on-every-event)). Removes ~50% of background LLM cost on a turn, halves user-visible event volume.
2. **Replace the in-process event bus with Postgres `LISTEN/NOTIFY`** ([SSE bus is in-memory only](#41-sse-delivery-is-1s-in-production)). Drops average SSE delivery from ~1s to ~50ms in production.

Together these two are a few hundred lines of change with no API surface impact and a step-change in user-perceived performance. They should land before any structural refactor, because every structural cleanup gets easier once the cost surface and event volume are smaller.

---

## How to read this audit

- File references use `[name](relative/path)` so they're clickable in IDE.
- Each finding lists: **what we see**, **why it's a problem**, **the change**, **what must survive**, and **risk** (Low / Medium / High).
- Numbered findings (e.g. **1.1**) can be cross-referenced from the [Recommended sequencing](#recommended-refactor-sequence) section.
- "Hypothesis" or "verify in code" tags mark anything that depends on details I couldn't see in the diagrams.

---

## 1. Bespoke code we don't need

### 1.1 PresentationGenerator polish on every event

**What we see.** [`RuntimeEventProducer`](../../src/agent_runtime/api/events.py) attaches a synchronous preliminary presentation, persists, notifies, then _spawns an async LLM polish task_ that emits a follow-up `PRESENTATION_UPDATED` event patching only body fields. The architecture index confirms this runs on visible events; flow [f2](f2-multi-turn-tool.puml) shows it firing on every `TOOL_RESULT`.

**Why it's a problem.** A turn that emits 30 visible events spawns ~30 background LLM calls just to rewrite display titles and summaries. Each polish triggers a second event append (`PRESENTATION_UPDATED`), so visible-event volume doubles. Cost is variable but real (provider tokens + rate-limit budget); the bigger hidden cost is event-pipeline pressure (every polish = INSERT + UPDATE + NOTIFY).

**The change.** Drop polish entirely. `activity_kind` (timeline bucket) plus structured `payload` plus a per-event-type rendering template on the client side covers every display surface I've seen documented. If polish has a behavior I'm missing — e.g. summarizing long tool outputs into a card-friendly preview — keep that one specific case as a synchronous transform on the writer side, not an async LLM rewrite.

**What must survive.** `RuntimeEventPresentation` (title / status_label / kind / summary / result_preview) on the envelope; clients consume these. Frozen fields (title / status_label / kind set by lifecycle) cannot regress. The `activity_kind` enum is the canonical timeline bucket and is non-negotiable.

**Risk.** Low if polish is purely cosmetic; Medium if any client renders solely from `presentation.summary` and would show empty cards without it. Verify by grepping the frontend for usage of `presentation.summary` vs `payload.*`.

### 1.2 Sync ports + async ports + async_wrappers (3 layers for 1)

**What we see.** Three coexisting layers: [`agent_runtime/api/ports.py`](../../src/agent_runtime/api/ports.py) (sync Protocols), [`agent_runtime/api/async_ports.py`](../../src/agent_runtime/api/async_ports.py) (async Protocols), and [`runtime_adapters/async_wrappers.py`](../../src/runtime_adapters/async_wrappers.py) (`adapt_*_to_async via to_thread` bridge). The architecture index admits "from*settings → in_memory only (sync ports)" — sync ports exist \_only* for the in-memory dev path. Production uses async ports; the worker runs `to_thread` to bridge sync calls into the async loop.

**Why it's a problem.** Three Protocol families (sync, async, bridge) for one concept. Every new persistence operation must be defined three times. Tests have to choose which surface to fake. `AsyncInMemoryRuntimeApiStore` is described as "thin wrapper, no real awaits" — async surface, sync semantics, no concurrency.

**The change.** Make in-memory adapters async-native. Delete `agent_runtime/api/ports.py` (sync). Delete `runtime_adapters/async_wrappers.py`. Delete `AsyncInMemoryRuntimeApiStore`. Promote async Protocols to `ports.py`.

**What must survive.** Test fakes. The async in-memory adapter must be importable from tests with no event-loop ceremony beyond `pytest-asyncio`'s default fixture.

**Risk.** Medium. Touches every service that depends on either Protocol family. Mechanical but wide. Recommended approach: write the async in-memory adapter first, run the suite, _then_ delete the sync code path file by file.

### 1.3 Custom hash-chained audit log — RESOLVED (de-duplicated, not deleted)

**Status.** Resolved. The original recommendation ("delete the chain, rely on SIEM-side integrity") was reversed after a code-level investigation found:

- A SIEM cursor already exists at [`/internal/v1/audit/cursor`](../../src/runtime_api/http/routes.py); the SIEM-export pattern was already in place.
- Append-only enforcement is already three-layer (Postgres `audit_writer` role with revoked UPDATE/DELETE, a constraint trigger on `runtime_audit_log`, and the chain itself).
- The chain catches tampering in the **write → SIEM-export window** (minutes-to-hours during pump outages); SIEM alone cannot.

The actual smell was code duplication between `services/backend` and `services/ai-backend`. Both files implemented the same HMAC chain primitive and had drifted in line count.

**What landed.** A new shared package `packages/audit-chain/` (`enterprise_audit_chain`) hosts the single canonical implementation. Both services import from it. Per-service `from_env(environment_env_var=...)` makes the env var dependency explicit. Compat-fixture tests in both services pin pre-refactor signatures to byte-identical values so historical chains keep verifying.

- New package: [packages/audit-chain/](../../../../packages/audit-chain/)
- PRD with full investigation, behavior preservation, phasing, and risk analysis: [docs/refactor/01-audit-chain.md](../refactor/01-audit-chain.md)
- Compat tests: [ai-backend test_audit_chain_compat.py](../../tests/unit/agent_runtime/observability/test_audit_chain_compat.py), [backend test_audit_chain_compat.py](../../../backend/tests/test_audit_chain_compat.py)
- Removed: `services/ai-backend/src/agent_runtime/observability/audit_chain.py`, `services/backend/src/backend_app/audit_chain.py`

### 1.4 Custom redactor

**What we see.** [`agent_runtime/observability/redaction.py`](../../src/agent_runtime/observability/redaction.py) implements `ObservabilityRedactor` with sensitive key/value patterns + an allow-list of user content keys. Runs on every event payload + metadata via Pydantic field validators.

**Why it's a problem.** Pattern-based PII / credential redaction is a solved problem with mature libraries (Microsoft Presidio for PII, detect-secrets / scrubadub for tokens). Maintaining patterns in-house means missing patterns when new PII shows up.

**The change.** Replace with [Presidio](https://microsoft.github.io/presidio/) for PII categories, [detect-secrets](https://github.com/Yelp/detect-secrets) for credential scrubbing, or Scrubadub for the lightweight middle ground. Keep the allow-list of user content keys local — that's a domain decision.

**What must survive.** Allow-listed user content keys (the negative space — what is intentionally _not_ redacted). The redactor's invocation point (Pydantic field validators on `payload` + `metadata`).

**Risk.** Medium. Output of a different redactor will look slightly different — diff golden tests carefully.

### 1.5 Custom retention sweep + 5-level policy resolver

**What we see.** [`agent_runtime/retention/policy_resolver.py`](../../src/agent_runtime/retention/policy_resolver.py) walks `CONVERSATION > ASSISTANT > USER > ORG > default` for resolution; the worker runs a `RetentionSweeperLoop` to tombstone messages, events, payloads, memory items.

**Why it's a problem.** Tombstone sweeps in application code do at small scale what Postgres does natively at any scale via time-partitioning + `DROP PARTITION`. The hierarchical scope is real, but the sweep is reinventing TTL.

**The change.** Two-step. (a) Keep `RetentionPolicyResolver` for the _resolution_ logic — that's domain. (b) Replace the sweep with `pg_partman` time-partitioned tables on the high-volume rows (`runtime_events`, `agent_messages`, `runtime_run_usage`, `runtime_model_call_usage`). `DROP PARTITION` runs in milliseconds and reclaims disk; tombstone-and-vacuum doesn't.

**What must survive.** The 5-level resolution hierarchy (a critical compliance behavior — buyers configure overrides at multiple levels). User-visible deletion semantics (a delete must remove the row in observable time).

**Risk.** Medium-High. Schema change, requires a migration window. Not a candidate for the first refactor wave.

### 1.6 Custom budget / pricing system + seed catalog

**What we see.** [`agent_runtime/pricing/`](../../src/agent_runtime/pricing/) ships its own `ModelPricingCatalog` + `seed_loader.py` + a `seeds/` directory with per-model pricing rows. [`agent_runtime/budgets/`](../../src/agent_runtime/budgets/) is 5 files (charger, enforcer, estimator, reservations, period).

**Why it's a problem.** [LiteLLM](https://github.com/BerriAI/litellm) ships per-model pricing for every provider, kept current upstream. Maintaining your own seed catalog is a recurring tax — every new model release requires a seed bump. The 5-file budget split is fine if budgets are a core feature; if not, this is more code than it deserves.

**The change.** (a) Make LiteLLM the _source_ for the pricing table — load it on startup, refresh periodically. Keep `ModelPricingRecord` as the persisted form so historical cost rows stay frozen. (b) Leave `budgets/` alone unless complexity hides duplication; verify with code review.

**What must survive.** Cost stamping at write time (pricing changes do not retroactively rewrite history — see [f9](f9-usage-metrics.puml)). Integer micro-USD with banker's rounding (precision is a real correctness requirement). Idempotent CAS update in `BudgetCharger.charge_run` (retry safety).

**Risk.** Low for the pricing-source swap. Tested by snapshotting cost values before and after for a representative usage set.

### 1.7 Custom migration runner

**What we see.** [`agent_runtime/persistence/schema/migrate.py`](../../src/agent_runtime/persistence/schema/migrate.py) — bespoke migration script.

**Why it's a problem.** Alembic exists, integrates with SQLAlchemy, supports autogenerate, supports data migrations, has a downgrade story.

**The change.** Adopt Alembic; convert existing migrations one-shot.

**What must survive.** Current schema state and the order of historical migrations.

**Risk.** Low. Greenfield switch; keep `migrate.py` as a no-op shim during transition if necessary.

### 1.8 EncryptExistingColumns running as a perpetual job

**What we see.** [`runtime_worker/jobs/encrypt_existing_columns.py`](../../src/runtime_worker/jobs/encrypt_existing_columns.py) listed as a worker background job.

**Why it's a problem.** Naming and intent both suggest this is a one-shot data migration that calcified into a forever-running daemon. Daemons that idempotently scan empty work add database load and complicate worker shutdown.

**The change.** Convert to an Alembic data migration, or a one-shot CLI job. Delete the worker hook.

**What must survive.** The actual encryption logic — re-use it from the migration.

**Risk.** Low. Verify by checking how often the job actually finds rows to encrypt.

---

## 2. Edge-case-level vs system-level

The signal in this category is "one type per concept, all the way down." Adding a new requirement means adding a new file, port, record, service, capability — alongside the existing ones, never replacing them.

### 2.1 9+ persistence ports + 17+ record types

**What we see.** Per [C8a](06-persistence.puml): `MemoryMetadataPort`, `PayloadStoragePort`, `CheckpointStorePort`, `DraftStorePort`, `SubagentStorePort`, `SourceStorePort`, `CitationStorePort`, `ConversationToolOrdinalStorePort`, `ShareStorePort` — plus the `PersistencePort` / `EventStorePort` / `RuntimeQueuePort` trio in `api/ports.py` × (sync + async) = ~15 Protocol interfaces. Records are split across 4 sub-packages (`run/`, `mem/`, `ws/`, `admin/`) for 17+ types.

**Why it's a problem.** Hexagonal pattern is justified by the in-memory + postgres adapter pair. Per-domain-object ports (`ConversationToolOrdinalStorePort` for a join table; `SubagentStorePort` for what the diagram explicitly calls a "read-only projection of SUBAGENT\_\* events") is excessive. Adding a column requires touching record + port + sync fake + async fake + postgres adapter.

**The change.** Collapse to 3–4 topical repositories: `RunRepository` (runs, events, queue, approvals, telemetry), `WorkspaceRepository` (drafts, shares, citations, ordinals, sources, subagent projections), `MemoryRepository` (memory items, payloads, checkpoints), `AdminRepository` (audit, retention, budgets, pricing). Use SQLAlchemy 2.0 with thin repository methods; let ORM models replace the bulk of `records/`. Pydantic stays at HTTP boundaries.

**What must survive.** All write idempotency invariants: `(run_id, sequence_no)` UNIQUE; `(run_id, connector, doc_id)` for citations; `(tool_call_id)` for ordinals. Field-level encryption (whatever `FieldCodec` does today). The role-tagged `application_name` on the postgres pool (operational visibility).

**Risk.** High. The biggest structural change. Best approached as a new repository in parallel, route reads to it under a feature flag, route writes to both, then cut over.

### 2.2 8+ files of citation infrastructure

**What we see.** Citations span: [`capabilities/citations.py`](../../src/agent_runtime/capabilities/citations.py) (`CitationRegistry`), [`capabilities/citation_resolver.py`](../../src/agent_runtime/capabilities/citation_resolver.py) (`CitationLedger`), [`capabilities/citation_projection.py`](../../src/agent_runtime/capabilities/citation_projection.py), [`capabilities/citation_capturing_tool.py`](../../src/agent_runtime/capabilities/citation_capturing_tool.py), [`capabilities/conversation_ordinals.py`](../../src/agent_runtime/capabilities/conversation_ordinals.py), [`capabilities/mcp/middleware/cite_mcp.py`](../../src/agent_runtime/capabilities/mcp/middleware/cite_mcp.py) (`CitationProjectingMcpMiddleware`), [`execution/providers/citation_pipeline.py`](../../src/agent_runtime/execution/providers/citation_pipeline.py) (`CitationStreamPipeline`), [`execution/providers/citation_extraction.py`](../../src/agent_runtime/execution/providers/citation_extraction.py).

**Why it's a problem.** Per [f5](f5-citations.puml), the _behavior_ is sophisticated and load-bearing — conversation-scoped ordinals shared across turns and subagents, idempotent ingestion, sealed snapshot at FINAL*RESPONSE, reconstruction from the store on resume. But the \_file count* is 1–2 classes worth of responsibility spread across 8 files. `CitationLedger` and `CitationRegistry` should not be separate types.

**The change.** Three files: `CitationService` (ledger + registry + ordinal allocator + sealing), `ProviderCitationExtractor` (multi-provider grounding metadata), `MCPCitationMiddleware` (projects `[[N]]` markers into MCP results). Delete the rest.

**What must survive.** Conversation-scoped ordinal namespace persisting across turns AND subagents. Idempotency keys: `(tool_call_id)` for ordinals, `(run_id, connector, doc_id)` for citations. Snapshot at FINAL_RESPONSE attaching sealed list to `payload.citations`. Reconstruction from `CitationStorePort.list_for_run` on approval-resume or worker crash. Workspace Sources tab via `SourceStorePort.aggregate_for_conversation`.

**Risk.** Medium. Mechanical consolidation, but citations are user-visible and any drift is obvious.

### 2.3 Four-way permission model (3 specific + 1 generic)

**What we see.** Per [C6](04-capabilities.puml): `ToolPermissionChecker`, `McpPermissionPolicy`, `SkillPermissionPolicy`, plus a generic [`CapabilityAuthGate`](../../src/agent_runtime/capabilities/auth_gate.py).

**Why it's a problem.** Three subsystem-specific policies coexist with a generic gate. Either the gate replaces the three or the three predate it; having both means the abstraction was added without retiring the specifics. New developers don't know which to extend.

**The change.** Pick one model. If `CapabilityAuthGate` is sufficient, delete the per-subsystem checkers and let each subsystem register its policy with the gate. If the per-subsystem checkers carry domain logic the gate can't express, delete the gate and standardize on the per-subsystem pattern.

**What must survive.** Per [f8](f8-mcp-auth.puml): MCP permission checks must run both at list time (filter cards visible to the model) and at call time (defense in depth). This is non-negotiable — the gate fires twice for a reason.

**Risk.** Low–Medium. Mostly a code-organization change.

### 2.4 ToolBudgetMiddleware → ToolBudgetGuard two-step

**What we see.** [`capabilities/tool_budget_middleware.py`](../../src/agent_runtime/capabilities/tool_budget_middleware.py) calls into [`capabilities/tool_budget_guard.py`](../../src/agent_runtime/capabilities/tool_budget_guard.py).

**Why it's a problem.** Two files for a per-task tool-call cap (default 5 per [f2](f2-multi-turn-tool.puml)).

**The change.** Merge unless `ToolBudgetGuard` is reused outside tool middleware. (Verify in code first.)

**Risk.** Low.

### 2.5 DraftBackend in capabilities

**What we see.** [`capabilities/backends/draft_backend.py`](../../src/agent_runtime/capabilities/backends/draft_backend.py).

**Why it's a problem.** Drafts are a product / domain concept (the Workspace pane), not a model capability. The capability cluster is for tool / MCP / skill / subagent surfaces the model uses. Drafts being a "capability" is path-of-least-resistance filing.

**The change.** Move to `agent_runtime/api/draft_service.py` (already exists) or its own module. Keep the `/drafts/` filesystem routing into `DraftStorePort`; that's the right abstraction, just in the wrong layer.

**Risk.** Low. File move + import updates.

### 2.6 Service splits inside C4 that should be one service each

**What we see.** Per [C4](05-runtime-services.puml):

- `ConversationFork` + `SelfFork` — two services for fork variants.
- `WorkspaceFeedService` + `WorkspaceDefaultsService` — two services for one concept.
- `McpDiscoveryService` + `SuggestibleConnectorsResolver` — different consumers but possibly overlapping responsibility.
- `UsageService` exposing `headroom_pct` — a stateless utility, not a service.

**Why it's a problem.** Each split adds a class, a test file, and a wiring point. None of these are large enough to warrant the ceremony.

**The change.** `ForkService` (one); `WorkspaceService` (one); leave `McpDiscoveryService` and `SuggestibleConnectorsResolver` separate until verified to overlap (per [f7](f7-mcp-add.puml) they serve different consumers); collapse `UsageService.headroom_pct` into `ConversationContextBuilder` (it already exists per [f9](f9-usage-metrics.puml)).

**Risk.** Low.

### 2.7 RuntimeApiService at 2.4k LOC

**What we see.** [`agent_runtime/api/service.py`](../../src/agent_runtime/api/service.py) — coordinator that "owns run lifecycle, approval lifecycle, notification fan-out, replay / context summaries" per [C4](05-runtime-services.puml). Eight peer "domain services" already exist beside it.

**Why it's a problem.** A single 2.4k-LOC class that's the coordinator for everything is the central god-class smell. The team already knows how to split (the 8 peer services prove it); they just stopped halving the coordinator.

**The change.** Last in the sequence, after the dependents shrink. Split into `RunCoordinator` (lifecycle, queue interaction), `ApprovalCoordinator` (approval state machine, MCP auth), `ConversationContextService` (replay, context summaries — likely already mostly extracted), `NotificationFanout` (the bus interactions). Each ~300–500 LOC.

**What must survive.** Every public method on `RuntimeApiService` that the API and worker call (the wide API surface from C4). Idempotency on retried commands. Both API and worker calling the same coordinator.

**Risk.** High. Do this last, after other refactors have shrunk the surface area to coordinate.

---

## 3. Library replacements

Each row below replaces or shrinks an in-house subsystem with a battle-tested library. None require a big-bang migration.

| In-house code                                                                                                                   | Replacement                                                               | What changes                                                                                                                                                                                                                                                              | Risk                            |
| ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------- |
| [`pricing/calculator.py`](../../src/agent_runtime/pricing/calculator.py) + `catalog.py` + `seed_loader.py` + `seeds/`           | LiteLLM as the pricing source                                             | Load LiteLLM's model_prices_and_context_window into `ModelPricingRecord` on startup; refresh weekly. Stored cost values stay frozen.                                                                                                                                      | Low                             |
| `execution/providers/anthropic_stream_adapter.py` + `openai_responses_stream_adapter.py` + `gemini_grounding_stream_adapter.py` | LiteLLM streaming + `acompletion`                                         | **Verify first** that LiteLLM handles thinking_mode (Anthropic), reasoning summary (OpenAI Responses), and grounding metadata (Gemini) correctly per [f6](f6-thinking.puml). If yes: delete adapters. If partial: keep custom adapters only for the unsupported features. | High — provider quirks are real |
| [`execution/provider_kwargs.py`](../../src/agent_runtime/execution/provider_kwargs.py)                                          | Pydantic Settings + LiteLLM kwargs passthrough                            | `workspace_model_kwargs` + `user_policy_model_kwargs` are config concerns that crept into execution. Resolve as settings, pass to LiteLLM.                                                                                                                                | Low–Medium                      |
| `CheckpointStorePort` + `CheckpointRecord` + adapters                                                                           | LangGraph Checkpointer (Postgres / in-memory built-in)                    | You're already on LangGraph; use its checkpointer instead of a parallel one. Persistence boundary moves into LangGraph.                                                                                                                                                   | Medium                          |
| Custom approval lifecycle inside `RuntimeApiService`                                                                            | LangGraph human-in-the-loop interrupts (with checkpointer for durability) | Approval row remains the rendezvous; LangGraph interrupt + resume becomes the mechanism. **Must preserve durability, separate-command resume, multi-fire (token rotation per [f8](f8-mcp-auth.puml)).**                                                                   | High                            |
| [`observability/audit_chain.py`](../../src/agent_runtime/observability/audit_chain.py)                                          | SIEM-side integrity / managed audit service                               | See [1.3](#13-custom-hash-chained-audit-log).                                                                                                                                                                                                                             | Medium (compliance)             |
| [`observability/redaction.py`](../../src/agent_runtime/observability/redaction.py)                                              | Presidio + detect-secrets                                                 | See [1.4](#14-custom-redactor).                                                                                                                                                                                                                                           | Medium                          |
| [`observability/db_statement_metrics.py`](../../src/agent_runtime/observability/db_statement_metrics.py)                        | OpenTelemetry SQLAlchemy + asyncpg auto-instrumentation                   | OTel ships statement timing, query categorization, slow-query flagging.                                                                                                                                                                                                   | Low                             |
| Most of `observability/` (10 files)                                                                                             | OTel SDK + auto-instrumentation + a thin `usage_attribution` shim         | Trace context, structured logging, HTTP / DB instrumentation come from OTel. Keep `usage_attribution.py` (per-user / per-org / per-connector token tagging is your domain).                                                                                               | Medium                          |
| [`retention/policy_resolver.py`](../../src/agent_runtime/retention/policy_resolver.py) sweep                                    | `pg_partman` time-partitioning + `DROP PARTITION`                         | Resolver stays; sweeper goes. See [1.5](#15-custom-retention-sweep--5-level-policy-resolver).                                                                                                                                                                             | Medium–High                     |
| [`persistence/schema/migrate.py`](../../src/agent_runtime/persistence/schema/migrate.py)                                        | Alembic                                                                   | See [1.7](#17-custom-migration-runner).                                                                                                                                                                                                                                   | Low                             |
| Most of the 9 persistence ports + 17 record types                                                                               | SQLAlchemy 2.0 + thin repositories                                        | See [2.1](#21-9-persistence-ports--17-record-types).                                                                                                                                                                                                                      | High                            |
| `runtime_api/sse/event_bus.py` + `inbox_bus.py` (in-memory)                                                                     | Postgres `LISTEN/NOTIFY` (or Redis pub/sub)                               | See [4.1](#41-sse-delivery-is-1s-in-production).                                                                                                                                                                                                                          | Medium                          |

**Caveats:**

- **LiteLLM streaming adapters need verification** before a full replacement. Reasoning streaming for Anthropic (`thinking_mode {ENABLED, ADAPTIVE}`, `display {OMITTED, SUMMARIZED}`), OpenAI Responses API summary, and Gemini grounding are recent and provider-specific. If LiteLLM lacks first-class support for any one, keep that adapter and migrate the other two.
- **LangGraph human-in-the-loop interrupts** must preserve all approval invariants per [f8](f8-mcp-auth.puml): durable across worker restart, resumed via separate `APPROVAL_RESOLVED` command (not inline continuation), multi-fire on token rotation mid-run, approval row as durable rendezvous.

---

## 4. Latency, unnecessary LLM calls, and missed parallelism

### 4.1 SSE delivery is ~1s in production

**What we see.** [`runtime_api/sse/event_bus.py`](../../src/runtime_api/sse/event_bus.py) is in-memory per process. Per [C1](02-runtime-api.puml) lifespan, the worker can be started in-process for dev (`RUNTIME_START_IN_PROCESS_WORKER=true`); in production it's a separate process. `RuntimeEventProducer.notify_sync` writes to the bus local to whichever process emitted the event. The SSE adapter falls back to `event_bus.wait(run_id, timeout=FALLBACK_POLL_SECONDS=2.0)` then re-queries the store.

**Why it's a problem.** In production the worker's notify never reaches the API process. The 2-second poll is the actual delivery mechanism. Average new-event latency to the browser is ≈1 second. The "live streaming" UX is 1-second-stutter streaming.

The same bug exists twice: [`event_bus.py`](../../src/runtime_api/sse/event_bus.py) for run streams, [`inbox_bus.py`](../../src/runtime_api/sse/inbox_bus.py) for the inbox.

**The change.** Postgres `LISTEN/NOTIFY` on a per-run channel. Worker `NOTIFY` after `append_event`; API `LISTEN` per active SSE stream. Sub-50ms wake-up, no extra infrastructure. Bonus: put `sequence_no` in the NOTIFY payload so the SSE adapter can skip `replay_events` if it's already current.

**What must survive.** SSE wire format (`event: <name>\nid: <seq>\ndata: <json>\n\n`). Resume contract (`?after_sequence=N`). `follow=false` synthetic heartbeat behavior. Terminal-status auto-close. Inbox bus has the same fix.

**Risk.** Medium. Self-contained. Roll out behind a flag (`RUNTIME_BUS_BACKEND=memory|postgres`) and compare delivery latency on staging before defaulting on.

### 4.2 LLM polish per event

See [1.1](#11-presentationgenerator-polish-on-every-event). This is also a latency / cost finding — the polish costs tokens, contributes background load to the model providers, and doubles event volume in the store.

### 4.3 Per-event DB amplification

**What we see.** Per [f1](f1-single-turn.puml) and [C3](07-adapters.puml) hazard fixes:

1. `INSERT INTO runtime_events` (`append_event`)
2. `UPDATE agent_runs SET latest_sequence_no = ?` (`set_run_latest_sequence`)
3. `SELECT … FOR UPDATE` on `agent_runs` to serialize concurrent writes per run (H2)

That's **3 DB ops per event**. With PRESENTATION_UPDATED, **6 ops per user-visible event**. A turn with 100 MODEL_DELTA chunks = ~300–600 DB ops just for event flow.

**The change.** Three independent wins:

1. Combine append + set-latest into one statement using a CTE (`WITH e AS (INSERT … RETURNING sequence_no) UPDATE agent_runs SET latest_sequence_no = (SELECT sequence_no FROM e) RETURNING ...`).
2. Replace `SELECT FOR UPDATE` with a per-run Postgres sequence (`CREATE SEQUENCE per_run_seq_<run_id>`) for `sequence_no` allocation. Eliminates the row lock entirely. (Or: a single sequence keyed by `(run_id, nextval)` enforced by the UNIQUE.)
3. Worker-side coalescing of MODEL_DELTA storms — accumulate chunks in a 50ms window, write one row with `payload.delta` containing N chunks. Trade slight client perceptibility delay for an order-of-magnitude reduction in write volume. (Verify the SSE consumer can render a chunk that contains multiple deltas; if not, keep one delta per row but coalesce the DB writes via batch insert.)

**What must survive.** Strict per-run monotonic `sequence_no`. UNIQUE(`run_id`, `sequence_no`) constraint. SSE resume contract.

**Risk.** Medium. Schema change (sequences) plus a touchy code path. Worth doing in stages.

### 4.4 Sequential bootstrap in `create_agent_runtime`

**What we see.** [`agent_runtime/execution/factory.py`](../../src/agent_runtime/execution/factory.py) (602 LOC). Per the diagram and the f-flow narrative, factory bootstrap fetches: `list_available_tools` + `list_available_servers` + `list_available_subagents` + `load_skill_directories` + `MembershipResolver` + `UserPoliciesResolver` + `SuggestibleConnectorsResolver`. All independent (different ports, different backend endpoints).

**Why it's a problem.** Sequential I/O on every run start. Adds startup latency to every single run — TTFB before the model even sees a token.

**The change.** `asyncio.gather()` everything that's independent. Verify in code which calls have inter-dependencies (e.g. tool listing might need the membership decision); only those stay sequential.

**What must survive.** Whatever ordering constraints actually exist in code. Permission decisions (no listing past an unauthorized scope).

**Risk.** Low. Local change to one file.

### 4.5 Sequential citation ingestion

**What we see.** Per [f5](f5-citations.puml), per cited source: `ord.record` → `led.ingest` → `CitationStorePort.insert_or_get` → emit `SOURCE_INGESTED` event. A tool returning 20 sources = 20 sequential idempotent inserts + 20 separate event appends.

**Why it's a problem.** Research-heavy tool calls (Linear search, Notion query) routinely return 10+ sources. 10× sequential roundtrips before the next model turn can start.

**The change.** Batch inserts (`INSERT … ON CONFLICT DO NOTHING` with VALUES from the full source list). One `SOURCES_INGESTED` event with N sources instead of N separate events.

**What must survive.** Idempotency on `(run_id, connector, doc_id)`. Ordinal allocation order matching the order the model will reference them.

**Risk.** Low–Medium.

### 4.6 SSE fan-out duplication

**What we see.** Each subscriber to the same run independently calls `replay_events`. Two browser tabs viewing the same run = two parallel reads of the same events.

**The change.** Single tail per run, fan out in-process to all subscribers. Only one `replay_events` per tick regardless of subscriber count.

**Risk.** Low. Standard pub/sub fan-out pattern.

### 4.7 Multi-tool parallel execution (verify)

**What we see.** When the model emits multiple tool calls in one turn, LangGraph supports executing them in parallel. **Verify** the `StreamingExecutor` and the Deep Agents builder enable this.

**Why it's a problem.** If they force sequential, every tool-heavy turn pays the sum of tool latencies instead of the max.

**The change.** Confirm parallel tool execution is enabled in the LangGraph configuration.

**Risk.** Verification first; change is config-level.

### 4.8 Polish task spawning (if polish stays)

**What we see.** Each event spawns its own polish task = its own LLM call.

**The change.** If [1.1](#11-presentationgenerator-polish-on-every-event) doesn't ship as a delete, batch instead: collect events over a 200ms window, ask the model "give me titles for these N events" in one call. 10× fewer model calls for the same coverage.

**Risk.** Low. Independent of broader polish decision.

### 4.9 `ConversationContextBuilder` per `/context` query

**What we see.** Per [f9](f9-usage-metrics.puml), `/v1/agent/conversations/{id}/context` fetches latest run-usage row + per-call LLM rows + compression events + active pricing on every call. The Builder is stateless and pure — recomputed from rows each time.

**Why it's a problem.** Fine if the frontend rate-limits. If `/context` is called on every keystroke or scroll, this is heavy.

**The change.** Verify call frequency from the frontend. If high-frequency, debounce client-side; if it has to be live, cache the rolled-up tuple in Redis (or a Postgres MV) keyed by (conversation_id, latest_event_sequence) so a hit is one row read.

**Risk.** Low if debounced; Medium if requires caching.

### 4.10 Per-event redaction CPU

**What we see.** `ObservabilityRedactor.redact_json_object` runs on `payload` + `metadata` via Pydantic field validators on every event.

**Why it's a problem.** Cheap individually; cumulative cost is real on long runs (1000+ events).

**The change.** Skip redaction for `visibility=INTERNAL` events (they don't reach users). Switch to a faster pattern engine if profiling shows redaction is hot (compiled regex + early return on no-match).

**Risk.** Low.

### 4.11 Three-layer permission check on tool calls

**What we see.** Per [C6](04-capabilities.puml): card visibility check → loader permission re-check → call-time permission re-check.

**Why it's a problem.** Defense in depth is correct. But if any of those three layers makes a network call (e.g. to backend for membership), each tool call adds RTTs.

**The change.** Cache per-(org, user, run) the permission decision. `MembershipResolver` already exists; ensure it caches.

**Risk.** Low.

### 4.12 Background jobs in the worker

**What we see.** `ApprovalExpirySweeper`, `RetentionSweeperLoop`, `EncryptExistingColumns`, `UsageRollupLoop`.

**Why it's a problem.** Probably already concurrent via separate asyncio tasks. Verify they don't share a serial scheduler that would interleave one with active run handlers.

**The change.** Verify; fix only if scheduler is serial.

**Risk.** Verification first.

---

## 5. Other architectural smells

### 5.1 Worker-side ToolCallLedger duplicates persistence-side ToolInvocationStorePort

**What we see.** [`runtime_worker/tool_call_ledger.py`](../../src/runtime_worker/tool_call_ledger.py) maintains worker-side state about tool calls. The persistence layer already has `ToolInvocationRecord` + a port for it.

**Why it's a problem.** Two sources of truth for "what tool calls happened in this run." If they disagree, which wins?

**The change.** Pick one. The DB-side record is the source of truth (survives worker restart); make the worker query it on demand instead of duplicating state.

**Risk.** Medium.

### 5.2 ApprovalRecognisers in the worker

**What we see.** [`runtime_worker/approval_recognisers.py`](../../src/runtime_worker/approval_recognisers.py) — pattern recognition on the LangGraph stream to identify approval requests.

**Why it's a problem.** APPROVAL*REQUESTED and MCP_AUTH_REQUIRED are first-class typed events. The recognizer exists because LangGraph emits raw chunks that the worker has to translate. That translation logic should live where the \_event is created* (the tool / MCP middleware that initiates the approval), not as a stream pattern matcher downstream.

**The change.** Move emission of typed approval events into the tool / middleware that initiates the request. Delete the recognizer.

**Risk.** Medium. Touches the streaming pipeline structure.

### 5.3 Streaming pipeline = 10 files inside the worker

**What we see.** Per [C2](03-runtime-worker.puml): `StreamingExecutor`, `StreamOrchestrator`, `stream_messages`, `stream_parts`, `stream_subagents`, `stream_tools`, `ToolCallLedger`, `ToolObservations`, `ApprovalRecognisers`, `AssistantRunMetrics`/`TokenUsageExtractor`.

**Why it's a problem.** Per-channel handlers (`stream_messages` / `stream_parts` / `stream_subagents` / `stream_tools`) are reasonable splits if each is non-trivial. The derived-state files ([`tool_call_ledger`](#51-worker-side-toolcallledger-duplicates-persistence-side-toolinvocationstoreport), [`approval_recognisers`](#52-approvalrecognisers-in-the-worker)) are smells called out separately.

**The change.** Once the derived-state smells above are addressed, evaluate whether the per-channel files can collapse. Probably 2–3 files are right (`StreamOrchestrator`, channel handlers as a single module, metrics).

**Risk.** Low–Medium after dependent refactors.

### 5.4 `atlas_task_tool.py` in execution/

**What we see.** [`agent_runtime/execution/atlas_task_tool.py`](../../src/agent_runtime/execution/atlas_task_tool.py) — "supervisor task → subagent trace linking" per [C5](08-execution-prompts.puml).

**Why it's a problem.** Couples C5 (execution) to C7 (delegation) and observability. The trace-linking concern belongs to one of those two clusters, not both.

**The change.** Move into `delegation/subagents/` (it's about subagent traces) or into `observability/` (it's about trace linking). Depending on what's in the file, one will fit better.

**Risk.** Low.

### 5.5 `agent_runtime/api/` mixes coordinator with domain services

**What we see.** [`api/`](../../src/agent_runtime/api/) holds both `RuntimeApiService` (coordinator) AND `DraftService` / `ShareService` / `WorkspaceFeedService` etc. (domain services). This is the C4/C5 split confusion.

**Why it's a problem.** "API" is presentation; "service" is domain. Mixing both in one package muddies the boundary that would otherwise be clean.

**The change.** Move domain services into `agent_runtime/services/` (or similar). Keep `api/` for the coordinator + ports + event production. After the [2.7 RuntimeApiService split](#27-runtimeapiservice-at-24k-loc), even the coordinator probably moves out of `api/`.

**Risk.** Low. File moves.

### 5.6 6 empty legacy directories under `agent_runtime/`

**What we see.** Per the architecture index Appendix A: `agent_runtime/agent/`, `mcp/`, `memory/`, `skills/`, `subagents/`, `tools/` are all empty (only `__pycache__`).

**Why it's a problem.** Search hits land in legacy paths first; new contributors get confused.

**The change.** `git rm -r` each one.

**Risk.** Trivial.

### 5.7 `dev_auth_bypass_allowed` toggle on `DeploymentProfile`

**What we see.** [`agent_runtime/deployment/profile.py`](../../src/agent_runtime/deployment/profile.py) lists `dev_auth_bypass_allowed` as one of its toggles.

**Why it's a problem.** The root [CLAUDE.md](../../../../CLAUDE.md) explicitly says "DEV_AUTH_BYPASS no longer exists." Stale toggle. (Hypothesis — verify.)

**The change.** Verify and delete if stale.

**Risk.** Trivial.

---

## Recommended refactor sequence

Ordered by ratio of impact to risk. Each row is independent; don't bundle.

| #   | Change                                                                                                                                                      | Concern           | Impact                                             | Risk                |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- | -------------------------------------------------- | ------------------- |
| 1   | Postgres `LISTEN/NOTIFY` for SSE bus ([4.1](#41-sse-delivery-is-1s-in-production))                                                                          | Latency           | ~1s → ~50ms SSE delivery                           | Medium              |
| 2   | Drop `create_agent_runtime` sequential bootstrap ([4.4](#44-sequential-bootstrap-in-create_agent_runtime))                                                  | Latency           | TTFB win on every run                              | Low                 |
| 3   | Drop or batch PresentationGenerator polish ([1.1](#11-presentationgenerator-polish-on-every-event))                                                         | Bespoke + Latency | ~50% background LLM cost gone, halves event volume | Low–Medium          |
| 4   | Combine append + set_latest, eliminate FOR UPDATE via per-run sequence ([4.3](#43-per-event-db-amplification))                                              | Latency           | Per-event DB ops 3 → 1                             | Medium              |
| 5   | Async-only ports; delete sync Protocols + async_wrappers + AsyncInMemoryRuntimeApiStore ([1.2](#12-sync-ports--async-ports--async_wrappers-3-layers-for-1)) | Bespoke           | Hundreds of LOC + a layer deleted                  | Medium              |
| 6   | Delete 6 empty legacy directories ([5.6](#56-6-empty-legacy-directories-under-agent_runtime))                                                               | Cleanup           | Trivial                                            | Trivial             |
| 7   | Switch `EncryptExistingColumns` to one-shot Alembic data migration ([1.8](#18-encryptexistingcolumns-running-as-a-perpetual-job))                           | Bespoke           | Removes a perpetual job                            | Low                 |
| 8   | Adopt Alembic ([1.7](#17-custom-migration-runner))                                                                                                          | Library           | Standard tooling                                   | Low                 |
| 9   | Batch citation ingestion ([4.5](#45-sequential-citation-ingestion))                                                                                         | Latency           | Wins on research-heavy turns                       | Low–Medium          |
| 10  | Replace pricing with LiteLLM source ([1.6](#16-custom-budget--pricing-system--seed-catalog))                                                                | Library           | Stops seed-catalog maintenance                     | Low                 |
| 11  | Consolidate citations 8 files → 3 ([2.2](#22-8-files-of-citation-infrastructure))                                                                           | Edge-case         | Code clarity                                       | Medium              |
| 12  | Move `DraftBackend` out of capabilities ([2.5](#25-draftbackend-in-capabilities))                                                                           | Edge-case         | Cleaner cluster boundaries                         | Low                 |
| 13  | Merge `Fork` services and `Workspace` services ([2.6](#26-service-splits-inside-c4-that-should-be-one-service-each))                                        | Edge-case         | Coordinator shrinks                                | Low                 |
| 14  | Pick one permission model ([2.3](#23-four-way-permission-model-3-specific--1-generic))                                                                      | Edge-case         | Code clarity                                       | Low–Medium          |
| 15  | Fix audit chain (SIEM-side or managed) ([1.3](#13-custom-hash-chained-audit-log))                                                                           | Library           | Removes fragile in-app integrity                   | Medium (compliance) |
| 16  | Replace redactor with Presidio + detect-secrets ([1.4](#14-custom-redactor))                                                                                | Library           | Mature pattern coverage                            | Medium              |
| 17  | Adopt OTel SDK + auto-instrumentation; thin observability/ ([3](#3-library-replacements))                                                                   | Library           | Standard observability story                       | Medium              |
| 18  | Verify LiteLLM streaming covers thinking/reasoning, then replace provider adapters ([3](#3-library-replacements))                                           | Library           | Provider stack shrinks                             | High                |
| 19  | LangGraph Checkpointer replaces CheckpointStorePort                                                                                                         | Library           | Single checkpoint story                            | Medium              |
| 20  | LangGraph human-in-the-loop interrupts replace approval lifecycle                                                                                           | Library           | Standard interrupt story                           | High                |
| 21  | Repository pattern collapses 9 ports + 17 record types ([2.1](#21-9-persistence-ports--17-record-types))                                                    | Edge-case         | Major code reduction                               | High                |
| 22  | `pg_partman` partitioning replaces retention sweep ([1.5](#15-custom-retention-sweep--5-level-policy-resolver))                                             | Bespoke           | DB-native TTL                                      | Medium–High         |
| 23  | Split `RuntimeApiService` ([2.7](#27-runtimeapiservice-at-24k-loc))                                                                                         | Edge-case         | Coordinator no longer god-class                    | High (do last)      |

**Rules.**

- Each item ships as its own PR.
- Each item lands with tests proving the named preserved behaviors still pass.
- No big-bang rewrites.
- After each item, re-run a representative latency benchmark to confirm the impact column.

---

## Behaviors that must be preserved

A refactor that drops any of these silently is a regression. Pin tests to each.

**Streaming and resume:**

- `RuntimeEventEnvelope` schema (every field) — clients depend on it.
- `sequence_no` is monotonic per `run_id`.
- SSE wire format: `event: <name>\nid: <seq>\ndata: <json>\n\n`.
- SSE resume: `?after_sequence=N` returns events with `sequence_no > N` exactly once.
- `follow=false` returns one synthetic HEARTBEAT envelope (`metadata.transient=true`) and closes.
- Terminal status (RUN_COMPLETED / RUN_FAILED / RUN_CANCELLED / RUN_REJECTED) auto-closes the SSE stream.
- `event_protocol_version=1` pinned.

**Cancellation:**

- Cancel goes through the queue as a separate `RUN_CANCEL_REQUESTED` command.
- Cancellation is cooperative (one extra MODEL_DELTA may arrive after cancel; documented behavior).
- `CANCELLED` is the terminal state of record.

**Approvals:**

- `AWAITING_APPROVAL` is a real run state.
- Approval row in persistence is the durable rendezvous (survives worker restart, SSE drop).
- StreamingExecutor short-circuits on `action_interrupt_events = {APPROVAL_REQUESTED, MCP_AUTH_REQUIRED}`.
- Resume happens via separate `APPROVAL_RESOLVED` command (not inline continuation).
- Multi-fire: token rotation mid-run fires the same approval cycle again.

**Citations:**

- Conversation-scoped ordinal namespace persists across turns AND subagents.
- Idempotency: `(tool_call_id)` for ordinals; `(run_id, connector, doc_id)` for citations.
- Sealed snapshot at FINAL_RESPONSE attached to `payload.citations`.
- Reconstruction from `CitationStorePort.list_for_run` on resume / crash recovery.
- Workspace Sources tab via `SourceStorePort.aggregate_for_conversation`.

**Memory:**

- 3 scopes (USER / AGENT / ORGANIZATION), tenant-isolated via `MemoryScope.for_*`.
- 3 actor roles (USER / ASSISTANT / APPLICATION).
- Path-policy default: only APPLICATION can write `/policies/*`.
- 4 compression strategies (INLINE / OFFLOAD / SUMMARIZE / FALLBACK_SUMMARY); OFFLOAD writes `ContextPayloadRecord`; both emit `COMPRESSION_NOTE` events.

**Pricing / billing:**

- Cost stamped at write time using active `ModelPricingRecord`.
- Pricing changes do NOT retroactively rewrite history.
- Integer micro-USD with banker's rounding.
- `BudgetCharger.charge_run` is CAS-based and idempotent.
- Reasoning tokens billed via separate column when provider differentiates.

**MCP:**

- ai-backend is read-only on the MCP registry; backend owns mutation.
- ai-backend re-queries the registry on every `create_agent_runtime` (no cache).
- Three install paths (JSON / catalog / custom) all converge on the same backend `ServerRecord`.
- Permission gate fires twice per call (visibility + call-time defense in depth).
- Auth state {none, pending, valid, error} is part of the card.

**Concurrency:**

- Per-run write serialization via `UNIQUE(run_id, sequence_no)` (and currently `SELECT FOR UPDATE` — the constraint is what matters; the lock is the implementation).
- `set_run_latest_sequence` never rewinds.
- Worker concurrency bounded by `settings.execution.max_parallel_runs`.

**Observability / compliance:**

- `RuntimeEventEnvelope.payload` and `.metadata` redaction enforced at field-validation time.
- `visibility` defaults to USER; INTERNAL and AUDIT are honored.
- `redaction_state` defaults to REDACTED.

**Identity / auth:**

- All inbound calls authenticated via `RuntimeServiceAuthenticator` (no `DEV_AUTH_BYPASS` shortcut path).
- Caller-supplied identity / role / scope / tenant treated as untrusted unless derived from a verified session / token.

---

## Open questions to resolve before refactoring

- **LiteLLM streaming coverage for reasoning / thinking modes.** Anthropic `thinking_mode {ENABLED, ADAPTIVE}` + `display {OMITTED, SUMMARIZED}`, OpenAI Responses API summary, Gemini grounding. Each must be verified independently before the provider-adapter replacement.
- **Bus cross-process behavior.** Confirm in code that `RuntimeEventBus` is truly process-local and that `notify_sync` from a separate worker process never reaches the API. (Hypothesis based on file location + the 2-sec poll fallback.)
- **`/v1/agent/conversations/{id}/context` call frequency from the frontend.** Determines whether [4.9](#49-conversationcontextbuilder-per-context-query) needs caching.
- **Multi-tool parallel execution in LangGraph builder.** Verify whether parallel tool-call execution is enabled in `StreamingExecutor` / Deep Agents. ([4.7](#47-multi-tool-parallel-execution-verify))
- **`McpDiscoveryService` vs `SuggestibleConnectorsResolver` overlap.** Per [f7](f7-mcp-add.puml) they have different consumers (in-chat suggest tool vs system-prompt hints). Verify before merging.
- **`ToolBudgetGuard` reuse outside middleware.** If reused, keep separate ([2.4](#24-toolbudgetmiddleware--toolbudgetguard-two-step)).
- **Audit hash chain compliance requirement.** Does any signed buyer contract specifically require in-app integrity? Determines whether [1.3](#13-custom-hash-chained-audit-log) is safe to delete.
- **`dev_auth_bypass_allowed` toggle.** Confirm stale ([5.7](#57-dev_auth_bypass_allowed-toggle-on-deploymentprofile)).
- **Background-job scheduler shape.** Verify that `ApprovalExpirySweeper` / `RetentionSweeperLoop` / `UsageRollupLoop` run as independent asyncio tasks rather than serially-scheduled iterations ([4.12](#412-background-jobs-in-the-worker)).
- **`PresentationGenerator.summary` consumption on the frontend.** Determines whether [1.1](#11-presentationgenerator-polish-on-every-event) is a delete or a keep-as-template.

---

## Cost / value summary

A rough back-of-envelope for the top three changes:

- **Bus + `LISTEN/NOTIFY`:** 1–2 days. Drops p50 SSE latency from ~1s to ~50ms. No API change. Single most user-visible win.
- **Drop / batch presentation polish:** 1–3 days depending on path. Removes ~50% of background LLM calls per turn. Halves event-store volume on visible turns. Direct cost saving.
- **Async-only ports:** 1 week. Deletes a Protocol family + a bridge file + a fake adapter. Reduces friction for every persistence change going forward.

Anything in items 1–9 of [Recommended sequencing](#recommended-refactor-sequence) is achievable within a quarter without risk to behavior. Items 18–23 are major and should be planned as their own initiatives with explicit migration windows.

---

_This audit reflects the documented design as of the diagrams in this folder. It will go stale as the code evolves; update or rewrite when the next round of changes lands._
