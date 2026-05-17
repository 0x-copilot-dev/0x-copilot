# Atlas Workspace Destinations — Master PRD

**Status:** draft (2026-05-17)
**Owner:** parth (orchestrator) — sub-PRDs delegated to per-destination agents
**Companion doc:** [PRD.md](PRD.md) — the workspace shell + composer + thread canvas PRD. This document picks up where that one stops: the 9 non-chats destinations.
**Design source of truth:** Claude Design handoff bundle at `/tmp/atlas-design/enterprise-search-template/` — `project/dest-*.jsx` files are the destination-specific designs. Master companion: `chats/chat1.md`.

---

## 1. Purpose

The Atlas Workspace has 11 destinations. The chats destination ships the thread canvas + composer + ChatScreen (covered in [PRD.md](PRD.md)). The remaining 9 — **Home / Inbox / Todos / Projects / Library / Agents / Tools / Connectors / Team / Memory** — are stubs today (or, in the case of Connectors, partially exist via the OAuth flow). This PRD turns them into a production-grade workspace.

It is the **orchestrator-level brief**. Each destination gets a **sub-PRD** written by the implementing subagent (see §11). The sub-PRD is the destination-specific contract; this master PRD is the cross-destination invariants that bind them together.

This is not an MVP. It is enterprise-grade software: multi-tenant, audited, paginated, accessible, performant, fully tested. Every destination satisfies the checklist in §3.

---

## 2. Architectural premise

### 2.1 Substrate-agnostic by design

Atlas ships on three substrates: web (Vite + React), desktop (Electron, planned), and eventually native mobile. The workspace UI is **substrate-agnostic** — every destination renders identically across substrates because the destination component lives in a shared package.

What is shared:

- `packages/chat-surface/` — every destination component, the workspace shell, the composer, the thread canvas. **Imported verbatim by both `apps/frontend` and `apps/desktop`.**
- `packages/api-types/` — every wire contract. Backend changes propagate through TypeScript types to all substrates simultaneously.
- `packages/design-system/` — every token (color/density/theme/typography), every primitive (Button, Card, Badge…). Both substrates consume `data-theme` / `data-density` / `data-accent` on `:root`.
- `packages/service-contracts/` — Python constants shared across services (used by ai-backend + backend + facade).

What is substrate-specific:

- **Routing**: web uses `HashRouter` (`apps/frontend/src/app/HashRouter.ts`); desktop uses native deep-link handlers wrapped in the same `Router<TRoute>` port. The shell sees `Router<TRoute>` — substrate-specific implementations behind a uniform interface.
- **Authentication storage**: web uses cookies + localStorage; desktop uses safeStorage (already in place for D24 per-server secrets).
- **Top-level menu chrome**: web shows browser chrome; desktop shows native window chrome + native menu bar. Neither is rendered by destinations.

Hard rule: **a destination component never knows which substrate it lives in.** If a destination needs a substrate-specific feature (file-system access, OS clipboard, native notifications), it gets it through a port in `packages/chat-surface/src/ports/` whose implementation is injected by the host.

### 2.2 Single source of truth, per destination

For each destination, **exactly one** of each of these exists:

- One `{Slug}Destination.tsx` (main view)
- One `{Slug}Panel.tsx` (context panel; exported from the same folder, supplied to `<ChatShell contextPanel={...}>` by the host)
- One wire-type group in `packages/api-types/src/{slug}.ts`
- One backend route module: `services/backend/src/backend_app/{slug}/`
- One facade proxy route in `services/backend-facade/src/backend_facade/{slug}_routes.py`
- One sub-PRD in `docs/atlas-new-design/destinations/{slug}-prd.md`

A second copy of any of these is a bug.

### 2.3 Backend ownership

The Atlas product is split:

- `services/backend` — **product persistence** (tenants, IdP integration, permissions, product state, audit, jobs, MCP registration, OAuth, user skills). The 9 destinations' state-of-the-world lives here.
- `services/ai-backend` — **agent runtime** (conversations, runs, events, approvals, model invocation). Most destinations DON'T live here; the ones that do (chats, agents, tools-as-skills) interact via HTTP.
- `services/backend-facade` — **product-facing API**. Apps talk only to the facade. The facade fans out to backend and ai-backend.

When a destination needs both product state and runtime state, it asks the facade and the facade composes. The destination never sees that fan-out.

---

## 3. Cross-cutting enterprise requirements (every destination satisfies these)

Every destination's sub-PRD must explicitly answer each item below. Subagents that ship a destination without satisfying these are rejected at review.

### 3.1 Multi-tenancy

- Every backend store has a `tenant_id` column. Every query filters by `tenant_id` derived from the verified session — never from request body.
- Tests for: cross-tenant read leak, cross-tenant write attempt (401/403), cross-tenant share/copy.
- `tenant_id` is derived server-side from the verified bearer's claims — apps never send it; the facade rejects requests that do.

### 3.2 Audit

