# PRD-07 — Project rollup counts, files, and the project-scoped chat list

## Problem

Open Projects. Every card reads **"0 chats · 0 files"**, forever, no matter how many
chats you have run or files you own. Open a project. The Chats section says "No chats
in this project yet" — always, for every project, including one you just chatted in.
Below it, the Files section says "Project files coming soon".

All three are the same defect wearing three costumes: **a project is not actually
attached to anything.** There is no column anywhere that records "this chat belongs to
that project". The counts table exists and has no writer. The project-activity endpoint
the detail view calls does not exist and 404s on every load, which is why the chat list
is permanently empty. And the file list — the one thing that _is_ fully built end to
end — is never asked for, because the client believes the endpoint is missing.

The user-visible consequence is that Projects is a folder that cannot hold anything.
The design sells it as "group related chats, files, and context"
(`copilot-app.jsx:392`); the product ships an empty box with a zero on it.

## Evidence

| Claim                                                                      | File:line                                                                                                                                                                | What the code actually does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Counts read path is real                                                   | `services/backend/src/backend_app/projects/service.py:1069-1086`                                                                                                         | CONFIRMED. `_counts_for` reads `store.get_counts(...)`; on `None` it synthesizes an all-zeros record, filling only `members` from `list_memberships_for_project(limit=501)`.                                                                                                                                                                                                                                                                                                                                                                                                         |
| `upsert_counts` has no caller                                              | `store.py:604` (in-memory), `store.py:1115` (Postgres)                                                                                                                   | CONFIRMED. Repo-wide grep for `upsert_counts` across `services/*/src`, `packages/`, `apps/` returns exactly those two definitions and zero call sites. `project_activity_counts` is therefore empty in every deployment that has ever existed.                                                                                                                                                                                                                                                                                                                                       |
| The counts table exists in Postgres                                        | `projects/schema.sql:223-244`, mirrored verbatim in `migrations/0043_projects.sql`                                                                                       | CONFIRMED. Table + RLS tenant-isolation policy + `GRANT` at `schema.sql:356`. Columns: `chats, todos_open, todos_done, inbox_items, library_items, routines_active, members, recomputed_at`. **No `files` column.**                                                                                                                                                                                                                                                                                                                                                                  |
| Counts are read N+1 per page                                               | `service.py:302-313`                                                                                                                                                     | CONFIRMED, and worse than the audit said: `list_projects` calls `self._counts_for(record)` inside a generator over the page, and each miss issues a second `list_memberships_for_project` call. Two queries per card.                                                                                                                                                                                                                                                                                                                                                                |
| A "projector" is promised but never written                                | `projects/schema.sql:174-177`, `store.py:176-181`                                                                                                                        | CONFIRMED. The schema comment names `backend_app/projects/activity_projector.py` as "out of scope for P6-A1"; that file does not exist. `append_activity`/`list_activity` have no production caller either (only `tests/integration/persistence/test_projects_store_live.py:395-398`).                                                                                                                                                                                                                                                                                               |
| Conversations carry no project link                                        | `services/ai-backend/migrations/0001_runtime_baseline.sql:27-49`                                                                                                         | CONFIRMED and this is the root cause. `agent_conversations` has 21 columns; `project_id` is not one of them. `ConversationRecord` (`runtime_api/schemas/conversations.py:114-156`) has no `project_id` field.                                                                                                                                                                                                                                                                                                                                                                        |
| `CreateConversationRequest.project_id` exists but is not persisted         | `runtime_api/schemas/conversations.py:65`, `agent_runtime/api/conversation_coordinator.py:141-176`                                                                       | CONFIRMED. The field is consumed for connector-allowlist inheritance and then written **only into an audit-log `context` blob** (`conversation_coordinator.py:173`). It never reaches a column.                                                                                                                                                                                                                                                                                                                                                                                      |
| The facade silently drops `project_id` on conversation create              | `services/backend-facade/src/backend_facade/app.py:61-67, 396-408`                                                                                                       | CONFIRMED. `FacadeConversationRequest` declares 6 fields, none of them `project_id`; Pydantic's default `extra="ignore"` plus `model_dump(exclude_none=True)` means a client that sends `project_id` has it deleted at the facade. **No app can file a chat under a project today, even by hand.**                                                                                                                                                                                                                                                                                   |
| `GET /v1/agent/conversations` cannot filter by project                     | `backend-facade/app.py:410-430`, `ai-backend/runtime_api/http/routes.py:113-130`                                                                                         | CONFIRMED. Both signatures accept only `limit`, `include_archived`, `include_deleted`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Detail Chats section is fed from ProjectActivity                           | `apps/frontend/src/features/projects/ProjectsRoute.tsx:624-673`                                                                                                          | CONFIRMED. `renderCrossDestinationTab("chats", …)` filters `activity` for `ref.kind === "chat"` and renders a bare `<ul>/<li>` whose only content is `a.preview` — no status, no model, no timestamp, because `ProjectActivityRecord` (`store.py:149-173`) has no such fields.                                                                                                                                                                                                                                                                                                       |
| **`GET /v1/projects/{id}/activity` does not exist** — NEW, audit missed it | `apps/frontend/src/api/projectsApi.ts:253-274` vs. `backend/projects/routes.py` + `backend-facade/projects_routes.py`                                                    | The client calls it. Enumerating every route on both sides (backend `routes.py` lines 205/277/302/340/401/453/488/520/571/617/661/771/802; facade `projects_routes.py` lines 118/141/156/173/190/231/249/273/292/312/335/380/401/424/443/466/482/501) shows **no `/activity` route anywhere**. `ProjectsRoute.tsx:462-465` `.catch(() => ({items: []}))` swallows the 404. **The detail chat list is unconditionally empty in the real app**; the parity harness only saw rows because `tools/design-parity/lib/render-live-projects.test.tsx:334-336` mocks `fetchProjectActivity`. |
| **DISPUTED — "NO files endpoint exists on any service"**                   | `backend/library/routes.py:103-146`, `backend-facade/library_routes.py:50-74`                                                                                            | The code disagrees with the brief and with FINDINGS RC-4. `GET /v1/library` accepts repeatable `filter[project_id]` (`routes.py:120`) and `filter[kind]` / `filter[file_kind]`, and the facade forwards every `filter[*]` param verbatim (`library_routes.py:63-69`). A project-scoped file list is one existing GET away. What is missing is the **client binding**, not the endpoint.                                                                                                                                                                                              |
| Library rows are already project-scoped, indexed, and ACL'd                | `backend/library/schema.sql:33, 69-71`; `backend/library/service.py:194-227`                                                                                             | CONFIRMED. `library_files.project_id uuid NULL` with `library_files_project_idx ON (tenant_id, project_id, updated_at DESC) WHERE project_id IS NOT NULL AND deleted_at IS NULL`. Visibility predicate: own row OR `project_id ∈ readable_project_ids` (one membership-port call per list).                                                                                                                                                                                                                                                                                          |
| The file count is already free                                             | `backend/library/store.py:489-499`; `packages/api-types/src/library.ts:232-240`                                                                                          | CONFIRMED. `list_items` returns `counts_by_kind` computed over the visibility-filtered, filter-applied set **before pagination**. `LibraryListResponse.counts_by_kind.file` is an exact project file count with zero extra round-trips.                                                                                                                                                                                                                                                                                                                                              |
| **Library (and todos/inbox/routines) have no Postgres adapter**            | `backend/app.py:2133`; `grep -rln library_files services/backend/src` → `library/store.py`, `library/schema.sql` only; `services/backend/migrations/` has no library DDL | CONFIRMED, and material. `resolved_library_store = library_store or InMemoryLibraryStore()` and nothing in the repo ever passes `library_store=`. Same for `InMemoryTodosStore` / `InMemoryInboxStore` / `InMemoryRoutinesStore`. Only Projects has `PostgresProjectsStore` (`projects/store.py:662`).                                                                                                                                                                                                                                                                               |
| No writer path for a project file exists in-product                        | `backend/library/upload_routes.py:281,348` vs `backend-facade/library_routes.py:50-215`                                                                                  | CONFIRMED. `POST /v1/library/files/upload-grant` + `…/finalize` exist on backend; the facade proxies only list / get / pages / patch / search / delete. No app can upload a file at all today.                                                                                                                                                                                                                                                                                                                                                                                       |
| The web host binds `fileCount` to the wrong field                          | `ProjectsRoute.tsx:758-759`                                                                                                                                              | CONFIRMED. `chatCount: project.counts.chats`, `fileCount: project.counts.library_items` — `library_items` is all three library kinds (file + page + dataset), not "files". Both are structurally 0 today.                                                                                                                                                                                                                                                                                                                                                                            |
| Desktop has no project detail at all                                       | `apps/desktop/renderer/destinationBinders.tsx:542-568`                                                                                                                   | CONFIRMED. `ProjectsBinder` renders `<ProjectsDestination items={result} onRetry={retry}/>` — no `focusedProjectId`, no `renderDetail`. Anything fixed only in `ProjectsRoute.tsx` is invisible on desktop.                                                                                                                                                                                                                                                                                                                                                                          |
| The chat-row projection is duplicated and already divergent                | `apps/frontend/src/features/chats/api/chatsApi.ts:104-118` vs `apps/desktop/renderer/destinationBinders.tsx:169-183`                                                     | CONFIRMED, re-verified in this tree (`grep -rn toArchiveRow apps packages` → `chatsApi.ts:90,104,192` and `destinationBinders.tsx:169,193`, nothing else). Web reads the first-class `conversation.pinned`, desktop reads `metadata.pinned`. The SSOT is PRD-03's `toChatArchiveRow` in `packages/chat-surface/src/projections/chats.ts` (README C8); this PRD consumes it and must not become a third copy.                                                                                                                                                                         |
| `ProjectDetailView` already has the right props and states                 | `ProjectDetailView.tsx:135-172, 553-700, 946-978`                                                                                                                        | CONFIRMED. `files?: SectionResult<ProjectFileRow[]> \| null` with a 4-state machine (`undefined` → "coming soon", `null` → skeleton, error/unavailable/empty/ready), and `soloSections` renders `SectionHeader count={project.chatCount}` / `count={project.fileCount}`. The view is not the problem.                                                                                                                                                                                                                                                                                |

