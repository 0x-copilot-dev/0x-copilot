# PRD-09 — Chats surface: pin write path, live refresh, pagination, archive

## Problem

The Chats destination is a read-only photograph of a moment that has already passed.

- **Pinned is a section nobody can fill.** There is no pin control anywhere on the surface. The one pin affordance that exists in the product lives in the legacy `ChatScreen` sidebar, is reachable only if the user opts out of the `runCockpitWeb` flag, and writes to `localStorage` — it can never populate the server column the Chats surface reads. Two pin concepts, neither of which reaches the user.
- **Worse than "Pinned is empty": Pinned is silently _incomplete_.** The surface fetches one flat page of 100 conversations ordered by `updated_at DESC` and buckets it client-side. A chat pinned six months ago falls off page 1 and vanishes from Pinned entirely. The user pinned it to keep it; the product loses it.
- **Nothing updates.** Open Chats while a run is in flight and the "Running" chip stays "Running" forever — until the user navigates away and back. There is no SSE, no poll, no refetch-on-focus.
- **History stops at 100 rows.** No cursor, no "Load more". The Archived section is the tail of the sort, so it is the first thing to disappear.
- **Archive is promised in the page copy and delivered nowhere.** The lead reads "…reopen, continue, or archive". There is no archive control on the surface, on either host.
- **No title, no visible search.** Chats is registered full-bleed, so the shell suppresses the topbar — and the ⌘K trigger is mounted only inside the topbar. The surface ships with zero discoverable search entry point.
- **The preview line shows the wrong thing mid-run.** The row preview is the newest message of any role, so while the agent is working the row reads back the user's own prompt instead of an outcome.

## Evidence

Every row opened and verified in this worktree (`claude/design-parity-audit-7ec82a`).

