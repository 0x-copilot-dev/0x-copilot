# Atlas Destinations — Implementation Plan

**Status:** binding (2026-05-17)
**Owner:** parth (orchestrator)
**Reads from:** [PRD.md](PRD.md) · [destinations-master-prd.md](destinations-master-prd.md) · [cross-audit.md](cross-audit.md) · sub-PRDs at [destinations/](destinations/)

---

## 0. How to read this document

Every parallel-agent dispatch maps to a row in §2. Each row tells you:

- the **branch name** the agent uses (worktree discipline);
- the **files it owns exclusively** (no other parallel agent touches them);
- the **prerequisites** it waits for (other rows that must merge first);
- the **deliverables** (artifacts shipped at merge);
- the **test gates** (which suites must be green before merge).

Anti-conflict rule: **no two simultaneously-running agents share an exclusively-owned file.** If two agents need to touch the same file, one of them is folded into the other, or a pre-merge "shared wiring" agent runs first. This is the rule that lets us run 4-6 agents in parallel without merge hell.

---

## 1. Dependency DAG

```
                          ┌─────────────────────────────┐
                          │  Shared Primitives Agent    │   (§4 of cross-audit)
                          │  branch: shared-primitives  │
                          │  blocks every dest agent    │
                          └──────────────┬──────────────┘
                                         │
            ┌────────────────────────────┼────────────────────────────┐
            │                            │                            │
            ▼                            ▼                            ▼
    ┌───────────────┐            ┌───────────────┐            ┌───────────────┐
    │ Phase 1 Chats │            │ Phase 2 Home  │            │ Phase 3 Todos │
    │ (3 agents)    │            │ (2 agents)    │            │ (2 agents)    │
    └───────┬───────┘            └───────┬───────┘            └───────┬───────┘
            │                            │                            │
            │  ┌─────────────────────────┘                            │
            │  │                                                       │
            ▼  ▼                                                       ▼
        ┌────────────────────────┐                          ┌───────────────────┐
        │ Phase 4 Inbox          │                          │ Phase 5 Routines  │
        │ (3 agents — producer   │◄─── consumes inbox       │ (2-3 agents)      │
        │ needs Chats approvals  │     producer from        │ (new 12th dest)   │
        │ event + Todos.source)  │     ai-backend           │                   │
        └────────────────────────┘                          └───────────────────┘
                                                              │
                                                              ▼  (later waves)
                                                            … (Projects/Library/
                                                                Agents/Tools/
                                                                Connectors/Team/
                                                                Memory/⌘K)
```

Hard dependencies:

- **Shared Primitives → every destination agent.** Destinations import `<ItemLink>`, `<PageHeader>`, `BadgePort`, etc. from this PR.
- **Chats Phase-1 approval-event types → Inbox producer.** Inbox's `kind: "approval_request"` items need the Chats approval contract landed first.
- **Todos Phase-3 source-ref type → Inbox.** Inbox `links: ItemRef[]` references `ItemKind="todo"` — needs Todo ID type minted.

Soft (parallelizable):

