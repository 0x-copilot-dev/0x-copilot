# Phase 4 — Remaining destinations · Implementation PRD

> Branch: `feat/desktop-redesign` · Worktree: `/Users/parthpahwa/Documents/work/enterprise-search-redesign`
> Design source of truth: [`docs/plan/desktop-redesign/design-reference/DESIGN-SPEC.md`](../design-reference/DESIGN-SPEC.md) §3 (List destinations), §5 (modal/flow patterns), §0 (tokens/dims).
> Plan: [`docs/plan/desktop-redesign/PLAN.md`](../PLAN.md) Phase 4 (§8), IA map (§5), consolidation map (§7), sequencing (§9).
> Template: [`docs/plan/desktop-redesign/_TEMPLATE.md`](../_TEMPLATE.md) — all 12 sections below, in order.

---

## 1. Context & problem

Phase 3 mounts **Run** as the flagship cockpit. Phase 4 fills the other five of the six locked destinations so the shell (Phase 2) is fully navigable: **Chats** (conversation archive; reopen → Run), **Projects** (grid + detail), **Activity** (recast run history that absorbs the old Agents + Inbox + audit-log surfaces), **Tools** (the renamed connectors destination with a per-tool Read / Read & act / Off control and a Connect flow), and **Skills** (the renamed skill catalog with Run / Edit / New skill). Most of these components already exist in `packages/chat-surface/src/destinations` as pure-presentation shells (`ProjectsDestination`, `ConnectorsDestination`, `ToolsDestination`, `ChatsDestination`/`ChatsSidebar`, `AgentsDestination`, `InboxDestination`) built to earlier ("12-destination") PRDs, so the work is **consolidate + recast + wire + fill holes**, not greenfield. This phase is grounded in DESIGN-SPEC §3, which fixes the shared `.pg` (max 960) list surface, the `.rowlist`/`.grid2`/`.grid3` shapes, per-destination copy, and the mandatory 4-state machine (loading / error / empty / ready) for every list destination.

Why now: Phase 2 delivered the profile-gated `destinations.ts` and mounted the destination outlet; the rail now points at six slugs, three of which (Activity, Tools, Skills) either have no component or point at a component whose _concept_ no longer matches the redesigned label. Until Phase 4 lands, four rail entries dead-end or render a stale surface. Phase 4 depends on Phase 2 (IA + outlet) and, for the reopen-into-Run affordance, on Phase 3 (Run destination + `ArtifactRoute.run`). It blocks the Phase 6 command-palette "Go to …" entries and the end-to-end live smoke.

---

## 2. Goals / Non-goals

### Goals

- Ship five list destinations on the shared `.pg` surface (DESIGN-SPEC §3) with the **4-state machine** (loading skeleton / error+Retry / per-view empty copy / ready) in each.
- **Chats** = archive/list (pinned / recent / archived sections), row = title + status chip + preview + mono model + mono time; "New chat" and row click both open **Run** via `ArtifactRoute.run` (Phase 3).
- **Projects** = card grid → detail (chats list + files list), reusing `ProjectsDestination` + `ProjectDetailView`.
- **Activity** = run history grouped by day (`.act-day` dividers), one destination that **absorbs** the old Agents, Inbox, and audit-log surfaces; live run row → Run; copy points retention/export/delete at **Settings → Privacy**.
- **Tools** (= connectors) = connected list with per-tool segmented **Read / Read & act / Off** + "Connect a tool" → **ConnectModal** (§5), generic-SaaS-first catalog (Notion/Linear/Slack/Google/GitHub/Stripe…).
- **Skills** (= skill catalog) = card grid (name, sub, N runs) with **Run / Edit / New skill**, backed by `/v1/skills`.
- Fold `home` / `library` / `inbox` / `todos` / `routines` / `agents` out of the top-level IA; redirect their routes to their new homes (Activity, Settings→Privacy, or nowhere).
- Keep every touched component **framework-agnostic** (props/ports only) so both `apps/frontend` and `apps/desktop` consume one copy; keep `apps/frontend` behaviorally green.

### Non-goals (explicitly deferred)

- **Run cockpit internals** (timeline, Studio/Focus, approvals, streaming) — Phase 3.
- **Settings surfaces** (Privacy & retention, Model & behavior approval policy, memory review) — Phase 5. Phase 4 only _links_ to them.
- **Command palette `⌘K` "Go to <dest>"** and global shortcuts — Phase 6.
- **New backend list/aggregation endpoints** for Activity run history and a Projects `files` list — see §11 (backend gaps). Phase 4 ships the presentation + a _composition_ binder over existing endpoints and flags the endpoints that must land in the backend workstream; it does not author new Python services.
- **Team-profile variants** of these destinations (shared projects ACL editing, team activity) — gated off under `single_user_desktop`; surfaced only when `ENTERPRISE_DEPLOYMENT_PROFILE=team`.
- **Skills authoring editor** beyond opening the existing editor route (the multi-step skill _builder_ is a separate effort); Phase 4 wires Run / Edit (open) / New (open).

---

## 3. User stories

Roles: **Solo user** (primary, `single_user_desktop`), **Team admin** (only where profile-gated), **Developer/maintainer** (DX/architecture).

### Chats

- **US-4.1 — Solo user, browse & reopen.** _As a Solo user, I want my past conversations grouped into pinned / recent / archived, so that I can find and reopen any thread._
  - **Given** the archive loads and `/v1/agent/conversations` returns threads, **when** the Chats destination renders, **then** rows appear under Pinned / Recent / Archived, each with title, a status chip (running/done/paused/archived), a one-line preview, mono model tag, and mono relative time.
  - **Given** a ready archive, **when** I click a row (or press Enter on it), **then** the host navigates to `ArtifactRoute.run` (reopen → Run), never to a dead chat canvas.
  - **Given** a ready archive, **when** I click "New chat", **then** the host opens Run on a fresh conversation.
  - **Given** the list is still loading, **when** the destination renders, **then** a skeleton row list (`data-state="loading"`) shows, not a spinner-only blank.
  - **Given** the fetch fails, **when** the destination renders, **then** an error empty-state with a **Retry** action shows and Retry re-requests.
  - **Given** the user has no conversations, **when** ready, **then** the empty copy invites "Start your first run" and offers "New chat".

### Projects

- **US-4.2 — Solo user, project grid → detail.** _As a Solo user, I want to see my projects as cards and open one to its detail, so that I can navigate related chats and files under one project._
  - **Given** `/v1/projects` returns projects, **when** the grid renders, **then** each card shows icon + name + status pill + counts (`N chats · N files`) and the filter tabs (All / Active / Archived / Starred) reflect counts.
  - **Given** a card, **when** I open it, **then** the detail slot replaces the grid body showing the project's chats list and files list; **and** a chat row opens Run, a file row opens its artifact.
  - **Given** loading / error / empty / unavailable, **when** the destination renders, **then** the four states from `ProjectsDestination` render (skeleton cards / Retry / "No projects yet" + New project / "unavailable").
