# PRD-H — Backend data-plane gaps

**Status:** Draft · **Surface:** cross-service · **Packages:** `services/backend`,
`services/ai-backend`, `services/backend-facade`, `packages/api-types`,
`apps/frontend` · **Blocked by:** — (parallel track) · **Feeds:** C (badge), F
(validate — already server-side), G/Chats (projection)

## 1. Context & problem

Most surfaces are wired to the facade with real data, but four concrete data-plane
gaps leave design elements dead or degraded:

- **P1 — Projects `/v1/projects/stream` does not exist.** The web client opens it
  for live updates (`apps/frontend/src/api/projectsApi.ts:313`;
  `ProjectsRoute.tsx:349`) but there is **no `/stream` route** in the facade
  (`projects_routes.py`) or backend (`projects/routes.py`). The connection errors
  and retries forever with exponential backoff (`ProjectsRoute.tsx:402-412`), so
  add-to-rail on `project_member_added` and cross-session archive/star/delete
  merges are dead, and the app runs a permanent failing reconnect loop.
- **P2 — Projects store is in-memory.** Default `InMemoryProjectsStore()`
  (`store.py:331`; `app.py:1786`; `desktop_app.py:165` "no postgres adapter yet")
  — projects don't survive a backend restart. Also `_projects-stub.ts` (frontend +
  chat-surface) still shadows the merged `packages/api-types/src/projects.ts`
  (the `TODO(merge)` rewire never ran) → two wire-type copies can drift.
- **P3 — Chats list fields aren't projected.** `pinned`/`preview`/`model` read from
  freeform `conversation.metadata.*` that **no backend path populates**
  (`chatsApi.ts:149-172`, flagged "PRD §11 gap"). Result: the **Pinned section is
  always empty**, and preview/model never render. There is also no pin action.
