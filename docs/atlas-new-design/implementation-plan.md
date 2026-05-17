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

### Phase 0.5 — Shared primitives (PREREQUISITE; blocks all destination work)

| Agent                      | Branch                             | Exclusive files                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Prereqs | Deliverables                                                                                                 | Test gates                                                             |
| -------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------- | ------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| **SP-1 Shared Primitives** | `worktree-agent-shared-primitives` | `packages/api-types/src/refs.ts` (NEW), `packages/api-types/src/brands.ts` (NEW), `packages/api-types/src/index.ts` (extend re-exports), `packages/chat-surface/src/refs/registry.ts` (NEW), `packages/chat-surface/src/refs/ItemLink.tsx` (NEW), `packages/chat-surface/src/refs/index.ts` (NEW), `packages/chat-surface/src/shell/PageHeader.tsx` (NEW), `packages/chat-surface/src/shell/FilterTabs.tsx` (NEW), `packages/chat-surface/src/shell/StatusPill.tsx` (NEW), `packages/chat-surface/src/shell/EmptyState.tsx` (NEW), `packages/chat-surface/src/shell/CardGrid.tsx` (NEW), `packages/chat-surface/src/shell/DocList.tsx` (NEW), `packages/chat-surface/src/shell/ActivityList.tsx` (NEW), `packages/chat-surface/src/util/time.ts` (NEW), `packages/chat-surface/src/ports/BadgePort.ts` (NEW), `packages/chat-surface/src/ports/NotificationPort.ts` (NEW), `packages/chat-surface/src/ports/FilePickerPort.ts` (NEW), `packages/chat-surface/src/ports/ClipboardPort.ts` (NEW), `packages/chat-surface/src/ports/index.ts` (extend), `packages/chat-surface/src/destinations/home/HomeDestination.tsx` (one-line migration to import `formatRelativeTime` from new location), all tests for the new primitives | none    | `ItemRef` + `ItemLink` registry + every shell primitive + 4 ports + branded IDs + `formatRelativeTime` hoist | `chat-surface` 100% green; `api-types` typecheck; `frontend` typecheck |

### Phase 1 — Chats thread canvas + composer migration + right rail tabs

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

| Agent                            | Branch                                | Exclusive files                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Prereqs    | Deliverables                                                                                              | Test gates                                                                                                                            |
| -------------------------------- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| **P3-A backend**                 | `worktree-agent-phase3-todos-backend` | `packages/api-types/src/todos.ts` (NEW), `packages/api-types/src/index.ts` (extend re-export — coordinate with P2-A via shared `app.py`-style merge order: P2-A merges first, P3-A rebases), `services/backend/src/backend_app/todos/` (NEW: routes.py, service.py, store.py, schema.sql), `services/backend-facade/src/backend_facade/todos_routes.py` (NEW), `services/backend/src/backend_app/app.py` (extend — todos-router registration line; merge AFTER P2-A), `services/backend-facade/src/backend_facade/app.py` (extend — todos proxy; merge AFTER P2-A), `services/ai-backend/src/runtime_worker/jobs/todo_extractor.py` (NEW — proposes todos from runs), `services/ai-backend/src/agent_runtime/...` (small extension for extractor's claim pattern), all related tests | SP-1       | Todos CRUD; extractions proposal pipeline; multi-tenant Postgres; audit hooks; bulk-action correlation_id | `backend` + `backend-facade` + `ai-backend` full suites; tenant isolation; project-member ACL tests; extraction-accept atomicity test |
| **P3-B chat-surface + frontend** | `worktree-agent-phase3-todos-surface` | `packages/chat-surface/src/destinations/todos/TodosDestination.tsx` (REWRITE), `packages/chat-surface/src/destinations/todos/TodosPanel.tsx` (NEW), `packages/chat-surface/src/destinations/todos/sections/*.tsx` (NEW), `packages/chat-surface/src/destinations/todos/inline-add.tsx` (NEW), `packages/chat-surface/src/destinations/todos/extraction-banner.tsx` (NEW), `packages/chat-surface/src/destinations/todos/index.ts` (NEW), `apps/frontend/src/app/App.tsx` (extend — todos destination + panel; merge AFTER P2-B; small diff), all related tests                                                                                                                                                                                                                       | SP-1, P3-A | Todos UI with sections + DnD-reorder + inline-add + extraction-banner; calls BadgePort                    | `chat-surface` + `frontend` full suites                                                                                               |

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

### Phase 2 Home (8 questions from sub-PRD §16)

| #   | Question                                     | Decision                                                                                  |
| --- | -------------------------------------------- | ----------------------------------------------------------------------------------------- |
| 1   | Activity window length                       | **24h default; user-configurable Wave 4+**                                                |
| 2   | New-tenant empty state                       | **Empty page + "New chat" CTA**; tour deferred                                            |
| 3   | Today's focus selection                      | **Automatic top-3 by composite score (server-side)**; user-pinning Wave 4+                |
| 4   | Upcoming meetings with no calendar connector | **Replace section with "Connect a calendar →" CTA**                                       |
| 5   | Greeting personalization                     | **IdP `given_name` → first token of `name` → email local-part → "" (never 'Atlas user')** |
| 6   | Section order                                | **Fixed in Phase 2; per-user reorder Wave 4+**                                            |
| 7   | Quick-action customization                   | **Server-driven defaults; admin UI Wave 5+**                                              |
| 8   | SSE drop-off behaviour                       | **Silent retry with exponential backoff**                                                 |

### Phase 3 Todos (9 questions from sub-PRD §16)

| #   | Question                             | Decision                                                                                                                     |
| --- | ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| 1   | Drag-reorder vs change-due gesture   | **Same gesture; drop-target's section decides via due-date change**                                                          |
| 2   | Recurring todos                      | **Wave 4** (deferred)                                                                                                        |
| 3   | Subtasks                             | **Wave 5** (deferred)                                                                                                        |
| 4   | Auto-extraction default-on or opt-in | **Default-on + per-tenant admin toggle + per-user opt-out**                                                                  |
| 5   | Extraction confidence threshold      | **Always-propose, per-item accept/reject; learn from rejections in Wave 5+**                                                 |
| 6   | New-todo project default             | **Current project context if applicable; else "Unfiled"**                                                                    |
| 7   | Snooze todos                         | **Wave 4** (change `due` is sufficient for Phase 3)                                                                          |
| 8   | Done-section cap                     | **Cap at 14d default; paginate beyond**                                                                                      |
| 9   | LLM prompt + budget for extraction   | **Cheapest reasoning-tier model, ≤8 candidates per run, ≤2K input tokens, oldest-first truncation** (sub-PRD recommendation) |

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
