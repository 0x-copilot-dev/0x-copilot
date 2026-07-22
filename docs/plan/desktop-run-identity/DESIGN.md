# Desktop Conversation & Run Identity — Architecture Design

**Status:** Proposed (design only; no code yet)
**Author:** Principal review, 2026-07-22
**Scope:** `apps/desktop`, `packages/chat-surface`, `services/ai-backend`, `packages/api-types`, `apps/frontend` (convergence)
**One line:** The desktop Run cockpit has no durable identity for a conversation or its runs; give it one — anchored in the Router URL, resolved from server truth, dispatched through a single controller — so the three shipped bugs become _structurally impossible_ and web+desktop converge on one implementation.

---

## 1. Problem Statement

### 1.1 Symptoms (as reported)

1. **You can send only one message.** The first message replies; every message after it produces no UI update and no streaming.
2. **Two chats both named "Desktop Session"** appear in the Chats tab, with no obvious cause.
3. **Reopening is broken.** Clicking the second "Desktop Session" does nothing; returning to the real chat drops to the empty **"NO ACTIVE RUN — give it a goal"** state.

### 1.2 Ground truth (from the live packaged app)

Pulled directly from the running desktop's logs and its on-disk file-native store (`~/Library/Application Support/0xCopilot/`):

| Evidence                                                                                                                                                                                                  | Source                                |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| **Two conversations, both titled "Desktop session"** — `09edc744…` (0 runs, 0 messages, phantom) and `88ddc5f3…` (5 completed runs, 10 messages, real), created **2 ms apart** (`05:58:39.669` vs `.671`) | `agent-data/v1/index/catalog.sqlite3` |
| **5 runs, all `completed`; 10 messages** on the real conversation                                                                                                                                         | same store                            |
| `GET /v1/agent/runs?conversation_id=…` → **405** (repeatedly)                                                                                                                                             | `logs/backend-facade.log`             |
| `GET /v1/agent/conversations/desktop-default/messages` → **404** (repeatedly)                                                                                                                             | `logs/backend-facade.log`             |
| Every run: `runtime.stream.succeeded` + `final_response` server-side                                                                                                                                      | `logs/ai-backend.log`                 |

**The backend processed all five messages correctly. Only the first ever rendered.** Nothing is wrong with the agent, the runtime, or run execution. The break is entirely in the desktop client's _identity model_ — and there isn't one.

### 1.3 Root cause (one fault, three surfaces)

The Run cockpit binds the entire UI to `useRunSession.runId`, and a run can only _become_ the bound run through transient React state set by the empty-state composer. Three structural gaps compound:

