# Projects Destination — Sub-PRD

**Status:** draft (2026-05-18)
**Owner:** parth (orchestrator) — implementation delegated to phase-6 impl agents
**Master:** [destinations-master-prd.md §5.4](../destinations-master-prd.md#54-projects-projects)
**Foundation:** [PRD.md](../PRD.md) — workspace shell + composer + thread canvas
**Binding cross-PRD decisions:** [cross-audit.md](../cross-audit.md) — `ItemRef` incl. `kind="project"` (§1.1), ports (§1.2), **project-scoped ACL is the master rule and Projects owns it** (§1.3), audit `context` (§1.4), filter axis OR (§1.5), `<PageHeader>` (§1.6), branded `ProjectId` (§2.1), `<ItemLink>` registry (§3.3), cascade default (§5.3)
**Reads from / consumed by:**

- [destinations/inbox-prd.md](inbox-prd.md) — Inbox carries `project_id`; Projects becomes a filter axis on Inbox (§9 below).
- [destinations/todos-prd.md](todos-prd.md) — Todos already define project-scoped reads (§7.2 of that PRD); Projects is the canonical destination registering `<ItemLink kind="project">` and shipping the **canonical** ACL resolver.
- [destinations/routines-prd.md](routines-prd.md) — Routines carry `project_id`; project archival behavior on in-flight Routine fires is in §12 Q4 below.
- [destinations/chats-canvas-prd.md](chats-canvas-prd.md) — Threads carry `project_id`; Phase 1.6 composer surface gap is reviewed in §10 below (no Projects-side delta required).

**Implementation phasing:** [implementation-plan.md](../implementation-plan.md) §2 Phase 6 row (P6-A backend / P6-B chat-surface), §4 merge order

**Design references:**

- master PRD §5.4 — premise + open questions.
- chat1.md project model — starred projects in rail, project-scoped threads, project color/icon.
- `apps/frontend/src/features/chat/runtime/composer/` historical `project_id` carrier (deleted by P1-C; the wire field remains via `Conversation.project_id`).

---

## §1 Premise + user job

### 1.1 What Projects is

A **Project** is the **workspace surface that groups a multi-thread set of related work** under a single ACL boundary with named members. Concretely, it is a durable record carrying:

1. A **name** (required, one line) — e.g. "Acme renewal", "Q3 product launch".
2. A **description** (optional, ≤ 400 chars) — what the project is for.
3. An **icon** (single emoji glyph) and **color hue** (HSL hue 0–359, design-system tokenized).
4. A **status** — `active` or `archived` (single bit; not a free enum).
5. An **owner** (`owner_user_id`) — the principal with full write authority. Transferable; never inherited (see §7 transfer rules).
6. A set of **members** with `role ∈ {owner, editor, viewer}` and `added_at` / `added_by` provenance.
7. A **created_at / updated_at / archived_at** timeline.

A Project is the answer to: _"this body of work — these chats, these todos, these inbox items, these library items, these routines — belongs together, is governed by the same people, and is named."_

Projects is the **13th destination** in the workspace rail, counting Routines as the 12th (see [routines-prd.md](routines-prd.md) §1.2). Implementation-plan §6 extends `ShellDestinationSlug` to add `"projects"` as the 13th slug.

### 1.2 Why a separate destination instead of "project = first-class on every other destination"

Three reasons, in priority order:

1. **A project is a thing, not a tag.** Every resource that carries `project_id` (Todos / Inbox / Library / Memory / Routines / Chats) treats it as a foreign key, not as a string tag. The set of valid `project_id` values, the membership ACL applied at read time, the cascade rule on project archival — these need a single authoritative destination that owns the lifecycle. Master §2.2 "one source of truth per destination" requires it.
2. **Project ACL is the cross-destination master rule.** Cross-audit §1.3 is binding: any resource with `project_id` follows the same read/write rule (owner writes, project-member reads, admin compliance reads, 404 to non-readers). That rule is **defined by Projects** and consumed by every other destination. Without a Projects destination, every other PRD reinvents the rule and drift becomes a bug.
3. **Members + ownership transfer is its own surface.** Adding/removing members, changing roles, transferring ownership — these are governance operations distinct from "doing the work". Burying them in a settings page hides the membership reality from the user. Projects gives membership a first-class home with audit, search, and role-aware affordances.

### 1.3 What Projects is NOT

| Anti-goal                       | Why not                                                                                                                                                                                                                                                                                                                                                |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Channel / conversation**      | Projects are not a stream. They have **no message timeline of their own** — the timeline at `/projects/<id>` is an _activity stream aggregating other destinations' events_, not a place to post. If the user wants to talk, they open or create a Chat **filed under** the project (`chats.project_id`).                                              |
| **Folder / drive**              | Projects are not a hierarchical folder system. They are **flat**: one tenant has many projects, no nesting. Nested projects (sub-projects) is explicitly out of scope (§12 Q7). If users want hierarchy, they tag, file, or rename.                                                                                                                    |
| **Slack channel**               | No "join this channel to follow updates". Members are explicitly added by name. Projects do not advertise themselves to non-members.                                                                                                                                                                                                                   |
| **Org / team / workspace**      | A workspace **contains** projects. A project does not span workspaces (security boundary; cross-audit §3.5 deferred inventory "Cross-tenant sharing → never"). Roles on a project (`owner`/`editor`/`viewer`) are project-local; they don't change the user's tenant role (`owner`/`admin`/`member`/`guest`).                                          |
| **External-collaborator hub**   | Wave 6 does not ship external/guest project members. Master §10 Q3 (cross-destination guest consistency) is open; Projects matches whatever tenant-level decision lands. Until then: a project member must be a tenant member.                                                                                                                         |
| **Templates / forking**         | Project templates ("clone this project's setup for new accounts") are explicitly out of scope (§12 Q6). Out of fear of poor abstractions: until we have ≥ 10 real projects per tenant and observe genuine template patterns, we ship none.                                                                                                             |
| **Permission inheritance tree** | A project does not own connector permissions, tool permissions, or skill permissions on the user's behalf. A routine filed under a project does NOT gain extra connector scope from the project (§12 Q3). Permission stays attached to the **owner** of the resource; project membership is **a read scope and a filing axis**, not a permission lift. |

### 1.4 User success states (what "done" looks like)

- _"I want all the Acme renewal threads, todos, library docs, and inbox items in one place."_ → Create project "Acme renewal"; file existing items via the file-to-project action on each destination; the project detail view shows them aggregated under Chats / Todos / Inbox / Library / Routines tabs.
- _"I want my AE and one engineer to see everything on the Acme renewal but only I can edit the routines / archive the project."_ → Create project; add AE as editor, engineer as viewer; routines filed under the project remain owner-only writes per cross-audit §1.3.
- _"I'm leaving the team; transfer my projects to my replacement."_ → Owner-initiated transfer via `POST /v1/projects/<id>/transfer`; previous owner becomes editor by default; one audit row records the transfer; cross-destination resources retain their original owner attribution (per §11 cascade rules).
- _"This project shipped; archive it but don't delete it."_ → Archive marks `status="archived"` and `archived_at=now()`; default list views hide archived projects; archived projects' contents remain readable to existing members and remain in audit; cannot be added-to until reactivated.
- _"Add the new joiner to all projects she should see."_ → Admin opens Projects destination filtered by project member = joiner's manager; bulk-add joiner as viewer (Wave 6 ships single-add only; bulk-add is Wave 6+).

### 1.5 Relationship to other destinations (single-source-of-truth map)

| Destination    | Project relationship                                                                                                                                                           | Project as filter axis on its list endpoint                                         |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------- |
| **Chats**      | `Conversation.project_id` (already in wire — see chats-canvas-prd §13). New conversations created from a project detail view default to that `project_id` (§9.3).              | `GET /v1/conversations?filter[project_id]=…` (multi-value OR per cross-audit §1.5). |
| **Todos**      | `Todo.project_id`; cross-audit §1.3 enforced by `services/backend/.../todos/`. The canonical resolver for the project-ACL predicate is shipped by **Projects** (§7.4 below).   | `GET /v1/todos?filter[project_id]=…` (already specified in todos-prd §8).           |
| **Inbox**      | `InboxItem.project_id`; project-scoped read added per cross-audit §1.3 (the existing Inbox sub-PRD was authored before that decision and is amended via §13.1 of cross-audit). | `GET /v1/inbox?filter[project_id]=…` (already specified in inbox-prd §4.4).         |
| **Library**    | `LibraryFile.project_id` / `LibraryPage.project_id` / `LibraryDataset.project_id` (Library destination is Phase 7; the field lands when Library lands).                        | Same OR pattern.                                                                    |
| **Routines**   | `Routine.project_id` (already in wire — routines-prd §4.1).                                                                                                                    | `GET /v1/routines?filter[project_id]=…` (already specified in routines-prd §4.5).   |
| **Memory**     | `MemoryItem.project_id` (Phase 11; the field lands when Memory lands).                                                                                                         | Same OR pattern.                                                                    |
| **Agents**     | An agent is NOT filed under a project (agents are workspace-scoped). A project may **reference** agents via membership-like assignment (Wave 7+ — out of scope for Phase 6).   | n/a in Phase 6.                                                                     |
| **Tools**      | Not project-scoped. (Tools are workspace-scoped; per-project tool restrictions deferred — §12 Q3.)                                                                             | n/a.                                                                                |
| **Connectors** | Not project-scoped in Phase 6 (master §10 Q4 is open; Projects matches whatever lands).                                                                                        | n/a.                                                                                |
| **Team**       | A person's project memberships are shown on their Team profile (Phase 10).                                                                                                     | n/a.                                                                                |
| **Home**       | Pinned/starred projects appear on Home as a panel section per master §5.1.                                                                                                     | n/a.                                                                                |

**Single-source-of-truth rule:** the predicate "is user X a member of project P with role R-or-stronger?" has **one implementation** — `services/backend/src/backend_app/projects/acl.py::is_member(tenant_id, project_id, user_id) -> ProjectRole | None`. Every other destination's project-ACL check calls into this module via in-process import (intra-`backend` only) or via the internal `/internal/v1/projects/<id>/membership/<user_id>` endpoint (cross-service from `ai-backend`). No destination reimplements the membership query.

---

## §2 Source-of-truth map

Per master PRD §2.2, each artefact has **exactly one** canonical location.

| Concern                                         | Canonical file                                                                                                                                                                                                                                                                                 | Status                               |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| Wire types                                      | `packages/api-types/src/projects.ts` (NEW)                                                                                                                                                                                                                                                     | introduce; re-export from `index.ts` |
| Branded `ProjectId`                             | `packages/api-types/src/brands.ts` (cross-audit §2.1)                                                                                                                                                                                                                                          | already shipped by SP-1              |
| `<ItemLink kind="project">` resolver registry   | `packages/chat-surface/src/destinations/projects/index.ts` (NEW) — registers the **canonical** resolver via `registerItemRefResolver("project", …)` (SP-1)                                                                                                                                     | introduce                            |
| Destination (router-mounted)                    | `packages/chat-surface/src/destinations/projects/ProjectsDestination.tsx` (NEW)                                                                                                                                                                                                                | introduce                            |
| Context panel                                   | `packages/chat-surface/src/destinations/projects/ProjectsPanel.tsx` (NEW)                                                                                                                                                                                                                      | introduce                            |
| Detail (`/projects/<id>`)                       | `packages/chat-surface/src/destinations/projects/ProjectDetail.tsx` (NEW)                                                                                                                                                                                                                      | introduce                            |
| Editor (`/projects/new`, `/projects/<id>/edit`) | `packages/chat-surface/src/destinations/projects/ProjectEditor.tsx` (NEW)                                                                                                                                                                                                                      | introduce                            |
| Per-tab content (cross-destination tabs)        | `packages/chat-surface/src/destinations/projects/tabs/{ChatsTab,TodosTab,InboxTab,LibraryTab,RoutinesTab,MembersTab,ActivityTab}.tsx` (NEW)                                                                                                                                                    | introduce                            |
| Member-management widgets                       | `packages/chat-surface/src/destinations/projects/members/{MemberRow,RolePicker,InviteMemberDialog,TransferOwnerDialog}.tsx` (NEW)                                                                                                                                                              | introduce                            |
| Activity-stream projector                       | `packages/chat-surface/src/destinations/projects/activity/projectActivityProjector.ts` (NEW)                                                                                                                                                                                                   | introduce                            |
| Backend route module                            | `services/backend/src/backend_app/projects/` (NEW): `routes.py`, `service.py`, `store.py`, `schema.py`, `acl.py`, `events.py`, `activity_projector.py`                                                                                                                                         | introduce                            |
| Backend Postgres schema                         | `services/backend/src/backend_app/projects/schema.py` + Alembic migration                                                                                                                                                                                                                      | introduce                            |
| Facade proxy                                    | `services/backend-facade/src/backend_facade/projects_routes.py` (NEW)                                                                                                                                                                                                                          | introduce                            |
| Internal cross-service ACL endpoint             | `services/backend/src/backend_app/projects/internal_routes.py` (NEW) — `/internal/v1/projects/<id>/membership/<user_id>`                                                                                                                                                                       | introduce                            |
| Activity stream producer                        | Each destination (Inbox / Todos / Library / Routines / Chats) is **already** writing audit rows; the **projector** in `services/backend/src/backend_app/projects/activity_projector.py` subscribes to a per-tenant audit-fanout topic and projects events into `project_activity` rows (§3.7). | introduce                            |
| Frontend HTTP wrappers + SSE                    | `apps/frontend/src/api/projects.ts` (NEW)                                                                                                                                                                                                                                                      | introduce                            |
| App switch case (mount destination)             | `apps/frontend/src/app/App.tsx` (extend)                                                                                                                                                                                                                                                       | extend                               |
| `ShellDestinationSlug` extension                | `packages/chat-surface/src/shell/destinations.ts` — add `"projects"` as the 13th slug                                                                                                                                                                                                          | extend                               |
| Project-scoped ACL helper consumed by others    | `services/backend/src/backend_app/projects/acl.py` — single implementation; consumed by Inbox / Todos / Library / Memory / Routines via in-process import                                                                                                                                      | introduce                            |

A second copy of any of these is a bug.

---

## §3 Architecture

### 3.1 Layout

Standard workspace shell from `ChatShell.tsx` with `<ProjectsPanel>` in the ContextPanel slot. Right rail collapsed for this destination (PRD §10 default; projects does not opt in). List vs detail vs editor pivot lives inside the main pane and is driven by `route.view` + `route.id` per master §4.5:

- `{ view: null, id: null }` → **list** view
- `{ view: null, id: <ProjectId> }` → **detail** view (with cross-destination tabs — §3.4)
- `{ view: "edit", id: <ProjectId> }` → **editor** (edit mode)
- `{ view: "new", id: null }` → **editor** (create mode)
- `{ view: "members", id: <ProjectId> }` → **members tab focus** (detail with Members tab selected; deep-linkable)
- `{ view: "activity", id: <ProjectId> }` → **activity tab focus**

Matches master §4.5 routing convention `/<dest>/<view?>/<id?>`.

### 3.2 List view (`/projects`)

`ProjectsDestination.tsx` (when `route.id == null` and `route.view == null`) renders, top to bottom:

1. `<PageHeader title="Projects" subtitle="Group related work under shared ACL" primaryAction={{ label: "New project", onClick: createNew }} badges={[activeCount, archivedCount, mineCount]} />` (cross-audit §1.6 shape).
2. `<FilterTabs value={filter} options={["all","active","mine","starred","archived"]} counts={countsByFilter} />` — multi-value OR semantics per cross-audit §1.5.
3. **List body** — virtualized when total > 100 (reuse `@tanstack/react-virtual` introduced by Inbox/Todos).

Per-row content (`CardGrid` primitive from SP-1):

```
[ icon · hue ]  [ Project name                           [ member count chip ] [ ⭐ ] ]
                [ description (1 line, truncated)                                       ]
                [ statusPill ] [ ownerChip ] [ updated relative-time ]
                [ activity chips: 12 chats · 4 todos · 2 routines · 7 library ]
                [ hover actions: ▶ Open · ⭐ Star · ⋯ Edit · 📦 Archive · 🗑 Delete ]
```

- Status pill uses cross-audit §1.6 `<StatusPill tone>`: `active` (ok), `archived` (neutral). No `paused` / `draft` — projects don't have a draft state.
- "Updated" shows `formatRelativeTime(updated_at, now)` (cross-audit §3.4).
- Activity chips are **derived counts** sourced from `project_activity_counts` (§5.1) — denormalized for list perf; refreshed by the projector after each event (§3.7).
- `mine` filter = `owner_user_id == current_user OR role IN ('editor','viewer') in project_memberships`.
- `starred` filter = per-user star (separate `project_stars` table — see §5.1).

### 3.3 Panel view (`ProjectsPanel.tsx`)

`ProjectsPanel.tsx` composes the generic `<ContextPanel title="Projects" subtitle="Workspaces for related work">`. Sections, top to bottom:

1. **Quick filters** — same 5 axes as the main `FilterTabs`, listed vertically with counts. One source of truth for filter state.
2. **Search** — debounced 250ms; `GET /v1/projects?q=…` searches over `name + description` (GIN index per §5.2).
3. **Starred** — collapsible list of the user's starred projects (≤ 20 per user; UI caps; backend enforces).
4. **By owner** — list of owners with ≥ 1 visible project, owner-grouping. (Tenant admins see all; non-admins see only their own projects and their members'.)
5. **By recency** — last 10 projects with activity in the past 7 days (sorted by `last_activity_at`).
6. **Footer** — `[+ New project]` CTA mirroring the PageHeader primary action (one tap from any context); link to "Project ACL guide" doc.

### 3.4 Detail view (`/projects/<id>`)

`ProjectDetail.tsx` renders stacked sections:

1. **Header** — icon/hue + name + `[Edit]` + `[⋯]` menu; description (truncated to 2 lines, expandable); ownerChip + updatedAtRelative + memberCountChip; action row: `[Open chat] [⭐ Star] [📦 Archive / Activate] [Transfer ownership] [Delete]`.
2. **Cross-destination tabs** — the central UX of the destination. Tab list: **Chats · Todos · Inbox · Library · Routines · Members · Activity**. Each tab renders an **embedded list** of the corresponding destination's items filtered to `project_id = <this project>`. Tabs are ARIA tabs (master §3.6, native semantics).
   - **Chats tab** — renders `<ItemLink kind="chat">` rows; calls `GET /v1/conversations?filter[project_id]=<id>&sort=updated_at:desc`. Click row → opens the chat in its destination.
   - **Todos tab** — embeds a read-only `<TodosList>` widget (from `destinations/todos/`) filtered to project; the embedded widget exposes a "Open in Todos" link in the upper right. Same `<ItemLink kind="todo">` per row.
   - **Inbox tab** — same pattern with `<InboxList>`; only the current user's inbox items in this project are visible (Inbox visibility is per-recipient; cross-audit §1.3 does not override Inbox's recipient-only read).
   - **Library tab** — embeds `<LibraryList>` (lands with Library Phase 7; tab renders a placeholder "Library coming in Phase 7" until then — explicit empty state, not "TODO"). When Library lands, all three Library kinds (Files / Pages / Datasets) appear with sub-tabs inside the Library tab.
   - **Routines tab** — embeds `<RoutinesList>`; `<ItemLink kind="routine">` per row.
   - **Members tab** — owner-visible member-management surface (§3.5).
   - **Activity tab** — the project's event stream — chats started, todos created/completed, inbox items received, routines fired, library files uploaded, members added (§3.6).

3. **Quick-add row** — the detail header carries a `[+ New chat in this project]` shortcut that opens the chats destination with `project_id` pre-filled (per §9.3 default-project rule).

Behavior:

- **Tab navigation** is keyboard-accessible: Left/Right cycle (wrap), Home/End jump, Enter activates. Tab badges (e.g. "Chats (12)") update from `project_activity_counts` and SSE.
- **Edit** opens `/projects/<id>/edit` (the same editor as create, prefilled).
- **Archive / Activate** calls `PATCH /v1/projects/<id>` with `{ status }`. Optimistic UI with rollback. Archived projects: cross-destination tabs are read-only (per §11.2); editor is closed; "Activate" CTA replaces "Archive".
- **Transfer ownership** opens a modal (§3.5.2). Single endpoint; audited.
- **Delete** soft-deletes; tombstone retained 30 days per §5.3.

### 3.5 Members tab (`/projects/<id>?tab=members` or `/projects/<id>/members`)

The Members tab is the governance surface — owner writes, members read, audited writes.

#### 3.5.1 Member list

Row layout:

```
[ avatar ]  [ Display name        @username · email                ]
            [ role chip ▼ ]   [ added by user · added_at relative ] [ ⋯ ]
```

- **role chip** is a dropdown (only enabled for the owner — others see read-only chip). Choices: `owner` (locked unless transfer), `editor`, `viewer`. Changing role calls `PATCH /v1/projects/<id>/members/<user_id>` with the new role. Audit row written per §6.
- **⋯ menu** (owner-only): "Remove from project" → `DELETE /v1/projects/<id>/members/<user_id>`. Cannot remove the owner; owner must first transfer.
- The current user's own row carries a "Leave project" affordance instead of remove (unless current user is owner; owners must transfer before leaving).

#### 3.5.2 Adding members

Owner clicks `[+ Add member]` → opens `InviteMemberDialog`:

- Single-add input (search-as-you-type against `/v1/team?q=…` from Team destination; resolves to `UserId`).
- Role picker (`editor` or `viewer`; not `owner` — see transfer flow).
- Submits `POST /v1/projects/<id>/members` with `{ user_id, role }`.
- Backend validates the user is in the same tenant; rejects cross-tenant with 422.
- 201 response carries the resulting `ProjectMembership` row; UI prepends it.

Bulk-add is **out of scope for Phase 6**; the dialog is single-add only.

#### 3.5.3 Transfer ownership

Owner clicks `[Transfer ownership]` from the detail header → opens `TransferOwnerDialog`:

- Target user picker (must already be a member with role `editor` or `viewer`; promoting an unrelated user requires adding them first).
- Confirmation: "After transfer you will become an editor on this project. This cannot be undone except by the new owner."
- Submits `POST /v1/projects/<id>/transfer { new_owner_user_id }`.

Backend (single transactional operation):

1. Validate caller is current owner.
2. Validate `new_owner_user_id` is a member.
3. Set `owner_user_id = new_owner_user_id`.
4. Update old owner's role to `editor` (default; configurable per §12 Q5 — orchestrator may opt for `viewer`).
5. Write audit row `project.ownership_transferred` with `context = { from_user_id, to_user_id, old_role_for_previous_owner }`.
6. Emit SSE envelope to all members: `project_ownership_transferred`.

Per cross-audit §1.3, ownership transfer is owner-only; admins cannot force-transfer in Phase 6 (§12 Q1 covers admin-force-transfer policy).

#### 3.5.4 Owner-offboarding cascade

When an owner's `users.disabled_at` is set (IdP deactivation), the daily backend cron (§5.4) detects the disabled-owner condition and writes one `Inbox` item per orphaned project, addressed to tenant admins, with `<ItemRef kind="project">` and CTA "Reassign owner". Admins use the Wave-7+ "admin force-transfer" workflow (out of scope here); until then, the project stays owned by the disabled user and continues to function read-only for members (writes are owner-only; with no owner, no writes). The cascade applies to Routines per routines-prd §13.1; same Inbox item pattern. Until reassignment, the project's child routines auto-pause per routines §7.4 (`pause_reason="owner_offboarded"`).

### 3.6 Activity tab — the cross-destination event stream

The Activity tab renders a chronological list of events across all destinations whose resources are filed under this project. It is the central proof-point that Projects unifies multi-destination work.

Render shape (`ActivityList` primitive from SP-1):

```
[ icon ]  [ Actor display_name · action verb · target preview ]   [ relative-time ]
          [ <ItemLink kind="…"> opens the target ]
```

Example rows (synthetic):

```
📝   Sarah · created todo · "Send renewal proposal to Acme"           5m ago
💬   Marcus · started chat · "Acme contract questions"                12m ago
✅   Atlas (agent) · completed todo · "Pull latest revenue numbers"   1h ago
📥   Inbox · approval request from Atlas · "Acme renewal v2 diff"     2h ago
⏱   Routine fired · "Weekly Acme briefing"                          1d ago
📦   Library file uploaded · "acme-msa-2026.pdf" by Sarah             1d ago
👤   Sarah · added Marcus as editor                                   3d ago
```

Each row resolves via `<ItemLink>` (cross-audit §3.3); clicking opens the target in its native destination. Activity is **read-only**; it is a projection of audit + create events, not an independent log.

Filter chips at top of the tab: `all` (default), `chats`, `todos`, `inbox`, `library`, `routines`, `members`. Multi-value OR per cross-audit §1.5.

Source: the `project_activity` table (§5.1) populated by the **activity projector** (§3.7). Reads are paginated (cursor by `(occurred_at, id)`). Live updates via SSE (§3.8).

### 3.7 Activity projector (server-side)

The `project_activity_projector` is a per-tenant background worker that subscribes to the audit-row fanout topic and projects audit events into `project_activity` rows.

**Why a projector (not a SQL union view):**

- **Performance.** A UNION across 6 destinations' audit tables on every project detail open is O(N projects × M destinations) latency; projecting once at event-time is O(1) per write.
- **DRY.** Each destination's audit row is already the source of truth; the projector copies the discriminating fields (`actor_user_id`, `action`, `target_kind`, `target_id`, `occurred_at`, `project_id`) into a denormalized table keyed by `project_id`.
- **Decoupling.** Project archival, activity-purge on project-delete, retention all operate on the projection — they don't reach into other destinations' tables.
- **Single source of truth.** The audit table remains canonical for compliance / SIEM. The projection is a derived index, regenerable from audit.

**Implementation:** `services/backend/src/backend_app/projects/activity_projector.py` mirrors `services/backend/src/backend_app/jobs/inbox_retention.py`'s loop pattern (claim batch with `FOR UPDATE SKIP LOCKED`, process, advance cursor). Subscribes to the existing audit-fanout (the audit-chain producer already exposes a per-tenant cursor; the projector consumes from the same place SIEM-export does).

The projector emits one `ProjectActivity` row per qualifying audit row. **Qualifying audit rows** are those where the producer destination injects `context.project_id` (cross-audit §1.4 audit `context` field) into the audit row. The producers (Inbox / Todos / Library / Routines / Chats) write `context.project_id` on every create + status-change event; the projector reads it.

Audit rows without `context.project_id` (e.g., a tenant-admin operation) are skipped — they aren't project-scoped.

**Run cadence:** every `PROJECT_ACTIVITY_PROJECTOR_INTERVAL_SECONDS = 5` (low-latency for the activity tab to feel live, but not real-time SSE — the SSE envelope from the audit producer reaches the projector and onward to the project SSE stream in ≤ 5s p95).

**Backfill:** when Phase 6 ships, a one-shot backfill job rewinds the audit cursor to a tenant-configurable window (default 90 days) and projects historical rows. Idempotent via UNIQUE `(tenant_id, audit_id)`.

### 3.8 SSE — real-time membership + activity push

Two streams converge into one SSE endpoint, multiplexed by event type:

- `/v1/projects/stream` (SSE) — server-push for project lifecycle and project-scoped activity.
- Each envelope: `{ sequence_no, event_type, project_id, payload, emitted_at }`.

Event types:

| `event_type`                     | Producer                                           | Payload                                                             | Audience                                                                                                                                                      |
| -------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `project_created`                | Backend `POST /v1/projects` succeeds               | full `Project`                                                      | All tenant members (visible projects only; per-recipient ACL filter on broadcast).                                                                            |
| `project_updated`                | `PATCH /v1/projects/{id}`                          | full `Project` after change                                         | Members only.                                                                                                                                                 |
| `project_archived` / `activated` | activate / archive endpoints                       | `{ project_id, archived_at }` / `{ project_id, activated_at }`      | Members.                                                                                                                                                      |
| `project_deleted`                | `DELETE /v1/projects/{id}`                         | `{ project_id }` (tombstone marker)                                 | Members.                                                                                                                                                      |
| `project_member_added`           | `POST /v1/projects/{id}/members`                   | `ProjectMembership`                                                 | **All affected members AND the newly-added user** — the added user's frontend subscribes and gets the envelope; UI auto-adds the project to the user's panel. |
| `project_member_removed`         | `DELETE /v1/projects/{id}/members/{user_id}`       | `{ project_id, user_id }`                                           | Remaining members AND the removed user (so the removed user's UI removes the project from their list).                                                        |
| `project_member_role_changed`    | `PATCH /v1/projects/{id}/members/{user_id}`        | `ProjectMembership` after change                                    | Members.                                                                                                                                                      |
| `project_ownership_transferred`  | `POST /v1/projects/{id}/transfer`                  | `{ project_id, from_user_id, to_user_id, previous_owner_new_role }` | Members.                                                                                                                                                      |
| `project_activity_appended`      | Activity projector writes a `project_activity` row | `ProjectActivity` row                                               | Members of the project only.                                                                                                                                  |

Frontend reconnect: `GET /v1/projects/stream?after_sequence=N` per cross-audit §5.2. Server replays buffered envelopes with `sequence_no > N`. Buffer retention: 24h (any longer is full-reload territory).

Single SSE endpoint per master pattern. Per-event ACL is enforced at fan-out — the same envelope is filtered by recipient membership before delivery.

### 3.9 Composer surface gap (Phase 1.6 cross-reference)

[Phase 1.6 composer surface gap](../implementation-plan.md) — the composer needs an affordance to attach a `project_id` to a new chat. This is a **composer responsibility, not a Projects-destination responsibility**. The composer adds a project picker (mention popover for `#project-name` per master §4.6 `<MentionPopover>`); the Projects destination ships the **read-side `<ItemLink kind="project">`** that the composer's mention popover uses to render selected projects.

No Projects-side delta is required beyond:

- registering `<ItemLink kind="project">` resolver (already in §2 source-of-truth map),
- exposing a `GET /v1/projects?q=…` endpoint with `name` / `description` search (already in §4.2),
- ensuring the search endpoint returns lightweight rows (no member lists) — see §4.1 `ProjectSummary` (vs `Project`) types.

The composer's project picker is shipped as part of Phase 1.6, not Phase 6. This sub-PRD does not redesign the composer.

---

## §4 Wire contracts

### 4.1 Types (`packages/api-types/src/projects.ts`)

```typescript
import type { ProjectId, TenantId, UserId } from "./brands";
import type { ItemRef, ItemKind } from "./refs";

export type ProjectStatus = "active" | "archived";

export type ProjectRole = "owner" | "editor" | "viewer";

/** Color hue (HSL 0–359). Lightness + saturation are design-system fixed. */
export type ProjectColorHue = number;

/** Validated server-side as a single emoji glyph (no multi-codepoint strings
 *  beyond skin-tone variants per Unicode emoji ZWJ rules). */
export type ProjectIconEmoji = string;

export interface Project {
  readonly id: ProjectId;
  readonly tenant_id: TenantId;
  readonly owner_user_id: UserId;
  readonly name: string; // ≤ 80 chars
  readonly description: string; // ≤ 400 chars (defaults to '')
  readonly icon_emoji: ProjectIconEmoji;
  readonly color_hue: ProjectColorHue;
  readonly status: ProjectStatus;
  readonly archived_at: string | null; // ISO; present iff status='archived'
  readonly created_at: string;
  readonly updated_at: string;
  readonly last_activity_at: string | null; // denormalized; advanced by projector
  /** Denormalized counts for list-view perf; refreshed by projector. */
  readonly counts: ProjectActivityCounts;
  /** Caller-relative — present iff the caller is a member; null otherwise. */
  readonly viewer_role: ProjectRole | null;
  /** Caller-relative — has the caller starred this project? */
  readonly viewer_starred: boolean;
}

export interface ProjectActivityCounts {
  readonly chats: number;
  readonly todos_open: number;
  readonly todos_done: number;
  readonly inbox_items: number; // recipient-scoped — viewer's count, not total
  readonly library_items: number; // file + page + dataset rollup
  readonly routines_active: number;
  readonly members: number;
}

/** Lightweight projection for list endpoints and `<ItemLink>` resolvers. */
export interface ProjectSummary {
  readonly id: ProjectId;
  readonly tenant_id: TenantId;
  readonly name: string;
  readonly description: string;
  readonly icon_emoji: ProjectIconEmoji;
  readonly color_hue: ProjectColorHue;
  readonly status: ProjectStatus;
  readonly owner_user_id: UserId;
  readonly viewer_role: ProjectRole | null;
  readonly viewer_starred: boolean;
  readonly counts: ProjectActivityCounts;
  readonly last_activity_at: string | null;
  readonly updated_at: string;
}

export interface ProjectMembership {
  readonly project_id: ProjectId;
  readonly user_id: UserId;
  readonly role: ProjectRole;
  readonly added_at: string;
  readonly added_by: UserId;
}

export interface ProjectActivity {
  readonly id: string; // ProjectActivityId — branded internally; opaque to clients
  readonly tenant_id: TenantId;
  readonly project_id: ProjectId;
  readonly actor_user_id: UserId | null; // null = system/automation
  readonly actor_display_name: string; // denormalized
  readonly action: string; // dotted form mirroring audit, e.g. "todo.create"
  readonly kind: ItemKind; // discriminator for filter chips
  readonly ref: ItemRef; // resolves via <ItemLink>
  readonly preview: string; // ≤ 200 chars; denormalized title / summary
  readonly occurred_at: string; // ISO
}

export interface ProjectListResponse {
  readonly items: ReadonlyArray<ProjectSummary>;
  readonly next_cursor: string | null;
}

export interface ProjectMembershipListResponse {
  readonly items: ReadonlyArray<ProjectMembership>;
  readonly next_cursor: string | null;
}

export interface ProjectActivityListResponse {
  readonly items: ReadonlyArray<ProjectActivity>;
  readonly next_cursor: string | null;
}

export type ProjectStreamEventType =
  | "project_created"
  | "project_updated"
  | "project_archived"
  | "project_activated"
  | "project_deleted"
  | "project_member_added"
  | "project_member_removed"
  | "project_member_role_changed"
  | "project_ownership_transferred"
  | "project_activity_appended";

export interface ProjectStreamEnvelope {
  readonly sequence_no: number;
  readonly event_type: ProjectStreamEventType;
  readonly project_id: ProjectId;
  readonly payload:
    | Project
    | ProjectSummary
    | ProjectMembership
    | ProjectActivity
    | {
        project_id: ProjectId;
        user_id?: UserId;
        archived_at?: string;
        activated_at?: string;
        from_user_id?: UserId;
        to_user_id?: UserId;
        previous_owner_new_role?: ProjectRole;
      };
  readonly emitted_at: string;
}
```

`ItemRef` is extended (already in SP-1 from cross-audit §1.1) to include `{ kind: "project"; id: ProjectId }`. Projects ships the canonical resolver at package-load.

### 4.2 Endpoints (facade — what apps call)

| Method | Path                                  | Purpose                                                                                                                       |
| ------ | ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/v1/projects`                        | List. Filter axes: `status` (OR), `owner_user_id` (OR), `member_user_id` (OR), `starred=true`, `q`, `sort`. Cursor-paginated. |
| GET    | `/v1/projects/{id}`                   | Single project with caller-relative `viewer_role` + `viewer_starred` + `counts`.                                              |
| POST   | `/v1/projects`                        | Create. Body: `{ name, description?, icon_emoji, color_hue }`. Owner = caller. Creator auto-added as owner-membership row.    |
| PATCH  | `/v1/projects/{id}`                   | Mutate `name` / `description` / `icon_emoji` / `color_hue` / `status` (with archive transitions). Owner-only.                 |
| DELETE | `/v1/projects/{id}`                   | Soft delete. Tombstone retained per §5.3. Owner-only.                                                                         |
| POST   | `/v1/projects/{id}/archive`           | Set `status='archived'`, write `archived_at`. Owner-only.                                                                     |
| POST   | `/v1/projects/{id}/activate`          | Set `status='active'`, clear `archived_at`. Owner-only.                                                                       |
| POST   | `/v1/projects/{id}/transfer`          | Transfer ownership. Body: `{ new_owner_user_id, previous_owner_new_role? }` (defaults to `editor`). Owner-only.               |
| POST   | `/v1/projects/{id}/star` / `unstar`   | Toggle viewer-relative star. Any member.                                                                                      |
| GET    | `/v1/projects/{id}/members`           | List members. Members only.                                                                                                   |
| POST   | `/v1/projects/{id}/members`           | Add member. Body: `{ user_id, role }`. Owner-only.                                                                            |
| PATCH  | `/v1/projects/{id}/members/{user_id}` | Change role. Body: `{ role }`. Owner-only.                                                                                    |
| DELETE | `/v1/projects/{id}/members/{user_id}` | Remove member. Owner-only (or self-remove via `DELETE …/members/me` shortcut). Cannot remove owner; transfer first.           |
| GET    | `/v1/projects/{id}/activity`          | Paginated activity stream. Filter: `kind` (OR per cross-audit §1.5). Members + compliance admin.                              |
| GET    | `/v1/projects/stream`                 | SSE. Reconnect via `?after_sequence=N`. Backend enforces per-recipient ACL on each envelope.                                  |

### 4.3 Endpoints (internal — used by other backend modules and ai-backend)

| Method | Path                                              | Purpose                                                                                                                                                                                                |
| ------ | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| GET    | `/internal/v1/projects/{id}/membership/{user_id}` | Returns `{ role: ProjectRole \| null }`. Used by `ai-backend` (which cannot in-process import `backend.projects.acl`) to enforce project-scoped reads on its surfaces (runs / approvals / etc.).       |
| GET    | `/internal/v1/projects/{id}/exists`               | Returns `200 {exists, archived, deleted}`. Lightweight check used by cross-destination producers to validate `project_id` references on insert.                                                        |
| POST   | `/internal/v1/projects/{id}/activity`             | Producer-side write to project activity. Service-token + claim-asserted. Used **only** if the destination cannot reach the audit-fanout path (back-compat; not the primary projector path — see §3.7). |

### 4.4 Filter / sort allowlist (per cross-audit §1.5)

- `filter[status]`: `active` | `archived` (multi-value OR).
- `filter[owner_user_id]`: UserId (multi-value OR; tenant-admin scope to see others').
- `filter[member_user_id]`: UserId (multi-value OR; visibility = projects where target user is a member; resolves to `projects WHERE id IN (SELECT project_id FROM project_memberships WHERE user_id IN (...))`).
- `filter[starred]`: `true` (single value; caller-relative).
- `q`: full-text on `name + description` (GIN, §5.2). No `icon_emoji` search.
- `sort`: `updated_at:desc` (default) | `updated_at:asc` | `name:asc` | `name:desc` | `created_at:desc` | `last_activity_at:desc`.

`filter[member_user_id]` semantics need attention: a non-admin caller can use it ONLY to filter to projects **they themselves** are a member of (i.e., `member_user_id=me`). Cross-user `member_user_id` queries by non-admins return 403. Admins (`tenant_role IN ('owner','admin')`) can use any UserId. This prevents membership-graph harvesting.

---

## §5 Storage + retention

### 5.1 Tables (Postgres, owned by `services/backend`)

**`projects`** — one row per project.

| Column                      | Type                           | Notes                                                                    |
| --------------------------- | ------------------------------ | ------------------------------------------------------------------------ |
| `id`                        | uuid PK                        | Branded `ProjectId` on the wire.                                         |
| `tenant_id`                 | uuid NOT NULL                  | Filter on every query.                                                   |
| `owner_user_id`             | uuid NOT NULL                  | FK to `users` (LOOSE — owner_offboarded handled via cron, §3.5.4).       |
| `name`                      | text NOT NULL (len ≤ 80)       | Non-blank; UNIQUE per `(tenant_id, lower(name))` to prevent confusion.   |
| `description`               | text NOT NULL DEFAULT ''       | Len ≤ 400.                                                               |
| `icon_emoji`                | text NOT NULL                  | Single emoji glyph (server-validated).                                   |
| `color_hue`                 | int NOT NULL                   | 0–359.                                                                   |
| `status`                    | text NOT NULL DEFAULT 'active' | `active` / `archived`.                                                   |
| `archived_at`               | timestamptz NULL               | Present iff status='archived'.                                           |
| `last_activity_at`          | timestamptz NULL               | Denormalized — advanced by projector on every `project_activity` insert. |
| `created_at` / `updated_at` | timestamptz NOT NULL           |                                                                          |
| `deleted_at`                | timestamptz NULL               | Soft-delete marker.                                                      |

Constraints:

- CHECK `status IN ('active','archived')`.
- CHECK `(status='archived') = (archived_at IS NOT NULL)`.
- CHECK `color_hue BETWEEN 0 AND 359`.
- CHECK `length(name) BETWEEN 1 AND 80`.
- UNIQUE `(tenant_id, lower(name)) WHERE deleted_at IS NULL`.

**`project_memberships`** — composite-PK row per (project, user).

| Column       | Type                 | Notes                                                                        |
| ------------ | -------------------- | ---------------------------------------------------------------------------- |
| `project_id` | uuid NOT NULL        | FK ON DELETE CASCADE.                                                        |
| `user_id`    | uuid NOT NULL        | FK ON DELETE CASCADE (when user is hard-deleted, membership row disappears). |
| `tenant_id`  | uuid NOT NULL        | Denormalized for query convenience and RLS.                                  |
| `role`       | text NOT NULL        | `owner` / `editor` / `viewer`.                                               |
| `added_at`   | timestamptz NOT NULL |                                                                              |
| `added_by`   | uuid NOT NULL        | FK to `users` (LOOSE).                                                       |

Constraints:

- PRIMARY KEY `(project_id, user_id)`.
- CHECK `role IN ('owner','editor','viewer')`.
- One owner row per project: PARTIAL UNIQUE on `(project_id) WHERE role='owner'`.
- `tenant_id` consistency: trigger ensures `project_memberships.tenant_id = projects.tenant_id` on insert/update (RLS enforcement is the secondary wall).

**`project_stars`** — per-user star.

| Column       | Type / Notes                        |
| ------------ | ----------------------------------- |
| `tenant_id`  | uuid NOT NULL                       |
| `user_id`    | uuid NOT NULL                       |
| `project_id` | uuid NOT NULL, FK ON DELETE CASCADE |
| `created_at` | timestamptz NOT NULL                |

PRIMARY KEY `(tenant_id, user_id, project_id)`. Backend enforces ≤ 20 stars per user (UI also caps).

**`project_activity`** — projected event stream (the `project_activity` projector writes here).

| Column                                      | Type / Notes                                                     |
| ------------------------------------------- | ---------------------------------------------------------------- |
| `id` (uuid PK) / `tenant_id` / `project_id` |                                                                  |
| `audit_id`                                  | uuid NOT NULL — idempotency key; UNIQUE `(tenant_id, audit_id)`. |
| `actor_user_id`                             | uuid NULL                                                        |
| `actor_display_name`                        | text NOT NULL (denormalized; refreshed on user rename via cron)  |
| `action`                                    | text NOT NULL (e.g., `"todo.create"`)                            |
| `kind`                                      | text NOT NULL (`ItemKind` discriminator)                         |
| `ref_kind`                                  | text NOT NULL — copied from `ref.kind` for filter indices        |
| `ref_id`                                    | text NOT NULL — copied from `ref.id`                             |
| `preview`                                   | text NOT NULL (len ≤ 200)                                        |
| `occurred_at`                               | timestamptz NOT NULL                                             |

**`project_activity_counts`** — single-row-per-project denormalized counts (for list view).

| Column                                                                                                  | Type / Notes           |
| ------------------------------------------------------------------------------------------------------- | ---------------------- |
| `tenant_id` + `project_id` (PK)                                                                         |                        |
| `chats` / `todos_open` / `todos_done` / `inbox_items` / `library_items` / `routines_active` / `members` | int NOT NULL DEFAULT 0 |
| `recomputed_at`                                                                                         | timestamptz NOT NULL   |

Updated incrementally by the projector on every event. A nightly reconciliation job recomputes from authoritative tables to repair drift.

### 5.2 Indexes

- `projects_tenant_status_idx` — B-tree on `(tenant_id, status, last_activity_at DESC NULLS LAST) WHERE deleted_at IS NULL` — primary list query.
- `projects_owner_idx` — B-tree on `(tenant_id, owner_user_id) WHERE deleted_at IS NULL` — owner-filter.
- `projects_search_idx` — GIN on `to_tsvector('simple', name || ' ' || description) WHERE deleted_at IS NULL` — search.
- `project_memberships_user_idx` — B-tree on `(tenant_id, user_id)` — "what projects am I in".
- `project_memberships_project_idx` — B-tree on `(project_id)` — members of a project.
- `project_stars_user_idx` — B-tree on `(tenant_id, user_id)`.
- `project_activity_project_time_idx` — B-tree on `(tenant_id, project_id, occurred_at DESC, id DESC)` — activity tab list + cursor pagination.
- `project_activity_kind_idx` — B-tree on `(tenant_id, project_id, kind, occurred_at DESC)` — filter-by-kind.
- `project_activity_audit_idx` — UNIQUE on `(tenant_id, audit_id)` — projector idempotency.

### 5.3 Retention (per master §3.3)

- **Project rows**: indefinite while `status != archived OR archived_at < now() - PROJECT_ARCHIVED_RETENTION_DAYS`. Default `PROJECT_ARCHIVED_RETENTION_DAYS = 365`; tenant-configurable. After window, archived projects are NOT auto-hard-deleted (admin must explicitly delete; the list view hides them by default already). Soft-deleted projects (`deleted_at` set): retained 30 days, then hard-deleted.
- **Memberships**: cascade with parent project on hard-delete. On user hard-delete, member rows disappear via FK cascade. Membership history (who-added-whom-when) is preserved in audit (immutable; cross-audit §5.3).
- **Stars**: cascade with parent project on hard-delete.
- **Activity rows**: 365 days from `occurred_at`. Same retention as the source audit rows. Hard-deleted on project hard-delete.
- **Activity counts**: cascade with parent project.
- **Audit rows**: per master rule (365d, append-only, anonymized on tenant GDPR delete).

### 5.4 Cleanup job

Daily backend cron `services/backend/src/backend_app/jobs/projects_retention.py`:

1. Hard-delete soft-deleted projects past 30 days.
2. Hard-delete `project_activity` rows past 365 days.
3. Detect disabled-owner projects (`owner.disabled_at IS NOT NULL`); write Inbox item per project to tenant admins (idempotent — checks existing inbox row for `(producer_id='projects', external_ref='owner-offboarded-{project_id}')`).
4. Reconcile `project_activity_counts` from authoritative tables — repair drift introduced by projector lag or audit-cursor reset.
5. Refresh denormalized `actor_display_name` on `project_activity` rows where the actor user's display name has changed in the past 24h (small batch; bounded).
6. Emit `project.retention_cleanup_run` audit summary per tenant.

Same idempotent, interruptible (`FOR UPDATE SKIP LOCKED`) pattern as Inbox / Routines retention crons.

---

## §6 Audit (per master §3.2 + cross-audit §1.4 `context` field)

Every state-changing operation writes an audit row through `packages/audit-chain`. Audit row's `context` field per cross-audit §1.4.

### 6.1 Action taxonomy

| Action                          | Trigger                                                    | `context` (cross-audit §1.4)                                                                                                                      |
| ------------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `project.created`               | `POST /v1/projects` succeeds                               | `{ name, owner_user_id, project_id }`                                                                                                             |
| `project.updated`               | `PATCH /v1/projects/{id}`                                  | `{ changed_fields }`; `before_state` + `after_state` populated                                                                                    |
| `project.archived`              | archive endpoint                                           | `{ archived_at }`                                                                                                                                 |
| `project.activated`             | activate endpoint                                          | `{ activated_at, previously_archived_at }`                                                                                                        |
| `project.deleted`               | `DELETE /v1/projects/{id}`                                 | `{ soft: true }`                                                                                                                                  |
| `project.member_added`          | `POST /v1/projects/{id}/members`                           | `{ user_id, role, added_by }`                                                                                                                     |
| `project.member_removed`        | `DELETE /v1/projects/{id}/members/{user_id}`               | `{ user_id, removed_by, previous_role }`                                                                                                          |
| `project.member_role_changed`   | `PATCH /v1/projects/{id}/members/{user_id}`                | `{ user_id, from_role, to_role, changed_by }`                                                                                                     |
| `project.ownership_transferred` | `POST /v1/projects/{id}/transfer`                          | `{ from_user_id, to_user_id, previous_owner_new_role }`                                                                                           |
| `project.starred` / `unstarred` | star / unstar endpoints                                    | `{ user_id }`                                                                                                                                     |
| `project.activity_projected`    | projector writes a row                                     | `{ source_audit_id, ref_kind, ref_id }` — NOT audited per-row by default (would dwarf signal); only on projector backfill/reconciliation summary. |
| `project.activity_reconciled`   | nightly reconciliation                                     | `{ rows_repaired, counts_drift }`                                                                                                                 |
| `project.retention_cleanup_run` | daily cron                                                 | `{ projects_hard_deleted, activity_rows_deleted }`                                                                                                |
| `project.compliance_read`       | tenant-admin `GET /v1/projects/{id}` on non-member project | `{ admin_user_id }`. Single-row audit per compliance read (mirrors Inbox §6.1 `inbox.item_body_accessed`).                                        |

**Critical:** project-scoped reads of other destinations' resources (e.g., a tenant admin reading a non-member project's todos) write their **own** destination's compliance-read audit row (`todo.compliance_read`, etc.). Projects does not double-audit cross-destination reads — each destination owns its own audit row.

### 6.2 What is NOT audited

- List queries (`GET /v1/projects`) — auditing every list scrape would dwarf signal with noise.
- SSE connections themselves (the projector + the event producer audit the upstream event; the fan-out is not separately audited).
- Reads of an individual project (`GET /v1/projects/{id}`) BY A MEMBER — non-sensitive metadata. Tenant-admin compliance reads of non-member projects ARE audited (§6.1 row above).
- Per-row activity projection writes (`project.activity_projected` aggregated into the reconciliation summary instead of one-row-per-event; the source audit row IS the canonical record).

Audit rows are append-only (audit-chain enforces). Exportable via the existing SIEM pipeline.

---

## §7 Authorization

This section is **the canonical statement of cross-audit §1.3 project-scoped ACL**. Other destinations consume the same rules via `acl.py::is_member`.

### 7.1 Visibility rules (read)

Per cross-audit §1.3 (project-scoped access — the master rule):

- A `Project` is **visible** when:
  - `tenant_id` matches the verified bearer's tenant claim, **AND**
  - `owner_user_id` matches the verified bearer's user_id, **OR**
  - `project_memberships` has a row for `(project_id, bearer.user_id)` (any role), **OR**
  - the bearer has the `tenant_admin` / `tenant_owner` role (compliance read; audited).
- Non-readers see **`404`** (existence-not-leaked default per cross-audit §1.3). NEVER `403`.

### 7.2 Mutation rules (write)

| Action                             | Required principal                                                                      |
| ---------------------------------- | --------------------------------------------------------------------------------------- |
| Create project                     | Any tenant member (`member` / `admin` / `owner`); never `guest`.                        |
| PATCH project (rename, desc, icon) | `owner_user_id` only.                                                                   |
| Archive / activate                 | `owner_user_id` only.                                                                   |
| Delete (soft)                      | `owner_user_id` only.                                                                   |
| Add member                         | `owner_user_id` only.                                                                   |
| Remove member                      | `owner_user_id` only — OR — the member removing **themselves** (`DELETE …/members/me`). |
| Change member role                 | `owner_user_id` only.                                                                   |
| Transfer ownership                 | `owner_user_id` only. New owner must already be a member (`editor` or `viewer`).        |
| Star / unstar                      | Any visible-to-caller project; per-user scoped.                                         |
| Read activity                      | Any project member; tenant-admin compliance read (audited).                             |

**Editor / viewer write authority on member rows:** none in Phase 6. Editors can mutate THEIR OWN child resources (their own todos, inbox items, library uploads) but cannot mutate the project's membership or metadata. This matches cross-audit §1.3: "Write: owner only. Project members cannot mutate someone else's project-filed item."

**Tenant-admin write authority on projects:** none in Phase 6. Admin force-pause / force-transfer for departed users is Wave 7+ (§12 Q1).

### 7.3 Project-scoped reads ON OTHER destinations (the master rule, canonically stated here)

This is the rule cross-audit §1.3 made binding. Every resource carrying `project_id` follows it; Projects ships the canonical predicate:

```python
# services/backend/src/backend_app/projects/acl.py
def is_member(
    tenant_id: TenantId,
    project_id: ProjectId,
    user_id: UserId,
) -> ProjectRole | None:
    """Returns the user's role on the project, or None if not a member.

    Single source of truth for the cross-audit §1.3 master ACL rule.
    Called by todos/, inbox/, library/, memory/, routines/ to gate
    project-scoped reads.

    Tenant-admin compliance reads do NOT route through this function;
    they are gated by `tenant_role IN ('owner','admin')` at the
    consuming destination's route layer and audited there.
    """
```

Consuming destinations apply the rule as:

```python
# Read access on a resource X with X.project_id IS NOT NULL:
allowed = (
    X.owner_user_id == current_user_id
    or (X.project_id is not None
        and projects.acl.is_member(tenant_id, X.project_id, current_user_id) is not None)
    or current_user.tenant_role in ('owner','admin')  # compliance read; audited
)
# Non-readers: 404, NEVER 403.
```

`ai-backend` cannot in-process import `backend.projects.acl`. It uses the internal endpoint:

```
GET /internal/v1/projects/{id}/membership/{user_id}
→ 200 { role: "owner" | "editor" | "viewer" | null }
```

Result is cached per-request (TTL = request scope only; no cross-request cache to avoid TOCTOU).

### 7.4 Cross-destination ACL test discipline

Every destination consuming `is_member` MUST ship tests for the 4-case matrix on its resource:

1. owner-of-resource is reader → 200.
2. project-member-non-owner is reader → 200 (read); 403 (write).
3. tenant-admin (non-member) reader → 200 with audit row.
4. non-member, non-admin → **404** (not 403; see cross-audit §1.3 existence-not-leaked).

Projects ships shared test fixtures (`services/backend/tests/integration/projects/test_acl_matrix.py`) that other destinations' test suites import to verify the matrix uniformly.

### 7.5 Cross-tenant safety

Master §3.1: `tenant_id` is derived from the verified bearer; never accepted from request body. The facade rejects requests carrying a body `tenant_id`. Backend's row-level checks are the second wall.

A project owner moving to a different tenant (extremely rare; covered by IdP off-boarding + new-tenant-on-board) results in the owner being treated as offboarded for the original tenant's projects — §3.5.4 handles it.

### 7.6 Sensitive-field handling

Projects has no inherently sensitive fields (no secrets, no PII content body, no instructions text). However:

- **Member email addresses** are NOT included in `ProjectMembership` payloads — only `user_id` is exposed. The frontend resolves emails via the Team destination (which has its own visibility rules).
- **`actor_display_name` on activity rows** is denormalized to the user's IdP display name at projection time; rename does not retroactively rewrite (the daily cron has a small refresh window for the past 24h; deeper history is the historical name, a feature not a bug for forensics).

---

## §8 Pagination + search (per master §3.5 + cross-audit §1.5)

- **Cursor pagination.** `?after=<opaque-cursor>&limit=<n>`. Default `limit=50`, max `limit=200`. Cursor encodes `(sort_field, id)` for stable scrolling under inserts.
- **Multi-value filter axis = OR within axis; AND across axes** per cross-audit §1.5.
- **Search.** `?q=<query>` runs PostgreSQL `plainto_tsquery('simple', q)` against `name || ' ' || description` via the GIN index. Debounced client-side at 250ms.
- **Sort allowlist** per §4.4.

Combined example:

```
GET /v1/projects?filter[status]=active&filter[starred]=true&q=acme&sort=last_activity_at:desc&limit=50
```

Activity-tab pagination:

```
GET /v1/projects/<id>/activity?filter[kind]=todo&filter[kind]=routine&sort=occurred_at:desc&limit=50&after=<cursor>
```

Members-tab pagination:

```
GET /v1/projects/<id>/members?sort=added_at:desc&limit=50
```

---

## §9 Cross-destination project filter (Projects as a filter axis on every other destination)

A core deliverable of Phase 6 is **wiring Projects as a filter axis** on every other destination's list endpoint. The wire fields already exist (each destination already specifies `?filter[project_id]=…`); what Phase 6 ships is the **UI binding**.

### 9.1 The pattern

Every list endpoint that accepts `filter[project_id]` (Chats / Todos / Inbox / Library / Routines) gains a **project filter chip** in its destination's panel:

```
[ Project ▼ ]
  ──────────────
  ⭐ Starred (3)
  Active (12)
  Archived (1)
  ──────────────
  All projects
  No project (Unfiled)
```

Clicking a project applies `?filter[project_id]=<id>`. Multiple selections OR together (cross-audit §1.5).

### 9.2 Implementation in Phase 6 — light touch

P6-B (chat-surface) ships:

- `packages/chat-surface/src/destinations/projects/widgets/ProjectFilterChip.tsx` — the shared filter chip widget. Each consuming destination imports it and wires it into its own panel; the panel updates the same `route` filter state already plumbed.
- `packages/chat-surface/src/destinations/projects/widgets/useProjectsForFilter.ts` — a hook that subscribes to a lightweight `GET /v1/projects?filter[member_user_id]=me&limit=200` for the filter chip's dropdown population. Cache-shared via React Query.

Consuming destinations (Chats / Todos / Inbox / Routines) add ONE line in their `XPanel.tsx` to mount `<ProjectFilterChip />`. Library does so when Library lands (Phase 7).

### 9.3 Default project for new resources

Per master §10 Q (one of the open product questions cross-referenced from cross-audit §3.5):

- **In a project detail view**: new chats / new todos / new routines / etc. default `project_id = <current project>`. The composer / inline-add UI shows a "filed under: <project name>" chip with `×` to clear.
- **In `/chats`, `/todos`, `/inbox`, `/routines` direct list views**: new resources default `project_id = null` (Unfiled).
- **In a destination's panel filtered to a specific project**: inline-add inherits the panel's filter (consistent with todos-prd §6 / Phase-3 Q6 decision).
- **From the rail's `[+]`** "New chat" button: `project_id = null` unless the user is currently in a project detail view (then current project).

This rule is **system-wide**; it lives in `apps/frontend/src/app/createContextDefaults.ts` (NEW helper in P6-B) and is called by every "new resource" flow. The composer's project picker (Phase 1.6) reads from the same helper.

### 9.4 What changes server-side — minimal

Each consuming destination already accepts `filter[project_id]`; no server changes in Phase 6 beyond:

- **Inbox**: cross-audit §1.3 makes Inbox project-scoped read explicit (the original Inbox PRD pre-dated that decision). P6-A adds the project-read pathway to Inbox's read route (already in spec but not yet implemented at the time of Phase 4 merge; Phase 6 confirms).
- **Library**: not yet implemented (Phase 7 ships it).
- **Memory**: not yet implemented (Phase 11).
- **Chats**: already supports `filter[project_id]` on `/v1/conversations` — no change.
- **Todos / Routines**: already support — no change.

### 9.5 SSE-driven filter updates

When `project_member_added` or `project_member_removed` envelopes arrive on `/v1/projects/stream`, the frontend re-fetches the `useProjectsForFilter` hook's data. Filter chips are kept fresh without manual refresh.

---

## §10 Composer surface gap (Phase 1.6 cross-reference)

Phase 1.6 (composer surface gap) is the composer-side affordance for typing `#project-name` and attaching a `project_id` to a new chat. Projects' role in that delivery:

- **Read-side resolver** — Projects' `<ItemLink kind="project">` resolver (shipped by P6-B's `index.ts`) is what the composer's `<MentionPopover>` calls for project rendering.
- **Search endpoint** — `GET /v1/projects?q=…&limit=20` is sufficient for the popover's typeahead. No new endpoint.
- **No composer changes required from Projects.** The composer ships its own picker in Phase 1.6; Projects ships only the read surface and resolver.

Phase 6 is sequenced after Phase 1.6 in implementation-plan §4; if Phase 1.6 lands first (likely), Projects validates the composer's project picker on merge and adds a smoke test. If Phase 6 lands first (less likely; depends on Phase 1.6's scope), the composer reads from a Phase-1.6-stub resolver registered by Projects.