- **P4 — Rail badge/identity have no source.** The design's Run badge (active-run
  count) and avatar initial (user identity) have no data feed; the rail can't show
  them (PRD-C renders whatever it's given).

`validate` (provider keys) already exists end-to-end server-side; PRD-F only wires
the client port — **not** an H item.

## 2. Goals / Non-goals

**Goals**

- G1 — Ship `GET /v1/projects/stream` (SSE) facade + backend, emitting the events
  the client already handles; the client stops error-looping and receives live
  updates. (Alternative if descoped: make the client not open `/stream` — but the
  design intends live updates, so build it.)
- G2 — Durable projects store (Postgres adapter) selectable like the runtime store;
  complete the `_projects-stub → @0x-copilot/api-types` rewire.
- G3 — Project `pinned` (first-class), `preview` (last-turn snippet), and `model`
  (latest run model) onto the **conversation list** contract, plus a pin/unpin
  action; the frontend reads projected fields, not `metadata`.
- G4 — An identity + active-run-count source the rail can consume (a lightweight
  `/v1/me`-derived initial the host already has, and an active-run count derived
  from the conversations/runs the client already fetches — formalise it, don't add
  a bespoke endpoint if existing data suffices).

**Non-goals**

- NG1 — Building `validate` (already exists).
- NG2 — Full realtime for every destination — scope to Projects `/stream` (which
  the client already expects) and the Chats list fields.

## 3. User stories

| ID     | As a…     | I want…                                           | so that…                                                |
| ------ | --------- | ------------------------------------------------- | ------------------------------------------------------- |
| US-H.1 | Solo user | projects/chats to update live and survive restart | the app is trustworthy, not stale or amnesiac           |
| US-H.2 | Solo user | to pin a chat and see its preview + model         | the Chats list is useful, and Pinned isn't always empty |
| US-H.3 | Solo user | the rail to show my initial + active-run count    | the rail reflects real session state                    |
| US-H.4 | Developer | one project wire-type (`api-types`)               | frontend and stub can't drift                           |

**Acceptance (US-H.2):** _Given_ a conversation, _when_ I pin it, _then_ a
persisted `pinned=true` moves it to the Pinned section across reloads and sessions;
_when_ it has run history, _then_ its list row shows a preview snippet and the run
model (from projected fields, not `metadata`).

## 4. Functional requirements

- **FR-H.1** — Implement `GET /v1/projects/stream` (SSE): backend
  `projects/routes.py` streams `project_updated`/`project_member_added`/
  `project_archived`/`project_deleted`/`project_starred` events for the caller's
  tenant; facade `projects_routes.py` proxies it (SSE passthrough, like the run
  stream). Events match the shapes `ProjectsRoute` already handles. Auth + tenant
  scoping identical to the REST routes.
- **FR-H.2** — Add a `PostgresProjectsStore` implementing the projects store port
  (schema from `projects/schema.sql`), selected by the same env switch pattern as
  the runtime store; `desktop_app.py` uses it (durable). In-memory remains for
  tests/dev.
- **FR-H.3** — Complete the contract rewire: frontend + chat-surface import project
  wire-types from `@0x-copilot/api-types` (`projects.ts`); delete both
  `_projects-stub.ts` copies.
- **FR-H.4** — Extend the conversation-list contract
  (`ai-backend .../schemas/conversations.py`, `api-types` conversations) with
  `pinned: bool`, `preview: string | null`, `model: string | null`, projected by
  the ai-backend store: `pinned` from a first-class column, `preview` from the last
  message snippet, `model` from `latest_run` model. Add `POST /v1/agent/
conversations/{id}/pin` (+ unpin) through facade → ai-backend. Frontend `chatsApi`
  reads projected fields (drop the `metadata.*` reads); Chats row gets a pin action.
- **FR-H.5** — Formalise the rail feed: expose the user initial from the identity
  the host already holds (`/v1/me/profile` display name → initial), and an
  `activeRunCount` derived from the conversations list (`latest_run_status ∈
{running,queued,...}`), surfaced to `ChatShell`→`AppRail` (PRD-C props). No new
  bespoke endpoint unless the derived count proves insufficient.

## 5. Architecture & system design

- **SSOT.** Project wire-types: `@0x-copilot/api-types` (delete stubs). Conversation
  list fields: the ai-backend conversation schema (projected once), not frontend
  `metadata` guesses. Streaming: reuse the run-stream SSE pattern (persisted
  events, tenant-scoped) rather than a new transport. Identity/count: derived from
  existing `/v1/me` + conversations, surfaced as rail props (PRD-C).
- **Boundaries.** Apps call the facade only; facade proxies `backend`/`ai-backend`;
  no cross-service imports. `pinned` is product state → lives with conversations in
  `ai-backend` (product persistence), consistent with the run spine; projects are
  `backend` product persistence.
- **Data flow.** Projects: `ProjectsRoute` SSE → facade → backend store event bus.
  Chats: `chatsApi` → `/v1/agent/conversations` (now with projected fields) +
  `/pin`. Rail: host binder derives `{initial, activeRunCount}` from data it
  already fetches → `AppRail` props.
- **Reuse vs new.** Reuse run-stream SSE machinery, `TokenVault`-style tenant
  scoping, `api-types`. New: `/projects/stream`, `PostgresProjectsStore`,
  conversation `pinned/preview/model` + `/pin`. Delete: `_projects-stub.ts` (×2).

## 6. Affected files

- **Modify:** `services/backend/src/backend_app/projects/{routes.py,store.py,
service.py,schema.sql}` (stream + PG store); `services/backend-facade/src/
backend_facade/projects_routes.py` (stream proxy);
  `services/ai-backend/src/.../schemas/conversations.py` + the file/pg conversation
  stores (pinned/preview/model + pin route); facade conversation routes;
  `packages/api-types/src/{projects.ts,conversations.ts}`;
  `apps/frontend/src/api/{projectsApi.ts,chatsApi.ts}`; the two `_projects-stub.ts`.
- **Delete:** `apps/frontend/src/api/_projects-stub.ts`,
  `packages/chat-surface/src/destinations/projects/_projects-stub.ts`.

## 7. PR / commit breakdown

- **PR-H.1** — `_projects-stub → api-types` rewire (pure contract cleanup). S.
- **PR-H.2** — `GET /v1/projects/stream` SSE (backend + facade) + client stops
  error-looping. M.
- **PR-H.3** — `PostgresProjectsStore` + durable selection for desktop. M.
- **PR-H.4** — Conversation `pinned/preview/model` projection + `/pin` route +
  frontend reads projected fields + Chats pin action. M/L (feeds PRD-G Chats).
- **PR-H.5** — Rail identity/active-run-count derivation surfaced to `AppRail`
  (feeds PRD-C). S.

## 8. Testing plan

- **Unit** (pytest, owning `.venv`): projects stream emits each event on the
  corresponding mutation, tenant-scoped (no cross-tenant leakage);
  `PostgresProjectsStore` round-trips CRUD + membership + ACL; conversation
  projection returns `pinned/preview/model`; `/pin` toggles and persists.
- **Unit** (vitest): `chatsApi` reads projected fields (no `metadata` reads);
  Pinned section populated when `pinned=true`; `ProjectsRoute` no longer opens a
  failing stream (or consumes events without error looping).
- **Integration:** live-smoke on the desktop supervised stack (per
  `apps/desktop/SMOKE.md`) — pin a chat, restart backend, pin persists; open two
  sessions, archive a project in one, it updates in the other via `/stream`.
- **Regression:** existing projects CRUD + conversation-list tests green; tenant
  isolation tests for the new stream/route.

## 9. UI/UX acceptance checklist

- [ ] Projects: no failing reconnect loop; live archive/star/member events land;
      projects survive restart.
- [ ] Chats: Pinned section populates from persisted `pinned`; rows show
      preview + mono model; pin action works and persists.
- [ ] Rail: avatar shows the user initial; Run badge shows the live active-run
      count (0 → hidden).

## 10. Dependencies & sequencing

Parallel track (start immediately). Consumers: PR-H.5 → PRD-C badge/identity;
PR-H.4 → PRD-G Chats rows; PR-H.2/H.3 → PRD-G Projects live/durable. Land PR-H.1
(contract) first to unblock PRD-G Projects work.

## 11. Risks & mitigations

| Risk                                  | Mitigation                                                                                                  |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| SSE fan-out/tenant leakage            | Reuse the run-stream pattern (persisted, tenant-scoped, sequence-guarded); tenant-isolation tests mandatory |
| Postgres projects adapter scope creep | Mirror the existing runtime PG adapter; schema already exists (`schema.sql`); in-memory stays for tests     |
| Conversation schema change ripples    | Additive fields + additive `/pin`; default `pinned=false`, `preview/model` nullable; old clients unaffected |
| Desktop durable store migration       | Ship behind the store-selection env switch; keep in-memory default until verified on the supervised boot    |

## 12. Definition of done

- [ ] `/projects/stream` live + tenant-safe; client loop gone; durable projects
      store on desktop; stubs deleted (one `api-types`).
- [ ] Conversation `pinned/preview/model` projected + `/pin` persists; Chats reads
      projected fields; Pinned non-empty when pinned.
- [ ] Rail identity/active-run-count surfaced to `AppRail`.
- [ ] pytest + vitest + live-smoke green; tenant-isolation covered; boundaries
      respected (apps→facade only).
