---
id: findings-ssot-violations
kind: report
title: SSOT violations — one fact maintained in many places
audit_date: 2026-07-20
---

# Single-Source-of-Truth Violations

Facts (enums, templates, catalogs, wire shapes, config values) hand-maintained in multiple places with only comments as the linkage. Unlike pure duplication, the harm here is *silent semantic drift at runtime* — a change in one copy that the others miss ships a broken contract with no compile-time signal.

Ordering: by blast-radius of a drift.

---

## Cross-language wire contracts

### SSOT-1. `api-types` is a ~9.6k-LOC hand-maintained dual-write with drift tests for only 4 enum tuples
**Severity/confidence:** high/high · **Verification:** confirmed (2 auditors) · **Clusters:** shared-packages, flow-contracts (flow-run-streaming F4)
**Merged sources:** shared-packages F1, flow-contracts F4, flow-run-streaming F4.
Every app-facing payload exists twice: server Pydantic and a hand-written TS mirror (9,231 LOC; `index.ts` alone 3,978). The only cross-language check is `test_api_type_contracts.py`, which set-compares just `RUNTIME_API_EVENT_TYPES`/`RUNTIME_EVENT_SOURCES`/`RUNTIME_ACTIVITY_KINDS`/`AGENT_RUN_STATUSES`. Everything else — approvals, usage, MFA, audit, settings, all destination payloads — can drift with zero signal; ~46 backend files self-declare "mirror of api-types". Compounding it, the strict `isRuntimeEventEnvelope` guard **rejects unknown event types**, so a backend-first event-type addition silently drops those envelopes in the cockpit (`useRunSession.parseEnvelope` → null) or raises a protocol error in legacy chat — the versioned envelope (`event_protocol_version`) can't actually evolve safely. Root `CLAUDE.md` calls this package "generated contracts"; it is not.
**Evidence:** packages/api-types/{README.md:6-8,SPEC.md:25-33}; services/ai-backend/tests/unit/runtime_api/test_api_type_contracts.py:11-37; packages/api-types/src/index.ts:2366-2369; packages/chat-surface/src/destinations/run/useRunSession.ts:318-326.
**Remediation:** Generate the TS from the FastAPI/Pydantic OpenAPI output into a `generated/` path (SPEC.md's own plan), or at minimum extend the drift test to every runtime tuple + add per-route response-shape fixtures; make the SSE guard tolerant of unknown event types (preserve-and-render-generic). Fix the root CLAUDE.md "generated" wording.
**Payoff:** removes the highest-likelihood, highest-blast-radius silent failure in the product (FE renders wrong/missing fields with no compile signal).

### SSOT-2. Six `_*-stub.ts` contract copies never rewired to api-types (already drifted)
**Severity/confidence:** high/high · **Verification:** confirmed (2 auditors) · **Clusters:** chat-surface-destinations, flow-contracts
**Merged sources:** chat-surface-destinations F2, flow-contracts F1.
Transitional `_{todos,projects,agents,inbox,library,routines,tools}-stub.ts` modules (in both `apps/frontend/src/api/` and `packages/chat-surface/src/destinations/*`) carry `TODO(merge): delete this file` headers, but the canonical `packages/api-types/src/{todos,projects,...}.ts` already landed. They have materially drifted: `_todos-stub.Todo` models `done: boolean` while server + api-types use `status`+recurrence/subtask fields — the dead `TodosRoute` reads `t.done` (always undefined) and `todosApi.ts` sends `filter[done]` the server (`filter[status]`) never reads. The live `ProjectsRoute` still imports `../../api/_projects-stub` despite `api-types/projects.ts` having every type.
**Evidence:** packages/chat-surface/src/destinations/todos/_todos-stub.ts:38; apps/frontend/src/api/_projects-stub.ts; packages/api-types/src/todos.ts:31,projects.ts:292-478; ProjectsRoute.tsx:84.
**Remediation:** Point every live import at `@0x-copilot/api-types` and delete all 14 stub files (most fall with the DEAD-1 sweep); keep only `_projects-stub` until `ProjectsRoute` is repointed.
**Payoff:** ~1.3k LOC + removes the repo's largest live contract-drift instance.

### SSOT-3. SIWE EIP-4361 message template maintained in 3 code copies (+ harness + doc = up to 5)
**Severity/confidence:** high/high · **Verification:** confirmed (5 auditors) · **Clusters:** backend-identity, flow-auth, flow-contracts, frontend-web, desktop-distribution, docs-corpus
**Merged sources:** backend-identity F3, flow-auth F5, flow-contracts F2, frontend-web F9, desktop-distribution F6, docs-corpus F11.
The byte-exact template + statement `"Sign in to Copilot"` is hand-kept in `services/backend/.../identity/siwe.py:290-302`, `apps/frontend/.../siweMessage.ts:30-41`, and `apps/desktop/main/auth/local-login.ts:78-91` — plus a **4th** copy in `tools/cli-testing/harness/siwe-session.mjs` and a **5th** in `docs/deployment/wallet-login.md`. Root CLAUDE.md documents only two. The backend parser rejects any drift with `SiweMessageInvalid`, so a one-side wording tweak **bricks wallet + local login at runtime**; only frontend↔backend share a fixture test.
**Evidence:** the five files above; siweMessage.test.ts:52,68 (pins only the frontend copy).
**Remediation:** Serve the template/params from the backend nonce response (best), or commit one golden fixture all suites assert against; add the harness + doc as illustrative-only ("canonical: siwe.py") and fix the CLAUDE.md count.
**Payoff:** converts a silent login-outage risk into a CI failure.

### SSOT-4. ≥6 divergent hardcoded model catalogs
**Severity/confidence:** high/high · **Verification:** confirmed (4 auditors) · **Clusters:** chat-surface-core, chat-surface-destinations, desktop-app, flow-contracts
**Merged sources:** chat-surface-core F7, chat-surface-destinations F7, desktop-app F4, flow-contracts F3.
Per-provider model-id lists are hardcoded — and already disagree — in `agent_runtime/api/model_catalog.py`, `desktopModelCatalog.ts`, `chat-surface ModelPicker.tsx`, `apps/frontend ChatScreen.tsx` (`demoModels`, has `gpt-5.4-nano` the desktop list lacks), `backend agents/service.py`, and `chat-surface settings/data/providerKeys.ts` (`claude-opus-4` vs `claude-opus-4-7` vs `anthropic:claude-opus-4-7`). Plus dead copies in `AgentEditor.tsx`/`RoutineEditor.tsx`.
**Evidence:** services/ai-backend/src/agent_runtime/api/model_catalog.py:46-122; apps/desktop/renderer/composer/desktopModelCatalog.ts:30-61; packages/chat-surface/src/{composer/ModelPicker.tsx:34,settings/data/providerKeys.ts:56-95}; apps/frontend/src/features/chat/ChatScreen.tsx:2561-2600.
**Remediation:** Serve one catalog from the facade (`/v1/models`-style, per the decided models.dev direction); pickers already accept descriptor props — feed them the served catalog; keep `PROVIDER_CATALOG` for provider identity/prefix only.
**Payoff:** collapses 6+ lists to one; ends drift that surfaces as user-facing "model not available" bugs.

### SSOT-5. MCP enum vocabulary hand-copied in 3 places; ~10 MCP-server representations
**Severity/confidence:** high/high (representation sprawl) · **Verification:** confirmed (2 auditors) · **Clusters:** flow-mcp, flow-contracts
**Merged sources:** flow-mcp F2 + F3, flow-contracts F5. (Representation sprawl is also a duplication finding.)
`McpTransport`/`McpAuthMode`/`McpAuthState`/`McpServerHealth` are declared independently in `backend_app/contracts.py:148-176`, `ai-backend mcp/cards.py:45-80` (derived from a *service-local* constants file, not `service-contracts`), and `api-types/index.ts:18-31` — a new auth state needs three manual edits, drift fails at runtime. Around that, ~10 representations of "an MCP server/connector" exist (`McpServerRecord`/`Response`/`InternalMcpServerCard`, `CatalogEntry`, connectors `ConnectorRecord`/`ConnectorCatalogEntry`, desktop `*Profile`, ai-backend `McpServerCard`/`McpServerConfigFile`, api-types `McpServer`/`Connector`, desktop Zod re-declarations) plus **three catalogs** (`DEFAULT_CATALOG` 13, `catalog.yaml` 9, `desktop_profiles.yaml`) and **two disjoint status taxonomies** (`ConnectorStatus` vs `McpAuthState`) with no mapping function anywhere.
**Evidence:** services/backend/src/backend_app/contracts.py:148-176; services/ai-backend/.../mcp/cards.py:45-80; packages/api-types/src/{index.ts:18-31,connectors.ts:49-53}; mcp_catalog.py, connectors/{catalog.yaml,desktop_profiles.yaml}.
**Remediation:** Host the MCP enum values in `service-contracts` (constants-only is allowed — this is exactly its job) with a conformance test; collapse the two live UI worlds (Settings grid on `McpServer` vs Tools destination on `Connector`) onto one representation — see RISK-connectors-stub for the read-model decision.
**Payoff:** three enum copies → one; removes the connector-representation debt driving the stub read-model.

---

## backend config / deployment

### SSOT-6. Deployment-profile loader triplicated across the three services
**Severity/confidence:** medium/high · **Verification:** confirmed (2 auditors) · **Clusters:** flow-contracts, ai-runtime-api, backend-platform
**Merged sources:** flow-contracts F6, ai-runtime-api F11, backend-platform F9. (Boundary-forced today — see boundary-violations.md.)
`service-contracts` shares only the profile *constants*; each service then re-implements a ~200-LOC profile→toggles **loader** (`backend_facade` 230, `backend` 196, `ai-backend` 215), self-documented as a mirror. The toggle-derivation table is a *fact* that could live as data in `service-contracts` (like `adapter_allowlist.json`) with per-service thin loaders. RBAC dependency helpers are duplicated the same way (backend↔ai-backend).
**Evidence:** services/backend-facade/src/backend_facade/deployment_profile.py:7-9; services/backend/src/backend_app/deployment_profile.py; services/ai-backend/src/agent_runtime/deployment/profile.py.
**Remediation:** Move the toggle table into `service-contracts` as data + a tiny pure evaluator (no service deps), leaving only FastAPI glue per service; same for the RBAC mode-resolution core.
**Payoff:** ~600 LOC of security-sensitive logic collapses to one source (currently must be fixed in 2-3 places).

### SSOT-7. Desktop→services env contract is ~20-30 unregistered TS string literals
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** flow-contracts (flow-desktop-boot F2)
`apps/desktop/main/services/service-env.ts:154-237` sets ~30 env names (`ENTERPRISE_DEPLOYMENT_PROFILE`, `RUNTIME_STORE_BACKEND`, `MCP_BACKEND_REGISTRY_URL`, `SIWE_ORIGIN`, `BACKEND_BASE_URL`…) as literals that must match each Python settings loader, with no TS-reachable registry. This already shipped a production-down bug: `BACKEND_BASE_URL` omission degraded ai-backend to Null policy/membership/notification resolvers (fixed `bcc65dbb`/#114, post-dating the audit base — verify it also covers `run-local.mjs`).
**Evidence:** apps/desktop/main/services/service-env.ts:154-237; services/ai-backend/src/agent_runtime/api/project_resolver.py:282-295.
**Remediation:** A JSON env-name manifest in `service-contracts` consumable from TS (like `adapter_allowlist.json`) so omissions are lint-detectable.
**Payoff:** makes a recurring class of desktop boot/runtime breakage compile-time detectable.

### SSOT-8. Todo recurrence grammar implemented twice across services
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** backend-product (ai-runtime-worker F10)
`RecurrenceRuleEvaluator` (FREQ/BYDAY/INTERVAL RFC-5545 subset) lives in both `backend todos/service.py:86` and `ai-backend todo_recurrence_materializer.py:94` with no shared contract or fixture — exactly the edge-case-prone logic (BYDAY+INTERVAL week alignment) that drifts unobserved.
**Evidence:** services/backend/src/backend_app/todos/service.py:55-204; services/ai-backend/src/runtime_worker/jobs/todo_recurrence_materializer.py:94.
**Remediation:** Move the grammar constants + a golden test-vector file into `service-contracts` (constants-only allowed) so both validate against one fixture set (also relevant to LIB-schedule and DUP-14).
**Payoff:** one fixture guards two implementations across the hard boundary.

### SSOT-9. Per-tool call-cap fact split across 3+ divergent sources; default tool-budget seed in 3 places
**Severity/confidence:** high/high · **Verification:** confirmed (2 auditors) · **Clusters:** ai-runtime-execution, ai-runtime-persistence
**Merged sources:** ai-runtime-execution F1, ai-runtime-persistence F9.
The prompt suffix bakes `_DEFAULT_TOOL_CALL_BUDGET = 5` at import; `RuntimeSettings.execution.tool_call_budget` defaults **6** and is depth-scaled onto `ModelConfig` — but **no production code reads `ModelConfig.tool_call_budget`**; actual enforcement is `ToolBudgetRecord.max_calls_per_run=6` seeded in the adapters (hand-maintained in `in_memory`, `file`, and the SQL migration `0010`). So the model is told a *different* number than is enforced, and the "mirrors the value ToolBudgetMiddleware hard-enforces" docstring is false.
**Evidence:** services/ai-backend/src/agent_runtime/execution/{deep_agent_builder.py:44,depth.py:127-130,models.py:117-122}; runtime_adapters/{in_memory,file}/runtime_api_store.py; migrations/0010_runtime_tool_budgets.sql.
**Remediation:** Pick one owner (the resolved `ModelConfig`), derive both the prompt suffix and the `ToolBudgetRecord` cap from it per run, delete the dead default; define the seed once in `persistence/constants.py`; add a test asserting prompt number == enforced number == SQL seed.
**Payoff:** removes an active model-vs-enforcement mismatch + 3-way seed drift.

---

## front-end / desktop

### SSOT-10. Two coexisting composer depth models; ReasoningDepth duplicated 3×
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** chat-surface-core (ai-runtime-execution F12, frontend-web F9)
`ThinkingDepth`/`THINKING_DEPTHS`/`DEPTH_LABEL` in `composer/depth.ts` (feeds the `reasoning_depth` wire field) vs an independent `Depth`/`DEPTHS` in `ModelPicker.tsx`, with a third pointer in `apps/frontend/.../depth.ts`; `depth.ts:5-12` flags the fork ("do not silently fork them"). Separately, the Python `ReasoningDepth` StrEnum is duplicated into **two** TS unions (`api-types/index.ts` and `agents.ts`).
**Evidence:** packages/chat-surface/src/composer/{depth.ts:24-42,ModelPicker.tsx:25-45}; packages/api-types/src/{index.ts:1314,agents.ts:99}.
**Remediation:** Collapse to `ThinkingDepth` (the wire-connected one), `ModelPicker` takes descriptors as props; collapse the two TS unions into one exported type + a contract test vs the Python enum.
**Payoff:** one depth model + one wire enum.

### SSOT-11. App/runtime identity + branding + Electron version hand-synced (Electron **major skew** ships to users)
**Severity/confidence:** high/high (Electron skew) · **Verification:** confirmed (2 auditors) · **Clusters:** desktop-distribution, flow-desktop-boot
**Merged sources:** desktop-distribution F1, flow-desktop-boot F6 + F7. (Branding-sync portion also in DUP-15.)
The most consequential: the published CLI pins `electron: 42.1.0` (the runtime npm-installs launch) while the app is developed/CI-tested/electron-builder-released on `43.1.1` — end users run an entire Electron major that nothing in CI exercises. Around it, `com.0x-copilot.app`, `"0xCopilot"`, platform keys, `APP_VERSION`, and the SERVICES dir lists are hand-synced across ≥5 files.
**Evidence:** tools/cli/package.json:26 vs apps/desktop/package.json:46; apps/desktop/main/branding.ts:21-26.
**Remediation:** Make the desktop app's Electron version the single source (read at assemble/prepack time, or a `ci-cli` equality lint) and bump the CLI to 43.1.1; a small shared constants module (or build-time injection) removes the branding/version sync class.
**Payoff:** eliminates an untested-runtime-major risk for every npm-path install.

### SSOT-12. Migration-manifest logic triplicated + the documented CI gate does not exist
**Severity/confidence:** medium/high · **Verification:** confirmed (2 auditors) · **Clusters:** build-deploy, flow-data
**Merged sources:** build-deploy F7, flow-data F10.
`tools/check_migration_manifest.py`'s parse/digest/render is byte-near-identical to `backend/db/migrate.py:99-160` and `ai-backend schema/migrate.py:95-158` (same sha256+NUL+rollback algo, same parse, same auto-generated header claiming "CI will refuse") — yet no workflow/hook/Make target invokes the checker; drift is caught only at service boot. Docs claim a CI gate. The yoyo `MigrationRunner` itself is also duplicated near-line-for-line across the two services.
**Evidence:** tools/check_migration_manifest.py; services/backend/src/backend_app/db/migrate.py:99-160; services/ai-backend/src/agent_runtime/persistence/schema/migrate.py:95-158.
**Remediation:** Add a `check_migration_manifest.py` step to `ci-backend`/`ci-ai-backend` (cheap, stdlib-only); consider a single shared implementation the services call (candidate `service-contracts` primitive alongside audit-chain).
**Payoff:** closes a false-safety-net gap + de-triplicates.

---

## smaller SSOT items (accepted, low)

### SSOT-13. Trusted service-header names re-declared next to the constants package that owns them
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** flow-mcp F4
`suggestible_connectors_resolver.py:33-38` hand-declares `x-enterprise-*` while `copilot_service_contracts.headers` is the SSOT (imported correctly one directory away). Same file re-implements `BACKEND_BASE_URL` env resolution.
**Remediation:** Import the constants; reuse the worker settings' env resolution.

### SSOT-14. Accepted-as-written low-severity SSOT items
**Severity/confidence:** low/high · **Verification:** accepted
- SIEM exporter env-var name split: `SIEM_EXPORTER_BACKEND` (exporter + security doc) vs `SIEM_EXPORT_BACKEND[_NAMES]` (admin routes + roadmap doc) — operators following one doc get an invisible exporter (backend-platform F6, **medium/confirmed** — pick one name in `service-contracts`).
- `RequireAnyScope("admin:users", ...)` hardcodes the literal despite `ADMIN_USERS` imported in the same file (ai-runtime-api F13).
- `CapabilityAuthGate` restates `McpAuthState.authenticated` as a string literal (ai-runtime-capabilities F12).
- Accent/theme unions duplicated between api-types + design-system (shared-packages F4).
- `api-types` reaches `service-contracts`' `adapter_allowlist.json` via relative FS path (shared-packages F3 — also boundary-violations.md).
- Default persona slug `"sarah_acme"` hardcoded ×4; workspace fallbacks `org_acme` vs `wsp_unknown` (flow-contracts F11, flow-desktop-boot F10).
- Three hand-maintained backend fact sets: `0032_todos.sql` vs module DDL, `routines/webhook.py` hardcodes HMAC constants `signer.py` claims to own, connector slug metadata across three files (backend-product F10).
- Workspace-layout facts drifted across `workspace-topology.md` + two `.cursor` mirrors + `service-boundaries.md` (docs-corpus F4, **medium/confirmed**).
- Module `schema.sql` maintained beside `migrations/` and already drifting (todos cites nonexistent `0033_todo_series.sql`) — flow-data F9 (**medium/confirmed**); browser `tool-schemas.ts` hand-mirrors its Zod (desktop-app F9); boot-phase copy duplicated main↔renderer (flow-desktop-boot F9).
