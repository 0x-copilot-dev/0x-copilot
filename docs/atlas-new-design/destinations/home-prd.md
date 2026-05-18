# Home — sub-PRD (Phase 9)

**Status:** draft (2026-05-18)
**Owner:** parth (orchestrator) — sub-PRD by the Phase 9 dispatch agent
**Parents:** [PRD.md](../PRD.md) · [destinations-master-prd.md](../destinations-master-prd.md) (§3 enterprise checklist, §4 shared primitives, §5.1 Home)
**Supersedes:** Phase 2 home-prd.md (this file is the rewrite — see §1.5 for redesign rationale and what changed).

---

## 1. Premise + user job

### 1.1 What Home is

Home is the **morning briefing** — the first surface the user sees when they open Atlas. It answers four questions, in order of urgency:

1. **Does anything need me right now?** (approvals waiting, runs that failed, overdue items)
2. **What happened while I was away?** (runs completed, threads updated, items shipped — past-tense, scannable)
3. **What's coming up today?** (meetings, scheduled routines, todos due today — one merged timeline)
4. **Where do I pick up?** (in-flight projects with recent activity)

Atlas operates across the user's SaaS surfaces between sessions (drafts emails, queries data, watches incidents, summarizes meetings). Most of that work is **agent-initiated**. Home is where that work surfaces in summarized form so the user can decide which thread to step back into first.

Home is **read-only**. No compose affordance, no inline mutations. Every action drills into another destination. Home is the index, not the workbench.

### 1.2 The 5-second test

A returning user lands on Home. **In under 5 seconds**, without scrolling, they can answer: "Do I need to do anything right now?" If yes, they click into it. If no, they continue scanning the briefing.

If the user has to scan 6 widgets before knowing whether triage is required, the design has failed. The triage strip exists exactly to compress this scan to a single horizontal glance.

### 1.3 Who it's for

Every Atlas user, every day. Not gated by role, plan, or tenant maturity. A brand-new tenant with zero data still sees Home — but with a **welcome state** (§14.4), not a six-empty-card graveyard.

### 1.4 What Home is NOT

- **Not a dashboard.** No charts, no sparklines, no "agent activity over time." Atlas is an agent workspace, not BI.
- **Not a chat launcher.** Chat is the right rail of the workspace, not the center of Home. No bottom-of-page composer.
- **Not a tool catalog.** Favorite tools belong in Tools; pinned chats belong in the chats sidebar — Home is neither.
- **Not infinite scroll.** Bounded counts per section. Drill-in to see more.
- **Not customizable layout.** Section order is fixed in Phase 9; personalization is data-driven, not chrome-driven.
- **Not a write surface.** Pinning, approving, completing, starring all happen on the source destination. (Inline approve from the LiveActivityRail is a possible Phase 11+ addition, not Phase 9.)

### 1.5 What changed from Phase 2 (rewrite rationale)

The Phase 2 home-prd.md specified a 7-section model: `agent_activity, pinned_chats, recent_runs, favorite_tools, todays_focus, upcoming_meetings, starred_projects`. The route + backend stubs shipped, but the live experience in dev (no seed data) reveals two structural problems:

1. **Empty-six-cards graveyard.** Every section returned `{status:"ok", data:[]}`. Six skeletons, then six "Nothing here yet" placeholders. The user can't tell what Home is _for_ from looking at it.
2. **Section list assembled from "what data do we have" instead of "what story does Home tell."** Pinned chats and favorite tools are _one click away_ in the sidebar / Tools destination — surfacing them again on Home is duplicate navigation, not signal. Recent runs as a standalone list competes with the activity feed for the same eye-real-estate. The today widgets (focus / meetings / routines) fragment one mental model ("what's on my plate today") into three side-by-side cards.

Phase 9 reframes Home around the four questions in §1.1 and re-cuts the section list accordingly:

| Phase 2 section     | Phase 9 fate                                                                                           |
| ------------------- | ------------------------------------------------------------------------------------------------------ |
| `agent_activity`    | **Demoted** — moved from top-of-page to **LiveActivityRail** (right-side or bottom-strip subordinate)  |
| `pinned_chats`      | **Dropped** — chats sidebar already surfaces pinned chats one click away                               |
| `recent_runs`       | **Subsumed** — completed runs now appear in the **WhatsNewDigest** alongside other past-tense activity |
| `favorite_tools`    | **Dropped** — belongs in Tools destination                                                             |
| `todays_focus`      | **Merged** — todos due today are entries in the **TodayTimeline**                                      |
| `upcoming_meetings` | **Merged** — calendar entries are TodayTimeline entries                                                |
| `starred_projects`  | **Replaced** — `InFlightStrip` shows projects with _recent activity_ (signal), not stars (preference)  |

And Phase 9 **adds** three new sections that the Phase 2 model did not have:

- **TriageStrip** (top of page, above the fold) — one-line "Does anything need me?" answer
- **TodayTimeline** — chronological merge of meetings + routine fires + todos due + scheduled runs
- **WhatsNewDigest** — past-tense "since last visit" cutoff, scannable list, bounded

Phase 9 also addresses two operational gaps:

- **Dev seed runner** (§7) — `dev_seed.yaml` keyed by persona slug, primes real persistence at startup so Home demos with density.
- **Facade SSE proxy fix** (§8) — the current 404 on `/v1/home/stream` (facade only proxies `/v1/home`, not the stream).

### 1.6 Success state

A Sarah Chen opens Atlas at 9:14 AM. In one viewport she sees:

- "Good morning, Sarah. Monday, May 18"
- `⚠ 3 approvals waiting · ⚠ 1 run failed · • 2 due today` — three clickable chips
- TodayTimeline: 4 entries between 9:30 and 16:00
- WhatsNewDigest: "Since 5:42 PM yesterday" — 3 completed runs, 1 failure, 1 routine fire
- InFlightStrip: 2 projects, last activity timestamps
- LiveActivityRail (right side): 4 most recent agent activities streaming in

She knows in under 5 seconds whether she needs to triage or can keep scanning. **That is the bar.**

---

## 2. Source-of-truth map