- **US-4.3 — Team admin, gated project ACL.** _As a Team admin on the `team` profile, I want member/role chips on project cards, so that I can see shared ownership._
  - **Given** `ENTERPRISE_DEPLOYMENT_PROFILE=team` and a project whose `viewer_role` is non-null, **when** the card renders, **then** a member/role chip strip (owner + member roles) shows on the card.
  - **Given** `single_user_desktop` (`viewer_role === null`), **when** the card renders, **then** no member/role chip is present — the chip strip is absent, not an empty rail.
  - **Given** the `team` profile but a project the viewer solely owns, **when** the card renders, **then** a single "Owner" chip shows and no empty member placeholder renders.

### Activity

- **US-4.4 — Solo user, watch what the agent did.** _As a Solo user, I want a single Activity feed of every run grouped by day, so that I can review, resume, or audit the agent's work in one place._
  - **Given** activity loads, **when** the destination renders, **then** rows group under `.act-day` day dividers (Today / Yesterday / <date>), each row = title, meta (connector/tool touched), mono time, and a status chip (running / done / paused / stopped).
  - **Given** a row whose run is **running**, **when** I click it, **then** the host navigates to `ArtifactRoute.run` (jump into the live run).
  - **Given** the Activity header, **when** it renders, **then** the lead copy reads "Everything the agent has done…" and a link "Retention, export, and delete live in Settings → Privacy" invokes the host's open-settings→privacy handler.
  - **Given** loading / error / empty, **when** the destination renders, **then** skeleton day-groups / Retry / "No activity yet" render.
- **US-4.5 — Solo user, one feed replaces three surfaces.** _As a Solo user, I no longer want separate Agents, Inbox, and audit-log tabs, so that there is one place to look._ (Old `agents`/`inbox` slugs redirect into Activity; approval-request items that used to live in Inbox surface as Activity rows with a `needs input` status.)

### Tools (= connectors)

- **US-4.6 — Solo user, govern per-tool access.** _As a Solo user, I want each connected tool to have a Read / Read & act / Off control, so that I decide what each app may do without opening Settings._
  - **Given** connected tools, **when** the Tools destination renders, **then** each tool row shows a 3-way segmented control (Read / Read & act / Off) reflecting its current access mode.
  - **Given** a tool, **when** I switch its segment, **then** the host persists the change (PATCH) and the segment reflects the new mode optimistically; on failure it reverts and shows an inline error.
  - **Given** the destination, **when** I click "Connect a tool", **then** the **ConnectModal** (§5: pick catalog → OAuth spinner → permission Read only / Read & act → Connect) opens.
  - **Given** the catalog, **when** it renders, **then** it is generic-SaaS-first (Notion/Linear/Slack/Google Calendar/Drive/GitHub/Stripe…); Safe/Dune are not defaults.
  - **Given** a connector in `error`/`expired` status, **when** the row renders, **then** a **Reconnect** action shows and starts re-OAuth.
  - **Given** loading / error / empty, **when** the destination renders, **then** skeleton cards / Retry / "Connect your first SaaS source" render.
  - Copy: "The approval _policy_ lives in Settings → Model & behavior" — a note links there.

### Skills (= skill catalog)

- **US-4.7 — Solo user, re-run saved workflows.** _As a Solo user, I want my saved skills as cards with Run / Edit, so that I can re-run a multi-step workflow in one click._
  - **Given** `/v1/skills` returns skills, **when** the destination renders, **then** each card shows name, sub, `N runs`, and Run / Edit actions.
  - **Given** a card, **when** I click **Run**, **then** the host starts a run of that skill and navigates to Run; **when** I click **Edit**, **then** the host opens the skill editor route.
  - **Given** the header, **when** I click **New skill**, **then** the host opens the new-skill editor.
  - **Given** loading / error / empty, **when** the destination renders, **then** skeleton cards / Retry / "No skills yet" + New skill render.

### Cross-cutting / DX

- **US-4.8 — Developer, one destination component, two consumers.** _As a maintainer, I want each destination to be pure presentation behind ports, so that `apps/frontend` and `apps/desktop` render the same component with no `apps/_`→`apps/_` import._
  - **Given** any Phase-4 destination component, **when** ESLint substrate rules run, **then** there is no bare `window`/`document`/`fetch`/`localStorage` — all IO flows through Transport/Router/KeyValueStore props or providers.
- **US-4.9 — Developer, folded slugs don't 404.** _As a maintainer, I want the removed slugs (home/library/inbox/todos/routines/agents) to redirect, so that stale deep-links resolve._
  - **Given** a route to a folded slug, **when** the host resolves it, **then** it redirects to the destination that absorbed it (agents/inbox → Activity; memory → Settings→Privacy; home/library/todos/routines → Run or Activity) rather than rendering a dead outlet.

---

## 4. Functional requirements

Grouped by area. Each maps to ≥1 story (§3) and ≥1 test (§8).

### Shared list surface (FR-4.1–4.4)

- **FR-4.1** Every Phase-4 list destination MUST render the shared `.pg` surface: content column `max-width: 960px`, `PageHeader` (title + subtitle), and a scrollable body. (US-4.1/4.2/4.4/4.6/4.7)
- **FR-4.2** Every Phase-4 list destination MUST implement the 4-state machine driven by a `SectionResult<T> | null` prop (or equivalent discriminated `FetchState`): `null` → loading skeleton (`data-state="loading"`); `status==="error"` → `EmptyState` with a Retry action; `status==="ok"` with zero rows → per-view empty copy; `status==="ok"` with rows → ready list. `status==="unavailable"` renders a distinct "not enabled for your workspace" empty-state. (US-4.1/4.2/4.4/4.6/4.7)
- **FR-4.3** Every destination MUST be pure presentation: no `fetch`, no `router.navigate` from list rows except via `ItemLink`/host callbacks, no direct SSE. Data + navigation flow through props/callbacks wired by the host. (US-4.8)
- **FR-4.4** Relative timestamps MUST be rendered from ISO strings via `packages/chat-surface/src/util/time.ts` `formatRelativeTime(iso, now)`, with `now` as a test seam — no pre-formatted time strings passed in. (US-4.1/4.4)

### Chats (FR-4.5–4.9)

- **FR-4.5** The Chats destination MUST render three sections in order: Pinned, Recent, Archived, each a `.rowlist` of thread rows; empty sections are not rendered. (US-4.1)
- **FR-4.6** Each chat row MUST show title, a `StatusPill` (running/done/paused/archived), a one-line truncated preview, a mono model tag, and mono relative time. (US-4.1)
- **FR-4.7** Clicking a chat row or pressing Enter MUST invoke `onReopen(conversationId)`; the host translates it to `ArtifactRoute.run`. The destination MUST NOT render an inline thread canvas. (US-4.1)
- **FR-4.8** "New chat" in the header MUST invoke `onNewChat()`; the host opens Run. (US-4.1)
- **FR-4.9** The Chats data source MUST be the conversation list (`/v1/agent/conversations`, including archived), not the legacy `/v1/chats/projects` stub currently read by `ChatsSidebar`. (US-4.1; see §11 gap)

### Projects (FR-4.10–4.13)

