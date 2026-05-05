# PR 2.2 — Sidebar enhancements + User card + Workspace switcher + Keymap

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 2, PR 2.2 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** frontend (sidebar + keymap) · auth (workspace switch reads existing `AuthContext`) · backend-facade (one read endpoint to list workspaces if not yet present)
> **Size:** **M.** Mostly FE composition; one new tiny dependency (`tinykeys`) for chord parsing; one optional small read endpoint (`GET /v1/me/workspaces`) reusing existing tables.
> **Reads alongside:** [`pr-2.1-topbar-chrome-thinking-depth.md`](pr-2.1-topbar-chrome-thinking-depth.md), [`pr-1.6-workspace-defaults-conversation-lifecycle.md`](pr-1.6-workspace-defaults-conversation-lifecycle.md), [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md), [`packages/design-system/CLAUDE.md`](../../packages/design-system/CLAUDE.md)
> **Sibling docs (Wave 2):** PR 2.1 — topbar chrome · PR 2.3 — welcome state + thread polish

---

## 0 · TL;DR

Today's sidebar (`apps/frontend/src/features/chat/components/thread/AssistantThreadList.tsx`) is a flat list of conversations rendered through `ThreadListPrimitive`, with a "New thread" button at the top, a refresh icon, and a single "Settings" button at the footer. The Atlas design wants:

