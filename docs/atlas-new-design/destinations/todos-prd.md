# Atlas Workspace — Todos Destination (Phase 3 sub-PRD)

**Status:** draft (2026-05-17)
**Owner:** parth (orchestrator) — implementation delegated to Phase 3 impl agents
**Master:** [destinations-master-prd.md](../destinations-master-prd.md) §5.3
**Shell PRD:** [PRD.md](../PRD.md)
**Design source of truth:** Claude Design handoff bundle at `/tmp/atlas-design/0x-copilot-template/` — `project/todos.jsx` (lines 1-379) is the load-bearing reference; `project/projects-todos.css` is the visual intent (we consume `packages/design-system` tokens, not literal CSS); `project/dest-misc.jsx:184-220` is the `TodosMain` mount; `project/data.jsx:118-200` is the mock data shape.

---

## 1. Premise + user job

### 1.1 What a "todo" is in Atlas (and what it is NOT)

A todo is **a single action item with provenance** — either typed by the user (manual queue) or extracted by Atlas from a chat / run and accepted by the user (proposed-then-committed queue).

It is **not** a generic task manager. Atlas does not compete with Asana / Linear / Things / Todoist. The destination exists for one product reason: the cross-surface, agent-driven nature of Atlas means most of what the user has to do next is _stated inside a chat_ ("Legal still reviewing the headline claim — owner: @sarah, due Fri" — `data.jsx:127`). If that line lives only in the transcript, the user must remember to scroll back. The destination surfaces those items in a single dueable, dismissable list — **with a link back to where they came from**.

### 1.2 User's success state

Opening the destination, the user sees within ~1 second, in priority order: **overdue** (red, top), **due today** (warn-tone), the **extraction banner** ("Atlas found 3 possible todos from your last chat"), then **this week** / **later** / **recently completed**. Every item is one click from done; every item with a chat / agent source is one click from the originating thread or run.

### 1.3 What this destination is for (one sentence)

> The provenance-bearing action queue that lets the user act on agent work without re-reading every transcript.

---

## 2. Source-of-truth map

This destination's canonical files. Anything not listed is an alias or a consumer.

| Concern                             | Canonical file                                                                     | Status                                                       |
| ----------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Public wire types                   | `packages/api-types/src/todos.ts`                                                  | **NEW** — does not exist yet                                 |
| Main view                           | `packages/chat-surface/src/destinations/todos/TodosDestination.tsx`                | EXISTS (scaffold, must be rewritten this phase)              |
| Context panel                       | `packages/chat-surface/src/destinations/todos/TodosPanel.tsx`                      | **NEW**                                                      |
| Destination barrel                  | `packages/chat-surface/src/destinations/todos/index.ts`                            | EXISTS — re-export panel + main                              |
| Backend route module                | `services/backend/src/backend_app/todos/`                                          | **NEW** — `routes.py`, `service.py`, `store.py`, `models.py` |
| Backend migrations                  | `services/backend/migrations/0032_todos.sql` + `.rollback.sql`                     | **NEW**                                                      |
| Backend migrations (extractions)    | `services/backend/migrations/0033_todo_extractions.sql` + `.rollback.sql`          | **NEW**                                                      |
| Facade proxy                        | `services/backend-facade/src/backend_facade/todos_routes.py`                       | **NEW**                                                      |
| Auto-extraction worker job          | `services/ai-backend/src/runtime_worker/jobs/todo_extractor.py`                    | **NEW** — see §2.1                                           |
| Extraction → backend post hook      | `services/ai-backend/src/agent_runtime/observability/todo_extraction_publisher.py` | **NEW** — see §2.1                                           |
| Badge port (destination icon badge) | `packages/chat-surface/src/ports/BadgePort.ts`                                     | **NEW** — see §14                                            |

### 2.1 Where auto-extraction lives (and why)

**Decision: extraction proposal lives in `ai-backend`** as a post-run deferred worker job; the committed todo always lives in `backend`.

Rationale (DRY + ONE source of truth):

- `ai-backend` already owns the run transcript, the event stream, the model client, and a worker process with claim-based job dispatch (`runtime_worker/jobs/*` — `retention_sweeper.py` is the closest precedent). Re-fetching transcripts from `backend` would duplicate the data path and reverse the service direction (today only `ai-backend → backend` exists).
- Product persistence belongs in `backend` per master §2.3. So extraction runs in `ai-backend` but emits the proposal to `backend` via the existing service-token internal path (which writes to `todo_extractions`). User accept/reject hits the public surface; on accept, `backend` atomically inserts `todos` and marks the extraction `accepted`.
- This keeps `backend` free of model-invocation code (no provider key) and `ai-backend` free of product-state ownership.

The publisher hooks `agent_runtime/observability/` (mirrors the existing `audit_publisher` style) and fires once per terminal run event. The worker job is the deferred path — the LLM extraction call is off the request handler.

---

## 3. Architecture (what the screen looks like + how the parts fit)

### 3.1 Component tree

```
TodosDestination (main)
├── ExtractionBanner          (top of Today, only when pending extractions exist)
├── ExtractionPreviewSheet    (modal/overlay; opens on banner click)
├── SectionList               (virtualised; one section per group)
│   ├── Section "Today"
│   │   ├── InlineAdd         (text input + Enter → optimistic insert)
│   │   └── TodoRow[]         (checkbox, text, due, priority chip, source chip)
│   ├── Section "Overdue"
│   ├── Section "This week"
│   ├── Section "Later"
│   └── Section "Done"        (collapsed by default; paginated infinite scroll)
└── BulkActionBar             (sticky, appears when ≥1 row is shift-selected)

TodosPanel (context panel — `<ChatShell contextPanel>`)
├── PageHeader                "Todos · N open"
├── FilterChipGroup "Priority" [low · med · high]
├── FilterChipGroup "Project"  [all · <project>... · Unfiled]
├── FilterChipGroup "Source"   [all · user · chat · agent]
├── InlineAdd                  (mirrors the section InlineAdd but always-visible)
└── EmptyStateHint             (when filters yield no rows)
```

