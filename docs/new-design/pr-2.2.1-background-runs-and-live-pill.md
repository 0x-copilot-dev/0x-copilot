# PR 2.2.1 — Background runs across conversations + live‑pill polish

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 2 (FE shell) follow‑up to PR 2.2 in [`0-OVERALL_PLAN.md`](0-OVERALL_PLAN.md)
> **Owner:** frontend (lift `ChatScreen` runtime singletons into one conversation‑keyed reducer · drop `switchingDisabled` hard‑block · sidebar liveSet derived from runtime · CSS pill polish) · ai‑backend / facade (1 small wire add: `latest_run_status` on `Conversation` so cold reloads can paint the live indicator without opening a stream first) · api‑types (1 optional field)
> **Size:** **M.** No new endpoints, no migrations, no SSE wire changes. The architectural shift is entirely client‑side: replace the scattered `useState/useRef` singletons in `ChatScreen.tsx` with one reducer keyed by `conversation_id`, and let the existing SSE keep running on the conversations the user navigates away from.
> **Depends on:**
>
> - ✅ PR 2.2 sidebar / `Sidebar.tsx` (`switchingDisabled`, `liveConversationId`)
> - ✅ Streaming infra (`streamRunEvents`, `?after_sequence=N` resume — already idempotent)
> - ✅ `runtime_events` persistence (every event already replayable from any sequence_no)
> - ✅ `pendingActionRunId(items)` (existing run‑resume probe — kept; called per‑conv now)
>
> **Reads alongside:**
>
> - [`pr-2.2-sidebar-user-card-keymap.md`](pr-2.2-sidebar-user-card-keymap.md) — established the live‑pill + switching‑disabled contract this PR loosens.
> - [`pr-1.5-subagent-discovery-workspace-feeds.md`](pr-1.5-subagent-discovery-workspace-feeds.md) — same per‑conversation state pattern that already lives in `useSubagents` / `useDrafts` / `useWorkspacePaneState`. We apply the same shape to the rest of the runtime.
> - [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md) — backend projection fields, no event‑name‑prefix derivation, facade‑only network rule, `?after_sequence=N` reconnection.

---

## 0 · TL;DR