## Design intent

The card (`copilot-app.jsx:396-424`) is a single `<button className="card proj-card">`
whose last line is a mono meta row:

```jsx
<div className="lrow__sub" style={{ marginTop: 10 }}>
  {p.chats} chats · {p.files} files
</div>
```

`.lrow__sub` (`copilot.css:1643-1648`): `font-size: 11px`, `color: var(--mut2)` =
`#64646d` (`copilot.css:19`), `font-family: var(--mono)`. Note the deliberate split
inside one card: the _description_ line overrides to `var(--body)` inline
(`copilot-app.jsx:416-419`) while the _counts_ line stays mono. Fixture values:
`{chats: 3, files: 12}`, `{3, 20}`, `{2, 7}` (`copilot-data.jsx:797-822`).

The detail view (`copilot-app.jsx:336, 363-381`) is decisive about where each number
comes from:

```jsx
const chats = CHATS.filter((c) => c.project === sel);   // :336
…
<div className="sect-h">Chats · {chats.length}</div>     // :363
{chats.map((c) => <ChatRow key={c.id} c={c} navigate={navigate} />)}
<div className="sect-h">Files · {p.files}</div>          // :369
```

Three literal specifications fall out of that:

1. **A project's chats are the chat list filtered by project.** Not an activity feed —
   the same `CHATS` collection the Chats surface renders, `.filter(c => c.project === sel)`.