The main is a single column maxing at 920px (mirrors `projects-todos.css` line 227 `max-width: 920px`). It is **not** the destination's job to be the project's right sidecar — that's `TodosWorkspaceTab` from `todos.jsx:312-377`, which is a different surface and belongs in the chat thread canvas (Wave 5 / Phase 1 thread canvas).

### 3.2 Sections (the user's mental model)

The five sections, in render order, with the exact bucketing rule:

| Section       | Bucket rule                                                                          | Tone    |
| ------------- | ------------------------------------------------------------------------------------ | ------- |
| **Today**     | `!done && due ∈ [start_of_today, end_of_today]` (user TZ)                            | accent  |
| **Overdue**   | `!done && due < start_of_today` — rendered ABOVE Today so the user can't miss it     | danger  |
| **This week** | `!done && due ∈ (end_of_today, end_of_week]`                                         | neutral |
| **Later**     | `!done && (due IS NULL OR due > end_of_week)`                                        | neutral |
| **Done**      | `done && completed_at >= now - 14d` (older done items reachable via "Show all done") | muted   |

A section with zero rows **does not render** (matches `todos.jsx:135` `if (items.length === 0) return null`). The single exception: if **all** sections are empty, the main renders an `EmptyState` ("Nothing here yet. Atlas extracts followups whenever it finds an action item — try the launch demo, or add one above." — `todos.jsx:159`).

### 3.3 Row anatomy

Per `todos.jsx:167-228` + master §3.6 + §4.2:

- `<TodoRow>` is a single tab stop. Tab moves between rows; arrow keys move within a section's rows (a11y per §9).
- Checkbox (left) — accent fill when done; danger-toned border when `priority === "high"`.
- Text (centre, flex 1) — single line; click expands the row to show full text + excerpt (when `source.excerpt` exists).
- Meta row (under text) — chips in this order: `auto` (when extracted) · priority (only if `high`) · due · project · source.
- Actions (right, opacity 0 → 1 on hover) — delete (X). Bulk-mode replaces this with a checkbox.
- `data-done`, `data-priority`, `data-completed` attributes drive styling and assertions.

### 3.4 Inline add

Two places — both produce the same `POST /v1/todos` call:

- **At top of every section's body** (per `todos.jsx:123-131` — there's one above all sections; we put one **per section** so adding to Later doesn't visually jump). Pressing **Enter** submits; Esc clears; clicking elsewhere clears.
- **Panel** — the always-visible "New todo" line. The panel's add defaults to the current filter context (e.g., if filtered by `project=p-1`, the new todo's `project_id=p-1`).

Optimistic UI: the row appears instantly with `data-pending="true"` (skeleton-toned border, no actions). On backend reject, the row remains with a row-level error message + retry. On success, the row swaps to its real id + activates.

### 3.5 Drag-reorder (Wave 2 — yes)

Two distinct gestures with two distinct semantics:

- **Drag within a section** → reorder. Changes the per-user `sort_index` of the dragged todo within that section. Open question §16 — we propose **same gesture, server resolves the destination semantics by which section row it landed on**.
- **Drag across sections** → re-categorise. Changes `due` (Today / This week / Later) or marks done (drop on Done). Overdue is computed, not stored — dragging into Overdue from below clamps to `due = yesterday`.

Implementation: **HTML5 native DnD via the existing browser API** — no library. Atlas's UX is not "vim-style chords" (PRD §14) but it also isn't `react-dnd`-heavy. A 60-line custom hook in `chat-surface/src/util/dnd.ts` (shared with Inbox phase 4 if it needs it). RAF-throttled (master §3.7 "render-count assertions").

Keyboard equivalent: `Space` picks up a row; `↑/↓` moves; `Enter` drops; `Esc` cancels. Mirrors `todos.jsx` plus a11y rules (§9). This is non-optional because the design is **not** keyboard-shortcut-heavy but drag without keyboard equivalent fails WCAG.

### 3.6 Bulk select (Wave 2 — yes)

- **Shift-click** a row's text to enter bulk mode and select a range; **Cmd/Ctrl-click** to toggle individual rows.
- A sticky `<BulkActionBar>` appears at the bottom: `N selected · Mark done · Change priority · Move to project · Delete · Done`.
- Bulk actions are a single backend request: `POST /v1/todos/bulk { action: "mark_done" | "set_priority" | "set_project" | "delete", ids: [...], payload: {...} }`. The backend executes them in a single transaction (atomic) and emits **one audit row per affected todo**, with a shared `correlation_id` (see §6).

### 3.7 Auto-extraction proposal flow

1. **Run completes in ai-backend** (any run with ≥1 final-response model_delta event).
2. **Worker job `todo_extractor` claims it** (off-thread, mirroring `runtime_worker/jobs/retention_sweeper.py`). Reads transcript, calls the model with a fixed system prompt ("extract concrete action items where the actor is the user or a teammate; return JSON list of {text, priority, due?}"), yields 0..N candidates.
3. **Publisher POSTs to `backend`** at `/internal/v1/todos/extractions` (service-token; `x-enterprise-org-id` + `x-enterprise-user-id` from the run). `backend` inserts one `todo_extractions` row with `status="pending"`.
4. **Frontend polls `/v1/todos/extractions?status=pending`** on destination load and every 60s while open. No SSE this phase; Wave 4 can introduce a workspace event stream.
5. **Banner appears** at top of Today: "Atlas found 3 possible todos from your last chat — review & add". Click → `<ExtractionPreviewSheet>` with per-candidate checkboxes (default checked), source chat title, and excerpt.
6. **Accept** → `POST /v1/todos/extractions/<id>/accept { accepted_indices }` → atomic backend transaction inserts N `todos` with `source={kind:"chat"|"agent",...}`, marks extraction `accepted`, writes one audit row per inserted todo + one for the extraction itself.
7. **Reject** → `POST .../reject` → status `rejected`; never re-proposed.

