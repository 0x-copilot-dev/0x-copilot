# Home — sub-PRD (Phase 2)

**Status:** draft (2026-05-17)
**Owner:** parth (orchestrator) — sub-PRD by the Phase 2 dispatch agent
**Parents:** [PRD.md](../PRD.md) · [destinations-master-prd.md](../destinations-master-prd.md) (§3 enterprise checklist, §4 shared primitives, §5.1 Home)
**Design source of truth:** `/tmp/atlas-design/enterprise-search-template/project/dest-home.jsx` (HomeMain + HomePanel), `os-app.jsx:140-147` (how they mount side-by-side), `os-data.jsx` (cross-destination shapes), `chats/chat1.md:104` (the activity-feed copy intent).

---

## 1. Premise + user job

### 1.1 What Home is

Home is the **morning briefing** — the first surface the user sees when they open Atlas. Not a landing page, not a dashboard, not a chat. It answers one question:

> _What happened while I was away, and what should I pick up?_

Atlas operates across the user's SaaS surfaces between sessions (drafts emails, queries Salesforce, watches incidents, summarizes meetings, surfaces risks). Most of that work is **agent-initiated** — Atlas or a scheduled subagent decided it was worth doing. Home is where that work surfaces in summarized form so the user can decide which thread to step back into first.

Home is **read-only**. No compose affordance on Home itself; every action drills into another destination. Home is the index, not the workbench.

### 1.2 Who it's for

Every Atlas user, every day. Not gated by role, plan, or tenant maturity. A brand-new tenant with zero chats / agents / todos still sees Home — with an empty-state (§12) that teaches them how to start.

### 1.3 Success state

In under 15 seconds, the user can: (1) read the greeting (confirms personalization works), (2) scan agent activity (what happened overnight), (3) identify the one thing that matters first, (4) drill in. A failed Home is one where the user opens it and has to navigate elsewhere to find their bearings — the acceptance bar is: a returning user knows their next action before they scroll.

### 1.4 What Home is not

- Not infinite scroll — activity feed is bounded (last 24h, 15 entries max).
- Not customizable — section order fixed in Wave 2; personalization is data-driven, not layout-driven.
- Not a write surface — new-chat is a panel quick-action that opens chats.
- Not real-time chat — SSE updates are kind-level events, not token streams.

---

## 2. Source-of-truth map