- Every state-changing operation (create / update / delete / share / approve) writes an audit row via `packages/audit-chain` (already exists in repo).
- Audit row: `(tenant_id, actor_user_id, action, target_kind, target_id, before_state, after_state, ts, request_id)`.
- Audit is **append-only**; no mutation, no skip.
- Exportable to customer SIEM (rule from root CLAUDE.md compliance section).
- Tests for: each mutation emits the expected audit row; the row is reachable via the export endpoint.

### 3.3 Retention + deletion

- Soft delete with a tombstone row; hard delete after `RETENTION_DAYS` (default 90, tenant-configurable).
- Hard delete cascades to: child rows, audit references (anonymized, not removed — audit is append-only), and search indices.
- "Right to be forgotten" / GDPR delete is a separate forced-hard-delete path (admin-only, single endpoint).
- Tests: soft delete is reversible during retention; hard delete leaves audit anonymized; cascade is total.

### 3.4 Authorization

- Roles: `owner`, `admin`, `member`, `guest` (per workspace; sourced from IdP claims).
- Resource-level: per-row ACL where applicable (e.g., a project may have member-only visibility).
- All checks at the API boundary (facade or backend), never at the UI. UI hints; backend enforces.
- Tests: each role attempting each action gets the expected result (200 / 403).

### 3.5 Pagination + search

- All list endpoints are cursor-paginated: `?after=<cursor>&limit=<n>` returns `{items, next_cursor}`.
- Default page size: 50; max: 200.
- Server-side search: `?q=<query>` with debounced client-side calls (250ms).
- Server-side sort: `?sort=<field>:<asc|desc>`. Allowlist sort fields per resource.
- Server-side filter: a discrete `?filter[<axis>]=<value>` shape, allowlisted per resource.

### 3.6 Accessibility

- WCAG 2.1 AA. Specifically:
  - Every interactive element has an accessible name (aria-label or text content).
  - Keyboard reachable; visible focus ring (already a token in design-system).
  - Color is never the sole carrier of state (icon + text accompany status colors).
  - Reduced motion respected (`prefers-reduced-motion`).
  - High-contrast theme works (already a token).
- Tests: axe-core on every destination's main + panel views, run in CI.
- Screen reader labels for streaming content (e.g., "Atlas is drafting" announced).

### 3.7 Performance

- LCP < 2.5s on the destination's main view (cold load, broadband).
- INP < 200ms on interactions (click, type, scroll, popover open).
- No re-render of the shell when navigating between destinations (state survives; only the body re-renders).
- Virtualized lists when item count > 100.
- Network: a destination's initial fetch is one round-trip (composed payload, no waterfall). Subsequent fetches are paginated.
- Tests: a lighthouse-like budget check in CI for each destination; render-count assertions on the shell.

### 3.8 Telemetry

- Each user-meaningful action (open destination, open item, perform action) emits an OpenTelemetry span (already a code path in `apps/frontend/src/observability/`).
- Spans include `tenant_id`, `user_id` (hashed), `destination`, `action`. NEVER include PII content (message bodies, names, emails).
- Backend errors emit structured logs with `request_id` correlation.

### 3.9 i18n-readiness

- All user-visible strings are wrapped in a `t()` placeholder (English-only for now; the wrapper is a no-op but extractable). No bare string literals.
- Dates rendered through a locale-aware formatter.
- Numbers, currencies same.

### 3.10 States (UX completeness)

Every screen renders cleanly in each state:

- **Loading** — skeleton matching the final shape (no layout shift on resolve).
- **Empty** — actionable empty state ("Pin a chat to keep it here" + a button to pin).
- **Error** — message + retry button. Errors are user-readable; tech details go to telemetry.
- **Saving** — optimistic UI; rollback on error.
- **Offline** — degraded state with a banner. Reads from cache when possible.
- **Stale** — if cached data is older than a destination-specific TTL, a refresh hint appears.

### 3.11 Cross-destination references

- Items may reference items in other destinations (a todo extracted from a chat; an agent that owns a tool; a project that has members + threads + library docs). These are **typed references**, never freeform strings.
- Backend stores foreign keys with cascade rules (delete-policy per relationship; spelled out in each sub-PRD).
- Frontend renders these as `<ItemLink kind="chat" id={...} />` — a single component that hands off to the right destination's route.

### 3.12 Desktop substrate caveats (per destination)

Every sub-PRD calls out:

- Anything substrate-specific the destination needs (file picker? notifications? OS-level URL handler?).
- Confirm the destination has zero direct browser API access — everything goes through a port in `packages/chat-surface/src/ports/`.

---

## 4. Shared primitives that bind destinations together (DRY)

Destinations look similar because they share these primitives. **If two destinations diverge from a primitive, the divergence is a bug — converge them or extend the primitive.**

### 4.1 Layout

