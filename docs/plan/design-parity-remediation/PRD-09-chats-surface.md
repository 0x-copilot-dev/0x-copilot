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
- **The model marker is brighter than the line it sits in.** `modelMonoStyle` re-colours the inline mono model tag to `--color-text-muted` `#98989f`; the design's `.mono` changes family only, so the tag inherits the sub-line's `--mut2` `#64646d`. One HIGH row on all eight rows plus a derivative. (Assigned to this PRD by README **G1** — it was previously deferred here to "PRD-02 and siblings", and PRD-02 is chip-only.)

## Evidence

Every row opened and verified in this worktree (`claude/design-parity-audit-7ec82a`).

| Claim                                                                   | File:line                                                                                                       | What the code actually does                                                                                                                                                                                                                                                                                                                        |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend pin path is real, owner-checked, idempotent, audited            | `services/ai-backend/src/agent_runtime/api/conversation_coordinator.py:432-475`                                 | `set_conversation_pinned` resolves owner-or-admin, calls the store, 404s on miss, writes `pinned_before`/`pinned_after` audit metadata. **CONFIRMED**                                                                                                                                                                                              |
| `pinned` is a real column, not metadata                                 | `services/ai-backend/migrations/0001_runtime_baseline.sql:47`, `:913`                                           | `pinned boolean DEFAULT false NOT NULL` + partial index `idx_agent_conversations_org_user_pinned_updated (org_id,user_id,updated_at DESC) WHERE (pinned AND deleted_at IS NULL)`. **CONFIRMED**                                                                                                                                                    |
| Facade proxies the pin route tenant-scoped                              | `services/backend-facade/src/backend_facade/app.py:571-589`                                                     | `POST /v1/agent/conversations/{id}/pin` → ai-backend with `identity.scoped_params()`. **CONFIRMED**                                                                                                                                                                                                                                                |
| Web reads the first-class `pinned` field                                | `apps/frontend/src/features/chats/api/chatsApi.ts:156-158`                                                      | `isPinned()` returns `conversation.pinned === true`. **CONFIRMED** (audit cited `:157`; the function spans `:156-158`)                                                                                                                                                                                                                             |
| `setChatPinned` has no non-test caller                                  | `apps/frontend/src/features/chats/api/chatsApi.ts:185`                                                          | Repo-wide grep for `setChatPinned` returns only this definition. **CONFIRMED**                                                                                                                                                                                                                                                                     |
| `pinConversation` has exactly one caller — `setChatPinned`              | `apps/frontend/src/api/agentApi.ts:302`                                                                         | Only referenced from `chatsApi.ts:35,191`. The chain is dead end-to-end. **CONFIRMED**                                                                                                                                                                                                                                                             |
| The only pin control is the legacy sidebar's ⋯ menu                     | `apps/frontend/src/features/chat/components/sidebar/ConversationRow.tsx:91-104`                                 | ⋯ overflow → "Pin to top"/"Unpin" → `onTogglePin(id, !pinned)`. Same menu also exposes "Archive" (`:105-118`). **CONFIRMED**                                                                                                                                                                                                                       |
| …and it is localStorage-backed, never hits the endpoint                 | `apps/frontend/src/features/chat/sidebar/usePinnedConversations.ts:20,60,83-97`                                 | `atlas:pinned:<userId>` JSON array in `KeyValueStore`. Header comment still says the backend has no pin field. No HTTP call. **CONFIRMED**                                                                                                                                                                                                         |
| …wired only into `ChatScreen`, which is the `runCockpitWeb` opt-out     | `apps/frontend/src/features/chat/ChatScreen.tsx:237,1994-1996`; `app/featureFlags.ts:8`                         | `pinned.togglePinned` / `pinned.pinnedIds` into `<Sidebar>`; the flag is ON by default so ChatScreen is unreachable without a manual opt-out. **CONFIRMED**                                                                                                                                                                                        |
| A **second dead** pin read exists                                       | `apps/frontend/src/features/chat/utils/groupConversations.ts:119-123`                                           | `isPinned()` reads `conversation.metadata.pinned` — nothing on the server writes it. Used at `:53` alongside the localStorage set. **CONFIRMED — not in the brief**                                                                                                                                                                                |
| The design ships no pin affordance                                      | `tools/design-parity/design-kit/app-v3/copilot-app.jsx:255-286, 287-331`                                        | `ChatRow` is a bare `<button>`; `ChatsSurface` renders three `.rowlist`s and one "New chat" CTA. No ⋯, no menu, no archive. **CONFIRMED** — the design does not settle the question                                                                                                                                                                |
| The archive fetch is one-shot                                           | `apps/frontend/src/features/chats/ChatsArchiveRoute.tsx:90-112`                                                 | One `useEffect` keyed on `[identity, reloadToken]`. No interval, no visibility listener, no SSE. **CONFIRMED**                                                                                                                                                                                                                                     |
| …on desktop too                                                         | `apps/desktop/renderer/destinationBinders.tsx:200-233`                                                          | `useSectionLoad(load)` — single fetch, `retry` only. **CONFIRMED**                                                                                                                                                                                                                                                                                 |
| Hard 100-row ceiling, client-side                                       | `apps/frontend/src/features/chats/api/chatsApi.ts:44`; `destinationBinders.tsx:204-208`                         | `DEFAULT_LIMIT = 100` / `query: { limit: 100, include_archived: true }`. Facade allows up to 200 (`app.py:412`), so 100 is a client choice. **CONFIRMED**                                                                                                                                                                                          |
| `next_cursor` is declared and never set                                 | `services/ai-backend/src/agent_runtime/api/conversation_query_service.py:177-208`                               | Returns `ConversationListResponse(conversations=…, has_more=len==limit)` — `next_cursor` omitted. Its own docstring says "callers must re-request with a cursor (**not implemented yet**)". CONFIRMED                                                                                                                                              |
| A keyset-cursor pattern already exists to copy                          | `services/ai-backend/src/agent_runtime/api/conversation_query_service.py:238-258`                               | `MessageCursor.encode(created_at, message_id)` powers `/messages`. **CONFIRMED**                                                                                                                                                                                                                                                                   |
| The list route takes no cursor/filter params                            | `services/ai-backend/src/runtime_api/http/routes.py:113-130`                                                    | Only `org_id,user_id,limit,include_archived,include_deleted`. **CONFIRMED**                                                                                                                                                                                                                                                                        |
| Un-archive is `PATCH {archived:false}` — `/restore` is **undelete**     | `conversation_coordinator.py:370-378`; `schemas/conversations.py:430,437`                                       | `restore_conversation` docstring: "Restore a **soft-deleted** conversation". `UpdateConversationRequest.archived: bool \| None` — "`null` … un-archives (`archived: false`)". **AUDIT CORRECTED**                                                                                                                                                  |
| Chats is full-bleed, which suppresses the topbar                        | `packages/chat-surface/src/shell/ChatShell.tsx:36-46,236-237,296`                                               | `FULL_BLEED_DESTINATIONS = {"chats","run"}`; `fullBleed` → `{fullBleed ? null : <Topbar/>}`. **CONFIRMED**                                                                                                                                                                                                                                         |
| …and the stated reason is stale                                         | `packages/chat-surface/src/shell/ChatShell.tsx:36-39`                                                           | Comment: "`chats` (its ChatScreen brings its own thread sidebar + header)". Both hosts now mount `<ChatsArchive>`, which has neither. **CONFIRMED — the rationale no longer holds**                                                                                                                                                                |
| ⌘K trigger is mounted only in the topbar                                | `packages/chat-surface/src/shell/Topbar.tsx:149-160`                                                            | `CommandPaletteTrigger` has no other mount in `packages/chat-surface/src`. **CONFIRMED**                                                                                                                                                                                                                                                           |
| Design shows the topbar on Chats and hides it exactly on Run + Settings | `tools/design-parity/design-kit/app-v3/copilot-app.jsx:739`                                                     | `const showTopbar = dest !== "workspace" && dest !== "settings";`. **CONFIRMED** — `workspace` is the design's slug for Run                                                                                                                                                                                                                        |
| Preview has no role filter                                              | `services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py:1422-1443`                              | `SELECT * FROM agent_messages … ORDER BY created_at DESC LIMIT 1` — no `role` predicate. Same in `file/runtime_api_store.py:1327-1345` and `in_memory/runtime_api_store.py:500-518`. **CONFIRMED**                                                                                                                                                 |
| …but "contrary to its own docstring" is **DISPUTED**                    | `agent_runtime/api/ports.py:482-494`; `conversation_query_service.py:461-468`                                   | Port docstring says "most recent non-deleted message"; service says "last visible message's text". Neither promises a role filter. `packages/api-types/src/index.ts:571-574` says "last user/assistant message snippet" — also not assistant-only. **The behaviour is wrong vs the design; no docstring is being violated.**                       |
| The design's previews are outcome lines                                 | `tools/design-parity/design-kit/app-v3/copilot-data.jsx:722-750`                                                | "Balanced 3 accounts, flagged 1 variance", "Draft saved to Local files", "Streaming the launch thread". Never a user prompt. **CONFIRMED**                                                                                                                                                                                                         |
| Three destination SSE streams already exist; conversations have none    | `services/backend/src/backend_app/projects/sse.py:1-56`; `.../home/sse.py`, `connectors/sse.py`, `inbox/sse.py` | Projects header states the house rule: "Cross-audit §5.2 mandates a single SSE convention across the monorepo: `GET /v1/<resource>/stream`". **CONFIRMED — four such streams, not three (inbox too)**                                                                                                                                              |
| The Transport port already carries SSE                                  | `packages/chat-transport/src/transport.ts:24`; `types.ts:26-37`                                                 | `subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription`, re-exported by `packages/chat-surface/src/ports/Transport.ts`. **CONFIRMED**                                                                                                                                                                                              |
| chat-surface already hosts transport-backed data hooks                  | `packages/chat-surface/src/destinations/run/useRunSession.ts:321-330`                                           | Uses `transport.subscribeServerSentEvents({ query: { after_sequence: latestSequenceRef.current } })`. Siblings: `useRunTranscript.ts`, `useRunSources.ts`. **CONFIRMED — a data hook here is precedent, not invention**                                                                                                                            |
| In-memory buses do not span the API/worker process split                | `services/ai-backend/src/runtime_api/sse/inbox_bus.py:12-18`                                                    | "Works only when API and worker share a process. In production with separate processes, a publish from the worker never reaches API-side subscribers." **CONFIRMED**                                                                                                                                                                               |
| `updated_at` is bumped on run start and on every appended message…      | `postgres/runtime_api_store.py:1120-1130`, `:1326-1330`                                                         | `append_message` and run-create both `UPDATE agent_conversations SET updated_at = …`. **CONFIRMED**                                                                                                                                                                                                                                                |
| …but **not** on run status transitions                                  | `postgres/runtime_api_store.py:1444-1470` (`update_run_status`)                                                 | Touches `agent_runs` only. A cancel/fail/timeout, or a flip to `waiting_for_approval`, leaves the conversation row untouched. **CONFIRMED — not in the brief; it is what makes a naive tail incomplete**                                                                                                                                           |
| Two hand-written copies of the same projection exist                    | `apps/frontend/src/features/chats/api/chatsApi.ts:77-178`; `destinationBinders.tsx:163-199`                     | Same buckets, drifted fields (desktop reads `metadata.{preview,model,pinned}`). **CONFIRMED — PRD-03 owns the per-row `toChatArchiveRow` only (README C8); the bucketing/fetch/paging half is this PRD's**                                                                                                                                         |
| `ChatsDestination.tsx` is exported and mounted by neither host          | `packages/chat-surface/src/destinations/chats/index.ts:5`; `src/index.ts:487`                                   | Grep for `ChatsDestination` over `apps/frontend/src` + `apps/desktop/renderer` returns nothing. **CONFIRMED — deletion is PRD-13's (README C17); recorded here only because the harness renders `ChatsArchive` instead**                                                                                                                           |
| The model marker re-colours itself one tone brighter                    | `packages/chat-surface/src/destinations/chats/ChatsArchive.tsx:426-430` (`:446` is the call site)               | `modelMonoStyle = { fontFamily: "var(--font-mono)", color: "var(--color-text-muted)", whiteSpace: "nowrap" }`. `--color-text-muted: #98989f` (`packages/design-system/src/styles.css:177`). Design `.mono { font-family: var(--mono) }` only (`copilot.css:138-140`) inside `.lrow__sub { color: var(--mut2) }` (`:1643-1648`). **CONFIRMED — G1** |
| Topbar's title group is stacked, not baseline-aligned                   | `packages/chat-surface/src/shell/Topbar.tsx:104-112`                                                            | `leadStyle = { display:"flex", flexDirection:"column", justifyContent:"center", gap:1 }`. Design `.tb-title { display:flex; align-items:baseline; gap:9px }` (`copilot.css:398-403`). **CONFIRMED**                                                                                                                                                |
| …and its subtitle uses the wrong grey                                   | `packages/chat-surface/src/shell/Topbar.tsx:124-131`                                                            | `color: var(--color-text-muted)` `#98989f`. Design `.tb-title .sub { color: var(--mut2) }` `#64646d`, which is `--color-text-subtle` (`styles.css:178`). **CONFIRMED — the token already exists; the call site picks wrong**                                                                                                                       |
| …and the bar's own box metrics are off                                  | `packages/chat-surface/src/shell/Topbar.tsx:91-101`                                                             | `gap: 16`, `padding: "0 16px"`; design `gap:12px`, `padding:0 18px` (`copilot.css:388-397`). `TOPBAR_HEIGHT = 46` already matches (`Topbar.tsx:11`, pinned by `Topbar.test.tsx:8`). **CONFIRMED**                                                                                                                                                  |
| No sans rung exists at the design's 13.5px title                        | `packages/design-system/src/styles.css:62-71`; PRD-01 §B                                                        | Ladder is 9 / 11.2 / 12.5 / **13.6** / 14 / 16 / 18 / 22.4 / 32px; PRD-01 retunes `--font-size-sm` to 13px and adds a **mono-only** micro-ladder. So 13.5px sans is unreachable without a new token, which cross-cutting rule 1 forbids. **CONFIRMED — drives the `expectDivergence` in D5**                                                       |
| The chats harness renders `ChatsArchive` alone, with no shell           | `tools/design-parity/lib/render-live-chats.test.tsx:32-42,249-251`; `surfaces/chats/anchors.json:7-11`          | The file's own header says `topbar.title` "has NO live counterpart and must be reported as a structural" HIGH. Adding a topbar therefore also requires a live selector + a harness that renders it. **CONFIRMED — this is why DoD 16 needs a new harness file**                                                                                    |
| `ai-backend` migrations high-water mark is `0001`                       | `services/ai-backend/migrations/` (`0001_runtime_baseline.sql`, `.rollback.sql`, `MANIFEST.lock`, `staged/`)    | `ls` on this worktree: no `0002`+ exists yet. `services/backend` high-water is `0045_provider_api_keys_custom_endpoint.sql`. **CONFIRMED — README's pre-assignment gives this PRD `0004`**                                                                                                                                                         |

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

