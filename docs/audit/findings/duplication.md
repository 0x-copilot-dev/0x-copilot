---
id: findings-duplication
kind: report
title: Duplication — copy-pasted logic to DRY out
audit_date: 2026-07-20
---

# Duplication

Byte-identical or structurally-identical logic maintained in N places by hand. These are the DRY targets: each is a drift vector where a fix in one copy silently misses the others. (SSOT-string/enum duplication lives in `ssot-violations.md`; library-replaceable bespoke code in `replace-with-libraries.md`.)

Ordering: by LOC saved / drift blast-radius.

---

## Client-side run-stream reduction

### DUP-1. Three parallel run-stream projection pipelines + triplicated reducers
**Severity/confidence:** high/high · **Verification:** confirmed (3 auditors) · **Clusters:** flow-run-streaming, chat-surface-core, chat-surface-destinations
**Merged sources:** flow-run-streaming F5, chat-surface-core F3, chat-surface-destinations F5.
The same `RuntimeEventEnvelope[]` stream is reduced three times: (1) legacy web `ChatScreen` + `agentApi.streamRunEvents` + the ~20-module `chatModel/*` reducer family (own reconnect/backoff + background-run registry); (2) the chat-surface cockpit `useRunSession` + `eventProjector` + pure selectors; (3) `TcSwimlanes`' own subscription + `toBead`. Approval reduction alone exists 3× (`chatModel/approval.ts`, `eventProjector.nextApprovalState`, `approvalProjection.ts` — the last's header admits it "mirrors the host-owned approval reducer"); subagent reduction 2× (`subagentHelpers.ts:206` reproduces `chatModel/subagentStatus.ts` byte-for-byte); run-status-from-event-type 3× (backend `_status_for`, `useRunSession.runStatusFromEventType`, `chatModel/status.ts`). `workspace/types.ts` structurally re-declares host hook shapes. The citations family already proved convergence works (web host wraps the package `linkReducer`).
**Evidence:** apps/frontend/src/features/chat/chatModel/{approval,subagentReducer,status,citationLinkReducer}.ts; packages/chat-surface/src/destinations/run/{approvalProjection,useRunSession}.ts; thread-canvas/eventProjector.ts; subagents/subagentHelpers.ts.
**Remediation:** Make the chat-surface package selectors canonical; converge the legacy `ChatScreen` path onto `RunDestination`/the projector (both are already Transport/SSE port-shaped); turn the host reducers into thin wrappers and delete `subagentStatus.ts`/duplicate approval + status reducers. Also delete TcSwimlanes' second subscription (see DUP-9).
**Payoff:** the single biggest LOC + consistency win in the front end (thousands of LOC; removes web/desktop behaviour divergence on the two flagship surfaces).

### DUP-2. Web maintains a complete parallel Settings implementation
**Severity/confidence:** high/high · **Verification:** confirmed (3 auditors) · **Clusters:** frontend-web, chat-surface-destinations, chat-surface-core
**Merged sources:** frontend-web F2, chat-surface-destinations F3, chat-surface-core F8. (Also an SSOT violation — cross-listed there.)
`apps/frontend/src/features/settings/SettingsScreen.tsx` (1,405 LOC) + 17 section panels + `sections.ts` import **zero** symbols from chat-surface settings, while desktop mounts the full `SettingsSurface` + ten pages. BYOK, appearance, model-behavior, privacy, and notifications UIs are dual-maintained and already divergent, contradicting the root CLAUDE.md "both hosts mount the same Settings" SSOT and `settingsNav.ts:11-13`'s "replaces" claim. `aui-*` CSS is likewise duplicated: `composer.css` (123 refs) vs `apps/frontend/src/styles.css` (513 refs).
**Evidence:** apps/frontend/src/features/settings/; apps/desktop/renderer/SettingsMount.tsx:31-57; packages/chat-surface/src/settings/settingsNav.ts:11-13.
**Remediation:** Port the web Settings screen onto `SettingsSurface`+section bodies (desktop mount is the template) behind host data binders; make web import `composer.css`/`workspace.css` from the package and delete the styles.css copies.
**Payoff:** ~9k LOC de-duplicated; ends silent web/desktop settings drift.

### DUP-3. Destination projections hand-duplicated between web and desktop
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** desktop-app
`bucketConversations`/`chatStatus`/`toArchiveRow` and `mapRunStatus`/`auditLabel`/`buildMetaIndex`/`projectActivityRows` are byte-for-byte between `apps/desktop/renderer/destinationBinders.tsx` and `apps/frontend/src/features/{chats,activity}/api/*.ts`. These are pure functions over `@0x-copilot/api-types` shapes with zero substrate dependency — exactly what chat-surface exists to single-source; the desktop copies are also untested while the web ones are tested.
**Evidence:** apps/desktop/renderer/destinationBinders.tsx:125-298; apps/frontend/src/features/chats/api/chatsApi.ts:70-140; features/activity/api/activityApi.ts:56-147.
**Remediation:** Lift the projections into `packages/chat-surface` (or an api-types-adjacent module) next to their consuming components; both hosts import them.
**Payoff:** ~200 LOC + test coverage of the desktop path for free.