Per [destinations-master-prd.md §2.2](../destinations-master-prd.md#22-single-source-of-truth-per-destination), exactly one canonical file exists for each concern. **A second copy is a bug.**

| Concern                             | Canonical path                                                                        | Status                                                                                                                                                     |
| ----------------------------------- | ------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Public wire types (`HomePayload` …) | `packages/api-types/src/home.ts` _(NEW file — relocates the type from a destination)_ | The current minimal type lives inside `HomeDestination.tsx:44-48`; Impl-A creates `home.ts` and re-exports through `index.ts`.                             |
| Home main view                      | `packages/chat-surface/src/destinations/home/HomeDestination.tsx`                     | Exists — Impl-B rewrites to the full 7-section layout. The current 3-card implementation (pinned/runs/favorites) is the seed.                              |
| Home context panel                  | `packages/chat-surface/src/destinations/home/HomePanel.tsx` _(NEW)_                   | Impl-B creates. Exported from `destinations/home/index.ts`.                                                                                                |
| Home sub-components (per §3.1)      | `packages/chat-surface/src/destinations/home/sections/{SectionName}.tsx` _(NEW)_      | Each of the 7 main sections + 2 panel sections is its own file. Co-located in a `sections/` subdir so the destination root stays a thin composition layer. |
| Backend route module                | `services/backend/src/backend_app/home/` _(NEW module)_                               | `home/route.py` (FastAPI router), `home/aggregator.py` (fan-out logic), `home/types.py` (Pydantic models), `__init__.py`, `tests/`.                        |
| Facade proxy                        | `services/backend-facade/src/backend_facade/home_routes.py` _(NEW)_                   | Thin pass-through: GET `/v1/home`, GET `/v1/home/stream` (SSE).                                                                                            |
| Sub-PRD (this doc)                  | `docs/atlas-new-design/destinations/home-prd.md`                                      | This file.                                                                                                                                                 |

**Convention:** the destination folder mirrors the master pattern. The 9 other destinations follow the same layout — `{slug}/{Slug}Destination.tsx`, `{slug}/{Slug}Panel.tsx`, `{slug}/sections/*.tsx`, `{slug}/index.ts` re-exports.

---

## 3. Architecture

### 3.1 HomeDestination component tree

The main view is a vertical stack of seven sections. **Every section is its own component** so it can render its own loading/empty/error sub-state independently (§3.5 partial-failure resilience). Order is fixed and matches the design (`dest-home.jsx:106-216`).

```
<HomeDestination>                                 // root, owns useTransport + state machine
  <HomeGreeting />                                // §3.1.1
  <HomeAgentActivityFeed />                       // §3.1.2  ← live-updated via SSE
  <HomePinnedChatsGrid />                         // §3.1.3
  <HomeRecentRunsList />                          // §3.1.4
  <HomeFavoriteToolsList />                       // §3.1.5
  <HomeTodaysFocusList />                         // §3.1.6
  <HomeUpcomingMeetingsList />                    // §3.1.7  ← conditional on calendar connector
</HomeDestination>
```

Each sub-section is a thin wrapper around master-PRD shared primitives:

- `<CardGrid>` for pinned chats (master §4.1)
- `<DocList>` for recent runs, favorite tools, todays focus, upcoming meetings
- `<ActivityList>` for agent activity (the only specially-styled list — see §3.1.2)
- `<StatusPill>` for run statuses and activity-feed kind badges (master §4.2)
- `<ItemLink kind=…>` for every cross-destination click-through (master §4.3)
- `<EmptyState>` for per-section empty (master §4.1)
- `formatRelativeTime(iso)` from `packages/chat-surface/src/util/time.ts` (master §4.4 — hoisted from current `HomeDestination.tsx:70-85`)

**§3.1.1 HomeGreeting** — `<h1>Good {time_of_day}, {user_first_name}.</h1>` + sub-line `{N} agents working · {M} need you · {tenant-local date}`. `time_of_day` is **server-computed against tenant timezone** (consistent text across substrates). Counts come from the same `HomePayload`, no separate fetch.

**§3.1.2 HomeAgentActivityFeed** — `role="list"` of `AgentActivityEntry` cards (last 24h, agent-initiated, capped 15). Discriminated by `kind` (§4.3) for kind-specific copy/icon/tone. Click → `<ItemLink>` to the originating target. Live-updated via SSE (§3.5).

Copy benchmark from `chats/chat1.md:104`:

> _"Atlas drafted a 4-page brief, cross-referenced last 6 months of email + call transcripts, and surfaced 3 risk signals worth raising. Two follow-up emails are queued and waiting on your sign-off."_

Kind-specific narration is **backend-composed** from structured fields. Frontend never builds sentences from primitives — keeps copy in one place for later localization.

**§3.1.3 HomePinnedChatsGrid** — `<CardGrid>` of up to 8 `PinnedChatSummary`. Click → `<ItemLink kind="chat">`. Same as today's `PinnedCard` (`HomeDestination.tsx:237-295`), tokenized to design-system vars (current hex is a Wave 1 leftover Impl-B fixes).

**§3.1.4 HomeRecentRunsList** — Up to 8 `RecentRunSummary`. Status uses `<StatusPill tone=…>`. Same as today's `RecentRunCard` (`HomeDestination.tsx:297-359`), tokenized.

**§3.1.5 HomeFavoriteToolsList** — Up to 8 `FavoriteToolSummary`. Click → tools destination. Same as today's `FavoriteCard` (`HomeDestination.tsx:361-411`).

**§3.1.6 HomeTodaysFocusList** — Top 3 `TodoSummary` due today (or carried-over-overdue). Row: read-only checkbox (toggling redirects to todos destination), text, source-attribution chip (chat/user/agent — see `data.jsx:118-228`).

**§3.1.7 HomeUpcomingMeetingsList** — Next 3 `MeetingSummary` today. Renders only when at least one calendar connector is connected; otherwise the section is replaced with a connect-CTA (§12, Q4).

### 3.2 HomePanel component tree

The context panel (224px column, per PRD.md §6) is a two-section stack matching `dest-home.jsx:53-94`:

```
<HomePanel>
  <HomeStarredProjectsSection />     // §3.2.1
  <HomeQuickActionsSection />        // §3.2.2
</HomePanel>
```

**§3.2.1 HomeStarredProjectsSection** — `<ContextPanel.Section title="Starred projects">` with up to 6 `StarredProjectSummary` rows, each `<ItemLink kind="project">` with the project's emoji + color hue (see `data.jsx:75-115`). Empty: "No starred projects yet" + "Browse projects →" link.

**§3.2.2 HomeQuickActionsSection** — `<ContextPanel.Section title="Quick start">` with server-driven `QuickAction[]` (§4.6). Each is a button; click navigates per `target.kind` (structured `ItemRef`, never a URL string). Server-driven so plan tier / admin role / tenant-custom actions can adjust without UI deploy.

Default seed list (baked into Wave 2 code; admin endpoint to mutate is Wave 5+):

| `id`              | Label             | `target.kind`   | Notes                         |
| ----------------- | ----------------- | --------------- | ----------------------------- |
| `new_chat`        | New chat          | `chat_new`      | Fresh thread in chats         |
| `new_todo`        | New todo          | `todos_new`     | Todos with inline-add focused |
| `onboard_api`     | Onboard an API    | `tools_onboard` | Tools onboarding wizard       |
| `build_agent`     | Build an agent    | `agent_new`     | Agents build wizard           |
| `invite_teammate` | Invite a teammate | `team_invite`   | Team invite flow              |

### 3.3 Backend `/v1/home` aggregation

Single endpoint: `GET /v1/home` → `HomePayload` (§4.1). Lives in `services/backend/src/backend_app/home/`. Handler flow:

1. Resolve `tenant_id` + `user_id` from verified bearer (never from body/query).
2. Cache check (§3.4) — return fresh cached payload if available.
3. Else **parallel fan-out** (`asyncio.gather(..., return_exceptions=True)`):
   - chats (backend) → `pinned_chats`
   - ai-backend (HTTP) → `recent_runs`, `agent_activity` (filtered §5.2)
   - tools (backend `user_skills` + MCP catalog) → `favorite_tools`
   - todos (backend) → `todays_focus`
   - projects (backend) → `starred_projects`
   - connectors (backend) → calendar-connected? → `upcoming_meetings` (or `null`)
   - quick-actions config (backend, server-driven) → `quick_actions`
4. Compose `HomePayload`, write to cache, return.

**Parallelism rule.** One slow upstream does not block others. Each section carries `ok | error | unavailable` status; the response always has the complete shape — per-section errors are signaled in the payload, not via top-level 5xx (§12.6).

**Facade.** `services/backend-facade/src/backend_facade/home_routes.py` is a thin pass-through to `backend:8100/v1/home`. The facade does **not** compose — backend has direct DB access to most upstreams; routing composition through the facade would be N additional HTTP hops. Facade owns auth + tenant pinning + `x-enterprise-org-id`/`x-enterprise-user-id` header injection, then forwards.

**ai-backend interop.** Backend queries ai-backend over HTTP for run records and noteworthy runtime events (ai-backend owns those tables): `GET ai-backend:8000/internal/v1/runs?owner_user_id=...&limit=8&order=started_at desc` + `GET .../events?since=24h&kinds=...`. One HTTP hop per upstream; facade doesn't see this.

### 3.4 Caching

**Key:** `home:v1:{tenant_id}:{user_id}` (Redis in prod; in-memory `lru_cache` in dev). **Value:** serialized `HomePayload` + `cached_at`. **TTL:** 5min (master §5.1 default). **SWR:** stale request returns stale payload + triggers async refresh; next request (≥1s later) gets fresh. Headers: `Cache-Control: max-age=0, stale-while-revalidate=300` + `X-Atlas-Cached-At`.

**Invalidation triggers** (drop cache key for the affected user):

| Trigger                                         | Reason                               |
| ----------------------------------------------- | ------------------------------------ |
| Run completion (ai-backend) for `owner_user_id` | Recent runs + agent activity changed |
| Chat pinned / unpinned                          | Pinned chats changed                 |
| Tool starred / unstarred                        | Favorite tools changed               |
| Todo created / completed / deleted              | Today's focus changed                |
| Project starred / unstarred                     | Starred projects changed             |
| Calendar connector connected / disconnected     | Upcoming meetings shape changed      |
| Admin updates quick-actions config              | Whole-tenant invalidate (broadcast)  |

Invalidation is **best-effort** — TTL bounds staleness on misses. The cache is not the audit log; correctness lives in the source tables.

### 3.5 Real-time updates (SSE)

When on Home, the frontend opens `GET /v1/home/stream`. The stream emits **`home_activity`** events (SSE event name) whose `data` is a JSON-serialized `AgentActivityEntry` (§4.3) — when noteworthy agent activity (§5.2 filter rule) lands for `(tenant_id, user_id)`. Frontend prepends to `<HomeAgentActivityFeed>`, capped at 15-most-recent (older scroll off).

**Only the activity feed is live.** Other sections re-fetch on next Home open or TTL expiry — pinning is rare, agent activity is the high-frequency surface.

**Auth + lifecycle.** Same bearer as `/v1/home`. Stream closes on: navigate away (`AbortController`), 30min idle (reconnects on interaction), backend graceful shutdown (exponential backoff reconnect).

**Backend reuse.** The home backend subscribes to ai-backend's existing `RuntimeEventEnvelope` pipeline (the `sequence_no` model from root CLAUDE.md "Streaming model") and forwards only entries passing the §5.2 filter. No new event bus.

### 3.6 Routing

Home renders at `route.destination === "home"` with **no `view` / `id`** — bounded to a single page. Shell mounts `<HomePanel>` into the ContextPanel slot, `<HomeDestination>` into main (mirrors `os-app.jsx:140-147`). Click-throughs go through `<ItemLink>` (master §4.3) which calls the `Router<TRoute>` port — no `window.location` access from a destination, keeping Home substrate-agnostic.

---

## 4. Wire contracts

All types are TypeScript-canonical (per [packages/api-types/CLAUDE.md](../../../packages/api-types/CLAUDE.md)). The Python equivalents in `services/backend/src/backend_app/home/types.py` are Pydantic models matching field-for-field. **The TypeScript file is the source of truth — Python mirrors.**

### 4.1 `HomePayload`

```typescript
// packages/api-types/src/home.ts

import type { ConversationId, RunId, SkillId } from "./index";

export type TimeOfDay = "morning" | "afternoon" | "evening" | "late";

export interface HomeGreeting {
  readonly time_of_day: TimeOfDay; // server-computed against tenant timezone
  readonly user_first_name: string; // from IdP claim; falls back to email-local-part if absent (see §16)
  readonly tenant_local_date: string; // ISO date `YYYY-MM-DD`, formatted in tenant timezone
  readonly tenant_local_iso: string; // ISO datetime, for client-side relative formatting if needed
  readonly agents_working_count: number; // sum of active personal + scheduled agents
  readonly needs_you_count: number; // pending approvals + inbox-decision items
}

export type SectionStatus = "ok" | "error" | "unavailable";

/** Per-section result. The payload always carries the full shape; `status: "error"`
 *  signals the consumer to render a per-section retry instead of the section's data. */
export interface SectionResult<T> {
  readonly status: SectionStatus;
  readonly data: ReadonlyArray<T>;
  readonly error_message?: string; // only set when status="error"; user-readable
}

export interface HomePayload {
  readonly greeting: HomeGreeting;
  readonly agent_activity: SectionResult<AgentActivityEntry>;
  readonly pinned_chats: SectionResult<PinnedChatSummary>;
  readonly recent_runs: SectionResult<RecentRunSummary>;
  readonly favorite_tools: SectionResult<FavoriteToolSummary>;
  readonly todays_focus: SectionResult<TodoSummary>;
  readonly upcoming_meetings: SectionResult<MeetingSummary> | null; // null = no calendar connector
  readonly starred_projects: SectionResult<StarredProjectSummary>; // for HomePanel
  readonly quick_actions: ReadonlyArray<QuickAction>; // server-driven, never paginated
  readonly cached_at: string; // ISO; mirrors X-Atlas-Cached-At header
}
```

### 4.2 Reused sub-types (extended from `index.ts`)

`PinnedChat`, `RecentRun`, `FavoriteTool` already exist in `HomeDestination.tsx:17-48`. Impl-A **moves** them to `home.ts`, **extends** each with a `summary` field that backend writes, and re-exports from `index.ts` so existing consumers don't break:

```typescript
export interface PinnedChatSummary {
  readonly conversation_id: ConversationId;
  readonly title: string;
  readonly subtitle?: string;
  readonly last_message_at: string; // ISO
  readonly unread_message_count: number; // 0 when the user has seen all
  readonly project_id?: string; // for the project badge (mirrors data.jsx:t-launch.project)
}

export type RecentRunStatus =
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "queued";

export interface RecentRunSummary {
  readonly run_id: RunId;
  readonly title: string;
  readonly status: RecentRunStatus;
  readonly started_at: string; // ISO
  readonly completed_at?: string; // ISO — only when terminal
  readonly conversation_id?: ConversationId; // present if run is inside a chat
}

export interface FavoriteToolSummary {
  readonly skill_id: SkillId;
  readonly name: string;
  readonly subtitle?: string;
  readonly tool_kind: "skill" | "mcp" | "api" | "builtin"; // matches Wave 2 /v1/mcp/tools kind tag
  readonly last_used_at?: string;
}
```

### 4.3 `AgentActivityEntry` — discriminated union

The activity feed is heterogeneous. UI must dispatch on `kind` to render kind-specific copy / icon / tone. **Never** render based on substring matches against `summary`.

```typescript
export type AgentActivityKind =
  | "drafted_artifact" // Atlas drafted a doc/email/slide/sheet
  | "sent_message" // Atlas (with approval) sent a message externally
  | "queued_approval" // a pending approval landed in inbox
  | "risk_signal" // a watcher subagent flagged something
  | "completed_run" // a scheduled / background run finished
  | "failed_run" // a run failed (tool-failure etc.)
  | "extracted_todos" // a meeting-recap / chat-followup agent filed todos
  | "ingested_dataset"; // a connector sync completed

export interface AgentActivityEntryBase {
  readonly id: string; // stable; used as React key + telemetry id
  readonly kind: AgentActivityKind;
  readonly agent_id: string; // the agent that did it
  readonly agent_name: string; // denormalized so frontend doesn't refetch
  readonly summary: string; // user-readable, backend-composed
  readonly created_at: string; // ISO
  readonly target: ItemRef; // the click-through target (§4.6)
  readonly tone: "neutral" | "positive" | "warning" | "alert";
}

export interface DraftedArtifactActivity extends AgentActivityEntryBase {
  readonly kind: "drafted_artifact";
  readonly artifact_kind:
    | "email"
    | "doc"
    | "sheet"
    | "slide"
    | "page"
    | "brief";
  readonly artifact_title: string;
  readonly word_count?: number;
}

export interface SentMessageActivity extends AgentActivityEntryBase {
  readonly kind: "sent_message";
  readonly surface: "email" | "slack" | "comment" | "salesforce" | "other";
  readonly recipient_display: string; // "to acme-legal@..." or "to #launch-aurora"
}

export interface QueuedApprovalActivity extends AgentActivityEntryBase {
  readonly kind: "queued_approval";
  readonly inbox_item_id: string;
  readonly priority: "low" | "med" | "high";
  readonly needs: "approval" | "decision" | "review";
}

export interface RiskSignalActivity extends AgentActivityEntryBase {
  readonly kind: "risk_signal";
  readonly severity: "low" | "med" | "high";
  readonly signal_summary: string; // shorter than `summary`; for the badge chip
}

export interface CompletedRunActivity extends AgentActivityEntryBase {
  readonly kind: "completed_run";
  readonly run_id: RunId;
  readonly duration_ms: number;
  readonly artifacts_produced: number;
}

export interface FailedRunActivity extends AgentActivityEntryBase {
  readonly kind: "failed_run";
  readonly run_id: RunId;
  readonly failure_reason_code: string; // structured; e.g. "mcp_token_expired"
  readonly failure_reason_message: string; // user-readable
  readonly recoverable: boolean;
}

export interface ExtractedTodosActivity extends AgentActivityEntryBase {
  readonly kind: "extracted_todos";
  readonly todos_filed: number;
  readonly assigned_to_you: number;
}

export interface IngestedDatasetActivity extends AgentActivityEntryBase {
  readonly kind: "ingested_dataset";
  readonly dataset_id: string;
  readonly row_count: number;
}

export type AgentActivityEntry =
  | DraftedArtifactActivity
  | SentMessageActivity
  | QueuedApprovalActivity
  | RiskSignalActivity
  | CompletedRunActivity
  | FailedRunActivity
  | ExtractedTodosActivity
  | IngestedDatasetActivity;
```

Backend composes `summary` per-kind from the structured fields. Example for `drafted_artifact`:

> _"Atlas drafted a {word_count}-word {artifact_kind} — {artifact_title}."_

This keeps copy in one place (the backend composer) so localization later wraps one function, not a UI templating maze.

### 4.4 `TodoSummary`

```typescript
export type TodoSourceKind = "user" | "chat" | "agent";
export type TodoPriority = "low" | "med" | "high";

export interface TodoSummary {
  readonly todo_id: string;
  readonly text: string;
  readonly priority: TodoPriority;
  readonly due_iso?: string; // ISO; undefined = no due date
  readonly is_overdue: boolean; // server-computed against now
  readonly source_kind: TodoSourceKind;
  readonly source_label?: string; // "from chat 'Q1 launch'", "from meeting recap"
  readonly project_id?: string;
}
```

The home backend picks top-3 by composite score: `(overdue desc, priority desc, due_iso asc, created_at desc)`. The full ordering is the todos destination's concern.

### 4.5 `MeetingSummary`

```typescript
export type MeetingConnectorKind =
  | "google_calendar"
  | "microsoft_calendar"
  | "other";

export interface MeetingSummary {
  readonly meeting_id: string; // connector-side id; opaque to frontend
  readonly title: string;
  readonly start_iso: string; // ISO; in tenant timezone for display
  readonly end_iso: string;
  readonly attendee_count: number;
  readonly is_organizer: boolean;
  readonly conferencing_url?: string; // direct-launch link if available
  readonly source_connector: MeetingConnectorKind;
}
```

Section returns `null` (not `SectionResult<MeetingSummary>`) when no calendar connector is connected — the UI replaces the section with a CTA row (§13).

### 4.6 `StarredProjectSummary` + `QuickAction` + `ItemRef`

```typescript
export interface StarredProjectSummary {
  readonly project_id: string;
  readonly name: string;
  readonly icon_emoji: string;
  readonly color_hue: number; // 0-359; design tokens consume as oklch hue
  readonly active_thread_count: number;
  readonly last_activity_at: string;
}

/** Server-driven so admins can re-order / hide actions without a UI deploy. */
export interface QuickAction {
  readonly id: string;
  readonly label: string; // user-visible
  readonly icon_name: string; // matches Icon registry; backend allowlist
  readonly target: ItemRef;
  readonly is_admin_only?: boolean; // hidden for non-admins
}

/** Polymorphic cross-destination link. Resolved by the ItemLink registry
 *  (master §4.3). Backend emits these so the wire contract carries intent,
 *  not URL strings. */
export type ItemRef =
  | { readonly kind: "chat"; readonly conversation_id: ConversationId }
  | { readonly kind: "chat_new" }
  | { readonly kind: "run"; readonly run_id: RunId }
  | { readonly kind: "inbox_item"; readonly inbox_item_id: string }
  | { readonly kind: "todo"; readonly todo_id: string }
  | { readonly kind: "todos_new" }
  | { readonly kind: "agent"; readonly agent_id: string }
  | { readonly kind: "agent_new" }
  | { readonly kind: "tool"; readonly skill_id: SkillId }
  | { readonly kind: "tools_onboard" }
  | { readonly kind: "project"; readonly project_id: string }
  | { readonly kind: "meeting_external"; readonly url: string }
  | { readonly kind: "team_invite" }
  | { readonly kind: "library_dataset"; readonly dataset_id: string };
```

`ItemRef` is shared with future destinations — Inbox / Todos / Activity all emit it. Lives in `packages/api-types/src/index.ts` (not `home.ts`) since it's cross-destination.

### 4.7 Cache-related headers

| Header                   | Direction | Meaning                                                           |
| ------------------------ | --------- | ----------------------------------------------------------------- |
| `X-Atlas-Cached-At`      | response  | ISO timestamp of when the cached payload was composed             |
| `Cache-Control`          | response  | `max-age=0, stale-while-revalidate=300`                           |
| `X-Atlas-Section-Errors` | response  | comma-separated list of section keys that failed (telemetry-only) |

---

## 5. Storage + retention

### 5.1 No new tables

Home is **aggregation-only**. It reads from existing tables. No `home_*` table exists or will exist.

| Section           | Reads from                                                                                                                                                                                                                                              |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| greeting          | `users` (first_name from IdP claim), `tenants` (timezone)                                                                                                                                                                                               |
| agent_activity    | `ai-backend`: `runtime_events` filtered by §5.2                                                                                                                                                                                                         |
| pinned_chats      | `conversations` + `conversation_pins` (in backend; the pin table exists today via `/v1/me/preferences` or equivalent — Impl-A confirms; if absent, Impl-A spec'd a `conversation_pins` migration as part of the chat-destination Wave, not Wave 2 Home) |
| recent_runs       | `ai-backend`: `runs` filtered by `owner_user_id`                                                                                                                                                                                                        |
| favorite_tools    | `user_skills` (favorites bit) + `mcp_servers` star projection                                                                                                                                                                                           |
| todays_focus      | `todos` (created by phase 3 — see §5.5)                                                                                                                                                                                                                 |
| upcoming_meetings | calendar connector via the connector framework                                                                                                                                                                                                          |
| starred_projects  | `projects` + `project_user_stars` (created by phase 5 — see §5.5)                                                                                                                                                                                       |
| quick_actions     | static config table `home_quick_actions_config` (single row per tenant, optional overrides; defaults baked into code)                                                                                                                                   |

### 5.2 Agent-activity filter rule

The activity feed reads ai-backend's event pipeline (same pipeline that powers chats timeline/right-rail). The home backend applies a **noteworthiness filter** — a runtime event lands in `agent_activity` iff ALL of:

- happened within last 24h
- `event.tenant_id == caller.tenant_id` (server-enforced)
- `event.owner_user_id == caller.user_id` OR emitted by an agent shared with caller
- `event.kind` is one of: `drafted_artifact`, `sent_message`, `queued_approval`, `risk_signal`, `completed_run`, `failed_run`, `extracted_todos`, `ingested_dataset`
- **agent-initiated** (`event.source` is `agent` or `scheduled_run`; never `user` — typing isn't noteworthy)
- **not** a subagent internal event (we want the parent's framing, not subagent token-stream noise)

The agent-initiated rule is the most load-bearing — without it, the feed becomes a transcript of the user typing. 24h window is the master-PRD §5.1 default (Q1 — Wave 4+ might make it per-user). **Cap:** 15 entries; rest silently dropped (deep-link via "See all activity →" — §8.3).

### 5.3 Per-user view

**No per-user materialized table.** Composition at request-time, cached 5min. Rationale: write-amplification of a materialized view across activity tables is worse than read-amplification of a 7-upstream parallel fan-out cached 5min.

### 5.4 Retention

Home stores nothing — retention reduces to cache lifetime: 5min TTL. On tenant or user hard-delete, the cache key is purged as part of the cascade (tenant-delete worker walks registered cache prefixes; Impl-A confirms / adds Home's registration). Upstream retention (runs / events / todos / …) is each owning destination's concern; Home inherits — if they soft-delete, Home's next refresh stops surfacing.

### 5.5 Cross-phase coupling

`todays_focus` (Phase 3) and `starred_projects` (Phase 5) depend on tables not yet shipped. Home Phase 2 returns `SectionResult{ status: "unavailable", data: [], error_message: "Todos coming in Phase 3" }` for those; frontend renders graceful "coming soon" — not an error. When the underlying destination ships, the aggregator picks it up — no Home redeploy.

---

## 6. Audit

**Home is read-only — no audit on Home reads.** `GET /v1/home` and `GET /v1/home/stream` write **no** audit rows (master §3.2 applies to state-changing ops; auditing tab-refresh churn yields no forensic value). Telemetry (§11) captures opens for product analytics — telemetry ≠ audit.

**Click-throughs are audited at their destination.** Activity-entry click into a run → runs/chats audits. Inbox-item open → inbox audits. Meeting `conferencing_url` → external link, not audited by Home. **Impl-A acceptance check:** confirm chats / runs / inbox / tools already audit `open` events; file gaps separately if any are missing (not a Home blocker).

**Quick-actions config changes ARE audited.** When a future admin endpoint `PATCH /v1/admin/home/quick-actions` lands (Wave 5+ admin surfaces), it writes via `packages/audit-chain` with `(tenant_id, actor_user_id, action="quick_actions.update", target_kind="home_quick_actions_config", before_state, after_state, ts, request_id)`. Wave 2 ships only the default config baked into code; the audited write path is the upgrade path.

---

## 7. Authorization

**§7.1 Tenant isolation.** `tenant_id` is derived from the verified bearer's claims **server-side** — never read from body / query / header (per root CLAUDE.md untrusted-input rule). Every fan-out passes the resolved `(tenant_id, user_id)` explicitly; downstream services re-verify at their boundary. **Required test (§17.3):** two-tenant, two-user setup — `user_b` calling `/v1/home` sees none of `user_a`'s data, and response is 200 (not 403).

**§7.2 Per-user scope.** Within a tenant, `/v1/home` returns only items the caller has read access to:

- Pinned chats: chats the user is a participant in or granted access to.
- Recent runs: runs where `owner_user_id == caller.user_id` (until multiplayer threads — PRD.md §13 #3).
- Agent activity: emitted on user's behalf or by agents shared with them.
- Favorite tools / starred projects: per-user state.
- Today's focus: user's todos.
- Upcoming meetings: meetings the user is invited to.

ACL enforcement is at each upstream's query layer; the aggregator does not duplicate ACL logic.

**§7.3 Role.** Home is not role-gated — every authenticated user gets a Home. Only role-aware element is `QuickAction.is_admin_only` (e.g., `invite_teammate` when SSO-provisioning is enabled). Backend omits actions from the array when the user lacks the role; frontend never evaluates `is_admin_only`.

**§7.4 Guests.** Per PRD.md §13 #3, multiplayer / external-collaborator support is undecided. Until then, **guests do not see Home** — they land directly in the chat / project they were invited to. Router enforces (out of scope for this sub-PRD; called out so Impl-B doesn't accidentally route guests here).

---

## 8. Pagination + search

**§8.1 No pagination.** Home is bounded-small. Caps: `agent_activity` 15, `pinned_chats` 8, `recent_runs` 8, `favorite_tools` 8, `todays_focus` 3, `upcoming_meetings` 3, `starred_projects` 6, `quick_actions` ~8. Total payload ≤ ~50 entries. One round-trip, no `?after=` cursors.

**§8.2 No search.** Search is ⌘K palette (Wave 6 per PRD.md §12). Home links to other destinations for full views.

**§8.3 "See all" deep-link endpoints.** Each section's header has a "See all" affordance opening the destination with a filter pre-applied:

| Section           | Target endpoint                                                              |
| ----------------- | ---------------------------------------------------------------------------- |
| agent_activity    | `GET /v1/inbox?filter[from_kind]=agent&filter[window]=24h` (Inbox — Phase 4) |
| pinned_chats      | `GET /v1/conversations?filter[pinned]=true`                                  |
| recent_runs       | `GET /v1/agent/runs?owner_user_id=<caller>&limit=…`                          |
| favorite_tools    | `GET /v1/mcp/tools?filter[starred]=true` (Tools — Phase 8)                   |
| todays_focus      | `GET /v1/todos?filter[when]=today` (Phase 3)                                 |
| upcoming_meetings | `GET /v1/connectors/{calendar_id}` (Connectors — Phase 9)                    |
| starred_projects  | `GET /v1/projects?filter[starred]=true` (Phase 5)                            |

For not-yet-shipped destinations, the link renders disabled with tooltip "Coming in Phase N" — same graceful-degradation pattern as §5.5.

---

## 9. Accessibility

Home satisfies master PRD §3.6 WCAG 2.1 AA:

- **Semantic structure.** `<main aria-label="Home destination">`; greeting is `<h1>`; each of the 7 main sections + 2 panel sections has an `<h2>`. Activity feed is `<ul role="list">` with each entry as `<li>` — single screen-reader announcement per entry (summary + tone + target).
- **Focus.** All interactives are `<button>` or `<a>`; visible focus ring via `--ring-color`. Tab order top-down, left-to-right. `Enter` and `Space` activate cards.
- **Color is never sole carrier.** Run status pills carry color + label; status dot is `aria-hidden`. Activity-feed tone pairs with kind-specific icon + textual severity in summary. Unread-count is number + label, not a colored dot alone.
- **Reduced motion.** SSE fade-in (60ms) and skeleton shimmer respect `prefers-reduced-motion: reduce`.
- **Live announcements.** Activity feed is `aria-live="polite"` + `aria-relevant="additions"`. New SSE entry announces `{agent_name} {summary}`; older entries do not re-announce.
- **High-contrast theme** covers Home automatically via design-system tokens — Impl-B uses `var(--color-*)`, never literal hex (current `HomeDestination.tsx:55-66` has Wave 1 hex leftovers Impl-B tokenizes).
- **Tests.** `axe-core` clean on every state (§17.6).

---

## 10. Performance

Master §3.7 targets: **LCP < 2.5s** cold, **INP < 200ms**, **CLS < 0.05**.

- **LCP element is the greeting `<h1>`**, not a section card — greeting renders the instant the payload arrives, sections may still hydrate. Layout-locked min-heights on each section prevent shift:
  ```typescript
  const SECTION_MIN_HEIGHTS = {
    greeting: 96,
    activity_feed: 320,
    pinned_chats: 200,
    recent_runs: 200,
    favorite_tools: 200,
    todays_focus: 160,
    upcoming_meetings: 160,
  };
  ```
  (Heights cross-referenced against `dest-home.jsx` rendered sizes.)
- **Initial fetch is one round-trip.** `GET /v1/home` returns the full payload. SSE stream opens **after** payload resolves; never blocks LCP.
- **Re-renders.** Shell does not re-mount on Home navigation (master §3.7). Within Home, sections are `React.memo`'d by their payload slice — an SSE update to `agent_activity` re-renders only the feed.
- **SSE lifecycle.** `useEffect` cleanup with `AbortController` cancels on unmount; no leaked listeners. One stream per tab.
- **Backend budget.** `/v1/home` p95 < 400ms warm (cache hit) / < 1500ms cold (parallel 7-upstream fan-out incl. cross-service ai-backend call). `/v1/home/stream` keepalive ping every 30s; memory cost bounded by concurrent-home-user count.

---

## 11. Telemetry

Per master PRD §3.8 — every user-meaningful action emits an OpenTelemetry span. **All IDs SHA-256 hashed with per-tenant salt; names / bodies / summaries / document titles never enter span attributes.**

### 11.1 Spans

| Span name                 | Key attributes                                                                  | When                                             |
| ------------------------- | ------------------------------------------------------------------------------- | ------------------------------------------------ |
| `home.open`               | `cached`, `cache_age_ms`                                                        | `HomeDestination` mount                          |
| `home.section_view`       | `section`, `entry_count`, `section_status`                                      | Per section, on render (incl. per-section error) |
| `home.activity_open`      | `kind`, `agent_id_hash`, `target.kind`, `tone`                                  | Click on activity-feed entry                     |
| `home.pinned_chat_open`   | `conversation_id_hash`                                                          | Click on pinned-chat card                        |
| `home.recent_run_open`    | `run_id_hash`, `status`                                                         | Click on recent-run card                         |
| `home.favorite_tool_open` | `skill_id_hash`, `tool_kind`                                                    | Click on favorite-tool                           |
| `home.todo_open`          | `priority`, `is_overdue`, `source_kind`                                         | Click on today's-focus item                      |
| `home.meeting_open`       | `source_connector`, `is_organizer`                                              | Click on upcoming meeting (opens external URL)   |
| `home.quick_action`       | `action_id`                                                                     | Click on a quick action                          |
| `home.section_retry`      | `section`                                                                       | Section retry after partial-failure              |
| `home.sse_connect`        | —                                                                               | SSE opens                                        |
| `home.sse_disconnect`     | `reason` (`navigated_away` / `idle_timeout` / `server_close` / `network_error`) | SSE closes                                       |
| `home.sse_event_received` | `kind` (sampled 1/10)                                                           | Each SSE event                                   |

Every span carries `tenant_id` + `user_id_hash` + `destination="home"` by default.

### 11.2 Backend logs

Structured logs (with `request_id` correlation) on: cache hit/miss/refresh, each upstream fan-out (latency + status), per-section failure, SSE backpressure (per-user event buffer fills).

### 11.3 Analytics queries

(Document the "why" behind the span shape — not part of the contract.) Open frequency by tenant; click-through distribution across sections (informs Q6 section order); active-SSE % (does live feed get used?); per-kind CTR (does `risk_signal` warrant different visual weight than `drafted_artifact`?).

---

## 12. States

Per master PRD §3.10. Every state designed + tested.

**§12.1 Loading.** Skeleton sections in their final positions (no `display:none`→`block` flip). `aria-hidden="true"` during load; announcement comes via `aria-live` when content arrives.

**§12.2 Ready.** All sections populated. Each section with zero entries renders its empty-state (§12.3) — consistent layout is the value.

**§12.3 Empty (per-section)**

| Section           | Copy                                                             | CTA                           |
| ----------------- | ---------------------------------------------------------------- | ----------------------------- |
| agent_activity    | "Nothing's happened yet today. Atlas activity will appear here." | —                             |
| pinned_chats      | "Pin a chat to keep it here."                                    | "Open chats →" (`chat_new`)   |
| recent_runs       | "Recent runs will appear here as Atlas works."                   | —                             |
| favorite_tools    | "Star a tool to bookmark it."                                    | "Browse tools →" (Phase 8)    |
| todays_focus      | "Nothing on your list for today. Want to plan?"                  | "Open todos →" (Phase 3)      |
| upcoming_meetings | "No meetings today." (when connector connected)                  | —                             |
| starred_projects  | "Star a project to keep it here."                                | "Browse projects →" (Phase 5) |

**§12.4 Empty (whole-Home, new tenant).** Every section empty → full-bleed: `🌱 Welcome to Atlas. Start a chat to see Atlas work for you.` + `[ New chat ]` button (always) + `[ Take a 2-min tour ]` (rendered only when tour-content registry is populated; in Wave 2 it isn't — see Q2).

**§12.5 Error (whole-Home).** Backend 5xx → single `<ErrorPanel>` matching today's `HomeDestination.tsx:133-183`: title + message + retry. 401 from expired bearer triggers existing dev-IdP auto-mint flow first.

**§12.6 Partial failure (load-bearing UX).** One section errors, others render normally. The errored section shows a section-local card: `⚠ Couldn't load {section}. Other sections are unaffected. [↻ Retry section]`. "Retry section" calls `GET /v1/home?refresh_section={name}` — query-param hint, not a separate endpoint; backend bypasses cache for that one section.

**§12.7 Offline.** `navigator.onLine === false` (or transport network error): read last successful `HomePayload` from `KeyValueStore` (the substrate-agnostic port from commit `0f29624`; key `home:last_payload:v1`, one payload per user, written on every successful response). Render with banner: `Offline — showing your last morning. (cached at {relative time})`. Click-throughs requiring network are muted; auto-retry on `online` event.

**§12.8 Stale (`cached_at` > 5min).** SWR served stale before refresh landed → small hint above the feed: `↻ Showing last refresh from 6 minutes ago — refreshing now.` Wave 2 keeps it simple: frontend re-fires `/v1/home` on mount when stale.

---

## 13. Cross-destination references

**Home only links out.** Every clickable item navigates to another destination via the shared `<ItemLink>` registry (master §4.3) — no bespoke navigation in Home.

| Home section      | Resolves to                                                              | Mechanism                                       |
| ----------------- | ------------------------------------------------------------------------ | ----------------------------------------------- |
| agent_activity    | Polymorphic `ItemRef` (runs, inbox, todos, agents, datasets, …)          | `<ItemLink>` registry                           |
| pinned_chats      | chats destination                                                        | `<ItemLink kind="chat">`                        |
| recent_runs       | runs surface (ai-backend, via facade)                                    | `<ItemLink kind="run">`                         |
| favorite_tools    | tools destination                                                        | `<ItemLink kind="tool">`                        |
| todays_focus      | todos destination                                                        | `<ItemLink kind="todo">`                        |
| upcoming_meetings | external `conferencing_url`                                              | `<a target="_blank" rel="noopener noreferrer">` |
| starred_projects  | projects destination                                                     | `<ItemLink kind="project">`                     |
| quick_actions     | `chat_new` / `todos_new` / `tools_onboard` / `agent_new` / `team_invite` | `<ItemLink>` registry                           |

**Delete cascade — implicit, source-of-truth-driven.** Home is a view; underlying destinations are the source of truth. When a chat is deleted: `conversations` row dropped → Home cache invalidated (§3.4 trigger) → next refresh, the chat is absent from `pinned_chats`. Same pattern for every section. A separate "Home cascade" worker would be the bug.

**Stale-entry resilience.** If a `recent_runs` entry's run was deleted within the 5min TTL, clicking it would 404 at the runs destination — handled there ("Run not found"), not in Home. `<ItemLink>` routes to the destination's not-found state; Home does not pre-validate references on cache read (would defeat caching).

---

## 14. Desktop substrate caveats

**None.** Home is plain React + transport call + SSE. No file picker, no native notifications, no OS clipboard. Every cross-destination link goes through `<ItemLink>` which uses the substrate-agnostic `Router<TRoute>` port (per [PRD.md §2.1](../PRD.md) and the ports work in `packages/chat-surface/src/ports/`).

For `meeting_external` links (calendar `conferencing_url`), the `<a>` element opens in a new window in browser substrate; in desktop substrate, the host's URL-handler port intercepts and opens in the OS default browser. This is handled by the existing ports facade — Home does not special-case substrate.

---

## 15. Implementation phasing

Two parallel impl agents. **They do not collide on files** — Impl-A owns Python + TypeScript types; Impl-B owns React. The TypeScript types are committed by Impl-A first (since Impl-B consumes them).

### 15.1 Impl-A (api-types + backend + facade + tests)

**Branch:** `worktree-agent-phase2-home-impl-a`

**Owned files (NEW unless noted EDIT):**

- `packages/api-types/src/home.ts` — types per §4
- `packages/api-types/src/index.ts` — EDIT: re-export from `./home`
- `services/backend/src/backend_app/home/{__init__.py, route.py, aggregator.py, types.py, sse.py, cache.py}` — FastAPI router, fan-out composition, Pydantic mirrors, SSE filter/forwarder, Redis cache adapter (in-memory fallback for dev)
- `services/backend/src/backend_app/app.py` — EDIT: register the home router
- `services/backend/tests/{unit,integration}/home/` — per §17.2-§17.4
- `services/backend-facade/src/backend_facade/home_routes.py` — thin pass-through (`GET /v1/home`, `GET /v1/home/stream`)
- `services/backend-facade/src/backend_facade/app.py` — EDIT: wire the router
- `services/backend-facade/tests/test_home_routes.py` — forwarding correctness

**Test headlines:** aggregator composition, section-status branches, activity filter, tenant isolation, partial-failure, cache hit/miss/SWR, invalidation triggers, SSE filter, facade pass-through. Full plan: §17.2-§17.4.

### 15.2 Impl-B (frontend)

**Branch:** `worktree-agent-phase2-home-impl-b`

**Owned files (NEW unless noted EDIT):**

- `packages/chat-surface/src/destinations/home/HomeDestination.tsx` — EDIT: full rewrite to 7-section layout
- `packages/chat-surface/src/destinations/home/HomePanel.tsx`
- `packages/chat-surface/src/destinations/home/sections/{HomeGreeting, HomeAgentActivityFeed, HomePinnedChatsGrid, HomeRecentRunsList, HomeFavoriteToolsList, HomeTodaysFocusList, HomeUpcomingMeetingsList, HomeStarredProjectsSection, HomeQuickActionsSection}.tsx`
- `packages/chat-surface/src/destinations/home/sse-stream.ts` — `useHomeActivityStream` hook
- `packages/chat-surface/src/destinations/home/index.ts` — EDIT: re-export both
- `packages/chat-surface/src/util/time.ts` — `formatRelativeTime` hoisted from current `HomeDestination.tsx:70-85` (master §4.4)
- `packages/chat-surface/src/destinations/home/{HomeDestination.test.tsx (EDIT), HomePanel.test.tsx}`
- `apps/frontend/src/app/App.tsx` — EDIT: wire HomePanel into ContextPanel slot when `destination="home"`

**Test headlines:** per-state rendering, `<ItemLink>`-only navigation, SSE hook lifecycle, axe-core clean on every state, typical-morning E2E, performance budget, preserved `data-testid` markers. Full plan: §17.1, §17.5-§17.7.

### 15.3 File boundaries + merge order

- **Impl-A NEVER touches** `packages/chat-surface/`. **Impl-B NEVER touches** `services/*` or `packages/api-types/`. Both touch `docs/` only to file a contract-bug back to the orchestrator (STOP + report; do not resume until orchestrator merges the doc fix).
- **Merge order:** Impl-A first (types + backend + facade), Impl-B second (consumes the new types). Orchestrator runs `make test` + per-service suites, browser-verifies Home, updates master PRD §5.1 to mark resolved open questions.

---

## 16. Open questions for product (parth)

Each has a recommended default — adopt the default unless product disagrees.

**Q1 — Activity window length.** Is "last 24h" right? User-configurable?
→ **Default 24h, not user-configurable in Wave 2.** Configurable adds a settings surface for marginal value; revisit when telemetry (§11) shows "See all activity" click-through rate.

**Q2 — New-tenant empty-state.** Guided tour or just empty + CTA?
→ **Empty page with "New chat" CTA + optional "Take a 2-min tour" link** (rendered only if a tour-content registry is populated; in Wave 2 it isn't, so the link is omitted). Heavyweight onboarding overlay is a separate product surface.

**Q3 — Today's focus — automatic or user-pinned?**
→ **Automatic in Wave 2.** Server-side composite score `(overdue desc, priority desc, due asc, recency desc)`. Pinning needs a mutation surface — defer to Wave 4+.

**Q4 — Upcoming meetings — what if no calendar connector?**
→ **Replace the section with a one-row CTA: "Connect a calendar to see today's meetings →"** linking to connectors. With a connector but zero meetings, show "No meetings today." The CTA doubles as empty-state and upsell.

**Q5 — Greeting personalization — source for first name?**
→ **Source priority:** (1) IdP claim `given_name`, (2) IdP `name` first token, (3) email local-part capitalized. Falling through: drop the name (`"Good morning."` is fine). Never "User" / "Atlas user".

**Q6 — Default section order — fixed or per-user reorder?**
→ **Fixed in Wave 2** (activity → pinned → runs → favorites → focus → meetings). Per-user reorder is Wave 4+. Telemetry (§11) tells us if the order is right.

**Q7 — Quick-action set tenant customization — admin UI now?**
→ **Wave 2 ships server-driven defaults, no admin UI.** Admin UI (`PATCH /v1/admin/home/quick-actions`) is Wave 5+ when admin surfaces are first-class. Server-driven shape (§4.6) makes this a pure delivery problem later.

**Q8 — SSE drop-off — silent retry or "paused" indicator?**
→ **Silent retry with exponential backoff (1s → 30s).** "Paused" badge adds anxiety; mobile-network blips are common. User can refresh manually if they sense staleness.

---

## 17. Test plan

### 17.1 Frontend unit tests (`packages/chat-surface/`)

- Greeting renders for each `time_of_day`; first-name pulled from payload.
- AgentActivityFeed dispatches by `kind` to kind-specific copy/icon/tone (every kind in §4.3 covered).
- AgentActivityFeed is `<ul role="list">` with `aria-live="polite"` + `aria-relevant="additions"`.
- SSE hook: subscribes on mount, unsubscribes on unmount, appends events to top, caps at 15.
- Each section's empty state renders the §12.3 copy + CTA; whole-Home empty (§12.4) renders when every section is empty.
- Whole-Home error renders `<ErrorPanel>` with retry (§12.5); partial-error state — one section in error, others ready (§12.6).
- Section retry button calls `?refresh_section=<name>`; offline state reads from `KeyValueStore` + banner (§12.7); stale-state hint when `cached_at` > 5min (§12.8).
- Skeleton heights match `SECTION_MIN_HEIGHTS`; CLS < 0.05 on data resolve.
- Every click-through goes through `<ItemLink>` — no direct `window.location` or `router.navigate` calls in section components.
- HomePanel renders both sections; quick-action click dispatches via `<ItemLink>`.
- Existing `data-testid` markers preserved (`home-destination`, `home-section-pinned`, …).

### 17.2 Backend unit tests (`services/backend/tests/unit/home/`)

- Aggregator composes payload from all-ok upstreams; marks section `error` on one upstream throw; marks `unavailable` for not-yet-shipped destinations (§5.5).
- Greeting `time_of_day` matches tenant timezone (not server-tz, not client-tz); first-name source-priority (Q5).
- Activity filter (§5.2): excludes user-initiated events, subagent internal events, events outside 24h; caps at 15.
- Today's focus picks top-3 by composite score (§4.4).
- Upcoming meetings returns `null` when no calendar connector (Q4).
- Quick actions filter strips `is_admin_only` for non-admins (§7.3).
- Section caps enforced server-side (§8.1).

### 17.3 Backend integration tests (`services/backend/tests/integration/home/`)

- `GET /v1/home` returns 200 with full shape.
- **Tenant isolation** (§7.1 hard requirement): two users in different tenants, neither sees the other's data; cross-tenant header injection ignored.
- Cache: hit returns cached payload + `X-Atlas-Cached-At`; miss fan-outs and caches; SWR serves stale + async refreshes.
- Invalidation triggers (§3.4): writing a pinned chat / completing a todo / starring a tool drops the cache key.
- `?refresh_section=recent_runs` bypasses cache for one section only.
- **Partial-failure** (§12.6): one upstream errors → 200 with `status: "error"` for that section; all upstreams error → still 200 with every section in error (NOT 5xx — preserves offline-fallback).
- Backend itself down: 5xx; facade forwards 5xx transparently.

### 17.4 SSE integration tests

- `/v1/home/stream` opens with valid bearer; rejects invalid bearer.
- Stream emits agent events; filters out user-initiated, other tenants', other users' agent-private events (§5.2).
- 30s keepalive ping; disconnect on auth revocation.

### 17.5 Frontend integration — typical morning flow

MSW-mocked. Open Home → assert greeting + 7 main sections + panel both sections render → SSE delivers one event after 500ms → assert event appears at top of feed → click a recent run → assert route transitions → navigate back → assert no second `GET /v1/home` (cache hit).

### 17.6 a11y + performance

- `axe-core` zero violations on loading / ready / empty-per-section / whole-empty / error / partial-error / offline.
- Lighthouse budget: LCP < 2.5s, INP < 200ms, CLS < 0.05 (master §3.7).
- `React.Profiler` assert: navigating to Home and back to chats does not re-mount the shell.

---

## 18. Anti-goals for this phase

Explicitly OUT OF SCOPE — flagged so subagents don't drift.

- **No write operations from Home.** No pinning, completing, starring, approving from Home — every action drills into the source destination. (Inline approve/dismiss on activity cards is Wave 4+.)
- **No section-order customization** (Q6 — Wave 4+).
- **No per-user activity-window length** (Q1 — Wave 4+).
- **No third-party feed widgets** (LinkedIn / Twitter / RSS / news). Strictly first-party data.
- **No marketing copy / upsells.** Empty-state and connector-CTA are the only "upsells" and live where the missing data would.
- **No global search bar on Home** (search is ⌘K — Wave 6).
- **No "messages from Atlas team" surface** (lives in profile menu / settings).
- **No analytics dashboards** — Home is a briefing, not a metrics page.
- **No drag-and-drop section reordering** (Wave 4+).
- **No background polling** — live updates are SSE-driven only.
- **No localStorage for the activity feed.** Only `KeyValueStore` (substrate-agnostic port), only for the last `HomePayload` offline-fallback. Per-section caches do not live in the browser.

---

## 19. References

- [PRD.md](../PRD.md) — workspace shell + composer + thread canvas foundation
- [destinations-master-prd.md](../destinations-master-prd.md) — §3 enterprise checklist · §4 shared primitives · §5.1 Home
- Design source: `/tmp/atlas-design/enterprise-search-template/project/dest-home.jsx` (HomeMain L98-218, HomePanel L53-94) · `os-app.jsx:140-147` (mount shape) · `os-data.jsx` (cross-destination data shapes) · `data.jsx` (todos source-attribution) · `chats/chat1.md:104` (activity-feed copy intent)
- Current code: `packages/chat-surface/src/destinations/home/HomeDestination.tsx` (3-section seed) · `packages/api-types/src/index.ts` (where the current `HomePayload` would live — Impl-A relocates)
- Service guides: [services/backend/CLAUDE.md](../../../services/backend/CLAUDE.md) · [services/backend-facade/CLAUDE.md](../../../services/backend-facade/CLAUDE.md) · [packages/api-types/CLAUDE.md](../../../packages/api-types/CLAUDE.md)
- Compliance: root [CLAUDE.md](../../../CLAUDE.md) §Compliance Reviews