- a **brand mark** + **collapse toggle** at the top,
- a **New chat** button with `⌘N`,
- a **chat search** input with `⌘K`,
- the chat list **grouped by Today / Yesterday / Earlier**,
- a **live "pulse"** badge on the active-run thread,
- a **user card** at the bottom that opens a popover with **workspace switch / settings / sign out**,
- and four global keyboard shortcuts (`⌘N`, `⌘K`, `⌘\`, `⌘↩`).

Almost every piece of state needed is **already in `ChatScreen.tsx`** (the `Conversation[]`, `activeRunId`, `identity`, `pendingActionRunId`). The only persistence touch in this PR is reusing the read endpoint added in PR 1.6 (which already returns `folder`, `deleted_at`, `parent_conversation_id`). Search, grouping, and the active-run pulse are pure presentation projections of state we already hold.

The keymap layer uses [`tinykeys`](https://github.com/jamiebuilds/tinykeys) (≈ 400 bytes minified, zero deps) to parse and bind chords. We **considered and rejected** `cmdk`, `kbar`, `react-hotkeys-hook`, and `mousetrap` — the rationale is in §3.4.

LoC estimate: FE ≈ 380 (sidebar 180, keymap 60, UserCard 90, WorkspacePicker 50) · 1 dep · facade ≈ 25 (one new GET) · backend ≈ 0 (reuses existing `organization_members` join) · tests ≈ 260.

---

## 1 · PRD

### 1.1 Problem

Today's sidebar:

- `AssistantThreadList.tsx` renders a header with `<LogoMark />` + a refresh icon, a list of `<ThreadListItemPrimitive.Trigger>` items, and a footer with a "Settings" button. It uses `@assistant-ui/react`'s `ThreadListPrimitive` to iterate `threads` from the runtime — passes `disabled={activeRunId !== null}` to disable thread switching while a run is active.
- The conversation list is provided by `ChatScreen.threadListAdapter` (an `ExternalStoreThreadListAdapter`) which today reads `conversations: Conversation[]` from `listConversations(identity)` and splits into `regular` vs. `archived` arrays. There is no grouping by day, no folder grouping, no search, no "live" badge.
- The footer has a single "Settings" button. There is no user identity pill, no workspace switcher, no sign-out affordance.
- There are **no global keyboard shortcuts**. ⌘N, ⌘K, ⌘\, and ⌘↩ are the four design-doc-mandated bindings; only the inline `⌘ Enter` in the assistant-ui composer responds today.

The design doc ([`Design Doc.html` § Sidebar / § Chrome behavior](../../../tmp/design-doc/enterprise-search/project/Design%20Doc.html)) requires:

- "Brand mark top-left + sidebar collapse toggle top-right." → already partially there (logo top-left, no top-right toggle in sidebar).
- "New chat button — keyboard ⌘N." → exists but no shortcut binding.
- "Search chats input — filters by title." → missing.
- "Chat list grouped by Today / Yesterday / Earlier." → missing.
- "Each item has title, preview, timestamp, optional badge ('live' pulse for the running thread)." → missing.
- "User card at the bottom — avatar, name, workspace · role, chevron to a popover with workspace-switch / settings / sign out." → missing.
- "Keyboard: ⌘K chat search, ⌘N new chat, ⌘\ toggle sidebar, ⌘↩ approve when an approval card is focused." → missing.

### 1.2 Goals

1. **Group + search + live-pulse render from existing state.** No new fetch, no new endpoint for the sidebar core. Pure functions of `Conversation[]`, `activeRunId`, and an in-memory query string.
2. **One small fetch only when the user opens the workspace picker** — `GET /v1/me/workspaces`. If the user has only one workspace, the picker collapses to a static label.
3. **Keymap is global, declarative, and one place.** A single `useKeymap()` hook registers chord → handler bindings; nothing rolls its own `keydown` listeners.
4. **⌘↩ knows which approval card to approve** without coupling to specific component internals — via a tiny `ApprovalFocusContext` (Set of approval IDs known to be currently visible + focusable) updated by the `ApprovalTool` (PR 1.4).
5. **No churn into the design system** — `IconButton`, `Menu`, `Badge`, `AppIcon`, `Card`, `TextInput` already exist there. PR 2.2 ships only feature components.
6. **Streaming impact is none.** Live-pulse derives from `activeRunId === conversation.runId`; we do not subscribe to a new event family. The existing `run_started/completed/failed/cancelled` events already drive `activeRunId` transitions.

### 1.3 Non-goals

- **Drag-reorder, pin, multi-select** — design's "later" pills (P1/P2). Out of v1.
- **Server-side cursor pagination of the chat list** — `listConversations` returns the user's full list today; PR 1.6 added `?include_deleted` and `folder`/`deleted_at` columns. Server-side groupings are explicitly **rejected** (browser `Intl.DateTimeFormat` does this in 12 lines — see PR 1.6 §3.5). The day-bucket reducer is FE-only.
- **Folder rename / drag-into-folder UI** — folders are personal labels; rename is a user editing the property of an individual chat (PR 1.6 PATCH endpoint). Bulk-move is P1.
- **Sign-out flow rewrite** — UserCard's "Sign out" dispatches the same auth signal as today's `auth.signOut()` (already in `AuthContext`). No new endpoint.
- **Workspace creation / invite** — UserCard's workspace switcher lists workspaces; admin actions live in Settings → Workspace (PR 4.2).
- **A command palette** (cmdk-style). The design's keymap is four bindings, not a palette. We're disciplined about scope.

### 1.4 Success criteria

- ✅ Sidebar renders a search input, a grouped list, a live-pulse on the active-run thread, and a user card at the bottom.
- ✅ `⌘N` opens a fresh chat (calls existing `onStartNewChat` from `ChatScreen.tsx`); `⌘K` focuses the search input; `⌘\` toggles `sidebarCollapsed` (existing state in `ChatScreen.tsx`); `⌘↩` clicks the primary button on the **focused** in-thread approval card if any, else no-ops with a polite toast.
- ✅ Sidebar correctly groups by `Today / Yesterday / Earlier` against the user's local timezone — verified with timezone-shifted test fixtures.
- ✅ When the user opens UserCard popover → "Switch workspace", a list of the user's workspaces appears, with member counts and last-active timestamps; clicking a row drives the existing `auth.switchWorkspace(orgId)` flow (or, if absent today, falls back to a hard nav with `?workspace=…`).
- ✅ The pulse badge appears on exactly one row at any time — the conversation that owns the active run; it disappears within 200 ms of `run_completed/cancelled/failed`.
- ✅ Search filters case-insensitively against `Conversation.title` and `Conversation.folder`. Empty query restores full list. Filter state is in-memory (lost on page reload).
- ✅ Sidebar auto-collapses below 820 px viewport (already wired); the `⌘\` shortcut still works at any width.
- ✅ All four shortcuts respect a global "input is focused" guard — they never fire while the user is typing in `<input>`/`<textarea>`/`[contenteditable]`, except for `⌘K` (which **wants** to land focus in `<input>`).
- ✅ No new `runtime_event` type, no SSE handshake change.

### 1.5 User stories

| As…              | I want…                                                                  | So that…                                                                                     |
| ---------------- | ------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| Sarah (end user) | to press ⌘K, type "launch", and see only Q1-launch threads               | three weeks of chat noise stays out of the way                                               |
| Sarah            | to see at a glance which thread is "live" right now                      | switching tabs and coming back, I don't lose the run I started                               |
| Sarah            | to press ⌘↩ when an approval card is on screen and have it approve       | I'm not paginating my mouse to the card during a long stream                                 |
| Sarah            | to switch workspaces from the user card                                  | I move between Acme (work) and a personal Atlas tenant without re-logging-in                 |
| Marcus (admin)   | the user card to show "Workspace · Role"                                 | when I'm on a screen-share, others see I'm in admin context                                  |
| Sarah            | the chat list grouped by Today / Yesterday / Earlier in **my** timezone  | the same conversation doesn't bounce between groups when I open it from a different timezone |
| Future-Wave-3    | the sidebar to consume `runUiState` rather than computing its own status | live-pulse stays consistent with topbar status across surfaces                               |

---

## 2 · Spec

### 2.1 Sidebar layout

```
┌─────────────────────────┐
│ ◧ Atlas       [⤺]        │  brand mark + collapse toggle (top-right)
│                          │
│ ┌──────────────────────┐ │
│ │ ➕ New chat   ⌘N      │ │  reuse existing onStartNewChat
│ └──────────────────────┘ │
│ ┌──────────────────────┐ │
│ │ 🔍 Search…    ⌘K      │ │  TextInput, ref'd by useKeymap('$mod+K')
│ └──────────────────────┘ │
│                          │
│  Today                   │
│  ● FY26 Q1 launch …      │   ← pulse dot when activeRunId matches
│    Drafted in Slack…     │
│    11:42                 │
│  ○ Positioning review …  │
│    Aurora platform        │
│    11:42                 │
│                          │
│  Yesterday               │
│    Pull Q4 close numbers │
│    Yesterday              │
│                          │
│  Earlier                 │
│    Update partner deck    │
│    Mon                    │
│   …                      │
│                          │
│ ┌──────────────────────┐ │
│ │ 🟠 Sarah Chen   ▼     │ │  user card (popover trigger)
│ │    Acme · Admin      │ │
│ └──────────────────────┘ │
└─────────────────────────┘
```

UserCard popover (anchored above):

```
┌────────────────────────┐
│ Sarah Chen             │
│ sarah.chen@acme.com    │
├────────────────────────┤
│ Workspaces             │
│  ✓ Acme (admin · 47)   │
│    Personal (owner · 1)│
│    +  Add workspace    │  ← gated; opens a "join existing" modal in PR 4.2
├────────────────────────┤
│  ⚙  Settings        ⌘, │
│  ⏏  Sign out           │
└────────────────────────┘
```

### 2.2 Modules — what we add, what we reuse

| Module                                                                          | New / Modified | Owns                                                                                                                                 |
| ------------------------------------------------------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `apps/frontend/src/features/chat/components/sidebar/Sidebar.tsx`                | **new**        | Layout shell. Replaces the body of `AssistantThreadList.tsx`. Owns no fetches.                                                       |
| `apps/frontend/src/features/chat/components/sidebar/SidebarSearch.tsx`          | **new**        | `TextInput` ref'd by the keymap, controlled value, `aria-controls={listId}`.                                                         |
| `apps/frontend/src/features/chat/components/sidebar/ConversationListGroups.tsx` | **new**        | Renders day-grouped `<section>`s with `<h3>` labels. Iterates the result of `groupConversations`.                                    |
| `apps/frontend/src/features/chat/components/sidebar/ConversationRow.tsx`        | **new**        | One row: title, snippet, timestamp, optional pulse badge.                                                                            |
| `apps/frontend/src/features/chat/components/sidebar/UserCard.tsx`               | **new**        | The bottom card + popover. Reads `useAuth()`. Opens `WorkspacePicker`.                                                               |
| `apps/frontend/src/features/chat/components/sidebar/WorkspacePicker.tsx`        | **new**        | Read-only list, click → switch. Lazy-fetches `GET /v1/me/workspaces` on first open.                                                  |
| `apps/frontend/src/features/chat/utils/groupConversations.ts`                   | **new**        | Pure reducer: `(rows, now) → DayGroup[]`. Uses `Intl.DateTimeFormat`. Exact module signature already proposed in PR 1.6 §3.5.        |
| `apps/frontend/src/features/chat/utils/filterConversations.ts`                  | **new**        | Pure: case-insensitive over `title`, `folder`. Returns same `Conversation[]` shape.                                                  |
| `apps/frontend/src/app/keymap.ts`                                               | **new**        | One module exports `useKeymap()`. Wraps `tinykeys` with input-focus guard.                                                           |
| `apps/frontend/src/features/chat/approval/ApprovalFocusContext.tsx`             | **new**        | `Set<approvalId>` of currently rendered+focusable approval cards. Provides `register/unregister`.                                    |
| `apps/frontend/src/features/chat/components/thread/AssistantThreadList.tsx`     | **modified**   | Body removed; render `<Sidebar />`. The component stays as the entry point so import sites don't move.                               |
| `apps/frontend/src/features/chat/ChatScreen.tsx`                                | **modified**   | One new piece of state (`searchQuery`); existing `sidebarCollapsed`/`onStartNewChat` are reused. Mounts `<KeymapProvider>` once.     |
| `apps/frontend/src/features/auth/AuthContext.tsx`                               | **modified**   | If `switchWorkspace` doesn't exist yet, add a thin wrapper. Reuses session API.                                                      |
| `apps/frontend/src/api/meApi.ts`                                                | **new**        | `listMyWorkspaces(identity): Promise<{ workspaces: Workspace[] }>`. Calls facade.                                                    |
| `services/backend-facade/src/backend_facade/routes/me.py`                       | **modified**   | Add `GET /v1/me/workspaces` proxy. Forwards to backend's existing membership endpoint.                                               |
| `services/backend/src/backend_app/routes/me.py`                                 | **modified**   | Add `GET /internal/v1/me/workspaces` reading `organization_members ⨝ organizations`. Already-existing helper used; no schema change. |
| `packages/api-types/src/index.ts`                                               | **modified**   | Export `Workspace`, `WorkspaceListResponse`. (Or reuse existing `Organization` shape if present.)                                    |

Existing files we **delete** — none.

### 2.3 Wire — workspace listing

```http
GET /v1/me/workspaces
→ 200
{
  "workspaces": [
    {
      "org_id": "org_acme",
      "display_name": "Acme",
      "slug": "acme",
      "role": "admin",
      "member_count": 47,
      "last_active_at": "2026-05-05T15:51:02.110Z",
      "is_current": true
    },
    {
      "org_id": "org_personal",
      "display_name": "Personal",
      "slug": "personal",
      "role": "owner",
      "member_count": 1,
      "last_active_at": "2026-05-04T08:14:00.000Z",
      "is_current": false
    }
  ]
}
```

- `role` is the user's role _in that workspace_; `member_count` is fetched via the existing `organization_members` count helper (no new SQL); `last_active_at` is `MAX(login_attempts.created_at)` for the user×org pair (already indexed).
- 200 on success even if the user has only one workspace (we still render the picker — disabled).
- 401 on missing identity. RLS already enforces tenant isolation on `organization_members`.

The endpoint is a thin read-through; **no migration**, no new audit event (it's a read).

### 2.4 Keymap — implementation

The keymap is a single module (`apps/frontend/src/app/keymap.ts`) that:

1. Imports [`tinykeys`](https://www.npmjs.com/package/tinykeys) (~400 B min, zero deps, MIT — last published 2024-04, 8M weekly downloads).
2. Exposes one hook:
   ```ts
   useKeymap({
     "$mod+N": () => onNewChat(),
     "$mod+K": () => searchInputRef.current?.focus(),
     "$mod+\\": () => setSidebarCollapsed((c) => !c),
     "$mod+Enter": () => approveFocusedApproval(),
   });
   ```
3. Wraps `tinykeys(window, bindings)` inside a `useEffect` that registers/unregisters; `$mod` is `tinykeys`'s built-in cross-platform alias for `Cmd` (macOS) / `Ctrl` (others).
4. Wraps each handler with a focus guard:
   ```ts
   function isTypingTarget(t: EventTarget | null): boolean {
     if (!(t instanceof HTMLElement)) return false;
     return (
       t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable
     );
   }
   ```
   `⌘K` opts out of the guard (it wants to **land** focus in an input). All others bail when typing.
5. Gates against the runtime — a registered binding is a no-op if the matching state is invalid (e.g. ⌘↩ with zero registered approvals → polite `aria-live` "No approval to confirm").

### 2.5 Approval-focus contract

`⌘↩` needs to know which approval to approve. Coupling that to component internals is brittle. The clean shape is a small context:

```ts
// apps/frontend/src/features/chat/approval/ApprovalFocusContext.tsx
interface RegisteredApproval {
  approvalId: string;
  approve: () => void; // calls onApprove from PR 1.4 ApprovalTool
}
const ctx = createContext<{
  register: (a: RegisteredApproval) => void;
  unregister: (approvalId: string) => void;
  approveTopmost: () => boolean; // returns true if it approved something
}>(...);

export function useApprovalFocus(): ApprovalFocusApi { … }
```

`ApprovalTool` (the existing component from PR 1.4) `useEffect`-registers when it mounts in unresolved state and unregisters on cleanup. The context keeps registrations in **insertion order** (a `Map`) and `approveTopmost()` invokes the _bottom-most_ (last-registered, scrolled-into-view) entry's `approve()`.

This avoids re-implementing focus / scroll-into-view logic. It also keeps `⌘↩` working when the user is **scrolled away** from the approval — they want a single keystroke to consent without scrolling first.

### 2.6 Streaming impact — explicitly **none**

| Subsystem                       | Touched?                                                                                   |
| ------------------------------- | ------------------------------------------------------------------------------------------ |
| `runtime_events` schema         | **No.**                                                                                    |
| SSE handshake                   | **No.**                                                                                    |
| `runtime_worker`                | **No.**                                                                                    |
| `chatModel/eventReducer.ts`     | **No.** Live-pulse uses existing `activeRunId` (already maintained in `ChatScreen` state). |
| Capabilities middleware / tools | **No.**                                                                                    |
| Audit chain                     | **No.**                                                                                    |
| ai-backend persistence          | **No.** Sidebar reads existing `Conversation` rows.                                        |

The single, additive backend touch is `GET /internal/v1/me/workspaces` — a read-through of `organization_members` and `organizations`. No write, no event, no migration.

### 2.7 Permissions

| Caller                                         | Action                                                                                                               |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Authenticated workspace member                 | sees own conversations (existing); can search; can switch to any workspace they're a member of (RLS-enforced).       |
| Authenticated, single workspace                | UserCard shows the picker disabled with "Only one workspace".                                                        |
| Authenticated viewer of a shared chat (PR 6.1) | sidebar shows "Shared with you" group instead of full list (PR 6.1 territory; this PR keeps the read path the same). |

⌘↩ is gated by the **owning user** of the approval (existing PR 1.4 contract). A non-actor pressing ⌘↩ on a forwarded approval gets the same 403 the click path does — the keymap is just a different trigger.

### 2.8 Error semantics

| Condition                                                           | Behavior                                                                                                                        |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `GET /v1/me/workspaces` returns 5xx                                 | UserCard popover shows "Couldn't load workspaces — retry"; "Switch workspace" link disabled.                                    |
| `auth.switchWorkspace(orgId)` throws                                | Toast in the existing notification surface with the error; user stays in the current workspace.                                 |
| `tinykeys` mounts before `<AuthGate>` resolves                      | Bindings register against `window`; handlers no-op if `auth.status !== 'authenticated'`.                                        |
| Search input blurred mid-type                                       | Query preserved across blur/focus; cleared explicitly by `Escape` while focused.                                                |
| Active-run thread soft-deleted by the user (PR 1.6 cancels its run) | Live-pulse ends as the run terminates; row disappears on next list refetch (filtered by `deleted_at IS NULL`).                  |
| `⌘↩` pressed with multiple approvals visible                        | `approveTopmost` picks the bottom-most registered (last-rendered) approval — that's the one closest to the composer.            |
| `⌘N` pressed while a run is active                                  | Existing `onStartNewChat` already preserves the run ("this run will be left in its current state" tooltip). No change.          |
| `⌘K` pressed while sidebar is collapsed                             | Side effect: `setSidebarCollapsed(false)` then focuses the input. One keystroke, two effects.                                   |
| `⌘\` pressed while focus is in a textarea                           | Focus guard fires; binding ignored. (Conscious tradeoff: chord conflicts are possible; we err toward not stealing input focus.) |

### 2.9 Accessibility

- Sidebar root is `<aside aria-label="Conversation history">` (today's value preserved).
- Search input has `aria-controls={listId}` and `aria-describedby` for the count-hint ("12 chats matching launch").
- Each conversation row is a `<button>` with `aria-current="true"` on the active conversation.
- The pulse badge announces to screen readers via `<span class="sr-only">live</span>` co-located with the visual dot.
- UserCard popover is a `Menu` (already accessible) with a `<dialog>`-style focus trap when open. We _do not_ mount `focus-trap-react`; the `Menu` primitive's existing `Escape` and outside-click handling are sufficient because the popover doesn't contain form fields beyond the workspace list.
- ⌘↩ triggers an `aria-live` announcement on success/failure ("Approved 'Send to #announcements'").

### 2.10 What we do NOT add

- **No `cmdk` / `kbar`**. They're command-palette libraries; we have four bindings and a single search input. Adding ~60 KB and a context layer for that would be exactly the kind of premature abstraction the user flagged.
- **No `react-hotkeys-hook`**. It's a thin React wrapper around manual `keydown`. `tinykeys` is smaller and platform-aware (`$mod` alias).
- **No `mousetrap`**. Unmaintained since 2018; doesn't handle modern composition events well.
- **No `focus-trap-react`**. Our `Menu` primitive handles dismissal; no nested focus-trap needed.
- **No `@floating-ui/react`** for popover positioning. UserCard always anchors above (no flip needed at the bottom of the viewport — exception is rare); fall-back is CSS `position: absolute; bottom: 100%; left: 0`.
- **No persistence of search query**. By design — search is ephemeral; the "live filter" interaction expects a clean slate per session.

---

## 3 · Architecture

### 3.1 Where this lives in the system

```
   ┌────────────────────────────────────────────────────────────┐
   │  ChatScreen.tsx (existing controller)                      │
   │   conversations, activeRunId, identity, items, …           │
   └─┬───────────────────────────────────────────────────────┬──┘
     │ props                                                  │
     ▼                                                        ▼
   ┌──────────────────────────────┐    ┌─────────────────────────────┐
   │ Sidebar.tsx (new)            │    │ KeymapProvider (new)         │
   │  brand · collapse · search · │    │  registers tinykeys bindings │
   │  list · UserCard             │    │  reads onStartNewChat,       │
   └──┬─────────┬─────────┬───────┘    │  setSidebarCollapsed, etc.   │
      │         │         │            └─────────┬───────────────────┘
      ▼         ▼         ▼                      │
  filter   group     UserCard                    │
  (pure)  (pure)     popover                     │
                         │                       │
                         ▼                       ▼
                 WorkspacePicker          ApprovalFocusContext
                  fetches /v1/me/        register/unregister/
                   workspaces            approveTopmost
                                                ▲
                                                │
                                  ApprovalTool (PR 1.4)
                                  registers when an approval
                                  is unresolved + visible
```

### 3.2 Data flow — search

```
   user types "launch" in SidebarSearch.tsx
        │
        ▼
   setSearchQuery("launch")  (state in ChatScreen)
        │
        ▼
   filterConversations(conversations, "launch")
        │
        ▼
   groupConversations(filtered, now)
        │
        ▼
   <ConversationListGroups groups={...}/>  (re-render)
```

`filterConversations` is `O(n)` over a list that's bounded by the user's chat count; we don't memoize beyond `useMemo` over `[conversations, searchQuery]`.

### 3.3 Data flow — live pulse

```
   submitUserMessage → setActiveRunId(runId) (existing)
        │
        ▼
   ConversationRow read activeRunId via prop
        │
        ▼
   Row whose `runId === activeRunId` → renders <PulseDot />
        │
        ▼
   on `run_completed/cancelled/failed`, ChatScreen.handleEvent → setActiveRunId(null) (existing)
        │
        ▼
   PulseDot disappears within next render frame
```

The `runId` to `conversation_id` mapping is already maintained in `ChatScreen.activeRunUserMessageIdsRef` indirectly; we lift one tiny derived selector to compute "active conversation":

```ts
const activeConversationId = useMemo(
  () => (activeRunId ? conversationIdForRunId(items, activeRunId) : null),
  [items, activeRunId],
);
```

(`conversationIdForRunId` is one line over `items.find(...)`.)

### 3.4 Library survey + decision

| Library                                                        | Size (gz) | Verdict                                                                                                              |
| -------------------------------------------------------------- | --------- | -------------------------------------------------------------------------------------------------------------------- |
| `tinykeys` ([github](https://github.com/jamiebuilds/tinykeys)) | ~400 B    | **CHOSEN.** Vanilla, zero deps, `$mod` alias, modern keyboard event handling, ~8M wk dl, MIT.                        |
| `react-hotkeys-hook`                                           | ~3 KB     | Rejected. React-only abstraction over `keydown`; we already wrap in `useEffect`.                                     |
| `cmdk` (Vercel)                                                | ~12 KB    | Rejected. Command palette; we have 4 bindings, not a palette.                                                        |
| `kbar`                                                         | ~14 KB    | Rejected. Same as cmdk.                                                                                              |
| `mousetrap`                                                    | ~8 KB     | Rejected. Unmaintained (2018).                                                                                       |
| Inline `keydown` listener (~30 LOC)                            | 0         | Considered. With `$mod`, repeats, contenteditable detection, and platform handling we'd reinvent ~80% of `tinykeys`. |

For popovers, dialogs, focus-traps:

| Library                   | Size   | Verdict                                             |
| ------------------------- | ------ | --------------------------------------------------- |
| `@radix-ui/react-popover` | ~10 KB | Rejected — `Menu` from design-system is sufficient. |
| `focus-trap-react`        | ~3 KB  | Rejected — not needed for our popover content.      |
| `@floating-ui/react`      | ~18 KB | Rejected — anchor positions are predictable.        |

For day grouping + relative dates:

| Library       | Size                     | Verdict                                                          |
| ------------- | ------------------------ | ---------------------------------------------------------------- |
| `date-fns`    | tree-shakeable, ~3–10 KB | Rejected — `Intl.DateTimeFormat` covers all needs (PR 1.6 §3.5). |
| `dayjs`       | ~7 KB                    | Rejected — same reason.                                          |
| Native `Intl` | 0                        | **CHOSEN.** Browser-built-in; respects user locale and timezone. |

**One package added: `tinykeys` (≈ 400 B).** Net dep delta is essentially zero.

### 3.5 DRY — what we reuse vs. what we add

| Concern                          | Reuse                                                                                                                                                                                                                                                                                  | Add                                                  |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| Conversation list                | `listConversations` + `Conversation[]` already in `ChatScreen.tsx`                                                                                                                                                                                                                     | —                                                    |
| Active run state                 | `activeRunId`, `pendingActionRunId` from `ChatScreen.tsx`                                                                                                                                                                                                                              | one `conversationIdForRunId` selector (~6 LOC)       |
| Sidebar collapse                 | `sidebarCollapsed` already in `ChatScreen.tsx`                                                                                                                                                                                                                                         | one ⌘\ binding                                       |
| New chat                         | `onStartNewChat` already in `ChatScreen.tsx`                                                                                                                                                                                                                                           | one ⌘N binding                                       |
| Auth identity                    | `useAuth()` from `AuthContext.tsx`                                                                                                                                                                                                                                                     | (maybe) one `switchWorkspace` wrapper if not present |
| Workspace listing                | `organization_members ⨝ organizations` (existing tables)                                                                                                                                                                                                                               | one read endpoint + one facade proxy                 |
| Day grouping                     | `Intl.DateTimeFormat`                                                                                                                                                                                                                                                                  | one 12-LOC reducer                                   |
| Search filtering                 | `String.prototype.toLocaleLowerCase`                                                                                                                                                                                                                                                   | one 8-LOC reducer                                    |
| Popover host                     | `Menu` from design-system                                                                                                                                                                                                                                                              | UserCard composition (~90 LOC)                       |
| TextInput                        | `TextInput` from design-system                                                                                                                                                                                                                                                         | thin wrapper for the search field                    |
| Icons                            | `IconButton`, `Badge` from design-system                                                                                                                                                                                                                                               | a "live pulse" CSS class (~8 LOC)                    |
| Approval registration            | `ApprovalTool` (PR 1.4) — already exposes `onApprove`                                                                                                                                                                                                                                  | a tiny `ApprovalFocusContext` (~50 LOC)              |
| ThreadListPrimitive              | We **stop** using `ThreadListPrimitive.Items` for iteration (it doesn't expose grouping/filter); we keep the `Root` context to play nicely with `useExternalStoreRuntime`'s thread runtime, and call `aui.threadList().switchToThread(threadId)` directly from each `ConversationRow`. | none — direct API calls.                             |
| `ExternalStoreThreadListAdapter` | Still passes `threads` and `archivedThreads` arrays to `useExternalStoreRuntime` — runtime is the source of truth for which thread is active.                                                                                                                                          | none — read-only consumer.                           |

**Net new code:** mostly JSX layout + two pure reducers + one context. ≈ 380 LOC excluding tests.

### 3.6 Sequence — Sarah presses ⌘↩ during a streaming approval

```
Sarah                         ApprovalTool                   ApprovalFocusContext               useKeymap                       PR 1.4 wire
  │                            │                                │                                    │                              │
  │  approval card mounts                                       │                                    │                              │
  │                             register({approvalId, approve})  │                                    │                              │
  │                            ─────────────────────────────────►│                                    │                              │
  │                                                              │  Map<id, approve>                  │                              │
  │                                                              │                                    │                              │
  │  presses ⌘↩                                                  │                                    │                              │
  │                                                              │                                    │ tinykeys fires "$mod+Enter" │
  │                                                              │                                    │ ◄─────────────────────────  │
  │                                                              │  approveTopmost()                  │                              │
  │                                                              │ ◄────────────────────────────────  │                              │
  │                                                              │  invokes approve() on bottom-most  │                              │
  │                                                              │ ─────────────────────────────────► │                              │
  │                                                              │                                    │   POST .../approvals/.../decide
  │                                                              │                                    │ ─────────────────────────────►│
  │                                                              │                                    │ ◄ 200 ────────────────────── │
  │                                                              │                                    │                              │
  │  approval transforms into a resolved record (existing PR 1.4)│                                    │                              │
  │                                                              │  ApprovalTool unmounts/unregisters │                              │
  │                                                              │ ◄─────────────────────────────────  │                              │
  │  aria-live: "Approved 'Send to #announcements'"              │                                    │                              │
```

If `approveTopmost()` returns `false` (no approvals registered), the keymap fires a polite "No approval to confirm." into a hidden `aria-live` region. No visual modal.

### 3.7 Edge cases (additional)

| Case                                                                           | Behavior                                                                                                                                                                   |
| ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| User has 200 conversations                                                     | Group reducer is `O(n)`; rendering uses `react-window` if profiling shows jank — **not now**. v1: render all rows; CSS contains scroll.                                    |
| `tinykeys` registers in StrictMode (double-effect)                             | Cleanup function in the `useEffect` removes the previous binding before re-registering.                                                                                    |
| User on a non-`$mod` platform (Linux without `Cmd`)                            | `tinykeys` resolves `$mod` to `Ctrl`; ⌘ glyphs in tooltips become "Ctrl" via a `formatChord` helper.                                                                       |
| Sidebar collapsed when ⌘K pressed                                              | Auto-uncollapse + focus search.                                                                                                                                            |
| User signs out from UserCard while a run is streaming                          | `auth.signOut()` cancels the active run via existing `auth` cleanup hook (already wired in 1.4).                                                                           |
| Workspace switch attempts while run is streaming                               | We confirm in a small dialog ("Switching workspace will stop the active response"). On confirm, cancel the run, then `switchWorkspace`.                                    |
| Search query matches a soft-deleted conversation (PR 1.6)                      | Default `?include_deleted=false` already excludes it. A future "Show deleted" filter (P1) toggles the query.                                                               |
| `Intl.DateTimeFormat` differs across browsers for "Today" boundary at midnight | Reducer takes `now: Date` injected by ChatScreen via `useMemo(..., [Math.floor(Date.now()/60_000)])` — re-buckets every minute. Tested with Vitest fake timers across DST. |
| Dual-monitor user resizes the window past 820 px boundary                      | Auto-collapse fires once; the user can re-expand manually; sticky preference is **not** persisted (matches today's behavior).                                              |

### 3.8 Test plan

**Frontend**

- `apps/frontend/src/features/chat/utils/groupConversations.test.ts` — Today/Yesterday/Earlier with timezone-shifted fixtures (UTC, Pacific, Tokyo); DST boundary; same-day-different-timezone.
- `apps/frontend/src/features/chat/utils/filterConversations.test.ts` — case-insensitive title and folder match; trim; empty query returns all.
- `apps/frontend/src/features/chat/components/sidebar/Sidebar.test.tsx` — composition snapshot; pulse on active row; user card mount.
- `apps/frontend/src/features/chat/components/sidebar/UserCard.test.tsx` — popover open/close; sign-out path; workspace switch confirmation when run active.
- `apps/frontend/src/features/chat/components/sidebar/WorkspacePicker.test.tsx` — single-workspace view; multiple workspaces; error retry.
- `apps/frontend/src/app/keymap.test.ts` — bindings register/unregister; input-focus guard; `$mod` alias resolution; cleanup in StrictMode.
- `apps/frontend/src/features/chat/approval/ApprovalFocusContext.test.tsx` — register order; `approveTopmost` returns false when empty; unregister on unmount.

**Backend / facade**

- `services/backend/tests/unit/routes/test_me_workspaces.py` — happy path; user with one workspace; user with multiple; foreign-org refusal at RLS.
- `services/backend-facade/tests/unit/routes/test_me_proxy.py` — proxy preserves identity headers; passes through 4xx.

**Cross-service smoke (`make test`)** — extend with one happy path: `GET /v1/me/workspaces` → 200 with two workspaces.

### 3.9 Rollout

- **Flag-free.** The new sidebar replaces the old in one PR. Old `AssistantThreadList.tsx` retains the same export so import sites don't move; its body is replaced.
- **One new dep (`tinykeys`).** Lockfile updated in the same PR.
- **Backend addition is read-only and additive.** Backout = revert FE + the two route adds; tables untouched.
- **Backout is reversible from a single revert commit.** `tinykeys` removed from lockfile in the revert.

### 3.10 Open questions

1. **Persistence of search query and group state across reloads?** v1: ephemeral. Revisit only if user testing shows people expect "last search" to stick.
2. **"Pinned" group at the top.** Design has it as a "later" pill; we don't model it in v1.
3. **Pulse on archived/deleted threads if a stray run still streams.** PR 1.6 cancels active runs on delete; the pulse will end naturally. We don't need extra logic.
4. **`⌘↩` semantics when multiple approvals exist on screen.** Bottom-most-registered is the heuristic; if that surprises users, we can swap to "the one nearest viewport center" with the same context shape. Trivial change.

---

## 4 · Acceptance checklist

- [ ] `apps/frontend/src/features/chat/components/sidebar/` ships with `Sidebar.tsx`, `SidebarSearch.tsx`, `ConversationListGroups.tsx`, `ConversationRow.tsx`, `UserCard.tsx`, `WorkspacePicker.tsx`.
- [ ] `apps/frontend/src/features/chat/utils/groupConversations.ts` and `filterConversations.ts` ship as pure modules with unit tests.
- [ ] `apps/frontend/src/app/keymap.ts` exports a single `useKeymap()` hook + a small `KeymapProvider` mounted once at `EnterpriseSearchApp` root in `App.tsx`.
- [ ] The four bindings (`$mod+N`, `$mod+K`, `$mod+\\`, `$mod+Enter`) are registered, with the input-focus guard semantics specified in §2.4.
- [ ] `apps/frontend/src/features/chat/approval/ApprovalFocusContext.tsx` is consumed by `ApprovalTool.tsx` (PR 1.4) — registration on mount/unmount, `approveTopmost` is exported.
- [ ] `AssistantThreadList.tsx` body is replaced by `<Sidebar />`; existing tests adapt.
- [ ] `GET /v1/me/workspaces` returns the expected shape; covered by backend + facade unit tests; proxied identity headers preserved.
- [ ] No new event types, no migration, no SSE handshake change.
- [ ] `tinykeys` added to `apps/frontend/package.json` dependencies; no other npm change.
- [ ] `npm run typecheck --workspace @enterprise-search/frontend` and `npm run build --workspace @enterprise-search/frontend` pass.
- [ ] `make test` green; ai-backend pytest unaffected; backend pytest covers `me_workspaces`.

---

## 5 · References

- [`apps/frontend/src/features/chat/components/thread/AssistantThreadList.tsx`](../../apps/frontend/src/features/chat/components/thread/AssistantThreadList.tsx) — body replaced.
- [`apps/frontend/src/features/chat/ChatScreen.tsx`](../../apps/frontend/src/features/chat/ChatScreen.tsx) — `conversations`, `activeRunId`, `sidebarCollapsed`, `onStartNewChat` source.
- [`apps/frontend/src/features/auth/AuthContext.tsx`](../../apps/frontend/src/features/auth/AuthContext.tsx) — identity + sign-out + (potentially extended) `switchWorkspace`.
- [`packages/design-system/src/index.tsx`](../../packages/design-system/src/index.tsx) — `Menu`, `IconButton`, `Badge`, `AppIcon`, `TextInput`.
- [`packages/api-types/src/index.ts`](../../packages/api-types/src/index.ts) — extended with `Workspace`, `WorkspaceListResponse`.
- [`services/backend/src/backend_app/routes/me.py`](../../services/backend/src/backend_app/routes/me.py) — extended with `GET /internal/v1/me/workspaces`.
- [`services/backend-facade/src/backend_facade/routes/me.py`](../../services/backend-facade/src/backend_facade/routes/me.py) — proxy entry.
- [`docs/new-design/pr-1.6-workspace-defaults-conversation-lifecycle.md`](pr-1.6-workspace-defaults-conversation-lifecycle.md) — `Conversation.folder/deleted_at` shape; day-grouping rationale (§3.5).
- [`docs/new-design/pr-2.1-topbar-chrome-thinking-depth.md`](pr-2.1-topbar-chrome-thinking-depth.md) — `runUiState` + status pill; status visual stays consistent across sidebar pulse and topbar pill.
- [`docs/new-design/pr-1.4-two-stage-approvals.md`](pr-1.4-two-stage-approvals.md) — `ApprovalTool` is the source of `approve` callbacks for `⌘↩`.
- [`tinykeys` · GitHub](https://github.com/jamiebuilds/tinykeys) · [`tinykeys` · npm](https://www.npmjs.com/package/tinykeys) — keymap library.
- [WAI-ARIA · `aria-current`](https://www.w3.org/TR/wai-aria-1.2/#aria-current) — active conversation row.
- [WAI-ARIA · Live regions](https://www.w3.org/TR/wai-aria-1.2/#aria-live) — polite announcements for keymap actions.