- **FR-4.10** The Projects destination MUST reuse `ProjectsDestination` (card grid + FilterTabs All/Active/Archived/Starred + 4 states). (US-4.2)
- **FR-4.11** Opening a project MUST render the detail via the existing `renderDetail`/`focusedProjectId` slot using `ProjectDetailView` (already tabbed: chats/members/activity + legacy todos/inbox/library/routines tabs). Phase 4 MUST surface a **files** list in the detail (new tab or section) alongside the existing chats list; when no files endpoint exists it MUST degrade to the empty/"coming soon" state (§11 files gap), not error. (US-4.2)
- **FR-4.12** A chat row in the project detail MUST open Run; a file row MUST open its artifact route via `ItemLink`. (US-4.2)
- **FR-4.13** Member/role chips (`viewer_role`) MUST render only when non-null (team profile); under `single_user_desktop` they MUST be absent. (US-4.3)

### Activity (FR-4.14–4.19)

- **FR-4.14** The Activity destination MUST render run rows grouped by day using `.act-day` dividers (Today / Yesterday / explicit date), most-recent day first. (US-4.4)
- **FR-4.15** Each activity row MUST show title, a meta line (tools/connectors touched), mono relative time, and a `StatusPill` for status ∈ {running, done, paused, stopped, needs input}. (US-4.4)
- **FR-4.16** Clicking a **running** run row MUST invoke `onOpenRun(runId)` (→ Run); non-running rows open a read-only run detail (via `ItemLink` `kind:"run"`). (US-4.4)
- **FR-4.17** The Activity header lead copy MUST read the DESIGN-SPEC §3 string ("Everything the agent has done… Retention, export, and delete live in Settings → Privacy.") and render a link that invokes `onOpenRetentionSettings()` (host → Settings → Privacy). (US-4.4)
- **FR-4.18** Activity MUST absorb the former Agents and Inbox surfaces: approval-request / needs-input items surface as Activity rows; there is no separate top-level Agents or Inbox destination. (US-4.5)
- **FR-4.19** The Activity list source MUST compose the available endpoints (`/v1/agent/conversations` + `/v1/audit`) in the host binder until a dedicated run-list endpoint exists; the destination component stays endpoint-agnostic (takes projected rows). (US-4.4; see §11 gap)

### Tools = connectors (FR-4.20–4.25)

- **FR-4.20** The Tools destination MUST reuse `ConnectorsDestination`, relabeled "Tools", with FilterTabs Connected / Available / Custom and the 4 states. (US-4.6)
- **FR-4.21** Each connected tool row MUST render a 3-way segmented control **Read / Read & act / Off** reflecting the tool's current access mode. (US-4.6)
- **FR-4.22** Changing the segment MUST invoke `onSetAccessMode(id, mode)`; the host persists it (PATCH) and the UI reflects the new mode optimistically, reverting + showing an inline error on failure. (US-4.6)
- **FR-4.23** "Connect a tool" MUST invoke `onConnect()` opening the ConnectModal (§5): catalog pick → OAuth spinner → permission (Read only / Read & act) → Connect. (US-4.6)
- **FR-4.24** The Available catalog MUST be generic-SaaS-first; the destination MUST NOT hardcode Safe/Dune as defaults (they may appear only as ordinary catalog entries). (US-4.6)
- **FR-4.25** A connector in `error`/`expired` status MUST render a Reconnect action wired to `onReconnect(id)`; the destination MUST render a note that the approval _policy_ lives in Settings → Model & behavior. (US-4.6)

### Skills = skill catalog (FR-4.26–4.29)

- **FR-4.26** The Skills destination MUST render a card grid of skills from `/v1/skills`, each card showing name, sub/description, and `N runs`, with the 4 states. (US-4.7)
- **FR-4.27** A skill card MUST expose **Run** (→ `onRunSkill(id)`, host starts a run + navigates to Run) and **Edit** (→ `onEditSkill(id)`, host opens editor). (US-4.7)
- **FR-4.28** The header MUST expose **New skill** (→ `onNewSkill()`). (US-4.7)
- **FR-4.29** Skills MUST be its own destination (not a Settings tab and not the tool-integration catalog); the DESIGN-SPEC copy MUST render as the subtitle. (US-4.7)

### IA folding & boundaries (FR-4.30–4.33)

- **FR-4.30** `SHELL_DESTINATIONS` in `packages/chat-surface/src/shell/destinations.ts` MUST expose exactly the six solo slugs (`run`, `chats`, `projects`, `activity`, `tools`, `skills`) under `single_user_desktop`, in DESIGN-SPEC §1 order; team profile adds team/members/billing. (Depends on Phase 2B — this phase consumes/finishes it.) (US-4.9)
- **FR-4.31** Routes to folded slugs (`home`/`library`/`inbox`/`todos`/`routines`/`agents`/`memory`) MUST redirect: `agents`,`inbox` → `activity`; `memory` → Settings → Privacy; `home`,`library`,`todos`,`routines` → `run` (or `activity`). No dead outlet. (US-4.9)
- **FR-4.32** No Phase-4 destination component may import from `apps/frontend/src` or `apps/desktop`; host binders live in the apps and pass props. (US-4.8)
- **FR-4.33** New wire contracts (chats archive row, activity run row, skill summary, connector access mode) MUST be added to `packages/api-types` and imported by the destinations — no `__brand:` re-declaration inside `chat-surface`. (US-4.8)

---

## 5. Architecture & system design

### Single source of truth

- **Destination components** live once in `packages/chat-surface/src/destinations/<name>` and are consumed by both apps. `apps/frontend/src/features/<name>/<Name>Route.tsx` and `apps/desktop/renderer` are **binders** (fetch + router + state) that render the shared component. This is the ADR (a) decision from PLAN §3.
- **IA / slug ↔ label** is owned by `packages/chat-surface/src/shell/destinations.ts` (`SHELL_DESTINATIONS`, `ShellDestinationSlug`). Phase 4 consumes the profile-gated six-slug set produced in Phase 2B; it does not re-fork the enum.
- **Route kinds** are owned by `packages/chat-surface/src/routing/router.ts` (`ArtifactRoute`). Reopen/open-run uses `{ kind: "run", runId }`; skill edit/open uses `{ kind: "skill", skillId }`; connector open uses `{ kind: "mcp", serverId }`. Host web-only screens (Settings) stay in the host route type, reached via callbacks (`onOpenSettings`, `onOpenRetentionSettings`) — not added to `ArtifactRoute`.
- **Wire contracts** are owned by `packages/api-types`. Existing: `projects.ts`, `connectors.ts`, `tools.ts`, `refs.ts` (`SectionResult`, `ItemRef`), `inbox.ts`. New in this phase: `chats.ts` (archive row), `activity.ts` (run row + day group), `skills.ts` (skill summary), and an `access_mode` field on the connector contract in `connectors.ts`.
- **Time formatting** is owned by `packages/chat-surface/src/util/time.ts`. **Status → tone** mapping is owned per-destination but rendered through the shared `StatusPill` (`packages/chat-surface/src/shell/StatusPill.tsx`).