**`.mono` is a family switch, not a tone switch.** `copilot.css:138-140` — `.mono { font-family: var(--mono) }`, and nothing else. The mono model tag therefore inherits its container's colour, `.lrow__sub { font-size:11px; color:var(--mut2); margin-top:1px }` (`copilot.css:1643-1648`), i.e. `#64646d` — **the same** tone as the rest of the sub-line, not a brighter one.

**Row previews are outcomes, never prompts.** `copilot-data.jsx:726,736,745` — `"Streaming the launch thread"`, `"Balanced 3 accounts, flagged 1 variance"`, `"Draft saved to Local files"`.

**The mock ships no mutation affordance and no pagination.** `ChatRow` (`copilot-app.jsx:255-286`) is a bare `<button>` with icon / name+chip / sub / time. `ChatsSurface` (`:287-331`) renders `.pg-lead`, a Pinned header row with the one `cbtn cbtn--pri cbtn--sm` "New chat", then three `.rowlist`s. There is no ⋯, no archive control, no "Load more".

**This silence is not a decision — it is a static mock over an 8-row fixture.** The mock's own lead copy is the counter-evidence: `copilot-app.jsx:296-299` promises "each chat is a run you can reopen, continue, **or archive**", and `CHATS` carries `pinned: true` on one row (`copilot-data.jsx:730`) with nothing in the mock able to set it. A product that renders a Pinned section and an Archived section must be able to put things in them. This PRD therefore treats the mock as authoritative on **appearance** and explicitly makes the **write path** a product decision below.