| Claim                                                                   | File:line                                                                                                       | What the code actually does                                                                                                                                                                                                                                                                                                  |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend pin path is real, owner-checked, idempotent, audited            | `services/ai-backend/src/agent_runtime/api/conversation_coordinator.py:432-475`                                 | `set_conversation_pinned` resolves owner-or-admin, calls the store, 404s on miss, writes `pinned_before`/`pinned_after` audit metadata. **CONFIRMED**                                                                                                                                                                        |
| `pinned` is a real column, not metadata                                 | `services/ai-backend/migrations/0001_runtime_baseline.sql:47`, `:913`                                           | `pinned boolean DEFAULT false NOT NULL` + partial index `idx_agent_conversations_org_user_pinned_updated (org_id,user_id,updated_at DESC) WHERE (pinned AND deleted_at IS NULL)`. **CONFIRMED**                                                                                                                              |
| Facade proxies the pin route tenant-scoped                              | `services/backend-facade/src/backend_facade/app.py:571-589`                                                     | `POST /v1/agent/conversations/{id}/pin` → ai-backend with `identity.scoped_params()`. **CONFIRMED**                                                                                                                                                                                                                          |
| Web reads the first-class `pinned` field                                | `apps/frontend/src/features/chats/api/chatsApi.ts:156-158`                                                      | `isPinned()` returns `conversation.pinned === true`. **CONFIRMED** (audit cited `:157`; the function spans `:156-158`)                                                                                                                                                                                                       |
| `setChatPinned` has no non-test caller                                  | `apps/frontend/src/features/chats/api/chatsApi.ts:185`                                                          | Repo-wide grep for `setChatPinned` returns only this definition. **CONFIRMED**                                                                                                                                                                                                                                               |
| `pinConversation` has exactly one caller — `setChatPinned`              | `apps/frontend/src/api/agentApi.ts:302`                                                                         | Only referenced from `chatsApi.ts:35,191`. The chain is dead end-to-end. **CONFIRMED**                                                                                                                                                                                                                                       |
| The only pin control is the legacy sidebar's ⋯ menu                     | `apps/frontend/src/features/chat/components/sidebar/ConversationRow.tsx:91-104`                                 | ⋯ overflow → "Pin to top"/"Unpin" → `onTogglePin(id, !pinned)`. Same menu also exposes "Archive" (`:105-118`). **CONFIRMED**                                                                                                                                                                                                 |
| …and it is localStorage-backed, never hits the endpoint                 | `apps/frontend/src/features/chat/sidebar/usePinnedConversations.ts:20,60,83-97`                                 | `atlas:pinned:<userId>` JSON array in `KeyValueStore`. Header comment still says the backend has no pin field. No HTTP call. **CONFIRMED**                                                                                                                                                                                   |
| …wired only into `ChatScreen`, which is the `runCockpitWeb` opt-out     | `apps/frontend/src/features/chat/ChatScreen.tsx:237,1994-1996`; `app/featureFlags.ts:8`                         | `pinned.togglePinned` / `pinned.pinnedIds` into `<Sidebar>`; the flag is ON by default so ChatScreen is unreachable without a manual opt-out. **CONFIRMED**                                                                                                                                                                  |
| A **second dead** pin read exists                                       | `apps/frontend/src/features/chat/utils/groupConversations.ts:119-123`                                           | `isPinned()` reads `conversation.metadata.pinned` — nothing on the server writes it. Used at `:53` alongside the localStorage set. **CONFIRMED — not in the brief**                                                                                                                                                          |
| The design ships no pin affordance                                      | `tools/design-parity/design-kit/app-v3/copilot-app.jsx:255-286, 287-331`                                        | `ChatRow` is a bare `<button>`; `ChatsSurface` renders three `.rowlist`s and one "New chat" CTA. No ⋯, no menu, no archive. **CONFIRMED** — the design does not settle the question                                                                                                                                          |
| The archive fetch is one-shot                                           | `apps/frontend/src/features/chats/ChatsArchiveRoute.tsx:90-112`                                                 | One `useEffect` keyed on `[identity, reloadToken]`. No interval, no visibility listener, no SSE. **CONFIRMED**                                                                                                                                                                                                               |
| …on desktop too                                                         | `apps/desktop/renderer/destinationBinders.tsx:200-233`                                                          | `useSectionLoad(load)` — single fetch, `retry` only. **CONFIRMED**                                                                                                                                                                                                                                                           |
| Hard 100-row ceiling, client-side                                       | `apps/frontend/src/features/chats/api/chatsApi.ts:44`; `destinationBinders.tsx:204-208`                         | `DEFAULT_LIMIT = 100` / `query: { limit: 100, include_archived: true }`. Facade allows up to 200 (`app.py:412`), so 100 is a client choice. **CONFIRMED**                                                                                                                                                                    |
| `next_cursor` is declared and never set                                 | `services/ai-backend/src/agent_runtime/api/conversation_query_service.py:177-208`                               | Returns `ConversationListResponse(conversations=…, has_more=len==limit)` — `next_cursor` omitted. Its own docstring says "callers must re-request with a cursor (**not implemented yet**)". CONFIRMED                                                                                                                        |
| A keyset-cursor pattern already exists to copy                          | `services/ai-backend/src/agent_runtime/api/conversation_query_service.py:238-258`                               | `MessageCursor.encode(created_at, message_id)` powers `/messages`. **CONFIRMED**                                                                                                                                                                                                                                             |
| The list route takes no cursor/filter params                            | `services/ai-backend/src/runtime_api/http/routes.py:113-130`                                                    | Only `org_id,user_id,limit,include_archived,include_deleted`. **CONFIRMED**                                                                                                                                                                                                                                                  |
| Un-archive is `PATCH {archived:false}` — `/restore` is **undelete**     | `conversation_coordinator.py:370-378`; `schemas/conversations.py:430,437`                                       | `restore_conversation` docstring: "Restore a **soft-deleted** conversation". `UpdateConversationRequest.archived: bool \| None` — "`null` … un-archives (`archived: false`)". **AUDIT CORRECTED**                                                                                                                            |
| Chats is full-bleed, which suppresses the topbar                        | `packages/chat-surface/src/shell/ChatShell.tsx:36-46,236-237,296`                                               | `FULL_BLEED_DESTINATIONS = {"chats","run"}`; `fullBleed` → `{fullBleed ? null : <Topbar/>}`. **CONFIRMED**                                                                                                                                                                                                                   |
| …and the stated reason is stale                                         | `packages/chat-surface/src/shell/ChatShell.tsx:36-39`                                                           | Comment: "`chats` (its ChatScreen brings its own thread sidebar + header)". Both hosts now mount `<ChatsArchive>`, which has neither. **CONFIRMED — the rationale no longer holds**                                                                                                                                          |
| ⌘K trigger is mounted only in the topbar                                | `packages/chat-surface/src/shell/Topbar.tsx:149-160`                                                            | `CommandPaletteTrigger` has no other mount in `packages/chat-surface/src`. **CONFIRMED**                                                                                                                                                                                                                                     |
| Design shows the topbar on Chats and hides it exactly on Run + Settings | `tools/design-parity/design-kit/app-v3/copilot-app.jsx:739`                                                     | `const showTopbar = dest !== "workspace" && dest !== "settings";`. **CONFIRMED** — `workspace` is the design's slug for Run                                                                                                                                                                                                  |
| Preview has no role filter                                              | `services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py:1422-1443`                              | `SELECT * FROM agent_messages … ORDER BY created_at DESC LIMIT 1` — no `role` predicate. Same in `file/runtime_api_store.py:1327-1345` and `in_memory/runtime_api_store.py:500-518`. **CONFIRMED**                                                                                                                           |
| …but "contrary to its own docstring" is **DISPUTED**                    | `agent_runtime/api/ports.py:482-494`; `conversation_query_service.py:461-468`                                   | Port docstring says "most recent non-deleted message"; service says "last visible message's text". Neither promises a role filter. `packages/api-types/src/index.ts:571-574` says "last user/assistant message snippet" — also not assistant-only. **The behaviour is wrong vs the design; no docstring is being violated.** |
| The design's previews are outcome lines                                 | `tools/design-parity/design-kit/app-v3/copilot-data.jsx:722-750`                                                | "Balanced 3 accounts, flagged 1 variance", "Draft saved to Local files", "Streaming the launch thread". Never a user prompt. **CONFIRMED**                                                                                                                                                                                   |
| Three destination SSE streams already exist; conversations have none    | `services/backend/src/backend_app/projects/sse.py:1-56`; `.../home/sse.py`, `connectors/sse.py`, `inbox/sse.py` | Projects header states the house rule: "Cross-audit §5.2 mandates a single SSE convention across the monorepo: `GET /v1/<resource>/stream`". **CONFIRMED — four such streams, not three (inbox too)**                                                                                                                        |
| The Transport port already carries SSE                                  | `packages/chat-transport/src/transport.ts:24`; `types.ts:26-37`                                                 | `subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription`, re-exported by `packages/chat-surface/src/ports/Transport.ts`. **CONFIRMED**                                                                                                                                                                        |
| chat-surface already hosts transport-backed data hooks                  | `packages/chat-surface/src/destinations/run/useRunSession.ts:321-330`                                           | Uses `transport.subscribeServerSentEvents({ query: { after_sequence: latestSequenceRef.current } })`. Siblings: `useRunTranscript.ts`, `useRunSources.ts`. **CONFIRMED — a data hook here is precedent, not invention**                                                                                                      |
| In-memory buses do not span the API/worker process split                | `services/ai-backend/src/runtime_api/sse/inbox_bus.py:12-18`                                                    | "Works only when API and worker share a process. In production with separate processes, a publish from the worker never reaches API-side subscribers." **CONFIRMED**                                                                                                                                                         |
| `updated_at` is bumped on run start and on every appended message…      | `postgres/runtime_api_store.py:1120-1130`, `:1326-1330`                                                         | `append_message` and run-create both `UPDATE agent_conversations SET updated_at = …`. **CONFIRMED**                                                                                                                                                                                                                          |
| …but **not** on run status transitions                                  | `postgres/runtime_api_store.py:1444-1470` (`update_run_status`)                                                 | Touches `agent_runs` only. A cancel/fail/timeout, or a flip to `waiting_for_approval`, leaves the conversation row untouched. **CONFIRMED — not in the brief; it is what makes a naive tail incomplete**                                                                                                                     |
| Two hand-written copies of the same projection exist                    | `apps/frontend/src/features/chats/api/chatsApi.ts:77-178`; `destinationBinders.tsx:163-199`                     | Same buckets, drifted fields (desktop reads `metadata.{preview,model,pinned}`). **CONFIRMED — owned by PRD-03; this PRD consumes its result**                                                                                                                                                                                |
| `ChatsDestination.tsx` is exported and mounted by neither host          | `packages/chat-surface/src/destinations/chats/index.ts:5`; `src/index.ts:487`                                   | Grep for `ChatsDestination` over `apps/frontend/src` + `apps/desktop/renderer` returns nothing. **CONFIRMED**                                                                                                                                                                                                                |