Per [destinations-master-prd.md §2.2](../destinations-master-prd.md#22-single-source-of-truth-per-destination), exactly one canonical file exists per concern. A second copy is a bug.

| Concern                             | Canonical path                                                                                         | Status                                                                                                                                                                                                                                                                                                   |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Public wire types (`HomePayload` …) | `packages/api-types/src/home.ts`                                                                       | EXISTS (Phase 2 created). Phase 9 rewrites — see §4.                                                                                                                                                                                                                                                     |
| Home main view                      | `packages/chat-surface/src/destinations/home/HomeDestination.tsx`                                      | EXISTS. Phase 9 rewrites to the 5-section layout (TriageStrip + TodayTimeline + WhatsNewDigest + InFlightStrip + welcome-state branch).                                                                                                                                                                  |
| Home context panel                  | `packages/chat-surface/src/destinations/home/HomePanel.tsx`                                            | EXISTS. Phase 9 keeps HomePanel for Quick Actions only; drops StarredProjectsSection (replaced by InFlightStrip in main).                                                                                                                                                                                |
| Live activity rail                  | `packages/chat-surface/src/destinations/home/sections/LiveActivityRail.tsx` _(NEW)_                    | New. Right-side or bottom-strip subordinate rail. Hosts the SSE stream.                                                                                                                                                                                                                                  |
| Home sub-sections (per §3.1)        | `packages/chat-surface/src/destinations/home/sections/{SectionName}.tsx`                               | Phase 2 has `Home{Greeting,PinnedChatsGrid,RecentRunsList,FavoriteToolsList,TodaysFocusList,UpcomingMeetingsList,AgentActivityFeed}`. Phase 9 replaces with `HomeGreeting, TriageStrip, TodayTimeline, WhatsNewDigest, InFlightStrip, LiveActivityRail`. Old files **deleted** (no parallel components). |
| Backend route module                | `services/backend/src/backend_app/home/`                                                               | EXISTS. Phase 9 rewrites `aggregator.py` and `types.py`; adds composers `triage.py`, `timeline.py`, `whats_new.py`, `in_flight.py`; keeps `sse.py`, `route.py`, `cache.py`.                                                                                                                              |
| Facade proxy                        | `services/backend-facade/src/backend_facade/home_routes.py`                                            | EXISTS for `/v1/home`. Phase 9 **adds** `/v1/home/stream` SSE proxy (the 404 bug).                                                                                                                                                                                                                       |
| Dev seed runner                     | `services/backend/dev_seed.yaml` _(NEW)_ + `services/backend/src/backend_app/dev_seed/` _(NEW module)_ | New. Mirrors `dev_personas.yaml` + `dev_idp/personas.py` pattern.                                                                                                                                                                                                                                        |
| Sub-PRD (this doc)                  | `docs/atlas-new-design/destinations/home-prd.md`                                                       | This file.                                                                                                                                                                                                                                                                                               |

---

## 3. Architecture

### 3.1 HomeDestination component tree

```
<HomeDestination>                                  // root, owns transport + state machine
  <HomeGreeting />                                 // §3.1.1
  <TriageStrip />                                  // §3.1.2 — always rendered if data present; otherwise green "All clear"
  <TodayTimeline />                                // §3.1.3 — collapsed if empty
  <WhatsNewDigest />                               // §3.1.4 — collapsed if empty
  <InFlightStrip />                                // §3.1.5 — collapsed if empty
  <LiveActivityRail />                             // §3.1.6 — subordinate; right rail (≥1024px) or bottom strip (<1024px)
</HomeDestination>
```

Each section is its own component so it owns loading / empty / error sub-state independently (§14.5 partial-failure resilience). Sections use the master-PRD shared primitives:

- `<PageHeader>` for the greeting block
- `<StatusPill>` for triage chips (master §4.2)
- `<ItemLink>` for every cross-destination click-through (master §4.3)
- `<EmptyState>` only for the **whole-page first-run welcome** (§14.4); per-section empty does NOT render — sections collapse instead
- `formatRelativeTime(iso)` from `packages/chat-surface/src/util/time.ts`

#### §3.1.1 HomeGreeting

`<h1>Good {time_of_day}, {user_first_name}.</h1>` + sub-line `{tenant_local_date}`.

- `time_of_day` is **server-computed against the tenant timezone** (consistent across substrates).
- `user_first_name` source priority: IdP `given_name` → IdP `name` first token → email local-part capitalized → omit name entirely (`"Good morning."` is acceptable).
- No agent-counts subline (Phase 2 had `agents_working_count` / `needs_you_count` — moved to TriageStrip).

#### §3.1.2 TriageStrip

A **single horizontal strip** of clickable chips. Above the fold, always. The visual centerpiece of the 5-second test.

Logic:

| Condition                | Chip rendered             | Chip color | Click target                                     |
| ------------------------ | ------------------------- | ---------- | ------------------------------------------------ |
| `approvals_waiting > 0`  | `⚠ N approval(s) waiting` | red        | `inbox?filter=approvals`                         |
| `runs_failed_24h > 0`    | `⚠ N failed run(s)`       | red        | `runs?status=failed&window=24h` (or `inbox?...`) |
| `todos_overdue > 0`      | `⚠ N overdue`             | red        | `todos?filter=overdue`                           |
| `todos_due_today > 0`    | `• N due today`           | amber      | `todos?filter=due_today`                         |
| All four counts are zero | `✓ All clear`             | green      | (non-interactive — affirmation, not a CTA)       |

If all four counts are zero **and** the user has had non-zero data in the prior 7 days, render the "All clear" affirmation. If the user has never had data (truly fresh persona), suppress TriageStrip entirely and rely on the welcome-state branch (§14.4).

Counts come from the same `HomePayload.triage_counts` — no separate fetch.

#### §3.1.3 TodayTimeline

A **single chronological list** of every "thing on my plate today" — meetings, routine fires, todos due today, scheduled runs. Merged into one mental model.

Each row:

```
09:30 · Standup                            (Calendar)
11:00 · Q3 metrics digest                  (Routine fires)
14:00 · Review PR #482                     (Todo · due)
16:00 · 1:1 with Marcus                    (Calendar)
```

- Sorted by `when_iso` ascending.
- Status decoration: `in_progress` (now bar), `overdue` (red caret), `completed` (strike-through). `upcoming` is the default.
- Click → `ItemRef` to the source destination (calendar opens external `conferencing_url` in new tab; routine opens routines destination; todo opens todos destination).
- Cap: 8 entries. If more, render "+N more today →" link to a filtered destination view (which destination depends on user intent — drops to `todos?filter=today` by default).
- **Section collapses entirely if `today_timeline.data` is empty** (zero meetings, zero todos due, zero routines firing). No "Nothing on your list" placeholder.

#### §3.1.4 WhatsNewDigest

Past-tense activity since the user's last Home visit.

Header: `WHAT'S NEW · since {formatRelativeTime(since_iso)}`

Each row uses the kind-discriminated copy from `AgentActivityEntry` (§4.3):

```
✓ dispatcher · synced 3 Jira tickets       2h ago
✓ launch-prep · summary posted in #team    11h ago
✗ q3-rollup · failed (auth expired)        12h ago
```

- Cap: 7 entries. Older entries reachable via the LiveActivityRail's "See all activity →" footer link, which deep-links to `inbox?filter=agent_activity&window=since_last_visit`.
- Section collapses entirely if `whats_new.data` is empty.
- Tone glyph (`✓` / `✗` / `⚠`) carries the `tone` field from the entry; never sole carrier of meaning (paired with kind icon + text).

#### §3.1.5 InFlightStrip

Projects with **recent activity** (last 7 days). Not "starred" — _active_. Up to 3 rows:

```
📁 Launch prep · 4 open · last edit 11h ago
📁 Q3 planning · 2 open · last edit 1d ago
```

- Sort: `last_activity_at desc`.
- `open_item_count` = open chats + open approvals + open todos in the project.
- Click → projects destination at `/projects/<id>`.
- Section collapses entirely if `in_flight_projects.data` is empty.

#### §3.1.6 LiveActivityRail

The SSE-driven activity feed. **Subordinate** placement:

- ≥1024px viewport: right-side rail (~240px wide), borderless / low-contrast typography, no card chrome.
- <1024px viewport: collapsed by default to a bottom strip with "Show live activity" toggle.

Entries are `AgentActivityEntry` (same discriminator system as Phase 2 §4.3). Rail caps at 15 most-recent; older entries scroll off (older history reachable via "See all →").

This is the "background hum" surface. Most users will glance at it occasionally; the primary signal lives in TriageStrip + WhatsNewDigest.

Live updates: `useHomeActivityStream` hook prepends incoming SSE entries. New entries fade-in (60ms, respects `prefers-reduced-motion`).

### 3.2 HomePanel component tree

```
<HomePanel>
  <HomeQuickActionsSection />
</HomePanel>
```

Phase 9 keeps QuickActions and **drops** StarredProjectsSection (replaced by InFlightStrip in main). HomePanel is now a single section — defensible because Quick Actions is server-driven and lives separately from the main briefing's narrative.

If product later wants to drop HomePanel entirely (collapse Quick Actions into the main TriageStrip area), this is a one-file change.

### 3.3 Backend `/v1/home` composition

Same shape as Phase 2: single endpoint `GET /v1/home` → `HomePayload`. Lives in `services/backend/src/backend_app/home/`. Handler flow:

1. Resolve `tenant_id` + `user_id` from verified bearer (never from body / query).
2. Cache check (§3.5) — return fresh cached payload if available.
3. Else **parallel fan-out** (`asyncio.gather(..., return_exceptions=True)`):
   - greeting composer (resolves IdP claim + tenant timezone)
   - triage composer (queries: pending approvals, failed runs 24h, overdue todos, todos due today)
   - timeline composer (queries: today's calendar entries, today's routine fires, todos due today, scheduled runs)
   - whats_new composer (queries: runtime events since last visit, filtered noteworthy)
   - in_flight composer (queries: projects with activity in last 7d)
   - quick_actions composer (server config + role filter)
4. Read `previous_visit_at` (used as `since_iso` for whats_new), then update `users.home_last_visit_at = now`.
5. Compose `HomePayload`, write to cache, return.

**Parallelism rule.** One slow upstream does not block others. Each section carries `ok | error | unavailable` status; the response always has the complete shape — per-section errors are signaled in the payload, not via top-level 5xx.

**Facade.** Thin pass-through (§8). Facade does **not** compose — backend has direct DB access; routing composition through the facade would be N additional HTTP hops.

**ai-backend interop.** Backend queries ai-backend over HTTP for run records and runtime events: `GET ai-backend:8000/internal/v1/runs?owner_user_id=...` + `GET .../events?since=...&kinds=...`. One HTTP hop per upstream; facade doesn't see this.

### 3.4 Composer modules (file-level breakdown)

`services/backend/src/backend_app/home/`:

- `__init__.py`
- `route.py` — FastAPI router (`/v1/home`, registration only)
- `aggregator.py` — fan-out orchestration (`compose_home_payload()`)
- `types.py` — Pydantic mirrors of the TS types in §4
- `composers/`
  - `greeting.py` — `compose_greeting(identity, tenant) -> HomeGreeting`
  - `triage.py` — `compose_triage_counts(identity) -> TriageCounts`
  - `timeline.py` — `compose_today_timeline(identity) -> SectionResult[TimelineEntry]`
  - `whats_new.py` — `compose_whats_new(identity, since_iso) -> WhatsNewSection`
  - `in_flight.py` — `compose_in_flight_projects(identity) -> SectionResult[InFlightProject]`
  - `quick_actions.py` — `compose_quick_actions(identity) -> tuple[QuickAction, ...]`
- `sse.py` — SSE stream handler (reused from Phase 2; emits `home_activity` events filtered per §5.2)
- `cache.py` — Redis adapter (in-memory fallback for dev)
- `last_visit.py` — `read_and_advance_last_visit(user_id) -> previous_iso` (single-source-of-truth for visit cutoff)

Each composer is **independently testable**: it takes `(identity, *deps)` and returns a typed section result. Aggregator wires composers + handles partial failure.

### 3.5 Caching

Identical to Phase 2:

- **Key:** `home:v1:{tenant_id}:{user_id}` (Redis prod; in-memory `lru_cache` dev)
- **TTL:** 5 min
- **SWR:** stale serves immediately + async refresh
- **Headers:** `Cache-Control: max-age=0, stale-while-revalidate=300`, `X-Atlas-Cached-At`

**Invalidation triggers** (drop cache key for affected user):

| Trigger                                         | Reason                                 |
| ----------------------------------------------- | -------------------------------------- |
| Run completion (ai-backend) for `owner_user_id` | WhatsNewDigest + triage counts changed |
| Approval enqueued / resolved                    | TriageStrip approvals_waiting changed  |
| Todo created / completed / deleted              | TodayTimeline + triage counts changed  |
| Project activity                                | InFlightStrip changed                  |
| Routine scheduled / fired                       | TodayTimeline changed                  |
| Calendar connector connected / disconnected     | TodayTimeline meetings shape changed   |

Invalidation is best-effort — TTL bounds staleness on misses.

**`since_iso` semantics interact with caching.** The `read_and_advance_last_visit` mutation happens at request-time, not cache-time — so a cache hit serves the stored `since_iso` from when the payload was composed. A user who reloads Home twice within 5 min sees the same `since_iso` both times (stale). **This is intentional** — a 5-min cache window is well below the meaningful resolution of "since when" for a morning briefing. The TTL bounds the staleness; the user's actual visit cadence is captured at fresh-fetch time.

### 3.6 Real-time updates (SSE)

When on Home, frontend opens `GET /v1/home/stream`. Stream emits `home_activity` SSE events whose `data` is a JSON-serialized `AgentActivityEntry` — when noteworthy agent activity (§5.2 filter) lands for `(tenant_id, user_id)`. Frontend prepends to the **LiveActivityRail**, capped at 15.

**Only the LiveActivityRail is live.** Other sections (Triage, Timeline, WhatsNew, InFlight) re-fetch on next Home open or TTL expiry. SSE is for ambient awareness, not state mutation.

**Auth + lifecycle.** Same bearer as `/v1/home`. Stream closes on: navigate away (`AbortController`), 30 min idle (reconnects on interaction), backend graceful shutdown (exponential backoff 1s → 30s, silent — no "paused" indicator).

**Backend reuse.** Subscribes to ai-backend's existing `RuntimeEventEnvelope` pipeline; forwards only entries passing §5.2 filter. No new event bus.

### 3.7 Routing

Home renders at `route.destination === "home"` with no `view` / `id`. Shell mounts `<HomePanel>` into ContextPanel slot, `<HomeDestination>` into main. Click-throughs use `<ItemLink>` (substrate-agnostic `Router<TRoute>` port).

---

## 4. Wire contracts

All types TypeScript-canonical. Python equivalents in `home/types.py` are Pydantic mirrors. **TS file is source of truth.**

### 4.1 `HomePayload`

```typescript
// packages/api-types/src/home.ts

import type { ConversationId, RunId, SkillId } from "./index";
import type { ItemRef } from "./index"; // cross-destination polymorphic link

export type TimeOfDay = "morning" | "afternoon" | "evening" | "late";

export interface HomeGreeting {
  readonly time_of_day: TimeOfDay;
  readonly user_first_name: string | null; // null = caller chose to omit / unavailable
  readonly tenant_local_date: string; // ISO `YYYY-MM-DD`
  readonly tenant_local_iso: string; // full ISO datetime, tenant timezone
}

export interface TriageCounts {
  readonly approvals_waiting: number;
  readonly runs_failed_24h: number;
  readonly todos_overdue: number;
  readonly todos_due_today: number;
}

export type SectionStatus = "ok" | "error" | "unavailable";

export interface SectionResult<T> {
  readonly status: SectionStatus;
  readonly data: ReadonlyArray<T>;
  readonly error_message?: string;
}

export interface WhatsNewSection {
  readonly status: SectionStatus;
  readonly since_iso: string; // when the user previously visited Home
  readonly data: ReadonlyArray<AgentActivityEntry>;
  readonly error_message?: string;
}

export interface HomePayload {
  readonly greeting: HomeGreeting;
  readonly triage: TriageCounts;
  readonly today_timeline: SectionResult<TimelineEntry>;
  readonly whats_new: WhatsNewSection;
  readonly in_flight_projects: SectionResult<InFlightProject>;
  readonly live_activity: SectionResult<AgentActivityEntry>; // initial backfill for the rail
  readonly quick_actions: ReadonlyArray<QuickAction>;
  readonly cached_at: string; // ISO; mirrors X-Atlas-Cached-At
  readonly is_first_run: boolean; // true iff every section is empty AND user has no historical data
}
```

### 4.2 `TimelineEntry` — discriminated union

Merges meetings, routines, todos, runs into one chronological list. The discriminator is `kind`; UI dispatches on `kind` for icon + subtitle.

```typescript
export type TimelineEntryKind =
  | "meeting"
  | "routine_fire"
  | "todo_due"
  | "run_scheduled";

export type TimelineEntryStatus =
  | "upcoming"
  | "in_progress"
  | "completed"
  | "overdue"
  | "missed";

export interface TimelineEntryBase {
  readonly id: string;
  readonly kind: TimelineEntryKind;
  readonly when_iso: string; // when this happens / happened / is due
  readonly title: string;
  readonly subtitle?: string; // backend-composed: "Calendar", "Routine fires", "Due", "Run scheduled"
  readonly status: TimelineEntryStatus;
  readonly target: ItemRef;
}

export interface MeetingTimelineEntry extends TimelineEntryBase {
  readonly kind: "meeting";
  readonly end_iso: string;
  readonly attendee_count: number;
  readonly is_organizer: boolean;
  readonly conferencing_url?: string;
  readonly source_connector: "google_calendar" | "microsoft_calendar" | "other";
}

export interface RoutineFireTimelineEntry extends TimelineEntryBase {
  readonly kind: "routine_fire";
  readonly routine_id: string;
  readonly trigger_kind: "scheduled" | "event_driven" | "manual";
}

export interface TodoDueTimelineEntry extends TimelineEntryBase {
  readonly kind: "todo_due";
  readonly todo_id: string;
  readonly priority: "low" | "med" | "high";
  readonly is_overdue: boolean;
  readonly source_kind: "user" | "chat" | "agent";
}

export interface RunScheduledTimelineEntry extends TimelineEntryBase {
  readonly kind: "run_scheduled";
  readonly run_id?: RunId; // present if already started
  readonly agent_id: string;
  readonly agent_name: string;
}

export type TimelineEntry =
  | MeetingTimelineEntry
  | RoutineFireTimelineEntry
  | TodoDueTimelineEntry
  | RunScheduledTimelineEntry;
```

### 4.3 `AgentActivityEntry` — discriminated union (preserved from Phase 2)

The kind taxonomy and discriminator pattern is preserved. UI dispatches on `kind` to render kind-specific copy / icon / tone. **Never** substring-match `summary`.

```typescript
export type AgentActivityKind =
  | "drafted_artifact"
  | "sent_message"
  | "queued_approval"
  | "risk_signal"
  | "completed_run"
  | "failed_run"
  | "extracted_todos"
  | "ingested_dataset";

export interface AgentActivityEntryBase {
  readonly id: string;
  readonly kind: AgentActivityKind;
  readonly agent_id: string;
  readonly agent_name: string;
  readonly summary: string; // backend-composed
  readonly created_at: string;
  readonly target: ItemRef;
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
  readonly recipient_display: string;
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
  readonly signal_summary: string;
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
  readonly failure_reason_code: string;
  readonly failure_reason_message: string;
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

Backend composes `summary` per kind so localization later wraps one function, not a UI templating maze.

### 4.4 `InFlightProject`

```typescript
export interface InFlightProject {
  readonly project_id: string;
  readonly name: string;
  readonly icon_emoji: string;
  readonly color_hue: number; // 0-359; design tokens consume as oklch hue
  readonly open_item_count: number; // open chats + open approvals + open todos in project
  readonly last_activity_at: string;
}
```

### 4.5 `QuickAction` (preserved from Phase 2)

Server-driven so admin role / plan tier / tenant overrides can adjust without UI deploy.

```typescript
export interface QuickAction {
  readonly id: string;
  readonly label: string;
  readonly icon_name: string; // matches Icon registry; backend allowlist
  readonly target: ItemRef;
  readonly is_admin_only?: boolean; // hidden for non-admins server-side
}
```

Default seed list (baked into Phase 9 code; admin endpoint to mutate is Wave 5+):

| `id`              | Label              | `target.kind`   |
| ----------------- | ------------------ | --------------- |
| `new_chat`        | New chat           | `chat_new`      |
| `new_todo`        | New todo           | `todos_new`     |
| `new_routine`     | Schedule a routine | `routine_new`   |
| `onboard_tool`    | Connect a tool     | `tools_onboard` |
| `invite_teammate` | Invite a teammate  | `team_invite`   |

### 4.6 `ItemRef` (preserved from Phase 2)

Lives in `packages/api-types/src/index.ts` (not `home.ts`) since cross-destination.

Phase 9 **adds** two new variants to support TimelineEntry click-throughs:

```typescript
// Added to existing ItemRef union:
| { readonly kind: "routine"; readonly routine_id: string }
| { readonly kind: "routine_new" }
```

### 4.7 Response headers

| Header                   | Direction | Meaning                                                   |
| ------------------------ | --------- | --------------------------------------------------------- |
| `X-Atlas-Cached-At`      | response  | ISO timestamp the cached payload was composed             |
| `Cache-Control`          | response  | `max-age=0, stale-while-revalidate=300`                   |
| `X-Atlas-Section-Errors` | response  | comma-separated section keys that failed (telemetry-only) |

---

## 5. Storage + retention

### 5.1 Tables read by composers

Home is aggregation-only. **No `home_*` table is created** — except one tiny addition for visit cutoff (§5.2).

| Section                      | Reads from                                                                                                            |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| greeting                     | `users.first_name` (from IdP claim) + `tenants.timezone`                                                              |
| triage.approvals_waiting     | `inbox_items` where `status = waiting AND kind = approval` (or equivalent in approvals table)                         |
| triage.runs_failed_24h       | `ai-backend.runs` where `status = failed AND completed_at > now - 24h AND owner_user_id = caller`                     |
| triage.todos_overdue         | `todos` where `is_overdue = true AND owner_user_id = caller AND status = open`                                        |
| triage.todos_due_today       | `todos` where `due_iso ∈ today_in_tenant_tz AND status = open AND owner_user_id = caller`                             |
| today_timeline.meeting       | `connector_calendar_events` (Google / MS calendar, via connector framework) where `start_iso ∈ today_in_tenant_tz`    |
| today_timeline.routine_fire  | `routines` joined with `routine_fires` where `fire_at ∈ today_in_tenant_tz AND user can see routine`                  |
| today_timeline.todo_due      | same as `triage.todos_due_today` (joined into timeline shape)                                                         |
| today_timeline.run_scheduled | `ai-backend.runs` where `scheduled_for ∈ today OR (status = running AND owner_user_id = caller)`                      |
| whats_new                    | `ai-backend.runtime_events` filtered by §5.3, bounded by `since_iso = users.home_last_visit_at`                       |
| in_flight_projects           | `projects` with `last_activity_at > now - 7d AND user is project_member`, joined with project counts                  |
| live_activity                | `ai-backend.runtime_events` filtered by §5.3, last 24h, capped 15                                                     |
| quick_actions                | static config table `home_quick_actions_config` (single row per tenant, optional overrides; defaults baked into code) |

### 5.2 `users.home_last_visit_at`

**One new column** on `users`: `home_last_visit_at TIMESTAMPTZ NULL`. Read + UPDATE inside `last_visit.py`. Used as `since_iso` for `whats_new`.

Migration:

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS home_last_visit_at TIMESTAMPTZ NULL;
```

Idempotency: `IF NOT EXISTS` so seed runner can also create the row implicitly via persona seeding without a separate alter.

First-time visit (NULL): `since_iso` falls back to `now - 24h` so the WhatsNewDigest renders the last 24h of activity for a brand-new account.

### 5.3 Agent-activity filter rule (preserved from Phase 2)

A runtime event lands in `whats_new` or `live_activity` iff ALL of:

- `event.tenant_id == caller.tenant_id` (server-enforced)
- `event.owner_user_id == caller.user_id` OR emitted by an agent shared with caller
- `event.kind` ∈ {drafted_artifact, sent_message, queued_approval, risk_signal, completed_run, failed_run, extracted_todos, ingested_dataset}
- **agent-initiated** (`event.source` is `agent` or `scheduled_run`; never `user`)
- **not** a subagent internal event

For `whats_new`: bounded by `created_at > since_iso`; cap 7 entries.
For `live_activity`: bounded by `created_at > now - 24h`; cap 15.

### 5.4 Retention

Home stores nothing — retention = cache TTL (5 min). On tenant or user hard-delete: cache key purged in cascade; `users.home_last_visit_at` deleted with the row. Upstream retention (runs / events / todos / approvals / projects) is each owning destination's concern.

### 5.5 Cross-phase coupling

Composers depend on tables owned by other phases:

| Composer dep                | Phase   | If table absent                                 |
| --------------------------- | ------- | ----------------------------------------------- |
| `todos`                     | Phase 3 | Composers return `status: "unavailable"`        |
| `inbox_items`               | Phase 4 | Composers return `status: "unavailable"`        |
| `routines`                  | Phase 5 | Composers return `status: "unavailable"`        |
| `projects`                  | Phase 6 | Composers return `status: "unavailable"`        |
| `connector_calendar_events` | Phase ? | Timeline meeting branch returns empty; no error |

Frontend renders `unavailable` sections as collapsed (same as empty) — no "coming soon" placeholder.

---

## 6. Audit

**Home is read-only — no audit on Home reads.** `GET /v1/home` and `GET /v1/home/stream` write no audit rows. Click-throughs are audited at their destination.

Quick-actions config changes (future `PATCH /v1/admin/home/quick-actions`) ARE audited via `packages/audit-chain` with `(tenant_id, actor_user_id, action="quick_actions.update", before_state, after_state, ts, request_id)`. Wave 2 ships only defaults; the audited write path is the upgrade path.

---

## 7. Dev seed runner (NEW — single source of truth for dev data density)

### 7.1 Goal

Make Home **demoable** for dev personas. A fresh `make dev` against an empty database must yield a Sarah whose Home shows triage chips, a today timeline, a whats-new digest, in-flight projects, and live activity.

**Substrate principle.** The seed writes through the **production** store interfaces — `create_chat`, `create_todo`, `create_project`, `record_run`, `enqueue_approval`. The composers don't branch on `BACKEND_ENVIRONMENT` — production code paths are untouched. Substitution at the data layer, not the composer layer.

### 7.2 `dev_seed.yaml`

Lives at `services/backend/dev_seed.yaml`. Keyed by persona slug. Loaded only when `BACKEND_ENVIRONMENT=development` (same gate as `dev_personas.yaml`).

```yaml
# Loaded only when BACKEND_ENVIRONMENT=development.
# Edit and save — /v1/dev/seed/refresh reloads on file mtime change.

personas:
  sarah_acme:
    chats:
      - slug: q3-planning
        title: "Q3 planning sync"
        last_message_offset: -2h # relative to now
        is_pinned: true
      - slug: launch-prep-thread
        title: "Launch prep — risks review"
        last_message_offset: -11h
        project_slug: launch-prep
      - slug: marcus-1on1
        title: "1:1 prep — Marcus"
        last_message_offset: -1d

    todos:
      - slug: review-pr-482
        text: "Review PR #482 (auth middleware)"
        due_offset: +5h # today, mid-afternoon
        priority: high
        source_kind: agent
        project_slug: launch-prep
      - slug: write-recap
        text: "Write Monday recap"
        due_offset: +7h
        priority: med
      - slug: legal-review # OVERDUE
        text: "Sign off on legal review draft"
        due_offset: -1d
        priority: high

    projects:
      - slug: launch-prep
        name: "Launch prep"
        icon_emoji: "🚀"
        color_hue: 220
      - slug: q3-planning-project
        name: "Q3 planning"
        icon_emoji: "📊"
        color_hue: 160

    routines:
      - slug: morning-digest
        name: "Morning digest"
        fire_offset: +2h # fires at 11am-ish today
        trigger_kind: scheduled

    runs:
      - slug: dispatcher-yesterday
        agent: dispatcher
        status: succeeded
        started_offset: -14h
        completed_offset: -13h45m
        artifacts_produced: 3
      - slug: launch-prep-summary
        agent: summarizer
        status: succeeded
        started_offset: -11h30m
        completed_offset: -11h
      - slug: q3-rollup-failed # for the triage failed_run chip
        agent: rollup
        status: failed
        started_offset: -12h30m
        completed_offset: -12h
        failure_reason_code: mcp_token_expired
        failure_reason_message: "Salesforce token expired"

    approvals:
      - slug: send-email-acme-legal
        kind: approval
        priority: med
        offset: -3h
        agent: outreach
        summary: "Approve sending follow-up to acme-legal@…"
      - slug: edit-jira-ticket
        kind: approval
        priority: high
        offset: -2h
        agent: dispatcher
        summary: "Approve editing JIRA AC-482 status to 'In review'"
      - slug: review-doc-1
        kind: review
        priority: low
        offset: -6h
        agent: drafter
        summary: "Review draft brief — Q3 GTM"

  marcus_admin:
    # Minimal seed — admin persona, less data density to validate role gating
    chats: []
    todos:
      - slug: review-team-perms
        text: "Review team permission audit"
        due_offset: +2h
        priority: med
    approvals: []
```

### 7.3 `DevSeedRunner` class

`services/backend/src/backend_app/dev_seed/runner.py`:

```python
class DevSeedRunner:
    """Loads dev_seed.yaml and writes through production store interfaces.

    Mirrors PersonaLoader: filesystem-backed, mtime-keyed reloads, single
    instance per process. Idempotent — re-running on restart is a no-op
    because every write is upsert-keyed on a deterministic ID derived
    from (persona_slug, item_slug).
    """

    def __init__(
        self,
        path: Path,
        *,
        chat_store: ChatStore,
        todo_store: TodoStore,
        project_store: ProjectStore,
        routine_store: RoutineStore,
        run_recorder: RunRecorder,
        approval_queue: ApprovalQueue,
        persona_loader: PersonaLoader,
    ) -> None: ...

    def seed(self) -> SeedReport:
        """Idempotent — safe to call on every startup.

        Returns a report enumerating: items_created, items_skipped (already exist),
        items_failed (with reason). Failure of one item does not abort the run.
        """
```

**Deterministic IDs.** `chat_id = f"chat_seed_{persona_slug}_{chat_slug}"`. Upserting on this ID means restart re-runs are no-ops; editing `dev_seed.yaml` and reloading creates new items but leaves existing ones intact (unless slug changes, which deletes-and-recreates).

**Relative timestamps.** `last_message_offset: -2h` resolved at seed time against `now`. Re-running every startup means the relative times stay fresh — Sarah's morning never has yesterday's timestamps reading "5 days ago."

### 7.4 Startup hook

`services/backend/src/backend_app/app.py` startup event:

```python
@app.on_event("startup")
async def maybe_run_dev_seed() -> None:
    environment = os.environ.get("BACKEND_ENVIRONMENT", "").strip().lower()
    if environment != "development":
        return
    seed_path = Path(__file__).parent.parent.parent / "dev_seed.yaml"
    if not seed_path.exists():
        return
    runner = DevSeedRunner(seed_path, ...)  # DI of real store handles
    report = runner.seed()
    logger.info("dev_seed", extra={"created": report.items_created, ...})
```

**Fail open.** If the seed file is malformed or a store throws, log + continue. The application starts even with a broken seed — broken seed must not block development.

### 7.5 Dev refresh endpoint

`POST /v1/dev/seed/refresh` — mounted only when `BACKEND_ENVIRONMENT=development` (same gate as `/v1/dev/identity/mint`). Re-loads `dev_seed.yaml` and re-seeds. Returns the `SeedReport`. Use case: edit YAML, refresh without restarting the server.

### 7.6 Idempotency contract

Every store the seed runner writes to must support **upsert by deterministic ID**. If a store today only supports `create_*` with auto-generated IDs, the seed runner is blocked on that store adding `upsert_by_id` (or accepting a caller-provided ID).

Per-store status (Phase 9 must verify and unblock where needed):

| Store           | Upsert support | Phase 9 action                                                          |
| --------------- | -------------- | ----------------------------------------------------------------------- |
| `ChatStore`     | TBD            | Verify; add `upsert_by_id` if missing (small store change)              |
| `TodoStore`     | TBD            | Verify                                                                  |
| `ProjectStore`  | YES (Phase 6)  | Already supports caller-provided `project_id`                           |
| `RoutineStore`  | TBD            | Verify                                                                  |
| `RunRecorder`   | TBD            | Runs are in ai-backend, not backend — seed runner calls ai-backend HTTP |
| `ApprovalQueue` | TBD            | Verify                                                                  |

Stores that don't yet support upsert get a small companion patch in their owning module — Phase 9 absorbs that work (called out per agent in §17).

### 7.7 Anti-goals

- **No fake fixtures in production code paths.** Composers don't read fixtures; they read stores. The seed lives in the store.
- **No "if dev, use fixture" branches** in `compose_*()` functions.
- **No fixture data in tests.** The seed runner is a dev convenience, not a test seed. Tests use unit-test fakes / `InMemoryStore`.
- **No marketing demo data** (no synthetic "Acme Q4 launch" content beyond the minimal density needed for Home to render meaningfully).

---

## 8. Facade SSE proxy fix (the current 404)

### 8.1 The bug

`services/backend-facade/src/backend_facade/home_routes.py` registers `GET /v1/home` only. The backend has `/v1/home/stream` registered at `services/backend/src/backend_app/home/sse.py`. The facade does not proxy the stream — every SSE reconnect from the frontend hits a 404 at the Vite proxy → facade boundary.

Console reproduction: `GET http://127.0.0.1:5173/v1/home/stream?... 404 (Not Found)`.

### 8.2 The fix

Add a streaming proxy in `home_routes.py`:

```python
@app.get("/v1/home/stream")
async def stream_home(request: Request) -> StreamingResponse:
    identity = FacadeAuthenticator.authenticate_request(request)
    return await forward_sse(
        app,
        "/v1/home/stream",
        target="backend",
        params=identity.scoped_params(),
        identity=identity,
        heartbeat_seconds=30,
    )
```

`forward_sse` is the same helper used by ai-backend's run-stream proxy (already exists at `backend_facade/app.py` per Phase 0-B SSE work; if it doesn't, Phase 9 adds it alongside `forward_json` — single helper, used by all SSE proxies).

