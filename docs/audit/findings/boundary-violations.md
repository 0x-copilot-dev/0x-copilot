---
id: findings-boundary-violations
kind: report
title: Boundary violations — layering/dependency-direction breaks
audit_date: 2026-07-20
---

# Boundary Violations

Places where the documented module/service layering is fiction at the import graph. None cross a *deployable* boundary (the hard `apps/*`→`apps/*` / `services/*`→`src/*` rule holds), but the intra-service "pure domain vs presentation" split the READMEs advertise does not — which makes future extraction painful and makes the domain untestable without importing the HTTP layer.

Ordering: by how many modules participate.

---

### BND-1. ai-backend "pure domain" (`agent_runtime`) imports the HTTP layer (`runtime_api`) and the worker (`runtime_worker`) — a package-level cycle
**Severity/confidence:** high/high · **Verification:** confirmed (5 auditors) · **Clusters:** ai-runtime-api, ai-runtime-execution, ai-runtime-capabilities, ai-runtime-persistence, ai-runtime-worker
**Merged sources:** ai-runtime-api F1, ai-runtime-execution F3, ai-runtime-capabilities F9, ai-runtime-persistence F8, ai-runtime-worker F4.
The service CLAUDE.md declares `agent_runtime/` pure domain, `runtime_api/` presentation, `runtime_worker/` execution. In practice the dependency direction is inverted and circular:
- **20/32 `agent_runtime/api` modules** import `runtime_api.schemas`/`http.errors` (e.g. `run_coordinator.py:37`, `ports.py:44`).
- **Capabilities** import them too: `citations.py:22`, `tool_budget_guard.py:25`, `backends/draft_backend.py:32`, `render_adapter_generator/capability.py:23`, plus a late import in `citation_resolver.py:108` whose comment admits it dodges a circular import.
- **Persistence** (the "pure domain" package) imports upward: `message_copy.py:27`, `base.py:8-14`, `file/runtime_api_store.py:120` — the canonical `ConversationRecord`/`MessageRecord`/`RunRecord`/`RuntimeEventEnvelope` models live in `runtime_api.schemas`. 27 files under `agent_runtime/` import `runtime_api`.
- **Memory/pricing:** `context/memory/subagent_trace.py:30-38` imports `runtime_api.schemas` + `agent_runtime.api.ports` and even re-defines `StreamTextHelper` verbatim to dodge the cycle; `pricing/refresh_loop.py:22` + `upsert_planner.py:15` import `agent_runtime.api.ports.PersistencePort`.
- **The reverse leg:** `runtime_api.app:76,593` + `system_skills.py:30` import `runtime_worker`; `agent_runtime/api/{self_fork,conversation_fork,mcp_discovery_service}.py` import `runtime_worker.audit.WorkerAuditEmitter`; `tool_budget_guard.py:28`/`tool_budget_middleware.py:17` TYPE_CHECK against `runtime_worker.tool_call_ledger`.
`test_import_boundaries.py` only bans retired module paths, not layering direction, so none of this is caught.
**Evidence:** services/ai-backend/src/agent_runtime/api/ports.py:44-61; persistence/message_copy.py:27; runtime_adapters/base.py:8-14; context/memory/subagent_trace.py:30-62; runtime_api/{app.py:76,system_skills.py:30}; services/ai-backend/CLAUDE.md.
**Remediation:** Relocate the record/envelope contracts (and `RuntimeApiError`, `RuntimeApiEventType`, `RuntimeEventEnvelope`, `MessageRecord`) into a domain-owned module (`agent_runtime/contracts` or `persistence/records`); have `runtime_api.schemas` re-export them for wire compatibility. Move `WorkerAuditEmitter` + `BUILTIN_SKILLS_ROOT` + the `ToolCallLedger` protocol out of `runtime_worker` into `agent_runtime` (audit is not worker-specific — the API already uses it for forks). Then extend `test_import_boundaries.py` to forbid `agent_runtime → runtime_api|runtime_worker`.
**Payoff:** the domain becomes importable/testable without FastAPI; unblocks any future extraction; a single lint prevents regression. This is the structural keystone — several duplication findings (StreamTextHelper re-def, worker/handler private reach-ins) exist only to route around the cycle.

### BND-2. `api-types` reaches `service-contracts` via a relative filesystem import with no declared dependency
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** shared-packages
`packages/api-types/src/adapterAllowlist.ts:10` imports `../../service-contracts/src/copilot_service_contracts/adapter_allowlist.json` while `api-types/package.json` declares no dependency on service-contracts (which is Python-only and has no npm manifest, so it *can't* be a proper npm dep). It works only because packages ship source in one checkout; every consumer Dockerfile must silently know to include service-contracts (`apps/frontend/Dockerfile:16` copies it uncommented). Publishing or relocating either package breaks the import invisibly.
**Evidence:** packages/api-types/src/adapterAllowlist.ts:10; packages/api-types/package.json:12-15; apps/frontend/Dockerfile:16.
**Remediation:** Vendor the JSON into api-types with a checksum canary test against the Python copy, or make the allowlist its own tiny npm+pip package; at minimum comment the Dockerfile line and declare the workspace dependency.
**Payoff:** removes an invisible build contract that will break on the first publish/relocate.

### BND-3. Minor / sanctioned-in-spirit boundary deviations
**Severity/confidence:** low/high · **Verification:** accepted · **Clusters:** chat-surface-core, backend-facade
- Desktop deep-imports `chat-surface/src/composer/composer.css` + `workspace/workspace.css` against the barrel-only rule (unavoidable for CSS, but undocumented); `Composer.tsx:660` reads `globalThis.document.activeElement` without the undefined guard every other touchpoint has (chat-surface-core F13). → Document CSS as an allowed deep-import (add `package.json` `exports` for the two files); add the guard.
- Root CLAUDE.md states backend `/internal/v1/*` is "consumed only by ai-backend", but the facade consumes ~50 backend internal endpoints + two ai-backend ones (backend-facade F6). → Update the doc to "consumed by ai-backend and backend-facade only; never exposed".

> **Note — the deployment-profile/RBAC loader triplication (SSOT-6) is boundary-adjacent:** it is duplication *forced* by the hard cross-service import rule, not a violation of it. The right fix is data-in-`service-contracts` + thin per-service loaders, not a shared import. Listed under SSOT-6.
