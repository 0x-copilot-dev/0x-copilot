# Library Destination — Sub-PRD

**Status:** draft (2026-05-18)
**Owner:** parth (orchestrator) — implementation delegated to phase-7 impl agents
**Master:** [destinations-master-prd.md §5.5](../destinations-master-prd.md#55-library-library)
**Foundation:** [PRD.md](../PRD.md) — workspace shell + composer + thread canvas
**Binding cross-PRD decisions:** [cross-audit.md](../cross-audit.md) — `ItemRef` incl. `library_file` / `library_page` / `library_dataset` / `library_entity` / `library_item` (§1.1), `FilePickerPort` + `ClipboardPort` + `NotificationPort` (§1.2), **project-scoped ACL master rule consumed via `services/backend/src/backend_app/projects/acl.py::is_project_member`** (§1.3), audit `context` (§1.4), filter axis OR (§1.5), `<PageHeader>` (§1.6), branded `LibraryFileId` / `LibraryPageId` / `LibraryDatasetId` / `LibraryEntityId` / `LibraryItemId` (§2.1), `SectionResult<T>` (§2.3), `<ItemLink>` registry (§3.3), cascade default (§5.3), SSE convention (§5.2), **token-usage attribution via existing `Purpose` enum extended with `LIBRARY_RETRIEVAL` and `LIBRARY_INDEXING`** (§5.5)

**Reads from / consumed by:**

- [destinations/projects-prd.md](projects-prd.md) — Library items carry optional `project_id`; project-scoped read is enforced by the canonical resolver shipped in Phase 6 P6-A1 at `services/backend/src/backend_app/projects/acl.py::is_project_member`. Library imports it; no reimplementation.
- [destinations/chats-canvas-prd.md](chats-canvas-prd.md) — agent replies cite Library items via `<ItemLink kind="library_file|library_page|library_dataset">`. "Save to Library" CTA on tool results, agent messages, and pinned chat ranges emits Library items.
- [destinations/routines-prd.md](routines-prd.md) — Routines whose `output_target.kind="library_page"` write back into Library; project-scoped Routines write into project-scoped pages.
- [destinations/inbox-prd.md](inbox-prd.md) — ingest-completion notifications (large dataset indexing finished / failed) appear as Inbox items kind=`info` / `error`.
- [destinations/home-prd.md](home-prd.md) — recently-saved + recently-accessed Library items appear on Home; Library is a producer for Home's aggregation.
- [destinations/todos-prd.md](todos-prd.md) — todos may carry a `library_*` `ItemRef` as their `source` (e.g. "Review the Q3 forecast dataset I uploaded").

**Implementation phasing:** [implementation-plan.md](../implementation-plan.md) §2 Phase 7 row — Phase 7 ships P7-A1 (backend metadata + Postgres), P7-A2 (blob store + signed-URL upload/download), P7-A3 (retrieval pipeline: embeddings + hybrid search), P7-B1 (destination shell + list views), P7-B2 (detail views + previews), P7-C (frontend wiring + cross-destination "Save to Library").

**Design references:**

- master PRD §5.5 — premise + open questions ("vector store choice", "page editor", "versioning").
- `/tmp/atlas-design/0x-copilot-template/project/dest-library.jsx` — design reference.
- chat1.md library quick-link in the command palette; agents cite library items in their replies.
- Current stub: `packages/chat-surface/src/destinations/library/LibraryDestination.tsx` (Wave 0 stub with Adapters / Results / Knowledge tabs that don't fetch).

---

## §1 Premise + user job

### 1.1 What Library is

The Library is the **user's knowledge surface** — the place where everything Atlas has helped them learn, capture, or ingest is preserved, searchable, and re-usable. Concretely, it is the union of three kinds of artefact:

1. **Files** (`LibraryFile`) — bytes uploaded by the user, saved by an agent, or pulled by a connector. PDFs, slides, sheets, images, raw docs. Metadata in Postgres; bytes in an S3-compatible object store; signed-URL retrieval.
2. **Pages** (`LibraryPage`) — markdown writeups: knowledge cards, briefings, summaries the user (or an agent on their behalf) wrote. Notion-style "I learned X" surface. Stored as text in Postgres with versioned edits.
3. **Datasets** (`LibraryDataset`) — structured tabular data: CSV / Parquet / JSON-Lines. Schema-aware (column specs + row counts); blob in object store; cell-grid preview lazily computed.

Every artefact is **embedded** at write time into a per-tenant vector index (pgvector — see §6.5) and **indexed** into a Postgres tsvector for keyword search. The Library's retrieval endpoint runs a **hybrid (BM25 + vector cosine) search** with optional cross-encoder re-ranking. Chat / Run citations from the LLM resolve to `ItemRef { kind: "library_*" }` and render via `<ItemLink>`.

A Library item is the answer to: _"I learned something useful or captured something important, and I want to be able to find it again, share it, or have an agent re-use it later."_

### 1.2 Why a separate destination — and not "just file storage"

Three reasons, in priority order:

1. **It's a knowledge surface, not a drive.** Drives organize bytes by folder. Atlas organizes knowledge by **what you can do with it** — search it, cite it from a chat, file it under a project, hand it to an agent. The kind (file / page / dataset) is a storage detail. The user never has to think "is this a file or a page" to find it; **search and the All-view subsume the kind**. The kind discriminator surfaces only when it affects what the user can do with the item (e.g. edit a page; preview a dataset's columns; download a file).
2. **It's the corpus Atlas knows about.** Every retrieval an agent does (cite a doc, look up a fact, summarize a dataset) reads from here. Without a canonical Library, every other destination grows its own attachment surface and the retrieval pipeline forks. Master §2.2 "one source of truth per destination" requires it.
3. **It's the destination cross-other-destinations save into.** A useful chat range, an exec-summary an agent drafted, a Salesforce export — these become Library items via a one-click "Save to Library" CTA. The Library is where institutional knowledge accumulates so the user (and the user's teammates, via project filing) can re-find it.

Library is the **3rd workspace destination** in the original 11-destination plan (master §2 row 3), now the 14th in implementation order behind Home / Todos / Inbox / Routines / Projects (master §8 phase plan; Phase 7 = Library).

### 1.3 What Library is NOT

| Anti-goal                             | Why not                                                                                                                                                                                                                                                                                                                    |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A drive / file manager**            | No folders, no nesting, no hierarchical mount. Filing axis is the optional `project_id`. Users tag, file, or rename — they don't nest. Master §3.5 + `inbox-prd.md` antigoals: flat is the default.                                                                                                                        |
| **A version-control system**          | Pages have versions (every save = a version; GC old-versions to last-10 per page after 30d — master §5.5 open question Q3, resolved here in §11.2). Files are immutable (re-upload = new file). Datasets are immutable per ingest (re-ingest = new dataset). No branching, no merging, no diff-resolve UX.                 |
| **A wiki**                            | No backlinks, no graph, no "what links here". The cross-destination link surface is the universal `<ItemLink>` + `ItemRef` from cross-audit §1.1 — anyone can paste a `library_*` ref into a chat / todo / inbox-reply, but the Library does not maintain its own internal hyperlink graph.                                |
| **A real-time editor / Notion clone** | Pages are markdown. Single editor at a time. Last-write-wins on save with an optimistic-concurrency `If-Match: <version_etag>` header — concurrent saves return 409 and prompt the user to merge. No multiplayer cursors. (Cross-audit §3.5 deferred-features inventory: multiplayer threads → Wave 5; same posture here.) |
| **A connector**                       | Library doesn't auth into Notion / Drive / Sharepoint itself. Connectors do that, then **push** docs into Library as `LibraryFile` / `LibraryPage` items with `source.kind="connector_sync"`. Library is the destination of ingest, not the ingest mechanism.                                                              |
| **A search-everything bar**           | Library search covers Library items only. Searching across chats, todos, inbox, library is the ⌘K command palette's job (Phase 11). Library's own search endpoint is a primitive the palette consumes.                                                                                                                     |
| **A retrieval framework**             | Library exposes a search endpoint and an embedding pipeline. It does NOT expose configurable retrieval pipelines, prompt templates for RAG, or "ask your library" chat surfaces — chats with retrieval are the Chats destination's job; Library is the corpus.                                                             |
| **A storage-tier admin surface**      | Hot / cold tier transitions, object-store bucket policies, KMS key rotation — those are deployment / SRE concerns. Library's API exposes signed URLs; the host infra makes tier decisions.                                                                                                                                 |
| **A cross-tenant share hub**          | Cross-audit §3.5 deferred-features: cross-tenant sharing → never. Library items live inside one tenant. External-share links are explicitly out of scope for Phase 7 (see §16 Q8).                                                                                                                                         |

### 1.4 User journeys — the seven that drive the design

These are the workflows the user actually performs. The data shape, endpoints, and UI in §3–§9 all defer to these.

#### J1. _"I had a useful chat last week — let me find what I learned."_

The user opens the workspace, types in the Library search bar `cohort retention metrics`. The hybrid search (BM25 + vector) returns:

- the saved knowledge page _"Cohort retention — Q3 product review"_ (saved from a chat 6 days ago),
- the original chat run (cross-destination result; shown but tagged "in Chats, not Library"),
- a PDF the user uploaded called _"Retention deep dive — Acme.pdf"_.

The user clicks the page → opens detail view → re-reads → re-cites it into a fresh chat by pasting the `library_page` ItemRef. The agent in the new chat opens the page automatically as context. **This is the most-used journey**; the destination header optimises for it: large search bar, recently-accessed items pinned, kind-agnostic results.

#### J2. _"Save this tool result for later."_

The user is in a chat. The agent ran a Salesforce query and returned 200 rows of opportunity data. The user clicks the **"Save to Library"** affordance on the tool-result card (one click, no modal — see §10). A `LibraryDataset` is created with:

- `name` defaulting to the tool call's display title,
- `source = { kind: "agent_save", chat_id, run_id, message_id, tool_call_id }`,
- the data persisted as Parquet in the object store (Atlas serialises the tool result),
- schema auto-derived,
- `project_id` inherited from the chat if filed.

The dataset is now searchable. The user can return to it from `/library`, cite it in a new chat, or attach it to a Routine.

#### J3. _"Build a knowledge card from this chat."_

The user is reading an agent's summary in a chat. The summary is the gold — they want to keep it. They highlight the message range and click **"Save as knowledge page"**. A `LibraryPage` is created in markdown form, prefilled with:

- title: derived from the first heading of the agent's message (editable inline),
- body: the highlighted markdown,
- `source = { kind: "agent_save", chat_id, run_id, message_id, range: { start, end } }`.

The user lands on the page detail view in **edit mode** to rename / curate before saving. On save, the page is embedded + indexed.

#### J4. _"Search across everything I've saved."_

The user opens `/library`, lands on **All** view (default — kind-agnostic). The search bar is autofocused. Typing yields results ranked by hybrid retrieval, with kind chips. Filter chips ("Files / Pages / Datasets") narrow; project chips narrow further. The full-text + vector hybrid lets queries like _"that quarterly memo about margin compression"_ resolve to the right page even when the user doesn't remember the exact title.

#### J5. _"Connect a Notion / Drive workspace and pull docs in."_