### 8.3 Test

`services/backend-facade/tests/test_home_routes.py::test_stream_proxies_to_backend`:

- mock backend SSE response
- call `GET /v1/home/stream` against facade
- assert response is `text/event-stream`, identity headers forwarded, query params scoped

---

## 9. Authorization

**§9.1 Tenant isolation.** `tenant_id` from verified bearer's claims **server-side**. Every fan-out passes resolved `(tenant_id, user_id)` explicitly; downstream services re-verify. Cross-tenant header injection ignored.

**§9.2 Per-user scope.** Each composer enforces ACL at the upstream's query layer:

- triage.approvals_waiting → only approvals targeted at this user
- triage.runs_failed_24h → only `owner_user_id == caller`
- triage.todos\_\* → only `owner_user_id == caller`
- timeline.meetings → only meetings the user is invited to
- timeline.routines → only routines the user owns or is a member of
- whats_new / live_activity → §5.3 filter (`owner_user_id == caller` OR agent shared with caller)
- in_flight_projects → only projects the user is `project_member` of

ACL enforcement is at each upstream's query layer; the aggregator does not duplicate ACL logic.

**§9.3 Role.** Home is not role-gated — every authenticated user gets a Home. Only role-aware element is `QuickAction.is_admin_only`. Backend omits actions from the array when the user lacks the role; frontend never evaluates `is_admin_only`.