- **Dead run-resolution contract.** `useRunSession` resolves a conversation's runs via `GET /v1/agent/runs?conversation_id=` ([useRunSession.ts:171](../../../packages/chat-surface/src/destinations/run/useRunSession.ts#L171)), but `/runs` is registered **POST-only** ([routes.py:566](../../../services/ai-backend/src/runtime_api/http/routes.py#L566)). So `session.runs` is _always_ `[]`, `autoResolvedRunId` is _always_ `null`, and the only non-null binding is a run started live in the current mount.
- **Two disconnected dispatch paths.** The empty-state composer routes through `onStartRun → setStartedRunId` and binds the session ([RunDestination.tsx:338‑392](../../../packages/chat-surface/src/destinations/run/RunDestination.tsx#L338)). The in-chat composer (`RunComposer`) POSTs its own run and **discards the `run_id`** with no callback ([RunComposer.tsx:433](../../../apps/desktop/renderer/composer/RunComposer.tsx#L433)). Message 2+ runs server-side but the cockpit never subscribes → **Bug 1**.
- **No conversation identity; racy creation.** `RunBinder` fabricates a conversation on mount with a `GET conversations?limit=1`-else-`POST {title:"Desktop session"}` heuristic ([destinationBinders.tsx:618‑649](../../../apps/desktop/renderer/destinationBinders.tsx#L618)); two mounts both saw an empty list → duplicate conversations → **Bug 2**. Chats rows call `onReopen={() => onOpenRun()}` and thread **no** conversation id ([destinationBinders.tsx:202](../../../apps/desktop/renderer/destinationBinders.tsx#L202)); on remount `session.runId` resolves to `null` → **Bug 3**.

### 1.4 Why this is architectural, not a hot-fix

The same shared cockpit (`RunDestination` + `useRunSession`) ships on **both** substrates. The web binder `RunRoute.tsx` is byte-for-byte the same broken shape (racy `limit=1`-else-create, empty-state-only dispatch) — it is simply feature-flagged **off** (`runCockpitWeb`, default OFF), so users never hit it. Web's _working_ chat is the legacy monolith `ChatScreen.tsx`, where **one component owns `conversationId`, `activeRunId`, the list, dispatch, and reopen** — there is no seam to drop identity across, and every message goes through one `submitUserMessage` that creates _and_ binds the run inline.

A point-fix (e.g. "add an `onRunStarted` callback to `RunComposer`") papers over Bug 1 while leaving the missing identity model that causes Bugs 2 and 3, and leaves web/desktop on divergent code. The correct move is to **reproduce web's single-source-of-truth structurally on the shared `chat-surface` substrate** so both hosts converge on one implementation that actually works.

---

## 2. Goals & Non-Goals

### Goals

- One **durable** source of truth for _(active conversation, active run)_ that survives remounts, reopen-from-Chats, and app relaunch.
- A **single send path**: empty-state composer and in-chat composer both route through one dispatcher that starts a run _and_ binds it — for turn 1 and turn N identically.
- **Idempotent, non-duplicating** conversation creation and real conversation-identity threading Chats → Run.
- Make all three bugs **unrepresentable**, not patched.
- **Converge** web + desktop on the same `chat-surface` cockpit; the web `RunRoute` inherits every fix.
- Respect the hard boundaries: `chat-surface` is substrate-agnostic (ports only — no `window`/`fetch`); apps call the facade only; no `apps/* → apps/*` imports.

### Non-Goals (this design)

- **Conversation auto-titling** from message content — a worthwhile enhancement, but orthogonal to identity (it names the _one_ real conversation; it does not fix duplication or binding). Tracked as a fast-follow (§10, note).
- **Live streaming of runs in non-visible (background) conversations** — the shared cockpit binds the _open_ conversation. A run started in a conversation you then navigate away from is picked up on reopen via head-field resolution + event replay (`?after_sequence=N`), not streamed live in the background. True concurrent cross-conversation streaming is a separate capability, called out in the `ChatScreen` parity audit (§7, Phase 8).

> **Scope change from the first draft:** everything previously deferred — the runs-list endpoint, server-side atomic ensure-conversation-on-run, retiring legacy web `ChatScreen`, and transcript tail pagination — is now **in scope as committed phases** (§7), per the long-term-sustainability decisions recorded in §10.

---

## 3. Functional Requirements

| ID       | Requirement                                                                                                                                                                                                            |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **FR-1** | Sending the _n_-th message in an active conversation starts a run **and** binds the cockpit's live session to it, so its stream renders — identically for n=1 and n>1.                                                 |
| **FR-2** | The empty-state goal composer and the in-chat composer invoke the **same** dispatch function; neither can produce a `run_id` that is not bound.                                                                        |
| **FR-3** | The active conversation id is carried as **durable identity** (Router URL), not mount-local React state; it survives remount, destination switches, and app relaunch.                                                  |
| **FR-4** | Reopening a conversation from Chats threads that conversation's **real id** into the cockpit and shows its transcript — even when its latest run is already `completed` (no false "NO ACTIVE RUN").                    |
| **FR-5** | A conversation is created in **exactly one place** (first send of a new chat), carrying a stable idempotency key so concurrent/double-tap creation collapses to a single row. Nothing creates a conversation on mount. |
| **FR-6** | The cockpit resolves the conversation's active/latest run from **server truth** (a conversation "head" field / message `run_id`), never from client-held run state or a non-existent runs-list.                        |
| **FR-7** | The canvas shows the conversation's transcript + composer whenever the conversation has content, decoupled from whether a run is currently live (a run-less but message-bearing conversation is not "empty").          |
| **FR-8** | A fresh send after a manual run selection always wins (deterministic bind precedence — no stale-selection trap).                                                                                                       |
| **FR-9** | The web `RunRoute` binds the same controller from its URL ids; `App.openRun` stops dropping its conversation id.                                                                                                       |

## 4. Non-Functional Requirements

| ID                                 | Requirement                                                                                                                                                                                            |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **NFR-1 (Scalability)**            | O(1) active-run resolution per conversation (reuse the existing `idx_agent_runs_org_conversation_created` index); no per-message table scans; ready to scale to many conversations and many runs each. |
| **NFR-2 (Convergence / DRY)**      | The conversation/run session logic lives once, in `chat-surface`; web and desktop are thin binders. No duplicated run-state machine.                                                                   |
| **NFR-3 (Substrate-agnostic)**     | All I/O in `chat-surface` stays port-only: `Transport` (POST + SSE), `Router` (URL identity), `KeyValueStore` (cold-boot mirror). No bare `window`/`fetch`/`localStorage`.                             |
| **NFR-4 (Backward compatibility)** | The one new server field is additive; existing clients ignore it. The `HashRouter` decoder accepts old and new hash shapes. Legacy web `ChatScreen` is untouched until parity is proven.               |
| **NFR-5 (Minimal server surface)** | No new route, no new index, no migration, no widened run contract for the bug fixes — reuse the query + projection machinery that already exists.                                                      |
| **NFR-6 (Verifiability)**          | Each phase is independently shippable and covered by the hermetic real-graph run→stream keystone test plus targeted per-bug tests; the desktop change is verified against the live packaged stack.     |
| **NFR-7 (Multi-device ready)**     | Because identity is a deep-linkable URL and run truth is server-resolved, the same conversation opens consistently on another device with no client-held run state to reconcile.                       |

---

## 5. Current Architecture (as-is)

```
Desktop send path today
──────────────────────────────────────────────────────────────────────
bootstrap.tsx  activeDestination = useState(slug)      ← nav identity is a bare slug
   │                                                      (HashRouter built at :92 but UNREAD)
   ▼
DestinationOutlet  conversationId = "desktop-default"   ← hardcoded placeholder (:45)
   │                (optional prop exists at :84, never supplied)
   ▼
RunBinder  GET conversations?limit=1 else POST "Desktop session"   ← RACE → dup convs (:618-649)
   │        activeConversationId resolved here, self-owned
   ▼
RunDestination  session = useRunSession(runId: startedRunId ?? explicitRunId)
   │  ├─ empty-state composer → onStartRun → setStartedRunId  ← ONLY path that binds (:338-392)
   │  └─ in-chat RunComposer → POST /runs, DISCARDS run_id     ← Bug 1 (RunComposer.tsx:433)
   ▼
useRunSession  runs = GET /v1/agent/runs?conversation_id=  → 405 → []   ← dead (:171)
               runId = selectedRunId ?? explicitRunId ?? null           ← precedence trap (:151)
               canvas gated on runId!==null                             ← Bug 3 (RunDestination:808)
```

**Assets that already exist and are unused (the design leans on these):**

- `packages/chat-surface/src/routing/router.ts` — `ArtifactRoute` union already models `{kind:"chat",conversationId}`, `{kind:"conversation",conversationId}`, `{kind:"run",runId}`, with hash encode/decode + deep-link subscribe. **The desktop constructs a `HashRouter` and never reads it.**
- `packages/api-types/src/index.ts:526‑527` — `ConversationResponse` already carries `latest_run_status` + `latest_run_id` on the wire.
- `conversation_query_service.py` (~:353‑363) — already calls `get_latest_run_for_conversation(...)` on the list path but keeps only `.model_name` and **discards `.run_id`**.
- `CreateConversationRequest.idempotency_key` + the postgres partial unique index `idx_agent_conversations_idempotency` — idempotent creation is already supported server-side; the client just never sends a key.
- `RunDestination`'s `startedRunId → useRunSession.runId` seam (:226‑230) — a working single rebind sink with only one producer wired.

---

## 6. Architectural Solution

**Router-anchored conversation identity + one `ConversationSession` controller in `chat-surface` + server-authoritative run/conversation truth.**

Three grafts, each the best of an evaluated approach (see §9):

- **Two-tier SSOT** (from the Router/URL approach): durable identity in the URL, run truth on the server. _This is the backbone._
- **Server-authoritative truth** (from the server-authoritative approach): the head field (O(1) resolution) now, plus atomic ensure-conversation-on-run and a real runs-list — adopted as the durable creation + history primitives (§10, D2/D3), not deferred.
- **Run-is-derived invariant** (from the event-sourced approach): the run is never stored as independent client identity — but resolved from server truth (head field / runs-list), not by re-plumbing message pagination.

### 6.1 Two-tier source of truth

| Tier                  | What                                                | Where                                                                                                   | Why                                                                                                                              |
| --------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| **Durable identity**  | `conversationId` (optionally `runId` for deep-link) | Router URL / hash (`ArtifactRoute` via the already-built `HashRouter`)                                  | Deep-linkable, survives remount/reopen/relaunch, multi-device-portable, substrate-agnostic analogue of web's `?conversationId=`. |
| **Durable run truth** | active/latest run of a conversation                 | Server: `agent_messages.run_id` + `ConversationResponse.latest_run_id` / new `latest_run_id_any_status` | The run is _resolved_, never persisted as client identity — so no seam can drop it.                                              |
| **Ephemeral mirror**  | "last active conversationId"                        | `KeyValueStore`                                                                                         | Cold-boot only, when the hash is empty. **Never** a run id.                                                                      |

`RunDestination.startedRunId` is demoted from "the binding" to an **optimistic within-session cache**, discarded on every `conversationId` change.

### 6.2 Components

| Component                                                         | Responsibility                                                                                                                                                                                                                                                                                                                  | Location                                                                                                                   |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| **`ConversationSession` controller** (generalize `useRunSession`) | Owns `conversationId`, resolves the active `runId` from server truth, exposes **one** `dispatch(text)` and **one** `bindRun(runId)` sink, streams events, projects the transcript.                                                                                                                                              | `packages/chat-surface/src/destinations/run/`                                                                              |
| **`session.dispatch(text)`**                                      | Non-branching **create-run-then-`bindRun`** (host `onStartRun` wins, else `Transport POST /v1/agent/runs`). On first send of a new chat, also creates the conversation with the stable idempotency key. A `run_id` can never be produced without being bound.                                                                   | lifted from `handleStartGoal` (`RunDestination.tsx:338‑392`)                                                               |
| **Extended `renderComposer` ctx**                                 | `{disabled, placeholder}` → `{disabled, placeholder, dispatch}`; both composers call the same `session.dispatch`.                                                                                                                                                                                                               | `RunDestination.tsx:174‑177`, `TcChat.tsx:197‑200`                                                                         |
| **Router-reading host binders**                                   | Read `conversationId` from the `ArtifactRoute`; thread it via props (drop `DESKTOP_DEFAULT_CONVERSATION_ID`); `onReopen(id)` navigates to the conversation route.                                                                                                                                                               | `apps/desktop/renderer/bootstrap.tsx`, `DestinationOutlet.tsx`, `destinationBinders.tsx`; `apps/frontend/.../RunRoute.tsx` |
| **`latest_run_id_any_status`**                                    | One additive `ConversationResponse` field (the **O(1) fast path** for opening/list — resolve a conversation's current run without an N+1 runs-list call), from the already-fetched `get_latest_run_for_conversation().run_id`.                                                                                                  | `services/ai-backend/.../conversation_query_service.py`, all 3 store adapters, `packages/api-types`                        |
| **Atomic ensure-conversation-on-run**                             | `POST /v1/agent/runs` accepts an _optional_ `conversation_id`; when absent it takes a `conversation_idempotency_key` and, in **one transaction**, get-or-creates the conversation and starts the run, returning both ids. The durable "start a chat" primitive — creation is server-authoritative, not a client responsibility. | `services/ai-backend/.../run_coordinator.py`, `runs.py` (request/response), facade proxy                                   |
| **`list_runs_for_conversation`**                                  | `GET /v1/agent/conversations/{id}/runs` — the **history primitive** that populates `RunMultiSelect` and retires the dead 405 path properly (reuses `idx_agent_runs_org_conversation_created`).                                                                                                                                  | `PersistencePort` (3 adapters), new route, facade proxy                                                                    |

### 6.3 Data flow (target)

```
Router URL  #/…/{conversationId}
   │  (subscribe)
   ▼
host binder  reads conversationId  ──props──▶  RunDestination(conversationId)
                                                   │
                                                   ▼
                                         ConversationSession
   ┌───────────────────────────────────────────────┼───────────────────────────────┐
   │ resolve active run:                            │ dispatch(text):               │
   │   GET /conversations/{id}                      │   [create conv if new + idem] │
   │   → latest_run_id (live) or                    │   POST /runs {conv,text}      │
   │     latest_run_id_any_status (finished)        │   → bindRun(run_id)  ◀── the   │
   │   → bindRun(that run)                           │      ONE sink                 │
   └───────────────────────────────────────────────┴───────────────────────────────┘
                                                   │
                                    subscribe /runs/{boundRunId}/stream?after_sequence=N
                                                   ▼
                                   transcript = persisted messages ⊕ live events
```

Both composers → `session.dispatch` → `bindRun`. Reopen → Router navigate → resolve head → `bindRun`. **One producer, one sink, one identity.**

### 6.4 The four decisions (explicit)

1. **SSOT location →** Two-tier: `conversationId` in the Router URL; run truth on the server. _Not_ mount-local `useState`, _not_ a `RunBinder` self-resolve effect — those are the exact seams that dropped identity. **Canonical route (D1): `{kind:"conversation", conversationId, runId?}`** — the conversation is the addressable artifact; `runId` is an _optional_ deep-link to a specific/historical run (default = bind the conversation's current run). The redundant `{kind:"chat"}` alias is **deprecated**: the decoder still accepts it (back-compat), the encoder only ever emits `conversation`. `{kind:"run",runId}` stays for run-only deep links (e.g. from Activity), resolving its conversation then binding.
2. **Server contract →** **Both**, each a distinct, permanently-justified primitive (D2): (a) the **conversation-head field** `latest_run_id_any_status` — the O(1) fast path for open/list, by un-discarding `get_latest_run_for_conversation().run_id` (`conversation_query_service.py:362`), reusing the existing query + index; and (b) a real **runs-list endpoint** `GET /v1/agent/conversations/{id}/runs` — the history primitive that populates `RunMultiSelect` and _retires_ the dead 405 path (rather than merely deleting the client call). The client **never** resolves runs from the removed `GET /v1/agent/runs`. Derive-from-events pagination surgery is rejected (§9).
3. **Unified dispatch →** Collapse to **one producer + one sink.** Lift `handleStartGoal` into `session.dispatch`; make `setStartedRunId`/`bindRun` the only sink; extend `renderComposer` ctx with `dispatch`; **delete** `RunComposer`'s own POST (also satisfies the no-`fetch` substrate rule); replace `useRunSession.ts:151`'s `selectedRunId ?? explicitRunId ?? autoResolvedRunId` with a single `boundRunId` written only by `bindRun` (which clears any manual selection).
4. **Conversation identity + creation →** Identity flows Router → props; creation is **server-authoritative and atomic** (D3). `conversationId` comes from the route (or the KV cold-boot mirror, else `listConversations()[0]` via one bootstrap resolver). `ChatsBinder.onReopen(row.id)` navigates to the conversation route. **Delete** the `RunBinder` self-create effect — nothing creates a conversation on mount. `session.dispatch` sends **one** `POST /v1/agent/runs`: for an existing conversation it carries `conversation_id`; for a new chat it carries a `conversation_idempotency_key` (minted once per new-chat intent) and the server get-or-creates the conversation + starts the run in one transaction. This removes the client's create round-trip _and_ the idempotency-key-lifetime hazard; the existing `idx_agent_conversations_idempotency` unique index is the duplicate backstop.

### 6.5 Why each bug becomes structurally impossible

- **Bug 1 (2nd message dead).** There is one dispatch and one bind sink; turn 1 and turn N are literally the same code path. A `run_id` cannot exist unbound because `dispatch` binds it in the same sequence. `RunComposer`'s independent POST is deleted.
- **Bug 2 (duplicate conversations).** Nothing creates a conversation on mount (the racy effect is gone). Creation is a single server-authoritative, atomic get-or-create keyed by idempotency key; the partial unique index collapses any concurrent/double-tap creation to one row.
- **Bug 3 (reopen → NO ACTIVE RUN).** Reopen threads the real id via the Router; the cockpit resolves the latest run from `latest_run_id_any_status` even for a finished conversation; and the canvas gate is decoupled from `runId` — a message-bearing conversation shows its transcript, never the empty state.

### 6.6 Alignment with the working web reference

Web's `ChatScreen` works because one component owns everything with no seam to drop identity. This design reproduces that **structurally on the shared substrate** instead of re-monolithing:

| Web `ChatScreen` (works)                                                | This design (shared)                            |
| ----------------------------------------------------------------------- | ----------------------------------------------- |
| `submitUserMessage` create-run-then-`setActiveRunId`+`startEventStream` | `session.dispatch` create-run-then-`bindRun`    |
| inline `setActiveRunId` (one sink)                                      | the single `bindRun` sink                       |
| lazy conversation create on first send                                  | idempotency-keyed create in `dispatch`          |
| `loadConversationById(id)` on reopen                                    | `router.navigate({conversationId})` → props     |
| runs reconstructed from messages, never a runs-list                     | run resolved from head field / `message.run_id` |

**Deliberate improvement over web:** identity lives in the deep-linkable Router URL (not component `useState`), and the controller lives in `chat-surface` so web+desktop share one implementation — the flagged web `RunRoute` inherits every fix and `App.openRun`'s deliberately-dropped id is finally threaded.

---

## 7. Phased Rollout (committed program — each phase independently shippable + verifiable)

Ordered **server truth → shared controller → identity → desktop → history → web convergence → legacy deletion → scale**. The first five kill the three bugs; the last four complete the sustainable end-state. All are committed (nothing "optional").

| Phase                                                  | Change                                                                                                                                                                                                                                                                                                                                                                                                               | Verify                                                                                                                                                                                                                                                  |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **1 — Server: conversation head field**                | Add `ConversationResponse.latest_run_id_any_status` (un-discard the run id at `conversation_query_service.py:353‑363`); implement across in-memory + postgres + file-store; add to `api-types`; confirm facade passes it through.                                                                                                                                                                                    | Unit: a **completed** conversation returns non-null `latest_run_id_any_status` on list + get; existing clients ignore the field.                                                                                                                        |
| **2 — Server: atomic ensure-conversation-on-run**      | `POST /v1/agent/runs` accepts optional `conversation_id` + a `conversation_idempotency_key`; when the conversation is absent, get-or-create it and start the run **in one transaction**, returning both ids (`run_coordinator.py`, `runs.py` request/response, facade).                                                                                                                                              | Unit: run POST with no `conversation_id` + a key creates exactly one conversation and one run; the same key twice → same conversation (idempotent); both ids returned.                                                                                  |
| **3 — chat-surface: `ConversationSession` controller** | Lift `handleStartGoal` → single `session.dispatch` (one `POST /runs`, ensure-on-run); single `bindRun` sink; **delete** the dead `GET /runs` auto-resolve (`useRunSession.ts:161‑195`) + collapse precedence (:151) to one `boundRunId`; extend `renderComposer` ctx with `dispatch`; decouple canvas gate (`RunDestination.tsx:808`) from `runId` → transcript-emptiness (seed from head/preview to avoid flicker). | Hermetic real-graph run→stream keystone still green; new tests: 2nd in-chat message renders (Bug 1), populated-but-runless reopen shows transcript (Bug 3), fresh dispatch after `selectRun` wins (precedence).                                         |
| **4 — Routing identity**                               | Make `{kind:"conversation", conversationId, runId?}` the canonical cockpit route; `HashRouter` encode emits it, decode stays backward-compatible with old hashes **and** the deprecated `chat` alias; wire `onReopen(row.id)` → conversation route.                                                                                                                                                                  | Unit: decoder accepts old + `chat`-alias + new shapes; deep-link / back-forward into `#/…/{conversationId}` binds the cockpit; a `runId` deep-link binds that run.                                                                                      |
| **5 — Desktop wiring (one PR)**                        | Subscribe the `HashRouter` in `bootstrap.tsx`; thread `conversationId` through `DestinationOutlet` (drop `DESKTOP_DEFAULT_CONVERSATION_ID`); **delete** the `RunBinder` self-create effect; `RunComposer` calls `ctx.dispatch` (delete its POST); `ChatsBinder.onReopen(row.id)` navigates.                                                                                                                          | Against the **live packaged stack** (`make desktop-install`): 2nd message renders; reopen a finished chat shows transcript + resolved run; double-tap New Chat then send → one conversation row; relaunch restores the last conversation from the hash. |
| **6 — Run history primitive**                          | `GET /v1/agent/conversations/{id}/runs` + `PersistencePort.list_runs_for_conversation` (3 adapters, reuse `idx_agent_runs_org_conversation_created`); populate `RunMultiSelect` from it. Retires the dead-path concept, not just the client call.                                                                                                                                                                    | Unit + integration: a conversation with N runs returns N ordered rows; `RunMultiSelect` renders them; picking one rebinds via `selectRun`.                                                                                                              |
| **7 — Web convergence**                                | Point `RunRoute` at the same controller mapping URL ids → props; stop `App.openRun` dropping its id (`App.tsx:814‑816`); run the `ChatScreen` **parity audit** (below); flip `runCockpitWeb` ON by default.                                                                                                                                                                                                          | Parity checklist vs `ChatScreen` all green; existing web suite green; dogfood web on the shared cockpit.                                                                                                                                                |
| **8 — Retire legacy web chat**                         | After the parity audit passes and bakes: **delete** `ChatScreen.tsx`, `chatRunState.ts`, the web-only run-state/background-run plumbing, and the `runCockpitWeb` flag. One implementation remains.                                                                                                                                                                                                                   | Web suite green post-deletion; no references to the removed modules; the shared cockpit is the only chat path.                                                                                                                                          |
| **9 — Transcript pagination at scale**                 | Fix `list_messages` to return the **most-recent** page (DESC from the tail) with a working `next_cursor` for upward paging; adopt in the shared transcript loader.                                                                                                                                                                                                                                                   | Unit: a >limit conversation returns its live tail first and pages older history; reopen shows the newest messages, not a truncated head.                                                                                                                |

**`ChatScreen` parity audit (gate for Phase 8).** Before deleting, enumerate every behavior `ChatScreen` has and confirm the shared cockpit covers it (or port it): the `chatRunState` phase derivation (planning pulse, header status — see `apps/frontend/CLAUDE.md` invariants), MCP-auth-action replay, drafts / subagents / sources hooks, the sidebar, and — explicitly — the **background-run manager** (`bg`: `run_id → conversationId` mapping, multi-run tracking, `closeStream`). If cross-conversation background streaming is a capability we keep, it must land in the shared controller _before_ deletion; if not, record the deliberate reduction. No deletion until the checklist is signed off.

---

## 8. Risks & Mitigations

| Risk                                                                                                                                                                                                                                                     | Mitigation                                                                                                                                |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Canvas-gate change edits the flagship cockpit's core render branch — a regression flashes empty state or hides live runs.                                                                                                                                | Seed the gate from head/preview to avoid flicker; lock with the hermetic real-graph test + an explicit populated-but-runless reopen test. |
| `idempotency_key` lifetime is a subtle hinge — must be minted per new-chat **intent** and cleared after the first successful create; reuse across two new chats collapses them into one row.                                                             | A focused unit test on key lifetime; hold the key in controller/route-draft state, not per-mount.                                         |
| `HashRouter` decode must stay backward-compatible with existing hashes or deep links break on upgrade (the Router goes from dead-wired to authoritative).                                                                                                | Decoder accepts old 2-segment and new conversation-scoped shapes; pinned by a decode test.                                                |
| Deleting the dead `GET /runs` auto-resolve removes `RunMultiSelect`'s (empty) data source.                                                                                                                                                               | Confirm no test asserts the GET fires; `RunMultiSelect` already no-ops on ≤1 run, so behavior is unchanged until Phase 6.                 |
| Sequencing hazard: `RunComposer`'s POST must be deleted in the **same** change that wires `ctx.dispatch`; the `RunBinder` self-create deleted only **after** `conversationId` reliably arrives from the Router — else first sends 404 or lose their run. | Phase 4 is one PR; land the wiring and the deletions atomically with the live-stack checklist.                                            |
| Pre-existing `list_messages` pagination stub (`next_cursor` always null, oldest-N-ASC) means very-long-transcript reopen can't page the tail.                                                                                                            | Out of scope; tracked in Phase 6 so "reopen at scale" claims stay honest.                                                                 |

---

## 9. Alternatives Considered

Three independent architectures were designed and scored (correctness, scalability, simplicity, alignment with the web reference + hard boundaries, migration cost):

| Approach                                                                               | Score   | Verdict                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| -------------------------------------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **P2 — Router/URL SSOT + one `ConversationSession` controller** (chosen backbone)      | **8.8** | Best alignment with the diagnosis; reproduces web's SSOT structurally on the shared substrate; minimal server; one dispatch/one bind is the correct Bug-1 kill; cleanest migration.                                                                                                                                                                                                                                                                                                                                 |
| **P3 — Event-sourced (run is a projection from messages/events)**                      | 7.8     | Elegant and faithful to the runtime's event model, but the optimistic-pending/reconcile layer is the highest-risk code, and deriving the run _list_ from messages forces a `list_messages` cursor/DESC rewrite on an endpoint the working legacy `ChatScreen` shares — a real regression hazard for uncertain benefit.                                                                                                                                                                                              |
| **P1 — Server-authoritative (runs-list endpoint + atomic ensure-conversation-on-run)** | 7.6     | Strongest correctness and best long-term multi-run/multi-device story; heavier surface (new `PersistencePort.list_runs_for_conversation` + route + facade proxy; a widened run contract). Scored below P2 as a _stand-alone backbone_, but its two ideas are **adopted** into the chosen design as the durable creation (D3) + history (D2) primitives — see §10. (The "partial-failure window" concern dissolves: the ensure-conversation-on-run happens _inside one transaction_, which is what makes it atomic.) |

**Chosen:** P2 as the backbone (Router SSOT + one controller), grafting **P1's server-authoritative truth** — head field now, plus atomic ensure-on-run and a real runs-list as committed phases — and **P3's** "run is derived, never client identity" as the guiding invariant (resolved from server truth, not by re-plumbing message pagination). Per the §10 sustainability decisions, none of P1's primitives are deferred.

---

## 10. Resolved Decisions

All five open questions are resolved for **long-term sustainability** (the durable end-state), not minimal surface. The three P0 bugs are still fixed first (Phases 1–5); the sustainable primitives follow as committed phases (6–9).

| #                                | Decision                                                                                                                                                                                                                                                           | Rationale                                                                                                                                                                                                                                                               |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **D1 — Route scheme**            | Canonical **`{kind:"conversation", conversationId, runId?}`**. The conversation is the addressable artifact; `runId` is an optional deep-link. Deprecate the redundant `{kind:"chat"}` alias (decoder accepts, encoder emits `conversation`).                      | Aligns the URL identity with the domain SoT (`ConversationId`, the `conversations` table, `/conversations` endpoints). Kills the `chat`/`conversation` synonym drift. `runId?` gives the multi-run future a deep-link with zero rework.                                 |
| **D2 — Run resolution**          | **Both** primitives, permanently: the **head field** (`latest_run_id_any_status`, O(1) open/list) **and** a real **runs-list endpoint** (`GET /conversations/{id}/runs`, history + `RunMultiSelect`). The dead `GET /v1/agent/runs` is retired, never resurrected. | They answer different questions — "the current run" (cheap, on every card) vs. "all runs" (history). Both are legitimate; neither is a shortcut. Reuses existing indexes; no migration.                                                                                 |
| **D3 — Creation**                | **Server-authoritative atomic ensure-conversation-on-run.** `POST /runs` get-or-creates the conversation + starts the run in one transaction, keyed by idempotency key. Client `dispatch` is a single call.                                                        | Removes a client responsibility, a round-trip, _and_ the client-held idempotency-key-lifetime hazard. A transaction is precisely what eliminates the orphan-conversation partial-failure window the two-call client path risks. The right primitive for "start a chat." |
| **D4 — Legacy web `ChatScreen`** | **Retire it** (Phase 8) once the shared cockpit passes the parity audit and bakes. Delete `ChatScreen` + `chatRunState` + the web run-state plumbing + the flag.                                                                                                   | Convergence (NFR-2/DRY) is the whole point — two chat implementations is the root disease. One implementation, gated on a signed-off parity checklist so nothing regresses.                                                                                             |
| **D5 — Transcript pagination**   | **Fix it (Phase 9), committed.** `list_messages` returns the live tail (DESC) with a working `next_cursor`. Not a blocker for the identity bugs, but **required before we claim "scales to long conversations."**                                                  | The oldest-N-ASC + null-cursor stub silently truncates long conversations on reopen — a latent correctness bug for an unbounded-growth product. Sustainable ≠ leaving a known truncation in place.                                                                      |

**Fast-follow noted (not in this design):** conversation **auto-titling** from the first message (your earlier idea). Now that creation is server-authoritative and atomic (D3), the natural home is a server-side title derivation on first-run of a keyless-created conversation — a clean addition once identity lands. Tracked separately so it doesn't couple to the identity work.