The user clicks **"Connect a source"** in the Library panel CTA. They are routed to `/connectors` (Phase 10's connectors destination) to OAuth with Notion. After auth, the connector enumerates accessible pages and offers a **sync scope picker** (one workspace or multiple; folders within). Sync is performed by Connectors → Library push (§14). The user returns to `/library`, sees a "Notion sync in progress" pill on the Library panel (`SectionResult<T>` aware — partial-success aggregation), and after completion the new pages appear under `source.kind = "connector_sync"`. **Phase 7 ships the Library-side ingest endpoint and wire shape; Phase 10 ships the connector that calls it.** See §14.

#### J6. _"Find the dataset I uploaded for the Q3 forecast."_

The user has uploaded a CSV three weeks ago. They open `/library`, click the **Datasets** filter, sort by recency. The list shows row counts, column previews, source attribution ("user_upload"). Clicking opens the detail view with a virtualized **cell grid** showing the first 200 rows (lazy-loaded via the preview endpoint). A "Download CSV" button issues a signed URL.

#### J7. _"Share a knowledge card with my project members."_

The user has a knowledge page; they want their two project mates to read it. They open the page, click **"File under project"**, select `Acme renewal`. The page now carries `project_id = acme-renewal`; cross-audit §1.3 ACL kicks in: AE and engineer (project members) can read; only the owner can mutate. Project members see it under `/projects/acme-renewal` → Library tab AND under their own `/library` (filter by project). No URL-sharing flow; the share happens through project membership, which is auditable.

### 1.5 User success states (what "done" looks like)

- _"I never lose a useful insight again."_ → Every chat with a saveable agent message has a one-click "Save to Library" affordance. Pages accumulate at zero friction.
- _"My agents cite my own corpus."_ → Library is the default retrieval store for chat agents (via `<ItemLink>` + the retrieval endpoint in §6). Agents quote project-scoped library items first when the chat is filed under a project.
- _"I find what I learned even when I don't remember the title."_ → Hybrid search resolves fuzzy / semantic queries; results are kind-agnostic and ranked.
- _"My team's institutional knowledge is reachable to teammates without me forwarding files."_ → Project filing + project-scoped ACL.
- _"I onboard a teammate by adding them to two projects."_ → Library items inherit project ACL automatically; no per-item share dance.

---

## §2 Source-of-truth map

Per master PRD §2.2, each artefact has **exactly one** canonical location.

| Concern                                                                                              | Canonical file                                                                                                                                                                                                                                                                                            | Status                                                   |
| ---------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ | --------- |
| Wire types                                                                                           | `packages/api-types/src/library.ts` (NEW)                                                                                                                                                                                                                                                                 | introduce; re-export from `index.ts`                     |
| Branded `LibraryFileId` / `LibraryPageId` / `LibraryDatasetId` / `LibraryEntityId` / `LibraryItemId` | `packages/api-types/src/brands.ts` (cross-audit §2.1)                                                                                                                                                                                                                                                     | already promised by SP-1; LibraryItemId already declared |
| `<ItemLink kind="library_file"                                                                       | "library_page"                                                                                                                                                                                                                                                                                            | "library_dataset">` resolvers                            | `packages/chat-surface/src/destinations/library/index.ts` — registers three resolvers via `registerItemRefResolver(...)` | introduce |
| Destination (router-mounted)                                                                         | `packages/chat-surface/src/destinations/library/LibraryDestination.tsx` (REWRITE — replaces Wave 0 stub)                                                                                                                                                                                                  | rewrite                                                  |
| Context panel                                                                                        | `packages/chat-surface/src/destinations/library/LibraryPanel.tsx` (NEW)                                                                                                                                                                                                                                   | introduce                                                |
| Detail per kind                                                                                      | `packages/chat-surface/src/destinations/library/detail/{LibraryFileDetail,LibraryPageDetail,LibraryDatasetDetail}.tsx` (NEW)                                                                                                                                                                              | introduce                                                |
| Page markdown editor                                                                                 | `packages/chat-surface/src/destinations/library/page/PageEditor.tsx` (NEW)                                                                                                                                                                                                                                | introduce                                                |
| Dataset cell-grid preview                                                                            | `packages/chat-surface/src/destinations/library/dataset/CellGrid.tsx` (NEW)                                                                                                                                                                                                                               | introduce                                                |
| File preview (PDF / image / fallback)                                                                | `packages/chat-surface/src/destinations/library/file/FilePreview.tsx` (NEW)                                                                                                                                                                                                                               | introduce                                                |
| Search box + result list                                                                             | `packages/chat-surface/src/destinations/library/search/{LibrarySearchInput,LibrarySearchResults}.tsx` (NEW)                                                                                                                                                                                               | introduce                                                |
| Save-to-Library popover (cross-destination)                                                          | `packages/chat-surface/src/destinations/library/save/SaveToLibraryPopover.tsx` (NEW)                                                                                                                                                                                                                      | introduce                                                |
| Upload widget (uses `FilePickerPort`)                                                                | `packages/chat-surface/src/destinations/library/upload/UploadButton.tsx` (NEW)                                                                                                                                                                                                                            | introduce                                                |
| Backend route module                                                                                 | `services/backend/src/backend_app/library/` (NEW): `routes.py`, `service.py`, `store.py`, `schema.py`, `acl.py`, `events.py`, `blob_store.py`, `search.py`, `embeddings.py`, `dataset_ingest.py`                                                                                                          | introduce                                                |
| Backend Postgres schema                                                                              | `services/backend/src/backend_app/library/schema.py` + Alembic migration                                                                                                                                                                                                                                  | introduce                                                |
| Facade proxy                                                                                         | `services/backend-facade/src/backend_facade/library_routes.py` (NEW)                                                                                                                                                                                                                                      | introduce                                                |
| Internal cross-service ingest endpoint                                                               | `services/backend/src/backend_app/library/internal_routes.py` (NEW) — `/internal/v1/library/files` / `/internal/v1/library/pages` / `/internal/v1/library/datasets` (Routines + Connectors → Library)                                                                                                     | introduce                                                |
| Embedding worker (out-of-band indexer)                                                               | `services/backend/src/backend_app/jobs/library_indexer.py` (NEW) — claims `pending_embedding` rows; calls `build_chat_model` for the embedding model with `Purpose.LIBRARY_INDEXING`; writes pgvector                                                                                                     | introduce                                                |
| Retention cleanup cron                                                                               | `services/backend/src/backend_app/jobs/library_retention.py` (NEW)                                                                                                                                                                                                                                        | introduce                                                |
| Search index builder                                                                                 | the embedding worker also maintains the Postgres `tsvector` column via a generated column / trigger (§5.2)                                                                                                                                                                                                | introduce                                                |
| Token-usage `Purpose` extension                                                                      | `services/ai-backend/src/agent_runtime/observability/attribution.py` — add `LIBRARY_RETRIEVAL` and `LIBRARY_INDEXING` enum values; cross-audit §5.5 explicit                                                                                                                                              | extend                                                   |
| LLM client wrapper (embedder, re-ranker, query planner)                                              | uses canonical `build_chat_model` in `services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py` — **NO new direct LLM provider imports.** A new internal RPC `POST /internal/v1/llm/embed` lives in ai-backend; backend's `library_indexer.py` calls it over HTTP per service-boundary rule. | extend                                                   |
| Frontend HTTP wrappers + SSE                                                                         | `apps/frontend/src/api/library.ts` (NEW)                                                                                                                                                                                                                                                                  | introduce                                                |
| App switch case (mount destination)                                                                  | `apps/frontend/src/app/App.tsx` (extend)                                                                                                                                                                                                                                                                  | extend                                                   |
| `ShellDestinationSlug` extension                                                                     | `packages/chat-surface/src/shell/destinations.ts` — `"library"` slug already exists in the Wave 0 stub; no extension needed                                                                                                                                                                               | already shipped                                          |

A second copy of any of these is a bug.

---

## §3 Architecture

### 3.1 Layout

Standard workspace shell from `ChatShell.tsx` with `<LibraryPanel>` in the ContextPanel slot. Right rail collapsed for this destination (PRD §10 default; Library does not opt in). List vs detail vs editor pivot lives inside the main pane and is driven by `route.view` + `route.id` + `route.subview` per master §4.5:

- `{ view: null, id: null }` → **list** view (default All)
- `{ view: "files" | "pages" | "datasets" | "all", id: null }` → **list** view with kind filter pre-applied (deep-linkable)
- `{ view: null, id: <LibraryItemId> }` → **detail** view, dispatched per kind (the URL carries a kind-agnostic id and the backend tells the frontend which detail to render — see §4)
- `{ view: "edit", id: <LibraryPageId> }` → **page editor** (edit mode; only valid for pages)
- `{ view: "new-page", id: null }` → **page editor** (create mode)
- `{ view: "search", id: null }` → **search results** view (when navigated from ⌘K or panel)

Matches master §4.5 routing convention `/<dest>/<view?>/<id?>`. The destination shell tabs (Files / Pages / Datasets / All) map to `view = "files" | "pages" | "datasets" | "all"`.

### 3.2 List view (`/library`, default All)

`LibraryDestination.tsx` (when `route.id == null`, `route.view ∈ {null, "all", "files", "pages", "datasets"}`) renders, top to bottom:

1. `<PageHeader title="Library" subtitle="Files, pages, and datasets you've saved" primaryAction={{ label: "New page", onClick: createNewPage }} badges={[totalCount, pendingIndexCount > 0 ? <Pill tone="info">{pendingIndexCount} indexing</Pill> : null]} actions={<UploadButton /><ConnectCTA />} />` (cross-audit §1.6 shape).
2. **Recently-accessed strip** — a compact horizontal scroller of up to 8 cards, sorted by `last_accessed_at desc`. Each card is `<ItemLink>`-clickable. **Reads from `GET /v1/library/recents`** (a cheap aggregation endpoint; §4.2). Visible on All only; suppressed for kind-filtered views to keep the page calm.
3. **Search bar** — large, autofocused on first paint. Debounced 250ms. Submits to `POST /v1/library/search`. Results render inline below the bar when query is non-empty (hides the regular list); empty query → regular list.
4. `<FilterTabs value={kindFilter} options={["all","files","pages","datasets"]} counts={countsByKind} />` — multi-value OR semantics per cross-audit §1.5. Selecting a tab updates the URL view segment for deep-linking.
5. **Refinement chip row** — secondary filters: `Project` (multi-select), `Source` (multi-select: user_upload / agent_save / connector_sync), `Date range`, `Tag` (multi-select; tags-as-strings — see §5.1 `tags`). Each chip is a filter axis per cross-audit §1.5.
6. **List body** — **CardGrid view** by default (3-up on desktop, 2-up on tablet, 1-up on mobile) using the SP-1 `<CardGrid>` primitive; user can toggle to **DocList view** (compact list rows) via a header toggle. Virtualized when total > 100 (reuse `@tanstack/react-virtual` introduced by Inbox/Todos).

Per-card content (default `<CardGrid>` view):

```
[ kind icon ]  [ Item name                              [⋆ pin] ]
               [ subtitle: source attribution + project chip    ]
               [ thumbnail / first-line preview / column count  ]
               [ updated relative-time · size or row-count chip ]
               [ hover actions: ▶ Open · ⤓ Download (file/dataset only) · 📦 File-to-project · 🗑 Delete ]
```

- Kind icon uses a destination token: doc / image / pdf / sheet / slide for files, page-glyph for pages, dataset-glyph for datasets. Cross-audit §1.6 color-token rule: status colors (error tone on failed-index) accompany an icon + text.
- "Updated" shows `formatRelativeTime(updated_at, now)` (cross-audit §3.4).
- Project chip uses `<ItemLink kind="project">` when `project_id` is set; clicking navigates to the project detail.

#### 3.2.1 Why CardGrid by default and DocList opt-in

Cards optimise for **browsing your knowledge surface** — thumbnails of file/page/dataset contents make recognition fast ("I recognise that page by its first paragraph; I don't need to read the title"). DocList optimises for **scanning lots of items**: when the user has > 200 items, the toggle preserves a familiar list affordance. The default is the recognition-first surface because Library is a knowledge surface, not an admin table — the user is "remembering what I learned", not "auditing rows".

### 3.3 Panel view (`LibraryPanel.tsx`)

`LibraryPanel.tsx` composes the generic `<ContextPanel title="Library" subtitle="What you've saved, in one place">`. Sections, top to bottom:

1. **Primary CTAs** — three buttons (full-width, stacked): `[+ Upload file]` (opens `FilePickerPort` per cross-audit §1.2) · `[+ New page]` (navigates to `/library/new-page`) · `[+ Connect a source]` (deep-links to `/connectors`).
2. **Quick filters** — same axes as main FilterTabs, listed vertically with counts; cross-audit §1.5 multi-value OR.
3. **Pinned items** — collapsible list of user-pinned items (max 20 per user; UI caps; backend enforces). Per-user pin stored in `library_pins` table (§5.1).
4. **By project** — collapsible groups: each project the user has Library items in, with item count. Click → applies `filter[project_id]=<id>`.
5. **By source** — `user_upload (n)` · `agent_save (n)` · `connector_sync (n)`. Click filters.
6. **Recently accessed** — last 10 items the user opened (per-user `library_access_log` — see §5.1).
7. **Saved searches** — same primitive as Inbox §3.3; ≤ 20 per user. (Library is search-heavy; saved searches matter.)
8. **Footer** — `[+ Upload]` repeat CTA + link to "Library guide" doc + `[Connect Notion / Drive / SharePoint]` deep-link.

### 3.4 Detail view (`/library/<id>`)

The detail view dispatches per kind. The URL carries a kind-agnostic `LibraryItemId` (see §4.1); the backend's `GET /v1/library/<id>` response embeds the `kind` discriminator and the frontend renders the corresponding detail component.

#### 3.4.1 `LibraryFileDetail`

Layout (top to bottom):

1. **Header** — kind icon + name + `[Edit metadata]` + `[⋯]` menu (with: rename · file-to-project · tags · delete). Action row: `[Download] [Cite in chat] [Pin] [File under project]`.
2. **Preview pane** — left 2/3:
   - PDF → embedded PDF.js viewer (lazy-loaded), first page rendered eagerly, subsequent on scroll.
   - Image → `<img>` with signed URL.
   - Doc / sheet / slide → first-N-pages thumbnail strip (server pre-rendered at ingest; see §6.4); "Open in source app" CTA for connector-sourced files.
   - Fallback → metadata-only with `[Download]` and a "Preview not available" hint.
3. **Metadata panel** — right 1/3: name, mime, size, source attribution (with `<ItemLink>` back to originating chat / run / tool / connector), project chip, tags, created/updated relative time, indexing status (`indexed` / `indexing` / `failed` — error chip with retry CTA when failed).
4. **Citations** — a small chip row: "Cited in 3 chats" → expandable list of `<ItemLink kind="chat">` rows (a back-index maintained by the projector — see §6.6).
5. **Audit log** — last 20 audit rows for `target_kind="library_file"`, `target_id=<id>`. See §7.

#### 3.4.2 `LibraryPageDetail` and `PageEditor`

The page is the most interactive Library kind. Detail view is read-only; editor is the same component in `mode="edit"`.

Read-only layout:

1. **Header** — title + `[Edit]` + `[⋯]` (rename · duplicate · file-to-project · tags · delete · view versions). Action row: `[Cite in chat] [Pin] [File under project] [View versions]`.
2. **Body** — rendered markdown (using the existing `@0x-copilot/design-system` markdown renderer; the same one chat messages use). Headings get auto-anchor IDs for in-page navigation. Cross-destination links (`<ItemLink>` embedded in markdown) resolve at render time.
3. **Metadata panel** — same shape as file: source attribution, project chip, tags, indexing status, citation back-index, audit log.

Editor layout (`PageEditor.tsx`):

1. **Title input** — single line, autofocus on create.
2. **Markdown textarea** — split-pane (edit / preview side-by-side on desktop; toggle on tablet/mobile). Auto-save **every 30s on dirty** AND on explicit save. Each save creates a version row (§11.2).
3. **Optimistic concurrency** — `If-Match: <version_etag>` header on save; 409 on conflict triggers a "Someone (or another tab) updated this page" banner with "View their version" / "Overwrite" / "Cancel" buttons.
4. **`<MentionPopover>`** — typing `@` inserts an `ItemRef` link (master §4.6 — the shared mention typeahead component). The serialized markdown stores the ref as a fenced annotation that the renderer resolves; this preserves portability (the body remains plain markdown for a human reader who copy-pastes).

#### 3.4.3 `LibraryDatasetDetail`

Datasets need a structured view.

1. **Header** — dataset name + `[Edit metadata]` + `[⋯]`. Action row: `[Download CSV] [Download Parquet] [Cite in chat] [Pin] [File under project]`.
2. **Schema panel** — column list with type, nullable, unique count (top-10), sample values. The schema panel is the **fastest way for the user to recall what a dataset contains**.
3. **Cell grid preview** — `<CellGrid>` virtualized (column-virtualized + row-virtualized via `@tanstack/react-virtual`). First 200 rows lazily fetched via `GET /v1/library/<id>/preview?rows=200`. Sort + filter on a single column (client-side over the loaded page); "Load more" pagination at 1000 rows max in the preview (full data via download).
4. **Provenance** — source attribution (`user_upload` from a chat's tool result, `agent_save` from a tool result, `connector_sync` from a connector).
5. **Indexing status** — datasets are embedded as **per-row summaries** by default (see §6.5); the indexing status indicates "n rows indexed of m total". Failed-index gets a retry CTA.
6. **Audit log** — last 20 audit rows.

### 3.5 Search view (`/library?q=…` or `/library/search`)

When the destination search bar is non-empty (or the user navigates from ⌘K to a search query), the list body is replaced by a **`LibrarySearchResults`** component:

1. Each result row is kind-agnostic — same row shape as the All list view — with two additions:
   - a **highlight excerpt** (the matched text from BM25 + the semantically-closest paragraph from the vector hit), wrapped in `<mark>` tags;
   - a **score / kind / source line**: `94 · page · agent_save · 2 weeks ago · Acme renewal`.
2. **Empty results** state: "No matches in your library for \"{q}\". Try a different query or [Connect a source]." with a `Connect-a-source` link.
3. **Slow-query SSE**: when the retrieval pipeline takes > 1.5s (re-ranking adds latency — see §6.3), the response is streamed: results land progressively as BM25 returns immediately, then the vector hits arrive, then the re-ranking re-orders. Frontend uses the SSE convention from cross-audit §5.2 with envelope kinds `library.search_bm25_result` / `library.search_vector_result` / `library.search_reranked` / `library.search_complete`.

### 3.6 Cross-destination "Save to Library" affordance

This is the **load-bearing UX of the destination** — Library only matters if items land in it cheaply.

#### 3.6.1 Where the affordance appears

- **On every tool-result card** (Chats destination) — a `[Save to Library]` button in the card's action row. Default `kind="dataset"` for structured tool results (JSON / table-shaped output); default `kind="file"` for binary tool outputs; default `kind="page"` for text-shaped output. The user can override via the popover (§3.6.2).
- **On every agent message** (Chats destination) — a `[Save as page]` action in the message action menu. Saves the rendered markdown of the message range; if the user highlights a sub-range first, only the highlighted text is saved.
- **On chat thread pin** (Chats destination) — when the user pins a thread for keep-around-ness, a `[Save thread summary as page]` action becomes available. Triggers an LLM summarisation via `Purpose.LIBRARY_INDEXING` (§13.3) and creates a `LibraryPage` with the summary plus a back-ref to the chat.
- **On a run-completion notification** (Inbox destination) — `[Save output to Library]` when the run produced a saveable artefact (file / dataset / structured output).
- **On a Routine output** — Routines whose `output_target.kind="library_page"` write directly to Library (the Routine's owner is the page owner; project-scoped if the Routine is filed).

#### 3.6.2 The `SaveToLibraryPopover`

One component, used by all of the above call sites. Composes:

- **Kind override** (file / page / dataset, defaults set per call site).
- **Name input** (prefilled from the source).
- **Project selector** (`<ProjectPicker>` from Projects Phase 6) — inherits the chat/run's `project_id` by default.
- **Tags multi-select** (free-text tags; chip input).
- **Source preview** — a small disclosure showing the bytes / markdown / first-rows that will be saved.
- **Save button** — single click commits; toast confirms with `<ItemLink>` to the new item.

The popover is rendered via the existing `<MentionPopover>` infra patterns (PRD §10 popover convention). No modal — the popover lives next to the originating affordance so the user doesn't lose context. **One click — popover open with smart defaults — Save → done.**

### 3.7 Empty states

Empty states matter because new users land on an empty Library. Master §3.10 requires actionable empty states.

| State                             | Renders                                                                                                                                                                                                                                                                                                                                                                                 |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **List empty (All)**              | A tutorial card titled "Your Library is empty" with three CTAs: `[+ Upload file]` · `[+ New page]` · `[+ Connect a source]`. Sub-line: "Library is where Atlas keeps your files, pages, and datasets. Save a tool result from a chat, write a knowledge page, or connect Notion to pull docs in." A short illustrated diagram (3 boxes: chat → save → library) makes the model legible. |
| **List empty (filter)**           | `<EmptyState icon="library" title="No {kind} match these filters" sub="Try clearing a filter or [Save your first {kind}]." action={{ label: "Clear filters", onClick: clearFilters }} />`.                                                                                                                                                                                              |
| **Search empty**                  | "No matches in your library for \"{q}\". [Connect a source] or [Clear search]."                                                                                                                                                                                                                                                                                                         |
| **Search empty (project-scoped)** | "No matches in {projectName}. Search across all projects?" with a "Search all" button.                                                                                                                                                                                                                                                                                                  |
| **Detail loading**                | Skeleton matching kind shape: file = thumbnail strip skeleton; page = title + paragraph blocks; dataset = column-header skeleton + 4 row skeletons.                                                                                                                                                                                                                                     |
| **Detail unauthorized / 404**     | "Not found" page with a `[Back to Library]` link; **`404` not `403`** per cross-audit §1.3 existence-not-leak.                                                                                                                                                                                                                                                                          |
| **Indexing failed**               | Detail header banner: "Indexing failed: {reason}. [Retry]" with a `routine.fire_skipped`-style audit context.                                                                                                                                                                                                                                                                           |
| **Upload in progress**            | Toast at top: "Uploading {n} of {m}: {filename}…" with progress bar; on completion, "✓ Uploaded. Indexing in background — searchable in ~30s."                                                                                                                                                                                                                                          |
| **Offline**                       | Banner: "You're offline — showing cached library items. New uploads will resume when you reconnect." Reads from `KeyValueStore` cache.                                                                                                                                                                                                                                                  |
| **Stale**                         | If last-fetch > 5 min AND SSE disconnected: top hint "Library may be out of date. Refresh." with refresh button.                                                                                                                                                                                                                                                                        |

---

## §4 Wire contracts (per master §3.5 + cross-audit §1.1 / §1.5 / §2.1 / §2.3)

### 4.1 Types (`packages/api-types/src/library.ts`)

```typescript
import type {
  LibraryFileId,
  LibraryPageId,
  LibraryDatasetId,
  LibraryItemId,
  ProjectId,
  UserId,
  TenantId,
  ConversationId,
  RunId,
  ConnectorId,
} from "./brands";
import type { ItemRef, SectionResult } from "./refs";

export type LibraryKind = "file" | "page" | "dataset";

export type LibrarySource =
  | { kind: "user_upload"; uploaded_by: UserId }
  | {
      kind: "agent_save";
      chat_id: ConversationId;
      run_id: RunId;
      message_id: string;
      tool_call_id?: string;
      range?: { start: number; end: number };
    }
  | {
      kind: "connector_sync";
      connector_id: ConnectorId;
      external_id: string;
      external_url?: string;
    };

export type LibraryIndexStatus =
  | "pending"
  | "indexing"
  | "indexed"
  | "failed"
  | "skipped";

export type LibraryFileMime =
  | "application/pdf"
  | "image/png"
  | "image/jpeg"
  | "image/gif"
  | "image/webp"
  | "image/svg+xml"
  | "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
  | "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
  | "application/vnd.openxmlformats-officedocument.presentationml.presentation"
  | "application/msword"
  | "application/vnd.ms-excel"
  | "application/vnd.ms-powerpoint"
  | "text/plain"
  | "text/markdown"
  | "text/csv"
  | "application/json"
  | "application/octet-stream";

export type LibraryFileKind =
  | "doc"
  | "image"
  | "pdf"
  | "sheet"
  | "slide"
  | "other";

export interface LibraryFile {
  readonly id: LibraryFileId;
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  readonly project_id: ProjectId | null;
  readonly kind: "file";
  readonly file_kind: LibraryFileKind;
  readonly name: string; // ≤ 200 chars
  readonly mime: LibraryFileMime;
  readonly size_bytes: number;
  readonly blob_ref: string; // opaque; backend resolves to signed URL via /preview / /download
  readonly thumbnail_blob_ref: string | null; // first-page thumbnail (server pre-rendered)
  readonly source: LibrarySource;
  readonly tags: ReadonlyArray<string>;
  readonly index_status: LibraryIndexStatus;
  readonly index_error: string | null;
  readonly checksum_sha256: string;
  readonly created_at: string;
  readonly updated_at: string;
  readonly last_accessed_at: string | null;
}

export interface LibraryPage {
  readonly id: LibraryPageId;
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  readonly project_id: ProjectId | null;
  readonly kind: "page";
  readonly title: string; // ≤ 200 chars
  readonly markdown: string; // body — content-served; ≤ 1 MB
  readonly version: number; // monotonic per page; bump on every save
  readonly version_etag: string; // optimistic-concurrency header value
  readonly source: LibrarySource;
  readonly tags: ReadonlyArray<string>;
  readonly index_status: LibraryIndexStatus;
  readonly index_error: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  readonly last_accessed_at: string | null;
}

export interface LibraryDatasetColumnSpec {
  readonly name: string;
  readonly type:
    | "string"
    | "integer"
    | "float"
    | "boolean"
    | "date"
    | "datetime"
    | "json"
    | "binary";
  readonly nullable: boolean;
  readonly sample_values?: ReadonlyArray<string>; // ≤ 5 sample reps
}

export interface LibraryDataset {
  readonly id: LibraryDatasetId;
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  readonly project_id: ProjectId | null;
  readonly kind: "dataset";
  readonly name: string; // ≤ 200 chars
  readonly description: string | null;
  readonly schema: ReadonlyArray<LibraryDatasetColumnSpec>;
  readonly row_count: number;
  readonly size_bytes: number;
  readonly blob_ref: string; // Parquet in object store
  readonly format: "parquet" | "csv" | "jsonl";
  readonly source: LibrarySource;
  readonly tags: ReadonlyArray<string>;
  readonly index_status: LibraryIndexStatus;
  readonly index_error: string | null;
  readonly checksum_sha256: string;
  readonly created_at: string;
  readonly updated_at: string;
  readonly last_accessed_at: string | null;
}

/** Discriminated union for kind-agnostic list endpoints. */
export type LibraryItem = LibraryFile | LibraryPage | LibraryDataset;

export interface LibraryListResponse {
  readonly items: ReadonlyArray<LibraryItem>;
  readonly next_cursor: string | null;
  readonly counts_by_kind: { file: number; page: number; dataset: number };
}

export interface LibrarySearchHit {
  readonly ref: ItemRef; // ref.kind ∈ {"library_file","library_page","library_dataset"}
  readonly snippet: string; // highlighted excerpt with <mark> tags
  readonly score: number; // 0..1 normalized fused score
  readonly score_breakdown: {
    readonly bm25: number | null;
    readonly vector_cosine: number | null;
    readonly reranker: number | null;
  };
  readonly source: LibrarySource;
  readonly project_id: ProjectId | null;
  readonly updated_at: string;
}

export interface LibrarySearchResponse {
  readonly query: string;
  readonly hits: ReadonlyArray<LibrarySearchHit>;
  readonly took_ms: number;
  readonly retrieval_strategy: "bm25_only" | "vector_only" | "hybrid";
  readonly reranker_used: boolean;
  readonly partial: SectionResult<{
    bm25_complete: boolean;
    vector_complete: boolean;
    rerank_complete: boolean;
  }>;
}

export interface LibraryRecentsResponse {
  readonly recently_saved: ReadonlyArray<LibraryItem>; // last 8 by created_at
  readonly recently_accessed: ReadonlyArray<LibraryItem>; // last 8 by last_accessed_at
}

export interface LibraryUploadInitRequest {
  readonly name: string;
  readonly mime: LibraryFileMime;
  readonly size_bytes: number;
  readonly checksum_sha256?: string;
  readonly project_id?: ProjectId;
  readonly tags?: ReadonlyArray<string>;
}

export interface LibraryUploadInitResponse {
  readonly file_id: LibraryFileId;
  readonly upload_url: string; // signed PUT URL (S3-compatible)
  readonly upload_headers: Record<string, string>;
  readonly expires_at: string;
}

export interface LibraryPageCreateRequest {
  readonly title: string;
  readonly markdown: string;
  readonly project_id?: ProjectId;
  readonly tags?: ReadonlyArray<string>;
  readonly source?: LibrarySource; // optional; defaults to user_upload
}

export interface LibraryPagePatchRequest {
  readonly title?: string;
  readonly markdown?: string;
  readonly tags?: ReadonlyArray<string>;
  readonly project_id?: ProjectId | null;
}

export interface LibraryDatasetCreateRequest {
  readonly name: string;
  readonly description?: string;
  readonly format: "parquet" | "csv" | "jsonl";
  readonly size_bytes: number;
  readonly checksum_sha256?: string;
  readonly project_id?: ProjectId;
  readonly tags?: ReadonlyArray<string>;
  readonly source?: LibrarySource;
}

export interface LibraryDatasetIngestResponse {
  readonly dataset_id: LibraryDatasetId;
  readonly upload_url: string;
  readonly upload_headers: Record<string, string>;
  readonly expires_at: string;
}

export interface LibraryPreviewResponse {
  readonly kind: LibraryKind;
  readonly file_preview?: {
    readonly thumbnail_signed_url: string;
    readonly page_count: number;
    readonly first_page_signed_url: string;
  };
  readonly page_preview?: {
    readonly title: string;
    readonly markdown_excerpt: string; // ≤ 4 KB
  };
  readonly dataset_preview?: {
    readonly schema: ReadonlyArray<LibraryDatasetColumnSpec>;
    readonly rows: ReadonlyArray<
      ReadonlyArray<string | number | boolean | null>
    >; // ≤ 200
    readonly total_rows: number;
  };
}

export interface LibraryVersion {
  readonly version: number;
  readonly etag: string;
  readonly created_at: string;
  readonly created_by: UserId;
  readonly diff_summary?: string; // optional auto-generated summary
}
```

### 4.2 Endpoints (facade — what apps call)

| Method | Path                                        | Purpose                                                                                                                                                                                                                    |
| ------ | ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/v1/library`                               | List (kind-agnostic by default). Filter axes: `kind` (multi-value OR per cross-audit §1.5), `project_id`, `source.kind`, `tag`, `owner_user_id`, `index_status`, `q`. Cursor pagination.                                   |
| GET    | `/v1/library/recents`                       | Recently-saved + recently-accessed for the destination header strip.                                                                                                                                                       |
| GET    | `/v1/library/{id}`                          | Single item; backend embeds `kind` discriminator. **404 not 403** per cross-audit §1.3.                                                                                                                                    |
| POST   | `/v1/library/files`                         | **Init upload.** Body: `LibraryUploadInitRequest`. Returns signed PUT URL + provisional `file_id` in `pending` index_status. Client then PUTs bytes directly to object store, then `POST /v1/library/files/{id}/finalize`. |
| POST   | `/v1/library/files/{id}/finalize`           | Confirm upload (server validates checksum + size); enqueues indexing.                                                                                                                                                      |
| POST   | `/v1/library/pages`                         | Create page with markdown body.                                                                                                                                                                                            |
| POST   | `/v1/library/datasets`                      | **Init dataset ingest.** Body: `LibraryDatasetCreateRequest`. Returns signed PUT URL. Server detects format on finalize, infers schema (or accepts explicit schema).                                                       |
| POST   | `/v1/library/datasets/{id}/finalize`        | Confirm upload; backend infers schema (or uses provided), counts rows, enqueues indexing.                                                                                                                                  |
| PATCH  | `/v1/library/{id}`                          | Mutate metadata (name / tags / project_id). For pages: pass `markdown` + `version_etag` (`If-Match` header) for body edit.                                                                                                 |
| DELETE | `/v1/library/{id}`                          | Soft delete; tombstone retained 90d (files) / 365d (pages — version history outlives the page).                                                                                                                            |
| POST   | `/v1/library/search`                        | Hybrid search. Body: `{ query, filters, max_hits, rerank }`. Response: `LibrarySearchResponse`.                                                                                                                            |
| GET    | `/v1/library/{id}/preview`                  | Lazy preview (thumbnail / markdown excerpt / cell-grid first 200 rows).                                                                                                                                                    |
| GET    | `/v1/library/{id}/download`                 | Returns a signed GET URL for the raw bytes (files / datasets). Audited per §7.1.                                                                                                                                           |
| GET    | `/v1/library/pages/{id}/versions`           | List versions (one row per save).                                                                                                                                                                                          |
| GET    | `/v1/library/pages/{id}/versions/{version}` | Fetch a historical version's markdown.                                                                                                                                                                                     |
| POST   | `/v1/library/{id}/pin`                      | Per-user pin/unpin (idempotent toggle).                                                                                                                                                                                    |
| POST   | `/v1/library/{id}/cite`                     | Record an access for "cited in chat X". Backend increments `last_accessed_at` and writes citation back-index row (§6.6).                                                                                                   |
| GET    | `/v1/library/search/stream`                 | SSE for slow-query progressive results (§3.5). Same auth as `/v1/library/search`.                                                                                                                                          |
| GET    | `/v1/library/stream`                        | SSE for live destination updates: `library.item_created`, `library.item_indexed`, `library.item_index_failed`, `library.item_updated`, `library.item_deleted`. `?after_sequence=N` reconnect per cross-audit §5.2.         |

### 4.3 Endpoints (internal — service-to-service)

Mounted under `/internal/v1/library/*` on the backend; consumed by `ai-backend` (chat-driven saves) and by the Connectors push pipeline (Phase 10).

| Method | Path                                | Purpose                                                                                                                                                                                                                                                                                                              |
| ------ | ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| POST   | `/internal/v1/library/files`        | Service-token ingest (Routine output, Connector sync). Body includes `source.kind` and explicit owner / project / tenant. Bypasses signed-URL handshake (caller posts bytes inline or supplies a server-side blob ref).                                                                                              |
| POST   | `/internal/v1/library/pages`        | Same for pages.                                                                                                                                                                                                                                                                                                      |
| POST   | `/internal/v1/library/datasets`     | Same for datasets.                                                                                                                                                                                                                                                                                                   |
| POST   | `/internal/v1/library/{id}/reindex` | Re-queue an item for embedding (admin / failure-retry).                                                                                                                                                                                                                                                              |
| POST   | `/internal/v1/llm/embed`            | **ai-backend** endpoint called by `library_indexer.py`. Routes through `build_chat_model` with the embedding model + `Purpose.LIBRARY_INDEXING`. Body: `{ texts: string[], model: string }`. Returns vectors. This is the only LLM call path for Library indexing; no direct provider import in `services/backend/`. |

### 4.4 Filter / sort allowlist (per cross-audit §1.5)

- `filter[kind]`: `file` | `page` | `dataset` (multi-value = OR).
- `filter[project_id]`: ProjectId (multi-value = OR).
- `filter[source.kind]`: `user_upload` | `agent_save` | `connector_sync` (multi-value = OR).
- `filter[tag]`: tag string (multi-value = OR).
- `filter[index_status]`: `pending` | `indexing` | `indexed` | `failed` | `skipped` (multi-value = OR).
- `filter[owner_user_id]`: UserId (multi-value = OR; project-scoped read still enforced).
- `filter[file_kind]`: `doc` | `image` | `pdf` | `sheet` | `slide` | `other` (multi-value = OR; only meaningful when `kind=file`).
- `sort`: `updated_at:desc` (default) | `created_at:desc` | `name:asc` | `name:desc` | `last_accessed_at:desc` | `size_bytes:desc`.
- `q`: full-text on the `tsvector` column (name + tags + description/title + first-2KB-of-body for pages / per-row summary for datasets — §6.2).

---

## §5 Storage + retention

Per master §3.3 + cross-audit §1.3 (project-scoped access).

### 5.1 Tables (Postgres, owned by `services/backend`)

**`library_files`** — file metadata. Bytes live in object store; this row is metadata + blob_ref.

| Column                                     | Type                          | Notes                                           |
| ------------------------------------------ | ----------------------------- | ----------------------------------------------- | --- | --- | --- | ----------------------------------------- |
| `id` / `tenant_id` / `owner_user_id`       | uuid PK / NN / NN             | Owner immutable post-create.                    |
| `project_id`                               | uuid NULL                     | Project filing per cross-audit §1.3.            |
| `name`                                     | text NN (≤200)                | User-visible.                                   |
| `file_kind`                                | text NN                       | enum {doc,image,pdf,sheet,slide,other}.         |
| `mime` / `size_bytes` / `checksum_sha256`  | text NN / bigint NN / text NN |                                                 |
| `blob_ref` / `thumbnail_blob_ref`          | text NN / text NULL           | Object-store keys (opaque to clients).          |
| `source`                                   | jsonb NN                      | matches `LibrarySource`.                        |
| `tags`                                     | text[] NN DEFAULT '{}'        |                                                 |
| `index_status` / `index_error`             | text NN / text NULL           | enum {pending,indexing,indexed,failed,skipped}. |
| `tsv`                                      | tsvector                      | generated column — `to_tsvector('simple', name  |     | ' ' |     | coalesce(array_to_string(tags,' '),''))`. |
| `created_at` / `updated_at` / `deleted_at` | timestamptz                   | `deleted_at` = soft-delete marker.              |
| `last_accessed_at`                         | timestamptz NULL              | updated by `POST /cite` and `GET /download`.    |

**`library_pages`** — page metadata + body.

| Column                                                          | Type                                    | Notes                                                            |
| --------------------------------------------------------------- | --------------------------------------- | ---------------------------------------------------------------- | --- | --- | --- | -------------------------- | --- | --- | --- | ---------------------------- |
| `id` / `tenant_id` / `owner_user_id` / `project_id`             | uuid PK / NN / NN / NULL                |                                                                  |
| `title`                                                         | text NN (≤200)                          |                                                                  |
| `markdown`                                                      | text NN (≤ 1 MB enforced at write-time) | body; canonical content.                                         |
| `version`                                                       | int NN DEFAULT 1                        | bumped on every save.                                            |
| `version_etag`                                                  | text NN                                 | random hex; rotated on every save. Optimistic-concurrency token. |
| `source` / `tags` / `index_status` / `index_error`              | as above                                |                                                                  |
| `tsv`                                                           | tsvector                                | generated — `to_tsvector('simple', title                         |     | ' ' |     | substring(markdown,1,2048) |     | ' ' |     | array_to_string(tags,' '))`. |
| `created_at` / `updated_at` / `deleted_at` / `last_accessed_at` | timestamptz                             |                                                                  |

**`library_page_versions`** — append-only version history.

| Column                                 | Type / Notes                                                                       |
| -------------------------------------- | ---------------------------------------------------------------------------------- |
| `id` / `page_id` / `tenant_id`         | uuid; `page_id` FK ON DELETE CASCADE                                               |
| `version` / `etag`                     | int / text                                                                         |
| `title_snapshot` / `markdown_snapshot` | text NN                                                                            |
| `created_at` / `created_by`            | timestamptz / uuid (UserId)                                                        |
| `diff_summary`                         | text NULL — optional LLM-summarised one-liner (Wave 7 opt-in; nullable until then) |

**`library_datasets`** — dataset metadata + schema. Bytes (Parquet) in object store.

| Column                                                          | Type                     | Notes                                              |
| --------------------------------------------------------------- | ------------------------ | -------------------------------------------------- | --- | --- | --- | ------------------------ | --- | --- | --- | ------------------------- | --- | --- | --- | ---------------------- |
| `id` / `tenant_id` / `owner_user_id` / `project_id`             | uuid PK / NN / NN / NULL |                                                    |
| `name` / `description`                                          | text NN / text NULL      |                                                    |
| `schema`                                                        | jsonb NN                 | array of `LibraryDatasetColumnSpec`.               |
| `row_count` / `size_bytes` / `checksum_sha256`                  | bigint / bigint / text   |                                                    |
| `blob_ref` / `format`                                           | text NN / text NN        | format ∈ {parquet,csv,jsonl}; canonical = parquet. |
| `source` / `tags` / `index_status` / `index_error`              | as above                 |                                                    |
| `tsv`                                                           | tsvector                 | generated — `to_tsvector('simple', name            |     | ' ' |     | coalesce(description,'') |     | ' ' |     | array_to_string(tags,' ') |     | ' ' |     | schema_columns_text)`. |
| `created_at` / `updated_at` / `deleted_at` / `last_accessed_at` | timestamptz              |                                                    |

**`library_embeddings`** — pgvector embeddings, one row per chunk.

| Column                      | Type / Notes                                                                                                                         |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `id` / `tenant_id`          | uuid; tenant-first index per §6.5                                                                                                    |
| `target_kind` / `target_id` | text / uuid — points to file/page/dataset id                                                                                         |
| `chunk_ordinal`             | int — chunk index within the target                                                                                                  |
| `chunk_text`                | text — the chunk used for embedding (≤ 4 KB; stored for explainability + re-rank input)                                              |
| `embedding`                 | vector(1536) — pgvector type; model + dimension pinned per `model_id`                                                                |
| `model_id`                  | text NN — embedding model identifier (e.g. `text-embedding-3-small`); rows from different models are co-stored but queried per model |
| `created_at`                | timestamptz                                                                                                                          |

**`library_pins`** — per-user pinned items.

| Column                                                | Type / Notes |
| ----------------------------------------------------- | ------------ |
| `tenant_id` / `user_id` / `target_kind` / `target_id` | composite PK |
| `pinned_at`                                           | timestamptz  |

**`library_access_log`** — per-user access events for the "Recently accessed" panel section.

| Column                                                       | Type / Notes                                       |
| ------------------------------------------------------------ | -------------------------------------------------- |
| `id` / `tenant_id` / `user_id` / `target_kind` / `target_id` | uuid; (tenant_id, user_id, accessed_at desc) index |
| `accessed_at`                                                | timestamptz                                        |
| `access_kind`                                                | text — `open` / `download` / `cite`                |

**`library_citations`** — citation back-index (chat or run cites a library item).

| Column                                           | Type / Notes                                               |
| ------------------------------------------------ | ---------------------------------------------------------- |
| `id` / `tenant_id` / `target_kind` / `target_id` | uuid + FK-by-convention to file/page/dataset               |
| `citer_kind` / `citer_id`                        | text + uuid — `chat` / `run` / `subagent` / `routine_fire` |
| `cited_at` / `cited_by_user_id`                  | timestamptz / uuid                                         |

### 5.2 Indexes

- `library_files_owner_idx` — B-tree on `(tenant_id, owner_user_id, updated_at DESC) WHERE deleted_at IS NULL` — list-view default sort.
- `library_files_project_idx` — B-tree on `(tenant_id, project_id) WHERE project_id IS NOT NULL AND deleted_at IS NULL` — project-scoped reads.
- `library_files_index_status_idx` — partial B-tree on `(tenant_id, index_status) WHERE index_status IN ('pending','indexing','failed')` — indexer worker poll.
- `library_files_tsv_idx` — GIN on `tsv` — keyword search.
- _(Mirror indexes on `library_pages` and `library_datasets`.)_
- `library_embeddings_vector_idx` — IVFFLAT on `embedding vector_cosine_ops` — vector search. Tenant + model_id pre-filtered before vector probe to keep recall scoped per tenant per model.
- `library_embeddings_target_idx` — B-tree on `(tenant_id, target_kind, target_id, chunk_ordinal)` — reverse lookup when re-indexing.
- `library_page_versions_idx` — B-tree on `(page_id, version DESC)`.
- `library_pins_user_idx` — B-tree on `(tenant_id, user_id, pinned_at DESC)`.
- `library_access_log_user_idx` — B-tree on `(tenant_id, user_id, accessed_at DESC)`; **rolling 30d retention** (see §5.3).
- `library_citations_target_idx` — B-tree on `(tenant_id, target_kind, target_id, cited_at DESC)`.

### 5.3 Retention (per master §3.3)

- **Files**: indefinite while not soft-deleted. Soft-deleted retained 90 days, then hard-deleted (metadata + blob bytes + thumbnails). Audit row anonymised (not deleted) per master §3.3.
- **Pages**: indefinite while not soft-deleted. Soft-deleted retained 365 days (pages are often "I'll come back to that") then hard-deleted. Page versions cascade with parent on hard delete.
- **Datasets**: indefinite while not soft-deleted. Soft-deleted retained 90 days then hard-deleted (metadata + Parquet bytes).
- **Page versions GC**: per master §5.5 Q3 (resolved here): every save = a version, but after **30 days** versions older than the last 10 per page are GC'd. The most recent version is never GC'd. Audit-relevant version markers (last-version-before-soft-delete) survive.
- **Library embeddings**: cascade with parent (file/page/dataset) on hard delete. Re-embedded on schema/model-id change (re-index job).
- **Access log**: rolling 30d window — anything older than 30d is hard-deleted by the retention cron (low-signal data; the audit trail captures everything anyway).
- **Citations back-index**: retained as long as both citer and citee exist; on either deletion, the citation row is soft-anonymised (the foreign id stays for audit; the `<ItemLink>` resolver returns `{route: null}` per cross-audit §5.3).
- **Audit rows**: 365d per master rule; cascade-on-delete never applies (audit append-only).
- **Blob bytes in object store**: hard-deleted bytes get a delete-marker; object-store lifecycle policy reclaims after 30d (deployment-level — out of scope here; documented for SRE).

### 5.4 Cleanup job (`library_retention.py`)

Daily backend cron extends the existing retention pattern. Tasks (each emits an audit summary row):

1. Hard-delete soft-deleted files past 90 days (cascade blob + thumbnails + embeddings).
2. Hard-delete soft-deleted pages past 365 days (cascade versions + embeddings).
3. Hard-delete soft-deleted datasets past 90 days (cascade blob + embeddings).
4. GC page versions: per page, retain last 10 versions; for the rest, hard-delete those older than 30d.
5. Trim `library_access_log` past 30 days.
6. Reconcile orphaned blob refs (object store keys with no metadata row) — log + alert; do not delete (defense-in-depth against accidental cascade failure).
7. Emit `library.retention_cleanup_run` audit summary per-tenant.

### 5.5 Upload size limits

- Files: 100 MB default per upload (configurable per tenant). Reject > 1 GB hard. **Streamed signed PUT** to object store — the API never proxies bytes (master §3.7 perf rule).
- Datasets: 500 MB default per upload (Parquet is dense). Reject > 5 GB hard. Same signed-PUT pattern; row-count derived post-upload by the indexer.
- Pages: 1 MB markdown body — generous for human-written knowledge cards; agent-generated drafts above 1 MB are split (Wave 7 feature; current behavior is reject with 413 + "Split into multiple pages" hint).
- Total per-tenant storage budget: a soft cap enforced by the indexer-side accounting (Wave 7 surfaces "Storage used: 4.3 GB of 10 GB" in the panel footer); hard-cap enforced only at the object-store layer (deployment concern). See §16 Q6.

---

## §6 Retrieval pipeline (load-bearing — the agentic value of Library)

Library's job is not "store bytes". It's "let an agent and a user find what they need". The retrieval pipeline is the load-bearing infra.

### 6.1 Overview

Three layers, executed in order, with progressive results streamed to the client when latency permits:

1. **BM25 keyword search** — Postgres `tsvector` GIN index (§5.2). Fast (sub-100ms p99). Always run first.
2. **Vector similarity search** — pgvector IVFFLAT on `library_embeddings.embedding`. Tenant-scoped pre-filter (the index probes within tenant). Cosine similarity; top-K (default K=50).
3. **Cross-encoder re-rank** (optional) — when `?rerank=true` (default `true` for human-driven `/v1/library/search`, default `false` for agent-driven retrieval to keep p50 < 200ms). LLM-based re-rank via `build_chat_model` with a small cheap model + `Purpose.LIBRARY_RETRIEVAL`. Re-ranks the union of top-N BM25 + top-N vector hits.

The fused score combines via **Reciprocal Rank Fusion (RRF)**: `score = Σ 1/(k+rank_i)` with `k=60`. RRF is parameter-light and well-studied; it avoids per-query scoring-scale gymnastics across BM25 and cosine.

When `partial_results=true` is set in the request, the response streams over SSE as each layer completes (BM25 hits → vector hits → re-ranked) so the user sees something within 100ms even when the full pipeline takes longer.

### 6.2 Indexing pipeline

Out-of-band worker (`services/backend/src/backend_app/jobs/library_indexer.py`), modelled on `runtime_worker/jobs/retention_sweeper.py` and the Phase 5 routines scheduler (cross-audit §3.4 / routines-prd §3.7):

**Loop shape:** poll every `LIBRARY_INDEXER_INTERVAL_SECONDS` (default 30s). Per tick:

1. Claim a batch of `pending` items with `FOR UPDATE SKIP LOCKED`, set `index_status='indexing'`, advance `claim_expires_at`.
2. For each item: extract text (per kind — see §6.4), chunk it (per §6.5), call `POST /internal/v1/llm/embed` on `ai-backend` for vectors, insert into `library_embeddings`.
3. Update `tsvector` (auto-maintained by the generated column on row write; this step is implicit for pages — for files/datasets, post-extraction the indexer writes a `text_extracted` column the `tsv` generator references).
4. On success: `index_status='indexed'`, emit `library.item_indexed` SSE envelope.
5. On failure: retry up to 3× with exponential backoff. After 3 failures: `index_status='failed'`, populate `index_error`, emit `library.item_index_failed` SSE envelope, and create an Inbox item for the owner (Phase 4 producer).

**Claim semantics** mirror Routines (cross-audit §3.5 + routines-prd §3.7): `FOR UPDATE SKIP LOCKED` + `CLAIM_TTL_SECONDS=300` (5min, since embedding extraction can take longer than scheduler ticks).

### 6.3 Chunking strategy

- **Files** — text is extracted via per-mime extractors (§6.4). Chunked at ~800 tokens per chunk with 100-token overlap. Each chunk → one `library_embeddings` row.
- **Pages** — markdown body is chunked along H2 / H3 boundaries where present; falls back to 800-token windowing. Title is prepended to every chunk so isolated chunks remain query-able.
- **Datasets** — **per-row summary embedding**: each row is rendered as a key/value text snippet (e.g. `"Customer: Acme · Amount: 540000 · Stage: Negotiation"`) and embedded. For large datasets (> 10k rows), embed a sampled subset of `min(10000, row_count)` rows + a dataset-level summary chunk derived from schema + sample rows. Cell-level retrieval is out of scope for Phase 7 (see §16 Q5).

### 6.4 Text extraction per file-kind

- **PDF** — `pypdf` (existing dep) for text; for image-heavy PDFs, OCR is deferred to Wave 7 (see §16 Q1).
- **Image** — image captioning is deferred to Wave 7. For Phase 7, images get name + tags as the only indexed text (still retrievable by filename/tag, not by content).
- **Doc / sheet / slide (Office)** — `python-docx`, `openpyxl`, `python-pptx` (Phase 7 adds these deps to `services/backend/requirements.txt`).
- **Markdown / plain text / CSV / JSON** — direct read.

### 6.5 Embedding model

- Phase 7 ships with **`text-embedding-3-small`** (OpenAI, 1536-dim) as the default. The model is configurable per-tenant via a backend setting (Wave 7 admin UI; Phase 7 hardcodes the default).
- pgvector is the store. **No external vector DB in Phase 7** — master §5.5 open question Q1 (resolved here): pgvector is sufficient for tenant-sized corpora (< 10 M chunks per tenant per master §3.7 perf budget). External vector DB is Wave 8+ when a tenant breaches the scaling envelope.
- Tenant + model_id pre-filter before vector probe keeps recall scoped: `WHERE tenant_id = $1 AND model_id = $2`. When the embedding model is changed, a `reindex` job re-embeds with the new model; old vectors stay until cleanup so search remains available during migration.

### 6.6 Citation back-index

When an agent (or user) cites a Library item from a chat / run / Routine fire:

1. `POST /v1/library/{id}/cite` (called by chat-surface when a `<ItemLink kind="library_*">` is clicked OR by the runtime when an agent emits a library citation in its response).
2. Backend writes a row in `library_citations` with `citer_kind` / `citer_id`.
3. The Library detail view reads `GET /v1/library/{id}/citations` to render the "Cited in N chats" chip.

The citation back-index lets the user answer "where did I use this?" and lets compliance auditors track sensitive-doc reuse.

### 6.7 Token-usage attribution per LIBRARY_RETRIEVAL and LIBRARY_INDEXING

Cross-audit §5.5 (binding): every LLM call routes through `build_chat_model`. Library extends the existing `Purpose` enum with two values:

- `Purpose.LIBRARY_RETRIEVAL` — for the cross-encoder re-rank step (§6.1) + the chat-time agent retrieval queries that hit Library (§13.3).
- `Purpose.LIBRARY_INDEXING` — for embedding-generation calls during ingest (§6.2).

`runtime_model_call_usage` rows from these purposes already carry `org_id` / `user_id` / `library_item_id` (via the existing `originating_tool_call_id` / `originating_tool_name` columns — Library indexing is recorded under `originating_tool_name = "library_indexer"` + the item id encoded in the run/task dimensions). Aggregating "what did Library cost this month?" becomes `WHERE purpose IN ('library_retrieval','library_indexing') AND org_id = $1`. **No new tracker; no parallel `library_usage` table.** Phase 7 P7-A3 adds the two Purpose values; no other token-usage code changes.

---

## §7 Authorization

### 7.1 Visibility rules (read)

Per cross-audit §1.3 (project-scoped access — the canonical resolver `services/backend/src/backend_app/projects/acl.py::is_project_member` is consumed by Library; no reimplementation):

- A `LibraryFile` / `LibraryPage` / `LibraryDataset` is visible when:
  - `tenant_id` matches the verified bearer's tenant claim, **and**
  - `owner_user_id` matches the verified bearer's user_id, **OR**
  - `project_id IS NOT NULL` AND `is_project_member(tenant_id, project_id, user_id) IN ('owner','editor','viewer')`, **OR**
  - the bearer has the `compliance_reader` role (tenant admin with audit-read scope; the read itself is audited).
- Non-readers see `404` (existence-not-leaked default per cross-audit §1.3).
- Downloads (`/v1/library/{id}/download`, `/v1/library/{id}/preview` with byte data) audited per §7.

### 7.2 Mutation rules (write)

| Action                                    | Required role                                                                                                                 |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Upload file                               | Any tenant member (creates item with `owner_user_id = self`).                                                                 |
| Create page                               | Any tenant member.                                                                                                            |
| Ingest dataset                            | Any tenant member.                                                                                                            |
| PATCH metadata (name / tags / project_id) | `owner_user_id` only. Project members CANNOT mutate metadata.                                                                 |
| PATCH page body (markdown edit)           | `owner_user_id` only. Concurrent saves rejected via `If-Match`.                                                               |
| DELETE                                    | `owner_user_id` only. Soft delete. Tenant admin can hard-delete via GDPR endpoint (see §7.3).                                 |
| Pin / unpin                               | Any reader (per-user; doesn't mutate the item itself).                                                                        |
| File-to-project (set project_id)          | `owner_user_id` only. The target project must be one the user owns/edits (project_acl.is_project_member with role >= editor). |
| Re-index                                  | `owner_user_id` only (or admin).                                                                                              |

Admins cannot edit another user's Library item (read-only compliance scope). The "admin force-transfer owner" workflow is Wave 7 (see §16 Q3).

### 7.3 GDPR / forced-delete

Master §3.3 GDPR forced-hard-delete is a single admin-only endpoint (`POST /v1/library/{id}/gdpr-purge`). It:

- Hard-deletes metadata + blob + thumbnails + embeddings.
- Anonymises (does not delete) audit rows referencing the item.
- Anonymises citation back-index rows: `target_id` retained for audit linkage; ItemLink resolver returns `{route: null}` per cross-audit §5.3.
- Emits a high-severity `library.gdpr_purge` audit row with the admin's actor + a redacted reason.

### 7.4 Sensitive-field handling

- **Blob refs**: opaque to clients. Clients never see object-store URLs directly; they get **signed GET URLs with a short TTL (10 minutes)** via `/preview` or `/download`. **No cleartext object-store URLs in audit rows** (cross-audit §7 "blob storage: signed URLs; no cleartext in audit rows" — binding). Audit rows record `target_id` only.
- **Page bodies**: treated as sensitive in telemetry. Body content never logged. `before_state.markdown` / `after_state.markdown` in audit rows are stored as content-hash + length, not raw content. Admins with compliance scope reveal-via-audit-export per the existing SIEM pipeline.
- **Dataset rows**: never logged. Preview endpoint returns rows under the same auth bracket as the metadata read.
- **Tags**: not sensitive; logged in telemetry. Users should avoid putting PII into tags (UI hint in the tag input: "Don't include personal data").
- **Search queries (`q`)**: hashed for telemetry; raw q never logged. Per cross-audit §5.5 PII rule.

### 7.5 Multi-tenant isolation tests (mandatory per §17)

- Cross-tenant GET → 404.
- Cross-tenant PATCH → 404.
- Cross-tenant download attempt with another tenant's `id` → 404.
- Tenant_id forged in request body → ignored (server derives from bearer; master §3.1 binding).
- Project-member read across tenants → impossible (a project lives in exactly one tenant; the `is_project_member` resolver returns null cross-tenant).

---

## §8 Pagination + search (per master §3.5 + cross-audit §1.5)

- **Cursor pagination.** `?after=<opaque-cursor>&limit=<n>`. Default `limit=50`, max `limit=200`. Cursor encodes `(sort_field, id)` for stable scrolling under inserts.
- **Multi-value filter axis = OR within axis; AND across axes** per cross-audit §1.5.
- **Search.** Two surfaces:
  - **Keyword-only `?q=…` on list endpoints** — runs `plainto_tsquery('simple', q)` against the kind's `tsv` GIN index. Fast; cheap.
  - **Full hybrid `POST /v1/library/search`** — body-form (long queries need not be URL-encoded), runs the full BM25 + vector + optional rerank pipeline (§6.1).
- **Sort allowlist** per §4.4.

Combined example:

```
GET /v1/library?filter[kind]=page&filter[kind]=file&filter[project_id]=acme&filter[source.kind]=agent_save&q=margin+compression&sort=updated_at:desc&limit=50
```

---

## §9 Accessibility (per master §3.6)

- **Search bar** — autofocus on first paint; `aria-label="Search your library"`; `aria-live=polite` region announces "Showing N results for {q}" on each query update; results re-announced when re-ranking completes ("Refined to N results by relevance"). Throttled to one announcement per 1s.
- **Filter tabs (kind)** — ARIA tabs pattern (`role="tablist"` / `role="tab"` / `role="tabpanel"`). Arrow keys cycle, Home/End jump, Enter activates.
- **CardGrid view rows** — each card is one tab stop. Enter opens detail. Hover/focus actions reveal on focus, not only hover.
- **Page editor** — `<textarea>` is the source-of-truth element (split-pane preview is decorative). Standard textarea semantics; markdown formatting buttons are optional helpers with `aria-label`.
- **Dataset cell grid** — `role="grid"`, `role="row"`, `role="columnheader"`, `role="gridcell"`. Arrow keys move focus across cells; Page Up/Down jumps a screen; Home/End jumps row start/end.
- **PDF / image previews** — `aria-label` describes the item; PDF viewer is keyboard-accessible (built-in pdf.js semantics); images have alt text from the item name + optional caption (Wave 7 feature).
- **Upload affordance** — `<input type=file>` is the underlying element (via `FilePickerPort` cross-audit §1.2 — defaults to native picker on web; OS native dialog on desktop). Drag-and-drop also works on web; drop zone has `aria-label="Drop files to upload"`.
- **Color is never the sole carrier** — failed-index combines red border + alert icon + the word "Indexing failed".
- **Reduced motion** — upload progress bar respects `prefers-reduced-motion`.
- **High-contrast theme** — kind icons keep their stroke at WCAG AA contrast against the surface; checked in CI.

---

## §10 Performance (per master §3.7)

- **LCP < 2.5s** — list endpoint returns first 50 items with denormalized owner name + project chip + thumbnail signed URLs already in the payload; no waterfall.
- **INP < 200ms** — filter tab clicks operate on already-fetched first page; pinning is optimistic with rollback; uploads happen out-of-band.
- **Virtualized CardGrid / DocList** when total > 100; dataset CellGrid virtualized always.
- **Search latency budget**:
  - BM25-only path: p50 < 100ms, p99 < 300ms.
  - Hybrid (no rerank): p50 < 300ms, p99 < 800ms.
  - Hybrid + rerank: p50 < 800ms, p99 < 2s (cross-encoder is the bottleneck; SSE progressive results keep perceived latency low — first results visible within 100ms).
- **Upload path**: API call < 100ms (signed-URL issuance). Object-store PUT direct from client. Total upload time bounded by network. **Bytes never proxied through API** — master §3.7 binding.
- **Indexer throughput**: target 1000 items / minute / worker on the embedding endpoint; horizontally scalable via `FOR UPDATE SKIP LOCKED` claim.
- **Preview**: PDF first-page render server-side at ingest (one-time cost); subsequent previews are signed-URL fetches.
- **Shell render isolation** — navigating to Library does not re-mount `ChatShell`; only the destination remounts.
- **SSE keepalive** — `:keepalive` every 25s; client tolerates 60s silence before reconnect.

---

## §11 Telemetry (per master §3.8)

OpenTelemetry spans (no PII; only ids + enum values):

```
destination=library
  action=list_open                  view=<all|files|pages|datasets>
  action=detail_open                target_kind=<file|page|dataset> target_id=<id>
  action=editor_open                target_kind=page mode=<new|edit>
  action=editor_save                target_kind=page result=<ok|conflict|validation_error>
  action=search                     q_len=<n> retrieval=<bm25|hybrid|hybrid_rerank> hits=<n> took_ms=<ms>
  action=filter_change              axis=<kind|project|source|tag> value_count=<n>
  action=upload_init                size_bucket=<small|med|large> mime_class=<doc|image|pdf|sheet|other>
  action=upload_finalize            result=<ok|checksum_mismatch|size_mismatch>
  action=index_status_change        target_kind=<file|page|dataset> from=<status> to=<status>
  action=cite                       target_kind=<file|page|dataset> citer_kind=<chat|run|routine>
  action=download                   target_kind=<file|dataset>
  action=save_to_library            from=<chat_tool_result|chat_agent_msg|run_completion|routine_output> target_kind=<file|page|dataset>
  action=connector_sync_start       connector_kind=<notion|drive|sharepoint>
  action=sse_reconnect              after_sequence=<n>
```

Backend emits structured logs with `request_id` correlation (per cross-audit §5.1 — OTel trace_id; facade injects). Error logs include `tenant_id`, route, error code; never user data; never page body content; never search query text.

Indexer worker emits per-tick metrics: `library_indexer_ticks_total`, `library_items_indexed_total{kind=...}`, `library_index_failures_total{reason=...}`, `library_indexer_chunk_count_total{kind=...}`, `library_indexer_embedding_latency_seconds`.

Retrieval emits per-query metrics: `library_search_total{strategy=...}`, `library_search_latency_seconds{stage=bm25|vector|rerank}`, `library_search_hits_total`, `library_search_zero_hits_total`.

---

## §12 States (per master §3.10)

| State                         | Renders                                                                                                                                                                   |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **List loading**              | Skeleton: PageHeader visible, FilterTabs visible (no counts), 6 skeleton cards in CardGrid.                                                                               |
| **List empty (any filter)**   | §3.7 tutorial card for All; filter-specific copy otherwise.                                                                                                               |
| **Filter-empty + search**     | "No items match \"{q}\" in {filter}." with "Clear filters" button.                                                                                                        |
| **Search progressive**        | BM25 hits visible within 100ms; vector hits arrive next, list reflows; rerank reorders on completion (animated re-position respects `prefers-reduced-motion`).            |
| **Search empty**              | §3.7 search-empty copy.                                                                                                                                                   |
| **Detail loading**            | Kind-specific skeleton.                                                                                                                                                   |
| **Detail indexing**           | Detail header banner: "Indexing in progress — searchable in ~30s. [Refresh]".                                                                                             |
| **Detail index failed**       | Banner: "Indexing failed: {reason}. [Retry]" — retry calls `POST /internal/v1/library/{id}/reindex`.                                                                      |
| **Page editor saving**        | Save button shows spinner; form disabled; on 200 → toast "Page saved" + redirect to detail; on 409 → conflict banner with merge/overwrite options.                        |
| **Page editor unsaved**       | Top-right "Unsaved changes" pill + auto-save countdown ("Auto-saving in 12s"). On nav-away: confirm dialog.                                                               |
| **Upload in progress**        | Toast at top: "Uploading 1 of 3: report.pdf — 45%" with progress bar; on completion: "✓ Uploaded. Indexing in background."                                                |
| **Upload failed**             | Toast at top: "Upload failed: {reason}. [Retry]"; original `LibraryFile` row remains in `pending` status with `index_error` populated.                                    |
| **Detail unauthorized / 404** | "Not found" page with `[Back to Library]` link; 404 per cross-audit §1.3.                                                                                                 |
| **Offline**                   | Banner: "You're offline — showing cached items. New uploads queued for when you reconnect." Reads from `KeyValueStore` cache; queued uploads stored in browser IndexedDB. |
| **Stale**                     | If last-fetch > 5min AND SSE disconnected: "Library may be out of date. [Refresh]".                                                                                       |

---

## §13 Cross-destination references (per master §3.11 + cross-audit §1.1, §3.3, §5.3)

Library cross-references (typed, via `<ItemLink>` registry):

| Field                                                   | Target                                      | UI affordance                                                                                                                                   |
| ------------------------------------------------------- | ------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- | ----------------------------------------------------------------- |
| `source.chat_id` (agent_save)                           | chats destination                           | chip in detail header; opens originating chat.                                                                                                  |
| `source.run_id` (agent_save)                            | ai-backend run records (via chats run pill) | chip in detail header; opens originating run.                                                                                                   |
| `source.connector_id` (connector_sync)                  | connectors destination                      | chip in detail header; opens connector detail.                                                                                                  |
| `project_id`                                            | projects destination                        | chip in detail header; opens project detail.                                                                                                    |
| `library_citations[*].citer_id`                         | chats / runs / routines                     | "Cited in N chats" expandable list of `<ItemLink>`.                                                                                             |
| Agent reply containing `<ItemLink kind="library_file    | library_page                                | library_dataset">`                                                                                                                              | library destination | rendered inline in chat messages; click opens the Library detail. |
| Todo with `source = library_*`                          | todos destination                           | renders the library item chip on the todo card.                                                                                                 |
| Routine `output_target.kind="library_page"`             | library destination                         | the produced page carries `source.kind = "agent_save"` with the routine's run_id; back-link via `<ItemLink kind="routine">` on the page detail. |
| Inbox item kind=`info`/`error` linked to a Library item | inbox destination                           | the item carries `links: [ItemRef]` pointing back to the Library item that triggered it.                                                        |

### 13.1 Cascade rules (per cross-audit §5.3 default — cross-destination = dead link, audit = never cascade)

| Origin deletion                                 | Library effect                                                                                                                                                                                                                                                  |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Owner deleted (offboarded)                      | Owner's items become **admin-claimable** — Wave 7 admin force-transfer (see §16 Q3). For Phase 7: a dead-link warning appears on the items; project-member reads continue (the ACL rule reads `project_id`, not `owner_user_id`); the item is NOT auto-deleted. |
| Project deleted (Library item has `project_id`) | `project_id` becomes a dead reference; "deleted project" pill on the detail; item NOT auto-deleted; filing falls back to unfiled.                                                                                                                               |
| Originating chat / run deleted                  | `source.chat_id` / `source.run_id` is a dead reference (resolver returns `{route: null}`); item retained. Per cross-audit §5.3.                                                                                                                                 |
| Connector disconnected                          | `source.connector_id` resolves to a "disconnected connector" pill; sync is paused; existing items retained.                                                                                                                                                     |
| Library page → chat citation                    | When a citing chat is deleted, the citation row is anonymised; "Cited in N chats" decrements; the back-index row is retained for audit.                                                                                                                         |
| Tenant deleted                                  | Hard cascade (master rule) — files / pages / datasets / embeddings / blobs all destroyed; audit anonymised.                                                                                                                                                     |

### 13.2 Citation surface in the chat surface

When an agent's response embeds `<ItemLink kind="library_*" id=…>` (the LLM is taught the citation grammar in the system prompt), the chat-surface citation renderer (already extant from `citationHrefs.ts`) resolves the ItemRef and renders:

- inline: a small chip with the library item's title + kind icon;
- on click: navigates to `/library/<id>` (or opens an inline preview-on-hover Wave 7 feature);
- on render: emits a `POST /v1/library/{id}/cite` call to bump access + write the back-index row.

### 13.3 Agent-time retrieval into Library

When a chat's agent runs a retrieval tool (the existing `retrieve_from_library` skill — Phase 7 P7-A3 introduces this skill as a standard tool offering on the agent runtime):

1. The tool wraps `POST /v1/library/search` with `rerank=false` (latency budget) + the agent's current `project_id` scope (from the chat's `project_id`).
2. The LLM call inside the search (re-rank or query-planning) attributes via `Purpose.LIBRARY_RETRIEVAL`.
3. The returned hits become tool-result citations the agent threads into its reply; each cited item gets a `POST /v1/library/{id}/cite` call.

The skill is part of the agent's default tool budget; admins can disable it per-agent (Wave 7 admin UI).

---

## §14 Connector integration (wire-shape now, Phase 10 wires it up)

Phase 7 defines the contract; Phase 10 (Connectors full destination) ships the connector-side implementation.

### 14.1 Wire shape

Connectors push items into Library via `POST /internal/v1/library/files` / `pages` / `datasets`:

```jsonc
POST /internal/v1/library/files
Authorization: Bearer <service-token>
X-Enterprise-Org-Id: <tenant-id>
X-Enterprise-User-Id: <on-behalf-of-user-id>
Content-Type: application/json

{
  "name": "Q3 Revenue Briefing.pdf",
  "mime": "application/pdf",
  "size_bytes": 1234567,
  "checksum_sha256": "abc...",
  "blob_ref": "s3://connectors-staging/notion/page-uuid",  // pre-uploaded by the connector
  "source": {
    "kind": "connector_sync",
    "connector_id": "notion-conn-123",
    "external_id": "notion-page-uuid",
    "external_url": "https://notion.so/p/uuid"
  },
  "project_id": "proj-acme",
  "tags": ["q3", "revenue"]
}
```

The connector is responsible for:

- uploading the bytes to its **own staging area** in the object store,
- supplying `blob_ref` in the request (the backend then promotes the blob into its canonical Library bucket asynchronously — or stamps the staging ref as canonical if the deployment co-locates).

The backend is responsible for:

- creating the metadata row,
- enqueuing the indexer,
- attributing the on-behalf-of user as the owner.

### 14.2 Sync model

- **Pull-driven** (Phase 10 default) — the connector enumerates external items and pushes one POST per item. Idempotent: `(source.connector_id, source.external_id)` is a UNIQUE constraint at the database level; a second POST with the same pair updates the existing row.
- **Push-driven** (Wave 8 — Notion / Drive webhooks land mid-Phase 10): the connector forwards external-source events to a Library `event` Routine (Phase 5 wire shape supports `connector.notion.page.updated`); the Routine fires and updates the corresponding Library item via the same internal endpoint.

### 14.3 Auth across the boundary

`POST /internal/v1/library/*` requires:

- `ENTERPRISE_SERVICE_TOKEN` in `Authorization: Bearer …`,
- `X-Enterprise-Org-Id` (tenant) header,
- `X-Enterprise-User-Id` (the on-behalf-of user; the connector's connecting principal).

Per root CLAUDE.md: "Treat caller-supplied identity, role, scope, tenant, org, and user values as untrusted unless derived from a verified session, token, mTLS identity, or IdP claim." Service-token + header bracket is the trusted cross-service path. Audit row `actor_user_id` = the header-supplied user id; `context.service_token_kid` records the key id used.

### 14.4 Disconnection behavior

When a connector is disconnected (Connectors destination → disconnect):

- existing Library items from that connector are retained (no auto-delete);
- they get a "disconnected connector" pill in the detail view;
- re-connecting restores sync; the idempotency key `(source.connector_id, source.external_id)` makes resumption clean.

---

## §15 Desktop substrate caveats (per master §3.12 + cross-audit §1.2)

- **File upload uses `FilePickerPort`** (cross-audit §1.2). Web default: `<input type=file>`; desktop: native OS dialog. Same UI; substrate-agnostic.
- **Drag-and-drop upload on web**: drop zone covers the destination main area; drop fires `FilePickerPort.pick`-equivalent semantics from the drop event. **Desktop drag-drop**: native OS drag-drop from Finder / Explorer via the Electron / Tauri main process; the chat-surface destination receives a file stream identical to the web `pick` return type.
- **Download** — uses `ClipboardPort` for the "Copy link" affordance + native `<a href={signedUrl} download>` for actual download. Desktop sandbox respects OS download dir; web saves to the browser-default download dir.
- **Native notification on indexing completion / failure** — fires through `NotificationPort` (cross-audit §1.2). Title `Indexed: <name>` / `Indexing failed: <name>`; body excluded for privacy. Web default: no-op when permission ungranted; desktop: native OS notification with click → `router.navigate({ kind: "library", id })`.
- **Deep-link routing** — desktop registers `atlas://library/<id>` and `atlas://library/<view>` as URL handlers. Frontend `HashRouter` and desktop main process resolve to the same `route.id`/`route.view` shape.
- **No direct browser API access from any library component** — substrate-agnostic. Clipboard, notifications, file pickers, deep-link routing all go through ports.

---

## §16 Open questions for product (parth)

These need a call before P7-A* / P7-B* / P7-C code the affected branch.

1. **OCR for image-heavy PDFs.** §6.4 default: no OCR in Phase 7 — PDFs whose extractor yields < 256 chars get `index_status="skipped"` with a hint "Add OCR in Wave 7". Confirm or escalate (some tenants will care immediately).
2. **Vector store choice.** §6.5 default: pgvector. Confirm. (Alternatives: Pinecone, Qdrant, Weaviate. The wire shape — `library_embeddings.embedding` as a `vector(1536)` — is portable; switching providers later is a migration job, not a destination redesign.)
3. **Admin force-transfer of orphaned items.** §13.1 + §7.2: when an owner is offboarded, who owns their items? Recommend: tenant admin can force-transfer to a designated user via a Wave 7 admin endpoint; for Phase 7, items remain owned by the offboarded (now-disabled) user and project members continue to see them. Confirm.
4. **Page editor block-based vs markdown.** Master §5.5 Q3: markdown for Wave 2. Phase 7 ships markdown per Q3 default. Confirm.
5. **Cell-level retrieval over datasets.** §6.3: row-level summary embeddings only in Phase 7. A user asking "Show me opportunities over $500k" against a dataset goes through an agent-driven SQL-against-Parquet path (a separate skill; Wave 8). Confirm cell-level retrieval is out of scope.
6. **Per-tenant storage budget.** §5.5: soft cap enforced at app layer + hard cap at object-store layer. Recommend Phase 7 ships no app-level cap; surfaces "Storage used" in the panel footer; admins can configure a hard cap in Wave 7. Confirm.
7. **Embedding model choice.** §6.5: `text-embedding-3-small` default. Per-tenant configurable later. Confirm default; or specify an Anthropic / open-source default if cost / data-residency is a concern.
8. **External-share links** (a public-ish URL for sharing a library item read-only without inviting to a project). Recommend **out of scope for Phase 7**: the share path is project membership. External-share is a security-review-gated Wave 9 feature. Confirm.
9. **Page versioning UI depth.** Master §5.5 Q3: "every save is a version; GC old versions to last-10 per page after 30d." Phase 7 ships a version list + per-version markdown view; diff UI is Wave 7. Confirm: list + view-only is enough for Phase 7.
10. **Saved-search semantics.** §3.3 #7: per-user saved searches, ≤ 20. Are saved searches sharable to a project (so all members see the same saved view)? Recommend **no in Phase 7**: saved searches are per-user. Confirm.
11. **Citation back-index visibility** — should non-owner project members see "Cited in N chats" with the count, or only the count without seeing _which_ chats? Recommend: project members see the count; click-through resolves only the chats they themselves have access to. Confirm.
12. **Auto-extracted knowledge cards.** When a chat run completes with a particularly useful agent reply, should Atlas propose "Save this as a knowledge page?" via an Inbox suggestion? Recommend Wave 7 (Phase 7 ships the manual "Save as page" affordance only). Confirm.

---

## §17 Test plan

### 17.1 Backend / facade unit + integration (P7-A1 + P7-A2 + P7-A3)

**Tenant + ACL isolation:**

- Cross-tenant GET/PATCH/DELETE → 404.
- Owner-only writes: project member reads OK; PATCH → 403; non-member → 404.
- Project-scoped read: project member can GET when `project_id` matches one of user's projects (resolved via `is_project_member` from Phase 6 P6-A1).
- Compliance read: tenant admin with `compliance_reader` can GET; audit row written.
- Cross-tenant blob ref forging attempt: cannot download a blob belonging to another tenant via guessing object-store path.

**Upload + finalize:**

- Signed PUT URL is single-use; valid for 10min; expires correctly.
- Finalize verifies checksum_sha256 (server fetches and rehashes; mismatch → 422 + delete blob).
- Finalize verifies size_bytes matches the actual object size; mismatch → 422.
- 100MB file: success. 1GB+1 byte file: 413 reject at init.
- Concurrent finalize calls for the same `id`: only one succeeds; second sees 409.

**Page editor concurrency:**

- Two concurrent PATCH with same `If-Match` etag: first succeeds; second 409 with current version returned.
- Edit then re-fetch round-trip preserves markdown byte-for-byte.

**Dataset ingest:**

- CSV → server infers schema correctly (string / int / float / boolean / date).
- Parquet → schema read from Parquet metadata.
- Empty dataset (0 rows): accepted; `row_count=0`; `index_status=skipped`.
- Malformed CSV (mixed types in one column): server picks the union type (string fallback); does not 500.

**Indexer:**

- Pending item picked up within 60s; status transitions `pending → indexing → indexed`.
- Indexer claim correctness: 10 concurrent workers + 100 pending items → each indexed exactly once (FOR UPDATE SKIP LOCKED).
- Worker crash mid-index → claim_expires_at lets another worker re-claim after CLAIM_TTL_SECONDS.
- Embedding model failure → 3 retries → `index_status="failed"` + Inbox item created.
- Re-index endpoint re-queues the item and clears `index_error`.
- Test with 10k-row dataset: row-summary embeddings produced in batches; total in < 60s for the test corpus.

**Search:**

- BM25-only: keyword match correctness (stemming, stopwords) verified against fixture corpus.
- Vector-only: semantic similarity returns expected near-neighbors against fixture (English-only Phase 7).
- Hybrid RRF: result order is deterministic given identical inputs; documented `k=60`.
- Rerank: rerank model invocation routed through `build_chat_model` with `Purpose.LIBRARY_RETRIEVAL`; token-usage row written.
- Tenant + project filter applied BEFORE the vector probe: cross-tenant items never appear in another tenant's results.
- Zero-hit: returns `hits: []` with `took_ms` populated.
- Slow-query SSE: progressive results delivered in order (bm25 → vector → reranked); reconnect resumes from last sequence.

**Citation back-index:**

- `POST /v1/library/{id}/cite` writes a row; idempotent on (target_id, citer_id, citer_kind, day).
- `GET /v1/library/{id}/citations` lists chats / runs; cross-tenant items never appear.

**Retention cleanup:**

- Soft-deleted file past 90d → blob bytes purged, metadata row hard-deleted, embeddings cascaded, audit row anonymised.
- Page versions GC: page with 50 versions, all > 30d old → trimmed to last 10; audit summary written.
- Access log past 30d → hard-deleted.
- Orphaned blob detection logs but does not delete (defense-in-depth).

**Cross-service ingest:**

- `POST /internal/v1/library/files` with valid service-token + headers → 201; metadata row + indexer enqueue.
- Same call without service token → 401.
- Same call without `X-Enterprise-Org-Id` → 400.
- Idempotency: `(source.connector_id, source.external_id)` UNIQUE — second call with same pair → 200 update, not 201 create.

**Audit immutability:** UPDATE on audit row → audit-chain raises.

**GDPR purge:** admin purge endpoint → metadata + blob + embeddings hard-gone; audit row anonymised but retained; citation back-index anonymised.

**Token-usage attribution:**

- Embedding call during ingest → `runtime_model_call_usage` row with `purpose='library_indexing'`, `org_id`, `originating_tool_name='library_indexer'`.
- Rerank call during search → row with `purpose='library_retrieval'`.
- Aggregating `WHERE purpose IN ('library_retrieval','library_indexing') AND org_id = $1` returns the right rollup.

**LLM provider import guard (Phase 0.6):** `services/backend/src/backend_app/library/embeddings.py` and `library_indexer.py` MUST NOT import `openai` / `anthropic` / etc directly — they call ai-backend's `/internal/v1/llm/embed`. The Phase 0.6 CI guard (commit `4939186`) verifies this.

### 17.2 Frontend unit + integration (P7-B1 + P7-B2 + P7-C)

- List view rendering with empty + small + large (100+) item lists; virtualization kicks in.
- Filter combinations: every pairwise of `filter[kind]` (multi-value OR) × `filter[project_id]` × `filter[source.kind]` × `q`.
- CardGrid ↔ DocList toggle preserves scroll position + filter state.
- Search debouncing: 250ms; rapid typing doesn't fire 10 requests.
- Search SSE progressive: BM25 hits visible first, vector hits arrive, rerank reorders; layout doesn't shift under `prefers-reduced-motion`.
- Page editor: typing → 30s auto-save fires; explicit save commits; conflict modal renders on 409 with merge / overwrite buttons.
- Dataset preview: CellGrid keyboard navigation (arrows, Page Up/Down, Home/End); column sort (client-side); first 200 rows rendered.
- File detail: PDF viewer lazy-loaded; first page rendered on mount.
- Upload via FilePickerPort mock: `pick()` returns a file → `/v1/library/files` init → mocked PUT → finalize → toast confirms.
- Upload failure: mocked PUT 500 → "Upload failed: …" toast + retry.
- SaveToLibraryPopover from a chat tool result: popover renders with smart defaults; project_id inherited from the chat; submit creates `LibraryDataset`; toast confirms with `<ItemLink>`.
- `<ItemLink kind="library_file">` / `kind="library_page">` / `kind="library_dataset">` resolve to detail route; deleted item → `{route: null}` per cross-audit §5.3 (renders `<deleted file>` chip).
- Pin / unpin toggle is optimistic; rollback on 5xx.
- File-to-project: target-project picker only shows projects where user is owner/editor; verification on backend.
- axe-core green on `LibraryDestination + LibraryPanel + LibraryFileDetail + LibraryPageDetail + LibraryDatasetDetail + PageEditor` in default + high-contrast themes.
- SSE reconnect: 3 server events while disconnected → all applied after reconnect.
- ItemLink registry: 3 kinds registered exactly once at package import; double-import doesn't double-register.

### 17.3 End-to-end smoke (added to `docs/dev-testing.md`)

```bash
export TOKEN=$(make dev-bearer)

# Upload a file (two-step: init + finalize)
INIT=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
       http://127.0.0.1:8200/v1/library/files \
       -d '{"name":"test.pdf","mime":"application/pdf","size_bytes":1024}')
URL=$(echo "$INIT" | jq -r .upload_url)
FILE_ID=$(echo "$INIT" | jq -r .file_id)
curl -X PUT "$URL" --data-binary @/tmp/test.pdf
curl -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8200/v1/library/files/$FILE_ID/finalize

# Create a page
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     http://127.0.0.1:8200/v1/library/pages \
     -d '{"title":"My first page","markdown":"# Hello\nFrom Atlas."}'

# Search
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     http://127.0.0.1:8200/v1/library/search \
     -d '{"query":"hello","filters":{"kind":["page"]}}'

# Stream live updates
curl -N -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8200/v1/library/stream
```

---

## §18 Implementation phasing (per implementation-plan.md §2 Phase 7 + §4 merge order)

Per master §7 + §8 (Phase 7 budgets 1 sub-PRD + 3 impl agents), Library uses a 6-agent fan-out because the surface is large enough that parallel work is the only way to stay inside the 2-day budget.

### 18.1 Agent boundaries (no overlap with shared files)

**P7-A1 backend metadata + ACL — `worktree-agent-phase7-library-backend-meta`**

Prereqs: SP-1 (`brands.ts` for `LibraryFileId`/`LibraryPageId`/`LibraryDatasetId`/`LibraryItemId`, `refs.ts` for `ItemRef` + `SectionResult`), P6-A1 (`projects/acl.py::is_project_member` canonical resolver).

Exclusive files:

- `packages/api-types/src/library.ts` (NEW); append re-export to `packages/api-types/src/index.ts` (rebase after P6-A).
- `services/backend/src/backend_app/library/` (NEW): `routes.py`, `service.py`, `store.py`, `schema.py`, `acl.py`, `events.py` (SSE bus).
- Alembic migration for `library_files`, `library_pages`, `library_page_versions`, `library_datasets`, `library_pins`, `library_access_log`, `library_citations` (without `library_embeddings` — that lands with P7-A3).
- `services/backend/src/backend_app/app.py` — append `include_router(library_router)` + internal-router (merge after P6-A).
- `services/backend-facade/src/backend_facade/library_routes.py` (NEW); append to `facade/app.py`.
- Audit hooks; retention cron entry-point stub.
- All tests per §17.1 except indexer + search.

Deliverables: CRUD endpoints; ACL enforcement; metadata storage; audit; cross-tenant isolation tests; SSE bus for `library.item_*` envelopes.

**P7-A2 blob store + signed-URL pipeline — `worktree-agent-phase7-library-blobs`**

Prereqs: P7-A1.

Exclusive files:

- `services/backend/src/backend_app/library/blob_store.py` (NEW) — abstracts S3-compatible store; signed PUT / signed GET; checksum validation; thumbnail extraction.
- `services/backend/src/backend_app/library/dataset_ingest.py` (NEW) — CSV / Parquet / JSONL ingest, schema inference, row counting.
- `services/backend/src/backend_app/library/file_extractor.py` (NEW) — per-mime text extractors (pypdf, python-docx, openpyxl, python-pptx, markdown, plaintext).
- Add deps to `services/backend/requirements.txt`: `boto3`, `pypdf`, `python-docx`, `openpyxl`, `python-pptx`, `pyarrow` (for Parquet).
- Endpoints: `/v1/library/files/{id}/finalize`, `/v1/library/datasets/{id}/finalize`, `/v1/library/{id}/preview`, `/v1/library/{id}/download`.
- Object-store config knobs in `services/backend/src/backend_app/settings.py` (kept off the default secrets — dev uses MinIO; prod injects).
- All tests per §17.1 upload + finalize + preview blocks.

Deliverables: bytes flow end-to-end; thumbnails generated at ingest; cell-grid preview endpoint; signed-URL download; checksum + size verification.

**P7-A3 embedding + retrieval pipeline — `worktree-agent-phase7-library-retrieval`**

Prereqs: P7-A1, P7-A2.

Exclusive files:

- `services/backend/src/backend_app/library/embeddings.py` (NEW) — calls ai-backend `/internal/v1/llm/embed`; chunk-and-embed logic.
- `services/backend/src/backend_app/library/search.py` (NEW) — hybrid BM25 + vector + RRF fusion; rerank dispatch.
- `services/backend/src/backend_app/jobs/library_indexer.py` (NEW) — claim loop modelled on `retention_sweeper.py`.
- Alembic migration for `library_embeddings` (with pgvector extension enabled) + IVFFLAT index.
- `services/ai-backend/src/runtime_api/http/llm_routes.py` (NEW) — `POST /internal/v1/llm/embed`; routes through `build_chat_model`.
- `services/ai-backend/src/agent_runtime/observability/attribution.py` — add `Purpose.LIBRARY_RETRIEVAL` + `Purpose.LIBRARY_INDEXING` enum values.
- `services/ai-backend/src/runtime_worker/__main__.py` — no change (Library indexer runs in `backend`'s job harness, not `ai-backend`'s, to keep service boundaries clean).
- Endpoints: `POST /v1/library/search`, `GET /v1/library/search/stream`, `POST /internal/v1/library/{id}/reindex`.
- All tests per §17.1 search + indexer blocks; token-usage attribution tests.

Deliverables: embeddings written; search end-to-end; SSE progressive results; Purpose-attributed usage rows.

**P7-B1 chat-surface shell + list views — `worktree-agent-phase7-library-surface-list`**

Prereqs: SP-1 (`<PageHeader>`, `<StatusPill>`, `<FilterTabs>`, `<EmptyState>`, `<CardGrid>`, `<DocList>`, `<ItemLink>`, `formatRelativeTime`), P7-A1 (wire contracts).

Exclusive files (replaces Wave 0 stub):

- `packages/chat-surface/src/destinations/library/LibraryDestination.tsx` (REWRITE).
- `packages/chat-surface/src/destinations/library/LibraryPanel.tsx` (NEW).
- `packages/chat-surface/src/destinations/library/index.ts` (REWRITE — registers `<ItemLink>` resolvers for 3 kinds).
- `packages/chat-surface/src/destinations/library/search/{LibrarySearchInput,LibrarySearchResults}.tsx` (NEW).
- `packages/chat-surface/src/destinations/library/list/{LibraryCardGrid,LibraryDocList,LibraryItemCard,LibraryItemRow}.tsx` (NEW).
- All chat-surface tests for the above; existing `LibraryDestination.test.tsx` is rewritten to cover the new shape.

Deliverables: list + search UX; All / Files / Pages / Datasets tabs; tutorial empty state; CardGrid ↔ DocList toggle; SSE updates.

**P7-B2 chat-surface detail views + editor — `worktree-agent-phase7-library-surface-detail`**

Prereqs: SP-1, P7-A1, P7-A2 (preview endpoint shape).

Exclusive files:

- `packages/chat-surface/src/destinations/library/detail/LibraryFileDetail.tsx` (NEW).
- `packages/chat-surface/src/destinations/library/detail/LibraryPageDetail.tsx` (NEW).
- `packages/chat-surface/src/destinations/library/detail/LibraryDatasetDetail.tsx` (NEW).
- `packages/chat-surface/src/destinations/library/page/PageEditor.tsx` (NEW; markdown editor with split-pane).
- `packages/chat-surface/src/destinations/library/dataset/CellGrid.tsx` (NEW; virtualised grid).
- `packages/chat-surface/src/destinations/library/file/FilePreview.tsx` (NEW; PDF/image/fallback).
- `packages/chat-surface/src/destinations/library/upload/UploadButton.tsx` (NEW; uses FilePickerPort).
- All chat-surface tests for the above.

Deliverables: detail per kind; markdown editor with conflict UI; cell-grid; file preview; upload widget.

**P7-C frontend + cross-destination wiring — `worktree-agent-phase7-library-wiring`**

Prereqs: P7-B1, P7-B2.

Exclusive files:

- `apps/frontend/src/api/library.ts` (NEW) — HTTP wrappers + SSE.
- `apps/frontend/src/app/App.tsx` — extend destination dispatch switch + ContextPanel slot (merge after Phase 6 P6-C frontend wiring).
- `packages/chat-surface/src/destinations/library/save/SaveToLibraryPopover.tsx` (NEW) — cross-destination popover.
- Edits to Chats tool-result card to render `[Save to Library]` action that opens the popover.
- Edits to Chats agent-message action menu to render `[Save as page]`.
- Edits to Inbox detail to render `[Save output to Library]` on run-completion items.
- e2e smoke per §17.3.

Deliverables: end-to-end Library; one-click save from anywhere; agent retrieval works in chats.

### 18.2 Merge order (strict, per implementation-plan.md §4)

1. SP-1 (shared primitives) → main _(prerequisite already merged)_.
2. P6-A1 (projects ACL canonical resolver) → main _(Phase 6 prereq; lands before P7-A1)_.
3. **P7-A1** → main. Rebases `api-types/index.ts`, `backend/app.py`, `facade/app.py` on top of P6-A.
4. **P7-A2** → main. Depends on P7-A1.
5. **P7-A3** → main. Depends on P7-A1, P7-A2. Adds `Purpose` enum values (single-line change to attribution.py; risk-of-conflict near-zero).
6. **P7-B1** → main. Depends on P7-A1.
7. **P7-B2** → main. Depends on P7-A1, P7-A2.
8. **P7-C** → main. Depends on P7-B1, P7-B2, P7-A3. Rebases `App.tsx` last.

P7-A2 / P7-A3 can run in parallel after P7-A1 lands; P7-B1 / P7-B2 can run in parallel after P7-A1 lands. Total wall time with 6 agents: ≤ 2 work-days per master §8 budget.

### 18.3 Acceptance criteria (gate to closing Phase 7)

- ✅ Every endpoint in §4.2, §4.3 implemented and tested.
- ✅ Audit rows emitted for every action in §7.1; verified in audit-chain export.
- ✅ Tenant + ACL isolation tests pass (cross-tenant: 404; cross-user-non-member: 404; project-member-read: 200; admin compliance read: 200 + audit row).
- ✅ Indexer claim correctness test: 10 concurrent fake workers + 100 pending items → no double-index; each indexed exactly once.
- ✅ Search hybrid test: BM25-only, vector-only, and hybrid+rerank each produce expected results on a fixture corpus; tenant + project pre-filter holds; SSE progressive results delivered in order.
- ✅ Token-usage attribution test: indexer emits `purpose='library_indexing'` row; reranker emits `purpose='library_retrieval'` row; aggregation query returns expected rollup.
- ✅ Page concurrency test: two concurrent PATCH with same etag → first 200, second 409 + version returned.
- ✅ Dataset preview test: 10k-row dataset, first 200 rows fetched in < 500ms p99.
- ✅ Upload + finalize test: 100MB file end-to-end works; checksum mismatch is rejected.
- ✅ Retention cron test: soft-deleted items past window → hard-deleted; page versions GC'd to last-10; access log trimmed; audit summary written.
- ✅ axe-core green on `LibraryDestination + LibraryPanel + 3 detail views + PageEditor` in default + high-contrast themes.
- ✅ SSE reconnect resumes from `?after_sequence=N` without dropping envelopes.
- ✅ Frontend typecheck + chat-surface tests + backend tests + ai-backend tests green; no `any` introduced in `library.ts`.
- ✅ Cross-destination "Save to Library" from a chat tool result, an agent message, an inbox run-completion item, and a routine output target — all four paths exercised end-to-end in the e2e smoke.

---

## §19 Anti-goals

Restated as testable invariants:

- ❌ **NOT a drive / folder system.** No nesting. The only filing axis is the optional `project_id`. (See §1.3.)
- ❌ **NOT a real-time multiplayer editor.** Pages are last-write-wins with optimistic concurrency. Cross-audit §3.5: multiplayer is Wave 5+.
- ❌ **NOT a connector.** Library doesn't OAuth into Notion / Drive. Connectors push into Library via the internal endpoint (§14).
- ❌ **NOT a search-everything bar.** Library searches Library only. ⌘K command palette is Phase 11.
- ❌ **NOT a retrieval-config surface.** Users don't tune RAG pipelines from Library. The endpoint is the contract; tuning is admin-config (Wave 7+) or per-agent (in Agents destination, Wave 8+).
- ❌ **NO external-share URLs.** Sharing is via project membership. Cross-tenant sharing → never (cross-audit §3.5).
- ❌ **NO direct LLM provider imports in `services/backend/`.** All embedding / rerank calls route through ai-backend's `/internal/v1/llm/embed`. Phase 0.6 CI guard verifies (cross-audit §5.5).
- ❌ **NO parallel token-usage tracker.** Library extends `Purpose` enum; `runtime_model_call_usage` is the single source of truth (cross-audit §5.5).
- ❌ **NO bytes proxied through the API.** Uploads use signed PUT; downloads use signed GET. Master §3.7 binding.
- ❌ **NO cleartext object-store URLs in audit rows.** Audit records `target_id` only; signed URLs are minted per request with 10-minute TTL (§7.4).
- ❌ **NO PII in telemetry or logs.** Search queries hashed; page bodies never logged; tags-with-PII discouraged via UI hint (§7.4, §11).
- ❌ **NO direct browser API access** — clipboard, notifications, file picker, deep-links go through ports (§15).
- ❌ **NO snapshot of agent or project state at save time** — `source` is recorded but resolves live via `<ItemLink>` per cross-audit §5.3 cascade default.
- ❌ **NO frontend-only ACL.** Every §7 check is server-validated; UI hints only.
- ❌ **NO double-index on indexer crash.** `FOR UPDATE SKIP LOCKED` + `CLAIM_TTL_SECONDS=300` guarantees at-most-once delivery per item.
- ❌ **NO citation back-index leak.** Project members see citation counts they can resolve; cross-tenant chat citations never leak.

---

## §20 References

- [PRD.md](../PRD.md) — workspace shell + composer + thread canvas (the foundation).
- [destinations-master-prd.md](../destinations-master-prd.md) — §3 (enterprise checklist), §4 (shared primitives), §5.5 (Library brief), §7 (dispatch pattern), §8 (Phase 7 budget). Library is the 3rd-listed destination in master §2; Phase 7 = Library per master §8.
- [cross-audit.md](../cross-audit.md) — binding decisions consumed: §1.1 ItemRef (incl. `library_file` / `library_page` / `library_dataset`), §1.2 ports, §1.3 project ACL master rule + `is_project_member` resolver, §1.4 audit `context`, §1.5 filter OR, §1.6 PageHeader, §2.1 branded IDs incl. all five Library ID brands, §2.3 SectionResult, §3.3 ItemLink registry, §3.4 formatRelativeTime, §3.5 deferred-features, §4 shared-primitives prereq, §5.1 request_id, §5.2 SSE, §5.3 cascade default, §5.4 port injection, **§5.5 token-usage `Purpose` enum extension (binding — extend, don't fork)**.
- [implementation-plan.md](../implementation-plan.md) — §2 Phase 7 row (P7-A1 / P7-A2 / P7-A3 / P7-B1 / P7-B2 / P7-C file boundaries), §4 strict merge order, §6 anti-conflict file rules.
- [destinations/projects-prd.md](projects-prd.md) — Phase 6 P6-A1 ships `services/backend/src/backend_app/projects/acl.py::is_project_member` canonical resolver; Library imports it. Project filing UX inherits from Phase 6.
- [destinations/routines-prd.md](routines-prd.md) — Routine `output_target.kind="library_page"` writes into Library via `POST /internal/v1/library/pages`. Phase 5 already specifies the wire shape; Phase 7 implements the receiver.
- [destinations/inbox-prd.md](inbox-prd.md) — Inbox is the producer for "indexing failed" + "save-suggestion" Inbox items (Wave 7 for save-suggestion). Cross-destination "Save to Library" from run-completion items.
- [destinations/chats-canvas-prd.md](chats-canvas-prd.md) — agent replies cite Library items via `<ItemLink>`; "Save to Library" lives on tool-result + agent-message action surfaces. The citation back-index resolves through `library_citations`.
- [destinations/home-prd.md](home-prd.md) — recently-saved Library items appear on Home; Library is a producer.
- [destinations/todos-prd.md](todos-prd.md) — todos may carry `source: ItemRef { kind: "library_*" }` per cross-audit §1.1.
- `services/ai-backend/src/agent_runtime/observability/attribution.py` — `Purpose` enum extended in P7-A3 with `LIBRARY_RETRIEVAL` + `LIBRARY_INDEXING`.
- `services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py::build_chat_model` — single LLM integration point per cross-audit §5.5; Library's embedding + rerank calls route through here.
- `services/backend/src/backend_app/projects/acl.py::is_project_member` — canonical project membership resolver (Phase 6 P6-A1).
- `services/backend/src/backend_app/token_vault.py` — not used by Library (Library has no per-user secret storage); listed only to make clear which adjacent infra Library does NOT touch.
- `services/ai-backend/src/runtime_worker/jobs/retention_sweeper.py` — template for `library_indexer.py` claim loop (§6.2); same per-tenant claim-loop pattern.
- `packages/audit-chain` — audit row writer (existing; cross-audit §1.4 `context` field consumed here).
- Root [`CLAUDE.md`](../../../CLAUDE.md) — compliance section (audit immutability, retention scope, tenant isolation, untrusted-input rules).
- [`services/ai-backend/CLAUDE.md`](../../../services/ai-backend/CLAUDE.md) · [`services/backend/CLAUDE.md`](../../../services/backend/CLAUDE.md) · [`services/backend-facade/CLAUDE.md`](../../../services/backend-facade/CLAUDE.md) · [`packages/api-types/CLAUDE.md`](../../../packages/api-types/CLAUDE.md).