### Boundaries & ports (respect `CLAUDE.md`)

- `chat-surface` stays framework-agnostic. Destinations either take all data/callbacks as props (pure presentation: `ProjectsDestination`, `ConnectorsDestination`, `ToolsDestination`) or read through the existing providers — **Transport** (`providers/TransportProvider` → `ports/Transport`), **Router** (`providers/RouterProvider` → `routing/router`), **KeyValueStore** (`ports/KeyValueStore`), **PresenceSignal** (`presence/PresenceSignal`). `ChatsSidebar` already uses `useTransport()` + `useRouter()`; the recast Chats archive follows the same port usage.
- No `apps/*` → `apps/*` imports (FR-4.32). Binders import the component from `@0x-copilot/chat-surface` and wire `@0x-copilot/api-types`.
- ESLint substrate rules (Phase 0E) enforce no bare `window`/`document`/`fetch`/`localStorage` in `chat-surface`.

### Data flow & key types/interfaces

- **Transport**: `packages/chat-surface/src/ports/Transport.ts` (re-exports `@0x-copilot/chat-transport` `Transport`, `TypedRequest`, `SseSubscription`). Binders call `transport.request<T>({ method, path })`.
- **Router**: `packages/chat-surface/src/routing/router.ts` `Router<ArtifactRoute>`; `navigate({kind:"run",runId})`.
- **SectionResult<T>**: `packages/api-types/src/refs.ts` — the uniform `{status:"ok"|"error"|"unavailable", data?, error?}` wrapper already used by `ProjectsDestination`, `ConnectorsDestination`, `InboxDestination`.
- **New types** (`packages/api-types`):
  - `chats.ts`: `ChatArchiveRow { id, title, status: "running"|"done"|"paused"|"archived", preview, model, updated_at, pinned }`, `ChatsArchive { pinned, recent, archived }` or a flat list the shell buckets.
  - `activity.ts`: `ActivityRunRow { runId, title, status: "running"|"done"|"paused"|"stopped"|"needs_input", meta, started_at }`, plus a `DayGroup` derivation done in-shell.
  - `skills.ts`: `SkillSummary { id, name, description, run_count, updated_at }`.
  - `connectors.ts` (extend): `access_mode: "read" | "read_act" | "off"` on `Connector`.

### Reuse vs new