## Design intent

Literal values from `tools/design-parity/design-kit/app-v3/`.

**Topbar is shown on Chats.** `copilot-app.jsx:739` — `const showTopbar = dest !== "workspace" && dest !== "settings";` — and `:817-828`:

```jsx
{
  showTopbar && (
    <div className="topbar">
      <div className="tb-title">
        <h1>{tTitle}</h1>
        <span className="sub">{tSub}</span>
      </div>
      <div className="tb-spacer" />
      <button className="tb-search" onClick={() => setPalette(true)}>
        <Icon.search /> Search & commands <kbd>⌘K</kbd>
      </button>
    </div>
  );
}
```

`TITLES.chats = ["Chats", "every conversation with the agent"]` (`copilot-app.jsx:599`).

`copilot.css:388-445` gives the exact metrics:

- `.topbar { height:46px; gap:12px; padding:0 18px; border-bottom:1px solid var(--line) }` — `--line: rgba(255,255,255,.06)` (`copilot.css:13`).
- `.tb-title { display:flex; align-items:baseline; gap:9px }` — **title and subtitle share one baseline row**, not stacked.
- `.tb-title h1 { font-size:13.5px; font-weight:600 }`.
- `.tb-title .sub { font-size:11.5px; color:var(--mut2) }` — `--mut2: #64646d` (`copilot.css:19`).
- `.tb-search { width:250px; max-width:32vw; padding:6px 10px; background:var(--panel); border:1px solid var(--line); border-radius:var(--r-sm) /*6px*/; color:var(--mut2); font-size:12px }`; `.tb-search svg { 13×13 }`; `.tb-search kbd { font-family:var(--mono); font-size:10px; border:1px solid var(--line2); border-radius:4px; padding:1px 4px }`.

**Row previews are outcomes, never prompts.** `copilot-data.jsx:726,736,745` — `"Streaming the launch thread"`, `"Balanced 3 accounts, flagged 1 variance"`, `"Draft saved to Local files"`.

**The mock ships no mutation affordance and no pagination.** `ChatRow` (`copilot-app.jsx:255-286`) is a bare `<button>` with icon / name+chip / sub / time. `ChatsSurface` (`:287-331`) renders `.pg-lead`, a Pinned header row with the one `cbtn cbtn--pri cbtn--sm` "New chat", then three `.rowlist`s. There is no ⋯, no archive control, no "Load more".