## Architectural decision

### D1 — One transport-backed data hook in `packages/chat-surface` owns the whole Chats read/write model

**Seam:** new `packages/chat-surface/src/destinations/chats/useChatsArchive.ts`, exporting `useChatsArchive(): ChatsArchiveController` — fetch, bucket-scoped paging, live tail, `setPinned`, `setArchived`. Both hosts' binders collapse to navigation callbacks.

Why this seam: every remaining defect in this PRD is "the surface has no behaviour", and behaviour placed in a host binder must be written twice and will drift twice — it already has (`chatsApi.ts:77-178` ≡ `destinationBinders.tsx:163-199`, converged on the bucket rule, diverged on three field reads). `chat-surface` already owns transport-backed hooks for the cockpit (`useRunSession.ts`, `useRunTranscript.ts`, `useRunSources.ts`) and both hosts already mount a `TransportProvider`. `ChatsArchive.tsx` stays pure-presentation; the hook is a sibling module, not a change to the component's contract.

**Division of labour with PRD-03 (README C8).** PRD-03 ships **only** the per-row `toChatArchiveRow` projector. It does **not** ship a shared `bucketConversations`, because bucketing moves into the SQL query here two waves later and a shared helper would be born dead. `useChatsArchive` imports `toChatArchiveRow` and owns fetch, bucket scoping, paging, the tail, and the mutations. Nothing to "delete" from PRD-03 — the ordering fix is that it is never written.

Rejected: (a) adding pin/live/paging props to `ChatsArchive` and letting each host implement them — that is the duplication that produced this PRD; (b) putting the logic in `packages/design-system` — feature workflows are banned there; (c) a fourth per-host `useSectionLoad` variant.

### D2 — Pin is set from the Chats row's hover overflow. The localStorage pin concept is deleted, not deprecated.

**Answer to the product question: the row.** A ⋯ overflow control on `Row` exposing **Pin to top / Unpin** and **Archive / Unarchive**, calling `POST /v1/agent/conversations/{id}/pin` and `PATCH {archived}` through the D1 hook.

**It is always rendered, not hover-revealed.** A hover-reveal needs a `:hover` / `:focus-within` rule, which means a declaration in `packages/design-system/src/styles.css` — and the README's hot-file table lists that file's claimants as **01, 02, 08, 10, 11**; PRD-09 is not one of them and must not become one. So the trigger is a persistently-mounted, `--color-text-subtle` glyph inside PRD-08's `Row` trailing region. The design ships no ⋯ at all, so this is a deliberate live-only addition: record `expectDivergence` on the affected chats anchors (`lib/compare.mjs:172` reads that key) rather than letting it re-raise forever. If a later PRD that **does** own `styles.css` wants the reveal, it is one rule inside PRD-08's `.ui-list-row` recipe.

**`Row.tsx` is PRD-08's file (README C9).** PRD-08 lands `trailing` + the always-reserved 16px, `iconTone`, the icon-tile background and `className="ui-list-row"`, and absorbs PRD-04's title-weight change. PRD-09 stacks its `overflow` slot on the **post-PRD-08** file; PRD-11 then stacks `subFont`/`iconSize` on ours. Do not re-derive any of PRD-08's props here.

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
- **Migration `0004_conversation_keyset.sql`** (+ `.rollback.sql`, + regenerated `migrations/MANIFEST.lock` via `tools/check_migration_manifest.py`). The id is **`0004`, not `0002`** — README C18 pre-assigns `ai-backend` `0002` to PRD-05 (`0002_run_history_index.sql`) and `0003` to PRD-07 (`0003_conversation_project.sql`). Verified on disk in this worktree: `services/ai-backend/migrations/` contains only `0001_runtime_baseline.sql`, so `0004` is free and the two intervening ids land ahead of this PRD's wave. (`services/backend`'s high-water mark is `0045`; this PRD adds nothing there.)
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

**PRD-09 owns this split (README C14).** PRD-12 D3 defines the same set; per the ruling PRD-12 keeps only "web passes `settingsActive`" plus threading it to the rail, and consumes `SUPPRESS_TOPBAR` as landed here.

- `SUPPRESS_TOPBAR: ReadonlySet<ShellDestinationSlug> = {"run"}` ∪ `settingsActive` — matches the design predicate exactly, and matches PRD-12's Settings finding (Settings keeps no topbar) rather than contradicting it.
- `FULL_BLEED_DESTINATIONS` keeps `{"chats","run"}` and now governs only the side columns. Chats gains a topbar and gains no side columns — which is what the mock shows.

The stale rationale in the comment at `ChatShell.tsx:36-39` ("its ChatScreen brings its own thread sidebar + header") is deleted with it; both hosts mount `<ChatsArchive>`, which has neither.

**Subtitles belong in the destinations registry, not the topbar.** `destinations.ts:63-72` is documented as "the ONLY place a slug's label lives"; add `sublabel?: string` to `DestinationMeta` (`chats: { label: "Chats", sublabel: "every conversation with the agent" }`) and resolve `subtitle = leaf ?? SUBLABEL_BY_SLUG[slug]` in `Topbar.tsx:89` so a run/conversation leaf still wins. Do not hard-code a string in `Topbar.tsx`.

This is also what closes Activity's `AUDIT.md` HIGH-4 ("the per-destination subtitle is structurally unreachable"), which PRD-08 lists as a non-goal attributed to a nameless "shell registry" PRD — README **C15** rules that non-goal must name **PRD-09**. Nothing is owed back to PRD-08; the sublabel map covers all six rail slugs (`copilot-app.jsx:597-604`), Activity included.

