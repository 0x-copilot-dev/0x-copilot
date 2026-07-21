---
id: findings-dead-code
kind: report
title: Dead code — shipped-but-unreachable surface to delete or park
audit_date: 2026-07-20
---

# Dead Code

Production-unreachable code (only its own tests, `__init__` re-exports, or docstrings reference it). Deleting or parking it removes the single largest LOC mass in the repo and stops it from masquerading as "capability exists" in compliance/refactor reviews. Estimated combined removable surface: **~95k LOC** (dominated by the two front-end fold subtrees).

Ordering: by removable LOC / risk-of-misleading-a-reader.

---

## Frontend / chat-surface (the IA-fold graveyard)

### DEAD-1. Folded/unmounted destination families — ~50k LOC across two packages
**Severity/confidence:** high/high · **Verification:** confirmed (3 auditors) · **Clusters:** frontend-web, chat-surface-destinations, chat-surface-core
**Merged sources:** frontend-web F1, chat-surface-destinations F1 + F4, chat-surface-core F5 + F6, flow-contracts F1.

The PR-4.11 IA fold left two parallel dead subtrees, both exported/tested/documented as if live:
- **`apps/frontend/src/features/{home,library,inbox,todos,routines,agents,memory,tools}`** + their `*Api.ts` modules + `_*-stub.ts` + ~3.8k LOC of tests — **~17.8k LOC**, zero non-test importers (`App.tsx:24-28` defers them to a "Phase-6C sweep").
- **`packages/chat-surface/src/destinations/{home,inbox,todos,agents,library,memory,routines,team,tools}`** (nine families incl. the old MCP `tools/` 4-wizard onboarding) — **~32k LOC**, mounted by neither host; `index.ts:463-1236` still exports every one.
- Finished-but-never-adopted shared components while web keeps its scaffold: `WebhookCreateWizard` (831), `ConnectorDetailView`+tabs (~1,350), `TemplateGallery` (466), `WebhookSecurityPage` (321), `ChatsSidebar`/`ChatsDestination` (546), `ProjectEditor`/`TemplateEditor`/dialogs.
- chat-surface-core orphans: `shell/RightRailTabs.tsx` (414, unexported, `RightRail.tsx:17-21` says it deliberately isn't used); `routing/route-table.ts` `ROUTE_TABLE` (stub components, no consumer); deprecated `registerSurface`/`resolveSurface`/`clearRegistry` surface path.

**Evidence:** apps/frontend/src/app/App.tsx:24-28; apps/frontend/src/api/_library-stub.ts:18; packages/chat-surface/src/index.ts:463-1236; packages/chat-surface/src/destinations/*; packages/chat-surface/src/shell/RightRailTabs.tsx; packages/chat-surface/src/routing/route-table.ts.
**Remediation:** Execute the sweep now — delete the eight web feature dirs + their API modules + tests, and either delete or move to an explicit `archive/` (outside the barrel) the nine dead chat-surface families + orphan components; regenerate the barrel around the live six destinations and pin it with an export-surface snapshot test. Keep `_projects-stub` until api-types absorbs it (see SSOT-2).
**Payoff:** ~50k LOC removed; barrel roughly halves; CI stops running ~14k LOC of tests for unmounted UI.

### DEAD-11. chat-surface `eventProjector` output is mostly unconsumed (~300 LOC) + unreachable error paths
**Severity/confidence:** high/high · **Verification:** confirmed · **Clusters:** chat-surface-core, chat-surface-destinations
**Merged sources:** chat-surface-core F2, chat-surface-destinations F6 (`toError`).

`ThreadCanvas` reads only `projection.surface` + `projection.timeline.beads`; the projector's `chat`/`activity`/`approvals`/`swimlanes` slices have zero non-test consumers (TcChat fetches its own messages, TcSwimlanes streams itself — see DUP-9/RISK-run). `useRunSession.resolveError` can never become non-null (the run-list catch discards it) so its resolution-error branch and `toError` (`useRunSession.ts:456`) are unreachable.
**Evidence:** packages/chat-surface/src/thread-canvas/useEventProjector.ts:59-83; ThreadCanvas.tsx:156-296; destinations/run/useRunSession.ts:183-195,456-461.
**Remediation:** Shrink the projector to `beads`+`surface` and delete the rest, or wire the slices (couples to DUP-9). The dead approval slice hides a latent `reject`/`rejected` bug (RISK-approval-projection) — delete removes the booby trap.
**Payoff:** ~300 LOC + eliminates a latent-bug landmine.

### DEAD-13. Run cockpit calls a `GET /v1/agent/runs?conversation_id=` endpoint that does not exist
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** chat-surface-destinations (flow-run-streaming F6)
`useRunSession` fires this on every mount; the facade and runtime_api register only `POST /runs` + per-id GETs, so the request always 405s into the catch. ~120 LOC of tolerant parsing (`parseRunList`, `pickActiveRunId`, `runListArray`, `parseRunListItem`) and the entire `RunMultiSelect` path can never populate against the real stack.
**Evidence:** packages/chat-surface/src/destinations/run/useRunSession.ts:164-204,357-454; services/backend-facade/src/backend_facade/app.py:886.
**Remediation:** Add the list endpoint (it is clearly intended) or delete the resolution path + parsers. Couples to LIB-parsers.
**Payoff:** ~120 LOC or a real feature unblocked.

---

## ai-backend (build-ahead machinery)

### DEAD-2. Capabilities dead subsystems — ~3,100 LOC (four code paths)
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** ai-runtime-capabilities (flow-mcp F7)
**Merged sources:** ai-runtime-capabilities F1-F4, flow-mcp F7.
Four production-dead subsystems, all fully tested (which disguises the deadness):
- `render_adapter_generator/` (~1,443 LOC) — only consumer is a comment at `runtime_api/schemas/common.py:181`; its sole contract dep `copilot_service_contracts.adapter_allowlist` is likewise test-only.
- Dynamic tool-loading: `tools/registry.py`, `tools/loader.py`, `tools/builtin/load_tool.py`, `tools/runtime_gate.py` (~520 LOC) — worker roots at `WebSearchToolRegistry`; the `handlers/run.py:784` `display_for` probe never hits `DynamicToolRegistry`.
- In-process AST-gated `tools/code_sandbox.py` + `tools/code_tool_adapter.py` (~690 LOC) — superseded by `interpreter/` (Monty) and `sandbox/` (remote).
- `mcp/files.py` "MCP-as-files" (`FileMcpConfigStore`/`FileMcpServerProvider`/`SecretShapeScanner`, 459 LOC) — exported from `mcp/__init__`, constructed nowhere.
**Evidence:** services/ai-backend/src/agent_runtime/capabilities/render_adapter_generator/; tools/{registry,loader,runtime_gate,code_sandbox,code_tool_adapter}.py; tools/builtin/load_tool.py; mcp/files.py.
**Remediation:** Delete all four + their tests (keep the live `interpreter/`+`sandbox/` execution paths and the live `tools/{cards,permissions,privacy}.py` contracts). If the surface-renderers roadmap needs the generator, record that in a spec, not shipped code. Also drop the now-orphan `adapter_allowlist` contract if F1 goes.
**Payoff:** ~3,100 LOC + removes CI time on unreachable code.

### DEAD-3. Worker `jobs/` — ~3.2k LOC wired to no entrypoint, plus an API served over an unfilled table
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** ai-runtime-worker (flow-data F2)
`RoutineSchedulerLoop` (+`routine_pre_fire_gate`), `TodoExtractor`, `ProposalExtractor`, `TodoRecurrenceMaterializerLoop`, `ApprovalExpirySweeper` are instantiated only by tests; `__main__.amain` starts only rollup/retention/db-metrics/pricing. Consequences: `runtime_api/app.py:222` serves the todo-extraction accept/reject API over a table nothing populates, and **approvals never expire** because the sweeper never runs. Related dead hook: `RetentionPolicyResolver.privacy_user_retention_days` has zero callers, so a user's "auto-delete after N days" does nothing (flow-data F2).
**Evidence:** services/ai-backend/src/runtime_worker/__main__.py:91-178; jobs/routine_scheduler.py, todo_extractor.py, proposal_extractor.py, todo_recurrence_materializer.py, approval_expiry_sweeper.py; runtime_api/app.py:222; agent_runtime/retention/policy_resolver.py:59,89.
**Remediation:** Either wire each behind its existing `*_ENABLED` gate in `amain` (they were designed for it) or park them out of the shipping image; add a boot-time log of started/skipped loops. Wiring the approval-expiry sweeper is also a compliance fix.
**Payoff:** ~3.2k LOC parked or a real feature set enabled.

### DEAD-4. Execution dead surface — async subagent lifecycle (~850 LOC) + ConfiguredRuntimeGraph + feature_flags + helpers
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** ai-runtime-execution
**Merged sources:** ai-runtime-execution F2, F7.
Real delegation is the in-process deepagents `task` tool; the async lifecycle (`runner.py` 473 LOC, `handoff.py` 79 LOC, `AsyncTask*`/`SubagentTask`/`SubagentResult` contracts ~300 LOC) is imported only by `delegation/subagents/__init__.py` + tests — and carries the *most elaborate* tests in the area. Also dead: `ConfiguredRuntimeGraph` (`graph.py:48`), `FeatureFlag`/`AgentRuntimeContext.feature_flags` (round-trips through records but gates nothing), `_normalize_scope`/`_coerce_iterable` (`contracts.py:742-756`).
**Evidence:** services/ai-backend/src/agent_runtime/delegation/subagents/{runner,handoff,contracts}.py; execution/graph.py:48-117; execution/contracts.py:334,742-756.
**Remediation:** Delete runner/handoff/AsyncTask contracts + their tests (keep `SubagentDefinition`/`DynamicSubagentCatalog`/`FilesystemPermissionSpec`); delete `ConfiguredRuntimeGraph` + unused helpers; either wire `feature_flags` into capability gating or drop the field.
**Payoff:** ~1,000+ LOC.

### DEAD-6. Persistence dead surface — file-store product APIs (~1.5-2k LOC) + unwired adapters
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** ai-runtime-persistence
**Merged sources:** ai-runtime-persistence F6, F7.
Built + tested but mounted nowhere: `export_conversation`/`import_conversation` (+`export_import.py`, 569 LOC), `search_conversations` FTS (+`search.py` + FTS tables), `store_health`/`conversation_health`/`needs_repair_ids`, `verify_audit_log` — no route, no desktop IPC, only unit tests (search + export are user-visible features left dark). Also dead: `InMemory/PostgresTodoExtractionStore` (unwired — its only consumer `todo_extractor.py:148` is itself dead per DEAD-3), `InMemoryShareSnapshotStore` (test-only), `PERSISTENCE_TABLE_RECORDS` (zero consumers), `schema/postgres.py` legacy shim (test-only).
**Evidence:** services/ai-backend/src/runtime_adapters/file/{runtime_api_store.py:858-1814,export_import.py,search.py,_health.py}; runtime_adapters/{postgres,in_memory}/todo_extraction_store.py, share_snapshot_store.py; agent_runtime/persistence/records/__init__.py:94; persistence/schema/postgres.py.
**Remediation:** Mount search+export through runtime-API routes for the desktop shell, or explicitly stage them with a tracking issue; move share-snapshot store to test fixtures; delete `PERSISTENCE_TABLE_RECORDS` + the schema shim.
**Payoff:** ~2k LOC removed or two shipped features.

### DEAD-7. 10 of 19 initial `runtime_persistence` tables have no writer; `runtime_checkpoints` never written on Postgres
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** ai-runtime-persistence (flow-data F3/F4)
`runtime_checkpoints, runtime_context_payloads, runtime_compression_events, runtime_capability_snapshots, runtime_async_tasks, runtime_subagent_results, runtime_memory_items, runtime_memory_scopes, runtime_tool_invocations, runtime_legal_holds` have zero `INSERT` in `services/ai-backend/src` (the live model folds subagent state *from* `runtime_events`). The retention sweeper diligently sweeps three always-empty tables; C7 encryption columns were planned for five; `list_retention_orgs` UNIONs over them. The schema materially misrepresents what is persisted. (`runtime_legal_holds` = RISK-legal-hold; `runtime_checkpoints` on Postgres = RISK-checkpoints.)
**Evidence:** services/ai-backend/src/agent_runtime/persistence/schema/postgres.py:19-39; runtime_adapters/postgres/runtime_api_store.py:3580.
**Remediation:** Drop the dead tables in a migration, or write the adapters intended to fill them; decide checkpoints (RISK-checkpoints) and legal-hold (RISK-legal-hold) first since those are behavior gaps, not just schema.
**Payoff:** schema truthful; removes sweeper work over empty tables.

### DEAD-10. `inbox_producer.py` / `inbox_fallback.py` have no production consumers
**Severity/confidence:** medium/medium · **Verification:** confirmed · **Cluster:** ai-runtime-api
`HttpInboxProducer`/`InboxProducerFactory` (352 LOC) + `InboxFallbackScheduler`/presence/tenant-settings ports (316 LOC) constructed nowhere in src (worker uses a separate mirrored `RoutineInboxProducerPort`).
**Evidence:** services/ai-backend/src/agent_runtime/api/inbox_producer.py:336; inbox_fallback.py:159.
**Remediation:** Wire the fallback scheduler into the approval flow it was written for, or delete both until the backend inbox-items lane ships.
**Payoff:** ~668 LOC.

### DEAD-15. Tracked junk in `services/ai-backend/` root
**Severity/confidence:** low/high · **Verification:** accepted · **Cluster:** ai-runtime-api
`Oops.rej` (leftover patch-reject), `.coverage` (53 KB binary), duplicate `env_example` + `.env.example`.
**Remediation:** Delete the first two, keep one env template, add both patterns to `.gitignore`.

### DEAD-17. Guards that can never fire
**Severity/confidence:** low/high · **Verification:** accepted · **Cluster:** ai-runtime-worker
`DefaultRuntimeDependenciesFactory._validate_capability_mode` (`dependencies.py:264-281`) — `WebSearchToolRegistry` always returns a 1-tuple so the "no capability sources" guard is unreachable.
**Remediation:** Check configured external sources (MCP/skills URLs), or delete the guard.

---

## backend

### DEAD-8. backend-product dead modules — installs (1,058), palette read model, dispatcher gate, rotation worker
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** backend-product
**Merged sources:** backend-product F4, F5, F6, F13.
- `agents/installs.py` (1,058 LOC — installs/overrides/fork + own store & routes): `register_agent_install_routes` has no caller; facade proxies only CRUD.
- Palette read model has no writers: `PaletteRefreshDispatcher` is stashed on `app.state` but no destination calls `upsert_entry`/`delete_entry`, so `GET /v1/palette/search` is always empty while the FE wires ⌘K to it.
- `notifications/dispatcher_gate.py` (260 LOC) — only its own test imports it.
- `WebhookRotationWorker` — no launcher anywhere (90-day rotations never fire).
**Evidence:** services/backend/src/backend_app/agents/installs.py:652; palette/refresh.py:38; notifications/dispatcher_gate.py; webhooks/rotation_worker.py.
**Remediation:** Register installs + facade proxies (it looks finished) or move it out of the tree; add palette dispatcher calls at destination create/update/delete boundaries (or federate live queries); delete dispatcher_gate; start the rotation worker from the app lifespan like `SessionSweeper`.
**Payoff:** ~1.3k+ LOC removed, or two user-visible features (⌘K palette, agent installs) unblocked.

### DEAD-9. backend-platform — `LibraryIndexerLoop` never started; `backend_app/migrations.py` zero importers
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** backend-platform
`LibraryService` enqueues `library_index_jobs` rows and Memory rides the same queue, but no composition root starts `LibraryIndexerLoop` — search silently degrades to BM25-only in every deployment. `backend_app/migrations.py` (SQL-constants shim) has no importers; also unused: `TelemetryBootstrap.instrument_httpx_clients`/`instrument_psycopg`, and `HttpOAuthTokenExchanger` (a rename shim whose override just calls `super()`).
**Evidence:** services/backend/src/backend_app/jobs/library_indexer.py:262; app.py:303 (lifespan starts only SessionSweeper); backend_app/migrations.py; service.py:171-187.
**Remediation:** Start the indexer from a composition root gated on `LIBRARY_INDEXER_ENABLED` + a boot test; delete the migrations shim and the rename shim; call the instrumentors at boot or remove them.
**Payoff:** ~150 LOC + working Library/Memory vector search.

### DEAD-12. backend-facade — shadowed admin routes, legacy `/v1/session`, unregistered readiness
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** backend-facade
**Merged sources:** backend-facade F2, F8, F12.
`/v1/admin/adapter_registry/candidates*` is registered by *both* `adapter_registry_routes.py` (static auth) and `adapter_review_routes.py` (the **stronger** `verify_with_touch` auth) — registration order makes the review module unreachable, so the weaker-auth copy wins silently and `test_adapter_review_routes.py` tests the wrong handlers. Legacy `GET /v1/session` has no consumer; `.env.example` documents dead `FACADE_DEV_ORG_ID/USER_ID`; `register_health_routes` gets no checkers so `/readyz` == liveness.
**Evidence:** services/backend-facade/src/backend_facade/{app.py:132-133,224-234,1317,adapter_registry_routes.py:79-131,adapter_review_routes.py:56-93}.
**Remediation:** Delete the admin trio from `adapter_registry_routes.py`, keep the touch-authenticated review module, add a duplicate-`app.routes` guard test; remove legacy `/v1/session` + stale env; register store/event-bus readiness checkers.
**Payoff:** removes a silent auth-downgrade + dead surface.

### DEAD-18. `settings/schema.sql` duplicates migration `0033` and is unread
**Severity/confidence:** low/high · **Verification:** accepted · **Cluster:** backend-identity
Two hand-maintained copies of the same DDL; the schema.sql copy even `CREATE POLICY`s a table it never enables RLS on.
**Remediation:** Delete `settings/schema.sql` (or reduce to a comment pointing at the migration).

---

## desktop + distribution

### DEAD-5. Desktop AC8 browser subsystem (~3.4k LOC) + tier-2 dormant modules + unused channel
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** desktop-app
**Merged sources:** desktop-app F1, F5, F8.
The entire `main/browser/*` agentic-browser subsystem (~3.4k LOC, 16 modules) is imported by nothing in main; no esbuild task emits its worker bundle; the supervisor never sets `DESKTOP_BROWSER_BROKER_URL/TOKEN` the ai-backend provider needs — yet `main/browser/index.ts:3-5` claims main builds it. Tier-2 `main/adapters/{harvest,download,loader,opt-out}.ts` (~660 LOC) have no non-test consumers and the install path runs on `StubLifecycleEventSource`. `auth.get-posture` IPC channel + the deep-link app-login branch are registered but never invoked.
**Evidence:** apps/desktop/main/browser/index.ts; esbuild.config.mjs:10-53; apps/desktop/main/adapters/{harvest,download,loader,opt-out}.ts; main/ipc/handlers.ts:215.
**Remediation:** Land the AC8 wiring slice (build task + main gate + env delivery) or move the subtree to an `incubating/` area and fix the docstring; park the four tier-2 modules until the registry backend exists; consume posture in `SignInGate` or drop the channel.
**Payoff:** ~4k LOC parked; removes a false "the desktop has an egress-policied browser" claim in compliance reviews.

### DEAD-16. CLI small dead surface
**Severity/confidence:** low/high · **Verification:** accepted · **Cluster:** desktop-distribution (flow-desktop-boot F8)
`needsStage` exported but uncalled (logic inlined); unused `existsSync` import; `repair --yes` flag never used.
**Remediation:** Delete the export + import; scope `--yes` to `uninstall` in help text.

---

## shared-packages

### DEAD-14. Unreferenced fonts + phantom runtime dep
**Severity/confidence:** low/high · **Verification:** accepted · **Cluster:** shared-packages
Six `.woff2` files (~123 KB) in design-system with no `@font-face` (styles.css:8-9 admits it); surface-renderers declares `@0x-copilot/chat-transport` as a runtime `dependency` whose only import is a lint-negative fixture.
**Remediation:** Delete the six fonts; move chat-transport to `devDependencies`.

---

## docs

### DEAD-19. `docs/decomp/` corpus is half-written and its indexes overstate coverage
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** docs-corpus
`docs/decomp/README.md` claims a 92-file inventory and names two companion deliverables that don't exist (`docs/refactor/`, `replacement-analysis.md`); the `_index.md` files link ~20 per-file docs never written. A consumer following the index hits missing files more often than not.
**Evidence:** docs/decomp/README.md; docs/decomp/*/_index.md.
**Remediation:** Mark missing docs "not yet written" (or write them); delete the refactor/replacement-analysis references.

> **Re-verify at HEAD (docs-corpus F7, PARTIAL):** the 2026-05-06 salvage list is stale for ≥2 items — `InboxEventEnvelopeSchema` **is** now used (`sse/inbox_adapter.py:17,72`) and `ToolBudgetMiddleware.check_admit` **is** wired (`handlers/run.py:1429`, `capability_tool_wiring.py:64`). Do not action those as dead. `register_health_routes(app)` still passes no checkers (readyz-always-ready holds — see DEAD-12 / risks).