The user can also **dismiss** (view-local — extraction stays `pending`) and **snooze** ("remind me tomorrow") — snooze hides the banner; the extraction persists with `snoozed_until`.

---

## 4. Wire contracts

Canonical types in `packages/api-types/src/todos.ts`. The existing `Todo` shape in `TodosDestination.tsx` (lines 17-24) is a frontend-only scaffold and **gets deleted in implementation**.

### 4.1 Core types

```typescript
// packages/api-types/src/todos.ts

export type TodoId = string & { readonly __brand: "TodoId" };
export type TodoExtractionId = string & {
  readonly __brand: "TodoExtractionId";
};

export type TodoPriority = "low" | "med" | "high";

export type TodoSource =
  | { readonly kind: "user" }
  | {
      readonly kind: "chat";
      readonly thread_id: string;
      readonly excerpt?: string;
    }
  | {
      readonly kind: "agent";
      readonly agent_id: string;
      readonly run_id?: string;
      readonly excerpt?: string;
    };

export interface Todo {
  readonly id: TodoId;
  readonly tenant_id: string;
  readonly owner_user_id: string;
  readonly text: string;
  readonly done: boolean;
  readonly completed_at?: string; // ISO; set iff done flipped true
  readonly due?: string; // ISO date (no time component); user-tz interpreted server-side
  readonly priority: TodoPriority;
  readonly source: TodoSource;
  readonly project_id?: string;
  readonly labels: ReadonlyArray<string>;
  readonly sort_index: number; // float between bucket neighbours; server-managed
  readonly created_at: string;
  readonly updated_at: string;
}

export interface TodoExtraction {
  readonly id: TodoExtractionId;
  readonly tenant_id: string;
  readonly owner_user_id: string;
  readonly source: { readonly thread_id: string; readonly run_id: string };
  readonly proposed_todos: ReadonlyArray<{
    readonly text: string;
    readonly priority: TodoPriority;
    readonly due?: string;
    readonly excerpt?: string;
  }>;
  readonly status: "pending" | "accepted" | "rejected" | "snoozed";
  readonly snoozed_until?: string;
  readonly created_at: string;
}
```

### 4.2 List + filter contract

```
GET /v1/todos
  ?filter[done]=true|false
  &filter[priority]=low|med|high           (repeatable)
  &filter[project_id]=<id>|unfiled         (repeatable; "unfiled" matches NULL)
  &filter[source]=user|chat|agent          (repeatable)
  &q=<full-text-query>
  &sort=due:asc|priority:desc|created_at:desc   (allowlisted per master §3.5)
  &after=<opaque cursor>
  &limit=<int 1..200, default 50>

→ 200 { items: Todo[], next_cursor?: string, total?: number }
```

`total` is included only when no filter narrows the result (so the panel can show "N open" without an extra call). Cursor is opaque (base64 of `(sort_field, id)` tuple — DON'T leak the implementation).

### 4.3 Mutations

```
POST   /v1/todos               { text, priority?, due?, project_id?, source? }   → 201 Todo
PATCH  /v1/todos/<id>          { text?, done?, priority?, due?, labels?, project_id?, sort_index? }  → 200 Todo
DELETE /v1/todos/<id>                                                            → 204
POST   /v1/todos/bulk          { action, ids, payload? }                         → 200 { affected: number, correlation_id: string }

GET    /v1/todos/extractions?status=pending&after=&limit=                        → 200 { items: TodoExtraction[], next_cursor? }
POST   /v1/todos/extractions/<id>/accept   { accepted_indices: number[] }        → 200 { todos: Todo[] }
POST   /v1/todos/extractions/<id>/reject                                          → 200 { id, status: "rejected" }
POST   /v1/todos/extractions/<id>/snooze   { until: string }                     → 200 { id, status: "snoozed", snoozed_until }
```

POST creates a `source={kind:"user"}` todo unless the caller is the internal extraction-accept path (which sets `kind:"chat"|"agent"`). The public `POST /v1/todos` **rejects** non-user `source` — that path is reserved for the extractor.

### 4.4 Internal contract (ai-backend → backend)

```
POST /internal/v1/todos/extractions     (service-token; x-enterprise-org-id; x-enterprise-user-id required)
  { source: { thread_id, run_id }, proposed_todos: [...] }
  → 201 TodoExtraction
```

This is on `backend`'s internal surface (not exposed via facade — master §2.3). Only `ai-backend` calls it.

---

## 5. Storage + retention

### 5.1 Postgres schema

Migration `0032_todos.sql`:

```sql
CREATE TABLE todos (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  owner_user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  text            TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 2000),
  done            BOOLEAN NOT NULL DEFAULT FALSE,
  completed_at    TIMESTAMPTZ,                         -- set on done=true flip
  due             DATE,                                -- date only; client renders in user TZ
  priority        TEXT NOT NULL DEFAULT 'med' CHECK (priority IN ('low','med','high')),
  source_kind     TEXT NOT NULL CHECK (source_kind IN ('user','chat','agent')),
  source_thread_id   UUID,
  source_run_id      UUID,
  source_agent_id    UUID,
  source_excerpt     TEXT,
  project_id      UUID,                                -- nullable; FK below (no cascade)
  labels          TEXT[] NOT NULL DEFAULT '{}',
  sort_index      DOUBLE PRECISION NOT NULL DEFAULT 0,
  text_tsv        TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', coalesce(text,''))) STORED,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at      TIMESTAMPTZ,                         -- soft-delete tombstone
  CONSTRAINT todos_source_shape CHECK (
    (source_kind = 'user'  AND source_thread_id IS NULL AND source_agent_id IS NULL) OR
    (source_kind = 'chat'  AND source_thread_id IS NOT NULL) OR
    (source_kind = 'agent' AND source_agent_id IS NOT NULL)
  )
);

CREATE INDEX todos_owner_open_due_idx
  ON todos (tenant_id, owner_user_id, done, due ASC NULLS LAST)
  WHERE deleted_at IS NULL;

CREATE INDEX todos_project_idx
  ON todos (tenant_id, project_id)
  WHERE deleted_at IS NULL AND project_id IS NOT NULL;

CREATE INDEX todos_text_tsv_idx
  ON todos USING GIN (text_tsv);

CREATE INDEX todos_source_thread_idx
  ON todos (tenant_id, source_thread_id)
  WHERE deleted_at IS NULL AND source_thread_id IS NOT NULL;

-- Tenant isolation (RLS, matching the 0008_rls_tenant_isolation pattern)
ALTER TABLE todos ENABLE ROW LEVEL SECURITY;
CREATE POLICY todos_tenant_isolation ON todos
  USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

Migration `0033_todo_extractions.sql`:

```sql
CREATE TABLE todo_extractions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  owner_user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source_thread_id  UUID NOT NULL,
  source_run_id     UUID NOT NULL,
  proposed_todos    JSONB NOT NULL,                    -- array of {text, priority, due?, excerpt?}
  status            TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','accepted','rejected','snoozed')),
  snoozed_until     TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at       TIMESTAMPTZ
);