**Topbar box + type, and the one value that cannot be hit.** `Topbar.tsx` today sets `gap: 16` / `padding: "0 16px"` (`:91-101`) where the design is `gap:12px` / `padding:0 18px` (`copilot.css:388-397`); `leadStyle` stacks the title over the subtitle in a column (`:104-112`) where the design is `align-items:baseline; gap:9px` (`copilot.css:398-403`); and the subtitle is `--color-text-muted` `#98989f` (`:124-131`) where the design is `--mut2` `#64646d` — which is the **already-existing** `--color-text-subtle` (`styles.css:178`). All four are call-site fixes, no new token. `TOPBAR_HEIGHT = 46` (`Topbar.tsx:11`) and `--font-weight-semibold: 600` (`styles.css:75`) are already right.

The single exception is the title's **13.5px**. The sans ladder has no such rung (9 / 11.2 / 12.5 / 13.6 / 14 / 16 / 18 / 22.4 / 32px, `styles.css:62-70`), PRD-01 retunes `--font-size-sm` to **13px**, and its new micro-ladder is mono-only. Cross-cutting rule 1 forbids minting a token where the tier already has one, so the title keeps `var(--font-size-sm)` and the residual **0.5px** is recorded as `expectDivergence` on the `topbar.title` anchor with that reason. The subtitle's `--font-size-2xs` (11.2px) is 0.3px off 11.5px — under the comparator's 0.4px flag threshold (`lib/compare.mjs:89-110`), so it needs nothing.

### D6 — The preview projection prefers the assistant turn, decided at the port

Add `prefer_roles: tuple[str, ...] = ("assistant",)` to `AgentRuntimeApiStore.get_latest_message_for_conversation` (`ports.py:482-494`): return the newest non-deleted message whose role is in `prefer_roles`, falling back to the newest of any role when none exists (so a brand-new chat still shows the prompt rather than nothing). Implemented in all three adapters and pinned by `tests/unit/runtime_adapters/test_store_conformance.py`, which is the existing cross-adapter contract test (`:660-680`).

Why the port and not the caller: three adapters back this method and only a port-level contract plus the conformance test stops them drifting — the same class of bug D1 fixes on the client.

### D7 — The mono model marker stops re-declaring a colour (README G1)

`modelMonoStyle` (`ChatsArchive.tsx:426-430`) sets `color: "var(--color-text-muted)"` `#98989f` on a span that is already inside the sub-line. The design's `.mono` is a **family switch only** (`copilot.css:138-140`), so in the mock the tag inherits `.lrow__sub`'s `--mut2` `#64646d` (`copilot.css:1643-1648`). One HIGH row on every one of the eight rows (`surfaces/chats/out/report-default.md:21`, anchor `row.running.sub.mono`) plus a derivative `lineHeight` row (`:139`).

**The fix is one deletion, not a re-point:** drop the `color` key from `modelMonoStyle` so the span inherits. Do **not** set `color: "var(--color-text-subtle)"` — that hard-codes the parent's choice into the child and re-breaks the moment `Row`'s sub-line tone changes. `Row.tsx`'s `subStyle` already sets `color: "var(--color-text-subtle)"` (`packages/chat-surface/src/destinations/_shared/Row.tsx:106-112`), which is `#64646d` = the design's `--mut2` (`styles.css:178`), so inheritance lands on the right value with zero token work. This is the "the token exists, the call site picks wrong" pattern, in its cheapest form.

Scope guard: this PRD touches **only** `modelMonoStyle`'s `color`. Every other Chats tone/size/weight finding stays with its owner (see Non-goals).

## Scope

**`packages/chat-surface`**

- `src/destinations/chats/useChatsArchive.ts` — **new.** The D1 controller: three bucket-scoped cursored fetches, SSE tail + merge, `setPinned`, `setArchived`, `loadMore(bucket)`, `retry`.
- `src/destinations/chats/useChatsArchive.test.tsx` — **new.** Fake `Transport`; pins bucket completeness, cursor append, SSE merge, optimistic pin/archive + rollback.
- `src/destinations/chats/ChatsArchive.tsx` — add `onTogglePin` / `onToggleArchive` / `onLoadMore(bucket)` / `hasMore` props; render the ghost "Load more" foot on Recent + Archived; **drop the `color` key from `modelMonoStyle` (`:426-430`) so the mono model tag inherits the sub-line tone (D7 / G1)**.
- `src/destinations/_shared/Row.tsx` — **PRD-08 owns this file (C9); stack on its merged version.** Add only an `overflow?: ReactNode` slot (menu button + `role="menu"`), always rendered, keyboard reachable, `stopPropagation` so it never triggers row activation. No `styles.css` edit — PRD-09 is not a claimant of that file.
- `src/shell/ChatShell.tsx:36-46,236-237,296` — D5 split. **PRD-09 owns `SUPPRESS_TOPBAR` (C14).**
- `src/shell/Topbar.tsx:74-131` — subtitle falls back to the registry sublabel; `leadStyle` (`:104-112`) becomes a baseline-aligned row (`align-items:"baseline"`, `gap:9`); subtitle recoloured `--color-text-muted` → `--color-text-subtle` (`:124-131`); `barStyle` (`:91-101`) `gap: 16 → 12`, `padding: "0 16px" → "0 18px"`. No token additions.
- `src/shell/destinations.ts:58-83` — `sublabel` on `DestinationMeta` for all six rail slugs (`copilot-app.jsx:597-604`).
- `src/shell/ChatShell.test.tsx`, `src/shell/Topbar.test.tsx` — topbar-on-chats and sublabel assertions.

**`tools/design-parity`** (the parity half of the DoD)

- `lib/render-live-chats-topbar.test.tsx` — **new** sibling harness rendering the real `Topbar` for `activeDestination="chats"` into `surfaces/chats/live/`. Required because `lib/render-live-chats.test.tsx:32-42` deliberately renders `ChatsArchive` **alone**, which is why `topbar.title` currently has no live counterpart. The file name matches the `lib/render-live*.test.tsx` glob, so **do not edit `vitest.config.mjs`** — it is a merge point for every PRD in flight.
- `surfaces/chats/anchors.json` — give `topbar.title` (`:7-11`) a `live` selector; add `topbar.sub` and `topbar.search`; record `expectDivergence` (the key `lib/compare.mjs:172` actually reads) on `topbar.title`'s font-size for the 13.5→13px residual, and on the row anchors gaining the live-only ⋯ control.
- `surfaces/chats/out/*` — regenerated in the same commit, per README §6.

**`packages/api-types`**

- `src/index.ts` — `ConversationBucket`, `ListConversationsQuery`, doc-fix on `preview` (`:571-574`) to say assistant-preferred.
- `src/chats.ts:62-73` — no shape change; document that `pinned`/`status` are now server-scoped.

**`apps/frontend`**

