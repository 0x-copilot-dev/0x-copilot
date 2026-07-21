---
id: findings-refactor-simplify
kind: report
title: Refactor & simplify — god-modules, overlapping machinery, complexity
audit_date: 2026-07-20
---

# Refactor & Simplify

Structural complexity that isn't a bug but taxes every future change: god functions/modules, overlapping abstractions, and split-brain interfaces. (Pure duplication is in `duplication.md`; this file is the "one unit is too big / the abstraction is wrong" set.) Type = refactor + complexity + efficiency.

Ordering: by how often the unit is edited / how much it blocks.

---

## God composition roots (each edited by every feature)

### REF-1. `backend/create_app` — ~1,620-line, ~60-70-parameter mega-factory
**Severity/confidence:** high/high · **Verification:** confirmed (2 auditors) · **Clusters:** backend-core, backend-platform, backend-product
**Merged sources:** backend-core F4, backend-platform F4, backend-product F11.
`create_app` (`app.py:409-2030`) wires ~30 subsystems inline with ~60-70 kwargs, 13+ mid-function imports to dodge cycles, an inline `_RoutineProjectAllowlistBridge` monkey-patched onto `routines_service._project_allowlist_lookup`, and repeated `# noqa: SLF001` reaches into `projects_service._membership_port` (6×) and `session_service._auth_secret`. Registration order is load-bearing (SSE-before-service, webhooks-before-connector). Every new destination edits this one function; it cannot be unit-tested in isolation.
**Evidence:** services/backend/src/backend_app/app.py:409-2030,679,1808,1822.
**Remediation:** Split into per-domain wiring modules (`wiring/{identity,destinations,mcp,...}.py`) composed by a thin `create_app`; expose public `membership_port`/`auth_secret` accessors + a public setter for the allowlist bridge; use `APIRouter` with explicit ordering to remove the order-comment hazard.
**Payoff:** the service's hottest file becomes reviewable + testable; removes the private-attr coupling that breaks silently on refactor (backend-core F11).

### REF-2. `backend-facade/app.py` — 1,505-line monolith (bootstrap + ~68 near-identical handlers)
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** backend-facade (F10)
Bootstrap mixed with the entire agent/MCP/usage surface; each handler is 8-15 lines of identical `authenticate → forward_json(...)`; `adapter_registry_routes.py:19` does a function-local `from backend_facade.app import forward_json` to dodge the resulting circular import.
**Evidence:** services/backend-facade/src/backend_facade/app.py:236-1315.
**Remediation:** A declarative route table (method, path, target, param/body policy) driving one generic handler over the shared `proxy.py` kernel (DUP-9); keep genuinely special handlers (skills merge, SSE, telemetry relay) hand-written. This also makes the auth-tier fix (RISK-facade-auth) a one-line policy change instead of a 65-handler edit.
**Payoff:** collapses most of the file; single-sources timeout/retry/auth policy.

### REF-3. `runtime_api/http/routes.py` — 1,777 LOC, five router factories with inline business logic
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** ai-runtime-api (F7)
`UsageApiRoutes` does persistence-port orchestration + cold-start rollup synthesis inline; `BudgetApiRoutes` constructs records against the port directly — both bypass the coordinator/service pattern every other surface uses.
**Evidence:** services/ai-backend/src/runtime_api/http/routes.py:689-946,1481-1608.
**Remediation:** Split usage + budget into their own modules (matching `retention_routes.py`); push fallback/rollup orchestration into `UsageQueryService` (also collapses DUP-7). `routes.py` shrinks to the `/v1/agent` router + internal routes.
**Payoff:** removes ~250 LOC of duplicated aggregation and restores the service-layer pattern.

## God data-modules / interfaces