CREATE INDEX todo_extractions_pending_idx
  ON todo_extractions (tenant_id, owner_user_id, status, created_at DESC)
  WHERE status = 'pending';

ALTER TABLE todo_extractions ENABLE ROW LEVEL SECURITY;
CREATE POLICY todo_extractions_tenant_isolation ON todo_extractions
  USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

`project_id` is intentionally **not** a FK with `ON DELETE CASCADE` — see §13 cascade rule (project delete nulls out `project_id`, doesn't delete the todo).

### 5.2 Retention

Per master §3.3 (90d default tenant-configurable retention) and the orchestrator brief's specifics:

| Class                          | Retention                                                             |
| ------------------------------ | --------------------------------------------------------------------- |
| Open todos (`done=false`)      | Indefinite until explicit user delete                                 |
| Done todos (`done=true`)       | 365 days from `completed_at`, then hard-deleted                       |
| Soft-deleted todos             | 30 days from `deleted_at`, then hard-deleted (audit row anonymised)   |
| Pending extractions            | 30 days from `created_at`, then hard-deleted if still `pending`       |
| Rejected / snoozed extractions | 90 days from `resolved_at`                                            |
| Accepted extractions           | 365 days from `resolved_at` (provenance trail of what Atlas proposed) |

The retention sweeper is **the existing `runtime_worker/jobs/retention_sweeper.py` pattern** extended with a `todos_retention` job class (Phase 3 impl-A delivers it). Hard delete cascades to nothing else (todos are leaves in the schema). Audit rows are NOT removed — they are anonymised (`actor_user_id` and `target_id` retained; `before_state`/`after_state` redacted to `{redacted:true, retention_class:"todo"}`).

GDPR / right-to-be-forgotten: the admin-only `POST /v1/admin/privacy/forget-user` (existing in `backend_app/privacy/`) is extended to delete all of a user's todos + extractions immediately, cascading to anonymise the audit chain.

---

## 6. Audit

Per master §3.2. Every state change emits one row to `packages/audit-chain`:

| Action              | When                                                           | Significance                                    | Notes                                                                                        |
| ------------------- | -------------------------------------------------------------- | ----------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `todo.create`       | `POST /v1/todos` or extraction-accept                          | normal                                          | `after_state` = full todo                                                                    |
| `todo.update`       | Any `PATCH` that changes text/due/priority/project/labels/sort | normal                                          | `before_state` and `after_state` diff                                                        |
| `todo.mark_done`    | `PATCH { done: true }`                                         | **significant** — surfaces in admin audit views | separate from generic update so SIEM can filter                                              |
| `todo.mark_undone`  | `PATCH { done: false }` on a done todo                         | **significant**                                 | symmetric                                                                                    |
| `todo.delete`       | `DELETE /v1/todos/<id>` (soft delete)                          | normal                                          |                                                                                              |
| `todo.hard_delete`  | Retention sweeper or admin forget                              | normal                                          | actor = system; audit row anonymised in place                                                |
| `todo.bulk_action`  | Per affected row in a bulk op                                  | normal                                          | All rows share `correlation_id` (UUID) for queryability                                      |
| `extraction.create` | Internal POST from ai-backend                                  | normal                                          | actor = system / ai-backend; `before_state=null`                                             |
| `extraction.accept` | `POST /v1/todos/extractions/<id>/accept`                       | **significant**                                 | one row for the extraction + one `todo.create` per accepted item; all share `correlation_id` |
| `extraction.reject` | `POST /v1/todos/extractions/<id>/reject`                       | normal                                          |                                                                                              |
| `extraction.snooze` | `POST /v1/todos/extractions/<id>/snooze`                       | normal                                          |                                                                                              |

**Bulk operations write one audit row per affected todo** (NOT one summary row). Rationale: SIEM ingestion is per-event; a summary row hides which specific items were touched and breaks per-item legal-hold workflows. The `correlation_id` (shared UUID across the bulk's rows) makes them queryable as a unit when needed.

Audit row schema (per master §3.2): `(tenant_id, actor_user_id, action, target_kind="todo"|"todo_extraction", target_id, before_state, after_state, ts, request_id, correlation_id?)`. Append-only — no UPDATE, no DELETE, ever. Exportable via the existing `/v1/audit/export` endpoint.

---

## 7. Authorization

### 7.1 Roles + base rule

- **Owner-only by default.** A todo's `owner_user_id` is the only principal who can READ or WRITE it. Other workspace members get 404 (not 403 — don't leak existence).
- Admins (workspace `role IN ('owner','admin')`) can **READ any todo in their tenant** for compliance / SIEM purposes, but **cannot mutate** (no admin write — compliance value comes from immutability of the user's own queue). Admin reads emit an `audit.admin_read` row (existing pattern).
- Guests: no access to the todos surface at all (`/v1/todos` returns 403 for guest role).

### 7.2 Project-scoped reads

When a todo has `project_id IS NOT NULL`:

- **Project members** (any role in `project_members`) can READ that todo.
- **Only the owner** can mutate it (text, done, priority, due, etc.).
- An admin demoted to no role on the project still has tenant-admin read via §7.1.

The exact rule, expressed as SQL:

```sql
-- Read access
todo.owner_user_id = current_user_id
OR (todo.project_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM project_members pm
    WHERE pm.project_id = todo.project_id AND pm.user_id = current_user_id))
OR current_user_role IN ('owner','admin')   -- tenant-admin
```

```sql
-- Write access
todo.owner_user_id = current_user_id        -- only owner; no admin write
```

### 7.3 Cross-tenant safety

Master §3.1: tenant_id is derived from the verified bearer; never accepted from request body. The facade rejects requests carrying a body `tenant_id`. The backend's RLS policy (§5.1) is the second wall.

Tests required (see §17): cross-tenant read returns 404; cross-tenant write returns 403; missing tenant claim returns 401.

---

## 8. Pagination + search

Per master §3.5. Specifics:

- **Default limit**: 50. **Max**: 200. Limit > 200 → 400 with `{ error: "limit exceeds max" }`.
- **Cursor**: opaque base64 of `(sort_field_value, id)`. The backend rejects malformed cursors with 400. Cursor + `sort` changes invalidate the cursor.
- **Sort allowlist**: `due:asc`, `due:desc`, `priority:desc`, `priority:asc`, `created_at:desc`, `created_at:asc`, `updated_at:desc`. Anything else → 400.
- **Filter allowlist**: `done`, `priority`, `project_id`, `source` (kind). Multiple values per axis OR'd; across axes AND'd. Unknown filter axis → 400.
- **Search (`q=`)**: Postgres `text_tsv @@ plainto_tsquery('simple', q)` (the `simple` config — no stopword stripping; product is English-only for Wave 2 per master §3.9). Frontend debounces 250ms. `q=` combines with filters (AND). Empty q → ignored, not "match all empty".
- **Section bucketing happens server-side?** No — the server returns a flat list; the destination buckets by `due` and `done` client-side. This keeps the server simple and lets the client re-bucket instantly when the user changes a `due` via drag (no refetch). Trade-off: when the list is long, the client paginates per section by re-issuing `GET /v1/todos?filter[done]=false&sort=due:asc` and tracking the cursor across sections. Acceptable for ≤200 open todos; if the user exceeds that, we paginate within Done first (Done is bucketed last and the user rarely scrolls deep).

---

## 9. Accessibility (WCAG 2.1 AA — see master §3.6)

- **One tab stop per row.** The checkbox is **inside** the row's focusable container; `Space` toggles done; `Enter` opens the row's expanded view (text + excerpt + source).
- **Arrow keys** move between rows within a section; `Home`/`End` jumps to first/last. Crossing a section boundary moves focus to the section header (which is also a button: collapses the section).
- **Done state announced**: visual = checkbox checked + line-through text + opacity 0.55 (matches `projects-todos.css:439-445`). Screen reader: `<TodoRow>` has `aria-checked={done}` on the checkbox AND the row text has the prefix "Completed: " when done. **Color is never the sole carrier of state** (master §3.6) — line-through + opacity + aria-checked all redundantly encode done-ness.
- **Inline-add textareas** have `<label>` (sr-only). Placeholder is hint text, not the label.
- **Drag handles are keyboard-operable** (§3.5 — `Space` picks up, `↑/↓` moves, `Enter` drops, `Esc` cancels). Announcements via `aria-live="polite"`: "Picked up: Get legal sign-off — use arrows to move, Enter to drop." On drop: "Moved to Today, position 2 of 4."
- **Priority chip** uses icon + label, never colour-only (`<Icon.flag /> high`). Danger-tone for high; muted for low.
- **Source chip** is a button with `aria-label="Open source thread: {title}"`.
- **Extraction banner** has `role="region" aria-label="3 proposed todos from Atlas"`.
- **Reduced motion**: drag animation honors `prefers-reduced-motion: reduce` — instant snap, no inertia.
- **High-contrast theme**: all tokens (`--color-danger`, `--color-accent`, `--color-text-muted`) are already high-contrast aware per design-system; no extra work needed but verified in tests.
- **CI test**: `axe-core` run on `<TodosDestination>` + `<TodosPanel>` in light/dark/high-contrast themes, drag-mode active.

---

## 10. Performance (master §3.7)

- **Initial fetch**: one round-trip for the list (50 items) + one for pending extractions. **No waterfall.** The frontend can fire them in parallel.
- **Virtualisation**: when a section's row count > 100, the section uses `react-window`-style fixed-height row virtualisation (62px per row, accounts for the meta chip row). Done section is virtualised by default (it can grow over a year).
- **Optimistic UI**:
  - **Mark done** — checkbox toggles instantly; row fades to done style; backend PATCH in the background. Rollback on error (existing pattern in `TodosDestination.tsx:474-520` — keep this).
  - **Inline add** — row appears with skeleton id; replaced with real id on POST resolve. Rollback marks the row red with retry.
  - **Priority change** — chip swaps instantly.
  - **Delete** — row collapses to 0 height instantly; on PATCH success, removed from DOM. On error, row re-expands with retry.
  - **Drag-reorder** — `sort_index` is updated client-side via the float-between-neighbours pattern (avoids re-indexing); backend PATCH replays the new index.
- **Drag is RAF-throttled** — pointer-move events coalesced via `requestAnimationFrame` so frame rate is honoured even when dragging through a long section.
- **No re-render of the shell on tab change.** Filter chip clicks update local state in `TodosPanel`; the destination subscribes via a context exported from the same folder (`TodosFilterProvider`) — only `<TodosDestination>` re-renders. Shell does not.
- **LCP budget**: <2.5s cold load (50 todos + pending extractions) on broadband. **INP budget**: <200ms for tab switch, checkbox toggle, popover open. CI lighthouse-like budget per master §3.7.

---

## 11. Telemetry (master §3.8)

Every user-meaningful action emits an OpenTelemetry span with attributes: `destination="todos"` (constant); `action` (one of `open`, `tab_change`, `mark_done`, `mark_undone`, `create`, `delete`, `update`, `bulk_action`, `drag_reorder`, `accept_extraction`, `reject_extraction`, `snooze_extraction`, `open_source`, `open_project`); `bulk_size` (int, bulk only); `source_kind` (`user`/`chat`/`agent`, where relevant); `priority` (enum); `latency_ms` (int, mutations only); `tenant_id` (UUID); `user_id_hash` (sha256(user_id)[:16]).

**No PII in spans.** Todo text, project names, thread titles, excerpts — none of these enter telemetry. The `action` + `priority` + `source_kind` triple carries enough signal for product analysis.

---

## 12. States (master §3.10)

| State             | Render                                                                                                                                                                                                                                                                                  |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Loading           | Skeleton: 4 section headers + 6 row skeletons (matches existing `SkeletonRow` pattern)                                                                                                                                                                                                  |
| Empty (new user)  | `<EmptyState icon={CheckSquare} title="Nothing here yet" sub="Atlas extracts followups whenever it finds an action item — try the launch demo, or add one above." action="Add a todo" />`                                                                                               |
| Section-empty     | Section not rendered (todos.jsx:135). If ALL sections empty, fall through to Empty state.                                                                                                                                                                                               |
| Error             | `<ErrorPanel>` with Retry button (existing pattern). User-readable error; tech details only in telemetry.                                                                                                                                                                               |
| Saving            | Per-row `data-pending="true"`; checkbox disabled with skeleton-toned border.                                                                                                                                                                                                            |
| Saved (transient) | After a successful mutation, the row briefly pulses with accent tone (200ms ease, honors prefers-reduced-motion).                                                                                                                                                                       |
| Offline           | Banner at top of destination: "Offline — reads from cache, writes will retry when online." Reads served from `KeyValueStore` cache of last successful GET. Writes are queued in the same KV and replayed on reconnect; failures after 5 retries are surfaced per-row with manual retry. |
| Stale             | If cached data is >5 min old AND the destination just opened from cache, a `<RefreshHint>` banner appears: "Showing cached todos from 5 min ago — refresh".                                                                                                                             |

---

## 13. Cross-destination references (master §3.11)

### 13.1 Outbound (a todo points at something)

- `source.thread_id` → chats destination (`<ItemLink kind="chat" id={thread_id} />`).
- `source.run_id` → ai-backend run record (`<ItemLink kind="run" id={run_id} />`) — opens the chat at the originating message.
- `source.agent_id` → agents destination (`<ItemLink kind="agent" id={agent_id} />`).
- `project_id` → projects destination (`<ItemLink kind="project" id={project_id} />`).

`<ItemLink>` is the master §4.3 cross-destination primitive — `chat-surface` registers a resolver for the `"todo"` kind from this destination so inbound links work too.

### 13.2 Inbound (other destinations link to a todo)

- Home destination "Today's focus" — top 3 open todos by `priority desc, due asc`.
- Inbox destination — when an inbox message has an associated extracted todo (Phase 4), the inbox preview shows a "see todo" chip.
- Projects destination — the project detail view embeds the project's open todos (per `dest-misc.jsx:281-307` ProjectPage pattern).

### 13.3 Cascade rules

Per master §3.11. Deletion behaviour, per relationship:

| Foreign object deleted   | What happens to the todo                                                                                                                   |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Thread (chats) deleted   | Todo keeps `source.thread_id`. Source chip renders "Thread deleted" badge (muted, non-interactive). Tested.                                |
| Run (ai-backend) deleted | Same — keep `source.run_id`; chip says "Run no longer available."                                                                          |
| Agent deleted            | Same — `source.agent_id` retained; chip says "Agent deleted."                                                                              |
| Project deleted          | Todo's `project_id` is nulled out (moved to "Unfiled"). Audited as `todo.update` with `before_state.project_id != after_state.project_id`. |
| Owner user deleted       | All of the user's todos hard-deleted (tenant_id retained for retention audit). Cascade via FK `ON DELETE CASCADE` in §5.1.                 |
| Tenant deleted           | All todos + extractions hard-deleted via `ON DELETE CASCADE`.                                                                              |

We do NOT cascade-delete a todo when its source thread is deleted because:

1. The user may still need to act on the action item even after the chat is gone.
2. Decoupling provenance from existence is a master-PRD principle (cross-destination refs are typed, not enforced).

---

## 14. Desktop substrate caveats (master §3.12)

### 14.1 Native menu bar badge

Mac (and Windows menu bar / tray icon) should show a **count badge** of `open && (overdue || due today)` todos for the active workspace. This requires a new port: **`BadgePort`**.

**Spec for `packages/chat-surface/src/ports/BadgePort.ts`** (NEW — does not exist yet):

```typescript
export interface BadgePort {
  /**
   * Set the destination's badge count. `count === 0` clears the badge.
   * The host renders this on the OS menu bar (desktop) or favicon (web).
   *
   * The implementation may debounce — the destination calls this freely.
   */
  setBadge(destination: ShellDestinationSlug, count: number): void;
}
```

Web host: no-op (or favicon overlay — out of scope for Phase 3; document as TODO).
Desktop host: `app.dock.setBadge(count)` (Mac) or tray-icon overlay (Windows). Implementation lives in `apps/mac` and `apps/windows` once they exist. **For Phase 3, the port exists and `<TodosDestination>` calls it; web host implementation is a no-op.**

The destination computes the badge count from the loaded todos and pushes it on every list refresh. **It does not poll on its own** — that's the host's job if/when it wants a periodic refresh.

### 14.2 Native notifications

Out of scope for Phase 3 (master §5.2 puts notifications in Inbox phase 4 or later). The `NotificationPort` referenced in master §5.2 will be added by phase 4.

### 14.3 No direct browser API access

The destination uses only `useTransport`, `useRouter`, `useKeyValueStore` (the existing ports). Drag uses standard DOM events (synthetic React events — substrate-agnostic). Date formatting uses `Intl.DateTimeFormat` (universally supported across both substrates).

---

## 15. Implementation phasing (which agents do what)

### 15.1 Parallel impl agents (after this sub-PRD lands)

**Impl-A — api-types + backend + facade + retention + audit + extraction-receiver** (~3 hrs)

- Scope: `packages/api-types/src/todos.ts`, `services/backend/src/backend_app/todos/{routes,service,store,models}.py`, `services/backend/migrations/0032_*.sql`, `0033_*.sql`, `services/backend-facade/src/backend_facade/todos_routes.py`, retention sweeper extension, audit-chain hooks, `/internal/v1/todos/extractions` endpoint, full pytest suite (tenant isolation, authz, retention, audit, optimistic-reject rollback at API boundary, cursor pagination, full-text search).
- Branch: `worktree-agent-phase3-todos-impl-a`.

**Impl-B — TodosDestination + TodosPanel + BadgePort + frontend wiring + tests** (~3 hrs)

- Scope: rewrite `TodosDestination.tsx` to match §3 (sections, inline-add, drag, bulk, extraction banner), add `TodosPanel.tsx`, add `packages/chat-surface/src/ports/BadgePort.ts`, wire web-host no-op badge implementation in `apps/frontend`, vitest + RTL coverage for every state in §12, axe-core suite per §9, optimistic-rollback tests, drag keyboard tests.
- Branch: `worktree-agent-phase3-todos-impl-b`.

**Impl-C — ai-backend extraction worker + publisher** (~2 hrs)

- Scope: `services/ai-backend/src/runtime_worker/jobs/todo_extractor.py`, `services/ai-backend/src/agent_runtime/observability/todo_extraction_publisher.py`, HTTP client to `backend`'s internal endpoint, the LLM prompt + schema validation for proposed todos, claim-based job dispatch, retry/backoff, dead-letter on permanent failure, pytest coverage of: extraction emits exactly one publisher call per terminal run, malformed model output is rejected (no extraction created), service-token + identity headers always present, retry on backend 5xx.
- Branch: `worktree-agent-phase3-todos-impl-c`.

**Order of merge:** Impl-A first (the contract + storage). Impl-B and Impl-C in parallel against Impl-A. Final orchestrator merge runs the full cross-service smoke (`make test`).

### 15.2 Boilerplate every dispatched agent gets

The standard preamble from master §7.4. Plus the **destination-specific** addendum:

```
Required reading (in order):
1. docs/atlas-new-design/PRD.md
2. docs/atlas-new-design/destinations-master-prd.md §3, §4, §5.3
3. docs/atlas-new-design/destinations/todos-prd.md  (this file)
4. /tmp/atlas-design/0x-copilot-template/project/todos.jsx
5. /tmp/atlas-design/0x-copilot-template/project/projects-todos.css
6. /tmp/atlas-design/0x-copilot-template/project/data.jsx (lines 118-200)
7. Your role's specific files (api-types + backend OR frontend OR ai-backend)
```

### 15.3 What does NOT ship in Phase 3

- Recurring todos (open question §16).
- Subtasks (open question §16).
- Drag-and-drop between destinations (todo → another user's queue — Wave 4 multiplayer territory).
- Third-party sync (Asana / Linear / etc.) — anti-goal §18.
- Real-time live sync via SSE (the destination polls; SSE is a destination-wide cross-cut for Wave 4+).
- Native notifications when extraction lands (deferred to phase 4 — needs `NotificationPort`).
- Sharing a todo with a teammate as a notification (Wave 4 multiplayer).

---

## 16. Open questions for product (parth)

These need a call before Impl-A starts:

1. **Drag-reorder vs change-due — same gesture or different?** Proposed: same gesture; drop target's section decides. Within-section → `sort_index` PATCH; across-section → `due` PATCH. Both audited.
2. **Recurring todos — Wave 2 or later?** **Recommend: later.** Atlas's framing is "action items from work-in-progress" — recurrence is a productivity-app feature.
3. **Subtasks — Wave 2 or later?** **Recommend: later.** Single-line text + drag-to-reorder is the entire interaction surface for now.
4. **Auto-extraction — opt-in or default-on?** **Recommend: default-on with a per-tenant admin toggle + per-user opt-out** (in `me_preferences`). The banner is non-modal — easy to dismiss. Default-on makes §1.1 hold; opt-in hides Atlas's extraction until discovered.
5. **Auto-extraction threshold — confidence-gated or always-propose?** **Recommend: always-propose, accept/reject per item.** Confidence thresholds are hard to tune across tenants. Per-item rejection trains future runs (Wave 5+: per-tenant reject-patterns cache).
6. **Project default for new todo** — when on a project detail view, default `project_id=<current>`; when on todos directly, default `null` (Unfiled). Inline-add inherits the panel's current project filter when one is selected.
7. **Snooze a todo — Wave 2 or later?** **Recommend: later.** Changing `due` already pushes a deadline; a separate snooze is productivity-app territory.
8. **Done-section limit — paginate or cap?** **Recommend: cap at 14d by default, paginate beyond** via `filter[done]=true&sort=completed_at:desc&after=<cursor>`. Recent completion = satisfaction; long-tail = archive search.
9. **LLM prompt + budget for extraction?** Impl-C proposes; orchestrator approves. Model: cheapest reasoning-tier (e.g. Haiku) — read-only, low-stakes. Budget: ≤8 candidates per run; ≤2K input tokens of the last N turns; oldest-first truncation.

---

## 17. Test plan

Tests required to merge. Each is non-negotiable.

### 17.1 Backend (Impl-A)

- **Tenant isolation**: cross-tenant GET returns no rows; cross-tenant PATCH/accept returns 404; RLS rejects SELECT without `app.tenant_id` set.
- **Authorization**: owner reads/writes; project member reads project-scoped but PATCH/DELETE → 403; non-member → 404 (existence not leaked); tenant admin reads any todo (audited as `admin_read`), cannot write; guest → 403.
- **Audit**: every state change produces exactly one audit row (per affected target for bulk, with shared `correlation_id`); audit rows immutable (UPDATE rejected).
- **Retention**: done todo with `completed_at = now - 366d` hard-deleted by sweeper with audit row redacted; soft-deleted with `deleted_at = now - 31d` hard-deleted.
- **Mutation validation**: PATCH with invalid `priority` → 400 (frontend optimistic UI rolls back).
- **Extraction accept atomicity**: N-item accept inserts exactly N todos in one transaction; constraint failure mid-insert → zero todos inserted, extraction stays `pending`.
- **Pagination + search**: 250 todos with `limit=50` produces 5 distinct cursor pages, no overlap; `q="legal"` returns the `text_tsv` matches.

### 17.2 Frontend (Impl-B)

- **Render every state** from §12 (loading, empty, section-empty, error, saving, saved, offline, stale).
- **Optimistic UI rollback** on mark-done / create / delete / priority-change rejection.
- **Drag**: pointer-drag reorder updates `sort_index` via PATCH; keyboard drag works (Space pickup, ↑/↓, Enter drop, Esc cancel); axe-core passes in drag mode; reduced-motion honored.
- **Bulk select**: shift-click selects range; bulk-done dispatches one `POST /v1/todos/bulk`; on partial failure, the bulk action bar surfaces per-row errors.
- **Extraction banner**: appears when `/v1/todos/extractions?status=pending` returns ≥1 item; opens preview sheet; accept-all dispatches the single bulk-accept POST; per-item accept dispatches with the right `accepted_indices`.
- **`<BadgePort>` integration**: the destination calls `setBadge("todos", count)` whenever the loaded todos change; the web host implementation is a no-op (verified by spy).
- **a11y**: axe-core 0 violations on main + panel in 3 themes (light, dark, high-contrast); keyboard traversal lands on every row + chip + action; SR announces done-state and drag pickup/drop.
- **Telemetry**: spans emitted for each action in §11 with correct attribute schema; no PII fields present.

### 17.3 ai-backend (Impl-C)

- **Extraction publishes exactly once per terminal run** — multiple `final_response` events don't trigger duplicates; the publisher is keyed on `(tenant_id, run_id)` with an idempotency table.
- **Malformed model output** (non-JSON, missing required fields, extra fields) is rejected; the publisher does NOT call backend.
- **Service-token + identity headers** present on every internal POST; missing → 401 from backend (verified in integration).
- **Retry**: backend 5xx → exponential backoff, max 3 retries, then dead-letter. Backend 4xx → no retry, dead-letter.
- **No model call when transcript empty** (the run produced no user-visible content) — short-circuit; no extraction.

### 17.4 End-to-end smoke (orchestrator)

Run after all three impls merged: open a chat → produce a final response → wait for extraction → see banner on todos destination → accept → todo appears with correct source → mark done → audit chain queryable end-to-end via `/v1/audit/export`.

---

## 18. Anti-goals for this phase

- **No third-party sync.** Atlas Todos is not Asana / Linear / Things / Todoist. Sync is a connector concern and a long-tail product decision; out of scope.
- **No team-shared todos.** A todo has a single, required `owner_user_id`. Workflows that need shared visibility go through projects (project members read the project's todos; they don't co-own them). Multiplayer todos arrive — if at all — in Wave 4 alongside multiplayer threads.
- **No reminder notifications.** Push / desktop / email notifications are a notification-infra concern (NotificationPort) and arrive in phase 4 Inbox at the earliest.
- **No richer text in the todo body.** Single line. No markdown rendering, no checkboxes-within-checkboxes, no @mentions inside todo text. Mentions live in the composer; project / source links live in the meta chips.
- **No recurring todos** (open Q §16; recommend later).
- **No subtasks** (open Q §16; recommend later).
- **No "smart bucketing" beyond Today / Overdue / This week / Later / Done.** No "Upcoming" (ambiguous), no "Pinned" (the panel's filters cover it), no per-day breakdown.
- **No frontend-only logic the backend should own.** The bucketing logic is client-side (acceptable — pure function over an already-tenant-filtered list). The retention logic, ACLs, audit, RLS — all server-side, always.
- **No silent placeholders.** Empty / loading / error states render real content, never `TODO:` or `Placeholder`.

---

## 19. References

- [PRD.md](../PRD.md) · [destinations-master-prd.md](../destinations-master-prd.md) — workspace shell PRD + master destinations PRD (§3 checklist, §4 primitives, §5.3 todos master, §7 dispatch)
- `/tmp/atlas-design/0x-copilot-template/project/todos.jsx` — design reference
- `/tmp/atlas-design/0x-copilot-template/project/projects-todos.css` — visual intent (consumed via design-system tokens)
- `/tmp/atlas-design/0x-copilot-template/project/data.jsx` lines 118-200 — mock todo data shape
- `/tmp/atlas-design/0x-copilot-template/project/dest-misc.jsx` lines 184-220 — TodosMain mount
- [`packages/audit-chain/`](../../../packages/audit-chain/) — append-only audit primitive
- [`services/backend/migrations/0008_rls_tenant_isolation.sql`](../../../services/backend/migrations/0008_rls_tenant_isolation.sql) — RLS pattern to mirror
- [`services/ai-backend/src/runtime_worker/jobs/retention_sweeper.py`](../../../services/ai-backend/src/runtime_worker/jobs/retention_sweeper.py) — claim-based job pattern Impl-C mirrors
- Service rules: [`apps/frontend/CLAUDE.md`](../../../apps/frontend/CLAUDE.md), [`services/backend/CLAUDE.md`](../../../services/backend/CLAUDE.md), [`services/backend-facade/CLAUDE.md`](../../../services/backend-facade/CLAUDE.md), [`packages/api-types/CLAUDE.md`](../../../packages/api-types/CLAUDE.md)