**This silence is not a decision — it is a static mock over an 8-row fixture.** The mock's own lead copy is the counter-evidence: `copilot-app.jsx:296-299` promises "each chat is a run you can reopen, continue, **or archive**", and `CHATS` carries `pinned: true` on one row (`copilot-data.jsx:730`) with nothing in the mock able to set it. A product that renders a Pinned section and an Archived section must be able to put things in them. This PRD therefore treats the mock as authoritative on **appearance** and explicitly makes the **write path** a product decision below.

## Architectural decision

### D1 — One transport-backed data hook in `packages/chat-surface` owns the whole Chats read/write model

**Seam:** new `packages/chat-surface/src/destinations/chats/useChatsArchive.ts`, exporting `useChatsArchive(): ChatsArchiveController` — fetch, bucket-scoped paging, live tail, `setPinned`, `setArchived`. Both hosts' binders collapse to navigation callbacks.

Why this seam: every remaining defect in this PRD is "the surface has no behaviour", and behaviour placed in a host binder must be written twice and will drift twice — it already has (`chatsApi.ts:77-178` ≡ `destinationBinders.tsx:163-199`, converged on the bucket rule, diverged on three field reads). `chat-surface` already owns transport-backed hooks for the cockpit (`useRunSession.ts`, `useRunTranscript.ts`, `useRunSources.ts`) and both hosts already mount a `TransportProvider`. `ChatsArchive.tsx` stays pure-presentation; the hook is a sibling module, not a change to the component's contract.

Rejected: (a) adding pin/live/paging props to `ChatsArchive` and letting each host implement them — that is the duplication that produced this PRD; (b) putting the logic in `packages/design-system` — feature workflows are banned there; (c) a fourth per-host `useSectionLoad` variant.

### D2 — Pin is set from the Chats row's hover overflow. The localStorage pin concept is deleted, not deprecated.

**Answer to the product question: the row.** A hover/focus-revealed ⋯ overflow on `Row` exposing **Pin to top / Unpin** and **Archive / Unarchive**, calling `POST /v1/agent/conversations/{id}/pin` and `PATCH {archived}` through the D1 hook.

Why the row and not the cockpit: the cockpit can only pin the conversation you are already inside. Curation is a list operation — you pin _this_ one out of twenty you are looking at. Chats is also the only surface that renders an Archived section, so archive and unarchive have to live where their result is visible or the action is a one-way door. And the affordance is not invented: `ConversationRow.tsx:91-118` already ships exactly this ⋯ → Pin/Unpin/Archive menu; we are relocating a proven interaction onto the real endpoint.

Why not migrate the legacy sidebar onto the endpoint instead: `ChatScreen` is the `runCockpitWeb` opt-out scheduled for retirement (WC-P8). Investing there buys a control on a surface being deleted and still leaves Chats without one.

**Retiring the second concept, in the same PR:**

1. Delete `apps/frontend/src/features/chat/sidebar/usePinnedConversations.ts` and its `pinnedIds`/`togglePinned` plumbing (`ChatScreen.tsx:237,1994-1996`, `Sidebar.tsx:49-51,153-155,191-200`).
2. Delete the `metadata.pinned` read at `groupConversations.ts:119-123` and make `groupConversations` read `conversation.pinned`.
3. While `ChatScreen` survives, its ⋯ calls the same shared mutation as Chats.
4. **One-shot, bounded migration** so nobody loses their pins: on first mount after upgrade, if `atlas:pinned:<userId>` exists, `POST /pin` for up to 50 ids (idempotent server-side, `conversation_coordinator.py:441-447`), then delete the key and set `atlas:pinned:<userId>:migrated`. Best-effort, fire-and-forget, no UI.

### D3 — The archive read model becomes bucket-scoped and cursor-paginated at the query, not at the client

The client-side bucket predicate over one page is not merely capped — it is **wrong**: a pinned or archived row older than the page boundary is unreachable. Fix the query.

**Contract change — `GET /v1/agent/conversations`** (ai-backend `routes.py:113-130`, mirrored in facade `app.py:410-431`):

| Param              | Type                       | Default | Meaning                                                                      |
| ------------------ | -------------------------- | ------- | ---------------------------------------------------------------------------- |
| `bucket`           | `pinned\|recent\|archived` | absent  | When set, server-side scoping. Absent → today's behaviour, byte-compatible.  |
| `cursor`           | opaque string              | absent  | Keyset from a prior `next_cursor`. Malformed/empty tolerated as "no cursor". |
| `limit`            | int 1..200                 | 30      | unchanged                                                                    |
| `include_archived` | bool                       | false   | unchanged; ignored when `bucket` is set                                      |
| `include_deleted`  | bool                       | false   | unchanged                                                                    |

Semantics: `pinned` → `pinned AND NOT archived AND deleted_at IS NULL`; `archived` → `(status='archived' OR archived_at IS NOT NULL) AND deleted_at IS NULL`; `recent` → the complement. All `ORDER BY updated_at DESC, id DESC`.