- Generic `<ContextPanel title subtitle search primaryAction children footer>` (already shipped in Wave 1). Each destination's panel composes this.
- Generic `<PageHeader title subtitle actions badges>` and `<FilterTabs>` per `os-shell.jsx`. **Wave 2 introduces these to `packages/chat-surface/src/shell/` and every destination uses them.**
- Generic `<EmptyState icon title sub action>`. One source of truth for empty-state styling.
- Generic `<CardGrid>`, `<DocList>`, `<ActivityList>` — three list primitives the design uses repeatedly.

### 4.2 Status pills + tones

- One `<StatusPill tone="ok|warn|alert|info|neutral" label />` component reading design-system tokens. No per-destination color choices.
- Tone-color mapping is in `packages/design-system/src/styles.css` — single source.

### 4.3 Item references

- `<ItemLink kind={"chat"|"project"|"agent"|"tool"|"doc"|"page"|"dataset"|"person"|"todo"|"inbox-msg"|"memory"} id={...} />` — one component, every cross-destination link.
- Resolves through a registry keyed by `kind`; each destination registers a resolver `(id) => { label, icon, route }` at package load.

### 4.4 Time formatting

- `formatRelativeTime(iso, now)` — already exists in HomeDestination. **Wave 2 hoists it to `packages/chat-surface/src/util/time.ts` and every destination uses it.**

### 4.5 List + detail patterns

- A two-pane "list → detail" pattern is standard: ContextPanel = list (or filters); main = detail. Each destination follows this where applicable.
- Routing convention: `/<dest>/<view?>/<id?>`. The shell encodes the path; destinations consume `{ view, id }` from the route. Already in `apps/frontend/src/app/routes.ts`'s `AppRoute` — Wave 2 extends `AppRoute.chat` (the destination-carrier) to carry `view` and `id` sub-route fields.

### 4.6 Mention typeahead (composer + library pages)

- `<MentionPopover>` (already in `packages/chat-surface/src/composer/`) — references people, agents, docs, pages, datasets, tools, projects, chats. The same component is used wherever `@` is typed across destinations.

### 4.7 ⌘K command palette

- `<CommandPalette>` (Wave 2 introduces it to `packages/chat-surface/src/shell/`). Indexes destinations + actions + items via the same `ItemLink` registry from §4.3.

---

## 5. Per-destination master sections

Each section below is **the brief** for a subagent that writes the destination's sub-PRD and implements it. Sub-PRD location: `docs/atlas-new-design/destinations/{slug}-prd.md`.

The subagent's first job in its phase is **to write the sub-PRD**, get it reviewed (the orchestrator approves), THEN implement. No "implement first, document later".

### 5.1 Home (`/home`)

**User job:** the morning-briefing screen. The first thing the user sees when they open Atlas. Answers "what happened overnight, what's queued, what should I do today."

**Reference designs:**

- `/tmp/atlas-design/enterprise-search-template/project/dest-home.jsx`
- The agent-activity feed copy: "Atlas drafted a 4-page brief, cross-referenced last 6 months of email + call transcripts, and surfaced 3 risk signals worth raising. Two follow-up emails are queued and waiting on your sign-off." (chat1.md line 104)

**Screens:**

- Main: greeting (`Good <time-of-day>, <user>. What are we shipping today?`), agent-activity feed (cards), pinned chats grid, recent runs, favorite tools, today's focus (top 3 todos), upcoming meetings (if calendar connector connected).
- Panel: starred projects shortcuts; quick actions (New chat / New todo / Onboard an API / Build an agent / Invite a teammate).

**Wire (api-types):**

- `HomePayload` = `{ greeting, agent_activity, pinned_chats, recent_runs, favorite_tools, todays_focus, upcoming_meetings, quick_actions }`. Each sub-field has its own type. Cursor pagination not needed for Home (everything is fixed-small).
- Existing `HomePayload` already covers `pinned`, `recent_runs`, `favorites`. The sub-PRD extends it.

**Storage (backend):** Home is **aggregation-only** — it reads from other destinations' tables. No new tables.

**Audit:** not applicable (read-only).

**Cross-destination references:**

- Pinned chats → chats destination
- Recent runs → ai-backend run records (via facade)
- Favorite tools → tools destination
- Today's focus → todos destination
- Upcoming meetings → connectors (calendar)
- Agent activity → agents destination + ai-backend events

**Desktop caveats:** none.

**Open questions for the sub-PRD:**

- What's the time window for "overnight" activity? (Default: last 24h, configurable per user.)
- Does Home cache, and for how long? (Recommend: stale-while-revalidate, 5min TTL.)
- Does Home update in real-time as runs land? (Recommend: yes, subscribe to run-event stream when on Home.)

---

### 5.2 Inbox (`/inbox`)

**User job:** the agent↔human↔agent message stream. When Atlas needs a human (clarifying question, approval-as-message), it lands here. When a teammate's agent needs the user's input, it lands here.

**Reference designs:**

- `/tmp/atlas-design/enterprise-search-template/project/dest-inbox.jsx`
- chat1.md line 313+ ("approval queue" is inline-in-surface, but cross-thread / cross-user notifications land in Inbox)