2. **The detail heading count is the rendered list length**, not a rollup. Only the
   card uses a rollup.
3. **The rows are the same `ChatRow`** used on the Chats surface (`copilot-app.jsx:255-286`):
   live/idle icon, title + `chip--ok|warn|off` status chip, sub-line
   `{preview} · <span className="mono">{model}</span>`, and `.lrow__time`. Every one of
   those fields exists on `ChatArchiveRow` (`packages/api-types/src/chats.ts:62-73`) and
   none exists on `ProjectActivityRecord`.

Files rows (`copilot-app.jsx:371-381`, data at `copilot-data.jsx:823-828`) are `.lrow`s
of `{n: "tokenomics.xlsx", m: "Sheets · edited 2d ago"}` — name on `.lrow__name`
(12.5px/500 `--tx`), `{kindLabel} · edited {relative}` on the mono `.lrow__sub`.

`.sect-h` (`copilot.css:1563-1573`): `var(--mono)`, `9.5px`, `letter-spacing .12em`,
uppercase, `var(--mut2)`, `margin: 22px 0 10px`.

Two mock artifacts that are **not** spec: the design's `Files · 12` sits over a
hard-coded 4-row array, so its header/list disagreement is fixture noise; and
`.proj-ic` overrides the per-project colour with `background: var(--panel3) !important`
(`copilot.css:1698-1710`), which is **PRD-10**'s problem, not this one (PRD-10 D3 keeps
the per-project hue and records the three tile-colour rows as `expectDivergence`).

## Architectural decision

Three seams change. None of them is "write to the counts table".

### Seam 1 — `agent_conversations.project_id` (the missing link)