- **Cursor:** new `ConversationCursor` in `services/ai-backend/src/agent_runtime/api/` modelled byte-for-byte on `MessageCursor` (`conversation_query_service.py:238-258`) — `encode(updated_at, conversation_id)` / `decode`. `next_cursor` is set from the LAST returned row when `has_more`; today it is always `None` (`:206-208`).
- **Status codes:** 200; 400 only for an out-of-range `limit` (FastAPI `Query(ge=1, le=200)`); an undecodable `cursor` is tolerated as absent, matching `MessageCursor.decode`.
- **Authorization:** unchanged — `scoped_identity(request, …)` derives `(org_id, user_id)` from the verified bearer; `bucket`/`cursor` are filter inputs only and can never widen scope. Postgres RLS still gates on `org_id`.
- **Migration `0002_conversation_keyset.sql`** (+ `.rollback.sql`, + regenerated `migrations/MANIFEST.lock` via `tools/check_migration_manifest.py`):
  ```sql
  CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agent_conversations_org_user_updated_id
    ON agent_conversations (org_id, user_id, updated_at DESC, id DESC)
    WHERE deleted_at IS NULL;
  ```
  The existing `idx_agent_conversations_org_user_active_updated` (`0001:911`) lacks the `id` tiebreaker, so a keyset can skip or repeat rows when two conversations share an `updated_at`. The pinned bucket keeps its existing partial index (`0001:913`).
- **api-types:** `packages/api-types/src/index.ts` gains `ConversationBucket = "pinned"|"recent"|"archived"` and a `ListConversationsQuery` interface. `ConversationListResponse.next_cursor` already exists (`:963-967`) and finally becomes non-null.

**UI:** a `.ui-button--ghost` "Load more" at the foot of Recent and Archived, matching the house pattern already used by `ToolInvocationsTable.tsx:236-238` and `ReadAuditTab.tsx:149-152` — not infinite scroll, and not a fourth pattern.

### D4 — Live refresh is a fifth `GET /v1/<resource>/stream`, driven by a store tail on the same keyset

`GET /v1/agent/conversations/stream?after=<cursor>` in **ai-backend** (conversations live there, not in `backend`), facade pass-through proxy copied from `inbox_stream_routes.py`. Envelope `conversation_changed` carrying the identical projected `ConversationResponse` the list route returns, so the hook merges rows through one projector.

Why a **store tail** and not a pub/sub bus: `inbox_bus.py:12-18` states plainly that the in-memory bus "works only when API and worker share a process. In production with separate processes, a publish from the worker never reaches API-side subscribers" — and the run worker is a separate process by default (`RUNTIME_START_IN_PROCESS_WORKER` is the opt-in). A bus here would be correct in dev and silently dead in production, which is exactly the failure mode this PRD exists to remove. The tail re-queries `list_conversations(bucket=…, cursor=watermark)` on a bounded slice and works identically in both topologies, with **no new table and no bus**.

Why `?after=<cursor>` rather than the house `?after_sequence=N`: the watermark _is_ the keyset cursor from D3 — one codec serves pagination, reconnect-resume, and the tail, and unlike a per-process sequence counter it survives an API restart. This is a deliberate, documented divergence from the run/inbox streams, not a new mechanism.

**The tail is incomplete without one backend fix.** `update_run_status` (`postgres/runtime_api_store.py:1444+`) touches `agent_runs` only, so a cancel / failure / timeout / flip to `waiting_for_approval` never moves the conversation's `updated_at` — the chip the user is watching is precisely the one the tail would miss. `update_run_status` must bump the parent conversation's `updated_at` inside the same transaction, in all three adapters. That makes `updated_at` an honest row-version for the archive read model, which is what both D3 and D4 assume.

Rejected: polling `/v1/agent/conversations` every 30s (what `useActiveRunCount.ts` does — O(page) per tick, and a fourth refresh pattern); refetch-on-focus alone (does not update a surface the user is looking at); reusing the per-run stream (the archive is not scoped to one run).

### D5 — Split `fullBleed` into two independent shell decisions

`ChatShell.tsx:236-237` conflates "no topbar" with "no side columns". The design separates them: `showTopbar = dest !== "workspace" && dest !== "settings"` (`copilot-app.jsx:739`), while no destination in the mock has a context column or right rail at all.

- `SUPPRESS_TOPBAR: ReadonlySet<ShellDestinationSlug> = {"run"}` ∪ `settingsActive` — matches the design predicate exactly, and matches PRD-12's Settings finding (Settings keeps no topbar) rather than contradicting it.
- `FULL_BLEED_DESTINATIONS` keeps `{"chats","run"}` and now governs only the side columns. Chats gains a topbar and gains no side columns — which is what the mock shows.

The stale rationale in the comment at `ChatShell.tsx:36-39` ("its ChatScreen brings its own thread sidebar + header") is deleted with it; both hosts mount `<ChatsArchive>`, which has neither.

**Subtitles belong in the destinations registry, not the topbar.** `destinations.ts:63-72` is documented as "the ONLY place a slug's label lives"; add `sublabel?: string` to `DestinationMeta` (`chats: { label: "Chats", sublabel: "every conversation with the agent" }`) and resolve `subtitle = leaf ?? SUBLABEL_BY_SLUG[slug]` in `Topbar.tsx:89` so a run/conversation leaf still wins. Do not hard-code a string in `Topbar.tsx`.