**§9.4 Guests.** Per [PRD.md §13 #3](../PRD.md), multiplayer / guest support is undecided. Until then, guests do not see Home — Router enforces.

---

## 10. Pagination + search

**§10.1 No pagination.** Home is bounded-small. Caps: triage chips ≤ 4, today_timeline ≤ 8, whats_new ≤ 7, in_flight_projects ≤ 3, live_activity ≤ 15, quick_actions ~5. Total payload ≤ ~50 entries.

**§10.2 No search.** Search is ⌘K palette (Wave 6).

**§10.3 "See all" deep-link endpoints.**

| Section            | Target                                                                       |
| ------------------ | ---------------------------------------------------------------------------- |
| triage.approvals   | `inbox?filter=approvals`                                                     |
| triage.runs_failed | `runs?status=failed&window=24h`                                              |
| triage.todos\_\*   | `todos?filter=overdue` / `todos?filter=due_today`                            |
| today_timeline     | `todos?filter=today` (default — primary intent is "what's left to do today") |
| whats_new          | `inbox?filter=agent_activity&since=<since_iso>`                              |
| in_flight_projects | `projects?filter=in_flight`                                                  |
| live_activity      | `inbox?filter=agent_activity&window=24h`                                     |

For not-yet-shipped destinations, link renders disabled with tooltip "Coming in Phase N."

---

## 11. Accessibility

Per master PRD §3.6 (WCAG 2.1 AA):

- **Semantic structure.** `<main aria-label="Home destination">`; greeting is `<h1>`; TriageStrip is `<nav aria-label="Triage">` with `<button>` chips; sections each have `<h2>`. LiveActivityRail is `<aside aria-label="Live agent activity">`.
- **Focus.** All interactives are `<button>` or `<a>`; visible focus ring via `--ring-color`. Tab order top-down. `Enter` and `Space` activate.
- **Color is never sole carrier.** Triage chips pair color (red/amber/green) with text + icon. Timeline status uses icon + label. WhatsNew tone glyphs pair with kind icon + text.
- **Reduced motion.** SSE fade-in (60ms) respects `prefers-reduced-motion: reduce`.
- **Live announcements.** LiveActivityRail is `aria-live="polite"` + `aria-relevant="additions"`. New entry announces `{agent_name} {summary}`; older entries do not re-announce.
- **Tests.** `axe-core` clean on every state (§18.5).

---

## 12. Performance

Master §3.7 targets: **LCP < 2.5s** cold, **INP < 200ms**, **CLS < 0.05**.

- **LCP element is the greeting + TriageStrip together** (one row). Renders the instant the payload arrives. Layout-locked min-heights:

  ```typescript
  const SECTION_MIN_HEIGHTS = {
    greeting_with_triage: 96,
    today_timeline: 200,
    whats_new: 200,
    in_flight: 96,
    live_activity_rail: 320,
  };
  ```

- **Initial fetch is one round-trip.** `GET /v1/home` returns full payload. SSE stream opens **after** payload resolves; never blocks LCP.
- **Re-renders.** Shell does not re-mount on Home navigation. Within Home, sections are `React.memo`'d by their payload slice.
- **SSE lifecycle.** `useEffect` cleanup with `AbortController` cancels on unmount; one stream per tab.
- **Backend budget.** `/v1/home` p95 < 400ms warm (cache hit) / < 1500ms cold (parallel 6-composer fan-out). `/v1/home/stream` 30s keepalive ping; memory cost bounded by concurrent-home-user count.

---

## 13. Telemetry

Per master §3.8 — every user-meaningful action emits an OTel span. **All IDs SHA-256 hashed with per-tenant salt.**

### 13.1 Spans

| Span name                 | Key attributes                                            | When                                |
| ------------------------- | --------------------------------------------------------- | ----------------------------------- |
| `home.open`               | `cached`, `cache_age_ms`, `is_first_run`                  | HomeDestination mount               |
| `home.section_view`       | `section`, `entry_count`, `section_status`                | Per section, on render              |
| `home.triage_chip_click`  | `chip_kind` (approval / failed_run / overdue / due_today) | TriageStrip chip click              |
| `home.timeline_open`      | `kind`, `status`                                          | TodayTimeline row click             |
| `home.whats_new_open`     | `kind`, `tone`                                            | WhatsNewDigest row click            |
| `home.in_flight_open`     | `project_id_hash`                                         | InFlightStrip row click             |
| `home.live_activity_open` | `kind`, `agent_id_hash`                                   | LiveActivityRail entry click        |
| `home.quick_action`       | `action_id`                                               | Quick action click                  |
| `home.section_retry`      | `section`                                                 | Section retry after partial-failure |
| `home.sse_connect`        | —                                                         | SSE opens                           |
| `home.sse_disconnect`     | `reason`                                                  | SSE closes                          |
| `home.sse_event_received` | `kind` (sampled 1/10)                                     | Each SSE event                      |

Every span carries `tenant_id` + `user_id_hash` + `destination="home"` by default.

### 13.2 Backend logs

Structured logs (with `request_id` correlation) on: cache hit/miss/refresh, each composer fan-out (latency + status), per-section failure, SSE backpressure, **dev seed runner reports** (created / skipped / failed counts).

---

## 14. States

Per master §3.10. Every state designed + tested.

**§14.1 Loading.** Skeleton sections in their final positions. `aria-hidden="true"` during load; announcement comes via `aria-live` when content arrives.

**§14.2 Ready (returning user with data).** Greeting + TriageStrip + populated sections per data availability. Empty sections **collapse** (no placeholder). The whole "did you visit?" cutoff text in WhatsNewDigest reflects `since_iso`.

**§14.3 Time-of-day variants.**

| Time          | Greeting         | Today timeline behavior                                                    |
| ------------- | ---------------- | -------------------------------------------------------------------------- |
| morning       | "Good morning"   | All today entries upcoming                                                 |
| afternoon     | "Good afternoon" | Past entries strike-through; "Up next" sub-header before first upcoming    |
| evening       | "Good evening"   | "Today" shifts to past-tense for completed; "Tomorrow preview" row appears |
| late (>22:00) | "Working late?"  | "Tomorrow preview" promoted above "Today (today)"                          |

The tomorrow-preview is a separate `tomorrow_preview: SectionResult<TimelineEntry>` field on `HomePayload`. Empty unless `time_of_day === "evening" | "late"`.

**§14.4 Empty (first-run / fresh persona).**

When `is_first_run === true` (every section empty AND user has no historical data), replace the whole briefing with a welcome card:

```
Welcome to Atlas.
Atlas works across your tools while you focus.

[💬 New chat]  [📋 Schedule a routine]  [🔌 Connect a tool]
```

TriageStrip, TodayTimeline, WhatsNewDigest, InFlightStrip, LiveActivityRail are all suppressed. HomePanel's QuickActions still renders (the welcome buttons are essentially horizontal QuickActions).

Backend computes `is_first_run` as:

```
triage all zero
AND today_timeline.data empty
AND whats_new.data empty
AND in_flight_projects.data empty
AND user has zero historical chats / runs / todos
```

Frontend trusts the boolean; doesn't re-derive.

**§14.5 Partial failure.** One section errors, others render. The errored section shows a section-local card: `⚠ Couldn't load {section}. [↻ Retry]`. Retry calls `GET /v1/home?refresh_section={name}`.

**§14.6 Whole-Home error.** Backend 5xx → single `<ErrorPanel>` with retry. 401 from expired bearer triggers existing dev-IdP auto-mint flow first.

**§14.7 Offline.** `navigator.onLine === false` (or transport network error): read last successful `HomePayload` from `KeyValueStore` (key `home:last_payload:v1`, one payload per user). Render with banner: `Offline — showing your last briefing. (cached at {relative time})`. Click-throughs requiring network are muted; auto-retry on `online` event.

**§14.8 Stale (`cached_at` > 5min).** SWR served stale before refresh landed → small hint above WhatsNewDigest: `↻ Refreshing…`.

---

## 15. Cross-destination references

**Home only links out.** Every clickable item navigates via `<ItemLink>` (master §4.3) — no bespoke navigation.

| Home element                | Resolves to                                   | Mechanism                                       |
| --------------------------- | --------------------------------------------- | ----------------------------------------------- |
| TriageStrip approval chip   | inbox?filter=approvals                        | `<ItemLink kind="inbox_filter">`                |
| TriageStrip failed-run chip | runs?status=failed&window=24h                 | `<ItemLink kind="runs_filter">` (Phase 7+)      |
| TriageStrip todo chips      | todos?filter=overdue / todos?filter=due_today | `<ItemLink kind="todos_filter">`                |
| TodayTimeline meeting       | external `conferencing_url`                   | `<a target="_blank" rel="noopener noreferrer">` |
| TodayTimeline routine       | routines/<id>                                 | `<ItemLink kind="routine">`                     |
| TodayTimeline todo          | todos/<id>                                    | `<ItemLink kind="todo">`                        |
| TodayTimeline run           | runs/<id>                                     | `<ItemLink kind="run">`                         |
| WhatsNewDigest entry        | polymorphic `ItemRef` per entry kind          | `<ItemLink>` registry                           |
| InFlightStrip row           | projects/<id>                                 | `<ItemLink kind="project">`                     |
| LiveActivityRail entry      | polymorphic `ItemRef`                         | `<ItemLink>` registry                           |
| QuickAction                 | per `target` field                            | `<ItemLink>` registry                           |

**Delete cascade — implicit, source-of-truth-driven.** Home is a view; underlying destinations are the source of truth. Cache invalidation (§3.5) handles propagation.

**Stale-entry resilience.** Clicking a deleted item routes to the destination's not-found state; Home does not pre-validate references on cache read.

---

## 16. Desktop substrate caveats

**None.** Home is plain React + transport call + SSE. No file picker, no native notifications, no OS clipboard. Cross-destination links use the substrate-agnostic `Router<TRoute>` port. External meeting links (`conferencing_url`) open via the existing URL-handler port — no Home special-casing.

---

## 17. Implementation phasing — 8 narrow agents

Phase 9 splits into **8 narrow parallel agents**. Each ≤30 min, ≤1000 LOC. Two of them (P9-A6 facade, P9-A7 seed) are independent and can ship first.

**Worktree discipline (repeat for every agent prompt):**

- Stay inside `.claude/worktrees/<id>/`. Branch BEFORE first change. Never write to or commit on the main repo path.
- After merge: `git worktree remove -f -f` + `git branch -d` + `git branch -D worktree-agent-<id>`.

### Backend track

**P9-A1 — `api-types` HomePayload v2 contract**

- Branch: `worktree-agent-phase9-api-types`
- Files: `packages/api-types/src/home.ts` (rewrite), `packages/api-types/src/index.ts` (extend `ItemRef` with `routine` / `routine_new` variants)
- Deliverable: full TS contract per §4. Re-export from `index.ts`. Zero changes to existing consumers other than removed types (`PinnedChatSummary`, `RecentRunSummary`, `FavoriteToolSummary`, `StarredProjectSummary`, `MeetingSummary` — replaced by `TimelineEntry` discriminator).
- Test: `npm run typecheck --workspace @enterprise-search/api-types` clean.

**P9-A2 — backend `triage` + `last_visit` modules**

- Branch: `worktree-agent-phase9-backend-triage`
- Files: `services/backend/src/backend_app/home/composers/triage.py`, `services/backend/src/backend_app/home/last_visit.py`, migration for `users.home_last_visit_at`.
- Deliverable: `compose_triage_counts(identity) -> TriageCounts`, `read_and_advance_last_visit(user_id) -> previous_iso`. Composer queries the 4 sources (approvals, runs, todos×2) in parallel via `asyncio.gather`.
- Tests: unit tests for each query, mutation idempotency, NULL-first-visit fallback to `now - 24h`.

**P9-A3 — backend `timeline` composer**

- Branch: `worktree-agent-phase9-backend-timeline`
- Files: `services/backend/src/backend_app/home/composers/timeline.py`, mirrors in `home/types.py`.
- Deliverable: `compose_today_timeline(identity) -> SectionResult[TimelineEntry]`. Queries 4 upstreams (calendar, routines, todos, runs) in parallel; merges into single sorted list; emits discriminated entries per §4.2.
- Tests: per-kind composition; sort order; today-bounded; tenant timezone correct.

**P9-A4 — backend `whats_new` + `in_flight` composers + aggregator rewrite**

- Branch: `worktree-agent-phase9-backend-whats-new`
- Files: `services/backend/src/backend_app/home/composers/whats_new.py`, `composers/in_flight.py`, `aggregator.py` (rewrite), `types.py` (final shape per §4.1), `route.py` (registration uses new aggregator).
- Deliverable: `compose_whats_new(identity, since_iso) -> WhatsNewSection`, `compose_in_flight_projects(identity) -> SectionResult[InFlightProject]`. Aggregator orchestrates ALL composers (greeting, triage, timeline, whats_new, in_flight, live_activity, quick_actions) in parallel; computes `is_first_run`.
- Tests: aggregator partial-failure (one composer raises, others render); `is_first_run` logic; `since_iso` propagation.

**P9-A5 — facade `/v1/home/stream` SSE proxy**

- Branch: `worktree-agent-phase9-facade-sse`
- Files: `services/backend-facade/src/backend_facade/home_routes.py` (add stream route), `services/backend-facade/tests/test_home_routes.py` (add proxy test). If `forward_sse` helper doesn't exist alongside `forward_json`, add it in `backend_facade/app.py`.
- Deliverable: `GET /v1/home/stream` proxies to backend with verified identity in scoped params + service-token headers; passes through `text/event-stream`.
- Tests: per §8.3.

**P9-A6 — dev seed runner**

- Branch: `worktree-agent-phase9-dev-seed`
- Files: `services/backend/dev_seed.yaml` (NEW), `services/backend/src/backend_app/dev_seed/__init__.py`, `dev_seed/runner.py`, `dev_seed/schema.py` (Pydantic models for the YAML), `app.py` (startup hook), `dev_idp/routes.py` (mount `POST /v1/dev/seed/refresh`). If any store lacks `upsert_by_id`, add it as part of this agent's scope (mention in PR description).
- Deliverable: per §7. Seed runs on dev startup; `POST /v1/dev/seed/refresh` re-runs.
- Tests: load + parse `dev_seed.yaml`; idempotent re-seed; malformed-yaml fails open (logs, doesn't crash startup); per-persona seed correctness.

### Frontend track

**P9-B1 — `HomeDestination` rewrite + `TriageStrip` + `TodayTimeline`**

- Branch: `worktree-agent-phase9-frontend-main`
- Files: `packages/chat-surface/src/destinations/home/HomeDestination.tsx` (rewrite), `sections/HomeGreeting.tsx` (rewrite — drop counts subline), `sections/TriageStrip.tsx` (NEW), `sections/TodayTimeline.tsx` (NEW), `sections/timeline-entry-renderer.tsx` (NEW — kind-discriminator dispatch). **Delete** `sections/HomePinnedChatsGrid.tsx`, `HomeRecentRunsList.tsx`, `HomeFavoriteToolsList.tsx`, `HomeTodaysFocusList.tsx`, `HomeUpcomingMeetingsList.tsx`.
- Deliverable: Per §3.1.1-3.1.3. Empty-collapse logic. First-run welcome branch.
- Tests: per-state rendering (loading / ready / partial-error / first-run / offline); kind-discriminator dispatch; ItemLink-only navigation.

**P9-B2 — `WhatsNewDigest` + `InFlightStrip` + `LiveActivityRail` + SSE hook + HomePanel cleanup**

- Branch: `worktree-agent-phase9-frontend-rail`
- Files: `sections/WhatsNewDigest.tsx` (NEW), `sections/InFlightStrip.tsx` (NEW), `sections/LiveActivityRail.tsx` (NEW — replaces old `HomeAgentActivityFeed.tsx`), `sse-stream.ts` (rewrite to feed the rail, not the top-of-page), `HomePanel.tsx` (drop `HomeStarredProjectsSection`; keep `HomeQuickActionsSection`), `destinations/home/index.ts` (re-export update). **Delete** `sections/HomeAgentActivityFeed.tsx`, `sections/HomeStarredProjectsSection.tsx`.
- Deliverable: Per §3.1.4-3.1.6 + §3.2. SSE hook prepends to rail, capped 15. Responsive: rail right-side ≥1024px, collapsible bottom strip <1024px.
- Tests: per-state rendering; SSE hook subscribe/unsubscribe; rail responsive behavior; quick-action click → `ItemLink`.

### Merge order

1. **P9-A1** (api-types) → blocks everything else
2. **P9-A2, A3, A4, A5, A6** run in parallel after A1 → backend + facade + seed
3. **P9-B1, B2** run in parallel after A1 → frontend
4. Orchestrator audit + test before merge: per-service suites green; `make test` cross-service green; browser-verify Home with the dev seed.

### File boundaries

- Backend agents never touch `packages/chat-surface/`.
- Frontend agents never touch `services/*`.
- All agents may touch `docs/` only to file a contract bug back to the orchestrator (STOP + report; do not resume until orchestrator fixes the doc).

---

## 18. Test plan

### 18.1 Frontend unit (`packages/chat-surface/`)

- HomeGreeting renders for each `time_of_day`; first-name pulled from payload; null name → omits gracefully.
- TriageStrip: each chip variant renders with correct text + ItemLink target; all-zero + historical-data renders "All clear"; all-zero + first-run suppresses entirely.
- TodayTimeline: per-kind dispatch (meeting / routine / todo / run); sort order; "+N more" link when >8; collapses when empty.
- WhatsNewDigest: per-kind dispatch (8 activity kinds); `since_iso` header; collapses when empty.
- InFlightStrip: rows with counts + last-activity; collapses when empty.
- LiveActivityRail: SSE hook subscribes on mount, unsubscribes on unmount, prepends events, caps 15; `aria-live="polite"`; responsive (right-side ≥1024px / bottom-strip <1024px).
- First-run welcome state renders when `is_first_run === true`.
- Whole-Home error renders `<ErrorPanel>` with retry.
- Partial-error: one section in error, others ready; section retry button calls `?refresh_section=<name>`.
- Offline: reads from `KeyValueStore` + banner.
- Skeleton heights match `SECTION_MIN_HEIGHTS`; CLS < 0.05.
- Every click-through goes through `<ItemLink>` — no `window.location` or `router.navigate` calls in section components.

### 18.2 Backend unit (`services/backend/tests/unit/home/`)

- `compose_greeting`: `time_of_day` against tenant tz (not server tz); first-name source priority (IdP given_name → name first token → email local → null).
- `compose_triage_counts`: each of 4 queries; tenant isolation; per-user scope; partial failure (one query throws → status="error" for that field? or whole composer status? — composer aggregates 4 queries, returns 4 numeric counts; one query failure logs + uses 0 + sets a section warning header — assert via `X-Atlas-Section-Errors`).
- `compose_today_timeline`: per-kind composition; merge sort; today-bounded by tenant tz; sub-query failure → partial section status.
- `compose_whats_new`: §5.3 filter; `since_iso` bounding; cap 7; first-visit NULL → falls back to `now - 24h`.
- `compose_in_flight_projects`: `last_activity_at > now-7d`; project_member scope; cap 3.
- `aggregator.compose_home_payload`: all-ok → full shape; one composer throws → partial; all composers throw → still 200 with every section in error; `is_first_run` computed correctly.
- `read_and_advance_last_visit`: returns previous value; advances to now; concurrent-call safety (assert via lock or row-version test).
- Quick actions filter strips `is_admin_only` for non-admins.

### 18.3 Backend integration (`services/backend/tests/integration/home/`)

- `GET /v1/home` returns 200 with full shape.
- **Tenant isolation hard requirement:** two users in different tenants, neither sees the other's data; cross-tenant header injection ignored.
- Cache hit returns `X-Atlas-Cached-At`; miss fan-outs and caches; SWR serves stale + async refreshes.
- Invalidation triggers (§3.5): writing each trigger source drops the cache key.
- `?refresh_section=triage` bypasses cache for one composer only.
- **Partial failure:** one composer errors → 200 with `status: "error"` for that section; all composers error → 200 with every section in error (NOT 5xx — preserves offline fallback).
- Backend itself down: 5xx; facade forwards 5xx transparently.

### 18.4 SSE integration

- `/v1/home/stream` opens with valid bearer; rejects invalid.
- Stream emits agent events; filters per §5.3.
- 30s keepalive; disconnect on auth revocation.
- **Facade proxy (§8.3):** facade forwards SSE; identity headers scoped; query params passed through.

### 18.5 Dev seed runner tests

- Loads `dev_seed.yaml` and seeds Sarah's data; restart re-runs no-op.
- Malformed YAML logs error + startup succeeds (fail open).
- `POST /v1/dev/seed/refresh` re-loads file + re-seeds.
- Per-persona role gating (admin seed differs from employee seed if configured).
- Each store's `upsert_by_id` is exercised; assert idempotency.

### 18.6 Frontend integration — typical morning flow

MSW-mocked. Open Home → assert greeting + triage chips + today timeline + whats new + in-flight rows + live rail render. Click triage chip → assert route transition. Click timeline meeting → assert external link opens. SSE event delivered after 500ms → assert it lands at top of LiveActivityRail. Navigate back → assert cache hit (no second `GET /v1/home`).

### 18.7 a11y + performance

- `axe-core` zero violations on loading / ready / partial-error / first-run / offline / error.
- Lighthouse budget: LCP < 2.5s, INP < 200ms, CLS < 0.05.
- `React.Profiler`: navigating Home ↔ chats does not re-mount the shell.

---

## 19. Anti-goals for this phase

Explicitly OUT OF SCOPE.

- **No write operations from Home.** No pinning, completing, starring, approving from Home — every action drills into the source destination.
- **No per-user section reorder** (Wave 4+).
- **No per-user "today" window length** (always tenant-local calendar day).
- **No third-party feed widgets** (LinkedIn / Twitter / RSS / news).
- **No marketing copy / upsells.** Empty-state and connector-CTA are the only "upsells" and live where missing data would.
- **No global search bar on Home** (search is ⌘K — Wave 6).
- **No "messages from Atlas team" surface.**
- **No analytics dashboards** — Home is a briefing, not a metrics page.
- **No drag-and-drop reordering.**
- **No background polling** — live updates are SSE-driven only.
- **No localStorage for the activity rail.** Only `KeyValueStore` for offline-fallback of last `HomePayload`.
- **No inline approve / dismiss on the LiveActivityRail.** Approvals route to inbox. (Phase 11+ may inline.)
- **No fixture-data branches in production composers.** The seed lives in the store. (§7.7)

---

## 20. Open questions for product (parth)

Each has a recommended default. Adopt unless product disagrees.

**Q1 — "All clear" affirmation: render or suppress?**
→ **Render when user has historical data; suppress when truly fresh.** A returning user benefits from positive confirmation that triage is empty (vs the absence of red — ambiguous). A first-run user already sees the welcome card; affirmation is redundant.

**Q2 — Tomorrow preview threshold.**
→ **Appear after 17:00 tenant-local time.** Earlier feels premature; later (e.g., 22:00) misses end-of-day planners.

**Q3 — InFlightStrip "in flight" definition.**
→ **`last_activity_at > now - 7d`.** Shorter (24h / 3d) excludes long-horizon projects; longer (14d / 30d) makes "in flight" meaningless. 7d aligns with weekly cadence.

**Q4 — WhatsNewDigest cap (7) vs LiveActivityRail cap (15).**
→ **Keep distinct.** Digest is past-tense summary (low cap = high signal); Rail is ambient stream (higher cap = more peripheral visibility).

**Q5 — First-run welcome — show one CTA or three?**
→ **Three: New chat / Schedule a routine / Connect a tool.** New chat alone is too thin a first step; three covers most user intents. Keep buttons large + touch-friendly.

**Q6 — Dev seed reload on save: filesystem watcher or explicit refresh endpoint?**
→ **Explicit `POST /v1/dev/seed/refresh`.** Filesystem watchers are flaky across substrates (macOS / WSL / Docker volumes). The dev endpoint is also useful for resetting state between manual test runs.

**Q7 — Greeting "Working late?" copy at 22:00+.**
→ **Ship as default.** Light personality consistent with Atlas's voice. If product wants neutral copy, easy single-string swap.

**Q8 — Welcome-state CTA buttons — `<ItemLink>` or `<button onClick>`?**
→ **`<ItemLink kind="chat_new">`-style.** Same substrate-agnostic Router port everything else uses. No special case.

---

## 21. References

- [PRD.md](../PRD.md) — workspace shell + composer + thread canvas foundation
- [destinations-master-prd.md](../destinations-master-prd.md) — §3 enterprise checklist · §4 shared primitives · §5.1 Home
- Phase 2 home-prd.md — historical version (this file is the rewrite; Phase 2 retained in git history only)
- Current code: `packages/chat-surface/src/destinations/home/HomeDestination.tsx` (the 3-section seed Phase 2 left behind, to be fully replaced by P9-B1/B2)
- Backend: `services/backend/src/backend_app/home/` (Phase 2 route + stubs; Phase 9 rewrites composers + aggregator)
- Facade: `services/backend-facade/src/backend_facade/home_routes.py` (Phase 2 `/v1/home` proxy; Phase 9 adds stream proxy)
- Dev IdP pattern (mirrored for seed runner): `services/backend/dev_personas.yaml` + `services/backend/src/backend_app/dev_idp/personas.py`
- Service guides: [services/backend/CLAUDE.md](../../../services/backend/CLAUDE.md) · [services/backend-facade/CLAUDE.md](../../../services/backend-facade/CLAUDE.md) · [packages/api-types/CLAUDE.md](../../../packages/api-types/CLAUDE.md)