Everything else is impossible without it. `ai-backend` migration
**`0003_conversation_project.sql`** (README migration table / C18 — `0002` is PRD-05's
`0002_run_history_index.sql`, `0004` is PRD-09's). Verified high-water mark on disk in
this tree: `ls services/ai-backend/migrations` → `0001_runtime_baseline.sql` (+ rollback)
and `MANIFEST.lock` only. Re-run `tools/check_migration_manifest.py --write` in the same
commit as the migration.

```sql
ALTER TABLE agent_conversations ADD COLUMN project_id text;
CREATE INDEX idx_agent_conversations_project
  ON agent_conversations (org_id, project_id, updated_at DESC)
  WHERE project_id IS NOT NULL AND deleted_at IS NULL;
```

Rollback drops both. `project_id` is a **loose reference** (no FK — projects live in a
different service's database; the same reasoning `projects.owner_user_id` already
documents at `0043_projects.sql:31`). Nullable, no backfill: pre-existing conversations
have no project, and inventing one would be a lie.

- `ConversationRecord` + `CreateConversationRequest` gain `project_id: str | None`
  (the request field already exists; it just starts being persisted).
- `UpdateConversationRequest` gains `project_id`, so an existing chat can be filed
  into a project (RFC 7396 merge-patch; `null` unfiles it).
- `list_conversations` gains `project_id: str | None` across the port
  (`agent_runtime/api/ports.py:115`), the query service
  (`conversation_query_service.py:178`) and all three adapters (`postgres`, `file`,
  `in_memory`). Predicate: `AND project_id = %s` when set.
- New route `GET /v1/agent/conversations/counts?project_ids=a,b,c` →
  `{"counts": {"a": 3, "b": 0}}`. `org_id`/`user_id` come from the verified identity
  exactly as `list_conversations` does (`routes.py:124`) — `project_ids` is a filter,
  never an authorization input. 200 with zeros for unknown ids; 422 for >100 ids.
  **Register the literal `/counts` path before any `/{conversation_id}` route** in both
  `runtime_api/http/routes.py` and the facade — FastAPI matches in registration order and
  the path param is an unconstrained `str` (same hazard the README flags for
  `/v1/agent/runs/active_count`). Wave order on both files is 05 → 07 → 09 → 12.

**Rejected:** storing the project in `metadata` JSONB. It is already how desktop reads
`pinned` (`destinationBinders.tsx:172`) and it is exactly why `pinned` had to be
promoted to a real column in migration 0034. A filter axis and a `GROUP BY` key must be
a column with an index.

### Seam 2 — counts are computed on read, by the service that owns the rows; the counter table is deleted

Delete `project_activity_counts` (migration **`0047_drop_project_activity_counts.sql`**
— README migration table / C18; `0046` is PRD-06's `0046_connector_access_mode.sql`.
Verified high-water mark on disk: `ls services/backend/migrations` tops out at
`0045_provider_api_keys_custom_endpoint.sql`. Re-run
`tools/check_migration_manifest.py --write` in the same commit), whose
rollback recreates the table verbatim from `0043_projects.sql:223-244`; delete
`get_counts`/`upsert_counts` from the `ProjectsStore` protocol and both adapters, and
delete the `counts` dict from `InMemoryProjectsStore`. Keep the `ProjectActivityCounts`
**model** — it stays as the computed wire shape.

Replace `_counts_for(record)` with `_counts_for_page(records)`: one batched call per
rollup source for the whole page, composed through a small registry.

```python
class ProjectRollupSource(Protocol):
    """One destination's contribution to the project rollup."""
    fields: tuple[str, ...]
    def count_by_project(
        self, *, tenant_id: str, project_ids: tuple[str, ...],
        caller_user_id: str, caller_roles: tuple[str, ...],
    ) -> dict[str, dict[str, int]]: ...   # project_id -> {field: count}
```

Registered in `backend_app/app.py` next to each destination's service, so
`projects/service.py` never imports `library`/`todos`/`inbox`/`routines` stores
directly:

| Source   | Fields                     | Predicate                                                                            |
| -------- | -------------------------- | ------------------------------------------------------------------------------------ |
| library  | `files`, `library_items`   | `kind='file'` / all kinds, `deleted_at IS NULL`, visibility per `service.py:194-227` |
| todos    | `todos_open`, `todos_done` | status open / done, live rows                                                        |
| inbox    | `inbox_items`              | **viewer-scoped** (recipient = caller), per `api-types/projects.ts:90-92`            |
| routines | `routines_active`          | `status='active'`                                                                    |
| members  | `members`                  | live memberships (replaces the per-project `limit=501` call)                         |

`chats` is **not** in that table, and cannot be: its rows live in `ai-backend`, and
`backend` calling `ai-backend` would invert the existing dependency direction
(`ai-backend → backend /internal/v1`) into a cycle. So:

- `backend` returns `counts.chats = null` — it is not entitled to an opinion.
- `packages/api-types/src/projects.ts`: `ProjectActivityCounts.chats: number | null`,
  plus a new `files: number` (kind=`file`) alongside the existing `library_items`
  (all kinds) — the design's "files" is the former; `ProjectsRoute.tsx:759` currently
  binds the latter.
- **`backend-facade` fills it in.** `GET /v1/projects` and `GET /v1/projects/{id}`
  gain one batched call to `ai-backend`'s counts route with the page's project ids, and
  merge the result into each `counts.chats`. The facade is the only component permitted
  to talk to both services, and a read-only fan-out join for a product-facing payload
  is precisely its job description; it adds no orchestration, no state, no retry logic.
  Upstream failure or timeout → `chats` stays `null`, the projects list still returns
  200, and the card renders "12 files" without a fabricated "0 chats".

**Alternatives rejected.**

- _Denormalised counters maintained transactionally on the events that change them_ —
  the option the schema comment assumes. It is unavailable for `chats` at any price:
  the insert happens in another service's database, so "transactionally" would mean a
  distributed write. It is also actively harmful for the other five: `library`,
  `todos`, `inbox` and `routines` are **in-memory-only stores** (`app.py:2133` and
  siblings; no Postgres adapter exists for any of them), so a durable Postgres counter
  would survive a restart that wipes the rows it counts — a card reading "12 files"
  over an empty list. Computed-on-read cannot disagree with the list, because it is
  the same read.
- _Periodic projection / nightly reconciler_ — buys eventual consistency and a new
  worker, a new failure mode, and a backfill, in exchange for a number the user is
  looking at directly next to the list it summarises. Drift here is visible drift.
- _Keep the table, add the missing writers_ — six writers plus a reconciler plus a
  backfill, to make a cache of a `GROUP BY` that runs on an index we already built
  (`library_files_project_idx`). Costed and rejected: the reads are per-page
  (`limit ≤ 50`) and index-only.

**Backfill:** none, and this is checkable rather than assumed —
`project_activity_counts` has never had a writer, so the drop is provably lossless.

### Seam 3 — the project-scoped chat list IS the chat list

Delete the hand-rolled `renderCrossDestinationTab("chats", …)` branch
(`ProjectsRoute.tsx:624-673`) and the `fetchProjectActivity` call from the detail read
(`ProjectsRoute.tsx:462-465`) — it calls a route that does not exist.

`ProjectDetailView` gains `chats?: SectionResult<ReadonlyArray<ChatArchiveRow>> | null`
with the same 4-state contract as `files`, replacing the host-injected
`renderCrossDestinationTab("chats", project.id)` call in the solo profile
(`ProjectDetailView.tsx:957-968`). Heading count = `rows.length`, per
`copilot-app.jsx:363`; `ProjectDetail.chatCount` stops being read in the solo profile
(it stays for the card).

**No `destinations/chats/ChatsSection.tsx` is extracted (README C16).** The row and
section _markup_ — `SectionHeader` + `_shared/RowList` + `_shared/Row` with the design's
icon / chip / sub / meta slots — is **PRD-10 D6**'s, and `ChatsArchive.tsx` belongs to
PRD-09, which rewrites it. PRD-07 lands the prop, the 4-state machine and the data, and
renders the ready state through the existing `_shared/RowList` / `_shared/Row`
primitives (the same components PRD-10 D6 specifies) so PRD-10 restyles one call site
rather than deleting a component this PRD invented. PRD-07 must not restyle those
primitives, and must not touch `ChatsArchive.tsx`.

Both hosts feed it through one new port, so desktop is not a copy of web:

```ts
// packages/chat-surface/src/ports/ProjectDataPort.ts
export interface ProjectDataPort {
  listProjectChats(
    projectId: ProjectId,
  ): Promise<SectionResult<ReadonlyArray<ChatArchiveRow>>>;
  listProjectFiles(
    projectId: ProjectId,
  ): Promise<SectionResult<ReadonlyArray<ProjectFileRow>>>;
}
```

Both implementations are thin and boring, and neither invents an endpoint:

- chats → `GET /v1/agent/conversations?filter[project_id]=<id>&include_archived=true`,
  mapped by PRD-03's shared per-row projector `toChatArchiveRow`
  (`packages/chat-surface/src/projections/chats.ts`, README C8 — PRD-03 ships the
  per-row function only; bucketing/paging is PRD-09's). Do not add a third
  `toArchiveRow`.
- files → `GET /v1/library?filter[project_id]=<id>&filter[kind]=file&limit=50`,
  mapping `LibraryFile` → `ProjectFileRow`: `id`, `name`, `fileKind` =
  `file_kind` label, `updatedAt` = `updated_at`, `sizeLabel` from `size_bytes`. The
  detail's `Files · N` uses `counts_by_kind.file` from that same response, so the
  header cannot disagree with the list.

`ProjectFileRow` is promoted to `packages/api-types/src/projects.ts` (its TODO at
`ProjectDetailView.tsx:117-123` asks for exactly this) and `id` becomes
`LibraryFileId`, so the existing `<ItemLink kind="library_file">` cast disappears.

**No `/v1/projects/{id}/files` route is created.** A project file is a library item
with `project_id` set — the model, the index, the ACL and the count already exist. A
second endpoint returning the same rows would be a second source of truth for
visibility, and library's project-membership predicate is the canonical one.

## Scope

**`packages/api-types`**

- `src/projects.ts` — `ProjectActivityCounts.chats: number | null`; add `files: number`;
  promote `ProjectFileRow` (id: `LibraryFileId`, name, file_kind, updated_at, size_bytes).
- `src/index.ts` — `Conversation.project_id?: string | null`; `ConversationListFilters`
  gains `project_id`. (Shared file; wave order 05 → **07** → 09 → 12.)

**`services/ai-backend`**

- `migrations/0003_conversation_project.sql` (+ `.rollback.sql`) — column + partial index
  (C18), plus the `MANIFEST.lock` rewrite in the same commit.
- `src/runtime_api/schemas/conversations.py` — `project_id` on `ConversationRecord`
  and `UpdateConversationRequest` (it already exists on `CreateConversationRequest:65`).
- `src/agent_runtime/api/conversation_coordinator.py` — persist `project_id` on create;
  honour it on patch. Stop treating it as audit-only metadata.
- `src/agent_runtime/api/ports.py`, `src/agent_runtime/api/conversation_query_service.py`
  — `project_id` filter + a `count_conversations_by_project` method.
- `src/runtime_adapters/{postgres,file,in_memory}/runtime_api_store.py`
  (+ `file/_catalog_index.py`) — column round-trip, filter predicate, grouped count.
- `src/runtime_api/http/routes.py` — `project_id` query param on `list_conversations`;
  new `GET /v1/agent/conversations/counts`.

**`services/backend`**

- `migrations/0047_drop_project_activity_counts.sql` (+ rollback, + `MANIFEST.lock`) and
  the matching edit to `src/backend_app/projects/schema.sql:223-244, 356`.
- `src/backend_app/projects/store.py` — delete `get_counts`/`upsert_counts` from the
  protocol and both adapters and the in-memory `counts` dict; keep the
  `ProjectActivityCounts` model, add `files`, make `chats` `int | None`.
- `src/backend_app/projects/service.py` — `_counts_for_page`; `ProjectRollupSource`
  protocol; kill the N+1 at `:302-313` and the `limit=501` membership read at `:1078-1084`.
- `src/backend_app/{library,todos,inbox,routines}/store.py` — one
  `count_by_project` each (grouped scan; these are dict-backed stores).
- `src/backend_app/app.py` — register the five rollup sources on the projects service.

**`services/backend-facade`**

- `src/backend_facade/app.py` — add `project_id` to `FacadeConversationRequest` (it is
  dropped today) and to the `list_conversations` proxy params.
- `src/backend_facade/projects_routes.py` — batched chat-count join for `GET /v1/projects`
  and `GET /v1/projects/{id}`; degrade to `chats: null` on upstream failure.

**`packages/chat-surface`**

- `src/ports/ProjectDataPort.ts` (+ export from `ports/index.ts`) — the new port.
- `src/destinations/projects/ProjectDetailView.tsx` — `chats` prop + 4-state machine;
  solo Chats section renders the ready rows through the existing `_shared/RowList` /
  `_shared/Row`; heading counts from list length; drop the
  `renderCrossDestinationTab("chats")` path in the solo profile
  (`ProjectDetailView.tsx:957-968`). **Data + props only** — the row anatomy and the
  section chrome are PRD-10 D6's (C16); file order is 07 → **10 owns**.
- `src/destinations/projects/ProjectsDestination.tsx` — card meta reads
  `counts.files`, hides the chats segment when `counts.chats === null`. Data binding +
  the meta line's content and its two type tokens only; the card's outer anatomy (hit
  area, `ProjectIconTile`, padding, grid) is PRD-10 D1–D3/D7, which owns this file
  (order 02 → 03 → **07** → 10).
- **Not in scope:** `src/destinations/chats/*` — PRD-09 owns `ChatsArchive.tsx` and
  PRD-07 extracts no `ChatsSection.tsx` (C16).

**`apps/frontend`**

- `src/api/projectsApi.ts` — delete `fetchProjectActivity` (calls a nonexistent route).
- `src/features/projects/ProjectDataPort.ts` (new) — web implementation over
  `/v1/agent/conversations` + `/v1/library`.
- `src/features/projects/ProjectsRoute.tsx` — PRD-10 Scope also deletes the hand-rolled
  `<ul>` at `:641-673` as part of its scaffold deletion (D1/D6). PRD-07 lands first and
  removes it here because its data source disappears with `fetchProjectActivity`; if
  PRD-10 has already landed, PRD-07 removes only the fetch. Concretely: remove the
  hand-rolled chat list
  (`:624-673`) and the activity fetch (`:462-465`); pass the port; bind `fileCount` to
  `counts.files`.

**`apps/desktop`**

- `renderer/destinationBinders.tsx` — the same `ProjectDataPort` implementation over the
  shared `Transport`, bound into `ProjectsBinder`. **Making the desktop detail reachable
  (`focusedProjectId` + `renderDetail`) is PRD-10** (D1/Scope, guarded by PRD-10 DoD 9)
  over PRD-03's shared Projects binder — PRD-07 does not pass those props. This file has
  eight claimants; PRD-07 edits it in wave 2, **after PRD-08** (which deletes the audit
  fan-out block).

**`tools/design-parity`**

- `lib/render-live-projects.test.tsx` — stop mocking `fetchProjectActivity`; feed the
  detail state through `ProjectDataPort` fixtures so the harness measures the real path.

## Non-goals

- **No `/v1/projects/{id}/files` endpoint.** See Seam 3.
- **No Library durability.** `library_files` has no Postgres adapter and no migration
  (`app.py:2133`); files are lost on restart. That is the Library destination's problem,
  not Projects'. This PRD's read binding is correct either way, and the Files section
  degrades to its existing "No files yet" empty state — never a lie. Tracked separately
  as _PRD-Library-Persistence_ (adapter + a library migration on the next free
  `services/backend` id — `0046` is PRD-06's and `0047` is this PRD's, so `0048`
  or later — plus the upload-grant/finalize
  facade proxy). Do not ship a fake writer to make this PRD look finished.
- **No file upload UI.** No writer path exists through the facade
  (`library_routes.py:50-215`); adding one is the same successor PRD.
- **No activity projector.** `project_activity` stays unwritten and
  `GET /v1/projects/{id}/activity` stays nonexistent. This PRD _stops calling_ it; it
  does not build it. The team-profile `ProjectActivityTab` receives `[]`.
- **No visual/token work.** The four divergent monogram tiles (RC-2), `.pg-lead`
  (RC-5), the `.pg` page shell (RC-9) and the accent-coloured navigation (RC-8) belong
  to **PRD-10** (C21 corrects this PRD's "PRD-05"); the `.sect-h` 9.5px drift (RC-7) is
  **PRD-01**'s `.ui-mono-caps` migration, applied to the label element and not to the
  `sect-h` wrapper (C13). The project chat rows' status chip is **PRD-02**'s component,
  rendered by PRD-10's markup — this PRD only supplies the `status` field on each row.
- **No auto-filing.** Nothing infers a chat's project from context. `project_id` is set
  explicitly by the caller; how the composer offers that choice is a product decision.
- **No cross-tenant or admin rollups.** Counts are viewer-scoped exactly as the
  underlying lists are.

## Risks & rollback

| Risk                                                                     | Guard                                                                                                                                                                                                                                                           |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dropping `project_activity_counts` destroys data                         | It cannot: no writer has ever existed (Evidence row 2). The rollback migration recreates the table verbatim. Add a pre-drop `SELECT count(*)` assertion to the live store test at `services/backend/tests/integration/persistence/test_projects_store_live.py`. |
| `counts.chats` becoming nullable breaks existing consumers               | `npm run typecheck --workspace @0x-copilot/api-types` plus the frontend and desktop typechecks fail closed on every unhandled site. `services/backend/tests/test_projects_routes.py` pins the wire shape.                                                       |
| Facade fan-out adds latency or a new failure mode to `GET /v1/projects`  | One batched call per page (not per project), short timeout, and failure degrades to `chats: null` rather than a 5xx. New test in `services/backend-facade/tests/test_projects_proxy.py` asserts a 200 with `chats: null` when the ai-backend call raises.       |
| Computed-on-read makes the projects list slow at scale                   | Each source is one grouped read per page; the only Postgres one (library, once it has an adapter) has `library_files_project_idx`. The change _removes_ today's 2N queries (`service.py:302-313`).                                                              |
| Three ai-backend adapters drift on the new column                        | `services/ai-backend/tests/unit/runtime_adapters/` already runs the same contract suite over in-memory/file/postgres; the new filter + count get a case in each.                                                                                                |
| Deleting `renderCrossDestinationTab("chats")` regresses the team profile | The slot stays for `todos/inbox/library/routines`; only the `chats` branch is removed, and only the solo profile stops calling it. `ProjectDetailView.test.tsx` covers both profiles.                                                                           |
| Racing PRD-10 in `ProjectDetailView.tsx` / `ProjectsDestination.tsx`     | Wave order is fixed (C16, README hot-file table): PRD-07 lands the props/data in wave 2, PRD-10 owns the markup in wave 4. PRD-07 extracts no `ChatsSection.tsx` and touches no `destinations/chats/*` file, so there is nothing for PRD-10 or PRD-09 to undo.  |

**Rollback:** three independent reverts. (1) `0047` rollback restores the counts table;
the code path that read it is gone, so restoring the table alone is inert — revert the
`service.py` commit with it. (2) `0003` rollback drops `project_id`; conversations are
unaffected because the column is nullable and nothing else reads it. (3) The client
seams revert to the previous commit; `ProjectDetailView` degrades to its existing
"coming soon" states when the port is absent (`files === undefined` at
`ProjectDetailView.tsx:594-606`), so a partial revert is not a broken screen.

## Definition of Done

1. `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_api/test_conversation_project_filter.py` passes, asserting: a conversation created with `project_id="p1"` is returned by `GET /v1/agent/conversations?project_id=p1`, is **not** returned for `project_id=p2`, and round-trips `project_id` through `GET /v1/agent/conversations/{id}`.
2. The same test asserts `GET /v1/agent/conversations/counts?project_ids=p1,p2` returns `{"counts": {"p1": 3, "p2": 0}}` for a fixture of 3 conversations on `p1`, and that a caller from a different `user_id` gets `{"p1": 0}` — proving the count is identity-scoped, not `project_ids`-scoped.
3. The identical assertions run against all three adapters: `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_adapters -k project_id` passes for `in_memory`, `file` and `postgres`.
4. `services/ai-backend/migrations/0003_conversation_project.sql` creates `idx_agent_conversations_project` and its `.rollback.sql` drops both column and index; `cd services/ai-backend && .venv/bin/python -m pytest tests/ -k migration` passes, and `python tools/check_migration_manifest.py` exits 0 (the `MANIFEST.lock` rewrite is in the same commit).
5. `cd services/backend-facade && .venv/bin/python -m pytest tests/test_forwarder.py -k project_id` passes, asserting `POST /v1/agent/conversations` with `{"project_id": "p1"}` forwards `project_id` upstream (today it is silently dropped by `FacadeConversationRequest`).
6. `cd services/backend-facade && .venv/bin/python -m pytest tests/test_projects_proxy.py` passes with two new cases: `counts.chats` is populated from the batched ai-backend call, and `counts.chats === null` (HTTP 200, not 5xx) when that call raises.
7. `cd services/backend && .venv/bin/python -m pytest tests/test_projects_service.py` passes, asserting `list_projects` returns `counts.files == 2` for a project holding 2 library files + 1 library page, and `counts.library_items == 3`.
8. `grep -rn "upsert_counts\|get_counts\|project_activity_counts" services/backend/src` returns **zero** hits, and the only file matching `grep -rln project_activity_counts services/backend` is `services/backend/migrations/0047_drop_project_activity_counts.rollback.sql`.
9. Three greps, each with a stated expected output: (a) `grep -rn "fetchProjectActivity" apps/frontend/src tools/design-parity` returns zero lines; (b) `grep -rln "toChatArchiveRow" apps/frontend/src/features/projects apps/desktop/renderer` lists both host `ProjectDataPort` implementations, proving each consumes PRD-03's shared projector; (c) `grep -rn "function toArchiveRow\|const toArchiveRow\|function toChatArchiveRow" apps packages` returns at most the three pre-existing definitions — `apps/frontend/src/features/chats/api/chatsApi.ts:104`, `apps/desktop/renderer/destinationBinders.tsx:169` (both deleted later by PRD-09) and `packages/chat-surface/src/projections/chats.ts` (PRD-03's) — i.e. this PRD adds **no** new row projection.
10. `packages/chat-surface/src/destinations/projects/ProjectDetailView.test.tsx` asserts that with `chats={{status:"ok", data:[row]}}` on the solo profile, `[data-testid="project-detail-section-chats"]` contains exactly one `[data-testid="chat-archive-row"]`, and that row's rendered text contains `row.title`, `row.model` and the formatted `row.updatedAt` — the three fields the activity-fed list could not carry (`ProjectActivityRecord`, `store.py:149-173`, has none of them). The row's _anatomy_ (icon slot, chip placement, `.lrow` padding) is asserted by PRD-10 DoD 17/18, not here.
11. The same test asserts (a) the Chats `SectionHeader` count equals the number of rendered `[data-testid="chat-archive-row"]` elements for a 3-row fixture (design `copilot-app.jsx:363` — `Chats · {chats.length}`), and (b) with `files={{status:"ok", data:[…12 rows]}}` the Files `SectionHeader` renders `12`, sourced from the same response's `counts_by_kind.file` so header and list cannot disagree.
12. **Design value pinned numerically:** `packages/chat-surface/src/destinations/projects/ProjectsDestination.test.tsx` asserts that for `counts: {chats: 3, files: 12, …}` the card meta element's `textContent` is exactly `"3 chats · 12 files"` (U+00B7 separator, `copilot-app.jsx:422-424`) and its style object is `fontFamily: "var(--font-mono)"`, `color: "var(--color-text-subtle)"` and `fontSize: "var(--font-size-2xs)"` — verified in this tree: `--color-text-subtle: #64646d` (`packages/design-system/src/styles.css:178`) is byte-identical to the design's `--mut2` (`copilot.css:19`), and `--font-size-2xs: 0.7rem` = 11.2px (`styles.css:63`) against the design's `.lrow__sub` 11px (`copilot.css:1643-1648`) — a 0.2px delta, below the comparator's 0.4px flag threshold (`tools/design-parity/lib/compare.mjs:98-99`), so no new rung is minted. The rest of the card's anatomy is PRD-10's.
13. **Regression guard for this PRD's bug (fails on `main`):** `ProjectsDestination.test.tsx` asserts that a project with `counts: {chats: null, files: 4}` renders the substring `"4 files"` and that the card's `textContent` does **not** contain `"0 chats"` — a fabricated zero must never reach the card again.
14. `grep -rn "ChatsSection" packages/chat-surface/src apps` returns **zero** matches, and `git diff --exit-code -- packages/chat-surface/src/destinations/chats/` reports no change: this PRD extracts no chats section component and does not touch PRD-09's files (C16).
15. `apps/desktop/renderer/destinationBinders.test.tsx` asserts that the desktop `ProjectDataPort.listProjectChats("p1")` issues exactly one `Transport` request whose path contains `filter[project_id]=p1`, and that the returned row carries `model` and `status` (i.e. it is mapped by `toChatArchiveRow`, not by a local projection). Desktop _reachability_ of the detail view (`focusedProjectId` + `renderDetail`) is PRD-10 DoD 9, not this PRD.
16. `npm run typecheck --workspace @0x-copilot/api-types && npm run typecheck --workspace @0x-copilot/frontend && npm run typecheck --workspace @0x-copilot/desktop` all exit 0 with `chats: number | null`.
17. The projects harness measures the real path instead of a mock: `grep -rn "fetchProjectActivity" tools/design-parity` returns zero lines (it is mocked today at `lib/render-live-projects.test.tsx:79` and `:334`) and `lib/render-live-projects.test.tsx` feeds the detail state from `ProjectDataPort` fixtures. Regenerating `tools/design-parity/surfaces/projects/out/report-detail.md` (procedure: `tools/design-parity/SKILL.md`) on this PR's **merge base** and on this PR adds **no** line under the `## HIGH` heading — `git diff --exit-code` on the regenerated report shows removals and rewrites only. The `detail.chatrow.*` anchors flipping from `missing-in-live` to matched is PRD-10 DoD 17's gate; this PRD's obligation is that the data is there for it.
18. `make test` passes at the repo root.

## Dependencies

This PRD is **wave 2**, and inside wave 2 it lands **after PRD-08** (README
implementation order): both edit `apps/desktop/renderer/destinationBinders.tsx`, and
PRD-08 deletes the audit fan-out block PRD-07 would otherwise re-touch.

**Must land first**

- **PRD-03 (host binder seam / shared conversation projection).** This PRD's chat list
  consumes PRD-03's per-row projector `toChatArchiveRow`
  (`packages/chat-surface/src/projections/chats.ts`). Per README C8, PRD-03 ships the
  **per-row function only** — bucketing, fetching and paging are PRD-09's, so PRD-07
  must not consume or re-derive a `bucketConversations`. If PRD-03 has not landed,
  PRD-07 moves the per-row projector as part of its own scope; it must not become the
  third copy alongside `chatsApi.ts:104` and `destinationBinders.tsx:169`.
- **PRD-02 (status chip).** The `status` value PRD-07 puts on each `ChatArchiveRow` is
  rendered as PRD-02's chip once PRD-10 D6 lands the row markup. PRD-07 supplies the
  field; it neither defines nor styles the chip.
- **PRD-05 (run history backend) — order only.** No behavioural dependency, but PRD-05
  edits the same five files first: `packages/api-types/src/index.ts`,
  `agent_runtime/api/conversation_query_service.py`, `runtime_api/http/routes.py`,
  `runtime_adapters/*/runtime_api_store.py` and `backend_facade/app.py`
  (order 05 → **07** → 09 → 12).

**Independent but adjacent**

- **PRD-10 (Projects surface: `_shared/Page`, `BackLink`, `ProjectIconTile`, one
  Projects list, `.ui-grid3`, and the detail row markup).** C21 corrects this PRD's
  earlier "PRD-05" references. PRD-10 is wave 4 and **owns the markup** in
  `ProjectDetailView.tsx` and `ProjectsDestination.tsx` (C16); PRD-07 lands the props
  and the data first and does not restyle. PRD-10 also owns the desktop detail's
  reachability and the `icon_emoji` → monogram fix.
- **PRD-09 (Chats surface)** owns `ChatsArchive.tsx` and deletes both host copies of the
  row projection. PRD-07 touches no `destinations/chats/*` file.

**This unblocks**

- Filing a chat into a project from the composer (the column and the facade
  passthrough are the prerequisite; the UI is a product decision).
- _PRD-Library-Persistence_ — with the project-scoped read bound, the Files section
  goes from "coming soon" to real content the moment the library gains a durable store
  and an upload path. No client change required.
- Any project-scoped filtering on Todos / Inbox / Routines: the `ProjectRollupSource`
  registry is the same seam their list filters will use.