### D6 — The preview projection prefers the assistant turn, decided at the port

Add `prefer_roles: tuple[str, ...] = ("assistant",)` to `AgentRuntimeApiStore.get_latest_message_for_conversation` (`ports.py:482-494`): return the newest non-deleted message whose role is in `prefer_roles`, falling back to the newest of any role when none exists (so a brand-new chat still shows the prompt rather than nothing). Implemented in all three adapters and pinned by `tests/unit/runtime_adapters/test_store_conformance.py`, which is the existing cross-adapter contract test (`:660-680`).

Why the port and not the caller: three adapters back this method and only a port-level contract plus the conformance test stops them drifting — the same class of bug D1 fixes on the client.

## Scope

**`packages/chat-surface`**

- `src/destinations/chats/useChatsArchive.ts` — **new.** The D1 controller: three bucket-scoped cursored fetches, SSE tail + merge, `setPinned`, `setArchived`, `loadMore(bucket)`, `retry`.
- `src/destinations/chats/useChatsArchive.test.tsx` — **new.** Fake `Transport`; pins bucket completeness, cursor append, SSE merge, optimistic pin/archive + rollback.
- `src/destinations/chats/ChatsArchive.tsx` — add `onTogglePin` / `onToggleArchive` / `onLoadMore(bucket)` / `hasMore` props; render the ghost "Load more" foot on Recent + Archived.
- `src/destinations/_shared/Row.tsx` — hover/focus-revealed `overflow` slot (menu button + `role="menu"`), keyboard reachable, `stopPropagation` so it never triggers row activation.
- `src/destinations/chats/ChatsDestination.tsx`, `index.ts:5`, `src/index.ts:487` — **delete** the dead forwarder rather than teach it the new props.
- `src/shell/ChatShell.tsx:36-46,236-237,296` — D5 split.
- `src/shell/Topbar.tsx:74-89` — subtitle falls back to the registry sublabel; `.tb-title` becomes a baseline-aligned row (design `align-items:baseline; gap:9px`), subtitle recoloured to `--color-text-subtle`.
- `src/shell/destinations.ts:58-83` — `sublabel` on `DestinationMeta` for all six rail slugs (`copilot-app.jsx:597-604`).
- `src/shell/ChatShell.test.tsx`, `src/shell/Topbar.test.tsx` — topbar-on-chats and sublabel assertions.

**`packages/api-types`**

- `src/index.ts` — `ConversationBucket`, `ListConversationsQuery`, doc-fix on `preview` (`:571-574`) to say assistant-preferred.
- `src/chats.ts:62-73` — no shape change; document that `pinned`/`status` are now server-scoped.

**`apps/frontend`**