---

## backend Python

### DUP-4. Audit-append pattern re-implemented ~5× in `backend/store.py`
**Severity/confidence:** medium/high · **Verification:** confirmed (2 auditors) · **Clusters:** backend-core, backend-platform
**Merged sources:** backend-core F6, backend-platform F8.
`PostgresSkillStore.append_skill_audit` is a verbatim structural copy of `PostgresMcpStore.append_audit` (advisory-lock → head SELECT → sign → INSERT), differing only in table/column; `_sign_{mcp,skill,deploy}_audit` and the three `list_*_audit_events` filter bodies are copy-variants; `_json_list`/`_json_object`/`_datetime`/`_connect`/`_connect_or_inherit` are duplicated verbatim across both Postgres stores. `AuditChainSigner.from_env` is even rebuilt per append (see REF-signer).
**Evidence:** services/backend/src/backend_app/store.py:382-402,658-730,990-1012,1141-1215,1291-1312.
**Remediation:** Extract a `ChainedAuditTable(table, payload_fields)` writer + a Postgres-store base mixin used by all three streams.
**Payoff:** ~350-450 LOC.

### DUP-5. File adapter re-implements the in-memory adapter's business logic verbatim
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** ai-runtime-persistence
Across the ~90-method port surface the file store duplicates in-memory method bodies nearly line-for-line (`charge_budget` is logic-identical except `async with self._state_lock` + one ledger call). The three `runtime_api_store.py` total ~11.1k LOC; `base.py` shares only ~404. Every port change is now a triple hand-sync (quadruple with the SQL).
**Evidence:** services/ai-backend/src/runtime_adapters/in_memory/runtime_api_store.py:1835-1868 vs file/runtime_api_store.py:2473-2509; base.py.
**Remediation:** Make the file store *compose* the in-memory store (materialized view + a per-table-family journaling/write-through decorator), or extract pure state-transition logic into `base.py`-style shared classes. Couples to REF-god-port (splitting `PersistencePort`).
**Payoff:** plausibly ~1,500-2,000 LOC.

### DUP-6. Nine `env_float`/`env_int`/`env_bool` copies + seven hand-rolled periodic-loop lifecycles
**Severity/confidence:** medium/high · **Verification:** confirmed (2 auditors) · **Clusters:** ai-runtime-worker, backend-platform
**Merged sources:** ai-runtime-worker F6, backend-platform F9 (in-cluster env parsers).
`def env_float/env_int/env_bool` appears in exactly 9 ai-backend files (`usage_rollup_loop.py`, five `jobs/*`, `db_statement_metrics.py`, `refresh_loop.py`, `postgres/runtime_api_store.py`) plus a tenth `_positive_float/_positive_int` in `dependencies.py`; the backend re-implements `env_int/env_float` three more times in-cluster (`_BackendPoolEnv`, `SiemExportPumpEnv`, `LibraryIndexerEnv`). Seven `start/stop/_run` asyncio periodic loops each re-implement the same lifecycle.
**Evidence:** grep `def env_float` across services/ai-backend/src; services/backend/src/backend_app/{store.py:61,siem_export/pump.py:56,jobs/library_indexer.py:88}.
**Remediation:** One `EnvReader` + one `PeriodicLoop` base per service (cross-service sharing is blocked by the boundary rule; a `service-contracts`-hosted pure `EnvReader` could serve all three — see SSOT-profile-loader). Each loop shrinks to a `tick()`.
**Payoff:** ~200-300 LOC per service.

### DUP-7. Usage aggregation logic in 4+ near-identical copies
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** ai-runtime-api
`UsageApiRoutes._rows_by_day/_rows_by_model/_connector_rows_from_*` (routes.py), `UsageQueryService.rollup_*_rows` + `_RollupBucket`/`_ConnectorRollupBucket`, and `AgentUsageRoutes._aggregate` (which carries a "promote `_aggregate` to `UsageQueryService`" comment) are the same accumulate-into-defaultdict-bucket loop with different keys.
**Evidence:** services/ai-backend/src/runtime_api/http/routes.py:990-1204; agent_runtime/api/usage_service.py:95-296; runtime_api/http/agent_usage.py:106-149.
**Remediation:** One generic `bucket_by(key_fn, rows) -> UsageTotals` in `UsageQueryService`; token-kind column list becomes one edit site.
**Payoff:** ~250 LOC.