**Screens:**

- Main: filter tabs (All / Mentions / Approvals / Errors / Done), list of messages, per-message preview with "Reply" / "Open thread" / "Dismiss".
- Panel: filter tree (by sender, by project, by status); saved searches.
- Detail (when an inbox item is open): the originating thread with the message highlighted; reply directly inline.

**Wire:**

- `InboxItem` = `{ id, tenant_id, recipient_user_id, sender_kind: "user"|"agent", sender_id, subject, preview, body_ref, thread_id?, project_id?, status: "unread"|"read"|"done"|"snoozed", priority: "low"|"med"|"high", labels[], created_at, updated_at }`.
- `GET /v1/inbox?filter[status]=unread&q=...&after=...&limit=...`
- `PATCH /v1/inbox/<id>` for read/done/snooze + label edits.

**Storage:** new Postgres table `inbox_items` in `backend`. Indexes on `(tenant_id, recipient_user_id, status, created_at desc)`. Body content stored in `inbox_bodies` (separate so list queries don't pay for body bytes).

**Audit:** read/done/snooze mutations audited. Body access audited (compliance — knowing who saw what when).

**Retention:** 90d default; tenant-configurable.

**Cross-destination references:**

- `thread_id` → chats
- `project_id` → projects
- `sender_id` (agent kind) → agents

**Desktop caveats:** native notifications on new inbox item (post-Wave 2; the port is `NotificationPort`).

**Open questions for the sub-PRD:**

- Snooze semantics — defer to "tomorrow 9am" / "next week" / pick a time?
- Bulk select + bulk mark — yes/no?
- Threading: an inbox item that's a reply to a prior item — group as a conversation, or flat?

---

### 5.3 Todos (`/todos`)

**User job:** action items (extracted from chats / manually added). The "what I need to actually do" list.

**Reference designs:**

- `/tmp/atlas-design/enterprise-search-template/project/todos.jsx`
- `/tmp/atlas-design/enterprise-search-template/project/projects-todos.css`
- chat1.md mentions todos as one of the rail destinations.

**Screens:**

- Main: sections (Today / Overdue / This week / Later / Done). Each section is a list; items have checkbox, text, due, priority, source-attribution chip (chat / user / agent).
- Panel: filter chips (priority, project, source); "New todo" inline-add.

**Wire:**

- `Todo` = `{ id, tenant_id, owner_user_id, text, done, due?, priority: "low"|"med"|"high", source: {kind:"user"|"chat"|"agent", thread_id?, run_id?, agent_id?}, project_id?, labels[], created_at, updated_at }`.
- `GET /v1/todos?...`, `POST /v1/todos`, `PATCH /v1/todos/<id>`, `DELETE /v1/todos/<id>`.

**Storage:** Postgres `todos` table in `backend`. Indexes on `(tenant_id, owner_user_id, done, due asc)`.

**Audit:** create/update/delete audited (especially done-flag).

**Retention:** completed todos retained 365d; uncompleted retained until explicit delete; tombstones 30d.

**Cross-destination references:**

- `source.thread_id` → chats
- `source.run_id` → runs
- `source.agent_id` → agents
- `project_id` → projects

**Desktop caveats:** none (well, the Mac menu bar Atlas icon eventually shows todo count — `BadgePort`).

**Open questions:**

- Recurring todos? (Recommend: not Wave 2; Wave 4.)
- Subtasks? (Recommend: not Wave 2.)
- Atlas auto-extracts todos from chats — opt-in or default-on?

---

### 5.4 Projects (`/projects`)

**User job:** the group-work surface. A project bundles members, agents, chats, library refs, todos. Most enterprise work happens inside a project.

**Reference designs:**

- `/tmp/atlas-design/enterprise-search-template/project/dest-tools.jsx` (no — wrong file)
- chat1.md project model: starred projects show in rail collapse, projects have nested threads, projects own a color/icon.
- `apps/frontend/src/features/chat/runtime/composer/` had a project context that's worth examining.

**Screens:**

- Main (list view at `/projects`): list of projects with member count, agent count, last-activity, starred flag.
- Main (detail at `/projects/<id>`): tabs (Threads / Members / Agents / Library / Todos / Activity). Each tab is an embedded list.
- Panel: project list with star indicator, search, "New project" CTA.

**Wire:**

- `Project` = `{ id, tenant_id, name, description, color_hue, icon_emoji, starred (per-user), created_by, members: ProjectMember[], agents: AgentRef[], created_at, updated_at }`.
- `ProjectMember` = `{ user_id, role: "owner"|"editor"|"viewer", joined_at }`.
- Standard CRUD + member management endpoints.

**Storage:** `projects`, `project_members`, `project_user_stars` (per-user starred flag) in `backend`.

**Audit:** every project mutation, every member add/remove/role-change audited.

**Retention:** projects with active members retained indefinitely; soft-deleted projects 90d.

**Cross-destination references:** projects appear in chats (a thread can be filed under a project), todos, inbox, agents (an agent can be assigned a project), library docs.

**Desktop caveats:** none.

**Open questions:**

- Project-level connector overrides? (Recommend: yes — projects can restrict which connectors are searched.)
- External-collaborator support? (Out of scope for Wave 2; flagged for later.)
- Project templates? (Out of scope for Wave 2.)

---

### 5.5 Library (`/library`)

**User job:** the workspace's documents, pages, and datasets. Files uploaded, pages written, datasets connected. The corpus Atlas knows about for retrieval.

**Reference designs:**

- `/tmp/atlas-design/enterprise-search-template/project/dest-library.jsx`
- chat1.md mentions library quick-link in the command palette.

**Screens:**

- Main: filter tabs (Files / Pages / Datasets). Each is a list with size / row-count / source.
- Panel: kind filter, search, "Upload" / "New page" / "Connect dataset" CTAs.
- Detail at `/library/<view>/<id>`: a doc/page/dataset preview, metadata, access list, embedding status.

**Wire:**

- Three resource types: `LibraryFile`, `LibraryPage`, `LibraryDataset`. Each with own CRUD endpoint. Shared list endpoint `/v1/library?kind=...`.
- Upload via signed-URL pattern (apps don't proxy bytes through the API).

**Storage:** three Postgres tables in `backend`. Blob storage (S3-compatible) for file bytes. Embeddings in a vector store (separate concern — see "open questions").

**Audit:** every upload, edit, delete, share audited. Access to a file's bytes also audited (download events).

**Retention:** files 365d after soft-delete; pages 365d; datasets — when disconnected from source, retained 90d as cached.

**Cross-destination references:** docs/pages/datasets referenced via mentions (composer), citations (assistant messages), and project library tabs.

**Desktop caveats:** drag-and-drop upload uses the `FilePickerPort` so the desktop OS file picker works natively.

**Open questions:**

- Vector store choice — pgvector, Pinecone, Qdrant? (Sub-PRD makes the call; doc-only retrieval is fine on pgvector for tenant-sized corpora.)
- Page editor — block-based (Notion-like) or markdown? (Recommend: markdown for Wave 2; richer editor later.)
- Versioning — every save a version, or only major-edit versions? (Recommend: every save, GC old versions to last-10 per page after 30d.)

---

### 5.6 Agents (`/agents`)

**User job:** persistent invokable agents. The user's "team" of specialized helpers (Research / Sheets / Email / Slides per chat1.md). Agents have skills, MCPs, memory, and runbook history.

**Reference designs:**

- `/tmp/atlas-design/enterprise-search-template/project/dest-agents.jsx`
- chat1.md line 184: subagents with collapsible thinking, tool calls, streaming bodies — that's runtime behavior; the destination is the _management_ surface for agents.

**Screens:**

- Main list at `/agents`: categories (Yours / Workspace / Marketplace). Each agent card shows status (idle / running / needs-attention), description, last-active.
- Main detail at `/agents/<id>`: tabs (Overview / Skills / MCPs / Memory / Runbook history / Permissions). The Overview shows the agent's system prompt (visible to admins, editable by owners), tool budget, model preference.
- Panel: category filter, search, "Build an agent" CTA (opens a wizard).

**Wire:**

- `Agent` = `{ id, tenant_id, name, description, owner_user_id, status, system_prompt, skills[], mcps[], memory_ref, model_preference?, default_depth?, default_tool_budget?, created_at, updated_at, last_active_at }`.
- Build/edit endpoints; invoke endpoint (delegates to ai-backend); list runs endpoint (proxies to ai-backend run records filtered by agent).

**Storage:** `agents` table in `backend`. Runbook history (run records) lives in `ai-backend` per existing architecture.

**Audit:** every create/edit/delete; every system-prompt change (compliance — admins should be able to see what changed).

**Retention:** agents retained while active; soft-deleted agents 90d.

**Cross-destination references:** agents appear in inbox (sender), todos (extracted-by), home (activity feed), projects (assigned agents), tools (an agent owns a set of skills + MCPs).

**Desktop caveats:** none.

**Open questions:**

- Agent marketplace — first-party only or community? (Recommend: first-party for Wave 2; community later behind a review gate similar to tier-2 adapters.)
- Memory format — same as workspace memory destination, or per-agent? (Recommend: shared memory pool; agents reference scoped subsets.)
- Agent-to-agent invocation — should an agent be able to delegate to another? (Already supported by ai-backend's subagent system; UI surface in the runbook detail.)

---

### 5.7 Tools (`/tools`)

**User job:** the catalog of tools Atlas can call. MCP servers, internal APIs, built-in skills. Onboarding wizard for new APIs.

**Reference designs:**

- `/tmp/atlas-design/enterprise-search-template/project/dest-tools.jsx`
- chat1.md mentions "Onboard an internal API" in the ⌘K palette.

**Screens:**

- Main list: categories (MCPs / APIs / Built-ins / Skills). Per-tool: description, scope (read/write), connection status, last-used.
- Main detail: tool's input/output schema, scope, owner, recent invocations.
- Main wizard at `/tools/onboard`: OpenAPI URL → auth picker → scope review → test call → save. (mirrors `wiz` pattern in os.css)
- Panel: category filter, search, "Onboard" CTA.

**Wire:**

- `Tool` = `{ id, tenant_id, name, description, kind: "mcp"|"api"|"builtin"|"skill", scope: "read"|"write"|"both", schema, owner, ... }` (sub-PRD will refine).
- Onboarding endpoints in `backend`.

**Storage:** existing user-skills + MCP registration tables in `backend` extended.

**Audit:** every tool registration, every scope change, every disable/enable audited. Tool invocations audited via ai-backend's existing event pipeline (cross-referenced).

**Retention:** tools retained while referenced; orphan tools 90d.

**Cross-destination references:** tools appear in agents (which tools an agent has), home (favorite tools), composer (which tools are available per chat).

**Desktop caveats:** none.

**Open questions:**

- Tools `kind` field discriminator — being added by Wave 2 in a parallel agent. Sub-PRD waits on that lands.
- Per-tool rate limits — yes/no? (Recommend: yes; defended at the backend; UI shows usage gauge.)

---

### 5.8 Connectors (`/connectors`)

**User job:** auth-gated SaaS sources. Salesforce, Email (Gmail/Outlook), Calendar, Slack, GDrive, etc. Where Atlas reads data from.

**Reference designs:**

- chat1.md references connectors heavily (the chat composer's `ConnectorPopover` already exists).
- `/tmp/atlas-design/enterprise-search-template/project/mcp-overlay.jsx` (MCP overlay is a related popover; connectors destination is the full management page).

**Screens:**

- Main list: sections (Connected / Disconnected). Per-connector: name, icon, last-sync, scope, status.
- Main detail at `/connectors/<id>`: scope, per-chat overrides, sync schedule, audit log of recent reads, disconnect button.
- Panel: filter (connected / disconnected), search, "Connect" CTA.

**Wire:**

- `Connector` = `{ id, tenant_id, kind: "salesforce"|"gmail"|"outlook"|..., display_name, status: "connected"|"disconnected"|"error", scope, owner_user_id, last_sync_at, ... }`.
- OAuth flow already implemented in `backend` (MCP OAuth path).

**Storage:** existing MCP registration store + OAuth token vault in `backend`.

**Audit:** every connect, disconnect, scope change, per-chat override. Reads from connector are audited via ai-backend tool-invocation events.

**Retention:** disconnected connectors retained 30d (token expiry); audit 365d.

**Cross-destination references:** connectors used by chats (per-chat connector overrides), agents (which connectors an agent uses), library datasets (datasets sourced from connectors).

**Desktop caveats:** OAuth flow opens a browser tab; desktop registers a custom URL scheme to receive the callback (handled in main process; chat-surface stays browser-only via the existing facade endpoint).

**Open questions:**

- Connector marketplace expansion path — same review gate as tools?
- Per-project connector restrictions — see Projects open questions.

---

### 5.9 Team (`/team`)

**User job:** the people + their agents. See who's in the workspace, their presence, their agents, recent activity.

**Reference designs:**

- `/tmp/atlas-design/enterprise-search-template/project/dest-misc.jsx` (Team is one of the views)
- chat1.md mentions presence stacks in the topbar.

**Screens:**

- Main list: people grid with avatar, name, role, presence dot, agent count.
- Main detail at `/team/<id>`: person's profile, their agents, recent activity, shared projects.
- Panel: filter (role, presence), search, "Invite a teammate" CTA.

**Wire:**

- `Person` = `{ id, tenant_id, display_name, email, avatar_url, role: "owner"|"admin"|"member"|"guest", presence: "active"|"away"|"in-meeting"|"offline", last_seen_at, agents: AgentRef[], ... }`.
- Invite flow endpoints.

**Storage:** `users` + `tenant_memberships` already exist in `backend`. Presence is volatile state — Redis (or in-memory KV) for now; cross-substrate WebSocket / SSE updates.

**Audit:** invites, role changes audited (admin operations).

**Retention:** offboarded members retained for 90d (handover window); then removed from team view but kept in audit forever.

**Cross-destination references:** people appear everywhere (chat senders, todo owners, agent owners, project members, inbox senders).

**Desktop caveats:** presence in OS menu bar — `PresencePort`.

**Open questions:**

- Multi-workspace people (someone in 3 tenants) — how does the picker work?
- Profile photo upload vs Gravatar vs IdP-supplied — sub-PRD chooses.

---

### 5.10 Memory (`/memory`)

**User job:** what Atlas knows about you and your team. Skills (reusable behaviors), facts (about you / your work), preferences. The "long-term context" surface.

**Reference designs:**

- `/tmp/atlas-design/enterprise-search-template/project/dest-misc.jsx` (Memory is here)
- chat1.md profile menu references "Memory & skills".

**Screens:**

- Main list: categories (Skills / Facts / Preferences). Per-item: created-by (you/agent), last-used, scope (you/workspace).
- Main detail: full item content, edit, scope toggle.
- Panel: category filter, search, "Add memory" CTA.

**Wire:**

- `MemoryItem` = `{ id, tenant_id, scope: "user"|"workspace", kind: "skill"|"fact"|"preference", title, body, created_by: {kind:"user"|"agent", id}, last_used_at, created_at, updated_at }`.
- CRUD endpoints; "use" tracking endpoint (the runtime increments last_used when an agent references a memory).

**Storage:** `memory_items` table in `backend`. Embeddings for retrieval in the same vector store as library.

**Audit:** create/edit/delete; scope changes especially.

**Retention:** indefinite while in use; soft-deleted 90d.

**Cross-destination references:** memories referenced by agents (which memories are in an agent's context), runs (which memories were retrieved for a run).

**Desktop caveats:** none.

**Open questions:**

- Auto-extraction — does the runtime propose memories ("I noticed you always do X — add as a preference?")? (Recommend: yes, with explicit confirm.)
- Memory versioning — every edit a version? (Recommend: yes, audit-driven, GC old versions per same rule as library pages.)
- Memory expiry — does a memory go stale? (Recommend: no automatic expiry; user-driven cleanup with "last used 6 months ago" hints.)

---

## 6. Cross-destination dependencies (which order to build)

| Phase | Destination(s)                                            | Why this order                                                               |
| ----- | --------------------------------------------------------- | ---------------------------------------------------------------------------- |
| 0     | Shell + Composer + Depth + Tools-kind                     | Already done (Wave 1 + 1.5) or in flight (A1, A2)                            |
| 1     | Chats thread canvas (Studio/Focus/Auto) + right rail tabs | Chats is the highest-traffic destination; the canvas is the headline feature |
| 2     | Home                                                      | Reads from many destinations — needs them sketched, not finished             |
| 3     | Todos                                                     | Self-contained; needed for Home's "today's focus" section                    |
| 4     | Inbox                                                     | Self-contained; needed for Home's "agent activity" cross-references          |
| 5     | Projects                                                  | Reaches into many destinations; deferred until Todos + Inbox are stable      |
| 6     | Library                                                   | Standalone but big (vector store decisions)                                  |
| 7     | Agents                                                    | Depends on Library (memory) + Tools                                          |
| 8     | Tools (full destination)                                  | Depends on Tools-kind from phase 0                                           |
| 9     | Connectors (full destination)                             | Builds on existing OAuth path                                                |
| 10    | Team                                                      | Builds on existing tenant memberships                                        |
| 11    | Memory                                                    | Depends on Library's vector store                                            |
| 12    | ⌘K palette + cross-destination polish                     | After everything else lands                                                  |

Phases 2-11 are largely independent in code (different `destinations/{slug}/` folders, different backend modules). They CAN be parallelized — 2 or 3 destinations in flight at once is fine. The dependency above is about WHICH PRODUCT VALUE arrives first, not which code conflicts.

---

## 7. Subagent dispatch pattern (per destination)

Every destination phase follows this pattern:

### 7.1 Step 1 — sub-PRD writer (single agent, ~30 min)

Brief: "You are a STAFF ENGINEER. Read this master PRD section 5.<n> for {slug}, the design files at `/tmp/atlas-design/.../dest-{slug}.jsx`, and the relevant existing code. Write `docs/atlas-new-design/destinations/{slug}-prd.md` answering every item in §3 (the enterprise checklist) and every open question in §5.<n>. Do NOT implement anything. Report back the sub-PRD path + a one-paragraph summary."

Orchestrator reviews the sub-PRD. Approves or sends back for revision.

### 7.2 Step 2 — implementation agents (2-3 in parallel, ~60-90 min each)

Once the sub-PRD lands:

- **Agent B-T: api-types + backend** — extends `packages/api-types/src/{slug}.ts`, builds the backend route module + Postgres tables + audit hooks + tests.
- **Agent B-F: frontend** — implements `{Slug}Destination.tsx` + `{Slug}Panel.tsx` + wires from `apps/frontend/src/app/App.tsx` + tests.
- **Agent B-D (optional)** — desktop-specific port implementation if the sub-PRD requires it.

### 7.3 Step 3 — verification (orchestrator)

- Merge the three branches in order: api-types/backend → frontend → desktop port.
- Run all tests (chat-surface, frontend, backend, ai-backend, backend-facade) + typechecks.
- Browser verify the destination in dev.
- Update the master PRD's per-destination "open questions" with the resolved answers.
- Close the phase.

### 7.4 Boilerplate every subagent gets

Every dispatched agent gets, prepended to its prompt:

```
You are a STAFF ENGINEER. Think from architectural principles:
DRY, substitution, SIMPLE & ELEGANT code is best code, ONE source
of truth, performant code, USER & UX first. No premature
abstraction; no half-finished work. This is NOT an MVP — it is
enterprise-grade software (multi-tenant, audited, paginated,
accessible, performant).

HARD WORKTREE RULES:
- Isolated git worktree at .claude/worktrees/<your-id>/.
  Verify with `pwd && git branch --show-current` BEFORE first write.
- Create branch off `main` IMMEDIATELY (git checkout -b
  worktree-agent-<phase>-<slug>-<role>). Never commit on main.
- NEVER write to or `cd` into the orchestrator's repo path.
- Don't merge. Report back worktree + branch + summary.

Required reading:
- docs/atlas-new-design/PRD.md (shell + composer + canvas)
- docs/atlas-new-design/destinations-master-prd.md §3 (enterprise
  checklist), §4 (shared primitives), §5.<n> (your destination)
- The destination's sub-PRD: docs/atlas-new-design/destinations/<slug>-prd.md
- Design references at /tmp/atlas-design/enterprise-search-template/
```

---

## 8. Phase plan (10 phases — explicit)

(Phase 0 already shipped or in flight; phase 1 is the chats canvas which is the headline.)

| Phase | Name                                                       | Sub-agents                                         | Estimated wall time |
| ----- | ---------------------------------------------------------- | -------------------------------------------------- | ------------------- |
| 0     | Foundation (shell, composer, Depth contract, Tools kind)   | done + 2 in flight                                 | done                |
| 1     | Chats thread canvas + right rail tabs + Composer migration | 1 sub-PRD + 3 impl                                 | 1-2 work-days       |
| 2     | Home                                                       | 1 sub-PRD + 2 impl                                 | 1 work-day          |
| 3     | Todos                                                      | 1 sub-PRD + 2 impl                                 | 1 work-day          |
| 4     | Inbox                                                      | 1 sub-PRD + 2 impl                                 | 1-2 work-days       |
| 5     | Projects                                                   | 1 sub-PRD + 2 impl                                 | 1-2 work-days       |
| 6     | Library                                                    | 1 sub-PRD + 3 impl (vector store is its own slice) | 2 work-days         |
| 7     | Agents                                                     | 1 sub-PRD + 2 impl                                 | 1-2 work-days       |
| 8     | Tools (full destination)                                   | 1 sub-PRD + 2 impl                                 | 1-2 work-days       |
| 9     | Connectors (full destination)                              | 1 sub-PRD + 2 impl                                 | 1-2 work-days       |
| 10    | Team + Memory + ⌘K + polish                                | 1 sub-PRD per area + 2 impl each                   | 2 work-days         |

Total: 10 phases after foundation. Each phase ends with a green test suite and a user-visible deliverable. The user pauses and re-prioritizes between phases.

---

## 9. Anti-goals (across all destinations)

- **No throwaway code.** Every line shipped is production-grade. Skip a feature; don't half-ship it.
- **No silent placeholders.** A destination that doesn't have its detail screen yet renders an explicit empty state, not "TODO" text.
- **No bespoke styling.** Every visual lives in design-system tokens or chat-surface primitives.
- **No frontend-only logic that the backend should own.** UI hints; backend enforces.
- **No PII in logs or telemetry.**
- **No web-only API access from a destination.** Substrate-specific access goes through a port.
- **No "wave 2-only" shortcuts that we promise to fix later.** If it isn't enterprise-ready, it doesn't ship.

---

## 10. Open product decisions (orchestrator-level)

These need a call from product (parth) before the dependent phase starts. Already-flagged decisions from [PRD.md](PRD.md) §13 still stand; below are destination-level adds.

1. **Default home view content for new tenants.** (When a tenant has no chats / agents / activity yet, what does Home show?)
2. **Workspace plan tiers** — does a tenant's plan limit destination features? (E.g., 5 connectors on Starter, unlimited on Enterprise.) If yes, the UI needs plan-gating; the backend enforces. Sub-PRDs include plan-gating as a TODO if applicable.
3. **External-collaborator support across destinations** — guest role consistency.
4. **Per-project connector overrides** — see §5.4.
5. **Notification preferences** — global, per-destination, per-thread? Needed before Wave 2 desktop notifications.
6. **Data residency** — single-region or multi-region storage? Affects storage choices in Library / Memory.

---

## 11. References

- [PRD.md](PRD.md) — shell + composer + thread canvas (the foundation this builds on)
- `/tmp/atlas-design/enterprise-search-template/` — design source bundle
- [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md) — frontend engineering rules
- [`services/backend/CLAUDE.md`](../../services/backend/CLAUDE.md) — product persistence rules
- [`services/backend-facade/CLAUDE.md`](../../services/backend-facade/CLAUDE.md) — facade rules
- [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md) — runtime rules
- [`packages/api-types/CLAUDE.md`](../../packages/api-types/CLAUDE.md) — contract stewardship
- Compliance section in [`CLAUDE.md`](../../CLAUDE.md) — controls, retention, audit
