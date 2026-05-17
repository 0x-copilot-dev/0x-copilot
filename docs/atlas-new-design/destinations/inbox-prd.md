# Inbox Destination — Sub-PRD

**Status:** draft (2026-05-17)
**Owner:** parth (orchestrator) — implementation delegated to phase-4 impl agents
**Master:** [destinations-master-prd.md §5.2](../destinations-master-prd.md#52-inbox-inbox)
**Foundation:** [PRD.md](../PRD.md) — workspace shell + composer
**Design references:**

- `/tmp/atlas-design/enterprise-search-template/project/dest-inbox.jsx` — main view + panel reference
- `/tmp/atlas-design/enterprise-search-template/project/os-data.jsx:141-271` — `MOCK_INBOX` shape (8 fixture items spanning every kind)
- `/tmp/atlas-design/enterprise-search-template/project/os-app.jsx:149` — `<InboxMain navigate={navigate} route={route} adminMode={adminMode} />` mount + breadcrumb wiring at lines 84-108, badge count at line 111
- `/tmp/atlas-design/enterprise-search-template/chats/chat1.md:295-322` — settled the in-thread inline approval ≠ Inbox distinction; chat1.md:309-322 ("Approval queue position? → We don't need this. Approval happens via diffs in the surface.") is the load-bearing decision

---

## §1 Premise + user job

### 1.1 What Inbox is

Inbox is the user's **pull list of items addressed to them, where the user is not currently looking**. It is the surface a user opens when they want to answer "what needs me right now, outside the thread I'm in."

Concretely, four item kinds land here:

1. **Mentions** — a teammate or a teammate's agent `@user`s the recipient on a page, doc, comment, or chat thread the recipient is not in.
2. **Approval requests routed cross-thread** — Atlas in thread A needs the user's input or a sign-off, but the user is currently working in thread B (or not in chats at all). The approval **belongs to** thread A; Inbox is the **out-of-band notification surface** that pulls the user back.
3. **Errors** — a connector token expired, a scheduled run failed, a tool returned an unrecoverable error. The user is the only one who can resolve it (re-auth, edit credentials, change scope).
4. **System** — billing changes, plan-tier transitions, admin actions taken on the user's account, retention warnings. Low frequency; high importance.

Inbox is a **bounded discrete list**: each item has a clear owner (the recipient), a clear sender, a clear action (reply / open thread / mark done / dismiss), and a clear terminal state (done or dismissed).

### 1.2 What Inbox is NOT

| Anti-goal                    | Why not                                                                                                                                                                                                                                         |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **In-thread approval queue** | Approvals on Atlas-drafted edits live inline in the surface where the edit lands (chat1.md:309-322). The diff IS the approval UI. Inbox shows approvals **only** when the user is not currently in the originating thread.                      |
| **Slack channel**            | Threads are bounded sessions, not continuous streams. Inbox does not show a running feed of agent activity. If you want "what's Atlas touching now," the answer is the thread canvas (Studio mode swimlane), not Inbox.                         |
| **Email reader**             | No compose-to-arbitrary-address. Inbox is for Atlas-originated and teammate-agent items. Replying lands back in the originating thread (or creates a new thread); it does not leave the workspace as an email.                                  |
| **Global activity feed**     | Activity-feed semantics are "every event that touched the workspace." Inbox semantics are "items addressed to **this** user." A row in the activity feed (Home destination) is never in Inbox unless it explicitly requires this user's action. |
| **Chat client**              | Replies route to the originating thread; the Inbox is a read+act surface, not a continuous conversation surface. If a back-and-forth develops, it flows through the thread, not through Inbox.                                                  |
| **Third-party email/social** | Wave 2 does not pull Gmail or LinkedIn into Inbox. Those are connector concerns — Atlas can _read_ them as sources, but they don't appear as inbox items.                                                                                       |

### 1.3 Inbox vs. in-surface approval — the routing rule

The product-critical rule (see §16 Q6 for product sign-off):

> An Atlas-drafted edit creates an **inline approval block in the surface**. If, within `INBOX_FALLBACK_INACTIVITY_MS` (default: 5 minutes), the user has not viewed the thread, **and** the approval is `priority: high`, the runtime additionally emits an Inbox item pointing to that approval. Resolving the approval (accept / reject / edit) in the surface auto-resolves the Inbox item.

This means:

- **Default path:** approval lives inline, never touches Inbox.
- **Fallback path:** if the user is elsewhere, Inbox pulls them back.
- **Convergence:** the approval has one source of truth (the inline block); Inbox holds a pointer to it.

Two implications:

1. Inbox is **not** the source of approval state. The ai-backend's `approvals` table is. Inbox items of kind `approval_request` carry `approval_id` and dereference for status.
2. Resolving inline auto-dismisses Inbox. Resolving from Inbox routes through the inline block (clicks "Open thread" or shows an inline-equivalent block in the Inbox detail).

---

## §2 Source-of-truth map

Per master PRD §2.2, each artefact has **exactly one** canonical location.

| Concern                         | Canonical file                                                                                        | Status                               |
| ------------------------------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------ |
| Wire types                      | `packages/api-types/src/inbox.ts` (NEW)                                                               | introduce, re-export from `index.ts` |
| Main destination view           | `packages/chat-surface/src/destinations/inbox/InboxDestination.tsx`                                   | EXISTS — extend                      |
| Context panel                   | `packages/chat-surface/src/destinations/inbox/InboxPanel.tsx` (NEW)                                   | introduce                            |
| Detail (`/inbox/<id>`)          | `packages/chat-surface/src/destinations/inbox/InboxDetail.tsx` (NEW)                                  | introduce                            |
| Backend route module            | `services/backend/src/backend_app/inbox/` (NEW): `routes.py`, `service.py`, `store.py`, `schema.py`   | introduce                            |
| Backend Postgres schema         | `services/backend/src/backend_app/inbox/schema.py` + Alembic migration                                | introduce                            |
| Facade proxy                    | `services/backend-facade/src/backend_facade/inbox_routes.py` (NEW)                                    | introduce                            |
| Producer (ai-backend → backend) | `services/ai-backend/src/agent_runtime/api/inbox_producer.py` (NEW)                                   | introduce                            |
| Frontend hook                   | `apps/frontend/src/api/inbox.ts` (NEW; HTTP wrappers + SSE subscription)                              | introduce                            |
| Item-link registry registration | `packages/chat-surface/src/destinations/inbox/index.ts` (extend the `ItemLink` resolver registration) | extend                               |

A second copy of any of these is a bug.

---

## §3 Architecture

### 3.1 Layout (web + desktop)

```
┌──────┬────────┬──────────────────────────────────────┬───────────┐
│ 52   │ 224    │     1fr (main)                       │   0/380   │
│ rail │ Inbox  │  ┌────────────────────────────────┐  │           │
│      │ Panel  │  │ Topbar  44px                   │  │ (Right    │
│      │        │  ├────────────────────────────────┤  │  rail     │
│      │ filter │  │ PageHeader: "Inbox" + actions  │  │  hidden   │
│      │ tree + │  ├────────────────────────────────┤  │  for      │
│      │ saved  │  │ FilterTabs (5)                 │  │  inbox)   │
│      │ search │  ├────────────────────────────────┤  │           │
│      │        │  │ virtualized list  OR  detail   │  │           │
└──────┴────────┴──────────────────────────────────────┴───────────┘
```

- Workspace shell from `ChatShell.tsx`. ContextPanel slot receives `<InboxPanel>`.
- Right rail collapsed when destination=inbox (per `PRD.md §10`: "Default to collapsed; explicit opt-in per destination" — Inbox does not opt in).
- The "list vs detail" pivot lives inside the main pane and is driven by `route.id`:
  - `route = { destination: "inbox", id: null }` → list view
  - `route = { destination: "inbox", id: "<inbox-id>" }` → detail view (still inside the same `InboxDestination` component)

### 3.2 Main view (list)

`InboxDestination.tsx` renders, from top to bottom:

1. `<PageHeader title="Inbox" subtitle="Items addressed to you" actions={[BulkMenu, RefreshButton]} badges={[unreadCount, snoozedCount]} />`
2. `<FilterTabs value={filter} options={["all","mentions","approvals","errors","done"]} counts={countsByFilter} />` — keyboard-navigable per `os-shell.jsx:FilterTabs` reference; arrow keys cycle, Enter selects.
3. **List body** — virtualized when total items > 100 (use `@tanstack/react-virtual`; introduced once for Inbox, reusable by Todos/Library lists per master §4.1).

Per-item row (matches `dest-inbox.jsx:91-127` density):

```
[ avatar ]  [ Sender → Recipient                                    [ time-ago ] ]
            [ **Subject** ]
            [ preview line truncated to one row, 200 chars ]
            [ kindChip ] [ needsChip? ] [ priorityChip? ]  [ status dot ]
            [ actions visible on hover/focus: Reply · Open thread · Snooze · Dismiss · Mark done ]
```

Unread rows are bold with a left accent bar; read rows are normal-weight; done rows render at 60% opacity (when `filter=done`). Empty list shows the per-filter empty state (§12).

### 3.3 Panel view (context panel)

`InboxPanel.tsx` composes the generic `<ContextPanel title="Inbox" subtitle="Items addressed to you">` from master §4.1. Sections, top-to-bottom:

1. **Quick filters** — same 5 axes as the main `FilterTabs` but listed vertically with counts (mirrors `dest-inbox.jsx:28-50` but using the typed filter set from §4). Click any → updates `filter` in route state; the main FilterTabs reflect the selection (one source of truth).
2. **Search** — debounced 250ms client-side; calls `GET /v1/inbox?q=…`.
3. **By sender** — collapsible tree, grouped by `sender.kind`:
   - **Agents** — counts per agent (only agents with ≥1 inbox item)
   - **People** — counts per human sender
   - **System** — counts per origin (`connector_error`, `billing`, `admin`)
4. **By project** — list of projects with ≥1 inbox item (foreign key to projects destination).
5. **Saved searches** — user-defined queries with names. Storage: `inbox_saved_searches` table (small; tenant + user + name + filter-payload JSON). CRUD endpoints `POST/DELETE /v1/inbox/saved-searches`. Cap at 20 per user (UI prevents adding more; backend enforces).
6. **Inbox rules** (footer) — link out to Agents → policies (mirrors `dest-inbox.jsx:51-57` "Edit policies →"). Surfaces the "your agent auto-resolves agent-to-agent requests when policy allows" affordance promised in the design.

### 3.4 Detail view (`/inbox/<id>`)

`InboxDetail.tsx` is mounted by `InboxDestination` when `route.id !== null`. Layout:

```
┌──────────────────────────────────────────────────┐
│ [< back to inbox]                                │
│                                                  │
│ ┌─ Item header ─────────────────────────────┐   │
│ │ [sender avatar] Sender → Recipient        │   │
│ │ **Subject**                               │   │
│ │ [kind chip] [priority chip] [labels...]   │   │
│ │ time-ago · ⊙ status                       │   │
│ │ [ Mark done ] [ Snooze ▾ ] [ Dismiss ]    │   │
│ └───────────────────────────────────────────┘   │
│                                                  │
│ ┌─ Body ────────────────────────────────────┐   │
│ │ (markdown-rendered; lazy-loaded after     │   │
│ │  detail mount via GET /v1/inbox/<id>)     │   │
│ └───────────────────────────────────────────┘   │
│                                                  │
│ ┌─ Originating thread preview ──────────────┐   │
│ │ (last 3 messages of thread_id with the    │   │
│ │  linked message highlighted)              │   │
│ │ [ Open full thread → ]                    │   │
│ └───────────────────────────────────────────┘   │
│                                                  │
│ ┌─ Inline reply composer ───────────────────┐   │
│ │ [ textarea: Reply to <sender>… ]          │   │
│ │ [ Send ]  routes to thread or new thread  │   │
│ └───────────────────────────────────────────┘   │
└──────────────────────────────────────────────────┘
```

Detail behaviour:

- **Body lazy fetch.** Body is fetched only on detail mount (the list endpoint never returns body bytes; see §5). This keeps the list small and audits body access discretely (§6).
- **Originating thread preview.** When `thread_id` is set, the last 3 messages of that thread render inline with the linked message highlighted. If the thread was deleted (cascade rule §13), the preview shows a deleted-thread placeholder card.
- **Reply composer.** If `thread_id` set → reply lands in that thread (`POST /v1/conversations/{thread_id}/messages` via the existing ai-backend conversation API, proxied by facade). If no `thread_id` → creates a new thread between sender and recipient (`POST /v1/conversations`). Reply is **append**, not delete — the inbox item is auto-marked done if it was an approval-request kind, otherwise it stays read until explicit done/dismiss.
- **Approval-kind detail.** When `kind = "approval_request"` and an `approval_id` is present, the detail embeds an inline `<ApprovalCard approvalId={…} />` (re-using the existing approval primitive from PR-1.4 / PR-4.4.6.2). Accepting / rejecting the approval inline auto-completes the Inbox item.

### 3.5 Producer flow (ai-backend → backend)

The runtime emits "agent needs human attention" events today via the per-user `InboxEventBus` (see `services/ai-backend/src/runtime_api/sse/inbox_bus.py`). Wave 2 evolves this into a **two-tier model**:

1. **Tier 1 — ephemeral SSE pulse** (existing — keep): per-user run-scoped events on `/v1/agent/me/inbox/stream` for `approval_assigned` / `approval_resolved`. Used for instant rail-badge updates while the user is online.
2. **Tier 2 — persisted Inbox row** (NEW): the runtime decides whether the assignment warrants a durable Inbox item (see §1.3 routing rule + §16 Q6). If yes, ai-backend posts to backend `POST /internal/v1/inbox/items` with the producer payload (§4.5). Backend assigns id, validates auth (§7), writes the row, audits, and returns 201.

Why two tiers: the in-surface inline approval doesn't need a durable inbox row when the user is actively in the thread — that would create UI noise. The durable row only exists when the user must be pulled back from elsewhere.

```
ai-backend.runtime_worker → emits RuntimeEvent (e.g. approval_requested)
        │
        ├── always: publish to InboxEventBus (SSE pulse to user session)
        │
        └── if (recipient not in originating thread) and (priority=high or kind∈{error,mention}):
                POST internal/v1/inbox/items  →  backend.inbox.service
                                                     │
                                                     ├── auth: service-token + claim assertion
                                                     ├── insert inbox_items row
                                                     ├── audit row (audit-chain)
                                                     └── publish SSE on /v1/inbox/stream (browser channel)
```

The frontend subscribes to **both**:

- `/v1/inbox/stream` (backend channel; durable items)
- `/v1/agent/me/inbox/stream` (ai-backend channel; ephemeral approval pulses) — backward-compat with PR-1.4.1.

In phase 4.x the two SSE endpoints converge into a single `/v1/inbox/stream` served by the facade that multiplexes both upstreams. Out of scope for this PRD; phase 4.5.

### 3.6 Real-time push (badge + rail pulse)

- `/v1/inbox/stream` (SSE) — server-push for new items, status changes, deletions.
- Each envelope: `{ sequence_no, event_type: "item_created"|"item_updated"|"item_deleted", item_summary, emitted_at }` — modelled on the `RuntimeEventEnvelope` reconnect contract (master uses `sequence_no` for resume; same here).
- Frontend reconnect: `GET /v1/inbox/stream?after_sequence=N`. On connect, server replays buffered envelopes with `sequence_no > N`.
- Rail badge: derived from `GET /v1/inbox/unread_count` on initial load, then incremented/decremented from SSE deltas (one source of truth, server-validated on each load).
- Degraded mode: if SSE fails (corporate proxy, sleep/wake), fall back to polling `/v1/inbox/unread_count` every 60s.
- Native notification (desktop only): on `item_created` where `priority=high`, fire through `NotificationPort` (Wave 2 introduces this port; web is a no-op).

---

## §4 Wire contracts

### 4.1 Types (`packages/api-types/src/inbox.ts`)

```typescript
// Branded id types — opaque to consumers; backend issues.
export type InboxItemId = string & { readonly __brand: "InboxItemId" };
export type InboxBodyRef = string & { readonly __brand: "InboxBodyRef" };

export type InboxItemKind = "mention" | "approval_request" | "error" | "system";

export type InboxItemStatus = "unread" | "read" | "done" | "snoozed";

export type InboxItemPriority = "low" | "med" | "high";

export type InboxSenderKind = "user" | "agent" | "system";

export type InboxSystemOrigin =
  | "connector_error"
  | "billing"
  | "retention_warning"
  | "admin_action";

export type InboxSender =
  | { kind: "user"; user_id: string }
  | { kind: "agent"; agent_id: string; agent_name: string }
  | { kind: "system"; origin: InboxSystemOrigin };

export interface InboxItem {
  readonly id: InboxItemId;
  readonly tenant_id: string;
  readonly recipient_user_id: string;
  readonly sender: InboxSender;
  readonly kind: InboxItemKind;
  readonly subject: string; // ≤ 200 chars
  readonly preview: string; // ≤ 200 chars
  readonly body_ref: InboxBodyRef; // dereferenced via /v1/inbox/<id> for body
  readonly thread_id?: string;
  readonly run_id?: string;
  readonly approval_id?: string; // present iff kind = "approval_request"
  readonly project_id?: string;
  readonly status: InboxItemStatus;
  readonly snoozed_until?: string; // ISO-8601; present iff status="snoozed"
  readonly priority: InboxItemPriority;
  readonly labels: ReadonlyArray<string>;
  readonly created_at: string; // ISO-8601
  readonly updated_at: string;
}

export interface InboxItemBody {
  readonly id: InboxItemId;
  readonly body: string; // markdown; rendered client-side via existing markdown primitive
}

export interface InboxListResponse {
  readonly items: ReadonlyArray<InboxItem>;
  readonly next_cursor: string | null;
}

export interface InboxUnreadCount {
  readonly unread: number;
  readonly high_priority_unread: number; // surfaced separately on the rail
  readonly as_of: string;
}

export type InboxStreamEventType =
  | "item_created"
  | "item_updated"
  | "item_deleted";

export interface InboxStreamEnvelope {
  readonly sequence_no: number;
  readonly event_type: InboxStreamEventType;
  readonly item: InboxItem; // full item for created/updated; for deleted: tombstone (status="done" with deleted_at set)
  readonly emitted_at: string;
}
```

### 4.2 Endpoints (facade — what apps call)

| Method | Path                            | Purpose                                                                                                                                            |
| ------ | ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/v1/inbox`                     | List items. Query: `filter[status]`, `filter[kind]`, `filter[sender_kind]`, `filter[project_id]`, `q`, `sort`, `after`, `limit`. Cursor-paginated. |
| GET    | `/v1/inbox/{id}`                | Single item + body (joins `inbox_bodies`). Audited per §6.                                                                                         |
| PATCH  | `/v1/inbox/{id}`                | Mutate status (`read`/`done`/`snoozed`+`snoozed_until`), edit `labels`. Tenant + recipient ACL.                                                    |
| DELETE | `/v1/inbox/{id}`                | Soft delete (dismiss). Tombstone retained per §5.                                                                                                  |
| POST   | `/v1/inbox/{id}/reply`          | Send a reply. Body: `{ text: string }`. Routes to `thread_id` if set, else creates new thread (returns the resulting `thread_id`).                 |
| GET    | `/v1/inbox/unread_count`        | Lightweight count for rail badge. No body bytes. Cached at edge for 5s.                                                                            |
| GET    | `/v1/inbox/stream`              | SSE; reconnect via `?after_sequence=N`. Per-user channel; backend enforces tenant + recipient ACL.                                                 |
| GET    | `/v1/inbox/saved-searches`      | List user's saved searches.                                                                                                                        |
| POST   | `/v1/inbox/saved-searches`      | Create. Body: `{ name, filter_payload }`. Cap 20 per user.                                                                                         |
| DELETE | `/v1/inbox/saved-searches/{id}` | Delete.                                                                                                                                            |
| POST   | `/v1/inbox/{id}/bulk-action`    | See §16 Q2 — if bulk lands, this is the endpoint. Spec deferred to product call.                                                                   |

### 4.3 Endpoints (internal — ai-backend → backend)

| Method | Path                            | Purpose                                                                                                                                    |
| ------ | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| POST   | `/internal/v1/inbox/items`      | Producer endpoint. Service-token auth + recipient/tenant claim assertion. Returns 201 with assigned `id`.                                  |
| PATCH  | `/internal/v1/inbox/items/{id}` | Producer can update its own items (e.g., to mark `done` when the upstream approval resolves). Idempotent on `(producer_id, external_ref)`. |

### 4.4 Filter / sort allowlists

Per master §3.5, allowlist everything server-side.

- `filter[status]`: `unread` | `read` | `done` | `snoozed` (or omit → all except dismissed)
- `filter[kind]`: `mention` | `approval_request` | `error` | `system`
- `filter[sender_kind]`: `user` | `agent` | `system`
- `filter[project_id]`: UUID
- `filter[sender_id]`: UUID (agent_id or user_id; combined with `sender_kind` for clarity)
- `sort`: `created_at:desc` (default) | `created_at:asc` | `priority:desc` | `snoozed_until:asc`
- `q`: free-text; PostgreSQL `to_tsvector` on `subject + preview` (GIN index, §5.2)

### 4.5 Producer payload

```typescript
// Posted from ai-backend → backend on /internal/v1/inbox/items
interface ProducerInboxItem {
  // recipient — server validates tenant match
  readonly recipient_user_id: string;
  readonly tenant_id: string; // asserted by service token

  // identity — server fills sender_kind from the calling agent's claims
  readonly sender_agent_id?: string; // present iff producer is an agent
  readonly sender_agent_name?: string; // denormalized for display
  readonly sender_system_origin?: InboxSystemOrigin;

  readonly kind: InboxItemKind;
  readonly subject: string;
  readonly preview: string;
  readonly body: string; // markdown; stored in inbox_bodies
  readonly thread_id?: string;
  readonly run_id?: string;
  readonly approval_id?: string;
  readonly project_id?: string;
  readonly priority?: InboxItemPriority; // default "med"
  readonly labels?: ReadonlyArray<string>;

  // Idempotency — producer-assigned external ref. Backend
  // (producer_id, external_ref) is unique; resubmits return 200 with the
  // existing row.
  readonly external_ref: string;
}
```

---

## §5 Storage + retention

### 5.1 Tables (Postgres, owned by `services/backend`)

**`inbox_items`** — one row per inbox item.

| Column                | Type                           | Notes                                                                     |
| --------------------- | ------------------------------ | ------------------------------------------------------------------------- |
| `id`                  | uuid PK                        | Backend-assigned.                                                         |
| `tenant_id`           | uuid NOT NULL                  | Filter axis on **every** query.                                           |
| `recipient_user_id`   | uuid NOT NULL                  | The user this item is for.                                                |
| `sender_kind`         | text NOT NULL                  | `user` / `agent` / `system`.                                              |
| `sender_id`           | text NULL                      | `user_id` if user; `agent_id` if agent; `null` otherwise.                 |
| `sender_origin`       | text NULL                      | for `system`: `connector_error` / `billing` / ...                         |
| `sender_display_name` | text NULL                      | denormalized for list view; refreshed on producer write                   |
| `kind`                | text NOT NULL                  | `mention` / `approval_request` / `error` / `system`.                      |
| `subject`             | text NOT NULL (len ≤ 200)      |                                                                           |
| `preview`             | text NOT NULL (len ≤ 200)      |                                                                           |
| `thread_id`           | uuid NULL                      |                                                                           |
| `run_id`              | uuid NULL                      |                                                                           |
| `approval_id`         | uuid NULL                      | present iff `kind = approval_request`                                     |
| `project_id`          | uuid NULL                      |                                                                           |
| `status`              | text NOT NULL DEFAULT 'unread' | `unread` / `read` / `done` / `snoozed`                                    |
| `snoozed_until`       | timestamptz NULL               |                                                                           |
| `priority`            | text NOT NULL DEFAULT 'med'    | `low` / `med` / `high`                                                    |
| `labels`              | text[] NOT NULL DEFAULT '{}'   |                                                                           |
| `producer_id`         | text NULL                      | e.g. `ai-backend` — for idempotency key.                                  |
| `external_ref`        | text NULL                      | producer-assigned idempotency key.                                        |
| `created_at`          | timestamptz NOT NULL           |                                                                           |
| `updated_at`          | timestamptz NOT NULL           |                                                                           |
| `deleted_at`          | timestamptz NULL               | soft-delete marker. Hidden from all queries unless admin compliance read. |

Constraints:

- UNIQUE `(tenant_id, producer_id, external_ref) WHERE external_ref IS NOT NULL` — idempotency.
- CHECK `kind ∈ {mention, approval_request, error, system}`
- CHECK `(status = 'snoozed') = (snoozed_until IS NOT NULL)`

**`inbox_bodies`** — one row per item; split out so list queries don't pay for body bytes.

| Column       | Type          | Notes                     |
| ------------ | ------------- | ------------------------- |
| `id`         | uuid PK / FK  | Same as `inbox_items.id`. |
| `tenant_id`  | uuid NOT NULL | Matches parent for ACL.   |
| `body`       | text NOT NULL | Markdown; max 64KB.       |
| `created_at` | timestamptz   |                           |

ON DELETE CASCADE from `inbox_items.id`.

**`inbox_saved_searches`** — small per-user table.

| Column           | Type           | Notes                              |
| ---------------- | -------------- | ---------------------------------- |
| `id`             | uuid PK        |                                    |
| `tenant_id`      | uuid NOT NULL  |                                    |
| `user_id`        | uuid NOT NULL  |                                    |
| `name`           | text NOT NULL  |                                    |
| `filter_payload` | jsonb NOT NULL | validated against filter allowlist |
| `created_at`     | timestamptz    |                                    |
| `updated_at`     | timestamptz    |                                    |

UNIQUE `(tenant_id, user_id, name)`. CHECK `name <= 80 chars`. Backend enforces ≤ 20 rows per `(tenant_id, user_id)`.

### 5.2 Indexes

- `inbox_items_recipient_status_idx` — B-tree on `(tenant_id, recipient_user_id, status, created_at DESC) WHERE deleted_at IS NULL` — primary list query.
- `inbox_items_recipient_kind_idx` — B-tree on `(tenant_id, recipient_user_id, kind, created_at DESC) WHERE deleted_at IS NULL` — kind-filter.
- `inbox_items_search_idx` — GIN on `to_tsvector('simple', subject || ' ' || preview) WHERE deleted_at IS NULL` — search.
- `inbox_items_snooze_idx` — B-tree on `(tenant_id, snoozed_until) WHERE status='snoozed'` — for the snooze-wake cron.
- `inbox_items_idem_idx` — UNIQUE partial as above for `(producer_id, external_ref)`.

### 5.3 Retention

Per master §3.3:

- **Items**:
  - `done` rows: retained 90 days from `updated_at`, then soft-deleted (sets `deleted_at`).
  - Soft-deleted rows (`deleted_at` set): retained 30 days, then hard-deleted by cron.
  - `unread` / `read` / `snoozed`: indefinite (until user-driven action).
  - Tenant-configurable via `tenants.inbox_retention_days` (admin-only setting; surfaced in Settings → Workspace).
- **Bodies**: cascade with parent.
- **Saved searches**: indefinite (no auto-expiry).
- **Audit rows**: 365 days (master rule).

### 5.4 Cleanup job (cron)

A daily backend cron (`services/backend/src/backend_app/jobs/inbox_retention.py`) runs:

1. Promote `done` items past retention → soft-delete.
2. Hard-delete soft-deleted items past 30 days.
3. Wake snoozed items whose `snoozed_until ≤ now` — sets `status = unread`, emits `item_updated` SSE event.
4. Emit cleanup metrics + structured audit summary (count of rows touched per tenant) per audit-chain conventions (event kind: `inbox.retention_cleanup_run`).

Cleanup must be **idempotent** and **interruptible** — the job uses `FOR UPDATE SKIP LOCKED` patterns for the wake/promote loops to coexist with normal traffic.

---

## §6 Audit (per master §3.2)

Every state-changing operation writes an audit row through `packages/audit-chain`. Audit row shape (master schema): `(tenant_id, actor_user_id, action, target_kind, target_id, before_state, after_state, ts, request_id)`.

### 6.1 Action taxonomy

| Action                        | Trigger                                 | Notes                                                                                     |
| ----------------------------- | --------------------------------------- | ----------------------------------------------------------------------------------------- |
| `inbox.item_created`          | producer write succeeds                 | `actor_user_id` = service-token's `x-enterprise-user-id` (the originating agent's owner). |
| `inbox.item_status_changed`   | `PATCH /v1/inbox/{id}` mutates `status` | `before_state.status` / `after_state.status` captured.                                    |
| `inbox.item_labels_changed`   | `PATCH /v1/inbox/{id}` mutates `labels` | full label arrays in before/after.                                                        |
| `inbox.item_dismissed`        | `DELETE /v1/inbox/{id}`                 | soft-delete; audit retains `target_id` so admins can correlate.                           |
| `inbox.item_body_accessed`    | `GET /v1/inbox/{id}` returns body       | **compliance-grade**: every body read audited. Master §3.2 + compliance rules.            |
| `inbox.reply_sent`            | `POST /v1/inbox/{id}/reply` succeeds    | logs target `thread_id` (or "new-thread" + new id).                                       |
| `inbox.saved_search_created`  | `POST /v1/inbox/saved-searches`         |                                                                                           |
| `inbox.saved_search_deleted`  | `DELETE /v1/inbox/saved-searches/{id}`  |                                                                                           |
| `inbox.retention_cleanup_run` | cron daily                              | summary per-tenant counts.                                                                |

Audit rows are **append-only** (audit-chain enforces). Audit is exportable via the existing SIEM export path (`services/backend/src/backend_app/siem_export/`). Sub-PRD does not add a new export endpoint.

### 6.2 What is NOT audited

- List queries (`GET /v1/inbox` without `/{id}`). Auditing every list scrape would dwarf signal with noise. Compliance answers "who saw what" via the per-item body-access audit, which is precise.
- Unread-count polls.
- SSE connections themselves (audit the published item, not each fan-out).

---

## §7 Authorization

### 7.1 Visibility rules

- An `InboxItem` is visible only when:
  - `tenant_id` matches the verified bearer's tenant claim, **and**
  - `recipient_user_id` matches the verified bearer's user_id, **OR** the verified user has the `compliance_reader` role (tenant admins with audit-read scope).
- Compliance reads are themselves audited (`inbox.item_body_accessed` row carries `actor_user_id` = the admin, not the recipient).
- Cross-tenant access is impossible by the index; cross-user-same-tenant access is rejected with 403 at the route layer.

### 7.2 Mutation rules

| Action              | Required role                                                                                                                                                |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| PATCH status/labels | `recipient_user_id` only.                                                                                                                                    |
| DELETE (dismiss)    | `recipient_user_id` only.                                                                                                                                    |
| POST reply          | `recipient_user_id` only AND must have write access to `thread_id` (existing chat ACL — chats destination owns this check; the inbox route delegates to it). |
| Saved search CRUD   | Owning user only.                                                                                                                                            |

Admins **cannot** mutate another user's inbox (read-only compliance scope). This is the safer default; a future "admin force-dismiss for departed user" workflow is a separate audited operation (out of scope for phase 4).

### 7.3 Producer authorization (internal route)

`POST /internal/v1/inbox/items` is gated by:

1. **Service token** — the existing `ENTERPRISE_SERVICE_TOKEN` (root `CLAUDE.md` auth section). Without it, 401.
2. **Claim assertions** — caller MUST send `x-enterprise-org-id` and `x-enterprise-user-id` headers. Backend validates:
   - `org_id` matches `recipient_user_id`'s tenant — cross-tenant inserts rejected with 403.
   - `x-enterprise-user-id` identifies the **producer's owner** (the user the agent acts on behalf of); audit logs this as `actor_user_id`.
3. **Producer policy** — initial Wave 2 policy: any in-tenant agent (validated by the run context) can produce inbox items addressed to any in-tenant user. This is the same default the design's "Inbox rules" copy promises (auto-resolve agent↔agent when policy allows). See §16 Q5 for tightening — admin-level per-tenant blocklist for "do not auto-create inbox items for X" is the recommended phase 4.x follow-up.
4. **Recipient validation** — recipient_user_id must exist in tenant; if not, 404 (don't leak existence beyond tenant boundary).

### 7.4 Idempotency

`(producer_id, external_ref)` is unique. A retry from ai-backend (network flake) returns 200 with the existing row, not a duplicate. external_ref MUST be a stable derivation from the upstream event (e.g. `approval-{approval_id}` for approval-request kind, `error-{run_id}-{tool_invocation_id}` for tool errors).

---

## §8 Pagination + search (per master §3.5)

- **Cursor pagination.** `?after=<opaque-cursor>&limit=<n>`. Default `limit=50`, max `limit=200`. Cursor encodes `(created_at, id)` for stable scrolling under inserts.
- **Search.** `?q=<query>` runs PostgreSQL `plainto_tsquery('simple', q)` against the `subject + preview` GIN index. Debounced client-side at 250ms.
- **Filter.** Discrete `?filter[<axis>]=<value>` per §4.4. Multiple axes compose as AND. Repeated `?filter[kind]=mention&filter[kind]=error` is **disallowed** (allowlist enforces single value); the UI uses separate FilterTabs for those.
- **Sort.** `?sort=<field>:<asc|desc>` allowlisted per §4.4.
- **Combined query example:** `GET /v1/inbox?filter[status]=unread&filter[kind]=approval_request&q=press&sort=priority:desc&limit=50`

---

## §9 Accessibility (per master §3.6)

- **Filter tabs** — `role="tablist"` already present in current code. Arrow keys cycle between tabs (left/right; wrap at ends); Home/End jump to first/last; Enter activates. (`FilterTabs` shared primitive — once introduced by master §4.1, reused here; current InboxDestination's TabBar is replaced by it in implementation.)
- **List rows** — each row is one tab stop. Enter opens detail. Backspace/Delete prompts "Dismiss this item?" then dismisses. Per chat1.md:383 caution against shortcut-heavy design: **no J/K vim-style navigation by default**; the up/down arrow within the focused list works because it's discoverable.
- **Unread announcement** — each unread row announces "unread {kind} from {sender_display_name}" via `aria-label`. Read rows announce without the "unread" prefix.
- **Live region** — when on Inbox destination, a polite `aria-live` region announces new arrivals: "New {kind} from {sender}". Throttled to one announcement per 3 seconds to avoid spam.
- **Color not sole carrier** — unread state combines bold weight, accent left-bar, AND the leading text "unread" in the aria-label. High-priority rows have a flag icon AND red kindChip. Status dot is paired with text in the row metadata.
- **Focus ring** — design-system token; visible on all interactive elements.
- **Reduced motion** — SSE arrival pulse animation respects `prefers-reduced-motion: reduce` (fade only, no slide).
- **High contrast** — design-system high-contrast theme tested as part of axe-core CI run.
- **Screen-reader transcripts** — body content already renders as markdown; ensure the markdown primitive emits semantic HTML (`<h>`, `<p>`, `<ul>`, etc.) not a flat blob.

---

## §10 Performance (per master §3.7)

- **LCP < 2.5s** — the list endpoint returns a 50-row first page with denormalized sender names + previews (no body bytes); shell + ContextPanel are static. No waterfall.
- **INP < 200ms** — filter tab clicks operate on the already-fetched first page (counts come from `counts` map per master pattern) and trigger a debounced refetch only if the filter narrows beyond what's loaded. Mark-read / mark-done are optimistic with rollback on error (existing pattern in `InboxDestination.tsx`).
- **Virtualization** — `@tanstack/react-virtual` once item count > 100. Average row height 80px; over-scan 5 rows.
- **Body lazy fetch** — only on detail mount. Body size cap 64KB (DB constraint); transfers compress well.
- **SSE keepalive** — server sends a `:keepalive` comment line every 25s to keep proxies happy. Client tolerates up to 60s silence before reconnect.
- **Unread-count caching** — edge-cached 5s; client invalidates locally on SSE delta to avoid the 5s lag.
- **Network budget** — destination's initial round-trip: 1 GET `/v1/inbox` + 1 GET `/v1/inbox/unread_count` (the badge is already in the shell's KV state so this is often skipped on revisits) + 1 SSE upgrade. Total ≤ 2 HTTP round-trips on a cold open.
- **Shell render isolation** — navigating to Inbox does not re-mount `ChatShell`; only the destination component remounts (per PRD foundation rule).

---

## §11 Telemetry (per master §3.8)

OpenTelemetry spans (no PII content — only ids and enum values):

```
destination=inbox
  action=open                             // entering /inbox
  action=open_item                        // route gains an id
  action=filter_change       value=<slug>
  action=search              q_len=<n>
  action=mark_read           item_kind=<kind>
  action=mark_done           item_kind=<kind>
  action=mark_snoozed        item_kind=<kind>  snooze_minutes=<n>
  action=dismiss             item_kind=<kind>
  action=reply               routed_to=<existing-thread|new-thread>
  action=saved_search_create
  action=saved_search_run
  action=sse_reconnect       after_sequence=<n>
  action=sse_failover_to_poll
```

Span attributes include `tenant_id`, `user_id` (hashed), `destination`, `action`. **Never** include subject, preview, body bytes, or sender display name.

Backend emits structured logs with `request_id` correlation; errors include `tenant_id`, route, error code (never the user's data).

---

## §12 States (per master §3.10)

| State                         | Renders                                                                                                                                              |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Loading**                   | Skeleton: PageHeader visible, FilterTabs visible (no counts), 5 skeleton rows (existing implementation — keep).                                      |
| **Empty (all filter)**        | `<EmptyState icon="inbox" title="Inbox zero" sub="No new mentions, approvals, or errors." />` — true zero state.                                     |
| **Empty (per filter)**        | filter-specific copy: mentions → "No mentions"; approvals → "No pending approvals"; errors → "No errors — clear sky"; done → "Nothing finished yet." |
| **Filter-empty + search**     | "No items match \"{q}\" in {filter}." with a "Clear filters" button.                                                                                 |
| **Error (load)**              | `<ErrorPanel message retry />` (existing).                                                                                                           |
| **Error (per-row)**           | inline error under the row (existing). On retry success, error clears.                                                                               |
| **Saving (status mutation)**  | optimistic UI; spinner replaces button label until 200; rollback on error with toast.                                                                |
| **Offline**                   | banner: "You're offline — showing cached items. New items will appear when you reconnect." Reads from `KeyValueStore` cache.                         |
| **Stale**                     | if last-fetch > 5 min and SSE disconnected: top hint "Items may be out of date. Refresh." with refresh button.                                       |
| **Snoozed section collapsed** | filter=snoozed renders the list grouped by `snoozed_until` bucket (Today, Tomorrow, Later); collapsed by default.                                    |

---

## §13 Cross-destination references (per master §3.11)

Inbox cross-references (typed, via `<ItemLink>` registry per master §4.3):

| Field             | Target destination                                        | UI affordance                                |
| ----------------- | --------------------------------------------------------- | -------------------------------------------- |
| `thread_id`       | chats — `/chats/{thread_id}`                              | "Open thread" button in row + detail.        |
| `run_id`          | ai-backend run record — surfaced through chats's run pill | shown inline in detail metadata.             |
| `approval_id`     | embedded `<ApprovalCard>` (PR-4.4.6.2 primitive)          | inline in detail when present.               |
| `sender.agent_id` | agents — `/agents/{agent_id}`                             | clickable agent name in row + detail header. |
| `sender.user_id`  | team — `/team/{user_id}`                                  | clickable user name.                         |
| `project_id`      | projects — `/projects/{project_id}`                       | project chip in detail.                      |

### 13.1 Cascade rules

| Origin deletion                | Inbox effect                                                                                                                                                                               |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| thread deleted (chat)          | Inbox item retains `thread_id`. UI renders the "Open thread" link as disabled with tooltip "Thread deleted". Body remains.                                                                 |
| run record purged (ai-backend) | Inbox item retains `run_id` as a dead reference; detail shows "run no longer available".                                                                                                   |
| approval resolved              | producer PATCHes inbox item to `status=done` via internal route. UI auto-updates via SSE.                                                                                                  |
| approval revoked               | producer PATCHes to `done` + adds label `revoked`.                                                                                                                                         |
| agent deleted                  | `sender.agent_id` remains for audit; UI shows "deleted agent" in place of the name. Future inbox items from that agent are not possible (agent is gone).                                   |
| project deleted                | `project_id` becomes a dead reference; UI shows "deleted project" pill. Items are NOT moved.                                                                                               |
| user deleted (offboarded)      | Items addressed to the offboarded user remain for the master §3.3 retention window (90d) then hard-delete. During the window: only admins with compliance scope can read (the user can't). |
| tenant deleted                 | Hard delete cascade (master rule).                                                                                                                                                         |

---

## §14 Desktop substrate caveats (per master §3.12)

- **NotificationPort** — fires on `item_created` with `priority=high`. Desktop implements via Electron `Notification` (web: no-op). Port introduced in Wave 2 desktop wiring; Inbox is one of the consumers (Todos badge is the other). Notification text: `{sender_display_name}: {subject}` — body excluded for privacy.
- **BadgePort** — rail badge count + (desktop) dock icon badge. Single source: `GET /v1/inbox/unread_count` + SSE deltas.
- **SSE keepalive across sleep/wake** — desktop main process owns the transport-keepalive pattern. On wake, the desktop main process reconnects all SSE channels including `/v1/inbox/stream` with the highest `sequence_no` seen; backend replays.
- **No direct browser API access from `InboxDestination`** — the destination is substrate-agnostic. Native notifications and dock badges flow through ports; the destination calls `notificationPort.fire({ title, body })` not `new Notification(...)`.
- **Deep-link routing** — desktop registers `atlas://inbox/<id>` as a URL handler. Frontend `HashRouter` and desktop main process resolve to the same `route.id` shape.

---

## §15 Implementation phasing

Per master §7, this destination uses the standard 3-agent pattern. Branches off `main`, each in its own worktree.

### 15.1 Agent boundaries

**Impl-A — api-types + backend + facade + audit + producer-server-side**

Owns:

- `packages/api-types/src/inbox.ts` (NEW) + re-export from `index.ts`
- `services/backend/src/backend_app/inbox/` (NEW): `routes.py`, `service.py`, `store.py`, `schema.py`, `events.py` (SSE bus)
- Alembic migration: `inbox_items`, `inbox_bodies`, `inbox_saved_searches` + indexes
- `services/backend/src/backend_app/jobs/inbox_retention.py` (NEW) — daily cron
- Audit hooks (audit-chain integration per §6)
- `services/backend-facade/src/backend_facade/inbox_routes.py` (NEW): proxy + SSE pass-through
- Internal endpoint `POST /internal/v1/inbox/items` + tests
- Unit + integration tests: tenant isolation, recipient-only visibility, producer auth, audit row presence, retention cleanup, SSE delivery + reconnect.

**Impl-B — frontend**

Owns:

- `packages/chat-surface/src/destinations/inbox/InboxDestination.tsx` (EXTEND): wire to `/v1/inbox`, add saved searches, replace TabBar with shared `FilterTabs`, virtualize list, add filter axes.
- `packages/chat-surface/src/destinations/inbox/InboxPanel.tsx` (NEW)
- `packages/chat-surface/src/destinations/inbox/InboxDetail.tsx` (NEW) — including originating-thread preview + inline reply.
- `packages/chat-surface/src/destinations/inbox/index.ts` — register `<ItemLink kind="inbox-msg">` resolver.
- `apps/frontend/src/api/inbox.ts` (NEW) — HTTP wrappers + SSE subscription hook.
- `apps/frontend/src/app/HashRouter.ts` — extend `AppRoute` to encode `/inbox/<id>` (per PRD §4.5 routing convention `/<dest>/<view?>/<id?>`).
- Mount `<InboxPanel>` and `<InboxDestination>` from `App.tsx` via `ChatShell.contextPanel` + body slots.
- Tests: filter combinations, mark-read/done/snooze flows, reply routing, SSE reconnect behaviour, axe-core on InboxDestination + InboxPanel + InboxDetail.

**Impl-C — ai-backend producer + desktop ports**

Owns:

- `services/ai-backend/src/agent_runtime/api/inbox_producer.py` (NEW) — the routing rule from §1.3 (when to write a durable Inbox item vs. only emit ephemeral SSE).
- Wiring from runtime events (`approval_requested`, `tool_error`, `mention`) into the producer.
- `packages/chat-surface/src/ports/NotificationPort.ts` interface (if not introduced elsewhere by Wave 2; coordinate with desktop phase).
- Web no-op implementation; desktop implementation deferred to desktop wave.
- Tests: producer respects routing rule; tier-1 SSE still fires; tier-2 durable item only when fallback condition met; idempotency on `(producer_id, external_ref)`.

### 15.2 Merge order

1. Impl-A → review → merge to main. **Must land first** — backend route + types are the contract everyone else consumes.
2. Impl-B (depends on Impl-A's contracts) → review → merge to main.
3. Impl-C (depends on producer payload from Impl-A) → review → merge to main.

Each PR runs the full test matrix (backend, ai-backend, facade, chat-surface, frontend, axe-core, retention-cron e2e).

### 15.3 Acceptance criteria (gate to closing phase 4)

- ✅ Every endpoint in §4.2 + §4.3 implemented and tested.
- ✅ Audit rows emitted for every action in §6.1; verified in audit-chain export.
- ✅ Tenant + recipient isolation tests pass (cross-tenant: 403; cross-user: 403).
- ✅ Retention cron promotes done→soft-deleted→hard-deleted across mocked time; audit cleanup row written.
- ✅ axe-core green on InboxDestination + InboxPanel + InboxDetail.
- ✅ SSE reconnect resumes from `?after_sequence=N` without dropping events.
- ✅ Frontend type-check + chat-surface tests + backend tests green; no `any` introduced in `inbox.ts`.

---

## §16 Open questions for product (parth)

These need a call before the relevant Impl agent codes the affected branch.

1. **Snooze semantics.** Proposed default UX:
   - Quick presets: "Tomorrow 9am" (user's local tz), "Next week (Mon 9am)", "Pick time…" (date picker).
   - Snoozed-until date saved as UTC; rendered in user's tz.
   - On wake (`snoozed_until ≤ now`): item transitions back to `unread`, emits `item_updated` SSE, optionally fires NotificationPort if `priority=high`.
     Confirm presets + behaviour.

2. **Bulk actions.** Proposed: mark-all-read within current filter; bulk dismiss within filter; bulk-label apply. NOT supported: bulk reply, bulk snooze, bulk done (each has nuance that doesn't bulk safely). Endpoint: `POST /v1/inbox/bulk-action` with `{ action, filter_payload }`. **Or** skip bulk in phase 4 and ship in 4.x? Need a call.

3. **Threading of inbox replies.** If a user replies inline to inbox item A and then sender@-mentions them again, do we:
   - (a) create inbox item B independent of A, or
   - (b) thread B under A as a follow-up?
     Recommend **(a) — flat** for phase 4. The originating chat thread is the natural conversation grouping; Inbox is a pull list, not a conversation surface.

4. **Notification preferences.** Three nested axes:
   - **Global** — "All new high-priority items" / "Approvals only" / "Off".
   - **Per-kind** — separate toggles for mention / approval_request / error / system.
   - **Per-sender** — mute a specific noisy agent or system origin.
     Settings location: Settings → Notifications (new section under §pr-4.x). Phase-4 Inbox stores preferences; Settings UI in a follow-up. **OK to ship Inbox with hard-coded "high-priority only" defaults and wire prefs later?**

5. **Per-tenant "do not auto-create inbox items for X" policy.** Admin-configurable blocklist (block specific agents, kinds, or origin combos from creating inbox items). Recommend yes, in `tenants.inbox_producer_policy` JSON column, enforced at the internal producer endpoint. Confirm.

6. **Inbox vs in-surface approval — the routing rule.** Proposed in §1.3:

   > Inline by default; durable inbox item only if user has not viewed the thread within 5 minutes **and** `priority=high`.
   > Variables to confirm:
   - `INBOX_FALLBACK_INACTIVITY_MS` default (5 min? 10 min?).
   - Per-tenant override?
   - Should `priority=med` errors also fall back to Inbox after a longer interval?

7. **Reply-to-error inbox routing.** When the user replies to an `error` kind item (e.g., "GitHub MCP token expired"), the reply routes to whom?
   - (a) The agent that owned the failed run (it's paused, but can read the reply on resume).
   - (b) The tenant admin / connector owner.
   - (c) Open a connectors-destination repair flow (Re-authorize button is the primary; reply is comment-only).
     Recommend **(c)** for connector_error specifically — primary CTA is "Re-authorize", reply is a comment that pages the connector owner if non-default. Confirm.

8. **Inbox detail in narrow viewports.** Desktop and wide web: list + detail side-by-side; narrow web (mobile-leaning): list-only at `/inbox`, navigates to `/inbox/<id>` for full-screen detail. Confirm breakpoint (suggest: ≤ 960px collapses to single-pane).

---

## §17 Test plan

### 17.1 Backend / facade unit + integration

- **Tenant isolation**: insert items in tenants A and B; user in tenant A sees only A's. Cross-tenant GET → 404 (not 403, to avoid existence leak); cross-tenant PATCH/DELETE → 404.
- **Recipient-only visibility**: insert items for users U1, U2 in same tenant; U1 lists → only U1's items. U1 GETs U2's item by id → 404.
- **Admin compliance read**: a tenant admin with `compliance_reader` role can GET another user's item; audit row written with `actor_user_id = admin`.
- **Producer auth**:
  - No service token → 401.
  - Service token, no `x-enterprise-user-id` → 400.
  - Cross-tenant insert (org_id ≠ recipient's tenant) → 403.
  - Recipient not in tenant → 404.
  - Valid call → 201 + audit row.
- **Idempotency**: 2× POST `/internal/v1/inbox/items` with same `(producer_id, external_ref)` → second returns 200 + existing id, only one DB row, only one audit row.
- **Status mutations**: PATCH with each valid `status`. Invalid transitions (e.g., `done → unread` without explicit "mark unread") → 422.
- **Snooze**: PATCH to snoozed with `snoozed_until > now` → row updated. With `snoozed_until <= now` → 422. Wake cron transitions back to unread with audit event.
- **Search**: `?q=urgent` returns items whose subject OR preview contains "urgent". GIN index used (verify EXPLAIN plan).
- **Filter combinations**: every pairwise combination of `filter[status]`, `filter[kind]`, `filter[sender_kind]`, `filter[project_id]`, `q`. Result set matches a Python-side reference filter.
- **Cursor pagination**: insert 75 items, page through with `limit=20`; pages don't repeat or skip under concurrent inserts (cursor is `(created_at, id)`).
- **Audit immutability**: attempt to UPDATE an audit row directly → audit-chain raises.
- **Retention cleanup**: insert items at varying `done`-timestamps; run cron under mocked clock; verify promotions + hard-deletes; audit summary row written. Verify "FOR UPDATE SKIP LOCKED" doesn't deadlock with concurrent normal traffic.
- **SSE delivery**: subscribe; POST a new item; client receives `item_created` envelope with `sequence_no=N`. Reconnect with `?after_sequence=N-1` → server replays the envelope. Disconnect during publish → next reconnect catches up.
- **SSE tenant ACL**: subscriber for user U1 never receives U2's events even if same tenant.
- **Body access audit**: every successful `GET /v1/inbox/{id}` writes one `inbox.item_body_accessed` row.

### 17.2 Frontend unit + integration

- **Tab arrow-key navigation** (axe + RTL).
- **Mark-read optimistic + rollback**.
- **Snooze flow** with mocked clock (Today, Tomorrow, custom).
- **Detail body lazy-fetched** only on detail mount.
- **Reply routing** — with thread_id → POSTs to existing thread endpoint; without → creates new thread.
- **SSE reconnect** — disconnect, push 3 server events, reconnect → all 3 deltas applied; rail badge correct.
- **Empty states per filter** render correct copy.
- **axe-core**: zero violations on InboxDestination + InboxPanel + InboxDetail in default + high-contrast themes.

### 17.3 Producer (ai-backend) unit

- Approval requested with user not in thread + `priority=high` → durable item created (verified via mocked backend HTTP).
- Approval requested with user actively in thread → no durable item; only SSE pulse.
- Tool error in scheduled run → durable item created with `kind=error`.
- Idempotency: same upstream event delivered twice → producer sends same `external_ref` → backend dedup → one item.

### 17.4 End-to-end smoke

`docs/dev-testing.md` recipe addition:

```bash
export TOKEN=$(make dev-bearer)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8200/v1/inbox
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8200/v1/inbox/unread_count
curl -N -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8200/v1/inbox/stream
# in another terminal: trigger an inbox-producing run via the conversation API.
```

---

## §18 Anti-goals

Restated from §1.2 + master §9, codified as testable invariants:

- ❌ **NOT an email client.** No "compose to <user@arbitrary-domain>" endpoint. The reply composer only routes to existing threads or creates new threads within the workspace.
- ❌ **NOT a Slack channel.** No continuous-feed semantics. Items are discrete with a terminal state. No "channel subscription" surface.
- ❌ **NOT a global activity feed.** A row in the Home destination's activity feed is never in Inbox unless it explicitly requires this user's action. Verified: producer routing rule (§1.3) requires either `kind ∈ {mention, approval_request, error, system}` AND a recipient_user_id.
- ❌ **No third-party-source inbox items.** Wave 2 does not pull Gmail/Outlook/LinkedIn into Inbox. Connectors-as-source is a separate concern handled in the connectors destination.
- ❌ **NOT a chat surface.** Replies route to the originating thread. The Inbox is read+act, not a conversation continuation.
- ❌ **No throwaway code.** Every endpoint, type, table, and audit hook is enterprise-grade from day one (per master §9).
- ❌ **No frontend-only filtering of items.** All filter axes (§4.4) are server-validated and allowlisted.
- ❌ **No PII in telemetry.** Verified by §11 — span attributes carry only ids and enum values.
- ❌ **No direct browser API access.** Native notifications + dock badges flow through ports.
- ❌ **No keyboard-shortcut-heavy UX.** Discoverable on-screen affordances; arrow keys + Enter + Backspace only. No J/K vim chords.

---

## §19 References

- [PRD.md](../PRD.md) — workspace shell + composer + thread canvas (the foundation).
- [destinations-master-prd.md](../destinations-master-prd.md) — master destinations PRD; §3 (enterprise checklist), §4 (shared primitives), §5.2 (Inbox brief), §7 (dispatch pattern).
- `/tmp/atlas-design/enterprise-search-template/project/dest-inbox.jsx` — main view + panel reference.
- `/tmp/atlas-design/enterprise-search-template/project/os-data.jsx` — `MOCK_INBOX` shape (lines 141-271).
- `/tmp/atlas-design/enterprise-search-template/project/os-app.jsx` — InboxMain mount (line 149); badges (line 111-116); breadcrumb (lines 84-108).
- `/tmp/atlas-design/enterprise-search-template/chats/chat1.md` — inline-approval vs Inbox decision (lines 295-322, 309-316); shortcut caution (line 383).
- `packages/chat-surface/src/destinations/inbox/InboxDestination.tsx` — existing implementation (skeleton + 4 filter tabs + optimistic mark-read).
- `packages/api-types/src/index.ts` lines 1424-1459 — existing `AssignedApproval` + `InboxEventEnvelope` types (PR-1.4.1 — the ephemeral SSE channel that complements the durable inbox introduced here).
- `services/ai-backend/src/runtime_api/sse/inbox_bus.py` — existing per-user inbox event bus (the tier-1 pulse channel).
- `services/ai-backend/src/runtime_api/schemas/inbox.py` — wire schema for the ephemeral SSE channel.
- [`services/backend/CLAUDE.md`](../../../services/backend/CLAUDE.md) — product persistence rules + audit conventions.
- [`services/backend-facade/CLAUDE.md`](../../../services/backend-facade/CLAUDE.md) — facade proxy rules; apps call facade only.
- [`packages/api-types/CLAUDE.md`](../../../packages/api-types/CLAUDE.md) — contract stewardship; breaking-change rules.
- [`services/ai-backend/CLAUDE.md`](../../../services/ai-backend/CLAUDE.md) — runtime rules; producer code lives here, audit lives in backend.
- Root [`CLAUDE.md`](../../../CLAUDE.md) — compliance section (audit immutability, retention scope, tenant isolation).