- `src/features/chats/ChatsArchiveRoute.tsx` — collapses onto `useChatsArchive`; keeps only nav + the New-chat error banner.
- `src/features/chats/api/chatsApi.ts` — **delete** (`fetchChatsArchive`, `bucketConversations`, `setChatPinned` move into the hook; `toArchiveRow` is PRD-03's shared projector).
- `src/api/agentApi.ts:302` — `pinConversation` retained only if still used by ChatScreen; otherwise delete with the file above.
- `src/features/chat/sidebar/usePinnedConversations.ts` — **delete** (D2).
- `src/features/chat/ChatScreen.tsx:237,463-478,1994-1996`, `components/sidebar/Sidebar.tsx:49-51,153-155,191-200` — drop `pinnedIds`; ⋯ calls the shared mutation.
- `src/features/chat/utils/groupConversations.ts:53,119-123` — read `conversation.pinned`.
- `src/features/chats/migrateLegacyPins.ts` — **new**, the bounded one-shot migration.
- `src/features/chats/ChatsArchiveRoute.test.tsx` — updated for the new binder.

**`apps/desktop`**

- `renderer/destinationBinders.tsx:163-233` — delete `metaString`/`toArchiveRow`/`bucketConversations`/`loadChats`; `ChatsBinder` mounts `useChatsArchive`.

**`services/ai-backend`**

- `src/runtime_api/http/routes.py:113-130` — `bucket` + `cursor` params; register `GET /conversations/stream` (before `/conversations/{conversation_id}` so the literal path wins).
- `src/runtime_api/schemas/conversations.py` — `ConversationBucket` enum, `ConversationStreamEnvelope`.
- `src/runtime_api/sse/conversation_adapter.py` — **new**, store-tail SSE adapter modelled on `inbox_adapter.py` (25s heartbeat, same `event:`/`id:`/`data:` framing).
- `src/agent_runtime/api/conversation_query_service.py:177-208` — bucket scoping, `next_cursor`, `ConversationCursor`.
- `src/agent_runtime/api/ports.py:482-494` — `prefer_roles` on the preview read; bucket/cursor params on `list_conversations`.
- `src/runtime_adapters/{postgres,file,in_memory}/runtime_api_store.py` — implement both; bump conversation `updated_at` in `update_run_status`.
- `migrations/0002_conversation_keyset.sql` + `.rollback.sql` + regenerated `MANIFEST.lock`.
- `tests/unit/runtime_adapters/test_store_conformance.py`, `tests/unit/runtime_api/…` (new bucket/cursor/stream route tests), `tests/integration/persistence/test_conversation_pin_live.py` (extend for keyset + role-preferred preview).

**`services/backend-facade`**

- `src/backend_facade/app.py:410-431` — forward `bucket` + `cursor`.
- `src/backend_facade/conversation_stream_routes.py` — **new**, pass-through SSE proxy copied from `inbox_stream_routes.py`.
- `tests/` — proxy + param-forwarding tests.

## Non-goals

- **Chip / row / type-scale styling** — RC-1 (`StatusPill` → `.ui-badge`), RC-2 (icon tile fill), RC-4 (model tone), RC-6/7/8 (type ladder), RC-11 (compact time format). PRD-02 and its siblings own those. This PRD adds a topbar and an overflow menu; it changes no existing token.
- **The `Conversation → ChatArchiveRow` per-row projector.** PRD-03 lands `toChatArchiveRow`; `useChatsArchive` consumes it. This PRD deletes PRD-03's `bucketConversations` **only** because bucketing moves into the query — coordinate, do not re-derive.
- **⌘K actually finding a chat.** The palette store has no writer (`services/backend/src/backend_app/app.py`, `palette/store.py`) and an unexposed FTS5 `search_conversations` exists in `runtime_adapters/file/runtime_api_store.py`. D5 restores the _trigger_; making it return chats is a separate PRD.
- **Delete and share affordances.** Both routes exist and both stay unexposed here; only pin and archive are promised by the surface's own copy.
- **Postgres `LISTEN/NOTIFY`** for any of the five streams. D4 is deliberately store-tailed; converting all buses at once is its own change.
- **The 960px centring** (`ChatsArchive.tsx:150`) — cited to FR-4.1; a design-side question, not a code bug.
- **Retiring `ChatScreen`** (WC-P8). D2 removes its second pin concept; the screen itself survives this PRD.

## Risks & rollback

| Risk                                                                                                         | Guard                                                                                                                                                                                   | Rollback                                                                                                                             |
| ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Bumping `updated_at` in `update_run_status` reorders every list that sorts on it (Chats, Activity, sidebar). | `services/ai-backend/tests/unit/runtime_adapters/test_store_conformance.py` + `tests/test_runtime_event_timeline.py`; add an explicit ordering assertion.                               | Revert the single `UPDATE agent_conversations` per adapter; the tail degrades to message/run-start granularity, nothing else breaks. |
| `bucket`/`cursor` change list semantics for existing clients.                                                | Both params are absent-by-default and the unfiltered path is byte-identical; `tests/unit/runtime_api/test_fastapi_runtime_api.py` pins the legacy response.                             | Drop the params; the hook falls back to a single unfiltered page.                                                                    |
| Keyset skips/duplicates rows on `updated_at` ties.                                                           | The `(updated_at DESC, id DESC)` index + a conformance test that inserts two conversations with an identical `updated_at` and pages across the boundary.                                | `CREATE INDEX CONCURRENTLY` is online; `DROP INDEX CONCURRENTLY` in the rollback SQL.                                                |
| The SSE tail leaks cross-tenant rows.                                                                        | Channel scope is `scoped_identity()`-derived only; `tests/integration/persistence/test_rls_isolation.py` pattern + a route test asserting an `org_b` subscriber sees zero `org_a` rows. | Unregister the stream route; the hook falls back to fetch-on-mount + retry.                                                          |
| Deleting `usePinnedConversations` loses users' pins.                                                         | `migrateLegacyPins` runs once, is idempotent server-side, and is covered by a unit test asserting N POSTs then key deletion.                                                            | The migration is additive; reverting the deletion restores the old read path.                                                        |
| Removing `chats` from topbar suppression breaks the Chats layout (double scroll / lost height).              | `packages/chat-surface/src/shell/ChatShell.test.tsx` grid-template assertions; the design-parity harness re-run.                                                                        | Re-add `"chats"` to `SUPPRESS_TOPBAR` — a one-line, isolated revert (that is why D5 splits the sets).                                |
| The row overflow steals row-activation clicks.                                                               | `Row.test.tsx` asserts `onActivate` does **not** fire when the menu button or a menu item is clicked.                                                                                   | Remove the overflow slot; the hook's mutations stay callable from ChatScreen.                                                        |

## Definition of Done

1. `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_adapters/test_store_conformance.py` passes, and it contains an assertion that `get_latest_message_for_conversation` returns the newest **assistant** message when a later `user` message exists, for all three adapters.
2. `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_api/` passes with a new test asserting `GET /v1/agent/conversations?bucket=pinned&limit=1` on a fixture of 3 pinned + 150 unpinned conversations returns exactly 1 pinned row and a non-null `next_cursor`, and that following the cursor twice yields the other two — **the regression guard for the silently-incomplete Pinned bucket**.
3. The same suite asserts `GET /v1/agent/conversations` **without** `bucket`/`cursor` returns a response byte-identical to the pre-change shape (no `next_cursor`, same ordering).
4. A route test asserts an `org_b`-scoped subscriber to `GET /v1/agent/conversations/stream` receives zero envelopes for `org_a` conversations.
5. A store test asserts `update_run_status(run_id, CANCELLED)` raises the parent conversation's `updated_at`, and that a subsequent `list_conversations(cursor=<pre-cancel watermark>)` returns that conversation.
6. `services/ai-backend/migrations/0002_conversation_keyset.sql` exists with a matching `.rollback.sql`, and `cd services/ai-backend && .venv/bin/python tools/check_migration_manifest.py` exits 0.
7. `cd services/backend-facade && .venv/bin/python -m pytest tests/` passes with a test asserting `bucket` and `cursor` are forwarded verbatim to ai-backend, and one asserting the `/v1/agent/conversations/stream` proxy returns `content-type: text/event-stream` with the upstream body unmodified.
8. `packages/chat-surface/src/destinations/chats/useChatsArchive.test.tsx` asserts, against a fake `Transport`: (a) three bucket-scoped requests on mount; (b) `loadMore("archived")` appends and does not re-request page 1; (c) a `conversation_changed` SSE envelope flipping `latest_run_status` from `running` to `completed` re-renders the row's status as `done` **without a refetch**; (d) `setPinned(id,true)` moves the row to `pinned` optimistically and rolls back on a rejected request.
9. `packages/chat-surface/src/destinations/_shared/Row.test.tsx` asserts clicking the overflow button, and clicking a `role="menuitem"` inside it, do **not** invoke `onActivate`.
10. `packages/chat-surface/src/shell/ChatShell.test.tsx` asserts the Topbar renders when `activeDestination="chats"` and does **not** render when `activeDestination="run"` or `settingsActive`, and that Chats still renders no ContextPanel and no RightRail.
11. `packages/chat-surface/src/shell/Topbar.test.tsx` asserts `[data-testid="topbar-subtitle"]` reads exactly `every conversation with the agent` for `chats`, sourced from `destinations.ts` — matching `copilot-app.jsx:599`.
12. **Design values pinned numerically** — `Topbar.tsx` computed styles assert: title `font-size: 13.5px` / `font-weight: 600`; subtitle `font-size: 11.5px` / `color: var(--color-text-subtle)` (`#64646d` = design `--mut2`, `copilot.css:19`); `.tb-title` container `align-items: baseline` with `gap: 9px`; bar `height: 46px`, `padding: 0 18px`, `gap: 12px` (`copilot.css:388-411`).
13. `grep -rn "usePinnedConversations\|metadata.pinned\|atlas:pinned" apps packages --include="*.ts" --include="*.tsx"` returns matches **only** inside `apps/frontend/src/features/chats/migrateLegacyPins.ts` and its test — proving one pin concept remains.
14. `grep -rn "ChatsDestination" apps packages --include="*.ts" --include="*.tsx"` returns zero matches.
15. `npm run typecheck --workspace @0x-copilot/frontend`, `--workspace @0x-copilot/api-types`, `--workspace @0x-copilot/chat-surface` and `npm run build --workspace @0x-copilot/frontend` all exit 0.
16. The design-parity report for `chats` shows **0 HIGH** rows in the `Shell` anchor group — i.e. `topbar.title` is no longer `missing-in-live` (re-run per `tools/design-parity/SKILL.md`; baseline is 17 HIGH incl. `topbar.title`, `tools/design-parity/surfaces/chats/out/report-default.md:11-14`).
17. Manual acceptance, both hosts: with a run in flight, opening Chats and waiting shows the row's chip change from Running to Done with no navigation and no reload; ⋯ → Archive moves the row to Archived and ⋯ → Unarchive returns it; with >100 conversations, "Load more" under Archived reveals older rows.

## Dependencies

**Must land first**

- **PRD-03** (binder / first-class fields) — supplies `toChatArchiveRow` as the single per-row projector that `useChatsArchive` consumes, and fixes the desktop `metadata.*` reads. D1 deletes PRD-03's `bucketConversations` because bucketing moves into the query; that deletion must be coordinated, not duplicated.
- **PRD-02** (chip) — `StatusPill` → `.ui-badge`. Independent of this PRD's behaviour, but both re-render the same rows; landing the chip first keeps the parity re-run in DoD #16 attributable.

**Must coordinate (not blocking)**

- **PRD-12** (Settings chrome). D5 splits topbar suppression from full-bleed and sets `SUPPRESS_TOPBAR = {"run"} ∪ settingsActive`, which keeps Settings without a topbar exactly as the design specifies (`copilot-app.jsx:739`). If PRD-12 wants a Settings topbar, it changes the same set — one line, one file, no conflict in intent.

**This unblocks**

- Any Chats-surface work needing live data or history beyond page 1 (archive search, retention UX, bulk actions).
- The ⌘K chat index PRD — `GET /v1/agent/conversations?bucket=…&cursor=…` is the backfill read the palette writer needs.
- The rail-badge count PRD — `bucket=recent` with a server-side count replaces `useActiveRunCount`'s 30s O(page) poll.
- Any future destination needing live refresh: `conversation_adapter.py` is the first store-tailed SSE and the template for converting the four bus-backed streams off in-memory pub/sub.
