# AC3 — Desktop runtime execution and recovery

| Field             | Value                                                                                                                                                                                                                                                                                                                                  |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Spec ID           | AC3 (epic; splits into **AC3a** UI-mount [ship] and **AC3b** cross-process worker/recovery [**optional/deferred**])                                                                                                                                                                                                                    |
| Status            | AC3a: decision-complete and implementation-ready. **AC3b: optional/deferred** — not required for the LIGHT store (see the split note below).                                                                                                                                                                                           |
| Wave              | AC3a → Wave 1 (product loop on existing store); AC3b → **deferred** (only if the worker is ever split into a separate process)                                                                                                                                                                                                         |
| Estimated effort  | Epic, not one PR. AC3a S–M (mount + send/stream/cancel on the existing store). AC3b XL — 20–30 engineer-days, **deferred** (separate worker, file notify, cross-process saver, leases, reconciliation, cancellation durability). See README "PR decomposition".                                                                        |
| Dependencies      | **AC3a:** AC1 gates + the in-flight phase-2 ThreadCanvas/Composer components; **no** AC2/AC4 dependency (runs on the existing PostgreSQL run/SSE path). **AC3b (deferred):** AC2 file-native session store; AC4 artifact byte store for checkpoint payloads.                                                                           |
| Required for      | AC6 code mode (checkpoint references; delivered by AC2-light + AC3a, not AC3b's cross-process machinery), AC10 hardening                                                                                                                                                                                                               |
| Primary owner     | `services/ai-backend` runtime worker and recovery                                                                                                                                                                                                                                                                                      |
| Supporting owners | Desktop supervisor, chat surface, chat transport, artifact storage, QA                                                                                                                                                                                                                                                                 |
| Coordination      | **Shares files with the in-flight `docs/plan/desktop/phase-2/{2A–2E}` track (2B in-progress on `desktop/phase-2-thread-canvas`; 2D done on `desktop/phase-2-tc-chat-composer-v2`).** See "Relationship to the in-flight phase-2 track" below — AC3 consumes the phase-2 mode/composer contract; it does not restate a conflicting one. |
| Web impact        | None (see the corrected shared-`chat-surface` note under "Relationship…"; shared props stay additive and backward-compatible).                                                                                                                                                                                                         |

## Relationship to the in-flight phase-2 track and the AC3a/AC3b split

This AC is an epic. It is split into two tracks so the highest-value product step is not gated behind the most speculative infrastructure — and, with the LIGHT store decision, the second track is now **optional/deferred**:

- **AC3a — Mount desktop chat on the existing store (Wave 1, ship first).** Delete `DesktopPlaceholder`, mount the real `ChatsDestination` → `ThreadCanvas` → `TcChat` → `Composer`, wire the desktop controller, and connect send/stream/cancel/approval to the **facade run + SSE path that already works on `RUNTIME_STORE_BACKEND=postgres` today**. This needs only AC1's gate names and the phase-2 components. It has **no dependency on AC2 or AC4** and must not be gated behind the file-store rewrite. This directly replaces the placeholder and closes the product's core chat loop.
- **AC3b — Cross-process worker, checkpoints, and recovery (OPTIONAL / DEFERRED).** Everything else in this document: the _separately supervised_ `runtime_worker`, the `file_notify` _cross-process_ wake, the cross-process LangGraph saver hand-off, queue leases with stale-owner compare-and-swap, and cross-process startup reconciliation. **The LIGHT store decision removes the need for this track.**

> **Why AC3b is deferred.** The [LIGHT store (AC2)](02-ac2-file-session-store.md) runs the desktop as a **single in-process worker** (`RUNTIME_START_IN_PROCESS_WORKER=true`), and subagents are **in-process async tasks** (`await subagent.ainvoke(...)`). There is exactly one writer/executor process, so:
>
> - the API and worker are the same process — SSE waiters are woken **in-process** directly after the canonical append, with **no `file_notify` cross-process notifier** required;
> - queue claims are an in-process SQLite transaction, with **no cross-process lease compare-and-swap** required;
> - recovery is single-process: on restart, load the JSONL (ignoring any torn tail), rebuild the disposable index, and reconcile runs from the last durable records — **no cross-process reconciliation protocol** required;
> - the graph checkpointer reuses LangGraph `SqliteSaver` (durable, single-writer), so **checkpoints are durable without the cross-process artifact-backed saver hand-off**.
>
> **AC3a + the LIGHT file store already deliver the durable product loop** (send → stream → cancel → approval, durable across restart, resumable from checkpoints). AC3b's machinery becomes necessary **only if** the worker is later split into a separately supervised process (two writers) — at which point the deferred AC2/AC3b cross-process crash-consistency machinery is what makes that safe. Until that decision is taken, everything below the split is retained as the **specified-but-not-built** design for that future; it is not on the shipping path. The AC2/AC4 dependencies noted throughout AC3b apply only when AC3b is activated.

**Phase-2 ownership and deconfliction (mandatory).** The `docs/plan/desktop/phase-2/{2A–2E}` track already owns mounting `ChatsSidebar`/`ThreadCanvas`/`TcChat`/`Composer` and the mode contract:

- `2B-thread-canvas.md` (in-progress, branch `desktop/phase-2-thread-canvas`) mounts `ThreadCanvas` with "chat is the right rail, swimlanes are host-owned approval chrome."
- `2D-tc-chat-composer-v2.md` (done, branch `desktop/phase-2-tc-chat-composer-v2`) freezes the composer contract: **Studio** shows the message list with the composer at the bottom; **Focus hides the composer** ("agent owns the room" — Activity/Approvals tabs only); `Composer` exposes a **pure `onSend(text)` callback** and is a host-driven event emitter (host-owns-actions, D28), not an action owner.

AC3 **defers to that contract** and does not restate a conflicting one. Concretely, the earlier draft's "the composer remains mounted in Studio, Focus, and Auto" and "`onSubmit` takes precedence over legacy `onSend`" are **withdrawn** wherever they contradict phase-2:

- AC3 does **not** require the composer mounted in Focus. It consumes phase-2's mode contract (composer visible in Studio/Auto, hidden in Focus). The mount-once/draft-preservation invariant applies only to modes where phase-2 keeps the composer mounted (Studio ↔ Auto), not Focus.
- AC3 wires send through phase-2's `Composer.onSend(text)` seam. Attachment support (the `RunAttachmentRequest` mapping below) is an **additive extension negotiated with the 2D owner** — a superset callback or adapter that leaves `onSend(text)` behavior intact — not a replacement `onSubmit` that changes the frozen 2D props.
- Whichever of AC3a and the phase-2 mount lands second rebases onto the merged component; the two must not fork `ThreadCanvas`/`TcChat`/`Composer`. If AC3a lands first it must match the 2B/2D design so phase-2 does not rebase onto a different component.

Where this document below still describes a composer mounted in Focus or an `onSubmit`-precedence contract, treat this section as the governing reconciliation.

## Problem and why now

The desktop shell can authenticate and proxy HTTP/SSE through Electron main, but it does not currently provide a working durable chat runtime:

- `apps/desktop/renderer/bootstrap.tsx` mounts `DesktopPlaceholder` inside `ChatShell`;
- `ChatsDestination` renders a `ThreadCanvas` placeholder rather than the real thread;
- `TcChat` owns a host-relative compatibility fetch that is not the desktop
  facade contract, and its `onSend` callback is optional and unwired;
- the desktop supervisor starts backend, AI API, and facade, but not `python -m runtime_worker`;
- setting `RUNTIME_START_IN_PROCESS_WORKER=true` does not solve the packaged configuration because the API only starts that debugging worker for an in-memory store;
- the configured in-memory event bus cannot wake an API-side SSE waiter from another process;
- the runtime graph uses a process-global `InMemorySaver`, so approval and graph continuation disappear with the worker;
- queue claims have expiries but no desktop lease renewal or stale-owner compare-and-swap;
- the forever loop waits for a run handler before claiming a cancellation command; and
- there is no startup reconciler for runs, subagents, approvals, terminal events, or uncertain side effects.

AC2 makes state durable. AC3 turns that state into a complete desktop execution path: real renderer controls, one supervised worker process, file-notify wake-ups, artifact-backed checkpoints, lease-safe dispatch, cancellation, and deterministic recovery. It preserves the existing facade and SSE protocol so this work does not create a desktop-only product API.

## Goals

**AC3a goals (ship):**

- Replace `DesktopPlaceholder` with the real Chats destination, thread canvas, message list, event projection, and composer.
- Use the existing renderer `IpcTransport`; the renderer continues to know neither bearer tokens nor local service URLs.
- Use only existing facade routes and response models for conversation list/create/read, message read, run create/status/events/stream/cancel, and approvals.
- Preserve current facade URLs, request/response models, SSE frame names/IDs/data, event envelopes, and reconnect semantics.
- Keep PostgreSQL worker scheduling, PostgreSQL event notification, web UI, and hosted deployment behavior unchanged.

**Durable loop on the LIGHT store (delivered by AC2-light + AC3a, single process):**

- Run the desktop as one **in-process** worker (`RUNTIME_START_IN_PROCESS_WORKER=true`); subagents are in-process async tasks. There is no separate worker process to supervise.
- Wake SSE waiters **in-process** directly after the canonical append; keep persisted event replay and `after_sequence` as the correctness mechanism.
- Use LangGraph `SqliteSaver` as the durable graph checkpointer (single-writer), replacing the desktop `InMemorySaver`; checkpoint references remain canonical AC2 records.
- Reconcile runs on restart within the single process: load JSONL (ignore a torn tail), rebuild the disposable index, and resume only from a validated committed safe point; never silently rerun an uncertain non-idempotent side effect.
- Make cancellation observable to active execution rather than waiting behind the run it is meant to cancel.

**AC3b goals (OPTIONAL / DEFERRED — only if the worker is split into a separate process):**

- Start exactly one _separately supervised_ desktop runtime worker from the staged AI-backend service, and explicitly disable the in-process worker. _(Deferred; contradicts the shipping single-process model above.)_
- Wake API-side SSE waiters _across processes_ with the AC1 `file_notify` backend, without PostgreSQL `LISTEN/NOTIFY`.
- Replace the checkpointer with a _cross-process_ artifact-backed LangGraph saver hand-off whose references are canonical AC2 records.
- Define the same durable reference journal for future Monty snapshots without importing or enabling Monty in AC3.
- Renew desktop queue leases, compare-and-swap claim completion, and make an expired/stolen claim stop producing side effects.
- Reconcile queued, running, approval-waiting, cancelling, and terminal parent/subagent work across processes on worker startup.

## Non-goals

- Redesigning chat visual language, introducing a second desktop chat implementation, or importing `apps/frontend` implementation code into desktop.
- Adding a desktop-only HTTP, WebSocket, IPC, or direct-AI-service API.
- Exposing the AC1 broker, physical file roots, checkpoint bytes, or worker controls to the renderer.
- Implementing AC4 object storage, AC5 file tools, AC6 Monty execution, AC7 remote execution, or AC8 browser automation.
- Guaranteeing that an arbitrary third-party external side effect is exactly once. Recovery requires provider idempotency/evidence or fails closed.
- Resuming in the middle of an arbitrary Python stack frame, model provider call, or non-cooperative native call.
- Continuing work after the user quits the desktop application.
- Changing PostgreSQL queue schema/claim semantics or replacing PostgreSQL event notification outside the desktop file backend.
- Adding new fields to `RuntimeEventEnvelope`, changing event type meaning, or deriving activity types in the renderer.
- Treating a readiness file, notification file, SQLite row, or renderer cursor as canonical execution state.

## User experience and failure behavior

### Normal desktop chat

1. Boot waits for backend, AI API/file-store recovery, the real runtime worker, and facade.
2. The Chats rail opens `ChatsDestination`, populated from `GET /v1/agent/conversations`; it does not call an unimplemented `/v1/chats/projects` endpoint.
3. Selecting a conversation mounts one `ThreadCanvas`. The desktop controller
   loads `GET /v1/agent/conversations/{conversation_id}/messages` and supplies
   controlled data to `TcChat`.
4. The shared `Composer` follows the phase-2 `2D` mode contract: mounted in Studio and Auto, hidden in Focus (Activity/Approvals tabs only). Switching between modes that keep the composer mounted preserves draft text, attachments, scroll, and tabs.
5. Send creates a conversation first when needed, then calls `POST /v1/agent/runs` with an idempotency key. The facade derives identity from the authenticated desktop session.
6. The controller subscribes to the returned `stream_url`, validates `RuntimeEventEnvelope`, and passes one ordered event list to `ThreadCanvas`.
7. The worker claims the durable command, executes it, commits events/checkpoints, and publishes file-notify hints. The API wakes and replays committed events over the unchanged SSE route.
8. Completion refreshes canonical messages and leaves the full replayable timeline available.

### Reopen and reconnect

- `ConversationResponse.latest_run_id` and `latest_run_status` identify an active/recent run after renderer or application restart.
- The controller gets run status/events through the facade, replays after its highest validated sequence, and follows the stream when nonterminal.
- A renderer cursor is only an optimization. If absent or ahead of the server, replay starts from zero or the server-confirmed cursor.
- Duplicate `event_id` is ignored only when the bytes match. A sequence gap triggers REST replay before later events are rendered.

### Worker or API loss

- A worker crash does not close the desktop shell or destroy committed data. The supervisor restarts it with bounded backoff.
- The UI shows **Recovering…** for a nonterminal run whose worker attempt ended unexpectedly.
- The new worker owns an exclusive singleton lock, expires stale prior-worker claims, reconciles state, and resumes from the last safe checkpoint.
- API restart causes the renderer stream to reconnect. Event replay fills every committed sequence regardless of missed notifications.
- Five worker crashes in five minutes retain the existing fatal crash-loop behavior. A redacted boot/runtime error is shown; the store remains intact.

### Recovery that cannot be automatic

- Missing/corrupt checkpoint payload, incompatible serializer/runtime version,
  a checkpoint reference to an uncommitted artifact, a cycle in parent-task
  lineage, an unreceipted model call, or an uncertain non-idempotent side
  effect fails the affected run safely.
- The UI receives an existing failure/terminal event with a safe code such as
  `recovery_checkpoint_invalid`, `recovery_model_call_uncertain`, or
  `recovery_side_effect_uncertain`.
- 0xCopilot never restarts the run from the original prompt merely to make progress.
- Diagnostics may identify a hashed operation and checkpoint ID but never expose checkpoint bytes, host paths, tokens, or raw tool arguments.

### Cancellation

- Pressing Stop calls the existing facade cancel route and immediately moves the UI to **Cancelling…**.
- The API persists `CANCELLING`, `run_cancelling`, and the cancel command. The active desktop worker observes the persisted fence within 250 ms under normal local I/O.
- No new model, tool, subagent, Monty, or remote operation may start after the fence.
- Cooperative model/stream/tool work is aborted. An already-dispatched external side effect cannot be undone; its eventual receipt or uncertainty is recorded before terminal reconciliation.
- A crash while cancelling never resumes normal execution. Startup completes cancellation.

## Alternatives considered

### Keep `DesktopPlaceholder` and open the web app in a browser

Rejected. It bypasses the packaged renderer/transport architecture and does not provide a desktop product surface.

### Import `apps/frontend/src/features/chat/ChatScreen.tsx`

Rejected. Deployable apps may not import sibling implementation code, and that component owns web-specific auth, routing, storage, and adapters. Desktop composes the shared `packages/chat-surface` primitives through its own controller.

### Add new desktop chat endpoints

Rejected. The facade already exposes the required conversation, run, event, stream, cancel, and approval contracts. A second API would drift and could bypass verified identity.

### Run the worker inside the API process

Rejected for packaged desktop. It couples API availability to execution, prevents independent crash recovery, makes in-memory notification look correct only by accident, and differs from the production runtime split.

### Poll events every two seconds with no notification

Rejected as the primary path because it makes local streaming visibly sluggish. Poll replay remains the correctness backstop; `file_notify` reduces wake latency.

### Use PostgreSQL `LISTEN/NOTIFY` with a file store

Rejected. It retains the AI runtime database solely as a notification bus and breaks the file-native target.

### Use OS-native filesystem watchers

Not selected. Native APIs coalesce/drop events and need platform-specific bindings. AC3 uses a bounded atomic state file per run plus a short active-subscription scan. It has the same lossy-hint semantics without a native dependency.

### Use loopback HTTP from worker to API

Rejected. It adds an internal callback endpoint, boot-order coupling, and another credential. Both processes already share the approved file-notify directory.

### Keep `InMemorySaver` and rerun after a crash

Rejected. Approval state and nested graph progress are lost, and rerunning can duplicate cost or side effects.

### Serialize checkpoints with pickle

Rejected. Loading pickle from a writable local store is arbitrary code execution in the trusted worker. The selected serializer has pickle fallback disabled.

### Mark every interrupted run failed

Rejected. It is safe but does not meet durable desktop execution. AC3 resumes validated safe points and fails only when evidence is insufficient.

### Resume every nonterminal run from its original input

Rejected. It duplicates provider calls, tool effects, approvals, and subagents. Original input is not a recovery checkpoint.

## Architecture and SOLID ownership

### Process topology

```text
desktop renderer
  ChatShell -> ChatsDestination -> DesktopThreadController
                                   -> ThreadCanvas -> TcChat -> Composer
        |
        | IpcTransport only
        v
Electron main TransportBridge -> backend-facade
                                  ├── backend
                                  └── ai-backend API
                                          |
                          AC2 JSONL + SQLite + file_notify
                                          |
                                  runtime_worker process
```

The runtime worker is a separately supervised process but uses the staged `services/ai-backend` directory and interpreter. It is not a new service, has no listening port, and imports no sibling service implementation.

### Ownership

| Responsibility                                                                                  | Owner                                       |
| ----------------------------------------------------------------------------------------------- | ------------------------------------------- |
| Desktop route selection, active conversation/run state, reconnect cursor, send/cancel callbacks | `apps/desktop/renderer`                     |
| Reusable thread, messages, composer, event projection, presentation-only modes                  | `packages/chat-surface`                     |
| Generic request/SSE and renderer-main IPC                                                       | `packages/chat-transport`                   |
| Bearer injection and facade URL                                                                 | Electron main transport                     |
| Public product API and byte-for-byte SSE proxy                                                  | `backend-facade`                            |
| Event replay, run lifecycle, checkpoint contracts, recovery policy                              | `services/ai-backend/agent_runtime`         |
| Worker scheduling, leases, cancellation propagation, reconciliation                             | `services/ai-backend/runtime_worker`        |
| File notification, checkpoint journal, queue lease, AC2 persistence                             | `services/ai-backend/runtime_adapters/file` |
| Checkpoint/snapshot bytes and digest verification                                               | AC4 artifact adapter                        |
| Physical root and process lifecycle                                                             | Electron main supervisor                    |

### SOLID mapping

- **Single responsibility:** the renderer controller owns UI orchestration; the worker coordinator owns process execution; the recovery planner is pure; adapters own file/checkpoint bytes.
- **Open/closed:** desktop adds file notification, file leases, and durable checkpointer adapters. Existing routes and PostgreSQL adapters do not branch on desktop UI concepts.
- **Liskov substitution:** `ArtifactBackedLangGraphSaver` passes the pinned LangGraph saver contract. File queue passes existing queue behavior plus a file-only lease port.
- **Interface segregation:** checkpoint blob, checkpoint journal, invocation
  recovery journal, recovery enqueue, claim lease, cancellation fence, and
  notification are narrow ports. PostgreSQL consumers are not forced to
  implement file-specific maintenance.
- **Dependency inversion:** execution and recovery depend on ports and immutable contracts. They do not import AC2 paths, SQLite, AC4 implementation, Electron, or platform APIs.

## Desktop renderer integration

### Component composition

`bootstrap.tsx` keeps one `IpcTransport` per authenticated workspace session and renders destination content explicitly:

```text
ChatShell
  activeDestination == "chats"
    -> ChatsDestination
         -> DesktopThreadController
              -> ThreadCanvas
                   -> TcChat
                        -> Composer
  otherwise
    -> implemented destination component, else DesktopDestinationFallback
```

`DesktopPlaceholder.tsx` is deleted. A new
`DesktopDestinationFallback.tsx` may render a destination-specific “not
available in desktop yet” state only for non-chat destinations; its props
are exactly
`{destination: Exclude<ShellDestinationSlug, "chats">}`, and a test proves the
Chats branch can never select it. This is not a fallback for chat
startup/runtime failure.

`ChatsDestination` receives a content slot and conversation-list state. It keeps sidebar/fullscreen layout but replaces its hard-coded `thread-canvas-placeholder`. The desktop controller uses the existing conversation response:

- group rows by `folder` when present and otherwise under **Chats**;
- use `latest_run_id`/`latest_run_status` for live/recovery badges and reconnect;
- route clicks through the existing `HashRouter` `ArtifactRoute`;
- add **New chat**, which creates a conversation through the facade and navigates to it; and
- never call `/v1/chats/projects`.

Shared-package props are additive and backward-compatible. Existing web hosts that do not supply controlled conversation data retain their current behavior until they opt in; desktop always supplies it.

The additive shared contract is:

```ts
export interface ControlledChatListItem {
  readonly conversationId: ConversationId;
  readonly title: string | null;
  readonly folder: string | null;
  readonly updatedAt: string;
  readonly latestRunId: RunId | null;
  readonly latestRunStatus: AgentRunStatus | null;
}

export type ControlledChatListState =
  | { readonly status: "loading" }
  | { readonly status: "error"; readonly safeMessage: string }
  | {
      readonly status: "ready";
      readonly conversations: readonly ControlledChatListItem[];
    };

export interface ControlledChatsSidebar {
  readonly state: ControlledChatListState;
  readonly activeConversationId: ConversationId | null;
  readonly onSelectConversation: (id: ConversationId) => void;
  readonly onNewConversation: () => void;
}

export interface ChatsDestinationProps {
  readonly controlledSidebar?: ControlledChatsSidebar;
  readonly thread?: ReactNode;
}
```

When `controlledSidebar` is present, `ChatsSidebar` performs no fetch and uses
only those rows/callbacks. When absent, its existing `/v1/chats/projects`
compatibility behavior remains unchanged. `thread` replaces the current
placeholder; the existing placeholder remains only for compatibility hosts
that omit the prop, never in desktop.

### Controlled thread data

The desktop controller is the single owner of:

```ts
export type DesktopThreadPhase =
  | "idle"
  | "loading"
  | "ready"
  | "queued"
  | "running"
  | "waiting_for_approval"
  | "cancelling"
  | "recovering"
  | "terminal"
  | "error";

export interface DesktopThreadState {
  readonly conversationId: string | null;
  readonly runId: string | null;
  readonly runStatus:
    | "queued"
    | "running"
    | "waiting_for_approval"
    | "cancelling"
    | "cancelled"
    | "completed"
    | "failed"
    | "timed_out"
    | null;
  readonly phase: DesktopThreadPhase;
  readonly messages: readonly TcChatMessage[];
  readonly events: readonly RuntimeEventEnvelope[];
  readonly latestSequenceNo: number;
  readonly safeError: string | null;
}
```

The controller:

- invalidates stale REST loads on route change and ignores their responses
  because the current IPC request contract does not carry `AbortSignal`;
- closes the prior run subscription before opening another;
- creates stable per-send idempotency keys before issuing a request;
- snapshots submitted text plus in-memory attachment handles until the canonical
  user message arrives; on explicit request/storage failure it restores text
  through `ComposerHandle` and re-exposes the same attachment handles;
- keeps an optimistic user message keyed by that ID until the canonical message arrives;
- permits only one active send per conversation;
- maps `CreateRunResponse.stream_url` and `events_url` directly rather than constructing internal service URLs;
- refetches messages after `final_response`/terminal state;
- persists only the last validated run sequence in `KeyValueStore`, keyed by
  SHA-256 of workspace/run IDs, purged on sign-out and after seven days; and
- on stream error, closes the `IpcTransport` subscription, REST-replays from
  the validated cursor, then resubscribes with full-jitter exponential backoff
  (250 ms initial, 5 s cap, reset after a healthy open); and
- clears optimistic state only after canonical reconciliation or an explicit error.

### `ThreadCanvas`, `TcChat`, and `Composer`

Required shared-surface corrections:

- `ThreadCanvas` receives controlled messages, running/cancelling state,
  `onSubmit`, attachment adapter, and `onCancel` and forwards them to `TcChat`.
- Uncontrolled `TcChat` keeps its existing host-relative fetch contract for
  current non-desktop hosts.
- Desktop uses controlled mode and loads
  `/v1/agent/conversations/{id}/messages`; `TcChat` issues no second request.
- Preserve `ThreadCanvas`'s existing mount-once invariant as defined by phase-2
  `2D`: the modes that keep the composer mounted (Studio and Auto) use the same
  message/composer component instance, Focus hides the composer, and controlled
  activity/approval slots may not add a mode-specific tree.
- `Composer.running` and `Composer.onCancel` reflect the active run.
- `TcSwimlanes` accepts controlled events. Desktop passes the same ordered list used by `ThreadCanvas`, so there is exactly one SSE subscription per active desktop run.
- Desktop passes `ThreadCanvas`'s existing
  `onApprove|onReject|onSuggestChanges` callbacks. The controller resolves the
  projected `diffId` to the server-issued `approval_id`, posts the existing
  decision route once, disables that approval while pending, and lets canonical
  `approval_resolved` replay determine the final UI. An unknown/stale mapping
  is rejected locally and refreshed; `diffId` is never sent as authority.
- The existing mount-once invariant gains an assertion that the composer DOM node and draft value survive transitions between composer-mounted modes (Studio ↔ Auto). Entering Focus hides the composer per phase-2 `2D`; leaving Focus restores the preserved draft.

The additive thread contract is:

```ts
export interface TcChatAttachment {
  readonly id: string;
  readonly name: string;
  readonly contentType: string | null;
  readonly size: number | null;
}

export type TcChatData =
  | { readonly source: "internal-fetch" }
  | {
      readonly source: "controlled";
      readonly status: "loading" | "ready" | "error";
      readonly messages: readonly TcChatMessage[];
      readonly safeError: string | null;
    };

export interface ThreadCanvasChatControl {
  readonly data: TcChatData;
  readonly activity: "idle" | "running" | "cancelling";
  readonly onSend?: (text: string) => void;
  readonly onSubmit?: (payload: ComposerSubmitPayload) => void;
  readonly attachmentAdapter?: AttachmentAdapter;
  readonly composerRef?: RefObject<ComposerHandle | null>;
  readonly onCancel?: () => void;
}
```

`ThreadCanvasProps.chatControl?: ThreadCanvasChatControl` is optional for source
compatibility and defaults to `{data:{source:"internal-fetch"},
activity:"idle"}`. Desktop always passes controlled data, `onSubmit`,
`attachmentAdapter`, `composerRef`, and `onCancel`.
`TcChat` never fetches when `source="controlled"`, and `Composer` calls cancel
only while activity is `running|cancelling`.

The desktop controller validates existing `MessageListResponse` and maps each
`Message` to `TcChatMessage` exactly: preserve `message_id`/role, create one
`{type:"text", text:content_text}` part when text is nonempty, map valid
`created_at` to epoch milliseconds, and map each `Message.attachments` entry to
`TcChatAttachment` metadata. The additive
`TcChatMessage.attachments?: readonly TcChatAttachment[]` renders inert
name/type/size chips; it never renders attachment bytes/URLs or auto-opens a
file. Uncontrolled hosts retain their existing `TcChatMessagesResponse`
mapping. Runtime reasoning remains event-derived; it is not reconstructed from
message metadata.

Desktop drives sending through phase-2 `2D`'s frozen `Composer.onSend(text)`
seam (host-owns-actions, D28). Attachment support is an **additive superset
negotiated with the 2D owner** — an optional payload/adapter path that leaves
the existing `onSend(text)` behavior and props intact — not a replacement
`onSubmit` that supersedes the frozen contract. Desktop supplies a
desktop-owned `AttachmentAdapter`. The adapter keeps the browser `File` only in
renderer memory, emits display metadata to `Composer`, and on submit maps bytes
to existing `RunAttachmentRequest` with
`{id,type,name,content_type,size,content:[{type,data,mime_type,filename,size}]}`
and a data URL in `data`. The request
travels through `IpcTransport` and the existing run route; AI API validates AC4's
100 MiB/item and 250 MiB/run limits, commits bytes to AC4, then commits the run
batch. Attachment bytes are never put in route state, `KeyValueStore`, logs, or
IPC diagnostics. A renderer/app restart discards an unsent attachment and asks
the user to reattach; no hidden temporary-file store is introduced.

No message body, attachment bytes, checkpoint data, or token is put in the URL, route, console, or Electron IPC diagnostics.

### Existing facade contract

Desktop uses only:

```text
GET    /v1/agent/conversations
POST   /v1/agent/conversations
GET    /v1/agent/conversations/{conversation_id}
GET    /v1/agent/conversations/{conversation_id}/messages
POST   /v1/agent/runs
GET    /v1/agent/runs/{run_id}
GET    /v1/agent/runs/{run_id}/events
GET    /v1/agent/runs/{run_id}/stream
POST   /v1/agent/runs/{run_id}/cancel
POST   /v1/agent/approvals/{approval_id}/decision
```

Request identity is omitted or overwritten at the facade and derived from the verified session. The renderer does not call backend or AI backend directly.

## Supervised worker lifecycle

### Process definition

The supervisor adds logical process name `runtime-worker` while mapping its service directory to `ai-backend`. Its command is exactly:

```text
<staged-python> -m runtime_worker
```

It receives the AI runtime environment plus:

```text
ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop
RUNTIME_STORE_BACKEND=file
RUNTIME_EVENT_BUS_BACKEND=file_notify
RUNTIME_FILE_STORE_ROOT=<Electron-injected app-owned root>
RUNTIME_START_IN_PROCESS_WORKER=false
RUNTIME_DESKTOP_BOOT_ID=<random UUID for this supervisor boot>
DESKTOP_BROKER_URL=<AC1 loopback URL>
DESKTOP_BROKER_TOKEN=<worker-audience token>
DESKTOP_BROKER_PROTOCOL_MAJOR=1
```

API receives its separate AC1 API-audience token and the same store root/boot ID. Neither process receives renderer-supplied values for these settings.

### Boot order and readiness

1. Electron provisions/validates the AC1 root and starts the AC1 broker.
2. Backend persistence starts as required by the existing desktop product.
3. Backend starts and becomes healthy.
4. AI API starts with file store, performs AC2 validation/repair and required projection rebuild, and becomes healthy.
5. Supervisor removes any stale ready hint for the new boot ID and starts `runtime-worker`.
6. Worker authenticates to the broker, opens the file store, obtains
   `<storage-root>/control/runtime-worker.lock`, completes queue projection and
   startup reconciliation, then atomically writes
   `<storage-root>/control/runtime-worker.ready.json`.
7. Supervisor validates `DesktopWorkerReadyV1.boot_id`, process PID, store ID, and file-backend marker.
8. Facade starts/becomes healthy, then the renderer is released from `BootGate`.

The worker has no HTTP port. Readiness is a hint validated against the live supervised child and current boot ID; canonical recovery state remains JSONL.

Shutdown order is:

```text
facade -> runtime-worker -> ai-backend API -> backend -> backend-owned database
```

Worker `SIGTERM` stops new claims, signals active tasks, allows up to five seconds for a safe checkpoint/terminal commit, releases leases, removes its ready hint, and exits. Existing `PythonService` escalation sends `SIGKILL` after the timeout. AC3 recovery covers the forced case.

AC3 adds only this disposable root-level control directory to AC1's v1 layout:

```text
<storage-root>/control/
├── runtime-worker.lock
└── runtime-worker.ready.json
```

Neither file is canonical. The lock is an OS advisory lock, not a PID-file
protocol. Both files use AC1 owner-only permissions and no symlink/reparse
following.

### Readiness contract

```python
class DesktopWorkerReadyV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    boot_id: UUID
    worker_instance_id: UUID
    process_id: Annotated[int, Field(ge=1)]
    store_id: UUID
    backend: Literal["file"] = "file"
    queue_projection_workspace_count: Annotated[int, Field(ge=0)]
    queue_projection_command_count: Annotated[int, Field(ge=0)]
    queue_projection_root_hash: Annotated[
        str, Field(pattern=r"^[0-9a-f]{64}$")
    ]
    reconciliation_id: UUID
    reconciled_at: AwareDatetime
    ready_at: AwareDatetime
```

`queue_projection_root_hash` is SHA-256 over canonical JSON containing the
sorted safe workspace key and, for every selected session, its safe
conversation key, selected generation, and last committed global sequence.
It proves which canonical frontier was folded before readiness without exposing
raw identity. The file is owner-only, contains no token/path/user ID, and is
replaced atomically. A ready file from another boot/PID is ignored and removed.

## Cross-process file notification

AC1 reserves `RUNTIME_EVENT_BUS_BACKEND=file_notify`. AC3 implements it without treating filesystem notification as durable data.

### Notification contract

```python
class FileEventNotificationV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    store_id: UUID
    notification_id: UUID
    batch_id: UUID
    workspace_key: Annotated[str, Field(pattern=r"^[a-z2-7]{52}$")]
    run_key: Annotated[str, Field(pattern=r"^[a-z2-7]{52}$")]
    highest_sequence_no: Annotated[int, Field(ge=1)]
    committed_at: AwareDatetime
```

Physical path:

```text
<storage-root>/workspaces/<workspace-key>/index/notify/runs/<run-key>/
├── publish.lock
└── state.json
```

`<workspace-key>` and `<run-key>` use AC1's lowercase-base32 SHA-256 scoped
path-key rule. The same safe keys inside the file must match the directory and
the active waiter's server-derived workspace/run keys before a wake. Raw
workspace, org, user, conversation, and run IDs appear in neither path nor
notification content.

### Publish

After AC2 has flushed the event batch and committed its SQLite projection, the writing API/worker:

1. obtains the stable per-run `publish.lock` with a 20 ms bound;
2. validates any existing `state.json` against store, path-derived
   workspace/run ownership, and the canonical latest run sequence; invalid or
   future values are discarded, while valid values are max-merged so concurrent
   API/worker publishers cannot regress the high-water mark;
3. writes a unique temp file in the same run directory and closes it;
4. atomically replaces `state.json`;
5. retries Windows sharing violations three times with 10 ms bounded delay;
6. releases the lock; and
7. drops the hint with a metric if lock/replacement still fails.

Notification files are not flushed. Losing one cannot lose an event.

### Consume

`FileNotifyEventBus` implements the existing event-bus protocol:

- register only active SSE `run_id` waiters;
- scan their `state.json` files every 50 ms;
- compare `notification_id`/highest sequence with the in-memory cursor;
- wake the matching `asyncio.Condition`;
- use `fallback_poll_seconds=0.5`; and
- remove run directories 24 hours after terminal status when no waiter exists.

`RuntimeSseAdapter` uses `event_bus.fallback_poll_seconds` rather than its hard-coded constant when a bus is supplied. Every wake performs `replay_events(after_sequence=N)`; the notification payload is never serialized to the client.

`auto` continues to resolve to PostgreSQL notification when PostgreSQL is configured in existing deployments. `file_notify` is accepted only by the AC1 desktop predicate and file store.

## Durable checkpoint contracts

### Separation of bytes and references

AC3 owns checkpoint identity, safe-point semantics, state transitions, and resume selection. AC4 owns immutable bytes. The order is mandatory:

1. serialize and validate bounded checkpoint/snapshot bytes;
2. AC4 commits bytes and returns `ArtifactRefV1`;
3. AC3 appends and flushes the checkpoint reference through AC2;
4. only then may execution report that safe point or pause.

A crash between steps 2 and 3 leaves an unreferenced object eligible for AC4 garbage collection. A canonical reference may never point at an uncommitted object.

### Generic durable reference

```python
class CheckpointEngine(StrEnum):
    LANGGRAPH = "langgraph"
    MONTY = "monty"


class CheckpointOwnerKind(StrEnum):
    PARENT_RUN = "parent_run"
    SUBAGENT = "subagent"
    INTERPRETER = "interpreter"


class CheckpointPayloadRole(StrEnum):
    STATE = "state"
    PENDING_WRITES = "pending_writes"
    INTERPRETER_SNAPSHOT = "interpreter_snapshot"


class CheckpointSafePoint(StrEnum):
    BEFORE_EXECUTION = "before_execution"
    GRAPH_STEP_COMMITTED = "graph_step_committed"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    EXTERNAL_EFFECT_RECEIPT_COMMITTED = "external_effect_receipt_committed"
    SUBAGENT_RESULT_COMMITTED = "subagent_result_committed"
    INTERPRETER_SUSPENDED = "interpreter_suspended"


class DurableCheckpointRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    checkpoint_ref_id: UUID
    conversation_id: Annotated[str, Field(min_length=1, max_length=128)]
    run_id: Annotated[str, Field(min_length=1, max_length=128)]
    owner_kind: CheckpointOwnerKind
    task_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    engine: CheckpointEngine
    payload_role: CheckpointPayloadRole
    thread_id: Annotated[str, Field(min_length=1, max_length=256)]
    checkpoint_namespace: Annotated[str, Field(max_length=512)] = ""
    engine_checkpoint_id: Annotated[str, Field(min_length=1, max_length=256)]
    parent_engine_checkpoint_id: Annotated[
        str, Field(min_length=1, max_length=256)
    ] | None = None
    langgraph_task_id: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    langgraph_task_path: Annotated[str, Field(max_length=1024)] | None = None
    write_index: Annotated[int, Field(ge=0)] | None = None
    resume_ordinal: Annotated[int, Field(ge=0)]
    safe_point: CheckpointSafePoint
    serializer: Literal[
        "langgraph-json-plus-v1",
        "monty-snapshot-v1",
    ]
    serializer_version: Annotated[str, Field(min_length=1, max_length=64)]
    engine_version: Annotated[str, Field(min_length=1, max_length=64)]
    runtime_contract_version: Literal[1] = 1
    artifact_use_reference_id: UUID
    artifact: ArtifactRefV1
    invocation_fence: Annotated[int, Field(ge=0)]
    created_at: AwareDatetime
```

Validation rules:

- parent refs have `task_id=None`; subagent/interpreter refs require it;
- `PENDING_WRITES` requires `langgraph_task_id`, `langgraph_task_path`, and
  `write_index`; all three are null for `STATE` and `INTERPRETER_SNAPSHOT`;
- LangGraph refs use `STATE` or `PENDING_WRITES` and an artifact kind of `langgraph_checkpoint`;
- Monty refs use `INTERPRETER_SNAPSHOT`, serializer `monty-snapshot-v1`, and artifact kind `monty_checkpoint`;
- both checkpoint kinds require validated MIME `application/octet-stream` and
  no preview; AC3 caps LangGraph logical bytes at 32 MiB and AC6 caps Monty at
  8 MiB, both below AC4's hard ceilings;
- `resume_ordinal` is strictly increasing for `STATE`/`INTERPRETER_SNAPSHOT`
  per `(run_id, owner_kind, task_id, engine)`; pending writes inherit their
  owning state ordinal and are unique by
  `(engine_checkpoint_id, langgraph_task_id, langgraph_task_path, write_index)`;
- `invocation_fence` is the highest committed external invocation ordinal included in the payload;
- `artifact_use_reference_id` names the AC4 `ArtifactUseRecordV1` committed in
  the same AC2 batch as this checkpoint reference;
- state refs for one owner form an acyclic parent-checkpoint chain; and
- an artifact digest, media type, size, serializer, or version mismatch makes the ref invalid.

`checkpoint_ref_id` uses AC2's store-scoped UUIDv5 function over
`[run_id, owner_kind, task_id, engine, payload_role, thread_id,
checkpoint_namespace, engine_checkpoint_id, langgraph_task_id,
langgraph_task_path, write_index]`. `artifact_use_reference_id` is UUIDv5 over
`[checkpoint_ref_id, "artifact-use"]`. Reusing that logical checkpoint key with
different artifact bytes, serializer, parent, or invocation fence is an
idempotency conflict, not a second reference.

### Reference state transitions

References are immutable. Their lifecycle is another canonical record:

```python
class CheckpointRefState(StrEnum):
    CREATED = "created"
    READY = "ready"
    SELECTED = "selected"
    CONSUMED = "consumed"
    SUPERSEDED = "superseded"
    INVALID = "invalid"


class CheckpointRefTransitionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    transition_id: UUID
    checkpoint_ref_id: UUID
    expected_state: CheckpointRefState
    next_state: CheckpointRefState
    recovery_epoch: Annotated[int, Field(ge=0)]
    reason_code: Annotated[str, Field(min_length=1, max_length=96)]
    occurred_at: AwareDatetime
```

`transition_id` uses AC2's UUIDv5 function over
`[checkpoint_ref_id, expected_state, next_state, recovery_epoch, reason_code]`.
`READY` and `CONSUMED` are resumable; `CONSUMED` means at least one prior
attempt decoded the checkpoint, not that the checkpoint is spent. Selection CAS
moves either state to `SELECTED` for one recovery epoch. Successful decode moves
it to `CONSUMED`; if that attempt dies before decode, startup proves the
attempt/lease is dead and returns it to `READY`. A later recovery epoch may
select `CONSUMED` again when no newer valid safe point exists. This prevents
concurrent ownership without making a crash consume the only resume point.
Older `READY|CONSUMED` refs remain resumable while their owner is nonterminal.
`latest_resumable` walks descending ordinals; if the newest ref is invalid, it
may select an older one only when every invocation after that ref's
`invocation_fence` has deterministic receipt/idempotency/query evidence.
Otherwise recovery fails closed. `SUPERSEDED` does not delete bytes; AC4
garbage collection waits until no retained reference or legal hold remains.

Allowed transitions are closed: `CREATED -> READY` in the reference's commit
batch, `READY|CONSUMED -> SELECTED` for the current epoch,
`SELECTED -> CONSUMED` after verified decode, `SELECTED -> READY` only after
proving its attempt dead, `READY|CONSUMED -> SUPERSEDED` only after the owner
is terminal or a retention/deletion policy makes it non-resumable, and any
state to `INVALID` on validation failure. A persisted `CREATED` ref without
same-batch `READY` is invalid. No other edge is accepted.

### Ports

Domain/recovery code depends on:

```python
class CheckpointBlobPort(Protocol):
    async def put_checkpoint_blob(
        self,
        *,
        request: ArtifactWriteRequestV1,
        chunks: AsyncIterator[bytes],
    ) -> ArtifactRefV1: ...

    async def open_checkpoint_blob(
        self,
        *,
        owner: ArtifactOwnerV1,
        artifact: ArtifactRefV1,
    ) -> ArtifactReadLease: ...


class CheckpointJournalPort(Protocol):
    async def commit_ref(
        self,
        *,
        artifact_use: ArtifactUseRecordV1,
        record: DurableCheckpointRefV1,
    ) -> DurableCheckpointRefV1: ...

    async def latest_resumable(
        self,
        *,
        run_id: str,
        owner_kind: CheckpointOwnerKind,
        task_id: str | None,
        engine: CheckpointEngine,
    ) -> DurableCheckpointRefV1 | None: ...

    async def list_pending_writes(
        self, *, run_id: str, engine_checkpoint_id: str
    ) -> Sequence[DurableCheckpointRefV1]: ...

    async def transition(
        self, transition: CheckpointRefTransitionV1
    ) -> CheckpointRefState: ...
```

`CheckpointBlobPort` is a checkpoint-restricted adapter over AC4's
`ArtifactStorePort.put()` and `open_verified(..., purpose="checkpoint")`; it
does not create another byte store. The write request uses verified
`ArtifactOwnerV1`, kind `langgraph_checkpoint` or `monty_checkpoint`,
representation `binary`, declared MIME `application/octet-stream`, retention
class `conversation`, and preview policy `none`. Bytes remain a bounded async
stream as AC4 requires.

AC2 implements the journal in the appropriate main/subagent JSONL stream.
`CheckpointJournalPort.commit_ref()` appends the AC4 `ArtifactUseRecordV1` and
`DurableCheckpointRefV1` plus its `CREATED -> READY` transition atomically in
one AC2 batch after validating matching artifact,
`artifact_use_reference_id`, verified org/workspace, conversation/run/task
ownership, and retention class. Execution imports neither concrete adapter.

## LangGraph saver

### Adapter

`ArtifactBackedLangGraphSaver` subclasses the pinned LangGraph `BaseCheckpointSaver` and implements:

- `get` / `aget`;
- `get_tuple` / `aget_tuple`;
- `list` / `alist`;
- `put` / `aput`;
- `put_writes` / `aput_writes`; and
- `delete_thread` / `adelete_thread` as retention-aware reference tombstones, not immediate object deletion.

Synchronous methods delegate through the service's safe blocking bridge and are covered by contract tests; desktop execution uses async methods.

`put/aput`:

1. reads `thread_id`, `checkpoint_ns`, and checkpoint ID from pinned LangGraph config;
2. resolves the internal run/conversation/task scope from server-owned runtime config;
3. serializes checkpoint, metadata, and new channel versions;
4. writes one AC4 object;
5. commits its `ArtifactUseRecordV1` and `STATE` reference in one AC2 batch; and
6. returns config containing the committed checkpoint ID.

`put_writes/aput_writes` writes immutable `PENDING_WRITES` artifacts keyed by
`(thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, write_index)` and
commits each artifact-use/reference pair atomically through the same journal.
Retrying an identical write returns the existing reference; different bytes at
the same key fail.

`get_tuple/aget_tuple` resolves the latest or requested resumable state ref,
validates the object, loads associated pending writes in deterministic
task/path/index order, and returns the pinned `CheckpointTuple`. In recovery
mode the planner first CAS-selects the exact ref for the current
epoch/attempt; the saver requires that selection and transitions it to
`CONSUMED` only after successful decode. An already current live attempt may
read the refs it wrote under its attempt/lease fence without creating a
recovery selection.

### Scope config

Parent runtime config remains `thread_id=run_id` and additionally carries server-owned:

```text
conversation_id
checkpoint_owner_kind=parent_run
checkpoint_task_id=null
checkpoint_ns=""
```

Nested LangGraph subagents retain LangGraph's generated `checkpoint_ns` and are annotated with their persisted `task_id`. Independently scheduled `AsyncTaskRecord` work uses its persisted `thread_id`/`langgraph_run_id` and `owner_kind=subagent`.

No scope field comes from a model tool argument or HTTP body.

### Serialization

- Use LangGraph JSON-plus/msgpack-compatible typed serialization with a product envelope identified as `langgraph-json-plus-v1`.
- Disable pickle fallback, `marshal`, arbitrary import hooks, and generic object reconstruction.
- Add explicit codecs only for the bounded message/tool/state classes present in runtime contract fixtures.
- Reject unknown extension tags, cycles, non-finite values, objects with executable reducers, and payloads over 32 MiB.
- Validate serializer, LangGraph, Deep Agents, runtime-contract, and artifact digest before load.
- Incompatibility is a typed non-retryable recovery failure. It never falls back to original-input replay.

The compatibility suite pins current package versions and committed golden checkpoints. An upgrade cannot ship until it reads the immediately prior supported checkpoint version or provides an offline upcaster.

### Runtime injection

Desktop runtime construction injects this saver through the execution dependency graph. `runtime_checkpointer()` may retain its in-memory default for tests/non-desktop paths, but the AC1 desktop predicate plus `RUNTIME_STORE_BACKEND=file` must fail startup if the durable saver is absent. No desktop code calls the process-global `InMemorySaver`.

Approval resume creates a fresh harness with the same injected saver and exact checkpoint config before issuing `Command(resume=decision)`.

## Monty checkpoint references

AC3 does not import Monty or create snapshots. It closes the durable handoff used by AC6:

- AC6 serializes only at a documented external-function suspension point.
- AC6 commits bytes through `CheckpointBlobPort` using serializer `monty-snapshot-v1`.
- AC6 commits `DurableCheckpointRefV1(engine=MONTY, payload_role=INTERPRETER_SNAPSHOT)`.
- Tool invocation/result is committed before the snapshot's `invocation_fence` advances.
- Resume compare-and-swaps `READY|CONSUMED -> SELECTED` for
  `(run_id, task_id, resume_ordinal)`.
- The snapshot's interpreter ABI, source hash, limit-profile hash, function-manifest hash, and next invocation index live inside the validated artifact envelope.
- A Monty external-call invocation index marked consumed/rejected can never
  dispatch again; this is distinct from a reusable checkpoint ref's
  `CONSUMED` lifecycle state.

This provides durable Monty references now while leaving interpreter implementation and security qualification to AC6.

## External-invocation recovery ledger

Checkpoint resume is safe only when provider calls and effects between safe
points have explicit evidence. AC3 adds canonical AC2 records linked to the
existing `ToolInvocationRecord` for tools and to the checkpoint pending-write
record for model results:

```python
class ExternalInvocationKind(StrEnum):
    MODEL_PROVIDER = "model_provider"
    TOOL = "tool"


class InvocationRecoveryMode(StrEnum):
    SAFE_READ = "safe_read"
    PROVIDER_IDEMPOTENCY = "provider_idempotency"
    PROVIDER_STATUS_QUERY = "provider_status_query"
    NEVER_AUTO_RETRY = "never_auto_retry"


class ExternalInvocationIntentV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    invocation_id: Annotated[str, Field(min_length=1, max_length=256)]
    run_id: Annotated[str, Field(min_length=1, max_length=128)]
    task_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    invocation_ordinal: Annotated[int, Field(ge=1)]
    invocation_kind: ExternalInvocationKind
    side_effect_class: ToolSideEffectClass | Literal["model_compute"]
    recovery_mode: InvocationRecoveryMode
    canonical_args_sha256: Annotated[
        str, Field(pattern=r"^[0-9a-f]{64}$")
    ]
    provider_idempotency_key_sha256: Annotated[
        str, Field(pattern=r"^[0-9a-f]{64}$")
    ] | None = None
    provider_query_adapter: Annotated[
        str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,95}$")
    ] | None = None
    prepared_at: AwareDatetime


class ExternalInvocationReceiptV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    receipt_id: UUID
    invocation_id: Annotated[str, Field(min_length=1, max_length=256)]
    invocation_ordinal: Annotated[int, Field(ge=1)]
    outcome: Literal["completed", "rejected", "failed", "uncertain"]
    provider_operation_id_sha256: Annotated[
        str, Field(pattern=r"^[0-9a-f]{64}$")
    ] | None = None
    result_record_id: UUID | None = None
    committed_at: AwareDatetime
```

Execution depends on one narrow atomic journal:

```python
class InvocationRecoveryJournalPort(Protocol):
    async def append_intent(
        self, intent: ExternalInvocationIntentV1
    ) -> ExternalInvocationIntentV1: ...

    async def commit_tool_outcome(
        self,
        *,
        result: ToolInvocationRecord,
        receipt: ExternalInvocationReceiptV1,
    ) -> ExternalInvocationReceiptV1: ...

    async def commit_model_outcome(
        self,
        *,
        artifact_use: ArtifactUseRecordV1,
        pending_write: DurableCheckpointRefV1,
        receipt: ExternalInvocationReceiptV1,
    ) -> DurableCheckpointRefV1: ...

    async def list_after_fence(
        self, *, run_id: str, task_id: str | None, invocation_fence: int
    ) -> Sequence[
        tuple[ExternalInvocationIntentV1, ExternalInvocationReceiptV1 | None]
    ]: ...
```

The AC2 file implementation commits tool result plus receipt in one batch and
model artifact-use plus `PENDING_WRITES` ref/READY transition plus receipt in
one batch. The model write uses the exact
`(checkpoint_id, langgraph_task_id, langgraph_task_path, write_index)` key that
the pinned saver will request, so its later `aput_writes` retry returns that
same ref. The journal never coordinates atomicity through separate port calls.

Ordinals are gap-free per `(run_id, task_id)` and allocated under the session
lock. Intent commits before dispatch. `TOOL` requires an existing matching
tool-invocation identity and a `ToolSideEffectClass`;
`MODEL_PROVIDER` requires `side_effect_class="model_compute"`.
`SAFE_READ` is valid only for `TOOL` plus `ToolSideEffectClass.READ`;
`PROVIDER_IDEMPOTENCY` requires a key derived from stable invocation identity
and its persisted hash; `PROVIDER_STATUS_QUERY` requires a registered
server-side query adapter that can recover the complete validated result, not
merely provider status. Write/external/destructive tools and model calls
default to `NEVER_AUTO_RETRY`. A model or tool argument cannot choose the mode.

A completed tool receipt requires the existing committed tool result. A
completed model receipt requires `result_record_id` to name a committed
LangGraph `PENDING_WRITES` record containing the normalized model result; that
pending-write reference and receipt commit in one AC2 batch. The recovered
graph consumes that write without dispatching the provider again. Receipt and
result commit before a checkpoint may advance its `invocation_fence`. Missing
evidence after dispatch becomes `uncertain` and is never converted to success
by elapsed time. If a model response arrived but that atomic result/receipt
batch did not commit, recovery fails `recovery_model_call_uncertain` unless
registered provider idempotency/query evidence reconstructs the same validated
result.

For a model result, `result_record_id` is the deterministic
`FileStoreRecordV1.record_id` wrapping the pending-write checkpoint reference;
it is not the artifact digest, artifact-use ID, or checkpoint-ref ID.

The canonical provider key is
`copilot_` plus lowercase unpadded base32 of
`SHA-256(store_id.bytes || UTF-8(invocation_id))`; an adapter may select
`PROVIDER_IDEMPOTENCY` only when the provider accepts that complete key.
`receipt_id` uses AC2's UUIDv5 function over
`[invocation_id, outcome, provider_operation_id_sha256, result_record_id]`.
A receipt must match the intent's ordinal and scope. `completed` requires a
matching committed result record; `uncertain` forbids one; `rejected|failed`
may reference only their committed terminal error/result record.
A later provider-verified completed/rejected/failed receipt may resolve an
earlier `uncertain` record, but two conflicting verified terminal receipts fail
the run for manual review.

## Queue leases and desktop scheduling

### File-only lease port

Existing `RuntimeQueuePort` remains unchanged. `RuntimePorts` gains optional
`recovery_queue` and `queue_lease` capabilities; this is the lease protocol:

```python
class RuntimeQueueLeasePort(Protocol):
    async def renew_claim(
        self,
        *,
        command_id: str,
        claim_id: str,
        worker_id: str,
    ) -> bool: ...

    async def complete_claim(
        self, *, claim_id: str, worker_id: str, result: RuntimeWorkerResult
    ) -> bool: ...

    async def retry_claim(
        self, *, claim_id: str, worker_id: str, result: RuntimeWorkerResult
    ) -> bool: ...

    async def dead_letter_claim(
        self, *, claim_id: str, worker_id: str, result: RuntimeWorkerResult
    ) -> bool: ...
```

`renew_claim` compares `command_id`, `claim_id`, `worker_id`, and current
nonexpired ownership in one SQLite write transaction and computes the new
expiry from its own UTC clock plus the fixed lease duration; callers cannot
choose an expiry. It writes no JSONL heartbeat. Finalization acquires the
session writer lock, starts the SQLite write transaction, repeats the ownership
comparison, flushes the canonical transition, updates/commits SQLite, and
releases in AC2 lock order. `False` means ownership was lost; the stale worker
may not append a terminal queue transition.

PostgreSQL `RuntimePorts.queue_lease` is `None` and follows its existing methods and behavior.

### Lease policy

- Initial lease: 60 seconds.
- Renewal: every 20 seconds.
- Renewal stops during process drain.
- Two consecutive renewal errors or one ownership mismatch cancels that local execution attempt.
- Every side-effect dispatch and checkpoint commit checks the live attempt fence.
- A stale worker may finish local computation but may not invoke another external operation, commit a checkpoint, publish a result, or mark the command complete.
- Lease expiry uses aware UTC for process-independent persistence. The
  coordinator also compares wall and monotonic deltas; a backward wall jump
  over one second or a heartbeat/suspend gap over 20 seconds marks every local
  lease suspect, blocks new dispatch/effects, cancels the local attempts, and
  runs normal claim recovery before work continues.
- `<storage-root>/control/runtime-worker.lock` ensures only one desktop worker process for the root. A second process exits with a typed startup error.

Lease renewals are disposable SQLite updates, not JSONL heartbeat records. The initial claim/attempt and final outcome remain canonical per AC2. On a clean worker restart, exclusive singleton ownership lets reconciliation release prior-instance claims immediately; it does not wait for wall-clock expiry.

### Desktop scheduler

The existing PostgreSQL `RuntimeWorker.run_forever` path remains unchanged. Under the desktop file backend, `DesktopWorkerCoordinator`:

- fills up to `max_parallel_runs` execution slots and reserves one separate
  control slot for cancellation/approval-resolution commands;
- claims in priority order: cancellation, approval resolution, recovery, new run, maintenance;
- runs a lease heartbeat beside each claimed task;
- maintains a process-local cancellation registry backed by persisted run status;
- exposes a public `RuntimeWorker.handle_claim()` seam rather than calling private methods; and
- finalizes through `RuntimeQueueLeasePort`.

The control slot may only persist/signal control state; it cannot run model,
tool, subagent, Monty, or remote work. Approval resolution hands continuation
to an execution slot. This guarantees cancellation can execute while every run
slot is occupied without changing hosted scheduling.

## Cancellation contract

The existing cancel HTTP response and event types remain unchanged.

### Durable fence

`AgentRunStatus.CANCELLING` is the durable cancellation fence. For the file
backend, the run compare-and-swap, deterministic `run_cancelling` event, and
deterministic cancel command commit in one AC2 immediate batch before the API
returns. A retry returns that committed result. Startup still repairs a missing
deterministic cancel command in imported/pre-AC3 partial state, but new AC3
writes cannot create that split state. The PostgreSQL coordinator keeps its
existing transaction/queue behavior.

### Active propagation

Each active desktop attempt receives a `RunCancellationToken` backed by:

- process-local event set by a concurrently handled cancel command;
- a 100 ms persisted-status watcher; and
- lease-loss/drain signals.

The token is passed to streaming model calls, tool dispatcher, subagent launcher, checkpoint writer, and future AC6/AC7 adapters. Required behavior:

- abort model HTTP/SSE promptly;
- stop consuming model frames;
- deny any new tool/subagent/execution dispatch;
- request cooperative cancellation from active children;
- persist a completed external-effect receipt if it arrives;
- otherwise persist `effect_outcome=uncertain` and never auto-retry that effect; and
- atomically terminalize run/children/events as cancelled once active work settles or reaches its existing hard timeout.

A non-cooperative in-process call does not authorize killing the whole worker immediately. It remains bounded by its existing operation timeout; the run stays `cancelling`. Supervisor process kill is reserved for app shutdown or crash-loop policy.

## Recovery planner and commands

Recovery planning is a pure fold over committed AC2 records and verified checkpoint/artifact evidence. It does not inspect SQLite alone.

### Recovery command

```python
class RecoveryScope(StrEnum):
    PARENT_RUN = "parent_run"
    SUBAGENT = "subagent"


class RuntimeRecoveryCommandV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    command_id: Annotated[str, Field(min_length=1, max_length=256)]
    recovery_id: UUID
    recovery_epoch: Annotated[int, Field(ge=1)]
    scope: RecoveryScope
    conversation_id: Annotated[str, Field(min_length=1, max_length=128)]
    run_id: Annotated[str, Field(min_length=1, max_length=128)]
    task_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    checkpoint_ref_id: UUID
    expected_run_status: AgentRunStatus
    expected_task_status: AsyncTaskStatus | None = None
    created_at: AwareDatetime
```

`command_id` is the lowercase hex form of AC2's UUIDv5 algorithm with record
kind `runtime.recovery_command` and logical parts
`[run_id, scope, task_id-or-null, recovery_epoch]`, matching the existing queue's
string command-ID contract. Replanning the same epoch cannot enqueue a
duplicate.

Semantic validation requires parent scope to have null `task_id` and
`expected_task_status`, subagent scope to have both, the checkpoint reference
to match the same conversation/run/task owner, and expected statuses to be
nonterminal. Identity/status values are loaded from committed server records,
not a renderer or model payload.

Recovery enqueue is an interface-segregated desktop capability rather than a
new method on every queue implementation:

```python
class RuntimeRecoveryQueuePort(Protocol):
    async def enqueue_recovery(
        self, command: RuntimeRecoveryCommandV1
    ) -> None: ...
```

`RuntimePorts.recovery_queue` is this file adapter for the desktop predicate
and `None` for PostgreSQL/in-memory paths. The planner fails startup if the
desktop file backend lacks it. Its command body and transitions use AC2's
canonical queue record, and existing `claim_next()` returns the ordinary
`RuntimeWorkerClaim` envelope with this registered command type.

The planner allocates `recovery_epoch = previous_max + 1` under the session
writer lock and commits the recovery-plan record plus queue command in one AC2
batch. If that epoch already has a nonterminal command, it reuses it rather than
incrementing. `recovery_id` uses AC2's store-scoped UUIDv5 function over
`[run_id, scope, task_id-or-null, recovery_epoch, "recovery"]`.

### Execution attempt

```python
class ExecutionAttemptV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    attempt_id: UUID
    run_id: Annotated[str, Field(min_length=1, max_length=128)]
    task_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    recovery_epoch: Annotated[int, Field(ge=0)]
    checkpoint_ref_id: UUID | None = None
    claim_id: Annotated[str, Field(min_length=1, max_length=256)]
    worker_instance_id: UUID
    invocation_fence: Annotated[int, Field(ge=0)]
    started_at: AwareDatetime
```

Only the current attempt may advance status, invocation fence, checkpoint state, or terminal outcome. Every such write includes the expected attempt/recovery epoch.
`attempt_id` uses the same store-scoped UUIDv5 function over
`[command_id, claim_id, worker_instance_id]`; normal
first execution uses `recovery_epoch=0`, while recovery commands use their
positive epoch.
Epoch zero forbids a recovery-selected checkpoint; a positive epoch requires
one. The claim, worker, run/task scope, and checkpoint selection must all name
the same current owner before this attempt record commits.

### Startup algorithm

After obtaining the singleton worker lock:

1. validate/repair AC2 and complete queue projection;
2. mark claims from prior worker instances recoverable while preserving attempts;
3. enumerate all nonterminal runs and their task trees;
4. validate task lineage is acyclic and all checkpoint artifacts exist/match;
5. reconcile terminal evidence first;
6. process `CANCELLING`;
7. restore valid approval waits;
8. enqueue independently scheduled child recovery in child-before-waiting-parent
   dependency order as described below;
9. enqueue parent recovery only when required child results are terminal/available;
10. ensure every queued run has one durable command; and
11. write a reconciliation summary and worker readiness.

No execution claim is taken before reconciliation completes.

## Run-state reconciliation

### `QUEUED`

- If a matching `run_requested` command is pending/retry/claimed-by-prior-worker, make it claimable.
- If no command exists, enqueue one with its original immutable runtime context and deterministic ID.
- If the user message or runtime context is missing/corrupt, fail with `recovery_run_input_missing`; do not synthesize input.

### `RUNNING`

Apply evidence in this order:

1. A coherent committed terminal run status and matching terminal event win;
   repair only stale projection/queue state.
2. A committed final assistant message plus `final_response`, with no later nonterminal work, completes the run by appending the deterministic missing `run_completed` terminal batch. It does not call the model again.
3. Exactly one of final assistant message or `final_response`, a terminal event
   while the canonical run status remains nonterminal, or conflicting terminal
   evidence fails `recovery_terminal_evidence_incomplete`; it never resumes
   past ambiguous model completion.
4. A provider/tool invocation with a committed receipt and matching
   result/pending write is folded into the invocation ledger before resume.
5. An unreceipted model call or external write/destructive invocation with no
   registered provider idempotency/query evidence fails
   `recovery_model_call_uncertain` or `recovery_side_effect_uncertain`.
6. Otherwise select the latest valid resumable (`READY` or `CONSUMED`)
   safe-point checkpoint and enqueue recovery.
7. With no valid safe point, fail `recovery_checkpoint_missing`.

Regular crash continuation invokes pinned LangGraph with `input=None` and the exact `(thread_id, checkpoint_ns, checkpoint_id)` config. It never invokes with original messages. Approval continuation uses `Command(resume=...)`.

### `WAITING_FOR_APPROVAL`

- Require a valid `WAITING_FOR_APPROVAL` checkpoint and coherent pending approval batch/items.
- If one or more decisions remain pending, leave the run paused and make no execution command claimable.
- If all decisions are committed but the approval-resolution command is missing, enqueue it deterministically.
- Compare-and-swap the approval batch `PENDING -> RESUMING`; only the winner selects the checkpoint.
- On successful pause-again or terminal completion, mark the batch resolved as today.
- Missing checkpoint/batch evidence fails `recovery_approval_state_invalid`; it never assumes approval.

### `CANCELLING`

- Ensure a deterministic cancel command/fence.
- Do not select or resume any checkpoint.
- Cancel nonterminal child tasks, reconcile known effect receipts, append one terminal cancellation batch, and complete stale run/recovery commands.

### Terminal run

For `CANCELLED`, `COMPLETED`, `FAILED`, or `TIMED_OUT`:

- never resume;
- complete/dead-letter duplicate commands idempotently;
- reconcile nonterminal children as specified below;
- ensure exactly one terminal lifecycle event using deterministic event IDs; and
- never rewrite an existing terminal status to a different terminal status without explicit manual repair.

## Parent and subagent recovery

### Two subagent forms

1. **Nested LangGraph/Deep Agents subgraphs:** checkpoints share the parent graph and have a LangGraph checkpoint namespace. Parent graph recovery owns their continuation. They are not separately enqueued.
2. **Persisted asynchronous tasks (`AsyncTaskRecord`):** each has its own `thread_id`, `langgraph_run_id`, task JSONL, checkpoint refs, attempt, and optional recovery command.

The task record explicitly marks its recovery owner so a task cannot be treated as both forms.

### Ordering

- Validate `parent_task_id` as an acyclic tree with maximum depth 32, 10,000
  tasks per run, and 1,000 direct children per task; excess is a typed
  non-retryable recovery failure.
- For an independently scheduled child that was RUNNING, recover that child before the parent checkpoint that waits for its result.
- Siblings may recover concurrently within run and global concurrency limits.
- A child result is committed with a stable `result_id` before the parent invocation ledger advances.
- Parent resume consumes a result by compare-and-swap on `(run_id, task_id, result_id)`.
- A completed child result is never regenerated because its terminal event was missed.

### Terminal reconciliation

- If task terminal status and committed result/terminal event agree, update only stale projections.
- If a valid result and terminal child event exist but task status is nonterminal, mark it completed without rerunning.
- If parent is terminal and child is nonterminal, terminalize the child:
  - parent cancelled → child cancelled;
  - parent timed out → child timed out;
  - parent failed → child failed with parent-terminal reason;
  - parent completed with no child result → child cancelled with `parent_completed_without_child_consumption`.
- If a child is terminal while parent is recoverable, preserve the child and resume the parent from its next safe point.
- Conflicting terminal evidence or a lineage cycle fails the affected run and writes a recovery audit record; it is not resolved by timestamp order.

Terminal state/event/result/queue completion for one scope is one AC2 committed batch. Deterministic logical IDs make replay and repeated reconciliation idempotent.

## Persistence and recovery invariants

1. SQLite, ready files, notify files, and renderer cursors are never sufficient recovery evidence.
2. A checkpoint reference is usable only after AC2 commit and AC4 digest verification.
3. A safe point includes all external invocation receipts through its `invocation_fence`.
4. A selected checkpoint can be owned by one recovery epoch/attempt.
5. Starting any model-provider/tool dispatch requires a committed invocation intent and current attempt/lease/cancellation fences.
6. Completing a provider/tool invocation requires a committed receipt and
   matching result/pending write before the next checkpoint.
7. A provider idempotency key is derived from stable invocation identity, never a worker attempt.
8. Tool reads may be retried; model calls and write/destructive effects require
   a committed result or provider idempotency/query evidence and otherwise fail
   closed.
9. One terminal status and terminal event set exists per run/task.
10. Event replay remains contiguous by existing per-run `sequence_no` across recovery attempts.
11. Shutdown/crash cannot turn `CANCELLING` back into `RUNNING`.
12. A newer unsupported checkpoint version is read-only diagnostics data, not an invitation to rerun.

## Trust and security

### Identity and authorization

- Renderer request identity is untrusted. Electron main attaches the bearer, and facade derives org/user/scope.
- Recovery uses persisted server-derived runtime context and revalidates current policy before any new tool/connector operation.
- Approval resume requires the persisted verified decision and current authorization; a checkpoint never embeds an authority that bypasses policy.
- Revoked grants, paused connectors, budget exhaustion, and expired approvals are re-evaluated on resume.

### Checkpoint loading

- Verify AC1 path key, AC4 artifact digest/size/media type, serializer/engine/runtime versions, run/task/thread/namespace, parent chain, and invocation fence.
- Enforce 32 MiB LangGraph payload and AC6's stricter Monty limit.
- Never use pickle, marshal, `eval`, `exec`, dynamic imports, or model-controlled class names.
- Decode into allowlisted data/message types only.
- Reject provider/OAuth/broker tokens, raw environment values, secret headers,
  and token-vault material before serialization; resume resolves current
  credentials through existing server-owned providers.
- Checkpoint bytes are not logged, indexed, returned over HTTP, or sent to Electron.

### Process and notification safety

- Worker receives a worker-audience broker token; API receives an API-audience token.
- Ready/notify files contain no secret, bearer, host path, user text, or raw identity.
- Notification content cannot cause event delivery without canonical replay and authorization.
- A forged ready file cannot spawn/authorize a worker; supervisor validates live child, boot ID, PID, and store ID.
- Singleton and session locks are OS locks; PID text never breaks a live lock.

### Side effects

Recovery does not claim exactly once where a provider cannot support it. The
server-owned model/tool invocation registry selects `InvocationRecoveryMode`:

- `SAFE_READ`: retry only after current policy/authorization revalidation;
- `PROVIDER_IDEMPOTENCY`: retry only with the exact original stable provider
  key;
- `PROVIDER_STATUS_QUERY`: query through the registered adapter and retry only
  when authoritative evidence says the operation did not occur; and
- `NEVER_AUTO_RETRY`: require a committed receipt, otherwise fail for user
  review.

`SAFE_READ` is never assigned to a model call. No “best effort” duplicate
provider call or write is permitted.

## Observability and audit

### Structured logs

Required events:

- `desktop_worker.starting`
- `desktop_worker.ready`
- `desktop_worker.draining`
- `desktop_worker.crashed`
- `desktop_worker.crash_loop`
- `file_notify.published`
- `file_notify.dropped`
- `file_notify.woke_waiter`
- `runtime_claim.renewed`
- `runtime_claim.lost`
- `runtime_recovery.started`
- `runtime_recovery.planned`
- `runtime_recovery.resumed`
- `runtime_recovery.completed`
- `runtime_recovery.failed`
- `runtime_recovery.model_call_uncertain`
- `runtime_recovery.side_effect_uncertain`
- `runtime_invocation.result_replayed`
- `runtime_checkpoint.committed`
- `runtime_checkpoint.invalid`
- `runtime_cancellation.observed`
- `runtime_terminal.reconciled`

Allowed fields are boot/worker/recovery/attempt IDs, hashed run/task/conversation IDs, checkpoint ordinal/ref ID, sequence/queue counts, status/reason code, duration, and bounded byte counts. Logs exclude checkpoint bytes, prompts/messages, tool arguments/results, physical paths, raw notification identifiers, tokens, and provider receipts.

### Metrics

- worker boot/reconciliation/readiness duration;
- worker restarts/crash-loop count;
- queue depth/oldest age, claim/renew/lost/recovered counts;
- cancellation fence-to-observation and fence-to-terminal latency;
- notification publish/drop/wake and SSE fallback-poll count;
- SSE reconnect/replay count, replayed events, gap/duplicate rejection;
- checkpoint put/get bytes/latency and invalid/incompatible/orphan counts;
- recovery decisions by state/outcome/reason;
- model/tool invocation recovery by bounded kind/mode/outcome;
- parent/subagent resumed/terminal-reconciled counts; and
- uncertain-side-effect failures.

Labels are bounded enums/versions/platforms only.

### Release SLOs and stop conditions

On the packaged reference corpus (100 sessions, 100,000 committed records, 100
recoverable commands):

- clean worker boot through ready: p95 at most 5 seconds;
- forced full queue-projection rebuild plus reconciliation: p95 at most 30
  seconds;
- committed event to API waiter wake with file hints: p95 at most 150 ms and
  p99 at most 750 ms;
- with every hint dropped, committed event discoverable by SSE replay: p99 at
  most 1 second; and
- cancellation fence to active token observation: p95 at most 250 ms and p99 at
  most 1 second, excluding the separately measured bounded completion of a
  non-cooperative operation.

Any event loss/duplication, automatic uncertain-effect replay, missed
cancellation fence, p99 over twice these latency bounds in two consecutive
release-candidate runs, or checkpoint compatibility failure stops rollout.

### Product audit

Audit:

- worker startup reconciliation summary;
- automatic run/subagent resume;
- invalid/missing checkpoint;
- terminal repair;
- cancellation after crash;
- uncertain model call fail-closed or evidence-backed result replay;
- uncertain side-effect fail-closed;
- manual retry/recovery action; and
- checkpoint compatibility migration.

Each includes actor (`system` for startup), verified user/org scope where applicable, hashed resource, prior/new status, checkpoint/recovery epoch, evidence class, outcome, and correlation ID. It never includes checkpoint content or external payloads.

Local structured logs are not a substitute for immutable/exportable audit or SIEM integration.

## Testing strategy

### Unit tests

- Strict Pydantic validation for notifications, ready records, checkpoint refs/transitions, recovery commands, and attempts.
- Checkpoint owner/task/engine/payload/artifact compatibility rules.
- Descending checkpoint fallback accepts an older ref only when the
  post-fence invocation ledger is fully deterministic.
- Model/tool invocation-kind, recovery-mode, result-link, and atomic
  pending-write/receipt validation.
- Recovery planner matrices for every run/task/approval/side-effect state.
- Deterministic command/event/result IDs and compare-and-swap transitions.
- Parent-task cycle/depth detection and topological recovery order.
- Notification key/path validation, coalescing, stale state, and dropped-hint behavior.
- Lease renew/loss/drain timing with a fake monotonic clock.
- Cancellation token propagation and no-dispatch-after-fence.
- Renderer reducer rejects malformed, duplicate-different, regressing, or gapped events.
- Composer stays mounted across composer-mounted modes (Studio ↔ Auto) and is hidden in Focus per phase-2 `2D`; the thread stays mounted across all modes.
- Desktop attachment mapping, item/run byte limits, submit failure retention,
  and byte absence from routes/KV/log/IPC diagnostics.

### LangGraph and port-contract tests

- Run the full pinned `BaseCheckpointSaver` sync/async contract.
- Round-trip checkpoint state, metadata, channel versions, namespaces, parent configs, pending writes, task paths, and list filters.
- Golden checkpoints from current and immediately prior supported serializer/runtime versions.
- Unknown extension tag, pickle payload, corruption, digest mismatch, oversize, wrong scope, and parent-cycle rejection.
- `CheckpointJournalPort`, `CheckpointBlobPort`,
  `InvocationRecoveryJournalPort`, `RuntimeRecoveryQueuePort`, and
  `RuntimeQueueLeasePort` fake/file-adapter conformance.
- Verify PostgreSQL `recovery_queue=None` and `queue_lease=None` retain current
  queue methods/behavior.

### Integration tests

- Packaged-style API + separate worker + facade + renderer transport: create conversation, send, stream, complete, reload messages.
- Submit a desktop attachment through the existing run route, verify AC4 owns
  the only durable bytes/reference, and verify restart loses only an unsent
  renderer attachment.
- Desktop uses only facade URL; direct internal service access is denied/not attempted.
- Worker event append updates file notify and wakes API SSE below 250 ms median while replay remains complete.
- Delete every notify file during a run; 500 ms poll fallback still delivers all events in order.
- Approval pauses, both processes restart, decision is submitted, and the exact LangGraph checkpoint resumes once.
- Desktop approval callbacks map projected diff IDs to canonical approval IDs,
  send one decision, reject stale mappings, and settle only from replay.
- A committed model pending write/receipt is consumed after restart without a
  second provider call; an unreceipted model call fails closed when its
  provider has no registered recovery evidence.
- Parent plus nested and independent subagents restart and reconcile with no duplicate result.
- Cancellation runs concurrently with an active run and prevents the next tool dispatch.
- Renderer reload recovers `latest_run_id`, replays, and follows without duplicate UI entries.
- Studio/Auto preserve composer draft, attachments, and scroll; entering/leaving Focus hides/restores the composer per phase-2 `2D` while preserving the draft; exactly one SSE subscription persists across all modes.

### Crash-injection tests

Kill API, worker, Electron main, or the whole app:

- before/after run enqueue;
- after queue claim, before handler;
- during model stream before/after an event commit;
- before/after model intent, provider dispatch, pending-write/result commit, and
  receipt;
- before/after checkpoint artifact commit;
- before/after checkpoint-reference commit;
- after external-effect intent, dispatch, receipt, result, and checkpoint;
- before/after approval checkpoint and decision command;
- while parent/child tasks are in every lifecycle state;
- during lease renewal and after lease theft;
- after final message but before terminal event;
- after terminal event but before queue completion;
- while cancellation is requested/observed/settling;
- before/after worker ready file; and
- during graceful drain and forced kill.

On restart assert no original-input replay, no duplicate event/result/terminal, correct side-effect decision, contiguous SSE, one claim owner, and deterministic terminal status.

### Adversarial tests

- Forged checkpoint run/task/thread/namespace, artifact kind, serializer, version, digest, size, parent, and invocation fence.
- Pickle opcodes and executable object reducers hidden in purported checkpoint bytes.
- Checkpoint/dependency bombs: deep nesting, cycles, huge maps/integers/strings, non-finite values, unknown tags.
- Secret-token/header/environment fixtures are rejected before checkpoint
  serialization and absent from artifacts, JSONL, logs, and diagnostics.
- Forged notification/ready files, symlink/reparse swaps, stale boot IDs/PIDs, notification storms, and sharing violations.
- Lease rollback/clock jumps, stale-worker finalization, duplicate recovery commands, and two worker processes.
- Caller-forged org/user/role/scope and approval actor.
- Cancellation races with terminal completion and external writes.
- Non-idempotent effect without receipt proves fail-closed and no retry.
- Forged model receipt/result links and model calls mislabeled as safe reads.
- Malformed SSE, duplicate IDs with different bytes, sequence rewind/gap, reconnect storm, and renderer route churn.

### macOS tests

- arm64 and x64 packaged runtime: separate worker spawn, broker audience, singleton lock, file notify, sleep/wake, app force-quit, relaunch/recovery, and graceful quit.
- Network transition during model stream and machine sleep past lease duration.
- Code signing/notarization includes the worker entrypoint and checkpoint dependencies.
- Case-insensitive APFS and owner-only checkpoint/artifact permissions.

### Windows tests

- x64 packaged runtime: `python.exe -m runtime_worker`, worker lock, atomic ready/notify replacement under antivirus contention, DACLs, abrupt Task Manager termination, relaunch/recovery, and clean handle release.
- Sleep/hibernate past lease, clock correction, long path, reparse-point, and file-sharing scenarios.
- Installer/signing/SBOM includes checkpoint serializer and no unapproved executable deserializer.

### Web regression tests

- Browser frontend behavior and bundles are unchanged; no desktop controller is imported by web.
- Shared chat-surface additive props preserve existing snapshots and call sites.
- PostgreSQL API/worker integration still uses PostgreSQL event notification and current queue scheduling.
- Existing in-memory test worker remains available only for its current debug/test profiles.
- Facade OpenAPI and forwarding snapshots for every listed route are unchanged.
- SSE `event`, `id`, and `data` frames remain byte-compatible; reconnect still uses `after_sequence`.
- `packages/api-types` requires no desktop-specific wire model.

## Rollout and backout

### Rollout

**AC3a ships first and independently.** Mounting the controlled Chats destination/thread/composer and wiring end-to-end send/stream/cancel/approval on the **existing PostgreSQL run/SSE path** (the numbered stage below that "wires the controlled Chats destination") is carved out as AC3a and sequenced ahead of AC2/AC4 and the rest of this list. It is not gated behind any file-store work. The numbered stages below are the **AC3b** durable-worker/recovery rollout and each numbered stage is its own PR (see README "PR decomposition").

1. Land generic checkpoint/recovery contracts, pure planner, saver contract tests, and desktop scheduler behind disabled flags.
2. Implement file notification and prove replay completeness with all hints dropped.
3. Implement artifact-backed LangGraph saver and pass compatibility/security tests.
4. During AC2 legacy import, mark checkpoint capability on each run. A legacy
   `QUEUED` run may execute because it never started; legacy
   `RUNNING|WAITING_FOR_APPROVAL` without a durable ref is terminalized with
   `legacy_checkpoint_unavailable` and preserved evidence; legacy
   `CANCELLING` completes cancellation. Never synthesize a checkpoint.
5. Start the real worker in internal packaged builds while retaining UI placeholder; verify readiness, leases, crash loops, and no in-process worker.
6. Wire the controlled Chats destination/thread/composer and end-to-end send/stream/cancel.
7. Enable restart recovery for read-only/no-side-effect runs.
8. Enable idempotent external-effect recovery after provider evidence tests.
9. Keep non-idempotent uncertain effects fail-closed.
10. Expand cohorts only after macOS/Windows crash matrices and notification/cancellation SLOs pass.

Server-authoritative gates:

```text
COPILOT_DESKTOP_AGENT_RUNTIME_V1
RUNTIME_ENABLE_DURABLE_CHECKPOINTS
RUNTIME_ENABLE_DESKTOP_RECOVERY
```

All require the AC1 activation predicate, `RUNTIME_STORE_BACKEND=file`, `RUNTIME_EVENT_BUS_BACKEND=file_notify`, and AC4 health. The renderer cannot enable them.

### Backout

- Disable new run creation first; allow active safe attempts to terminalize or cancel.
- Stop facade, worker, and API in order and preserve AC2/AC4 data.
- UI-only backout may return to a diagnostics/read-only screen while the file runtime remains canonical.
- Recovery backout may run the same separate worker with automatic resume disabled; nonterminal runs are surfaced for cancel/manual diagnostics, never replayed from original input.
- Store backout follows AC2's verified file-to-fresh-PostgreSQL export. Do not point an old binary at stale retained data.
- A binary that cannot read the current checkpoint version opens affected runs read-only. It does not downgrade artifacts or refs.
- Do not re-enable the in-process packaged worker or switch to in-memory state as a fallback.

## Acceptance criteria and definition of done

AC3 is done only when all are true:

- [ ] Desktop no longer renders `DesktopPlaceholder`.
- [ ] `ChatsDestination` mounts a real controlled `ThreadCanvas`, `TcChat`, and shared `Composer`.
- [ ] Conversation list/messages/send/cancel/approval use only existing facade routes and verified identity.
- [ ] Composer is wired through phase-2 `2D`'s `onSend(text)` seam, cancellable, and preserves draft state across composer-mounted modes (Studio ↔ Auto); Focus hides it per `2D`.
- [ ] Exactly one desktop SSE subscription exists per active run and reconnects from the highest validated sequence.
- [ ] A separately supervised `python -m runtime_worker` starts, becomes ready after reconciliation, restarts safely, and drains on quit.
- [ ] Packaged API has `RUNTIME_START_IN_PROCESS_WORKER=false`.
- [ ] File notification wakes cross-process SSE without PostgreSQL; dropping every hint still yields complete replay.
- [ ] Desktop runtime never uses `InMemorySaver`.
- [ ] Artifact-backed LangGraph saver passes pinned sync/async contract, crash, compatibility, and no-pickle security tests.
- [ ] Canonical checkpoint references support parent, nested/async subagent, pending-write, and future Monty snapshot identities.
- [ ] Artifact bytes commit before refs; invalid/dangling refs never resume.
- [ ] File queue claims renew every 20 seconds, expire safely, and finalize by claim compare-and-swap.
- [ ] Cancellation is observed during active execution and no new side effect starts after its durable fence.
- [ ] Startup reconciles every run status, approval state, parent/subagent tree, queue claim, checkpoint, final message, and terminal event deterministically.
- [ ] Model/tool dispatches use the durable invocation ledger; committed model
      results replay from pending writes and unreceipted calls fail closed unless
      registered provider recovery evidence exists.
- [ ] Automatic recovery never reruns original input, an uncertain model call,
      or an uncertain non-idempotent side effect.
- [ ] Parent/subagent results and terminal events are idempotent across repeated crashes.
- [ ] macOS and Windows packaged crash matrices pass.
- [ ] Logs/audit are redacted and required metrics/runbooks exist.
- [ ] The accepted lead implementation spec maps AI runtime, desktop, shared
      UI, pinned serializer versions, and all acceptance evidence.
- [ ] Facade, SSE, PostgreSQL worker, web UI, and API type contracts are unchanged.

## Critical files

### Runtime contracts, checkpointing, and recovery

- `services/ai-backend/src/agent_runtime/execution/runtime.py`
- `services/ai-backend/src/agent_runtime/execution/factory.py`
- `services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py`
- `services/ai-backend/src/agent_runtime/execution/checkpoints/contracts.py`
- `services/ai-backend/src/agent_runtime/execution/checkpoints/ports.py`
- `services/ai-backend/src/agent_runtime/execution/checkpoints/serializer.py`
- `services/ai-backend/src/agent_runtime/execution/checkpoints/artifact_saver.py`
- `services/ai-backend/src/agent_runtime/recovery/planner.py`
- `services/ai-backend/src/agent_runtime/recovery/contracts.py`
- `services/ai-backend/src/agent_runtime/recovery/invocations.py`
- `services/ai-backend/src/agent_runtime/api/run_coordinator.py`
- `services/ai-backend/src/agent_runtime/api/ports.py`
- `services/ai-backend/src/agent_runtime/persistence/records/outbox.py`
- `services/ai-backend/src/agent_runtime/persistence/records/recovery.py`
- `services/ai-backend/src/agent_runtime/persistence/records/invocations.py`
- `services/ai-backend/src/agent_runtime/persistence/records/subagents.py`

### File adapters and SSE

- `services/ai-backend/src/runtime_adapters/factory.py`
- `services/ai-backend/src/runtime_adapters/file/checkpoint_journal.py`
- `services/ai-backend/src/runtime_adapters/file/checkpoint_blob.py`
- `services/ai-backend/src/runtime_adapters/file/invocation_journal.py`
- `services/ai-backend/src/runtime_adapters/file/queue_lease.py`
- `services/ai-backend/src/runtime_adapters/file/notification.py`
- `services/ai-backend/src/runtime_api/sse/event_bus.py`
- `services/ai-backend/src/runtime_api/sse/file_notify.py`
- `services/ai-backend/src/runtime_api/sse/adapter.py`
- `services/ai-backend/src/runtime_api/app.py`

### Worker

- `services/ai-backend/src/runtime_worker/__main__.py`
- `services/ai-backend/src/runtime_worker/loop.py`
- `services/ai-backend/src/runtime_worker/desktop_coordinator.py`
- `services/ai-backend/src/runtime_worker/lease.py`
- `services/ai-backend/src/runtime_worker/cancellation.py`
- `services/ai-backend/src/runtime_worker/reconciliation.py`
- `services/ai-backend/src/runtime_worker/invocation_recovery.py`
- `services/ai-backend/src/runtime_worker/streaming_executor.py`
- `services/ai-backend/src/runtime_worker/handlers/run.py`
- `services/ai-backend/src/runtime_worker/handlers/approval.py`
- `services/ai-backend/src/runtime_worker/handlers/cancel.py`

### Desktop supervisor

- `apps/desktop/main/services/runtime-paths.ts`
- `apps/desktop/main/services/service-env.ts`
- `apps/desktop/main/services/python-service.ts`
- `apps/desktop/main/services/supervisor.ts`
- `apps/desktop/main/services/desktop-supervisor.ts`
- `apps/desktop/main/services/health.ts`

### Desktop and shared chat UI

- `apps/desktop/renderer/bootstrap.tsx`
- `apps/desktop/renderer/DesktopPlaceholder.tsx` (delete)
- `apps/desktop/renderer/DesktopDestinationFallback.tsx`
- `apps/desktop/renderer/DesktopChatsDestination.tsx`
- `apps/desktop/renderer/DesktopAttachmentAdapter.ts`
- `apps/desktop/renderer/useDesktopThreadController.ts`
- `packages/chat-surface/src/destinations/chats/ChatsDestination.tsx`
- `packages/chat-surface/src/destinations/chats/ChatsSidebar.tsx`
- `packages/chat-surface/src/thread-canvas/ThreadCanvas.tsx`
- `packages/chat-surface/src/thread-canvas/TcChat.tsx`
- `packages/chat-surface/src/thread-canvas/TcSwimlanes.tsx`
- `packages/chat-transport/src/ipc/IpcTransport.ts`
- `apps/desktop/main/transport-bridge.ts`

### Tests and runbooks

- `services/ai-backend/tests/contract/test_langgraph_checkpoint_saver.py`
- `services/ai-backend/tests/contract/test_runtime_queue_lease.py`
- `services/ai-backend/tests/unit/agent_runtime/recovery/test_planner.py`
- `services/ai-backend/tests/unit/agent_runtime/recovery/test_invocations.py`
- `services/ai-backend/tests/unit/runtime_api/sse/test_file_notify.py`
- `services/ai-backend/tests/unit/runtime_worker/test_desktop_coordinator.py`
- `services/ai-backend/tests/unit/runtime_worker/test_reconciliation.py`
- `services/ai-backend/tests/integration/test_desktop_runtime_recovery.py`
- `services/ai-backend/tests/integration/test_desktop_checkpoint_resume.py`
- `apps/desktop/main/services/supervisor.test.ts`
- `apps/desktop/main/services/service-env.test.ts`
- `apps/desktop/renderer/bootstrap.test.tsx`
- `apps/desktop/renderer/DesktopChatsDestination.test.tsx`
- `apps/desktop/renderer/DesktopAttachmentAdapter.test.ts`
- `packages/chat-surface/src/thread-canvas/ThreadCanvas.test.tsx`
- `packages/chat-surface/src/thread-canvas/TcChat.test.tsx`
- `packages/chat-surface/src/thread-canvas/TcSwimlanes.test.tsx`
- `services/ai-backend/docs/specs/desktop-agent-capabilities/ac3-runtime-recovery.md`
- `docs/operations/desktop-runtime-recovery.md`
- `docs/operations/desktop-checkpoint-compatibility.md`

## Unresolved risks (implementation choices closed)

There is no open implementation choice in this PRD. Remaining risks have fixed outcomes:

- **Checkpoint serializer incompatibility:** block the upgrade or ship an explicit offline upcaster. Never enable pickle or original-input replay.
- **Uncertain model call:** replay only a committed pending-write result or use
  registered provider idempotency/query evidence; otherwise fail the run rather
  than risk duplicate billing or divergent output.
- **Uncertain external write:** fail the run for user review. Never guess, duplicate, or label it successful.
- **File notification loss:** replay on the 500 ms fallback. Never promote notify state to canonical.
- **Worker lease loss:** cancel the local attempt and reject subsequent writes/finalization. Never extend ownership from stale local state.
- **Non-cooperative cancellation:** wait for the existing bounded operation timeout and keep `cancelling`; do not start more work or kill unrelated runs automatically.
- **Renderer reconnect storms:** use bounded exponential backoff with jitter (250 ms initial, 5 s cap) and REST replay. Never reset the cursor to “latest.”
- **Large/corrupt task trees:** enforce depth/size bounds and fail the affected run. Never recursively trust persisted lineage.
- **AC4 unavailable:** fail desktop runtime readiness because durable checkpoints cannot be promised. Do not substitute in-memory checkpointing.
- **Crash-loop threshold reached:** surface the existing fatal state and preserve data. Do not silently keep restarting.
- **Shared chat-surface regression:** additive controlled props and web regression tests are mandatory. Do not fork a desktop-only composer or import web app implementation.