Today the chat surface treats one conversation at a time as the entire runtime. Switching threads is hard‑disabled while a run streams (`switchingDisabled={activeRunId !== null}` at [`AssistantThreadList.tsx:65`](../../apps/frontend/src/features/chat/components/thread/AssistantThreadList.tsx#L65)) — both the rows and `+ New chat` go grey, and `loadConversationById` early‑returns at [`ChatScreen.tsx:547`](../../apps/frontend/src/features/chat/ChatScreen.tsx#L547). The single `streamRef` / `activeRunId` / `items` / `citations` / `latestSequenceRef` / `activeRunUserMessageIdsRef` set assumes one visible conversation owns the only stream.

Two visible symptoms:

| Symptom                                                                                                  | Root cause                                                                                                                                                                                                                                   |
| -------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Sidebar rows go un‑clickable while a run is streaming; **`+ New chat`** is disabled.                     | `loadConversationById` and the sidebar both gate on `activeRunId !== null`. Switching would close the SSE that's still spending tokens, leak state, and there's no per‑conversation slot to put the incoming conversation's items+citations. |
| The live row's amber accent reads twice: a left rail **and** a lowercase `live` word with a pulsing dot. | `[data-live="true"]::before` paints the left rail; `.aui-conversation-row__live` paints the word + dot. Two pieces of UI for one idea; the word is also off‑rhythm against neighbour rows showing `10:37`.                                   |

**The principle:** a conversation that has a run is the **owner** of that run's stream — not the visible UI. The visible UI is just one of N possible projections. Switching projections doesn't tear down streams. This is already the pattern used by `useSubagents`, `useDrafts`, and `useWorkspacePaneState`; we're applying it to the rest.

After this PR:

| Surface                        | Today                                                                                                                                       | After                                                                                                                                                                                                                                                                                                            |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Switching while live**       | Blocked.                                                                                                                                    | Allowed. The outgoing conversation's SSE keeps streaming into its own runtime slot; the user sees the new conversation immediately. Switching back rebinds the UI to the slot — no replay needed if it never left memory.                                                                                        |
| **`+ New chat` while live**    | Blocked.                                                                                                                                    | Allowed. New conversation gets its own slot.                                                                                                                                                                                                                                                                     |
| **Sidebar live indicator**     | One conversation can show `live` (the active one).                                                                                          | Any number of conversations can show live simultaneously, derived from the runtime's set of live `runId`s. Cold reload paints live correctly using `Conversation.latest_run_status` from the list response (no need to open a stream first to find out).                                                         |
| **Live row visual**            | Left amber rail **+** lowercase `live` word **+** pulsing dot.                                                                              | Left amber rail kept (the strong signal). Drop the word; keep the pulsing dot in the meta slot where the timestamp normally goes. One visual idea, expressed once.                                                                                                                                               |
| **Page reload with live runs** | Visible conversation reconnects via `?after_sequence=N`. Other conversations that were running show stale state until you switch into them. | Visible conversation reconnects as today. Other conversations whose `latest_run_status === "running"` are reflected in the sidebar live‑set immediately on cold load. We **lazily** open their streams on first switch‑in (still uses `?after_sequence=N`), so we never run more streams than the user looks at. |

**Three principles**

1. **Streams belong to runs, not screens.** A run's SSE lives in a runtime slot keyed by `conversation_id`. The visible chat is just whichever slot the topbar/composer/thread is bound to right now.
2. **One reducer, one Map.** Replace ~10 scattered singletons in `ChatScreen.tsx` (`activeRunId`, `streamRef`, `latestSequenceRef`, `activeRunUserMessageIdsRef`, `latestReplaySequenceByRunRef`, `items`, `citations`, `sourcesMap`, `latestRunEvent`, `showConnectorSuggestions`) with one reducer holding `Map<conversationId, ConvRuntime>`. View components subscribe to the visible slot via a context.
3. **No bandaids.** No `setTimeout`s to "let the old stream finish before switching"; no per‑switch teardown that would cause a token‑burn or a missed event. The reducer owns the lifecycle invariants. Reconnect on terminal events and reconnect on resume both use the same `?after_sequence=N` path that already exists.

LoC estimate: frontend ≈ 520 (new `ChatRuntimeProvider` + `useConversationRuntime` hook + reducer; `ChatScreen` shrinks by ~140 lines as it becomes the binder, not the owner; sidebar + thread tweak; pill CSS) · api‑types ≈ 12 (one optional field) · ai‑backend ≈ 25 (project `latest_run_status` into the conversation list endpoint reading from existing `runtime_runs.status`) · backend‑facade ≈ 0.

---

## 1 · PRD

### 1.1 Problem

The chat surface in the Atlas design (and the one the user is building toward) treats parallel conversations as a first‑class affordance. The Marketing Ops persona kicks off a long subagent run on chat A, switches to chat B to draft a Slack reply, hops back to A when she sees the live‑pill flicker on the sidebar — exactly the workflow that makes the agent useful for non‑engineers who orchestrate multiple things at once.

Today the runtime can't do that. Three concrete failures:

1. **Switching is blocked** ([`ChatScreen.tsx:547`](../../apps/frontend/src/features/chat/ChatScreen.tsx#L547), [`AssistantThreadList.tsx:65`](../../apps/frontend/src/features/chat/components/thread/AssistantThreadList.tsx#L65)). Sidebar rows go disabled; `+ New chat` greys out. The user can stop and restart, but they cannot multitask.
2. **Slot reuse**. When the user does try to switch (after the run terminates), `setItems`, `setCitations`, `setSourcesMap`, `latestSequenceRef`, `activeRunUserMessageIdsRef` are all single‑slot. Re‑entering the previous conversation re‑replays history from scratch instead of restoring memory.
3. **Live indicator is binary**. Sidebar tracks one `liveConversationId`. Cold reload can't paint live state on the other conversations because we have no signal — and once we _do_ allow background streaming, we need the sidebar to reflect that, which today it doesn't have a model for.

The CSS issue is a smaller cousin of the same gap: the row says "live" _twice_ because the runtime model only has one signal and the UI ended up painting it from two places.

### 1.2 Goals

1. **One typed runtime, one Map.** Lift conversation‑scoped state into a single reducer keyed by `conversation_id`. The visible UI binds to one slot; switching = swap the binding, never tear down state.
2. **Background SSE works without us pretending we own it.** When the user switches away from a live run, the SSE keeps running. When they switch back the UI rebinds to the existing slot — no fresh replay if the slot is still in memory; reconnect via `?after_sequence=N` if it isn't.
3. **Cold reload paints live state.** Add `latest_run_status` to the `Conversation` list response so the sidebar can render the live‑set instantly without N speculative SSE opens.
4. **Drop `switchingDisabled`.** Permanently. Sidebar rows are always clickable; `+ New chat` is always available. Removing the prop is the architectural cue that the new model is correct.
5. **Live row visual: one signal.** Keep the left amber rail. Drop the lowercase `live` text. Keep a single pulsing dot in the meta slot — sized and aligned to the neighbour timestamps so the row's vertical rhythm doesn't break when it goes live.
6. **Server stays stupid.** No new endpoints. No new event types. `latest_run_status` is the only wire add and it's an additive optional field.
7. **Tests stay green.** `Sidebar.test.tsx` / `ConversationListGroups.test.tsx` keep passing with the new prop names; new tests cover the multi‑live path.

### 1.3 Non‑goals

- **N parallel SSE connections at all times.** We open at most one SSE per conversation we have open in memory, not N for every running conversation in the org. Cold reload reconnects the visible one only; the others rebind on switch‑in.
- **Multi‑pane / split‑view.** "Show me chat A and chat B side by side" is not in scope. One visible conversation at a time; the others run in the background invisibly.
- **Cross‑conversation merge.** No "queue of all approvals across all chats" topbar; that's a future surface (the Approvals tab in the workspace pane is per‑chat).
- **A new run‑status enum.** The existing `AgentRunStatus` (`running | paused | completed | cancelled | failed`) is enough.
- **Persisting the runtime Map across reload.** It lives in memory only. A reload uses the existing `?after_sequence=N` reconnect for the visible conversation, and `latest_run_status` on the conversation list to paint sidebar live‑set.
- **Streaming the live‑pill from a server‑sent push.** The sidebar polls the conversation list on the existing cadence + receives terminal events on the visible run. We don't add a "tenant‑wide events" subscription.

### 1.4 Success criteria

- ✅ Sidebar rows are always clickable. `disabled` and `aria-disabled` are never set on a row because of an in‑flight run; `+ New chat` is never disabled by `switchingDisabled`. The prop and its callers are removed entirely (not silenced).
- ✅ Starting a run on conv A, switching to conv B, returning to conv A — token streaming continues uninterrupted in A (visible state shows the late tokens; no replay round‑trip if A's slot was still in memory).
- ✅ Sidebar paints `live` for every conversation whose runtime slot has a non‑terminal run, including conversations the user hasn't opened in this session (driven by `Conversation.latest_run_status === "running"`).
- ✅ The live row visual: left amber rail + a single pulsing dot in the meta slot, aligned with where neighbour rows render `10:37`. No lowercase `live` word. The aria‑label `"Live run"` stays for screen readers.
- ✅ `?after_sequence=N` reconnect works from the slot's `latestSequence`, regardless of whether the slot was bound to the visible UI when the disconnect happened.
- ✅ Memory is bounded. Closed terminal slots evict their `items`/`citations` after the user switches away (we keep the slot's metadata so the sidebar live‑pill state stays correct, but heavyweight content is dropped). Reopening replays from history. Cap: at most 8 in‑memory slots with content; LRU eviction otherwise.
- ✅ `npm run typecheck --workspace @0x-copilot/frontend` and `npm run build --workspace @0x-copilot/frontend` green. ai‑backend pytest green for the projection update. `make test` green.
- ✅ No new event types. No new endpoints. No new persistence.

### 1.5 User stories

| #    | Persona                 | Story                                                                                                                                                                                                                                                         |
| ---- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US‑1 | Sarah · multitask       | Kicks the Aurora launch run on Chat A. Switches to Chat B and types a Slack reply. Sidebar shows Chat A with a pulsing dot. Returns to Chat A — sees the assistant has streamed three more paragraphs. No "loading" spinner; the slot was warm.               |
| US‑2 | Sarah · cold reload     | Reloads the tab while two chats are running. Sidebar paints both with the pulsing dot using `latest_run_status` from the list. Visible chat reconnects its SSE; the other chat reconnects on switch‑in.                                                       |
| US‑3 | Sarah · new chat        | Mid‑run on Chat A she clicks `+ New chat`. New conversation opens, composer focused. Chat A keeps running; sidebar shows two live rows (Chat A pulsing, the new chat pulsing once its first run starts).                                                      |
| US‑4 | Marcus · QA             | Cancels the run on Chat A from the topbar. Stream terminates. Sidebar dot stops pulsing on Chat A. Marcus is on Chat B — Chat B is unaffected.                                                                                                                |
| US‑5 | Marcus · live‑pill diff | Looks at the sidebar — only the dot reads as "live" now; the row's title aligns with non‑live rows; no lowercase "live" word breaks the visual rhythm.                                                                                                        |
| US‑6 | Devi · slow network     | Mid‑run network blip. The slot's reconnect timer fires; `?after_sequence=latestSequence` resumes; UI reflects events without dup/gap. If Devi switched away during the blip, the reconnect still happens on the slot, and she sees the result on switch‑back. |

---

## 2 · Spec

### 2.1 Wire — `Conversation.latest_run_status` (additive)

```ts
// packages/api-types/src/index.ts — Conversation gets one optional field.

export interface Conversation {
  // ... existing fields ...

  /**
   * Status of the conversation's most recent run. Optional — older
   * server payloads omit it. Used by the sidebar to paint the live
   * indicator on cold reload without speculatively opening a stream.
   * `null` means the conversation has never had a run.
   */
  latest_run_status?: AgentRunStatus | null;
  /** PR 2.2.1 — paired with `latest_run_status`. Lets the runtime
   * Map skip a list round‑trip on reconnect: if the user switches into
   * a conversation we already know about and `latest_run_id` matches
   * the slot's `runId`, we resume; otherwise we re‑replay history. */
  latest_run_id?: string | null;
}
```

**Why optional and on the list payload**: the sidebar needs the live state for _every_ conversation it renders. Putting the field on the existing `Conversation` shape (returned by `GET /v1/agent/conversations`) means zero new round‑trips. Optional keeps every existing test green; older server builds skip the field and the FE falls back to "not live until proven otherwise."

**Why not a separate `/v1/agent/conversations/runs/status` endpoint**: nothing else needs that data; the conversation list is already the right primary key.

### 2.2 Server projection — `services/ai-backend`

```py
# services/ai-backend/src/runtime_api/http/conversations.py — list endpoint.

# Today the list returns rows from `agent_conversations`. Extend the
# projection to LEFT JOIN `runtime_runs` filtered to the most recent
# row by (conversation_id, started_at DESC). One row per conversation.
# The query is keyed by org_id (existing RLS); no new index needed —
# `runtime_runs(conversation_id, started_at)` is already covered by
# the migration 0007 partial index for the streaming reads.

class ConversationListItem(BaseModel):
    # ... existing fields ...
    latest_run_status: AgentRunStatus | None = None
    latest_run_id: str | None = None
```

The same projection applies on `GET /v1/agent/conversations/{id}` (single‑resource read), so a fresh navigation into a conversation also populates the slot's reconnection hint.

### 2.3 Frontend runtime — one reducer, one Map

The new state lives in `apps/frontend/src/features/chat/runtime/conversationRuntime.ts` (new file) and a `<ChatRuntimeProvider>` mounted at `ChatScreen.tsx`.

```ts
// apps/frontend/src/features/chat/runtime/conversationRuntime.ts

interface ConvRuntime {
  conversationId: string;
  /** Active run id; null means no live run for this slot. */
  runId: string | null;
  items: ChatItem[];
  citations: CitationRegistryByRun;
  sources: SourceEntryMap;
  /** Per‑run user‑message id index (was `activeRunUserMessageIdsRef`). */
  userMessageIdByRunId: Map<string, string>;
  /** Last applied sequence per run, for `?after_sequence=N` reconnect. */
  latestSequenceByRunId: Map<string, number>;
  /** Most recent run UI event for the topbar / status pill projection. */
  latestRunEvent: RuntimeEventEnvelope | null;
  /** SSE handle. Owned by the slot, not by ChatScreen. */
  stream: AgentEventStream | null;
  reconnectTimer: number | null;
  showConnectorSuggestions: boolean;
  status: string;
  /** Eviction hint: false = keep `items`/`citations`/`sources` warm in
   *  memory; true = the slot is metadata‑only (runId / live‑set still
   *  valid). LRU policy in §2.4. */
  contentEvicted: boolean;
}

interface ChatRuntimeState {
  byConversation: Map<string, ConvRuntime>;
  /** The conversation the visible UI is currently bound to. Switching
   *  threads = setting this. The runtime never tears anything down on
   *  a switch; it only changes which slot is "in front." */
  visibleConversationId: string | null;
}

type Action =
  | { type: "BIND_VISIBLE"; conversationId: string | null }
  | { type: "EVENT"; event: RuntimeEventEnvelope }
  | {
      type: "REPLAY_LOADED";
      conversationId: string;
      items: ChatItem[];
      citations: CitationRegistryByRun;
      latestSequenceByRunId: Map<string, number>;
    }
  | {
      type: "RUN_STARTED";
      conversationId: string;
      runId: string;
      userMessageId: string;
    }
  | {
      type: "RUN_TERMINAL";
      conversationId: string;
      runId: string;
      finalEvent: RuntimeEventEnvelope;
    }
  | { type: "STREAM_OPENED"; conversationId: string; stream: AgentEventStream }
  | { type: "STREAM_CLOSED"; conversationId: string }
  | { type: "EVICT_CONTENT"; conversationId: string };
// ... etc.

function reduce(state: ChatRuntimeState, action: Action): ChatRuntimeState {
  /* … */
}
```

The hook is the public surface:

```ts
export function useConversationRuntime(): {
  visibleConversationId: string | null;
  visible: ConvRuntime | null;
  liveSet: ReadonlySet<string>;
  bindVisible(id: string | null): void;
  startRun(args: {
    conversationId: string;
    runId: string;
    userMessageId: string;
  }): void;
  cancelVisibleRun(): Promise<void>;
  // ... etc.
};
```

**Why a reducer, not Zustand / Jotai / Redux Toolkit.** We don't add a state library for one feature. React `useReducer` + Context is enough; the surface is internal and tested via the hook.

**Why a single Map and not "slot per conversation as separate hook calls."** A separate hook per conversation can't see siblings; `liveSet` and LRU eviction need a single owner. One reducer = one ground truth.

### 2.4 LRU eviction policy

Memory is bounded. The reducer keeps at most **8 slots warm** (with `items`/`citations`/`sources` populated) plus N metadata‑only slots. Eviction order on overflow:

1. Slots whose `runId === null` (no active run), sorted by least recently visible.
2. Never evict the slot with `visibleConversationId === conversationId`.
3. Never evict a slot with `runId !== null` — we'd have to either close the SSE (drops events) or replay (wastes bandwidth). The cap accommodates this.

Evicted slots keep their metadata so the sidebar can still render the live‑pill from `runId` if the run is still active. They lose `items`/`citations`/`sources`. Reopening = `loadConversationById` replays from history (cheap; this is the existing path).

**Why 8.** The user opens at most a handful of chats in a session. If they regularly use 12, we lift the cap; if they use 3, the cap is a no‑op. The number is a runtime constant, not a tweak.

### 2.5 SSE lifecycle ownership

Today: `ChatScreen.tsx` owns `streamRef`, `reconnectTimeoutRef`, etc. After:

- **Stream open** is dispatched to the slot reducer; the reducer's effect (`useStreamSyncEffect`) opens / closes streams to match the desired state. One stream per slot with `runId !== null`.
- **`onEvent`** dispatches `{ type: "EVENT", event }` to the reducer. The reducer fans the event into the slot keyed by `event.run_id`'s owning conversation (we know it because we bookkept `runId → conversationId` at start time).
- **`onError`** dispatches a reconnect hint; the same effect re‑opens. Reconnect is **per‑slot**, not global.
- **Terminal events** dispatch `RUN_TERMINAL`; the reducer closes the stream and clears `runId`. Sidebar's live‑set drops it on the next render.

### 2.6 Sidebar live‑set wiring

```ts
// AssistantThreadList → Sidebar
liveConversationIds={runtime.liveSet}    // Set<string>, not just one
```

`Sidebar` already takes `liveConversationId`; we widen to a set. `ConversationListGroups` and `ConversationRow` get a `live: boolean` per row computed from membership. **`switchingDisabled` is removed entirely** from `Sidebar` and `AssistantThreadList`. Tests update to assert it's gone.

### 2.7 Live‑row visual

```css
/* apps/frontend/src/styles.css — replace .aui-conversation-row__live block. */

/* Before: text + dot. After: dot only. Sized + offset to slot in for
 * the timestamp so the row's vertical rhythm doesn't shift when it
 * goes live. The aria-label on the span keeps the screen-reader text. */
.aui-conversation-row__live {
  align-items: center;
  display: inline-flex;
  flex: none;
  height: 1lh; /* match the line-height of the timestamp it replaces */
  width: 0.95rem; /* ~ same visual footprint as "10:37" small text */
  justify-content: center;
}
.aui-conversation-row__live::before {
  animation: aui-live-pulse 1.6s ease-out infinite;
  background: var(--color-accent);
  border-radius: 999px;
  box-shadow: 0 0 0 0 color-mix(in srgb, var(--color-accent) 40%, transparent);
  content: "";
  height: 6px;
  width: 6px;
}
/* aui-live-pulse keyframes unchanged. */
```

JSX changes in `ConversationRow.tsx`:

```tsx
{
  isLive ? (
    <span
      className="aui-conversation-row__live"
      aria-label="Live run"
      role="status"
    />
  ) : (
    <span className="aui-conversation-row__time">{time}</span>
  );
}
```

The text content `live` is dropped; the `<span>` becomes content‑less and uses `aria-label` for accessibility (already present). Left amber rail (`[data-live="true"]::before`) is unchanged.

### 2.8 Audit / permissions / errors

Unchanged. No new endpoints, no new privileged writes, no new RLS surfaces. The `latest_run_status` projection inherits the existing org‑scoped read on `runtime_runs` (RLS already filters by `org_id`).

### 2.9 Telemetry

- New `pg_stat_statements` row for the LEFT JOIN in conversation list — verify it stays under p99 budget (the existing index covers it).
- Frontend logs **new metric**: `chat.runtime.bg_streams_open` (gauge), `chat.runtime.evictions` (counter). Reuses the existing telemetry transport — no new sink.

---

## 3 · Architecture

### 3.1 Module layout

```
apps/frontend/src/features/chat/runtime/
├── conversationRuntime.ts          (NEW — reducer + types)
├── ChatRuntimeProvider.tsx         (NEW — context + dispatcher + stream effect)
├── useConversationRuntime.ts       (NEW — public hook)
├── eviction.ts                     (NEW — LRU policy fn; pure, unit-testable)
└── __tests__/
    ├── conversationRuntime.test.ts (reducer behaviour)
    ├── eviction.test.ts            (LRU)
    └── useConversationRuntime.test.tsx (integration: switch while live)

apps/frontend/src/features/chat/ChatScreen.tsx
  - Lose ~140 lines of singleton state.
  - Wrap in <ChatRuntimeProvider> at mount.
  - Read visible slice via useConversationRuntime().
  - submitUserMessage / cancelRun now dispatch through the hook.

apps/frontend/src/features/chat/components/sidebar/Sidebar.tsx
  - Remove `switchingDisabled` prop.
  - Take `liveConversationIds: ReadonlySet<string>` instead of singular.

apps/frontend/src/features/chat/components/sidebar/ConversationRow.tsx
  - Drop the "live" word (CSS + JSX).
  - Drop the `disabled`-from-switching codepath.

apps/frontend/src/features/chat/components/thread/AssistantThreadList.tsx
  - Forward `liveConversationIds` from runtime; remove `switchingDisabled`.

apps/frontend/src/styles.css
  - Update .aui-conversation-row__live to dot-only.
```

### 3.2 Why this is not a bandaid

Five reasons.

1. **The reducer is the invariant carrier.** Today, "exactly one stream is open" is enforced by `streamRef` being a single ref. After, "exactly one stream per slot with `runId !== null`" is enforced by the reducer's effect that diffs intent against opened sockets. Same correctness, broader contract.
2. **No new race surfaces.** A `RUN_TERMINAL` action is the only thing that closes a stream; it's also the only thing that mutates `runId` to `null`. Today these were two separate mutations (clear `streamRef`, clear `activeRunId`) that could interleave. Now they are one reducer step.
3. **Reconnect logic stays in one place.** Today the inline `onError` callback inside `startEventStream` does the timer + reconnect dance. After, the slot effect handles open / close / reopen by diffing — a decline in cyclomatic complexity, not an increase.
4. **Replay path is unchanged.** Cold load of a conversation = `loadHistoryItems` → reducer dispatches `REPLAY_LOADED`. Warm switch = reducer simply rebinds `visibleConversationId`. Two cases, same data shape, no new wire.
5. **Server is unchanged where it matters.** No new event types, no new endpoints, no new auth, no new RLS. The only server change (`latest_run_status` projection) is additive on an existing list endpoint.

### 3.3 Edge cases handled by the reducer

| Edge case                                                                          | Reducer rule                                                                                                                                                                                                                                                    |
| ---------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| User opens conv A (live), navigates to conv B, terminal event arrives for A's run. | `RUN_TERMINAL` mutates A's slot; live‑set updates; visible slot is B, so the topbar/status pill on B does not flicker.                                                                                                                                          |
| User reloads while two convs are live.                                             | List endpoint returns both with `latest_run_status="running"`. Reducer seeds metadata‑only slots for both; visible one (the topmost in the list, by current selection rule) opens its SSE; the other does not. Sidebar paints both pulsing dots from `liveSet`. |
| User switches into a non‑visible live slot we don't have a stream for.             | Reducer dispatches `STREAM_OPEN_INTENT`; effect opens SSE with `?after_sequence=latestSequenceByRunId.get(runId) ?? 0`. The first events received are the missed ones; subsequent events stream.                                                                |
| Server drops the SSE for the **non‑visible** live slot (e.g. backend restart).     | We weren't subscribed (visible only). On switch‑in we re‑replay events from history (full replay endpoint), then resubscribe. No event loss because the events are persisted in `runtime_events`.                                                               |
| User types a `+ New chat` while two slots have live runs and we're at the LRU cap. | Eviction picks the least‑recently‑visible terminal slot (or, if none qualify, raises the cap by 1 — a live slot is never evicted). Cap is soft on purpose.                                                                                                      |
| Run terminates while user is on the slot but tab is in background.                 | `RUN_TERMINAL` dispatches; document‑hidden has no effect; the topbar/status pill update on next paint when the tab regains focus.                                                                                                                               |
| User cancels visible run.                                                          | `cancelVisibleRun()` calls existing `cancelRun(runId, identity)`; server emits `run_cancelled`; reducer treats it like any terminal event.                                                                                                                      |

### 3.4 What this PR does not change

- LangGraph executor, deep agent builder, capability loader, MCP middleware, OAuth flow, token vault.
- `runtime_events`, `runtime_runs`, `agent_conversations` schemas.
- The SSE wire format (envelope, `sequence_no`, replay).
- `?after_sequence=N` reconnect semantics.
- `usePinnedConversations`, `useSubagents`, `useDrafts`, `useWorkspacePaneState` (already conversation‑scoped; we leave them; the new runtime composes with them).
- The `pendingActionRunId` derivation; we just call it per‑slot now.

---

## 4 · Verification

### 4.1 Unit tests

- `conversationRuntime.test.ts` — reducer transitions: `BIND_VISIBLE`, `EVENT`, `RUN_TERMINAL`, `EVICT_CONTENT`. Eight live slots cap; ninth open evicts the right victim; visible slot never evicted; live slot never evicted.
- `eviction.test.ts` — LRU pure function: picks the right victim across mixed live / terminal slots.
- `useConversationRuntime.test.tsx` — integration: start run on A, switch to B (renders B's items immediately), terminal event arrives for A (live‑set drops A), switch back (renders A with the new tail; no replay round trip).

### 4.2 Sidebar tests

- `Sidebar.test.tsx` — assert `switchingDisabled` is gone (no prop accepted), rows render `disabled=false` even when `liveConversationIds` includes the active row.
- `ConversationRow.test.tsx` — `aui-conversation-row__live` renders as an empty span with `aria-label="Live run"`; no text content.
- Snapshot test for the live row (CSS class assertions only — no chrome paint).

### 4.3 Service tests (`services/ai-backend`)

- `tests/unit/runtime_api/http/test_conversations_list.py` — list endpoint projects `latest_run_status` and `latest_run_id` from the latest `runtime_runs` row per conversation; `null`s for never‑run conversations.
- Cross‑org check (RLS): a user from org A cannot see org B conversations' `latest_run_status` (existing RLS suite covers the broader read; we add one row asserting the new field is also gated).

### 4.4 Cross‑service smoke (`make test`)

The Aurora demo: start the launch run, switch to a different chat, observe the sidebar paint two pulsing dots when the second chat starts a run. Switch back and forth — no replay flicker, no stream drops, no token loss.

### 4.5 Compliance gate

Unchanged surface area. The new wire field is read‑only and additive. No new privileged write paths.

---

## 5 · Out of scope (this PR)

- **Persisting the runtime Map across reload.** In‑memory only.
- **Cross‑conversation merged views** (Approvals queue, Activity feed). Future PR.
- **Resume strategy that opens N background SSEs at cold load.** We open the visible one only.
- **Topbar live‑pill across all conversations.** Sidebar surface only; topbar is still scoped to the visible conversation's run.
- **A multi‑pane / split‑view chat surface.** Hard out of scope; would need a different layout primitive.

---

## 6 · References

- Existing PRDs:
  - [`pr-2.2-sidebar-user-card-keymap.md`](pr-2.2-sidebar-user-card-keymap.md)
  - [`pr-1.5-subagent-discovery-workspace-feeds.md`](pr-1.5-subagent-discovery-workspace-feeds.md)
  - [`pr-3.6-approval-detail-and-subagent-timeline.md`](pr-3.6-approval-detail-and-subagent-timeline.md) — same DRY principle, additive only.
- Relevant code:
  - `apps/frontend/src/features/chat/ChatScreen.tsx` — current singletons (lines 149–195).
  - `apps/frontend/src/features/chat/components/sidebar/Sidebar.tsx` — `switchingDisabled` consumers.
  - `apps/frontend/src/features/chat/components/sidebar/ConversationRow.tsx` — live‑row markup.
  - `apps/frontend/src/styles.css` — live‑pill styles (lines 819–858).
  - `services/ai-backend/src/runtime_api/http/conversations.py` — list endpoint to extend.