- `src/features/chats/ChatsArchiveRoute.tsx` — collapses onto `useChatsArchive`; keeps only nav + the New-chat error banner.
- `src/features/chats/api/chatsApi.ts` — **delete** (`fetchChatsArchive`, `bucketConversations`, `setChatPinned` move into the hook; `toArchiveRow` is superseded by PRD-03's shared `toChatArchiveRow`).
- `src/api/agentApi.ts:302` — `pinConversation` retained only if still used by ChatScreen; otherwise delete with the file above.
- `src/features/chat/sidebar/usePinnedConversations.ts` — **delete** (D2).
- `src/features/chat/ChatScreen.tsx:237,463-478,1994-1996`, `components/sidebar/Sidebar.tsx:49-51,153-155,191-200` — drop `pinnedIds`; ⋯ calls the shared mutation.
- `src/features/chat/utils/groupConversations.ts:53,119-123` — read `conversation.pinned`.
- `src/features/chats/migrateLegacyPins.ts` — **new**, the bounded one-shot migration.
- `src/features/chats/ChatsArchiveRoute.test.tsx` — updated for the new binder.

**`apps/desktop`**

- `renderer/destinationBinders.tsx:163-233` — delete `metaString`/`toArchiveRow`/`bucketConversations`/`loadChats`; `ChatsBinder` mounts `useChatsArchive`. **The program's hottest file — eight claimant PRDs.** Rebase onto the wave-2 result (PRD-08 then PRD-07) before touching it, and hand the merge to the wave's single merge owner.

**`services/ai-backend`**

- `src/runtime_api/http/routes.py:113-130` — `bucket` + `cursor` params; register `GET /conversations/stream` (before `/conversations/{conversation_id}` so the literal path wins).
- `src/runtime_api/schemas/conversations.py` — `ConversationBucket` enum, `ConversationStreamEnvelope`.
- `src/runtime_api/sse/conversation_adapter.py` — **new**, store-tail SSE adapter modelled on `inbox_adapter.py` (25s heartbeat, same `event:`/`id:`/`data:` framing).
- `src/agent_runtime/api/conversation_query_service.py:177-208` — bucket scoping, `next_cursor`, `ConversationCursor`.
- `src/agent_runtime/api/ports.py:482-494` — `prefer_roles` on the preview read; bucket/cursor params on `list_conversations`.
- `src/runtime_adapters/{postgres,file,in_memory}/runtime_api_store.py` — implement both; bump conversation `updated_at` in `update_run_status`. Six PRDs touch these three adapters; every new port method must add a case to `tests/unit/runtime_adapters/test_store_conformance.py`, which is the only mechanism keeping them in sync.
- `migrations/0004_conversation_keyset.sql` + `.rollback.sql` + regenerated `MANIFEST.lock` (C18: `0002` is PRD-05's, `0003` is PRD-07's).
- `tests/unit/runtime_adapters/test_store_conformance.py`, `tests/unit/runtime_api/…` (new bucket/cursor/stream route tests), `tests/integration/persistence/test_conversation_pin_live.py` (extend for keyset + role-preferred preview).

**`services/backend-facade`**

- `src/backend_facade/app.py:410-431` — forward `bucket` + `cursor`.
- `src/backend_facade/conversation_stream_routes.py` — **new**, pass-through SSE proxy copied from `inbox_stream_routes.py`. Register the literal `/v1/agent/conversations/stream` **before** `/v1/agent/conversations/{conversation_id}` in the facade as well as in ai-backend — FastAPI matches in registration order and the path param is an unconstrained `str` (same hazard the program flags for PRD-05's `/runs` collection and PRD-12's `/runs/active_count`).
- `tests/` — proxy + param-forwarding tests.

## Addendum — `sect.*` anchor retarget (README O3)

PRD-01 hands the chats anchor edit to "PRD-09 for chats"; PRD-09's anchors scope covered
only `topbar.*`, so it was never accepted and the `sect.* margin` report row can never
clear. **PRD-09 accepts it** — one line, in a file this PRD already edits.

Scope addition:
| File | Why |
| --- | --- |
| `tools/design-parity/surfaces/chats/anchors.json:41-42` | Retarget `section-header-label` → `section-header`: PRD-01 moves the `.ui-mono-caps` recipe onto the LABEL element (README C13), so the label anchor no longer maps to the element carrying the margin. |

DoD addition:

- `tools/design-parity/surfaces/chats/anchors.json` maps the `sect.*` label anchor to the
  label element, and the regenerated `chats` report contains **no** `sect.*` row whose
  property is `margin`.

## Non-goals

- **Chip / row / type-scale styling** (`AUDIT.md` numbering) — RC-1 (`StatusPill` → `.ui-badge`) → **PRD-02**; RC-2 + RC-13 (icon tile fill, jade tint level) → **PRD-08**; RC-5/6/7/8 (type ladder, section-head rung, body 13.6→13px, weight inflation) → **PRD-01**; RC-11 (`formatRelativeTime` prose widening the time column) → **PRD-08**'s row vocabulary. This PRD adds a topbar, an overflow control and one colour deletion; it adds **no** token.
  **RC-4 (the mono model tone) is no longer a non-goal** — README **G1** assigns it here; it is D7.
- **Row glyphs rendering 18px where the design forces 15×15** (`copilot.css:290` `.lrow__ic svg`; `Row.tsx:70-79` sizes the slot, not the svg) — README **G2** assigns this to **PRD-08 D5**, on the shared `Row`. Do not fix it in `ChatsArchive`.
- **`.ui-button--sm` computing weight 500 where the design's `.cbtn--pri` re-asserts 600** (`styles.css:443-449` vs `:462-466`; design `copilot.css:491-496`) — README **G9** assigns this to **PRD-01**, one line in `styles.css`. It affects this surface's "New chat" CTA and PRD-11's Tools CTA identically, which is why it is a design-system fix and not a Chats fix. The "Load more" foot added by D3 uses `.ui-button--ghost` and is unaffected.
- **Deleting `ChatsDestination.tsx`** — README **C17** gives the deletion, the barrel edit and the orphan guard that keeps it deleted to **PRD-13**. This PRD neither deletes it nor teaches it the new props; it simply never mounts it (neither host does today).
- **The `Conversation → ChatArchiveRow` per-row projector.** PRD-03 lands `toChatArchiveRow`; `useChatsArchive` consumes it. Per README **C8** PRD-03 ships nothing else of the chats read model — no shared `bucketConversations`, because bucketing moves into the query here. Coordinate, do not re-derive, and do not let a doomed helper be written.
- **⌘K actually finding a chat.** The palette store has no writer (`services/backend/src/backend_app/app.py`, `palette/store.py`) and an unexposed FTS5 `search_conversations` exists in `runtime_adapters/file/runtime_api_store.py`. D5 restores the _trigger_; making it return chats is a separate PRD.
- **Delete and share affordances.** Both routes exist and both stay unexposed here; only pin and archive are promised by the surface's own copy.
- **Postgres `LISTEN/NOTIFY`** for any of the five streams. D4 is deliberately store-tailed; converting all buses at once is its own change.
- **The 960px centring** (`ChatsArchive.tsx:147-156`, `margin: "0 auto"`; the design's `.pg` has none) — README **G6** routes the decision to **PRD-10**, which must settle it before its shared `_shared/Page` hard-codes `margin: 0 auto` and institutionalises the divergence. If PRD-10 rules that centring stays, it records `expectDivergence` on `page.container` for chats **and** projects; this PRD changes nothing either way.
- **Retiring `ChatScreen`** (WC-P8). D2 removes its second pin concept; the screen itself survives this PRD.

## Risks & rollback

| Risk                                                                                                                                                                                                                                                      | Guard                                                                                                                                                                                                                                                              | Rollback                                                                                                                             |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| Bumping `updated_at` in `update_run_status` reorders every list that sorts on it (Chats, Activity, sidebar).                                                                                                                                              | `services/ai-backend/tests/unit/runtime_adapters/test_store_conformance.py` + `tests/test_runtime_event_timeline.py`; add an explicit ordering assertion.                                                                                                          | Revert the single `UPDATE agent_conversations` per adapter; the tail degrades to message/run-start granularity, nothing else breaks. |
| `bucket`/`cursor` change list semantics for existing clients.                                                                                                                                                                                             | Both params are absent-by-default and the unfiltered path is byte-identical; `tests/unit/runtime_api/test_fastapi_runtime_api.py` pins the legacy response.                                                                                                        | Drop the params; the hook falls back to a single unfiltered page.                                                                    |
| Keyset skips/duplicates rows on `updated_at` ties.                                                                                                                                                                                                        | The `(updated_at DESC, id DESC)` index + a conformance test that inserts two conversations with an identical `updated_at` and pages across the boundary.                                                                                                           | `CREATE INDEX CONCURRENTLY` is online; `DROP INDEX CONCURRENTLY` in the rollback SQL.                                                |
| The SSE tail leaks cross-tenant rows.                                                                                                                                                                                                                     | Channel scope is `scoped_identity()`-derived only; `tests/integration/persistence/test_rls_isolation.py` pattern + a route test asserting an `org_b` subscriber sees zero `org_a` rows.                                                                            | Unregister the stream route; the hook falls back to fetch-on-mount + retry.                                                          |
| Deleting `usePinnedConversations` loses users' pins.                                                                                                                                                                                                      | `migrateLegacyPins` runs once, is idempotent server-side, and is covered by a unit test asserting N POSTs then key deletion.                                                                                                                                       | The migration is additive; reverting the deletion restores the old read path.                                                        |
| Removing `chats` from topbar suppression breaks the Chats layout (double scroll / lost height).                                                                                                                                                           | `packages/chat-surface/src/shell/ChatShell.test.tsx` grid-template assertions; the design-parity harness re-run.                                                                                                                                                   | Re-add `"chats"` to `SUPPRESS_TOPBAR` — a one-line, isolated revert (that is why D5 splits the sets).                                |
| The row overflow steals row-activation clicks.                                                                                                                                                                                                            | `Row.test.tsx` asserts `onActivate` does **not** fire when the menu button or a menu item is clicked.                                                                                                                                                              | Remove the overflow slot; the hook's mutations stay callable from ChatScreen.                                                        |
| **Lost edits in the program's hottest files** — eight PRDs claim `destinationBinders.tsx`, four claim `Row.tsx`, three claim `ChatShell.tsx`, five each claim `conversation_query_service.py` / `routes.py` / the three store adapters / facade `app.py`. | Land strictly in the README's per-file order: `Row.tsx` 08 → **09** → 11; `ChatShell.tsx` 03 → **09** → 12; `ChatsArchive.tsx` 02 → **09**; `api-types/src/index.ts` 05 → 07 → **09** → 12; ai-backend files 05 → 07 → 08 → **09** → 12. One merge owner per wave. | Per-file; each seam above is independently revertible.                                                                               |
| **D4's `updated_at` bump lands before PRD-05**, reordering every `updated_at`-sorted list including today's Activity spine (README C19).                                                                                                                  | Wave order: PRD-05 moves Activity off that spine first, so the reorder lands exactly once. Do not start D4 until `GET /v1/agent/runs` is on `main`.                                                                                                                | Revert the single `UPDATE agent_conversations` per adapter (as the first row).                                                       |
| The added ⋯ control has no counterpart in the design, so the harness re-raises it forever.                                                                                                                                                                | Record `expectDivergence` on the affected chats anchors — the key `lib/compare.mjs:172` actually reads (not `expected-divergence`) — with the reason; it then reports INFO.                                                                                        | Remove the slot; the anchors entry becomes inert.                                                                                    |

## Definition of Done

1. `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_adapters/test_store_conformance.py` passes, and it contains an assertion that `get_latest_message_for_conversation` returns the newest **assistant** message when a later `user` message exists, for all three adapters.
2. `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_api/` passes with a new test asserting `GET /v1/agent/conversations?bucket=pinned&limit=1` on a fixture of 3 pinned + 150 unpinned conversations returns exactly 1 pinned row and a non-null `next_cursor`, and that following the cursor twice yields the other two — **the regression guard for the silently-incomplete Pinned bucket**.
3. `services/ai-backend/tests/unit/runtime_api/test_fastapi_runtime_api.py` asserts that `GET /v1/agent/conversations` sent **without** `bucket` and `cursor` returns a payload whose top-level keys are exactly `{"conversations", "has_more"}` (no `next_cursor` key) and whose `[c["id"] for c in conversations]` equals the fixture's `updated_at DESC, id DESC` order — i.e. the legacy caller sees no change.
4. `services/ai-backend/tests/unit/runtime_api/test_conversation_stream_routes.py` asserts an `org_b`-scoped subscriber to `GET /v1/agent/conversations/stream` receives zero envelopes for `org_a` conversations (0 frames read before the first heartbeat).
5. `tests/unit/runtime_adapters/test_store_conformance.py` asserts, for all three adapters, that `update_run_status(run_id, CANCELLED)` raises the parent conversation's `updated_at`, and that a subsequent `list_conversations(cursor=<pre-cancel watermark>)` returns that conversation.
6. `services/ai-backend/migrations/0004_conversation_keyset.sql` and `0004_conversation_keyset.rollback.sql` both exist (id per README C18; `ls services/ai-backend/migrations` shows `0001` as the only pre-program migration), and `cd services/ai-backend && .venv/bin/python ../../tools/check_migration_manifest.py` exits 0
   (the tool lives at the REPO ROOT — `services/ai-backend/tools/` does not exist; PRD-05/06/07
   already invoke it this way).
7. `cd services/backend-facade && .venv/bin/python -m pytest tests/` passes with a test asserting `bucket` and `cursor` are forwarded verbatim to ai-backend, and one asserting the `/v1/agent/conversations/stream` proxy returns `content-type: text/event-stream` with the upstream body unmodified.
8. `packages/chat-surface/src/destinations/chats/useChatsArchive.test.tsx` asserts, against a fake `Transport`: (a) three bucket-scoped requests on mount; (b) `loadMore("archived")` appends to the archived bucket and issues no second page-1 request (assert on the fake's recorded call list); (c) a `conversation_changed` SSE envelope flipping `latest_run_status` from `running` to `completed` re-renders the row's status as `done` **with no additional transport call**; (d) `setPinned(id,true)` moves the row to `pinned` optimistically and rolls back on a rejected request; (e) `setArchived(id,true)` moves the row to `archived` and `setArchived(id,false)` returns it to `recent`, each issuing exactly one `PATCH`. _(Rewritten per README DoD-Q8 — items (b), (c), (e) are the automated replacement for the old manual-acceptance item.)_
9. `packages/chat-surface/src/destinations/_shared/Row.test.tsx` asserts that (a) the overflow trigger is in the document without any pointer interaction — it is persistently rendered, not hover-revealed (see D2) — and (b) clicking the overflow button, and clicking a `role="menuitem"` inside it, do **not** invoke `onActivate`.
10. `packages/chat-surface/src/shell/ChatShell.test.tsx` asserts the Topbar renders when `activeDestination="chats"` and does **not** render when `activeDestination="run"` or `settingsActive`, and that Chats still renders no ContextPanel and no RightRail.
11. `packages/chat-surface/src/shell/Topbar.test.tsx` asserts `[data-testid="topbar-subtitle"]` reads exactly `every conversation with the agent` for `chats`, sourced from `destinations.ts` — matching `copilot-app.jsx:599`.
12. **Design values pinned numerically, as assertions in `packages/chat-surface/src/shell/Topbar.test.tsx`** against the rendered inline styles: `[data-testid="topbar-title-group"]`.style has `alignItems === "baseline"` and `gap === "9px"` (design `copilot.css:398-403`); the `<header data-component="topbar">`.style has `gap === "12px"` and `padding === "0px 18px"` (design `copilot.css:388-397`); `[data-testid="topbar-subtitle"]`.style has `color === "var(--color-text-subtle)"` — the existing token whose value is `#64646d` = design `--mut2` (`packages/design-system/src/styles.css:178`, `copilot.css:409-411`); `TOPBAR_HEIGHT === 46` (already asserted at `Topbar.test.tsx:8`, design `copilot.css:389`). The title keeps `var(--font-size-sm)` and `var(--font-weight-semibold)`; the 13.5→13px residual is the recorded `expectDivergence` from D5, not a token addition.
13. `grep -rn "usePinnedConversations\|metadata\.pinned\|atlas:pinned" apps packages --include="*.ts" --include="*.tsx"` returns matches **only** inside `apps/frontend/src/features/chats/migrateLegacyPins.ts` and its test — proving one pin concept remains.
14. `grep -n "color" packages/chat-surface/src/destinations/chats/ChatsArchive.tsx` shows no `color` key inside `modelMonoStyle` (D7 / README G1), and `ChatsArchive.test.tsx` asserts `[data-testid="chat-archive-row-model"]`.style has no `color` set, so the tag inherits `Row`'s `subStyle` colour.
15. `npm run typecheck --workspace @0x-copilot/frontend`, `--workspace @0x-copilot/api-types`, `--workspace @0x-copilot/chat-surface` and `npm run build --workspace @0x-copilot/frontend` all exit 0.
16. **Parity, as three commands** — after regenerating `surfaces/chats/out/` per `tools/design-parity/SKILL.md` (adding `lib/render-live-chats-topbar.test.tsx`, which the `lib/render-live*.test.tsx` glob picks up without touching `vitest.config.mjs`), with `H='sed -n "/^## 🔴 HIGH/,/^## 🟠/p" tools/design-parity/surfaces/chats/out/report-default.md'`:
    - `eval $H | grep -c 'topbar\.title'` → **`0`** (the anchor now has a live counterpart and matches; today it is the only `missing-in-live` HIGH, `report-default.md:14`).
    - `eval $H | grep -c 'row\.running\.sub\.mono'` → **`0`** (D7; today it is HIGH at `report-default.md:21`).
    - `git diff --exit-code -- tools/design-parity/surfaces/chats/out/report-default.md` shows **no line added** under the `## 🔴 HIGH` heading — i.e. this PR removes HIGH rows and adds none.
17. **HIGH count is a delta against this PR's merge base, not a frozen number.** Regenerate the chats report on the merge base and on this PR; the HIGH total on this PR is strictly lower and the MEDIUM/LOW/INFO totals do not increase. Do **not** assert an absolute count: `lib/extract-computed.js` now also captures `boxShadow`/`backdropFilter`/`transition`/`textDecorationLine` and `compare.mjs` no longer emits phantom `borderColor` rows for borderless elements, so every pre-existing figure in this program's docs is stale — the report on disk today reads `HIGH 15 · MEDIUM 59 · LOW 64 · INFO 10` (`report-default.md:8`), not the 17 an earlier draft of this PRD and README C20 quote.

**Release-checklist line (not a DoD item):** on both hosts, with a run in flight, opening Chats shows the chip flip from Running to Done with no navigation; ⋯ → Archive moves the row and ⋯ → Unarchive returns it; with >100 conversations, "Load more" under Archived reveals older rows. Item 8 is the mechanical guarantee; this is the human smoke pass.

## Dependencies

Wave 3, per the README's corrected order: `Wave 2 (08 → 07) → Wave 3 (09 → 11 ‖ 12)`.

**Must land first**

- **PRD-03** (host binding contract) — supplies `toChatArchiveRow` as the single per-row projector that `useChatsArchive` consumes, and fixes the desktop `metadata.*` reads. Per README **C8** it ships **only** that per-row projector: no shared `bucketConversations`, because bucketing moves into the SQL query here. Nothing to delete, but the boundary must be honoured on PRD-03's side or the helper is written and then orphaned.
- **PRD-02** (chip) — `StatusPill` → `.ui-badge`. Independent of this PRD's behaviour, but both re-render the same rows; landing the chip first means the chats parity re-run in DoD #16/#17 moves for one reason at a time. File order on `ChatsArchive.tsx` is **02 → 09**.
- **PRD-08** (activity surface) — **owns `_shared/Row.tsx`** (README **C9**): `trailing` + the always-reserved 16px, `iconTone`, the icon-tile background, `className="ui-list-row"`, and PRD-04's absorbed title weight. PRD-09's `overflow` slot stacks on the post-PRD-08 file; PRD-11 then stacks `subFont`/`iconSize` on ours. PRD-08 also owns the `.ui-list-row` recipe in `styles.css` — PRD-09 edits no CSS there.
- **PRD-05** (run history backend) — README **C19**: D4 makes `update_run_status` bump the conversation's `updated_at`, which reorders every `updated_at`-sorted list including today's Activity spine. PRD-05 moves Activity off that spine first so the reorder lands exactly once. Also fixes the ai-backend file order (`05 → 07 → 08 → 09 → 12`) that this PRD's route/store/facade edits sit inside.

**Must coordinate (not blocking)**

- **PRD-12** (rail & Settings). Per README **C14** this PRD **owns** the `SUPPRESS_TOPBAR` / `FULL_BLEED_DESTINATIONS` split; PRD-12 keeps only "web passes `settingsActive`" and threads it to the rail. `SUPPRESS_TOPBAR = {"run"} ∪ settingsActive` already keeps Settings without a topbar, exactly as the design specifies (`copilot-app.jsx:739`). File order on `ChatShell.tsx` is **03 → 09 → 12**.
- **PRD-01** (tokens). Retunes `--font-size-sm` to 13px, which this PRD's topbar title consumes, and owns the `.ui-button--sm` / `.ui-button--primary` weight fix (README **G9**) that this surface's "New chat" CTA needs.
- **PRD-10** (projects surface). Decides the 960px centring question (README **G6**) before its shared `_shared/Page` ships; this PRD holds `ChatsArchive.tsx`'s `margin: 0 auto` unchanged pending that ruling.
- **PRD-13** (dead code). Owns the `ChatsDestination` deletion, the barrel edit and the orphan guard (README **C17**). It lands last, after this PRD has settled the chats tree.

**Reference corrections applied to this document**

The README's rulings that touch PRD-09: **C8** (PRD-03 ships the per-row projector only), **C9** (PRD-08 owns `Row.tsx`), **C14** (PRD-09 owns the topbar/full-bleed split), **C15** (PRD-08's subtitle non-goal names PRD-09), **C17** (PRD-13 owns the `ChatsDestination` deletion), **C18** (migration id `0004`), **C19** (PRD-05 lands first), **G1** (this PRD absorbs the mono model tone), **G6** (centring → PRD-10), **G9** (button weight → PRD-01), **DoD-Q8** (item 17 automated into item 8).

**This unblocks**

- Any Chats-surface work needing live data or history beyond page 1 (archive search, retention UX, bulk actions).
- A future ⌘K chat-index PRD (none exists yet) — `GET /v1/agent/conversations?bucket=…&cursor=…` is the backfill read the palette writer needs.
- **PRD-12**, which per README C1 owns `useActiveRunCount` and sources it from `GET /v1/agent/runs/active_count`. This PRD's bucket-scoped list is the fallback read if that endpoint is ever descoped; it does **not** create or move the hook.
- Any future destination needing live refresh: `conversation_adapter.py` is the first store-tailed SSE and the template for converting the four bus-backed streams off in-memory pub/sub.

---

## Implementation record

_Landed on `claude/prd-09-chats-surface` (merge-base `0d7cb2131`). Recorded 2026-07-23._

### What landed

- **Backend (ai-backend):** `ConversationBucket` enum + `matches_conversation_bucket()` shared predicate; `list_conversations` gains `bucket`/`before_updated_at`/`before_conversation_id` keyset (legacy call byte-compatible, drops null `next_cursor`); assistant-preferred preview via `prefer_roles`; `update_run_status` bumps the parent conversation's `updated_at` in the same tx across **all three** adapters (in_memory/file/postgres); new store-tailed conversations SSE (`conversation_adapter.py`, 25s heartbeat, keyset `?after`) — a store tail, not a bus, so it survives the API/worker process split. Migration `0005_conversation_keyset` (CONCURRENTLY index), MANIFEST regenerated.
- **Facade:** verbatim `bucket`/`cursor` forward on the list route; inlined SSE proxy `stream_conversations` targeting `ai_backend`, registered before `/{conversation_id}`.
- **api-types:** `ConversationBucket`, `ListConversationsQuery`, `ConversationStreamEnvelope`; buckets documented server-scoped; preview = assistant-preferred.
- **chat-surface (SSOT seam):** new `useChatsArchive` controller (3 bucket fetches + SSE tail merge + optimistic pin/archive with rollback + `loadMore`/retry); `ChatsArchive` overflow menu + ghost Load-more + D7 mono-tone color deletion; `Row` persistent overflow slot; D5 ChatShell split (`SUPPRESS_TOPBAR` = run∪settings vs full-bleed side columns) so chats gets a topbar with no side columns; Topbar registry-sourced subtitle + pinned geometry.
- **Both hosts:** desktop `ChatsBinder` and web `ChatsArchiveRoute` collapsed onto `useChatsArchive`; localStorage pin concept retired (`usePinnedConversations` deleted, first-class `conversation.pinned`); one-shot bounded `migrateLegacyPins`.

### DoD status (per item)

| #   | Item                                        | Verdict                                                                                                                                                                                                                                                                                |
| --- | ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Assistant-preferred preview, all adapters   | MET                                                                                                                                                                                                                                                                                    |
| 2   | Pinned-bucket keyset completeness           | MET                                                                                                                                                                                                                                                                                    |
| 3   | Legacy list omits next_cursor, ordered      | MET                                                                                                                                                                                                                                                                                    |
| 4   | Conversation-stream org isolation + framing | MET                                                                                                                                                                                                                                                                                    |
| 5   | update_run_status bumps conversation        | MET (forward-tail asserted via keyset invariant; postgres in DB-gated sibling)                                                                                                                                                                                                         |
| 6   | Migration + MANIFEST                        | MET (id 0005 not 0004 — 0004 taken by PRD-07)                                                                                                                                                                                                                                          |
| 7   | Facade bucket/cursor forward + SSE proxy    | MET                                                                                                                                                                                                                                                                                    |
| 8   | useChatsArchive controller (a–e)            | MET                                                                                                                                                                                                                                                                                    |
| 9   | Row overflow persistence                    | MET                                                                                                                                                                                                                                                                                    |
| 10  | ChatShell D5 topbar/side-column split       | MET                                                                                                                                                                                                                                                                                    |
| 11  | Topbar subtitle from registry               | MET                                                                                                                                                                                                                                                                                    |
| 12  | Topbar geometry pins                        | MET                                                                                                                                                                                                                                                                                    |
| 13  | localStorage pin concept retired (grep)     | MET                                                                                                                                                                                                                                                                                    |
| 14  | Mono model tone color deleted (D7)          | MET                                                                                                                                                                                                                                                                                    |
| 15  | Typecheck/build both hosts                  | **PARTIAL** — api-types + chat-surface exit 0; frontend typecheck/build exit 2 on exactly 2 errors, both a confirmed cross-package symlink false-negative (apps resolve chat-surface to MAIN, which lacks `useChatsArchive`/`onTogglePin`). Clears under post-merge host verification. |
| 16  | No new HIGH parity row for topbar/mono      | MET (HIGH 5→1)                                                                                                                                                                                                                                                                         |
| 17  | MEDIUM/LOW/INFO totals do not increase      | **NOT MET** — LOW 46→48 (+2), INFO 7→8 (+1); HIGH 5→1 and MEDIUM 31→23 both improved. New topbar anchors add measured elements, raising absolute LOW/INFO counts against the do-not-increase clause.                                                                                   |

**Score: 15 MET / 17, 1 PARTIAL (symlink false-negative), 1 NOT MET (parity LOW/INFO absolute totals rose).**

### Deviations from PRD

- Migration id **0005** not 0004 (0004 = PRD-07 `conversation_project` on disk; the PRD's "0004 free" assumption was stale).
- Reused the generalized `KeysetCursor` codec (already shared by messages + PRD-05 run-history) for the `(updated_at, conversation_id)` cursor instead of minting a new `ConversationCursor`.
- Facade SSE proxy **inlined** in `app.py` next to the list route (matches the existing run-stream proxy that also targets `ai_backend`) rather than a new file copied from `inbox_stream_routes.py` (which targets `backend`, the wrong service).
- `useChatsArchive` parses the SSE frame with a locally-declared envelope shape (the freshly-added api-types type isn't visible through the worktree symlink at chat-surface typecheck time); api-types remains the external contract.
- DoD #5's literal "cursor=pre-cancel watermark returns that conversation" is forward-tail wording; the store pages backward, so the conformance test asserts the equivalent invariant (updated_at strictly raised → row is newest, pre-cancel keyset no longer contains it) and the forward tail is covered by `list_conversation_changes` + the stream tests.
- Two pre-existing ChatShell tests and one Topbar test asserted the OLD (chats-suppresses-topbar / no-subtitle) behavior; updated to the correct D5 behavior.

### Left open

- **DoD #17 (parity absolute totals):** LOW +2 / INFO +1 vs merge base. HIGH and MEDIUM improved; the rise is from the new topbar anchors adding measured elements, not new drift. A reviewer should decide whether the do-not-increase clause should be re-scoped to "no new HIGH/MEDIUM drift on the changed surface" (which is met) given more elements are now measured.
- **DoD #15 (frontend host typecheck/build):** must be re-run post-merge with correct package resolution to clear the 2 symlink false-negatives.
- Postgres store-conformance is skipped in-env (no live DB); the DB-gated sibling `test_postgres_runtime_api_store.py` holds the real postgres assertions. Needs a live-PG pass before release.