| Concept                                                 | Disposition                                                          | Real path                                                                                                       |
| ------------------------------------------------------- | -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Projects grid + 4 states                                | **Reuse**                                                            | `packages/chat-surface/src/destinations/projects/ProjectsDestination.tsx`                                       |
| Project detail (already tabbed: chats/members/activity) | **Reuse + extend** (add files list/section)                          | `packages/chat-surface/src/destinations/projects/ProjectDetailView.tsx`                                         |
| Connectors → **Tools** grid + 4 states                  | **Reuse (relabel)**                                                  | `packages/chat-surface/src/destinations/connectors/ConnectorsDestination.tsx`                                   |
| Connector card / detail / scope tab                     | **Reuse**                                                            | `packages/chat-surface/src/destinations/connectors/{ConnectorCard,ConnectorDetailView,ScopeReviewTab}.tsx`      |
| Connect flow (ConnectModal)                             | **New** (per DESIGN-SPEC §5)                                         | `packages/chat-surface/src/destinations/connectors/ConnectModal.tsx`                                            |
| Per-tool Read/Read&act/Off segment                      | **New**                                                              | `packages/chat-surface/src/destinations/connectors/AccessModeSegment.tsx`                                       |
| Chats archive list (pinned/recent/archived)             | **Recast** of placeholder                                            | `packages/chat-surface/src/destinations/chats/ChatsDestination.tsx` (+ new `ChatsArchive.tsx`)                  |
| Chats sidebar (legacy `/v1/chats/projects`)             | **Retire from top-level** (kept for Run's own thread rail if needed) | `packages/chat-surface/src/destinations/chats/ChatsSidebar.tsx`                                                 |
| Activity destination (day-grouped runs)                 | **New**                                                              | `packages/chat-surface/src/destinations/activity/ActivityDestination.tsx`                                       |
| Activity row primitives                                 | **Reuse**                                                            | `packages/chat-surface/src/shell/{ActivityList,ActivityTabContent,DocList,StatusPill}.tsx`                      |
| Agents gallery                                          | **Fold** into Activity (superseded as top-level)                     | `packages/chat-surface/src/destinations/agents/AgentsDestination.tsx`                                           |
| Inbox destination                                       | **Fold** into Activity (needs-input rows)                            | `packages/chat-surface/src/destinations/inbox/InboxDestination.tsx`                                             |
| Skills catalog (name/sub/N runs, Run/Edit/New)          | **New**, reusing tools grid scaffolding                              | `packages/chat-surface/src/destinations/skills/SkillsDestination.tsx`                                           |
| Tool-integration catalog (MCP/OpenAPI/code)             | **Superseded** as top-level Skills (concept mismatch — see §11)      | `packages/chat-surface/src/destinations/tools/ToolsDestination.tsx`                                             |
| Shared list primitives                                  | **Reuse**                                                            | `packages/chat-surface/src/shell/{PageHeader,FilterTabs,CardGrid,EmptyState,StatusPill,DocList}.tsx`            |
| Time formatting                                         | **Reuse**                                                            | `packages/chat-surface/src/util/time.ts`                                                                        |
| Host binders (web)                                      | **Modify/New**                                                       | `apps/frontend/src/features/{chats,projects,activity,tools,skills}/*Route.tsx`, `apps/frontend/src/app/App.tsx` |
| Host binder (desktop)                                   | **Modify**                                                           | `apps/desktop/renderer/bootstrap.tsx`                                                                           |

---

## 6. Affected files / component inventory

### Create

- `packages/api-types/src/chats.ts` — archive row + archive contract.
- `packages/api-types/src/activity.ts` — run row + status enum.
- `packages/api-types/src/skills.ts` — skill summary.
- `packages/chat-surface/src/destinations/chats/ChatsArchive.tsx` — pinned/recent/archived list + tests.
- `packages/chat-surface/src/destinations/activity/ActivityDestination.tsx` (+ `index.ts`, `_activity-stub.ts` until api-types merge) + tests.
- `packages/chat-surface/src/destinations/skills/SkillsDestination.tsx` (+ `SkillCard.tsx`, `index.ts`) + tests.
- `packages/chat-surface/src/destinations/connectors/ConnectModal.tsx` + tests.
- `packages/chat-surface/src/destinations/connectors/AccessModeSegment.tsx` + tests.
- `apps/frontend/src/features/chats/ChatsArchiveRoute.tsx`, `apps/frontend/src/features/activity/ActivityRoute.tsx`, `apps/frontend/src/features/skills/SkillsRoute.tsx` (+ `api/*Api.ts`) + tests.

### Modify

- `packages/api-types/src/connectors.ts` — add `access_mode` field; `packages/api-types/src/index.ts` — export new modules.
- `packages/chat-surface/src/destinations/chats/ChatsDestination.tsx` — recast placeholder → archive host (renders `ChatsArchive`, drops inline thread-canvas placeholder).
- `packages/chat-surface/src/destinations/connectors/ConnectorsDestination.tsx` — relabel "Tools", add `onSetAccessMode`, render `AccessModeSegment` on connected rows, wire `ConnectModal` trigger, generic-SaaS copy.
- `packages/chat-surface/src/destinations/connectors/ConnectorCard.tsx` — host the access-mode segment.
- `packages/chat-surface/src/destinations/projects/ProjectsDestination.tsx` / `ProjectDetailView.tsx` — ensure files list renders in detail (or flag gap).
- `packages/chat-surface/src/index.ts` — export `ActivityDestination`, `SkillsDestination`, `ChatsArchive`, `ConnectModal`, `AccessModeSegment`, new types.
- `packages/chat-surface/src/shell/destinations.ts` — six-slug profile-gated set (finish Phase 2B if not already the six).
- `apps/frontend/src/app/App.tsx`, `apps/frontend/src/app/routes.ts` — destination dispatch for the six slugs + folded-slug redirects; relabel connectors→Tools, tools→Skills.
- `apps/frontend/src/features/projects/ProjectsRoute.tsx` — wire detail/files.
- `apps/frontend/src/features/connectors/ConnectorsRoute.tsx` → Tools binder: access-mode PATCH + connect flow.
- `apps/desktop/renderer/bootstrap.tsx` — mount the six destinations via the shell outlet.

### Delete / supersede

- Top-level mounting of `AgentsDestination`, `InboxDestination`, `HomeDestination`, `LibraryDestination`, `TodosDestination`, `RoutinesDestination`, `MemoryDestination` (components remain in-tree but are no longer rail destinations; their web feature routes redirect). Actual file deletion is deferred to Phase 6C (dead-code sweep) to keep each Phase-4 PR small and green — Phase 4 only removes them from the IA and dispatch.
- `apps/desktop/renderer/DesktopPlaceholder.tsx` mount for these slugs (Phase 2E already removed the generic placeholder; Phase 4 ensures no folded slug re-introduces it).

---

## 7. PR / commit breakdown

Ordered; each ≤ ~1000 LOC, independently mergeable, leaves `main` + web green. Sizes: S ≤200, M ≤500, L ≤1000 LOC.

- **PR-4.1 — api-types: chats/activity/skills contracts + connector access_mode.** Add `chats.ts`, `activity.ts`, `skills.ts`; extend `connectors.ts` with `access_mode`; export from `index.ts`; unit tests for the brand/shape. _Files:_ `packages/api-types/src/{chats,activity,skills,connectors,index}.ts` + `*.test.ts`. _Deps:_ none. _Accept:_ `npm run typecheck --workspace @0x-copilot/api-types` green; new types exported; no consumer breakage (additive). _Size:_ S.

- **PR-4.2 — Chats archive component.** New `ChatsArchive.tsx` (pinned/recent/archived buckets, row shape FR-4.6, 4 states, `onReopen`/`onNewChat` callbacks). Recast `ChatsDestination.tsx` to render it, dropping the thread-canvas placeholder. _Files:_ `packages/chat-surface/src/destinations/chats/{ChatsArchive,ChatsDestination}.tsx` + tests; `index.ts`. _Deps:_ PR-4.1. _Accept:_ vitest covers 4 states + reopen callback; `data-state` attrs present. _Size:_ M.

- **PR-4.3 — Chats host binding (web + desktop).** `ChatsArchiveRoute.tsx` fetches `/v1/agent/conversations` (incl. archived), buckets, wires `onReopen → navigate(run)`, `onNewChat → new run`. Wire into `App.tsx` dispatch + desktop bootstrap. Web keeps behaving (old chats route replaced by archive; regression checks §8). _Files:_ `apps/frontend/src/features/chats/ChatsArchiveRoute.tsx` + `api/chatsApi.ts` + tests; `apps/frontend/src/app/App.tsx`; `apps/desktop/renderer/bootstrap.tsx`. _Deps:_ PR-4.2, Phase 3 (`ArtifactRoute.run`). _Accept:_ clicking a row navigates to run; loading/error/empty exercised with a mocked transport. _Size:_ M.

- **PR-4.4 — Projects detail + files wiring.** Mount `renderDetail`/`focusedProjectId` in the binder; render chats + files lists in `ProjectDetailView`; chat row → run, file row → artifact. Flag/scaffold the files source (§11). _Files:_ `packages/chat-surface/src/destinations/projects/ProjectDetailView.tsx`; `apps/frontend/src/features/projects/ProjectsRoute.tsx` + tests. _Deps:_ PR-4.1. _Accept:_ opening a card shows detail; chat row navigate asserted; files list renders (or documented empty when endpoint absent). _Size:_ M.

- **PR-4.5 — Activity component.** New `ActivityDestination.tsx`: day-grouped run rows (`.act-day`), status chips, header lead copy + retention link callback, 4 states, `onOpenRun`/`onOpenRetentionSettings`. Reuse `DocList`/`StatusPill`/`ActivityList`. _Files:_ `packages/chat-surface/src/destinations/activity/ActivityDestination.tsx`, `.../activity/index.ts`, `.../activity/_activity-stub.ts` (matching the repo `_<dest>-stub.ts` convention), `.../activity/ActivityDestination.test.tsx`; `packages/chat-surface/src/index.ts` export. _Deps:_ PR-4.1. _Accept:_ vitest for grouping-by-day, status-tone map, running-row→onOpenRun, retention link, 4 states. _Size:_ M.

- **PR-4.6 — Activity host binding (compose conversations + audit).** `ActivityRoute.tsx` composes `/v1/agent/conversations` + `/v1/audit` into day-grouped `ActivityRunRow`s; wires open-run and open-settings→privacy; redirect old `agents`/`inbox` slugs → `activity`. _Files:_ `apps/frontend/src/features/activity/ActivityRoute.tsx` + `api/activityApi.ts` + tests; `apps/frontend/src/app/{App.tsx,routes.ts}`; desktop bootstrap. _Deps:_ PR-4.5. _Accept:_ composed rows render grouped; `agents`/`inbox` deep-link resolves to Activity; mocked transport covers error/empty. _Size:_ M.

- **PR-4.7 — Tools access-mode segment + relabel.** `AccessModeSegment.tsx` (Read/Read&act/Off, a11y radiogroup), integrate into `ConnectorCard`/`ConnectorsDestination`; relabel destination "Tools", generic-SaaS copy + policy note; `onSetAccessMode`. _Files:_ `packages/chat-surface/src/destinations/connectors/{AccessModeSegment,ConnectorCard,ConnectorsDestination}.tsx` + tests. _Deps:_ PR-4.1. _Accept:_ segment reflects mode, fires callback; relabel asserted; policy note links via callback. _Size:_ M.

- **PR-4.8 — Tools connect flow + host binding.** `ConnectModal.tsx` (catalog → OAuth spinner → permission → Connect, StepDots per §5); Tools binder wires access-mode PATCH (optimistic + revert) and connect flow into the existing `apps/frontend/src/features/connectors/ConnectorsRoute.tsx`. _Files:_ `packages/chat-surface/src/destinations/connectors/ConnectModal.tsx` + `ConnectModal.test.tsx`; `apps/frontend/src/features/connectors/ConnectorsRoute.tsx` + `apps/frontend/src/features/connectors/__tests__/ConnectorsRoute.test.tsx`; `apps/desktop/renderer/bootstrap.tsx`. _Deps:_ PR-4.7. _Accept:_ modal steps advance; mode PATCH optimism + revert-on-error asserted; connect callback opens OAuth. _Size:_ M. **Split trigger:** if `ConnectModal.tsx` + tests exceed ~400 LOC, land the modal component alone here and move the binder (PATCH optimism + connect wiring + desktop bootstrap) to a follow-up **PR-4.8b** to keep both ≤ M.

- **PR-4.9 — Skills component.** New `SkillsDestination.tsx` + `SkillCard.tsx` (name/sub/N runs, Run/Edit, New skill header, 4 states) reusing `CardGrid`/`PageHeader`/`EmptyState`. _Files:_ `packages/chat-surface/src/destinations/skills/SkillsDestination.tsx`, `.../skills/SkillCard.tsx`, `.../skills/index.ts`, `.../skills/SkillsDestination.test.tsx`; `packages/chat-surface/src/index.ts` export. _Deps:_ PR-4.1. _Accept:_ Run/Edit/New callbacks fire; 4 states covered; subtitle copy asserted. _Size:_ M.

- **PR-4.10 — Skills host binding.** `SkillsRoute.tsx` fetches `/v1/skills` (reusing the existing `apps/frontend/src/features/skills/useSkills.ts` hook where it already wraps the endpoint), wires Run (start run + navigate), Edit/New (open editor route). _Files:_ `apps/frontend/src/features/skills/SkillsRoute.tsx`, `apps/frontend/src/features/skills/SkillsRoute.test.tsx` (reuse/extend `useSkills.ts`); `apps/frontend/src/app/App.tsx`; `apps/desktop/renderer/bootstrap.tsx`. _Deps:_ PR-4.9. _Accept:_ Run navigates to run; error/empty exercised. _Size:_ M.

- **PR-4.11 — IA fold + redirects + dispatch cleanup.** Finalize six-slug dispatch in `App.tsx`; redirect folded slugs (FR-4.31); ensure `destinations.ts` matches the six under `single_user_desktop`; relabel Tools/Skills in rail labels. _Files:_ `apps/frontend/src/app/{App.tsx,routes.ts}`; `packages/chat-surface/src/shell/destinations.ts` (if not final from Phase 2B); tests. _Deps:_ PR-4.3/4.6/4.8/4.10. _Accept:_ rail shows six; folded deep-links redirect; web nav smoke green. _Size:_ S–M.

---

## 8. Testing plan

Runner: **vitest** for TS via `npm run test --workspace @0x-copilot/chat-surface` / `--workspace @0x-copilot/frontend` / `--workspace @0x-copilot/api-types`. **pytest** in the owning service `.venv` for any facade change. Live desktop smoke per `apps/desktop/SMOKE.md`.

### Unit (chat-surface / api-types)

- `packages/api-types/src/chats.test.ts` — `ChatArchiveRow` status union exhaustive; `ChatsArchive` shape. (FR-4.5/4.6/4.33)
- `packages/api-types/src/activity.test.ts` — `ActivityRunRow` status union incl. `needs_input`. (FR-4.15/4.33)
- `packages/api-types/src/skills.test.ts` — `SkillSummary` fields. (FR-4.26/4.33)
- `packages/api-types/src/connectors.test.ts` — `access_mode` union `read|read_act|off`. (FR-4.21/4.33)
- `packages/chat-surface/src/destinations/chats/ChatsArchive.test.tsx` — renders Pinned/Recent/Archived, hides empty sections (FR-4.5); row shows title/status/preview/model/time (FR-4.6); Enter + click fire `onReopen` (FR-4.7); "New chat" fires `onNewChat` (FR-4.8); loading/error+Retry/empty states (FR-4.2).
- `packages/chat-surface/src/destinations/activity/ActivityDestination.test.tsx` — groups rows into Today/Yesterday/date via injected `now` (FR-4.14); status→tone map incl. running/stopped/needs_input (FR-4.15); running row click → `onOpenRun` (FR-4.16); retention link → `onOpenRetentionSettings` (FR-4.17); 4 states (FR-4.2).
- `packages/chat-surface/src/destinations/skills/SkillsDestination.test.tsx` — card shows name/sub/`N runs` (FR-4.26); Run/Edit/New callbacks (FR-4.27/4.28); subtitle copy (FR-4.29); 4 states.
- `packages/chat-surface/src/destinations/connectors/AccessModeSegment.test.tsx` — radiogroup semantics; selecting a segment fires `onChange(mode)`; keyboard arrow nav; reflects current mode (FR-4.21/4.22).
- `packages/chat-surface/src/destinations/connectors/ConnectorsDestination.test.tsx` (extend) — connected row renders segment; "Connect a tool" fires `onConnect`; catalog has no Safe/Dune default; policy note link fires callback; Reconnect on error/expired (FR-4.20/4.23/4.24/4.25).
- `packages/chat-surface/src/destinations/connectors/ConnectModal.test.tsx` — StepDots advance catalog→OAuth→permission→Connect; permission choice Read only/Read & act; close/cancel (FR-4.23).
- `packages/chat-surface/src/destinations/projects/ProjectDetailView.test.tsx` (extend) — detail shows chats + files lists; chat row → run; files empty-state when absent (FR-4.11/4.12).
- `packages/chat-surface/src/shell/destinations.test.ts` (new) — under `single_user_desktop`, `SHELL_DESTINATIONS` resolves to exactly `[run, chats, projects, activity, tools, skills]` in DESIGN-SPEC §1 order; folded slugs (`home`/`library`/`inbox`/`todos`/`routines`/`agents`/`memory`) are absent; `team` profile appends team/members/billing (FR-4.30).

### Integration (host binders, mocked transport)

- `apps/frontend/src/features/chats/ChatsArchiveRoute.test.tsx` — fetch `/v1/agent/conversations` (incl. archived), bucket, reopen→`navigate({kind:"run"})`; error/empty via mocked transport (FR-4.9).
- `apps/frontend/src/features/activity/ActivityRoute.test.tsx` — composes conversations + audit into grouped rows; `agents`/`inbox` slug redirect resolves to Activity (FR-4.18/4.19/4.31).
- `apps/frontend/src/features/connectors/__tests__/ConnectorsRoute.test.tsx` (extend — the Tools binder keeps its existing `features/connectors/` home; the "Tools" label is UI-only) — access-mode PATCH optimistic update + revert on 500; connect flow opens OAuth (FR-4.22/4.23).
- `apps/frontend/src/features/skills/SkillsRoute.test.tsx` (new; may reuse `useSkills.ts` fakes) — fetch `/v1/skills`; Run starts run + navigates; error/empty (FR-4.27).
- `apps/frontend/src/app/App.test.tsx` (new — no App-level test exists today; routing is covered by `app/HashRouter.test.ts`/`app/keymap.test.ts`) — six-slug rail; folded-slug redirects; Tools/Skills labels (FR-4.30/4.31).

### E2E / live desktop smoke (`apps/desktop/SMOKE.md`)

Boot the supervised desktop (`COPILOT_RUNTIME_DIR=… npm run dev --workspace @0x-copilot/desktop`), sign in, then:

1. Rail shows six destinations; click each — no dead outlet, no `DesktopPlaceholder` (FR-4.30/4.31).
2. Chats: open a thread → lands in Run (FR-4.7); New chat → Run.
3. Projects: open a project → detail with chats/files (FR-4.11).
4. Activity: rows grouped by day; a live run row → Run; retention link opens Settings→Privacy (FR-4.16/4.17).
5. Tools: flip a tool Read→Read&act→Off (persists across reload); Connect a tool opens OAuth (FR-4.22/4.23).
6. Skills: Run a skill → Run; New skill opens editor (FR-4.27/4.28).
   > Live smoke is mandatory: unit fakes have hidden real-run breakage before (MEMORY: Virtuals launch). Exercise the real transport, not just mocks.

### Regression guard (web app behaviorally identical where not intentionally changed)

- `npm run typecheck --workspace @0x-copilot/frontend` and full `apps/frontend` vitest suite green after every PR.
- Chats/Activity/Tools/Skills relabel + fold are intentional web changes; assert the _new_ behavior and that no unrelated feature route (settings, share, provider-keys) regressed by running the existing App-level tests unchanged.
- `packages/chat-surface` full suite green (`npm run test --workspace @0x-copilot/chat-surface`) — existing `ConnectorsDestination`/`ProjectsDestination`/`InboxDestination` tests must still pass (additive props).

### FR → test map

FR-4.1..4.4 → shared-surface assertions in each `*.test.tsx`; FR-4.5..4.9 → `ChatsArchive.test.tsx` + `ChatsArchiveRoute.test.tsx`; FR-4.10..4.13 → `ProjectDetailView.test.tsx` + `ProjectsRoute.test.tsx`; FR-4.14..4.19 → `ActivityDestination.test.tsx` + `ActivityRoute.test.tsx`; FR-4.20..4.25 → `ConnectorsDestination.test.tsx` + `AccessModeSegment.test.tsx` + `ConnectModal.test.tsx` + `features/connectors/__tests__/ConnectorsRoute.test.tsx`; FR-4.26..4.29 → `SkillsDestination.test.tsx` + `SkillsRoute.test.tsx`; FR-4.30/4.31 → `app/App.test.tsx` + `shell/destinations.test.ts`; FR-4.32 → the Phase 0E ESLint boundary rule (`no-restricted-imports` blocking `apps/*` + bare `window`/`document`/`fetch`/`localStorage` in `packages/chat-surface`) run in CI, asserted green; FR-4.33 → `packages/api-types/src/{chats,activity,skills,connectors}.test.ts`.

---

## 9. UI/UX acceptance checklist

Grounded in DESIGN-SPEC §0 (tokens/dims) and §3 (list destinations). Token names are the design-system semantic vars (Phase 0B folds sky `#5fb2ec` into `--color-accent`; jade `#57c785`, ember `#f0764f`, amber `#e8b45e` are semantic-only).

**Shared list surface (all five)**

- [ ] `.pg` content column `max-width: 960px`, centered; content lines wrap ≤ 620px where prose; base font 13px, line-height 1.5.
- [ ] `PageHeader` title in `--font-display` weight 600, letter-spacing −.01em; subtitle muted 11.5–13px (`--color-text-muted`).
- [ ] `.sect-h` section headers mono, uppercase; `.act-day` day dividers (Activity).
- [ ] `.rowlist`/`.lrow`: neutralized logo 30px or icon 28px, name 12.5px, mono sub, mono time. Connector/lane colors neutralized to `--panel3`/`--color-text-muted` (no brand color).
- [ ] Radii 8 / 12 / 6 (`--radius-*`); card grids use `.grid2`/`.grid3` (auto-fill minmax).
- [ ] **Single-accent discipline:** only `--color-accent` (sky) for interactive accent; status uses jade/ember/amber semantically; no stray decorative color.

**States (default / hover / active / focus-visible / loading / empty / error)**

- [ ] Default rows neutral; hover = subtle `--color-bg-elevated` tint; active/selected = accent-soft tint + 2px accent left border (matches `ChatsSidebar` active thread).
- [ ] Focus-visible: `2px solid var(--color-accent)` ring, offset 2, on every row/button/segment/tab.
- [ ] Loading: skeleton cards/rows with `aria-hidden`, `data-state="loading"` — not a bare spinner.
- [ ] Empty: per-view copy (Chats "Start your first run"; Projects "No projects yet"; Activity "No activity yet…"; Tools "Connect your first SaaS source"; Skills "No skills yet") + primary CTA.
- [ ] Error: `EmptyState` with **Retry** action; `role="alert"` on the error node.
- [ ] Streaming/live: Activity running rows + Chats running status use jade `--color-success` chip; no accent misuse.

**Per-destination**

- [ ] Chats: Pinned/Recent/Archived sections; status chip running(jade)/done(muted)/paused(amber)/archived(muted); mono model + mono time; row click → Run.
- [ ] Projects: card icon + name + status pill + `N chats · N files`; FilterTabs All/Active/Archived/Starred with counts; detail shows chats + files.
- [ ] Activity: `.act-day` dividers; status running/done/paused/stopped/needs input; retention link styled as inline link to Settings→Privacy.
- [ ] Tools: segmented Read / Read & act / Off (3-way, equal segments, selected = accent-soft + accent text); Connect CTA; error/expired → Reconnect; lead copy per DESIGN-SPEC §3 ("The apps the agent can read from and act through — a destination, not a settings tab. The approval _policy_ lives in Settings → Model & behavior."); Skills subtitle per §3 ("Saved multi-step workflows you can re-run in one click — their own place, not a settings tab.").
- [ ] Skills: card name + sub + `N runs`; Run (primary) / Edit (ghost); New skill header CTA.

**a11y**

- [ ] Roles: `role="tablist"/"tab"/"tabpanel"` on FilterTabs; `role="radiogroup"/"radio"` (or segmented equivalent with `aria-pressed`) on AccessModeSegment; `role="alert"` on errors; `role="status"` on loading; lists as `<ul>/<li>` or `role="list"/"listitem"`.
- [ ] Keyboard: rows Enter/Space activate; segmented control arrow-key nav; modal focus-trapped, Esc closes, StepDots reachable; tab order logical.
- [ ] Focus management: opening Projects detail moves focus to the detail heading; closing returns focus to the originating card; ConnectModal returns focus to the Connect button on close.
- [ ] `prefers-reduced-motion` / `[data-reduce-motion=1]`: caret/segment transitions zeroed.
- [ ] Contrast: text on surfaces ≥ WCAG AA in both themes; status chips legible.

**Theming / density**

- [ ] Light + dark both correct (design-system `[data-theme]`); neutrals per §0 (dark `#09090b`/`#111114`; light `#f4f4f6`/`#ffffff`).
- [ ] `[data-density=compact|spacious]` spacing respected (10/7 and 18/14) — no hardcoded pad that ignores density.
- [ ] Accent swatch options sky/jade/ember/violet via `[data-accent]` flow through.

**Component reuse noted**

- [ ] Reuses `PageHeader`, `FilterTabs`, `CardGrid`, `EmptyState`, `StatusPill`, `DocList`, `ItemLink`, `util/time` — no bespoke buttons/px outside tokens.

---

## 10. Dependencies & sequencing

**Upstream (blocked by):**

- **Phase 0** — design-system v2 tokens/fonts wired on desktop (0A/0B/0C), `DeploymentProfile` port (0D), `chat-surface` module homes + ESLint boundary guard (0E).
- **Phase 2** — profile-gated `destinations.ts` six-slug set (2B), Settings entry + `onOpenSettings` (2C), destination outlet mounted, placeholder removed (2E). Phase 4 _consumes/finalizes_ the six-slug IA.
- **Phase 3** — `ArtifactRoute.run` + Run destination (reopen/open-run targets). PR-4.3/4.6 depend on it. Projects/Tools/Skills components (PR-4.4/4.5/4.7/4.9) do **not** depend on Phase 3 and can land first.

**Internal DAG:** PR-4.1 → {PR-4.2→PR-4.3, PR-4.4, PR-4.5→PR-4.6, PR-4.7→PR-4.8, PR-4.9→PR-4.10} → PR-4.11 (needs all bindings). PR-4.3/4.6 additionally wait on Phase 3.

**Downstream (blocks):** Phase 5 (Settings) is reached via Phase-4 links (retention, policy) but is independent; Phase 6 command palette "Go to <dest>" + live smoke depend on Phase 4 destinations existing.

Audit after this + the next phase per PLAN §9 (every two merged phases).

---

## 11. Risks & mitigations

| Risk                                                                                                                                                                                                                                                                                                          | Sev  | Mitigation / rollback                                                                                                                                                                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **No run-list endpoint** — Activity has no dedicated `/v1/agent/runs` list; only per-id GET, `/v1/audit`, `/v1/agent/conversations`.                                                                                                                                                                          | High | PR-4.6 composes conversations + audit in the _binder_; `ActivityDestination` takes projected rows so a future `/v1/activity` endpoint drops in without a UI change. **Flag to backend workstream** to add `GET /v1/activity` (paginated run history). Rollback: Activity renders conversations-only until audit compose lands.                                         |
| **`/v1/chats/projects` is a stub** — `ChatsSidebar` reads a route with no facade handler; real list is `/v1/agent/conversations`.                                                                                                                                                                             | Med  | FR-4.9: archive binds to `/v1/agent/conversations` (incl. archived). Retire the stub read; keep `ChatsSidebar` only if Run needs its own thread rail.                                                                                                                                                                                                                  |
| **Projects `files` list has no endpoint** — `/v1/projects/{id}` returns counts but there is no `/v1/projects/{id}/files`.                                                                                                                                                                                     | Med  | Render files section with an empty/"coming soon" state gated on a capability flag; **flag backend** to add a files list. Chats list uses existing project→conversation linkage.                                                                                                                                                                                        |
| **Connector "access mode" is a new concept** — connectors expose OAuth _scopes_ (`ScopeReviewTab`, `PATCH /v1/connectors/{id}/scopes`) and there is a separate approval policy (`/v1/me/policies/tool-use`), but no 3-way per-connector Read/Read&act/Off field.                                              | High | Add `access_mode` to the connector contract (PR-4.1) and a facade PATCH; **decision needed**: is Read/Read&act/Off a projection of the tool-use policy, of granted scopes, or a new per-connector field? Recommend a new `access_mode` field persisted per connector, with the global policy remaining in Settings. Flag for backend + product sign-off before PR-4.8. |
| **Skills concept mismatch** — DESIGN-SPEC "Skills" = saved multi-step workflows (name, sub, N runs, Run/Edit/New) backed by `/v1/skills`, but PLAN §5 maps "Skills ← current `tools` destination", and `tools/ToolsDestination.tsx` is a _tool-integration_ catalog (MCP/OpenAPI/code) — a different concept. | High | Build **new** `SkillsDestination` backed by `/v1/skills` (matches the design copy and endpoint), reusing the tools grid _scaffolding_ only. Treat `tools/ToolsDestination` as superseded for the top-level Skills slug; fold the tool-integration catalog under Settings/Advanced or defer. **Flag the PLAN §5 wording as inaccurate.**                                |
| Relabel connectors→Tools / tools→Skills confuses code readers (file dir names lag labels).                                                                                                                                                                                                                    | Low  | Keep dir names (`connectors/`, `tools/`) but relabel UI + add a header comment noting label↔dir; a full rename is a Phase 6 cleanup to avoid churn now.                                                                                                                                                                                                                |
| Hoisting/recasting regresses web app.                                                                                                                                                                                                                                                                         | Med  | Additive props, one destination per PR, keep existing tests green, feature-route redirects behind the six-slug dispatch; per-PR web typecheck + vitest gate.                                                                                                                                                                                                           |
| Folded slugs leave dead deep-links.                                                                                                                                                                                                                                                                           | Low  | FR-4.31 redirects; App-level test asserts each folded slug resolves.                                                                                                                                                                                                                                                                                                   |
| `chat-surface` framework-agnostic invariant broken by a stray `fetch`/`window`.                                                                                                                                                                                                                               | Med  | ESLint substrate rules (Phase 0E) run in CI; destinations stay pure-presentation (props/ports).                                                                                                                                                                                                                                                                        |

---

## 12. Definition of done

- [ ] All FR-4.1–4.33 implemented and each mapped test green.
- [ ] Six destinations navigable in the desktop shell (`run`, `chats`, `projects`, `activity`, `tools`, `skills`); no `DesktopPlaceholder`, no dead outlet; folded slugs redirect.
- [ ] Chats reopen → Run; Activity running row → Run; Skills Run → Run; Tools access-mode persists; Connect flow opens OAuth (live smoke passed per `apps/desktop/SMOKE.md`).
- [ ] Each list destination has the 4-state machine (loading/error+Retry/empty/ready) verified in vitest.
- [ ] UI/UX checklist (§9) passed in light + dark, comfortable/compact/spacious, single-accent discipline held.
- [ ] `apps/frontend` typecheck + full vitest suite green; `packages/chat-surface` + `packages/api-types` suites green; ESLint substrate boundary clean.
- [ ] No `apps/*`→`apps/*` import; new wire types live in `packages/api-types`; destinations pure-presentation.
- [ ] Backend gaps (run-list, project files, connector access_mode) filed to the backend workstream with the recommended shape; binders compose existing endpoints in the interim and degrade gracefully.
- [ ] READMEs / this PRD updated to reflect the shipped Skills-vs-tools decision; no dead code introduced (final component deletions scheduled for Phase 6C).