### DUP-8. MCP dispatcher re-implements the citation ordinal-hint block; `cite_mcp.py` is a pass-through alias
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** ai-runtime-capabilities
`mcp/middleware/call_tool.py:156-205` duplicates `citation_capturing_tool.py:139-181` (allocator lookup, `_CitationHint.append_to`, identical warn/info log shapes) and even imports the private `_CitationHint`; `mcp/middleware/cite_mcp.py` is a pure forward to `CitationProjector.project`.
**Evidence:** services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/{call_tool.py:13,156-205,cite_mcp.py}; citation_capturing_tool.py:139-181.
**Remediation:** Extract one `CitationAnnotator.annotate(result, *, connector, tool_name, tool_call_id)` used by both paths; delete the alias class.
**Payoff:** ~60 LOC + one place to fix hint bugs.

### DUP-16. Run vs approval handler duplicated flows held in sync by hand
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** ai-runtime-worker
`_workspace_snapshot_emitter`, `_dependencies_for_run`/`_dependencies_for_resume` (incl. duplicated `DraftBackend` construction), and the `DRAFT_UPDATED` payload are built independently in both handlers; `approval.py:311/335/343` reach into `RuntimeRunHandler` private classmethods. This is the exact drift class that already produced Bug R1 and the RISK-approval-resume side-effect gap.
**Evidence:** services/ai-backend/src/runtime_worker/handlers/run.py:889-1008 vs approval.py:471-809.
**Remediation:** A shared per-run wiring/completion module (same pattern as `FileStoreWorkerWiring`) consumed by both handlers — this is also the fix for the approval-resume completion gap (see RISK-approval-resume).
**Payoff:** ~150 LOC + closes a metering/billing/audit hole.

---

## facade / boundary-forced Python