---

## §11 Performance + retention details

### 11.1 Performance budgets (per master §3.7)

- **LCP < 2.5s** on `/projects` list endpoint (cold load, broadband). Index `projects_tenant_status_idx` covers; counts are denormalized via `project_activity_counts`.
- **INP < 200ms** on filter changes, member-add interactions, star toggles. Star toggle is optimistic.
- **Detail-page LCP < 2.5s** — single round-trip for `GET /v1/projects/{id}` returns metadata + caller-relative role + counts. Each tab lazy-loads on tab activation. Tabs' lists support cursor pagination; first page is 50 rows.
- **Activity tab** — virtualized when > 100 rows (reuse `@tanstack/react-virtual` introduced by Inbox/Todos).
- **Member-list** — typically small (< 50 per project); no virtualization needed; if a tenant has projects with > 200 members (rare; flagged), the list paginates the same way.
- **No re-render of the shell** when navigating Projects ↔ other destinations; master §3.7 invariant.
- **SSE keepalive**: `:keepalive` comment every 25s. Client tolerates 60s silence before reconnect.

### 11.2 Large-member-list considerations

Most projects have < 20 members. Outliers (org-wide projects) may approach 200+ members. The Member tab paginates at 50/page with cursor sort by `added_at:desc`. Search within members (Wave 7+) is not yet shipped — Phase 6 ships unfiltered pagination only.