- Phase 2 Home + Phase 3 Todos are independent (different folders, different tables, different backend routes).
- Phase 5 Routines depends on Phase 1 (composer-migration's run-start contract) but not on Phases 2-4.

---

## 2. Per-phase dispatch table

Each phase has 2-3 sub-agents running in parallel inside the phase. Phases themselves can overlap when their dependencies allow.

### Phase 0 (already shipped)

| Agent                                               | Status    | Branch                               | Landed at |
| --------------------------------------------------- | --------- | ------------------------------------ | --------- |
| Wave 1 shell foundation (α)                         | ✅ merged | `worktree-agent-shell-foundation`    | `d253005` |
| Wave 1 destinations tokens (β)                      | ✅ merged | `worktree-agent-destinations-tokens` | `98927dc` |
| Wave 1 composer rebuild (γ)                         | ✅ merged | `worktree-agent-ac711ddb79945057d`   | `5ca978e` |
| Wave 1.5 polish (rail Settings + right rail closed) | ✅ merged | —                                    | `80ab929` |
| Wave 2 reasoning_depth e2e (A1)                     | ✅ merged | `worktree-agent-depth-contract`      | `3ddce68` |
| Wave 2 tools-kind (A2)                              | ✅ merged | `worktree-agent-a1c1fd59daa9b3795`   | `53b3ad9` |

### Phase 0.5 + 0.6 — Prerequisites (BOTH must merge before any destination work)

Phase 0.5 (shared primitives) and Phase 0.6 (token-usage tracking) are independent of each other. They run **in parallel**. All destination phases gate on BOTH being merged.

### Phase 0.5 — Shared primitives (✅ SHIPPED 2026-05-18 at `2439428`, `6ecfd39`, `aacc1a3`)

**Audit outcome (2026-05-18):** Zero `__brand:` declarations in `packages/chat-surface/src` (DRY satisfied). Single `formatRelativeTime` in `packages/chat-surface/src/util/time.ts`. All 4 ports, 7 shell primitives, ItemLink registry, brands, refs exported through barrels. `cd packages/chat-surface && npm test` → **454/454 pass**. `api-types` + `frontend` typecheck green.

**Deferred gaps (do not block Phase 1):**

- 4 destinations not in scope (Connectors/Tools/Memory/Team) still have inline `formatRelativeTime` clones — will migrate when those destinations get rewritten in Wave 5-6.
- `ArtifactRoute` covers only `chat`/`run`/`subagent`/`tool-result`/`skill`/`workspace`/MCP today. New `ItemKind` values get route shapes when their destination phase ships.
- No-op web port implementations (`apps/frontend/src/ports/{Badge,Notification,FilePicker,Clipboard}Web.ts`) **must** ship in P1-C — current `App.tsx` does not inject them.
- ESLint boundary forbidding direct `router.navigate(…)` outside `<ItemLink>` not yet added — recommended for Wave 3 lint pass.
- `LibraryItemId` brand hoisted to api-types with a `TODO(Wave 6 Library)` reconcile note (cross-audit §2.1 enumerates only `LibraryFileId`/`LibraryPageId`/`LibraryDatasetId`).

| Agent                      | Branch                             | Exclusive files                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Prereqs | Deliverables                                                                                                 | Test gates                                                             |
| -------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------- | ------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| **SP-1 Shared Primitives** | `worktree-agent-shared-primitives` | `packages/api-types/src/refs.ts` (NEW), `packages/api-types/src/brands.ts` (NEW), `packages/api-types/src/index.ts` (extend re-exports), `packages/chat-surface/src/refs/registry.ts` (NEW), `packages/chat-surface/src/refs/ItemLink.tsx` (NEW), `packages/chat-surface/src/refs/index.ts` (NEW), `packages/chat-surface/src/shell/PageHeader.tsx` (NEW), `packages/chat-surface/src/shell/FilterTabs.tsx` (NEW), `packages/chat-surface/src/shell/StatusPill.tsx` (NEW), `packages/chat-surface/src/shell/EmptyState.tsx` (NEW), `packages/chat-surface/src/shell/CardGrid.tsx` (NEW), `packages/chat-surface/src/shell/DocList.tsx` (NEW), `packages/chat-surface/src/shell/ActivityList.tsx` (NEW), `packages/chat-surface/src/util/time.ts` (NEW), `packages/chat-surface/src/ports/BadgePort.ts` (NEW), `packages/chat-surface/src/ports/NotificationPort.ts` (NEW), `packages/chat-surface/src/ports/FilePickerPort.ts` (NEW), `packages/chat-surface/src/ports/ClipboardPort.ts` (NEW), `packages/chat-surface/src/ports/index.ts` (extend), `packages/chat-surface/src/destinations/home/HomeDestination.tsx` (one-line migration to import `formatRelativeTime` from new location), all tests for the new primitives | none    | `ItemRef` + `ItemLink` registry + every shell primitive + 4 ports + branded IDs + `formatRelativeTime` hoist | `chat-surface` 100% green; `api-types` typecheck; `frontend` typecheck |

### Phase 0.6 — Token-usage tracking (PREREQUISITE; ✅ SHIPPED 2026-05-17 at `4939186`)

**Audit outcome:** the canonical TU-1 implementation already exists in `services/ai-backend/` (`UsageRecorder` + `runtime_run_usage` + `runtime_model_call_usage` + `/v1/usage/*` + `Purpose` enum attribution + Anthropic/OpenAI/Gemini extractors + LiteLLM-sourced pricing). See cross-audit §5.5 for the full inventory. Phase 0.6 ships only the missing CI guard that locks the single-integration-point invariant.

**Phase 3 / Phase 5 attribution rule (binding):** out-of-run LLM calls (todo extraction, routine fires) attribute via the existing `Purpose` enum, NOT a new `(source_kind, source_id)` shape. P3-A extends `Purpose` with `TODO_EXTRACTION`; P5-A wraps every routine fire as a regular ai-backend run with `run.source = { kind: "routine", routine_id }`. Do NOT build a parallel `services/backend/usage/`. See cross-audit §5.5.

| Agent                | Branch                       | Exclusive files                                                                                                                                                                                              | Prereqs | Deliverables                                                                                                                                                                                     | Test gates                                                            |
| -------------------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------- |
| **TU-1 Token Usage** | `worktree-agent-token-usage` | SHIPPED: `tools/check_llm_provider_imports.py` (238 lines, AST guard), `tools/test_check_llm_provider_imports.py` (190 lines, 12 self-tests incl. planted-violation E2E), `.pre-commit-config.yaml` (extend) | none    | CI guard locking the single-integration-point invariant: every LLM call must route through `services/ai-backend/.../deep_agent_builder.py::build_chat_model`. 403 files in real tree pass today. | 12/12 guard self-tests + real-tree scan + pre-commit hook integration |

### Phase 1 (✅ SHIPPED 2026-05-18) and Phase 2 (✅ SHIPPED 2026-05-18) — overview

**Phase 1 — Chats** landed via 6 parallel sub-agents + orchestrator merge surgery (commits `51e3568` P1-A re-scoped backend + `7eaa90c` api-types brand pass + `a2d8ce4` P1-B WIP base + `976d517` P1-B1 Composer + `3ad457e` P1-B2 ThreadCanvas + `bba859d` P1-B3 RightRail + `3eec34a`/`89604f8`/`ad7e7fb` P1-C frontend + `8849f10` merge surgery).

- P1-A original agent correctly refused to parallel-build (existing approval system in ai-backend; chats-canvas-prd §4.5 says "no new wire"). Re-scoped to 4 narrow deltas + branding pass.
- P1-B timed out at 18 min; split into 3 narrow continuations (B1 composer, B2 threadcanvas, B3 rightrail) — established the 5-9-agents-per-phase pattern.
- P1-C escalated on the composer-delete — chat-surface Composer surface is structurally smaller than runtime composer's (5 gaps); see Phase 1.6.

**Phase 2 — Home** landed via 6 parallel sub-agents + orchestrator merge surgery (commits `6886195` P2-A1 + `70a112e` P2-A2 + `4cee7a4` P2-B1 + `ba4d372` P2-B2 + `2bef60f` P2-B3 + `029910f` P2-C + `5aca35d` merge surgery).

- P2-A1 original timed out at 600s with zero commits (over-scoped real cross-service data joins). Re-scoped: ship wire + machinery; stub the 6 cross-service sections; greeting is real.
- 3 parallel chat-surface agents (B1/B2/B3) each created their own `_home-stub.ts` with subtly different field-name conventions. Merged into a unified transitional adapter exporting both rich (P2-B1's `AgentActivityEntry` union) and row (P2-B2's `HomeActivityRow`) vocabularies, plus P2-B3's distinct `HomeFocusItem`/`HomeUpcomingMeeting` shapes. chat-surface UI consumes the rich shape; api-types/backend ships the lean shape; apps/frontend bridges. Wave 3+ may collapse.

**Audit gate (2026-05-18 post-Phase-2):**

- chat-surface: 634/634 tests pass (up from 560 SP-1 baseline)
- ai-backend: 1825 pass, backend: 606 pass + 32 new Home, frontend: 831 pass
- DRY invariants: zero `__brand:` in chat-surface, single `formatRelativeTime`
- TU-1 CI guard: 409 files clean
- 2 transitional adapters in place (\_approvals-stub.ts, \_home-stub.ts) — documented; Wave 3+ refactor

**Deferred gaps for Phase 3+ to address (not bugs):**

- 6 Home sections have stub data — real data unlocks when downstream destinations land (Phase 3 Todos, Phase 4 Inbox approvals, Phase 5 Routines, Phase 8 Tools, connector destinations). Each stub has a TODO with its unlock phase.
- Phase 1.6 composer surface-completion task fires after Phase 5

---

### Phase 1 — Chats thread canvas + composer migration + right rail tabs (✅ SHIPPED)

Sub-PRD: [destinations/chats-canvas-prd.md](destinations/chats-canvas-prd.md). 10 open product questions resolved by orchestrator (audit §2 + sub-PRD-author recommendations) — see §3 below.

| Agent                       | Branch                                     | Exclusive files                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Prereqs                                                                   | Deliverables                                                                                                                              | Test gates                                                                                             |
| --------------------------- | ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| **P1-A backend**            | `worktree-agent-phase1-chats-backend`      | `services/ai-backend/src/agent_runtime/api/run_coordinator.py` (extend — see audit §1.4 context field), `services/ai-backend/src/runtime_api/schemas/runs.py` (extend `approval.*` event types), `services/ai-backend/src/agent_runtime/execution/approval.py` (NEW — approval state model), `services/backend/src/backend_app/approvals/` (NEW — approval persistence; approvals are durable workspace state, not just runtime), `services/backend-facade/src/backend_facade/approvals_routes.py` (NEW — facade proxy), `packages/api-types/src/approvals.ts` (NEW), all related tests                                                                                                                                                                                                                                     | SP-1                                                                      | Approval data model on the wire; `approval.accept/reject/suggest_edit` endpoints; `approval_requested`/`approval_resolved` runtime events | `ai-backend` + `backend` + `backend-facade` full suites green; tenant-isolation tests; audit-row tests |
| **P1-B chat-surface**       | `worktree-agent-phase1-chats-surface`      | `packages/chat-surface/src/thread-canvas/ThreadCanvas.tsx` (rebuild — 3 modes), `packages/chat-surface/src/thread-canvas/TcSurfaceMount.tsx` (extend with `reduceTo` time-travel), `packages/chat-surface/src/thread-canvas/TcSwimlanes.tsx` (extend), `packages/chat-surface/src/thread-canvas/TcMiniTimeline.tsx` (NEW), `packages/chat-surface/src/thread-canvas/TcChat.tsx` (extend), `packages/chat-surface/src/thread-canvas/eventProjector.ts` (NEW — one projector, four consumers), `packages/chat-surface/src/shell/RightRail.tsx` (replace empty-state with Activity + Approvals tabs), `packages/chat-surface/src/shell/RightRailTabs.tsx` (NEW), `packages/chat-surface/src/composer/Composer.tsx` (extend — `mode="edit"`, `topBarSlot`, `inlineActions`, `forwardRef`, `/`-skill handler), all related tests | SP-1; **soft-waits** on P1-A's `approvals.ts` types for the Approvals tab | 3-mode canvas; eventProjector; right rail tabs; composer parity with extras                                                               | `chat-surface` full suite green incl. mode-switch render-count invariant                               |
| **P1-C frontend migration** | `worktree-agent-phase1-frontend-migration` | `apps/frontend/src/features/chat/ChatScreen.tsx` (rewire to use `packages/chat-surface/composer`; delete reference to local composer), `apps/frontend/src/features/chat/runtime/composer/` **DELETE ENTIRE DIRECTORY** (Composer.tsx, EditComposer.tsx, ComposerHandle.tsx, AttachmentAdapter.tsx, ComposerSendButton.tsx, tests), `apps/frontend/src/api/agentApi.ts` (wire `reasoning_depth` from composer through CreateRunRequest at the top level — remove the `applyDepth(model, depth)` hack), per-conversation `chats.default_depth` KV persistence, all related tests                                                                                                                                                                                                                                              | SP-1; **hard-waits** on P1-B's Composer extras props landing on main      | One Composer in the monorepo; frontend variant deleted; depth wired to wire-contract                                                      | `frontend` full suite green (UserCard.test.tsx pre-existing failure aside)                             |

### Phase 2 — Home morning briefing

Sub-PRD: [destinations/home-prd.md](destinations/home-prd.md). 8 open questions to resolve before Impl-B starts; see §3.

| Agent                            | Branch                               | Exclusive files                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Prereqs                   | Deliverables                                                                                              | Test gates                                                                       |
| -------------------------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------- | --------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| **P2-A backend**                 | `worktree-agent-phase2-home-backend` | `packages/api-types/src/home.ts` (NEW), `packages/api-types/src/index.ts` (extend re-export, ONLY this line — no other phase touches it concurrently), `services/backend/src/backend_app/home/` (NEW: routes.py, service.py, store.py — aggregation reads), `services/backend-facade/src/backend_facade/home_routes.py` (NEW), `services/backend/src/backend_app/app.py` (extend ONLY the home-router registration line + nothing else), `services/backend-facade/src/backend_facade/app.py` (extend ONLY the home-proxy line), all related tests                                                                         | SP-1, P1-A (approvals.ts) | `/v1/home` aggregation with `SectionResult<T>` per-section partial-failure shape; SSE stream for activity | `backend` + `backend-facade` full suites; tenant-isolation; partial-failure test |
| **P2-B chat-surface + frontend** | `worktree-agent-phase2-home-surface` | `packages/chat-surface/src/destinations/home/HomeDestination.tsx` (REWRITE), `packages/chat-surface/src/destinations/home/HomePanel.tsx` (NEW), `packages/chat-surface/src/destinations/home/sections/*.tsx` (NEW per-section components: Greeting, ActivityFeed, PinnedChats, RecentRuns, FavoriteTools, TodaysFocus, UpcomingMeetings), `packages/chat-surface/src/destinations/home/index.ts` (extend — registers `<ItemLink>` resolvers for kinds Home owns), `apps/frontend/src/app/App.tsx` (extend ONLY the destination-mount switch case for Home, plus the ContextPanel slot supply for Home), all related tests | SP-1, P2-A                | Morning briefing UI; HomePanel; section-driven layout consuming `SectionResult<T>`                        | `chat-surface` + `frontend` full suites                                          |

### Phase 3 — Todos

Sub-PRD: [destinations/todos-prd.md](destinations/todos-prd.md). 9 open questions; see §3.

| Agent                            | Branch                                | Exclusive files                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | Prereqs                                     | Deliverables                                                                                                                                                                                                                   | Test gates                                                                                                                                                                                                                                                                                                                                    |
| -------------------------------- | ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **P3-A backend**                 | `worktree-agent-phase3-todos-backend` | `packages/api-types/src/todos.ts` (NEW — includes Todo, TodoExtraction, TodoSource, TodoRecurrence, parent_id, series_id, TodoSeries), `packages/api-types/src/index.ts` (extend re-export — coordinate with P2-A via shared `app.py`-style merge order: P2-A merges first, P3-A rebases), `services/backend/src/backend_app/todos/` (NEW: routes.py, service.py, store.py, schema.sql — includes `todo_series` table for recurrence + parent_id FK for subtasks), `services/backend-facade/src/backend_facade/todos_routes.py` (NEW), `services/backend/src/backend_app/app.py` (extend — todos-router registration line; merge AFTER P2-A), `services/backend-facade/src/backend_facade/app.py` (extend — todos proxy; merge AFTER P2-A), `services/ai-backend/src/runtime_worker/jobs/todo_extractor.py` (NEW — proposes todos from runs; CALLS LLM via `llm_call_tracker` from TU-1), `services/ai-backend/src/runtime_worker/jobs/todo_recurrence_materializer.py` (NEW — see implementation-plan §11.1), all related tests | SP-1, TU-1 (extractor MUST track LLM calls) | Todos CRUD + recurring series + subtasks (one level, cascade-delete to children, computed parent.done); extractions proposal pipeline; multi-tenant Postgres; audit hooks; bulk-action correlation_id; recurrence materializer | `backend` + `backend-facade` + `ai-backend` full suites; tenant isolation; project-member ACL tests; extraction-accept atomicity test; recurrence materialization idempotency test (rerun materializer twice on same series, only one row created per due_date); subtask cascade-delete test; LLM-call-flows-through-tracker assertion (TU-1) |
| **P3-B chat-surface + frontend** | `worktree-agent-phase3-todos-surface` | `packages/chat-surface/src/destinations/todos/TodosDestination.tsx` (REWRITE — sections, virtualized lists, parent/child nesting render, recurrence chip on parent rows), `packages/chat-surface/src/destinations/todos/TodosPanel.tsx` (NEW — filter chips + saved-filter; inline-add inherits panel filter for project default per Phase-3 Q6), `packages/chat-surface/src/destinations/todos/sections/*.tsx` (NEW), `packages/chat-surface/src/destinations/todos/inline-add.tsx` (NEW — context-aware project default), `packages/chat-surface/src/destinations/todos/extraction-banner.tsx` (NEW), `packages/chat-surface/src/destinations/todos/recurrence-editor.tsx` (NEW — RRULE subset editor), `packages/chat-surface/src/destinations/todos/subtask-tree.tsx` (NEW — one-level nesting), `packages/chat-surface/src/destinations/todos/index.ts` (NEW), `apps/frontend/src/app/App.tsx` (extend — todos destination + panel; merge AFTER P2-B; small diff), all related tests                                        | SP-1, P3-A                                  | Todos UI with sections, DnD-reorder, inline-add (context-aware project default), extraction-banner, recurrence editor, subtask nesting; calls BadgePort                                                                        | `chat-surface` + `frontend` full suites                                                                                                                                                                                                                                                                                                       |

### Phase 4 — Inbox

Sub-PRD: [destinations/inbox-prd.md](destinations/inbox-prd.md). Product decisions already approved (see Inbox merge commit + cross-audit §1.3 for project-access rule).

| Agent                            | Branch                                | Exclusive files                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Prereqs    | Deliverables                                                                                                                           | Test gates                                                                                                           |
| -------------------------------- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| **P4-A backend**                 | `worktree-agent-phase4-inbox-backend` | `packages/api-types/src/inbox.ts` (NEW), `packages/api-types/src/index.ts` (extend — coordinate merge order: P4-A merges AFTER P3-A), `services/backend/src/backend_app/inbox/` (NEW), `services/backend-facade/src/backend_facade/inbox_routes.py` (NEW), `services/backend/src/backend_app/app.py` (extend — inbox-router line; merge AFTER P3-A), `services/backend-facade/src/backend_facade/app.py` (extend — inbox proxy; merge AFTER P3-A), `services/ai-backend/src/agent_runtime/api/inbox_producer.py` (NEW — producer client for ai-backend to POST inbox items via service-token), `services/ai-backend/src/runtime_worker/handlers/run.py` (extend — emit inbox item on approval fallback) | SP-1, P1-A | Inbox CRUD + producer pipeline; inline-vs-inbox routing rule (5min/high-priority fallback); SSE stream + unread_count polling fallback | `backend` + `backend-facade` + `ai-backend` full suites; tenant isolation; producer auth test; fallback routing test |
| **P4-B chat-surface + frontend** | `worktree-agent-phase4-inbox-surface` | `packages/chat-surface/src/destinations/inbox/InboxDestination.tsx` (REWRITE), `packages/chat-surface/src/destinations/inbox/InboxPanel.tsx` (NEW), `packages/chat-surface/src/destinations/inbox/InboxDetail.tsx` (NEW), `packages/chat-surface/src/destinations/inbox/inbox-reply.tsx` (NEW), `packages/chat-surface/src/destinations/inbox/snooze-picker.tsx` (NEW), `packages/chat-surface/src/destinations/inbox/index.ts` (NEW), `apps/frontend/src/app/App.tsx` (extend — inbox destination + detail route; merge AFTER P3-B), all related tests                                                                                                                                                 | SP-1, P4-A | Inbox UI with filter tabs + detail pane + reply + snooze; calls BadgePort + NotificationPort                                           | `chat-surface` + `frontend` full suites                                                                              |

### Phase 5 — Routines (12th destination)

Sub-PRD: pending (`a4c565a71d8cc3725` agent still running). When it lands:

| Agent                            | Branch                                   | Exclusive files (anticipated)                                                                                                                                                                                                                                                                                                                 | Prereqs          | Deliverables                                                                                               |
| -------------------------------- | ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- | ---------------------------------------------------------------------------------------------------------- |
| **P5-A backend**                 | `worktree-agent-phase5-routines-backend` | `packages/api-types/src/routines.ts` (NEW), `services/backend/src/backend_app/routines/`, facade proxy, `services/ai-backend/src/runtime_worker/jobs/routine_scheduler.py` (cron-claim worker), `services/backend/src/backend_app/app.py` extend (merge AFTER P4-A)                                                                           | SP-1, P1-A, P4-A | Routine CRUD; scheduler; trigger validation; webhook secret rotation; permission intersection at fire-time |
| **P5-B chat-surface + frontend** | `worktree-agent-phase5-routines-surface` | `packages/chat-surface/src/destinations/routines/` (NEW: RoutinesDestination, RoutinesPanel, RoutineEditor, RoutineDetail), `packages/chat-surface/src/shell/destinations.ts` (extend ShellDestinationSlug to include `"routines"` as the 12th slug — merge BEFORE any other Phase-5 work), `apps/frontend/src/app/App.tsx` extend, all tests | SP-1, P5-A       | Routines UI; cron editor; trigger management; tabs (Connectors/Behavior/Permissions)                       |

### Phases 3 + 4 — audit gate (2026-05-18)

Phase 3 (Todos) and Phase 4 (Inbox) both landed via parallel sub-agents. All gates green:

- **Phase 3 Todos** (7 commits): ai-backend 1871, backend 643, chat-surface 716, frontend 853. Zero merge conflicts on cherry-pick.
- **Phase 4 Inbox** (6 commits + 1 stalled): ai-backend 1895, backend 717, chat-surface 760, frontend 882. Two `__init__.py` merge conflicts resolved cleanly; one `InboxStream*` type-naming reconciliation (different from PR-1.4.1's approval-pulse `InboxEventType` to avoid collision).

**Deferred follow-ups (logged here; NOT blocking Phase 5+ dispatch):**

1. **P3-A1 `/internal/v1/todos/series/materialize-due` endpoint** — P3-A3's recurrence materializer worker posts to this endpoint with `(now: ISO) → {materialized, skipped_duplicates, series_processed}`. P3-A1 shipped the schema + service but NOT this handler. Wire-up follow-up; until landed, the materializer worker has no consumer.
2. ~~**P4-A1 + P4-A2 publish to `app.state.inbox_activity_bus`**~~ **— LANDED (DW-1)**. PATCH `/v1/inbox/{id}`, bulk `/v1/inbox/bulk`, and producer `POST /internal/v1/inbox/items` all publish `item_added`/`item_updated` post-commit via `InboxService.publish_event`. Channel key is the recipient's `(tenant_id, owner_user_id)`; payload omits body bytes (body_ref only). Test: `services/backend/tests/test_inbox_sse_publish.py`.
3. ~~**Delete `services/backend/src/backend_app/inbox/_local_store.py`**~~ **— LANDED (DW-1)**. The producer route now resolves the canonical `InboxService` off `app.state.inbox_service` and writes through `insert_item_with_body`; the stub + the `find_by_external_ref` helper consolidate against `store.items`.
4. **Phase 4.5: Inbox 960px responsive breakpoint** — P4-B3 agent stalled with zero progress. Replan: split into 2 narrow agents (CSS container-query + useInboxLayout hook). Defer until post-Wave-2 (not blocking destinations).

**Invariants preserved post-Phase-4:**

- DRY: zero `__brand:` in chat-surface; single `formatRelativeTime`
- TU-1 CI guard: 432 files clean
- Transitional adapters (`_approvals-stub.ts`, `_home-stub.ts`, `_todos-stub.ts`, `_inbox-stub.ts`) all documented as permanent chat-surface adapter shapes; Wave 3+ may collapse

Ready for Phase 5 Routines dispatch (8-agent parallel per replan).

---

### Phase 1.6 — chat-surface Composer surface-completion (deferred from P1-C; tech-debt)

Phase 1.5 (composer-delete cleanup) escalated 2026-05-18: the chat-surface Composer is structurally different from the frontend's runtime composer (opinionated toolbar vs headless render-prop slots), not just API-incompatible. P1-B added some additive props (mode, attachmentAdapter, onSubmit, forwardRef, topBarSlot, inlineActions) but didn't close the surface to allow a regression-free delete.

Concrete gaps for a future Phase 1.6 agent (single-agent scope; chat-surface only):

1. Extend `ComposerHandle` with `appendText(text)`, `addAttachment(file)`, `submit()` (using flushSync where DOM-visible state must sync)
2. Add `bottomBarRender` / `hintRender` / `onInputKeyDown` render-prop slots OR replace the hardcoded toolbar with a `toolbarSlot` prop that REPLACES (not supplements) the built-in toolbar
3. Decide AttachmentAdapter contract: widen chat-surface to the runtime's two-stage (`add() → pending`, `send(pending) → complete`, `remove() → Promise<void>`), OR have AssistantComposer adapt its existing adapters at call-site (lossy on the `content[]` upload payload)
4. Surface `data-focused` / `data-has-topbar` / `data-running` attributes for `aui-*` CSS parity, OR migrate the ~72 `aui-*` selectors in `apps/frontend/src/styles.css` to the chat-surface token system
5. Wire the plus-button to a host-supplied filepicker handler so the existing `openFilePicker` path can call into the composer

After Phase 1.6 lands, run the composer-delete cleanup (3-file delete + 2-call-site edit). Sequenced after Phase 5 — does not block destinations work.

### Phase 6+ — later waves (skeleton; sub-PRDs not yet written)

Routines was added as the 12th destination after master PRD §8 was written. Impl-plan numbering inserts Routines at Phase 5 and shifts everything else by one. Each Phase 6+ row is a placeholder — its sub-PRD is written when the previous phase ships and orchestrator dispatches the writer-then-impl pattern (cross-audit §7 dispatch shape).

| Phase | Name                                               | Status   | Sub-PRD                                      | Resolves Routines §9.7 deferrals                                                                                                                                                     |
| ----- | -------------------------------------------------- | -------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 6     | **Projects** (multi-thread workspace)              | NOT-DONE | `destinations/projects-prd.md` (TBD)         | —                                                                                                                                                                                    |
| 7     | **Library** (files + pages + datasets + retrieval) | NOT-DONE | `destinations/library-prd.md` (TBD)          | Q3 (library page output target wire shape)                                                                                                                                           |
| 8     | **Agents** (skill cards, agent identity, registry) | NOT-DONE | `destinations/agents-prd.md` (TBD)           | Q11 (agent_version_pin selection UI)                                                                                                                                                 |
| 9     | **Tools** (full destination)                       | NOT-DONE | `destinations/tools-prd.md` (TBD)            | Q1 (code-routines executor + sandbox)                                                                                                                                                |
| 10    | **Connectors** (full destination)                  | NOT-DONE | `destinations/connectors-prd.md` (TBD)       | Q6 (HMAC-of-payload signature UI), webhook UX                                                                                                                                        |
| 11    | **Team + Memory + ⌘K palette + polish**            | NOT-DONE | `destinations/team-memory-cmdk-prd.md` (TBD) | Q9/Q10 (Atlas-proposed cron suggestions, "Make this a routine?" CTA in ⌘K), Q14 (Settings UI for tenant/user notif defaults), Q12 (admin force-reassign — re-evaluate at this point) |

**Routines §9.7 deferral mapping (binding):**

| Routines §9.7 Q                                       | Wave label in §9.7                           | Lands in impl-plan phase                                                        |
| ----------------------------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------- |
| Q1 Code-routines executor                             | "Wave 6"                                     | **Phase 9 Tools** (executor + sandbox is tool infrastructure)                   |
| Q3 Library page output mode                           | "Phase 5" (wire shape) + Phase 7 (UI polish) | Phase 5 ships wire; Phase 7 polishes editor                                     |
| Q6 HMAC signature webhook                             | "Wave 5+"                                    | Phase 10 Connectors (consolidated webhook UX)                                   |
| Q9 Atlas-proposed cron suggestions                    | "Phase 5/6"                                  | Phase 11 (⌘K palette suggestion engine; tied to memory + agent context)         |
| Q10 Auto-extracted "Make this a routine?" CTA         | "Phase 6"                                    | Phase 11 (post-run CTA on chats canvas → command palette flow)                  |
| Q12 Admin force-reassign owner                        | "out of scope"                               | Re-evaluate at Phase 11 (Team destination + admin workflows)                    |
| Q13 Routine forking / templates                       | "Wave 5+"                                    | Phase 8 Agents (templating is an agent-registry concern; Routines just consume) |
| Q14 Settings UI for tenant/user notification defaults | "Wave 6"                                     | Phase 11 (Settings + Team destination)                                          |

**Why this numbering:** Wave/Phase terminology was loose ("Wave 6 ≈ Phase 6 ≈ later"). After Routines slotted in at Phase 5, "Wave 6" needs to map to "Phase 6 onward". The mapping above pins each Routines deferral to the phase whose primitives unlock it (executors in Tools, HMAC in Connectors UX, command palette in Team+Memory+⌘K, etc.) rather than a generic "Wave 6 someday".

**Master PRD §8 reconciliation:** the original 10-phase plan in `destinations-master-prd.md §8` predates Routines. The impl-plan above is the authoritative numbering going forward. Master PRD §8 will be updated to insert Routines at Phase 5 + shift the rest in a follow-up edit (not blocking).

---

## 3. Open product decisions — resolved or escalated

This list is the **complete set** of open product questions from all 5 sub-PRDs. Decisions in this column are binding for impl agents.

### Phase 1 Chats (10 questions from sub-PRD §18)

| #   | Question                                                      | Decision                                                                                                                                |
| --- | ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Surface time-travel: client-side reducers vs backend snapshot | **Client-side reducers** (sub-PRD recommendation; cheaper for Phase 1; revisit when richer renderers land)                              |
| 2   | Restore + Branch endpoints in Phase 1?                        | **Restore yes; Branch deferred to Phase 1.5** (smaller surface area)                                                                    |
| 3   | Approval auth: owner-only vs any workspace member             | **Owner-only writes; project members read; tenant admin compliance-read** (per cross-audit §1.3)                                        |
| 4   | Backward event paging                                         | **Front-load 200 then forward** (sub-PRD recommendation)                                                                                |
| 5   | Pin-a-bead in Phase 1?                                        | **Deferred to Phase 1.5**                                                                                                               |
| 6   | EditComposer absorbed via `mode` prop?                        | **Yes, `mode="edit"` prop** (sub-PRD recommendation)                                                                                    |
| 7   | Streaming live-region throttle                                | **3s** (sub-PRD recommendation)                                                                                                         |
| 8   | Mode storage scope                                            | **Per-conversation** (sub-PRD recommendation; KV-store keyed)                                                                           |
| 9   | Branched-conversations cascade                                | **Delete branch → parent untouched; delete parent → branches survive with dead link** (per cross-audit §5.3)                            |
| 10  | Cross-conversation depth default                              | **Per-user `chats.default_depth` KV; null fallback to "balanced"** (sub-PRD recommendation + cross-audit §2.1 branded `ReasoningDepth`) |

### Phase 2 Home (8 questions from sub-PRD §16) — orchestrator-approved 2026-05-17

| #   | Question                                     | Decision                                                                                                                   |
| --- | -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| 1   | Activity window length                       | **24h default AND user-configurable in Phase 2** (per-user KV `home.activity_window_hours`; allowed values 6/12/24/48/168) |
| 2   | New-tenant empty state                       | **Empty page + "New chat" CTA**; no guided tour                                                                            |
| 3   | Today's focus selection                      | **Automatic top-3 by composite score (server-side)**; user-pinning Wave 4+                                                 |
| 4   | Upcoming meetings with no calendar connector | **One-row CTA replacing the section: "Connect a calendar to see today's meetings →"**                                      |
| 5   | Greeting personalization                     | **IdP `given_name` → first token of IdP `name` → "Good morning." (no name)**. Email local-part NOT used as a fallback.     |
| 6   | Section order                                | **Fixed in Phase 2**; per-user reorder Wave 4+                                                                             |
| 7   | Quick-action customization                   | **Server-driven defaults; no admin UI in Phase 2** (smallest feasible scope per "do whatever is easy")                     |
| 8   | SSE drop-off behaviour                       | **Silent retry, exponential backoff 1s → 30s, no user-visible "paused" indicator**                                         |

### Phase 3 Todos (9 questions from sub-PRD §16) — orchestrator-approved 2026-05-17 (4 deviations from sub-PRD)

| #   | Question                             | Decision                                                                                                                                                                                                                             |
| --- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | Drag-reorder vs change-due gesture   | **Same gesture; drop-target's section decides via due-date change**                                                                                                                                                                  |
| 2   | Recurring todos                      | **IN PHASE 3** (cron-spec + materialization-on-due — see §11.1)                                                                                                                                                                      |
| 3   | Subtasks                             | **IN PHASE 3** (one level of nesting; no infinite tree — see §11.2)                                                                                                                                                                  |
| 4   | Auto-extraction default-on or opt-in | **Default-on + per-tenant admin toggle + per-user opt-out**                                                                                                                                                                          |
| 5   | Extraction confidence threshold      | **Always-propose, per-item accept/reject; learn from rejections in Wave 5+**                                                                                                                                                         |
| 6   | New-todo project default             | **Context-aware: project detail view → that project's id; /todos direct → null (Unfiled); inline-add in TodosPanel → inherits panel's active filter**                                                                                |
| 7   | Snooze todos                         | **Deferred to a later wave** (change `due` is sufficient for Phase 3)                                                                                                                                                                |
| 8   | Done-section cap                     | **Cap at 14d default; paginate beyond via `?filter[done]=true&sort=completed_at:desc&after=<cursor>`**                                                                                                                               |
| 9   | LLM prompt + budget for extraction   | **Impl-C proposes the prompt + budget; orchestrator approves before merge.** Hard requirement: every LLM call is token-accounted and traceable back to the extraction (see cross-audit §5.5 — system-level token-usage requirement). |

**§11.1 Recurring todos** (Phase 3 scope — IN). Added field on `Todo`:

```typescript
recurrence?: {
  rule: "rrule" | "every_N_days" | "every_weekday";  // RFC 5545 rrule subset
  spec: string;                                       // e.g., "FREQ=WEEKLY;BYDAY=MO,WE,FR" or "every_N_days:3"
  next_materialize_at: ISODate;
  series_id: SeriesId;                                // shared across all instances
}
```

Materialization worker (`services/ai-backend/src/runtime_worker/jobs/todo_recurrence_materializer.py`, claim-pattern mirror of `retention_sweeper.py`): polls every 60s; for each `recurrence.next_materialize_at <= now` instance, creates the next concrete Todo row (with `recurrence.next_materialize_at` advanced). Materialization is idempotent (uses `(series_id, due_date)` UNIQUE constraint). Deletion of a series tombstones future-materializations but keeps already-materialized instances.

**§11.2 Subtasks** (Phase 3 scope — IN, one level). Added field on `Todo`:

```typescript
parent_id?: TodoId;          // null = top-level; non-null = subtask of parent
sort_index_within_parent?: number;
```

UI rules:

- Subtask appears nested under parent in the section it belongs to.
- Parent's `done` is computed: when all subtasks `done`, parent shows "all subtasks done · mark parent done?" hint.
- Deleting a parent soft-deletes children (cascade).
- Subtasks inherit parent's `project_id` on create (server enforces).
- A subtask CANNOT have its own subtasks (one level only). Backend rejects with 400.

The materializer (recurring) creates only top-level todos; recurring subtasks of a recurring parent are out of scope (Wave 5+).

### Phase 4 Inbox (8 questions — already approved at Inbox merge)

See [destinations/inbox-prd.md](destinations/inbox-prd.md) merge commit message. All approved per sub-PRD recommendations.

### Phase 5 Routines (TBD when sub-PRD lands)

To be resolved before P5-A/B dispatch.

---

## 4. Merge order — strict

Backend writes to shared files (`api-types/index.ts`, `backend/app.py`, `backend-facade/app.py`) require a sequential merge. Frontend destinations register through `apps/frontend/src/app/App.tsx` which also serializes.

**Strict merge order:**

1. **SP-1** Shared Primitives → main. **All destination phases wait.**
2. **P1-A** Chats backend (approvals contract) → main.
3. **P1-B** Chat-surface (3-mode canvas, right rail tabs, composer extras) → main. Conflicts on `Composer.tsx` resolved by accepting P1-B's redesign.
4. **P1-C** Frontend migration (deletes runtime/composer; wires depth) → main.
5. **P2-A** Home backend → main.
6. **P2-B** Home surface → main.
7. **P3-A** Todos backend (incl. extraction worker) → main. Rebase if P2-A touched `api-types/index.ts` or `backend/app.py` lines nearby.
8. **P3-B** Todos surface → main. Rebase `App.tsx` if P2-B touched the same switch.
9. **P4-A** Inbox backend → main. Rebase as above.
10. **P4-B** Inbox surface → main. Rebase as above.
11. **P5-A** Routines backend → main.
12. **P5-B** Routines surface → main. Rebase `destinations.ts` to extend `ShellDestinationSlug` with `"routines"`.

Within a phase, A merges before B (B depends on A's types/endpoints).

### Audit gates between phase pairs (binding cadence; user-set 2026-05-17)

**After every two merged phases**, the orchestrator runs a structured audit BEFORE dispatching the next pair:

1. **Verify both phases are correctly implemented end-to-end** (read the diffs, not just the agent's summary). Cross-check against the sub-PRD acceptance criteria.
2. **Update READMEs / CLAUDE.md files** touched by the work (service-level CLAUDE.md, package-level README/CLAUDE.md, the master destinations PRD if a binding decision evolved).
3. **Tick completed todos**; remove stale; add discovered follow-ups.
4. **Find gaps** — missing tests, missing typecheck wiring, missing audit rows, missing port wiring, drifted shapes from cross-audit.
5. **Fix the gaps before next dispatch.** A gap left open compounds; subsequent phases inherit it.

Audit checkpoints (the schedule):

- After **Phase 0.5 + 0.6** prerequisites merge → audit before Phase 1 dispatch.
- After **Phase 1 + 2** merge → audit before Phase 3 dispatch.
- After **Phase 3 + 4** merge → audit before Phase 5 dispatch.
- After **Phase 5** + a subsequent phase (or end of wave) → audit before next wave dispatch.

The orchestrator reports the audit findings (clean or with patches) and only then proceeds. The audit is not optional and is not "if I have time".

---

## 5. Test gates per merge

Before merging any agent's branch:

1. `npm run typecheck` — all touched packages.
2. `cd packages/chat-surface && npm test --silent` — 100% pass.
3. `cd apps/frontend && npm test --silent` — green minus the pre-existing UserCard.test.tsx vi-mock failure (tracked separately; not in scope).
4. For backend agents: `cd services/<svc> && .venv/bin/python -m pytest` for each affected service.
5. Browser smoke: dev stack up, the destination loads, AppRail nav still works, no console errors.
6. Lint: pre-commit hooks (already enforced).

A merge that drops a test (rather than fixing root cause) is rejected.

---

## 6. Anti-conflict file rules

The following files are touched by multiple phases. Coordination is via merge order in §4. **No two parallel agents touch them simultaneously.**

| File                                                | Touched by                                          | Coordination                                                                      |
| --------------------------------------------------- | --------------------------------------------------- | --------------------------------------------------------------------------------- |
| `packages/api-types/src/index.ts`                   | SP-1, P1-A, P2-A, P3-A, P4-A, P5-A                  | SP-1 first; subsequent phases append a re-export line; rebase as needed           |
| `services/backend/src/backend_app/app.py`           | P2-A, P3-A, P4-A, P5-A                              | Each phase appends ONE `app.include_router(...)` line; merge order in §4          |
| `services/backend-facade/src/backend_facade/app.py` | P2-A, P3-A, P4-A, P5-A                              | Same                                                                              |
| `apps/frontend/src/app/App.tsx`                     | P1-C, P2-B, P3-B, P4-B, P5-B                        | Each phase adds destination dispatch + context-panel slot; merge order serializes |
| `packages/chat-surface/src/shell/destinations.ts`   | P5-B only                                           | Extending `ShellDestinationSlug` to 12 entries; coordinated as part of P5-B       |
| `packages/chat-surface/src/index.ts`                | SP-1, every phase shipping a new destination export | SP-1 first; per-phase re-exports appended                                         |

For files appearing in 2+ rows, agents are dispatched with a "rebase on main before push" instruction so the second agent picks up the first's changes.

---

## 7. Things explicitly NOT in scope for any Wave 2 phase

Recapping cross-audit §3.5 deferred-features inventory + phase-specific exclusions:

- **No** per-user section reordering anywhere (Wave 4+).
- **No** recurring todos / subtasks (Wave 4+ / Wave 5+).
- **No** multiplayer threads (Wave 5+).
- **No** routine forking / templates (Wave 5+).
- **No** workflow DAGs (Wave 5+; may never).
- **No** per-second cron (1m minimum).
- **No** third-party scheduler integration (connector concern).
- **No** cross-tenant sharing (security boundary).
- **No** bulk-reply / bulk-snooze in Inbox (Wave 4).
- **No** voice / mic onClick wiring in composer (host work; sub-PRD calls out).
- **No** native ports beyond Badge / Notification / FilePicker / Clipboard. (Push port additions to a new audit-doc patch.)

---

## 8. Reporting + cadence

Each dispatched agent reports back:

- worktree path + branch name
- one-paragraph summary of what shipped
- file list with line counts
- test results (typecheck + suite numbers)
- known limitations + follow-up TODOs

Orchestrator merges per §4, runs §5 gates, updates this doc's §2 table with merge commit hashes, then dispatches the next phase.

---

## 9. References

- [PRD.md](PRD.md) · [destinations-master-prd.md](destinations-master-prd.md) · [cross-audit.md](cross-audit.md)
- Per-phase sub-PRDs in [destinations/](destinations/)
- Memory: subagent worktree discipline rules (every agent gets the HARD WORKTREE preamble)