### REF-4. `backend/contracts.py` — 2,571-line god-module, ~1,600 lines are the identity domain imported back by the identity package
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** backend-core (F5)
Lines ~940-2560 are Organization/User/Role/OIDC/Password/Lockout/MFA/SAML/SCIM/SIWE records — not core MCP/skill/deploy contracts. 27 `backend_app.identity.*` modules import their *own* domain models back out of core; 63-64 modules total depend on the file — one edit hotspot and a layering inversion (the identity package doesn't own its own contracts).
**Evidence:** services/backend/src/backend_app/contracts.py:940-2560.
**Remediation:** Move identity records into `backend_app/identity/contracts.py`; keep core contracts to MCP/skills/deploy/tamper-chain.
**Payoff:** removes the single largest import hotspot; the identity package becomes self-contained.

### REF-5. `PersistencePort` — ~79/90-method structural-Protocol god interface (drift = runtime 500)
**Severity/confidence:** high/high · **Verification:** confirmed · **Cluster:** ai-runtime-persistence (F4)
One `@runtime_checkable` Protocol spans conversations/messages/runs/approvals/audit/usage/pricing/budgets/retention. Because it is structural, incomplete implementations boot cleanly — which is exactly how Postgres shipped missing three approval methods (RISK-postgres-approval, a live 500). The satellite `persistence/ports.py` already demonstrates the right 7-role granularity.
**Evidence:** services/ai-backend/src/agent_runtime/api/ports.py:85-928.
**Remediation:** Split into cohesive role ports (`ConversationStore`, `ApprovalStore`, `UsageStore`, `BudgetStore`, `RetentionStore`…), compose in `RuntimePorts`, and enforce per-port conformance in a test (a simple `inspect` check catches missing methods without a DB). This is the precondition that makes DUP-5 (file/in-memory collapse) tractable.
**Payoff:** turns a class of runtime-500 drift into an import-time/test error.

### REF-6. `api-types/src/index.ts` — 3,978-line barrel monolith beside file-per-domain modules
**Severity/confidence:** low/high · **Verification:** accepted · **Cluster:** shared-packages (F11)
MCP/runs/events/approvals/usage/auth/MFA/audit/settings all sit in the barrel while newer contracts correctly split per-domain; the barrel is where the untested drift-prone contracts concentrate (SSOT-1).
**Remediation:** Mechanically extract the barrel's domains into modules (mcp.ts, runs.ts, approvals.ts, usage.ts, auth.ts, audit.ts), keep the barrel re-export-only — also makes per-domain drift tests tractable.

## Oversized front-end units

### REF-7. `ChatScreen.tsx` (2,698) — god component, no direct test
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** frontend-web (F6)
Orchestrates run lifecycle, SSE subscription/resume, seven reducer registries, approvals, connector auth, model/depth, drafts, workspace pane, share state — with no `ChatScreen.test.tsx`. `App.tsx` (1,031) + `SettingsScreen.tsx` (1,405) are smaller instances.
**Remediation:** Extract the event-subscription + reducer-fanout into a headless `useRunStreams` hook (testable without the DOM) — but the higher-leverage move is converging onto the chat-surface cockpit (DUP-1) rather than growing this. Do it before the desktop Run-cockpit convergence.

### REF-8. `Composer.tsx` (1,639) — the package's least-reviewable unit
**Severity/confidence:** medium/medium · **Verification:** confirmed · **Cluster:** chat-surface-core (F9)
Textarea + caret management (flushSync) + attachment pipeline + mention/tool/model popovers + drag-drop + submit assembly + ~500 lines of inline styles in one file (test file another 1,435).
**Remediation:** Extract the attachment pipeline + popover orchestration into hooks; move inline style constants next to `composer.css`.

### REF-9. `styles.css` (9,049) monolith; `api-types` barrel; barrel accretion in chat-surface
**Severity/confidence:** low/high · **Verification:** accepted · **Clusters:** frontend-web F7, chat-surface-destinations F10
9,049-line stylesheet mixing `aui-*`/`atlas-*`/screen rules with load-bearing invariants as comments; chat-surface `index.ts` (1,270) + `settings/index.ts` export the full dead surface (halves once DEAD-1 lands).
**Remediation:** Co-locate feature CSS with features; regenerate the barrel around live exports + pin with a snapshot test after the dead-code sweep.

## Overlapping machinery

### REF-10. Four near-identical tool-wrapper classes + three registry decorators (composition order lives only in prose)
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** ai-runtime-capabilities (F8)
`ToolBudgetGuardedTool`/`ToolErrorPolicyTool`/`CitationCapturingTool`/`RetryingTool` each re-implement "propagate name/description/args_schema, override `_run`/`_arun`"; three registry decorators each re-implement `list_available_tools` + idempotent `_wrap`; `display_metadata.py` adds a fifth structurally-different wrap. Order exists only at `runtime_worker/dependencies.py:129-131`, and each wrapper handles `args_schema=None`/injected `tool_call_id`/idempotency slightly differently — so schema-propagation bugs surface only in fully-composed runs.
**Evidence:** tool_budget_guard.py:183-289; tool_error_policy_tool.py:54-158; citation_capturing_tool.py:106-254; retrying_tool.py:43-130; middleware/display_metadata.py:516-643.
**Remediation:** A generic `wrap_registry(inner, *wrappers)` + one `DelegatingTool` base; keep each concern a small hook; makes ordering declarative and gives one place to fix schema propagation.
**Payoff:** ~150-250 LOC + one seam for schema bugs.

### REF-11. `RuntimeRunHandler.handle` — ~340-line method in a 1,667-line class
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** flow-run-streaming (F8)
Interleaves budget preflight, seven ContextVar bind/unbind pairs, streaming-vs-invoke branching, interrupt detection, final-message assembly, and four terminal paths — each repeating discard-ledger/discard-metrics/record-usage/audit choreography. The bind/unbind pairs are an `ExitStack` begging to exist; the terminal choreography belongs on `RunTerminationCoordinator` (which already exists) so a new terminal path can't forget a step — which is exactly what happened in the approval-resume handler (RISK-approval-resume).
**Evidence:** services/ai-backend/src/runtime_worker/handlers/run.py:229-573.
**Remediation:** `ExitStack` for the ContextVars; move terminal choreography onto `RunTerminationCoordinator`; share it with the approval handler (couples to DUP-16).
**Payoff:** behavior-preserving; makes the metering/audit/budget steps unforgettable across both handlers.

### REF-12. Per-destination CRUD scaffolding copy-pasted across 9+ backend modules
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** backend-product (F7)
Each destination defines `XNotFound`/`XForbidden`/`XInvalidRequest`, a `_can_read` 404-not-403 predicate, `_validate_*_payload`, `_safe_dump`, `_now()`, and its own `_ADMIN_ROLES = frozenset({"admin","owner"})` — the frozenset alone in **14 files**; route layers repeat the same exception→status table.
**Evidence:** grep `_ADMIN_ROLES` across services/backend/src/backend_app/*/service.py; todos/service.py:216-238 vs routines/service.py:153-183.
**Remediation:** One `destination_common` module (exception bases keyed by resource, admin-role constant, dump/validation helpers, exception→HTTP mapper); single-sources the 404-not-403 rule.
**Payoff:** several hundred LOC + one place for the ACL rule.

## Efficiency (low)

### REF-13. Accepted-as-written efficiency/complexity items
**Severity/confidence:** low · **Verification:** accepted
- `AuditChainSigner.from_env` reconstructed on *every* audit append in both Postgres stores (re-reads env, re-derives key) vs in-memory caching one signer (backend-core F12) — build once per store instance.
- N+1 vault/token reads on every MCP card listing (`_effective_auth_state` → `store.get_token` per server) — joined read or a token-state column (flow-mcp F10).
- Unbounded event replay with hardcoded `has_more=False` + delta coalescing off by default → arbitrarily large first-connect responses (flow-run-streaming F11) — implement the page size the schema already carries.
- Two parallel citation token systems ([cN] ledger vs [[N]] ordinals) across five modules (ai-runtime-capabilities F11) — document the two-lane contract in one place, consider converging.
- Two independent retention mechanisms in the file store (policy sweep vs env-gated whole-store purge) on different clocks (flow-data F13) — collapse to one.
- Facade SSE passthrough only notices client disconnect on upstream traffic (flow-run-streaming F12) — bounded once keepalives land (RISK-sse-keepalive).
- Desktop `main/index.ts` minor consolidation (duplicate `allowPlaintext` expr; RunBinder/RunComposer redundant per-mount readiness probes) (desktop-app F10).