**Caps:** soft 500 members/project (UI warns at 200, hard 500 enforced server-side). Beyond 500 indicates the user wants a workspace, not a project — surface a help message linking to Team destination.

### 11.3 Archived projects — read-only behavior

When `status='archived'`:

- All write endpoints on the project (PATCH/DELETE, members, transfer) return `409 Conflict` with `{ error: "project archived", code: "project_archived" }` — caller must activate first.
- Cross-destination resources filed under the archived project remain readable but **default-hidden** in their parent destination's list (e.g., todos in an archived project don't appear in the default `/todos` list unless `filter[include_archived_projects]=true`).
- Routines filed under archived projects: in-flight Routine fires complete (do not hard-pause); new fires are **paused at fire time** with `pause_reason="project_archived"` (per §12 Q4 product decision; this PRD's recommendation — allow in-flight runs to complete, pause future fires).
- Chats in archived projects remain interactive (a user can keep talking; the project being archived doesn't kick people out of the conversation).
- Inbox items filed under archived projects continue to arrive (a deferred bug-tracking comment can still land in an archived project's Inbox); admin operators can audit them.

Activation reverts all of the above. No mass un-archive of children.

### 11.4 Cascade rules (per cross-audit §5.3 default — dead link, audit never cascades)

| Origin deletion                         | Projects effect                                                                                                                                                                                                                                                                                                                |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Project soft-deleted                    | `deleted_at` set. Memberships + stars remain queryable for retention window (30d). Activity rows preserved. All child resources' `project_id` becomes a **dead link** (per cross-audit §5.3): the resource shows a `<deleted project>` chip via `<ItemLink>` resolver returning `{ route: null }`. Resources are NOT cascaded. |
| Project hard-deleted (post-30d)         | Memberships + stars + activity + counts hard-deleted (FK cascade). Audit rows retain `target_id`; never cascade.                                                                                                                                                                                                               |
| Owner user disabled (`disabled_at` set) | Project remains; admin Inbox item written; project effectively becomes read-only (no owner = no writes); §3.5.4 handles.                                                                                                                                                                                                       |
| Owner user hard-deleted                 | Membership row vanishes via FK cascade; ownership becomes unassigned. Daily cron writes Inbox to admins immediately. Admin force-transfer Wave 7+.                                                                                                                                                                             |
| Member user disabled                    | Membership remains (allows audit + reactivation). Read access is blocked at the user-disabled gate (separate from project-membership). UI shows "User disabled" chip on member rows.                                                                                                                                           |
| Member user hard-deleted                | Membership cascade-deletes. Audit retains `user_id` in `context.user_id` on the membership-add row.                                                                                                                                                                                                                            |
| Tenant deleted                          | Hard cascade (master rule).                                                                                                                                                                                                                                                                                                    |

Memory items / Routines / Todos / Inbox items / Library items / Chats filed under a deleted project: their `project_id` becomes a dead link. The resources themselves persist; only the project FK is dead. UI renders `<deleted project>` chip with retained `project_name_snapshot` if the resource captured one at insert (todos-prd §13.3, routines-prd §13.1).

---

## §12 Open product questions (orchestrator to resolve before P6-A / P6-B dispatch)

These need a call before Phase 6 implementation. Each carries a sub-PRD recommendation marked **REC**; orchestrator may approve or deviate (deviations are recorded in implementation-plan §3 Phase 6 row).

### Q1. Admin-level project ownership transfer / forced reassignment on deactivation

When an owner is offboarded (IdP `disabled_at` set), §3.5.4 writes Inbox items to admins. **How does the admin transfer ownership?**

- **REC (sub-PRD):** Phase 6 ships no admin force-transfer endpoint. The Inbox item is the trigger; admin manually contacts a successor in the org and the new owner (after being added as a member by the disabled owner's project — wait, that's blocked because the disabled owner can't act). **Refined REC:** Phase 6 ships a **single admin-only endpoint** `POST /v1/admin/projects/{id}/force-transfer { new_owner_user_id }` that bypasses the "current-owner-must-call" rule. Audited with `project.admin_force_transferred`; `context = { admin_user_id, reason }`. Auto-add new owner as member if not already.
- **Alternative:** defer entirely to Wave 7+. Until then, disabled-owner projects sit in read-only state.

**Recommendation:** ship the admin endpoint in Phase 6 (it's a 30-LOC route + audit row; the Inbox item is already there); blocking on Wave 7+ leaves a known-broken state in production.

### Q2. Default project for new chats — system-inferred or user-picked?

Per §9.3, the rule is:

- In a project detail view → default to that project.
- Direct `/chats` → null (Unfiled).
- Composer mention picker overrides explicit.

**Q:** Does the system **infer from context** (e.g., the last 3 chats the user opened were in project X, so default to X)?

- **REC:** No inference in Phase 6. Inference is opaque to the user and an audit-headache (which chat-creation got "auto-filed" by inference?). Explicit only. Wave 7+ can revisit with a feature-flag.

### Q3. Project-level connector scope override

Master PRD §5.4 open question + cross-audit §3.5 deferred inventory: **per-project connector restrictions**.

Routines already support per-routine connector scope override (routines-prd §3.8). Should Projects own a **default** for resources filed under it?

- **REC:** No in Phase 6. Connector scope stays attached to the **resource owner**, not the filing project. A routine's connector scope is the routine's own (per routines-prd §3.10). Project membership is a read scope and filing axis, not a permission lift. (This matches §1.3 anti-goal "Permission inheritance tree".)
- **Alternative:** project sets a `default_connector_allowlist`; new routines / chats filed under the project inherit at create-time (not at fire-time — too much coupling). Wave 8+.

**Recommendation:** defer. The added complexity (cascade on inherit, drift between project setting and resource setting, "did the project change?") is not warranted until we observe demand.

### Q4. Project archival — in-flight runs?

When a project is archived, what happens to:

- Running Routine fires? **REC:** allow to complete; future fires pause with `pause_reason="project_archived"` (§11.3).
- Live chat conversations? **REC:** unaffected. Users can keep talking. The chat's `project_id` remains; the project chip on the chat shows "Archived" badge.
- Pending Approvals? **REC:** unaffected. Approval resolution is a function of the run + user, not the project.
- Pending Inbox items? **REC:** unaffected. Items continue to arrive (read-only on the project, but in-flight items still resolve).

**Recommendation:** archive = "hide from default views + pause future-scheduled work"; not "halt everything". Confirm.

### Q5. Old-owner role after transfer

After `POST /v1/projects/{id}/transfer`, what role does the **previous owner** take?

- **REC:** `editor` by default. The old owner is the most informed contributor and likely needs continued write access (their own todos, their own routines).
- **Alternative:** parameterize via `previous_owner_new_role` (default `editor`, can be `viewer` or even `none` = remove). The wire shape supports this; UI defaults to `editor` and lets the transferor change before confirming.

**Recommendation:** parameterize with `editor` default; lets the transferor handle outliers ("I'm leaving the company; remove me entirely").

### Q6. Project templates / forking

Cross-audit §3.5 lists "Project templates" as out of scope.

- **REC:** confirmed out of scope for Phase 6. Wave 7+.

### Q7. Nested projects (sub-projects)

- **REC:** out of scope; flat-only per §1.3 anti-goal. Never.

### Q8. External-collaborator (guest) project members

Master §10 Q3 (cross-destination guest consistency) is open.

- **REC:** out of scope for Phase 6. Project members must be tenant members. When guest support lands (master §10), Projects matches whatever shape lands at the tenant level. Wire shape (`ProjectMembership.role`) does not need extension — `guest` would be a new `ProjectRole` value when the time comes.

### Q9. Bulk-add members

- **REC:** Phase 6 single-add only. Wave 6+ ships bulk-add (paste-list or CSV-style); orchestrator may decide.

---

## §13 Test plan

### 13.1 Backend / facade unit + integration (P6-A)

**Tenant isolation**

- Cross-tenant GET/PATCH/DELETE/MEMBERSHIP → 404. Single test matrix asserting four-way tenant + user combinations.
- Missing tenant claim → 401.

**Project-scoped ACL (the master rule)**

- 4-case ACL matrix (§7.4) tested AND **exported as a reusable fixture** consumed by:
  - `services/backend/tests/integration/inbox/test_acl_matrix.py`
  - `services/backend/tests/integration/todos/test_acl_matrix.py`
  - `services/backend/tests/integration/routines/test_acl_matrix.py`
  - future Library / Memory test suites.

**Mutation ACL**

- Owner writes succeed; editor writes 403; viewer writes 403; non-member writes 404.
- Cannot remove project owner (must transfer first) → 409 with `{ code: "owner_cannot_be_removed" }`.
- Cannot add member already added → 409 with `{ code: "membership_exists" }`.

**Transfer**

- Owner → existing-member: success; audit row; SSE envelope to all members.
- Owner → non-member: 422 with `{ code: "new_owner_not_member" }`.
- Non-owner → x: 403 (404 if non-member of project).
- After transfer: old owner has role `editor` (default) and the new owner row has role `owner`.
- Cascade: `project_memberships` PARTIAL-UNIQUE-on-owner is honored (atomic owner swap; no transient two-owner state).

**Archival**

- Archive → status='archived', archived_at set; SSE; audit.
- Mutations after archive → 409.
- Children: new routine-fire scheduled → paused on next tick with `pause_reason="project_archived"` (integration with P5-A scheduler).
- Activate → status='active', archived_at cleared; SSE; audit.

**Owner-offboarded cascade**

- Disable owner; run daily cron; expect Inbox row to admins; idempotent (running cron twice does NOT create a duplicate Inbox row).
- Admin force-transfer (if §12 Q1 ships): owner-transfer path bypasses the "must-be-current-owner" check; audited as `project.admin_force_transferred`.

**Activity projector**

- Create a Todo with `project_id` → expect `project_activity` row within 5s (test uses synchronous projector trigger, not actual sleep).
- Idempotent: replay same audit event → no duplicate `project_activity` row (UNIQUE on `audit_id`).
- Backfill: insert 1000 historical audit rows in past 90 days; run backfill once; expect 1000 `project_activity` rows; replay backfill; expect 0 new rows.
- Reconciliation: corrupt `project_activity_counts.todos_open`; run nightly reconciliation; expect repair + audit summary.
- Activity row preview is denormalized: rename the actor; existing rows keep old `actor_display_name` (recent-24h cron refresh handles fresh rows).

**Search + filter**

- `q=acme` matches `name` and `description`; not `icon_emoji`.
- `filter[status]=active&filter[status]=archived` OR's per cross-audit §1.5.
- `filter[member_user_id]=other_user` by non-admin → 403.
- `filter[member_user_id]=me` by member → returns own projects.
- `sort=last_activity_at:desc` orders correctly under inserts; cursor stable.
- Multi-value filter axis: `?filter[member_user_id]=u1&filter[member_user_id]=u2` returns union (OR).

**SSE**

- Connect → receive `:keepalive` within 30s.
- Drop + reconnect with `?after_sequence=N` → replays envelopes > N.
- Per-recipient ACL: envelope for project P with member set {u1,u2} is NOT delivered to u3.
- Newly-added-member receives `project_member_added` envelope on the same connection (auto-add to rail/panel).
- Removed-member receives `project_member_removed` then their stream filter no longer includes that project.

**Retention**

- Daily cron: hard-delete soft-deleted projects past 30d; hard-delete `project_activity` past 365d; cascade-delete `project_memberships` / `project_stars` / `project_activity_counts`.
- Audit-row append-only verified.

**Cross-destination project filter (server side)**

- `GET /v1/todos?filter[project_id]=<id>` returns only owner + project-member-readable todos under that project.
- `GET /v1/inbox?filter[project_id]=<id>` returns only the caller's own inbox items under that project (Inbox is recipient-scoped; cross-audit §1.3 does not override recipient-only for Inbox).
- `GET /v1/routines?filter[project_id]=<id>` matches routines-prd §4.5.

**Performance**

- List `/v1/projects?limit=50` p95 latency < 100ms (warm cache, 1000-project tenant).
- Detail `GET /v1/projects/{id}` p95 < 100ms.
- Activity tab list p95 < 150ms.

### 13.2 Frontend unit + integration (P6-B)

- Editor validation: empty name → save disabled; duplicate name (within tenant) → server returns 409, UI highlights name field.
- Color hue picker: rendered chips for 12 swatches mapping to design-system hues; selecting writes `color_hue`.
- Icon emoji picker: limited emoji set + custom-input; client validates single-glyph; server re-validates.
- Members tab: add member → optimistic; rollback on 422; role-change → optimistic with rollback.
- Transfer dialog: confirmation required; submit calls endpoint; success closes modal; SSE updates everyone's UI.
- Tab keyboard navigation: Left/Right cycle (wrap); Home/End jump; Enter activates; tab badges in `aria-describedby`.
- `<ItemLink kind="project">` resolves to detail route; deleted project → `{ route: null }` per cross-audit §5.3, renders `<deleted project>` chip.
- SSE reconnect: 3 events while disconnected → all applied after reconnect; rail updates for member-add envelopes for the active user.
- Archived projects rendered with status pill + reduced opacity in default list; activation reverts.
- Star toggle: optimistic; rollback on 5xx.
- Filter combinations: pairwise `filter[status]` × `filter[starred]` × `q`.
- axe-core green on `ProjectsDestination + ProjectsPanel + ProjectDetail + ProjectEditor + ProjectActivityTab + ProjectMembersTab` in default + high-contrast themes.
- `<ProjectFilterChip>` from §9.2 mounts in Todos/Inbox/Routines panels (each consuming destination's test verifies wiring).
- Project default rules (§9.3) tested in `createContextDefaults.test.ts`.

### 13.3 Cross-destination integration

- File a todo under project P; archive P; verify todo appears in P's detail Todos tab; verify todo HIDDEN by default in `/todos` (unless `include_archived_projects`); reactivate P; todo reappears.
- File a routine under project P; archive P; trigger scheduler tick; routine pause-on-fire with `pause_reason="project_archived"`; verify audit; activate P; next tick fires normally.
- Delete project P (soft); todo's `<ItemLink kind="project">` → dead-link chip. Hard-delete after 30d; same.
- Transfer project P to user U2; cross-destination resources retain original owner attribution (not retroactively rewritten).

### 13.4 End-to-end smoke (added to `docs/dev-testing.md`)

```bash
export TOKEN=$(make dev-bearer)
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     http://127.0.0.1:8200/v1/projects -d '{"name":"Acme","icon_emoji":"🚀","color_hue":210}'
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8200/v1/projects     # list
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     http://127.0.0.1:8200/v1/projects/<id>/members -d '{"user_id":"<u2>","role":"editor"}'
curl -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8200/v1/projects/<id>/archive
curl -N -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8200/v1/projects/stream
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8200/v1/projects/<id>/activity
```

---

## §14 Accessibility (per master §3.6)

- **Editor tabs** — ARIA tabs pattern. Arrow keys cycle (left/right wrap), Home/End jump, Enter activates. Validation badges in `aria-describedby`.
- **Member list** — each row is one tab stop. Role chip dropdown is keyboard-accessible. ⋯ menu (Remove) is keyboard-accessible and confirms destructive action via dialog.
- **Star toggle** — `aria-pressed` reflects state; tooltip label "Star this project" / "Unstar".
- **Color is never the sole carrier** — status combines pill color + icon + text. Archived projects also get a 📦 icon and the word "archived".
- **Live region** — when on the Projects destination, polite `aria-live` announces:
  - "Project archived" / "Project activated" / "Member added: {display_name}" / "Ownership transferred to {display_name}" on SSE envelopes (throttled to one announcement per 3s).
- **Transfer modal** — focus trap; Esc closes; warning text reads as an `aria-live="polite"` region after submit ("Ownership transferred to {name}").
- **Icon-emoji picker** — keyboard-navigable grid (Arrow keys / Enter); searchable by label.
- **Reduced motion** — star-toggle pulse animation respects `prefers-reduced-motion`.

---

## §15 Telemetry (per master §3.8)

OpenTelemetry spans (no PII; only ids + enum values):

```
destination=projects
  action=list_open
  action=detail_open                  project_id=<id>
  action=editor_open                  mode=<new|edit>
  action=editor_save                  result=<ok|validation_error>
  action=filter_change                value=<slug>
  action=search                       q_len=<n>
  action=star_toggle                  project_id=<id>  starred=<bool>
  action=archive                      project_id=<id>
  action=activate                     project_id=<id>
  action=member_added                 project_id=<id>  role=<role>
  action=member_removed               project_id=<id>
  action=member_role_changed          project_id=<id>  from_role=<role>  to_role=<role>
  action=ownership_transferred        project_id=<id>
  action=sse_reconnect                after_sequence=<n>
  action=tab_change                   project_id=<id>  tab=<chats|todos|inbox|library|routines|members|activity>
```

Backend emits structured logs with `request_id` correlation (cross-audit §5.1). Error logs include `tenant_id`, route, error code (never user data).

Projector emits per-tick metrics: `project_activity_projector_ticks_total`, `project_activity_rows_projected_total{action=...}`, `project_activity_projector_lag_seconds`.

---

## §16 Token usage tracking (per cross-audit §5.5)

**Projects does NOT emit LLM calls in Phase 6.** Activity projection is rule-based (no LLM), search is PostgreSQL FTS (no LLM), members management is CRUD. The `Purpose` enum does not need a new value for Projects in Phase 6.

If a future "AI suggested members" or "AI summarize project activity" feature lands, it will:

- Route through the canonical `build_chat_model` in `services/ai-backend/.../deep_agent_builder.py` (cross-audit §5.5 single integration point invariant; locked by Phase 0.6 CI guard).
- Add a new `Purpose.PROJECT_SUMMARIZATION` (or similar) value to `services/ai-backend/src/agent_runtime/observability/attribution.py`.
- Be a separate sub-PRD; not in scope for Phase 6.

---

## §17 States (per master §3.10)

| State                       | Renders                                                                                                                                                                                                                            |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **List loading**            | Skeleton: PageHeader visible, FilterTabs visible (no counts), 6 skeleton cards in grid.                                                                                                                                            |
| **List empty (any filter)** | `<EmptyState icon="folder" title="No projects yet" sub="Projects group related chats, todos, library items, and routines under a shared ACL." action={{ label: "New project", onClick: createNew }} />`                            |
| **List empty (filter)**     | filter-specific copy: archived → "No archived projects"; starred → "You haven't starred any projects yet."; mine → "You're not a member of any projects yet."                                                                      |
| **Filter-empty + search**   | "No projects match \"{q}\" in {filter}." with "Clear filters" button.                                                                                                                                                              |
| **Detail loading**          | Skeleton matching detail shape (header / tab list / current-tab placeholder).                                                                                                                                                      |
| **Detail (archived)**       | Detail header shows archived pill + archived_at relative time + `[Activate]` CTA; cross-destination tabs show count and items but mutation affordances on those tabs are disabled with tooltip "Activate project to make changes". |
| **Editor saving**           | Save button shows spinner; form disabled; on 200 → toast "Project saved" + nav back to detail; on error → toast + error highlighted on offending field.                                                                            |
| **Editor name conflict**    | Banner: "A project named '{name}' already exists in this workspace. Choose a different name."                                                                                                                                      |
| **Member-add failure**      | Inline error under input: "User not found" (404) / "User already a member" (409) / "Cross-tenant user cannot be added" (422). User remains in the dialog; can retry.                                                               |
| **Transfer success**        | Modal closes; toast "Ownership transferred to {display_name}"; detail re-renders with new owner chip.                                                                                                                              |
| **Transfer failure**        | Modal stays open with error: "{server reason}. Try again."                                                                                                                                                                         |
| **Activity tab loading**    | Skeleton: 6 activity rows.                                                                                                                                                                                                         |
| **Activity tab empty**      | `<EmptyState icon="activity" title="No activity yet" sub="Activity will appear here as people work in this project." />`                                                                                                           |
| **Offline**                 | Banner: "You're offline — showing cached projects. New activity will resume when you reconnect." Reads from `KeyValueStore` cache.                                                                                                 |
| **Stale**                   | If last-fetch > 5 min AND SSE disconnected: top hint "Project list may be out of date. Refresh." with refresh button.                                                                                                              |
| **Compliance read banner**  | When a tenant-admin (non-member) views a project, a top banner reads: "Reading as compliance admin — this read is audited." Always visible during the admin's session on the non-member project. Master §3.2 + cross-audit §1.3.   |

---

## §18 Desktop substrate caveats (per master §3.12 + cross-audit §1.2)

- **Projects edit / member-add on desktop is identical to web.** No desktop-specific surface.
- **Star toggle** uses no native API.
- **Project chip badge in OS menu bar** — desktop's per-destination `BadgePort` (cross-audit §1.2) is **not** used for Projects in Phase 6 (the destination doesn't have a meaningful single-int count — "how many projects do you own?" is rarely useful as a badge). If product later wants "projects-with-unread-activity count", it lands as a Wave 7+ addition.
- **Native notification on member-added / ownership-transferred** — fires through `NotificationPort` (cross-audit §1.2). Title `Added to project: <name>` / `Ownership transferred to you: <name>`; body excluded for privacy. Web default: no-op when permission ungranted; desktop: native OS notification with click → `router.navigate(<ProjectId>)`.
- **Deep-link routing** — desktop registers `atlas://projects/<id>` and `atlas://projects/<id>/edit` as URL handlers. Frontend `HashRouter` and desktop main process resolve to the same `route.id` / `route.view` shape.
- **No direct browser API access from any projects component** — substrate-agnostic. Clipboard, notifications, deep-link routing all go through ports.

---

## §19 Implementation phasing (per implementation-plan §2 Phase 6 + §4 merge order)

Per master §7, this destination uses a 2-agent pattern.

### 19.1 Agent boundaries (no overlap with shared files)

**P6-A backend — `worktree-agent-phase6-projects-backend`**

Prereqs: SP-1 (`brands.ts` for `ProjectId`, `refs.ts` for `ItemRef` + `ItemKind="project"`), P5-A (rebase on `api-types/index.ts` + `backend/app.py` + `facade/app.py` shared lines).

Exclusive files:

- `packages/api-types/src/projects.ts` (NEW); append one re-export line to `packages/api-types/src/index.ts` (rebase after P5-A).
- `services/backend/src/backend_app/projects/` (NEW): `routes.py`, `service.py`, `store.py`, `schema.py`, `acl.py`, `events.py` (SSE bus), `activity_projector.py`, `internal_routes.py`.
- Alembic migration for `projects`, `project_memberships`, `project_stars`, `project_activity`, `project_activity_counts` + indexes.
- `services/backend/src/backend_app/jobs/projects_retention.py` (NEW).
- `services/backend/src/backend_app/app.py` — append `include_router(projects_router)` + internal-router lines (merge after P5-A).
- `services/backend-facade/src/backend_facade/projects_routes.py` (NEW); append to `facade/app.py`.
- `services/backend/tests/integration/projects/test_acl_matrix.py` (the reusable cross-destination fixture).
- All tests per §13.1.

Deliverables: project CRUD + member management + activity projector + transfer endpoint (with §12 Q1 admin-force-transfer if approved) + retention cron + audit hooks + SSE + cross-audit §1.3 canonical ACL implementation.

**P6-B chat-surface + frontend — `worktree-agent-phase6-projects-surface`**

Prereqs: SP-1 (`<PageHeader>`, `<StatusPill>`, `<FilterTabs>`, `<EmptyState>`, `<CardGrid>`, `<ItemLink>`, `<ActivityList>`, `BadgePort`, `NotificationPort`, `ClipboardPort`, `formatRelativeTime`), P6-A (wire contracts).

Exclusive files:

- `packages/chat-surface/src/shell/destinations.ts` — extend `ShellDestinationSlug` to add `"projects"` as the 13th slug + extend `SHELL_DESTINATIONS` array (only Phase-6 touch to this file).
- `packages/chat-surface/src/destinations/projects/` (NEW): `ProjectsDestination.tsx`, `ProjectsPanel.tsx`, `ProjectDetail.tsx`, `ProjectEditor.tsx`, `tabs/{ChatsTab,TodosTab,InboxTab,LibraryTab,RoutinesTab,MembersTab,ActivityTab}.tsx`, `members/{MemberRow,RolePicker,InviteMemberDialog,TransferOwnerDialog}.tsx`, `activity/projectActivityProjector.ts` (frontend; merges SSE envelopes into local store), `widgets/{ProjectFilterChip.tsx, useProjectsForFilter.ts}`, `index.ts` (registers `<ItemLink kind="project">` resolver).
- `packages/chat-surface/src/index.ts` — append Projects re-export.
- `apps/frontend/src/api/projects.ts` (NEW) — HTTP wrappers + SSE.
- `apps/frontend/src/app/App.tsx` — extend destination dispatch switch + ContextPanel slot (merge after P5-B).
- `apps/frontend/src/app/createContextDefaults.ts` (NEW) — the system-wide "default project for new resource" helper used by composer + every inline-add.
- All tests per §13.2.

Deliverables: Projects UI; cross-destination tabs; member management; transfer modal; activity stream; `<ProjectFilterChip>` for consumer destinations; `createContextDefaults` helper.

### 19.2 Merge order (strict, per implementation-plan §4)

1. SP-1 → main _(prereq merged)_
2. P1-A (approvals) → main _(prereq merged)_
3. P1-B → main _(prereq merged)_
4. P1-C → main _(prereq merged)_
5. P2-A → main _(prereq merged)_
6. P2-B → main _(prereq merged)_
7. P3-A → main _(prereq merged)_
8. P3-B → main _(prereq merged)_
9. P4-A → main _(prereq merged)_
10. P4-B → main _(prereq merged)_
11. P5-A → main _(prereq merged)_
12. P5-B → main _(prereq merged)_
13. **P6-A** → main. Rebases `api-types/index.ts`, `backend/app.py`, `facade/app.py` on top of P5-A.
14. **P6-B** → main. Rebases `App.tsx` on top of P5-B and `destinations.ts` (adding 13th slug after Routines' 12th slug addition).

### 19.3 Acceptance criteria (gate to closing Phase 6)

- ✅ Every endpoint in §4.2, §4.3 implemented and tested.
- ✅ Audit rows emitted for every action in §6.1; verified via audit-chain export.
- ✅ Tenant + ACL isolation tests pass; the §7.4 4-case matrix shipped as a reusable fixture and adopted by Inbox + Todos + Routines test suites.
- ✅ Activity projector test: insert 1000 audit rows with `context.project_id`; projector creates 1000 `project_activity` rows; idempotent on replay; counts denormalized correctly.
- ✅ Owner-offboarded cascade test: disable owner; cron creates Inbox to admins; idempotent.
- ✅ Transfer test: existing-member → success + audit + SSE; non-member → 422; non-owner-caller → 404.
- ✅ Archive cascade test: archive a project; child routines pause on next fire; activate; routines fire normally.
- ✅ Soft-delete + 30d hard-delete + cascade tested.
- ✅ axe-core green on all 7 surfaces (`ProjectsDestination + ProjectsPanel + ProjectDetail + ProjectEditor + ChatsTab + TodosTab + InboxTab + RoutinesTab + MembersTab + ActivityTab`) in default + high-contrast themes.
- ✅ SSE reconnect resumes from `?after_sequence=N` without dropping envelopes; per-recipient ACL holds.
- ✅ `<ItemLink kind="project">` resolver registered at package-load; deleted-project → `{ route: null }` per cross-audit §5.3.
- ✅ `<ProjectFilterChip>` mounted in Todos/Inbox/Routines panels with passing tests.
- ✅ `createContextDefaults` helper used by composer Phase 1.6 picker.
- ✅ Frontend typecheck + chat-surface tests + backend tests green; no `any` introduced in `projects.ts`.

---

## §20 Anti-goals (restated as testable invariants)

- ❌ **NOT a channel / conversation surface.** Projects has no message timeline of its own. The Activity tab is a read-only projection.
- ❌ **NOT hierarchical.** No sub-projects, no nested folders. Flat per tenant.
- ❌ **NOT a permission lift.** Membership grants read scope and filing axis only. Connector / tool / skill scope stays attached to the resource owner (§12 Q3 rec).
- ❌ **NOT a template engine.** No project templates or forking (§12 Q6).
- ❌ **NOT a cross-tenant primitive.** Projects are tenant-scoped; cross-audit §3.5 "Cross-tenant sharing → never".
- ❌ **NOT an inference engine.** No system-inferred default project for new resources (§12 Q2 rec: explicit only).
- ❌ **NOT a guest hub** in Phase 6 (§12 Q8).
- ❌ **NOT an admin-mutation surface** beyond force-transfer (§12 Q1). Admins read; only the project owner writes.
- ❌ **NO frontend-only ACL.** Every §7 check is server-validated.
- ❌ **NO PII in telemetry or logs.** Project names, member emails, activity previews never logged (§15).
- ❌ **NO direct browser API access** — clipboard, notifications, deep-links go through ports (§18).
- ❌ **NO double-source-of-truth on the ACL predicate.** `services/backend/src/backend_app/projects/acl.py::is_member` is the only implementation; consuming destinations import or call via internal route (§7.3).

---

## §21 References

- [PRD.md](../PRD.md) — workspace shell + composer + thread canvas (the foundation).
- [destinations-master-prd.md](../destinations-master-prd.md) — §3 (enterprise checklist), §4 (shared primitives), §5.4 (Projects brief), §7 (dispatch pattern). Projects is the 13th destination, counting Routines as the 12th.
- [cross-audit.md](../cross-audit.md) — binding decisions consumed: §1.1 `ItemRef` incl. `kind="project"`, §1.2 ports, **§1.3 project-scoped ACL master rule (Projects owns the canonical implementation)**, §1.4 audit `context`, §1.5 filter OR, §1.6 PageHeader, §2.1 branded `ProjectId`, §2.3 SectionResult (n/a — Projects' list is single-fetch, not aggregated), §3.3 ItemLink registry, §3.4 formatRelativeTime, §3.5 deferred-features, §4 shared-primitives prereq, §5.1 request_id, §5.2 SSE, §5.3 cascade default (dead-link), §5.4 port injection.
- [implementation-plan.md](../implementation-plan.md) — §2 Phase 6 row (P6-A / P6-B file boundaries), §4 strict merge order, §6 anti-conflict file rules.
- [destinations/inbox-prd.md](inbox-prd.md) — consumer of project-scoped ACL on `InboxItem.project_id`; cross-audit §1.3 amendment applies.
- [destinations/todos-prd.md](todos-prd.md) — consumer; §7.2 of that PRD aligns to cross-audit §1.3 (the resolver Projects ships in `acl.py`).
- [destinations/routines-prd.md](routines-prd.md) — consumer; §7 + §13.1 cascade for project-archived routines.
- [destinations/chats-canvas-prd.md](chats-canvas-prd.md) — consumer; `Conversation.project_id` carrier; composer surface gap is Phase 1.6 cross-reference (§10 above).
- [destinations/home-prd.md](home-prd.md) — consumer; Home renders starred-projects panel.
- `services/backend/src/backend_app/jobs/inbox_retention.py` — template for `projects_retention.py` (§5.4) and `activity_projector.py` (§3.7).
- `packages/audit-chain` — audit row writer (existing; cross-audit §1.4 `context` field carries `project_id` from every consuming destination).
- Root [`CLAUDE.md`](../../../CLAUDE.md) — compliance section (audit immutability, retention scope, tenant isolation, untrusted-input rules).
- [`services/ai-backend/CLAUDE.md`](../../../services/ai-backend/CLAUDE.md) · [`services/backend/CLAUDE.md`](../../../services/backend/CLAUDE.md) · [`services/backend-facade/CLAUDE.md`](../../../services/backend-facade/CLAUDE.md) · [`packages/api-types/CLAUDE.md`](../../../packages/api-types/CLAUDE.md).