### DUP-9. Facade per-module helper copy-paste + three forwarding kernels
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** backend-facade
`_raise_for_upstream` ×20 files, `_upstream_error_detail` ×12, `settings_for`/`_settings_for` ×25, `_safe_json` ×12, `_coerce_object_or_raise` ×15, the SSE pass-through closure ×~9, and three parallel forwarding kernels (`app.py` `forward_json`, the per-module `_forward_*` family, inline handlers) — several copies self-justify as "avoid circular import".
**Evidence:** grep `def _raise_for_upstream` across services/backend-facade/src/backend_facade/*.py; me_routes.py:428-429; adapter_registry_routes.py:19.
**Remediation:** One shared `backend_facade/proxy.py` leaf module (forward_json, forward_sse, error mapping, settings accessor); makes timeout/retry policy single-source and the auth-tier fix (RISK-facade-auth) a one-line policy.
**Payoff:** est. ~2.5-4k LOC (couples to REF-facade-app).

### DUP-10. Six near-identical SSE stacks (three with no producer)
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** backend-product
`inbox/`, `memory/`, `tools/`, `connectors/`, `home/`, `team/` `sse.py` (~2,438 LOC) each re-implement the same deque ring-buffer + `asyncio.Condition` bus + `LastEventIdResolver` + adapter loop (`inbox/sse.py:14` says it's "a copy of the home SSE discipline"). Worse, `tools`/`connectors`/`home` buses have **no producer** — those streams emit only heartbeats forever.
**Evidence:** services/backend/src/backend_app/{inbox,memory,tools,connectors,home,team}/sse.py; publisher census (`.publish(` only in inbox/memory/team).
**Remediation:** One generic `TenantChannelBus[EventT]` + SSE adapter + resolver parameterized by event name; then wire producers for tools/connectors/home or delete those streams.
**Payoff:** ~2k LOC + removes silently-dead streams the FE subscribes to.

### DUP-11. Two parallel dev-mint implementations + HMAC bearer codec 3×
**Severity/confidence:** medium/high · **Verification:** confirmed (2 auditors) · **Clusters:** backend-identity, flow-auth
**Merged sources:** backend-identity F4 + F5, flow-auth F3 + F4.
The compact HMAC bearer codec exists three times — `identity/sessions.py:88-141` (`_BearerCodec`), `dev_idp/_sign.py:26-38`, `backend_facade/auth.py:382-413,502-511` (the facade/backend split is boundary-forced, but **two copies inside `services/backend`** is a straight DRY violation; `dev_idp` even hand-types literal claim keys instead of the `CLAIM_*` constants). Separately, two dev-mint paths diverge: `POST /v1/dev/identity/mint` signs a session-less 365-day bearer (no `sid`, unrevocable) while `POST /internal/v1/auth/sessions/dev-mint` mints a real 24h session — the FE/desktop/Makefile all use the former, so all dev bearers are effectively eternal (compounds RISK-bearer-exp).
**Evidence:** services/backend/src/backend_app/identity/sessions.py:88-141; dev_idp/{_sign.py:26-38,routes.py:111-164}; services/backend-facade/src/backend_facade/auth.py:382-413.
**Remediation:** Promote the codec into `packages/service-contracts` (pure stdlib) or at minimum make `dev_idp` reuse `_BearerCodec` + `CLAIM_*` constants; fold the dev-IdP mint onto `SessionService.create` so dev bearers carry `sid` and ride revocation/expiry.
**Payoff:** ~80 LOC + closes an unrevocable-token hole.

### DUP-12. Frontend SIWE orchestration duplicated between `WalletSignIn` and `LoginScreen`
**Severity/confidence:** low/high · **Verification:** accepted · **Cluster:** frontend-web (flow-auth F9)
`LoginScreen`'s v2 card re-implements the nonce→build→`personal_sign`→verify→adoptSession ramp + hex-encoding helper instead of mounting `<WalletSignIn>` (the comment admits it mirrors the private helper); `WalletHandoffPage` correctly reuses `WalletSignIn`.
**Remediation:** Mount `<WalletSignIn>` from `LoginScreen`.

---

## other duplications

### DUP-13. `BackendMcpServiceAuth.headers` == `BackendSkillServiceAuth.headers`; token estimator twice
**Severity/confidence:** low/high · **Verification:** accepted · **Cluster:** ai-runtime-capabilities (F6, F7)
Byte-identical `ENTERPRISE_SERVICE_TOKEN` header builders in two files; the 4-chars/token, 100k-cap estimator implemented in both `tool_budget_guard.py` and `runtime_worker/capability_tool_wiring.py`.
**Remediation:** One `BackendServiceAuth` next to `BackendHttpPool`; export the estimator and reuse it.

### DUP-14. Two parallel RFC-5545 rrule evaluators (+ duplicated `action_interrupt_events` frozenset)
**Severity/confidence:** low/high · **Verification:** accepted · **Cluster:** ai-runtime-worker (F10; also backend-product F8 as cross-service SSOT)
`CronSpecEvaluator` (routine scheduler) and `RecurrenceRuleEvaluator` (todo recurrence) implement the same FREQ/BYDAY/INTERVAL subset with identical `_Weekday` tables; the same grammar is *also* re-implemented in `services/backend/todos/service.py` (cross-service — see SSOT-recurrence). Both are bespoke where `croniter`/`dateutil.rrule` would do (see LIB-schedule).
**Remediation:** One shared schedule-evaluator module (or a library) once the jobs are wired (DEAD-3 decides their fate first).

### DUP-15. Desktop boot implemented twice + branding/SERVICES facts hand-synced
**Severity/confidence:** high/high (boot) · **Verification:** confirmed (2 auditors) · **Clusters:** flow-desktop-boot, desktop-distribution
**Merged sources:** flow-desktop-boot F3 + F6, desktop-distribution F2 + F3 + F8.
`tools/desktop-runtime/run-local.mjs` re-implements free-port allocation, health polling, the create-DB psycopg snippet, and the full service env table that `apps/desktop/main/services/{ports,health,postgres,service-env}.ts` implement — and they have **materially drifted**: `initdb -U postgres -A trust` vs `-U atlas --auth=scram-sha-256`; DB names `backend`/`ai_backend` vs `atlas_backend`/`atlas_ai`; supervisor sets `SIWE_ORIGIN`/`FACADE_WEB_DIST_DIR`/`RUNTIME_EVENT_BUS_BACKEND` that run-local doesn't, so the smoke no longer proves wallet-page serving despite the README's "exactly the processes" claim. Separately, `assemble-payload.mjs` re-declares the SERVICES + SHARED_PACKAGES dir lists that `stage.mjs` owns ("Mirror stage.mjs SERVICES"), and branding constants (`com.0x-copilot.app`, "0xCopilot", platform keys, `isSafeTarget`, `APP_VERSION`) are hand-synced across 5+ files with "keep in sync" comments.
**Evidence:** tools/desktop-runtime/run-local.mjs:371-442 vs apps/desktop/main/services/service-env.ts:143-248; tools/cli/scripts/assemble-payload.mjs:26-34 vs tools/desktop-runtime/stage.mjs:56-77; apps/desktop/main/branding.ts:21-26.
**Remediation:** Extract one shared boot-contract module (env table + init params) both sides read, or drive `run-local` from the compiled supervisor; export a `tools/desktop-runtime/staging-spec.mjs` for the SERVICES/packages tables; hoist branding constants + `isSafeTarget` into `tools/cli/lib/paths.mjs`. Add the wallet env + a `GET /wallet.html` smoke assertion to `run-local`.
**Payoff:** removes the single most consequential boot drift (the smoke that is supposed to catch production-down bugs no longer does).
