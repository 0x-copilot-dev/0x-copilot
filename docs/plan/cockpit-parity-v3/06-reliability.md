# Streaming reliability on the approval wait — v3 parity plan

> Scope: pure hardening of the run-lifecycle streaming hot path, concentrated on the
> human-in-the-loop approval wait. No new product surface, no new backend event type.
> Evidence source: `docs/audit/flows/run-lifecycle-streaming.md` — findings **F2**
> (worker serial dispatch) and **F7** (no keepalive + no cockpit auto-reconnect),
> plus failure-mode notes at that doc's lines 96, 100, 105, 106.

## Problem statement

A user asks the agent to do something, the agent pauses to ask permission
("Send this email?"), and the Run cockpit is supposed to surface an inline approval
card the moment the run enters `waiting_for_approval`. Today two independent reliability
holes converge on exactly that moment — the one moment where a human is watching and
waiting — and both fail _silently_.

- **The stream goes quiet and can die (F7).** While a run sits in
  `waiting_for_approval` (seconds to minutes, however long the human takes), the run
  SSE stream writes **zero bytes**: `RuntimeSseAdapter.stream` emits no keepalive in
  follow mode (`services/ai-backend/src/runtime_api/sse/adapter.py:41-70`), unlike the
  sibling inbox stream which sends `: keepalive` every 25s
  (`runtime_api/sse/inbox_adapter.py:26-30,52-60`). Any intermediary — the facade's
  own pooled connection, an nginx ingress, a corporate proxy, a laptop sleeping — is
  free to drop an idle connection. When it does, the cockpit side has **no recovery**:
  `runSseStream` returns silently on a clean EOF with no callback
  (`packages/chat-transport/src/web/sse.ts:58-61`), and `useRunSession` only reconnects
  on an explicit user `retry()` (`packages/chat-surface/src/destinations/run/useRunSession.ts:251-256,291-296`).
  The cockpit freezes at `status: "streaming"`, the approval card never arrives, and the
  user stares at a spinner for a decision the backend is _already blocked waiting on_.
- **The Approve click can hang (F2).** Even when the stream is healthy and the user
  clicks Approve, the worker processes commands **serially**: `run_forever` → `run_once`
  claims one command and `await`s the entire run inside that claim before claiming the
  next (`services/ai-backend/src/runtime_worker/loop.py:124-132,170-176`). The
  `RuntimeApprovalResolvedCommand` that carries the resume is enqueued behind whatever
  is currently executing and **cannot be claimed until that run finishes**. In a
  single-worker deployment (the desktop app's embedded runtime, and any 1-replica web
  deploy) the resume starves behind an unrelated long-running run — the user's Approve
  appears to do nothing. The `max_parallel_runs` semaphore that would fix this is dead
  weight on the production path: it is only exercised by `run_until_idle`, which only
  tests call (flow doc F2, lines 114-116; failure-mode line 96).

v3 intent: an approval wait is a _first-class, indefinite_ state. The stream must stay
warm through it, survive a transport blip without the user noticing more than a subtle
"reconnecting" flicker, and the Approve must dispatch out-of-band so it is never queued
behind a full run. This is the difference between "the agent asks permission and waits
patiently" and "the agent asks permission and the UI dies." It matters now because the
approval loop is the core trust primitive of the product — the one place where silent
failure directly erodes user confidence in letting the agent act.

## Functional requirements

- **FR-1 (keepalive).** The run SSE stream MUST emit a periodic SSE **comment** frame
  (`: keepalive\n\n`) while a non-terminal run produces no events, at a fixed interval
  (default 15s), so idle intermediaries do not drop the connection. Applies to
  `follow=true` mode (the only mode reachable through the facade — `follow` is not
  forwarded, `services/backend-facade/src/backend_facade/app.py:1050,1065`). The
  keepalive MUST NOT be emitted after the run reaches a terminal status (the stream
  closes then).
- **FR-2 (keepalive is inert on the wire).** A keepalive frame MUST be a pure comment
  frame — a single line beginning with `:` followed by the blank-line frame terminator
  — carrying no `event:`/`data:`/`id:` field, so it neither dispatches an `onMessage`
  nor advances any client resume cursor. It MUST NOT corrupt event framing for any
  client, current or older.
- **FR-3 (client auto-reconnect).** On stream close OR error for a run that has **not**
  reached a terminal status, `useRunSession` MUST automatically re-subscribe to the
  same run at `?after_sequence=<highest received sequence_no>` (resume without replay),
  without discarding already-projected events and without remounting the cockpit.
- **FR-4 (bounded backoff).** Auto-reconnect MUST use bounded exponential backoff with
  jitter (e.g. 0.5s → 1s → 2s → 4s, cap 10s), reset to the floor on a successful
  reconnect (first event or open after reconnect). It MUST stop retrying once the run
  is terminal or the session is disabled/unmounted.
- **FR-5 (visible-but-subtle reconnecting state).** While a reconnect is in flight the
  session MUST expose a distinct, non-error `"reconnecting"` phase so the cockpit can
  show a subtle indicator (not the full error chrome, not a frozen "streaming").
- **FR-6 (approval resume never starves).** An enqueued `approval_resolved` (or
  `run_cancel_requested`) command MUST become claimable and begin executing while an
  unrelated run is in flight — it MUST NOT be head-of-line-blocked behind a full run in
  a single-worker deployment. Concretely: worker dispatch MUST NOT `await` a run to
  completion inside the claim that would gate the next claim.
- **FR-7 (bounded worker concurrency).** Concurrent dispatch MUST remain bounded by the
  existing `max_parallel_runs` semaphore (default 4, `agent_runtime/settings.py:118`) so
  the fix does not turn one worker into an unbounded fork bomb, and the worker MUST NOT
  claim a command it has no capacity to run (no lock-lease churn on commands it must
  immediately re-park).

## Non-functional requirements

- **NFR-1 (no loss, no dup across reconnect — after_sequence idempotence).** Reconnect
  correctness rests on the existing monotonic per-run `sequence_no` + `?after_sequence`
  cursor (contract row, flow doc line 88). The client already dedupes by `sequence_no`
  (`useRunSession.ts:235-238`) and tracks the high-water mark in `latestSequenceRef`
  (`useRunSession.ts:241-245`); reconnect MUST resume from `latestSequenceRef.current`,
  so the boundary event may be redelivered (dedupe drops it) and no event is skipped.
  The synthetic-heartbeat sequence bug (`adapter.py:56-63`, flow doc line 105) is
  **unreachable** here — `follow` is not forwarded by the facade — and FR-1's keepalive
  is a comment frame with no `id:`, so it can never be mistaken for a cursor.
- **NFR-2 (resume path idempotent).** Reconnect re-issues a GET stream; it is a pure
  read of persisted events and safe to repeat. FR-6's out-of-band dispatch MUST preserve
  the existing approval-resume idempotency — `RuntimeApprovalHandler` resumes a
  LangGraph checkpoint keyed by the run/approval id; re-delivery of the same
  `approval_resolved` command MUST NOT double-resume (guarded today by the queue's
  at-most-once `mark_complete` + the checkpoint being consumed). Do not weaken the
  command-dedup guarantees when moving to task dispatch.
- **NFR-3 (framing integrity).** Per FR-2, the keepalive is `": keepalive\n\n"`. The web
  SSE reader already skips comment lines (`sse.ts:98`: `line.startsWith(":") → continue`)
  and only dispatches when `event === expectedEvent && dataLines.length > 0`
  (`sse.ts:109-112`), so a comment frame is provably inert — this is the contract-safe
  rollout property (older clients ignore it; no api-types bump).
- **NFR-4 (single projection — FR-3.3 honored).** No new SSE subscription and no new
  projector. Reconnect re-subscribes the **one** subscription `useRunSession` already
  owns; the append-only `events` array and every pure selector over it
  (`projectSubagents` / `projectApprovals` / `projectChatMessages` / `useRunSources`)
  simply resume. The `"reconnecting"` phase is derived state on the existing session,
  not a second data source.
- **NFR-5 (single mount — FR-3.9 honored).** Reconnect and the reconnecting indicator
  MUST NOT remount `TcChat` or `ThreadCanvas`. The effect that owns the subscription is
  already keyed on `[transport, activeRunId, enabled, connectNonce]`
  (`useRunSession.ts:256`); reconnect bumps an internal nonce inside that effect's
  lifecycle without changing `activeRunId`, so the reset-on-run-change effect
  (`useRunSession.ts:209-217`) does **not** fire and no state is discarded.
- **NFR-6 (substrate boundary honored).** All timing lives in the hook via
  `setTimeout`/`clearTimeout` — permitted (not in the banned `window`/`fetch`/
  `localStorage`/`EventSource` set). No new persisted pref is required; if the backoff
  parameters ever become user-tunable they go through the `KeyValueStore` port, not bare
  storage. No bare `EventSource`/`fetch` — reconnect goes through the `Transport` port.
- **NFR-7 (a11y).** The reconnecting indicator MUST be announced politely
  (`aria-live="polite"`, `role="status"`) and MUST NOT steal focus from the approval
  card or composer. It is decorative-plus-status, never a modal.
- **NFR-8 (perf).** Keepalive interval (15s) is well under typical idle-proxy timeouts
  (30–60s) and adds negligible bandwidth. Backoff is capped at 10s so a persistent
  outage does not hammer the facade. Task-based worker dispatch stays bounded at
  `max_parallel_runs`; no unbounded task growth.
- **NFR-9 (tests required).** Every FR above ships with the unit/integration coverage
  in the Test plan; no FR merges without a regression guard.

## Architecture & plan

Three independently-shippable layers; each is contract-safe on its own and can merge in
any order (the keepalive is inert to old clients; the client reconnect is a no-op if the
stream never drops; the worker fix is invisible until two commands contend).

### A. Backend — keepalive on the run SSE adapter (FR-1, FR-2)

**File:** `services/ai-backend/src/runtime_api/sse/adapter.py`.

Current `RuntimeSseAdapter.stream` (lines 26-70) replays after the cursor (42-50),
returns on terminal status (51-54), and in follow mode waits on the bus with a 2s
fallback poll (67-68: `await event_bus.wait(run_id, timeout=cls.FALLBACK_POLL_SECONDS)`;
`FALLBACK_POLL_SECONDS = 2.0` at line 24). No frame is emitted across an idle wait.

**Change:** mirror `InboxSseAdapter`'s proven pattern (`inbox_adapter.py:52-60`).

1. Add `HEARTBEAT_INTERVAL_SECONDS = 15.0` next to `FALLBACK_POLL_SECONDS` (line 24).
2. In the follow branch, track the monotonic time of the last frame emitted (real event
   **or** keepalive). After the `event_bus.wait(...)` returns (or the no-bus
   `asyncio.sleep`), if the loop's next replay yields no events **and**
   `monotonic() - last_frame_at >= HEARTBEAT_INTERVAL_SECONDS`, `yield ": keepalive\n\n"`
   and reset `last_frame_at`. Because the fallback poll is 2s, the keepalive fires within
   one poll of the 15s boundary — no separate timer needed. Terminal-status check at
   51-54 continues to short-circuit before any keepalive, so no keepalive is emitted
   after close (FR-1).
   - Simpler equivalent (matches inbox exactly): wrap the wait in
     `asyncio.wait_for(event_bus.wait(run_id, timeout=FALLBACK_POLL_SECONDS),
timeout=HEARTBEAT_INTERVAL_SECONDS)` and `yield ": keepalive\n\n"` on
     `asyncio.TimeoutError`. Prefer whichever keeps the existing 2s missed-NOTIFY
     backstop intact — the elapsed-tracking form is safer because it does not swallow the
     inner 2s poll.
3. Do **not** touch `format_event` (107-115), `heartbeat_event` (72-105), or the
   non-follow branch (55-66) — the non-follow synthetic heartbeat is a separate,
   facade-unreachable path (NFR-1) and is out of scope here.

**Facade:** no code change. `stream_run`
(`services/backend-facade/src/backend_facade/app.py:1046-1089`) already proxies upstream
bytes with `timeout=None` (1067) and yields each chunk through (1080-1088), so comment
frames flow untouched. **Bonus:** a 15s keepalive incidentally bounds F12 (flow doc
line 154-156) — the facade only checks `request.is_disconnected()` on an arriving chunk
(1083), so with keepalives a departed client is now detected within ~15s instead of
"never until the run emits again."

### B. Frontend — auto-reconnect with bounded backoff + reconnecting state (FR-3..FR-5)

**Files:**

- `packages/chat-transport/src/types.ts` — `SseSubscribeOptions` (lines 26-33) currently
  has `onMessage`/`onOpen`/`onError` but **no** close callback. Add
  `readonly onClose?: () => void;` (fired on clean EOF, distinct from `onError`).
- `packages/chat-transport/src/web/sse.ts` — `SseRunnerOptions` (3-12) add `onClose?`.
  In `runSseStream`, the reader loop's clean-EOF branch `if (done) { return; }`
  (60-61) must call `opts.onClose?.()` **before** returning. Guard against calling it
  after an abort (`controller.signal.aborted`), same pattern as the catch at 42/77.
- `packages/chat-transport/src/web/WebTransport.ts` — `subscribeServerSentEvents`
  (78-86) forward `onClose: opts.onClose` alongside the existing `onOpen`/`onError`
  (84-85).
- `packages/chat-transport/src/ipc/IpcTransport.ts` — subscribe (103) currently bridges
  `open`/`message`/`error` IPC kinds (199/204/208). Add a `"closed"`→`onClose` mapping.
  Desktop main `apps/desktop/main/transport-bridge.ts` already emits
  `{ kind: "closed" }` on `unsubscribe` (line 93) but **not** on upstream clean EOF —
  add an `onClose` handler to its `subscribeServerSentEvents` call (55-86) that emits
  `{ kind: "eof" }` (a NEW IPC signal distinct from the client-initiated `"closed"` at
  93, so the renderer does not treat its own `close()` as a server drop). Map `"eof"`→
  `onClose` in `IpcTransport`. (If threading a new IPC kind is deferred, desktop still
  reconnects via `onError` on a hard drop; clean-EOF-only reconnect is the web-first
  win. Call this out in the commit that lands it.)

**Hook:** `packages/chat-surface/src/destinations/run/useRunSession.ts`.

The subscription effect is lines 221-256, keyed `[transport, activeRunId, enabled,
connectNonce]` (256). Reset-on-run-change is a _separate_ effect keyed on `activeRunId`
only (209-217) — critical: reconnect must **not** change `activeRunId` (NFR-5).

1. Add state: `const [isReconnecting, setIsReconnecting] = useState(false);` and a
   backoff bookkeeping ref `const reconnectRef = useRef<{ attempt: number; timer:
ReturnType<typeof setTimeout> | null }>({ attempt: 0, timer: null });`. Add an
   internal `reconnectNonce` state (separate from the user-facing `connectNonce` bumped
   by `retry()` at 294) so an auto-reconnect re-runs the subscription effect the same
   way `retry()` does but without clearing user-error state.
2. Add a `scheduleReconnect` callback: if `!enabled` or the run is terminal
   (`runStatusFromEvents` in `AGENT_RUN_STATUSES` terminal set) → do nothing (FR-4 stop
   condition). Else compute `delay = min(10000, 500 * 2 ** attempt) + jitter`, set
   `isReconnecting(true)`, and `setTimeout` to bump `reconnectNonce` and increment
   `attempt`.
3. Wire the subscription's `onError` (currently 251-253, only `setSseError(err)`) and a
   **new** `onClose` to call `scheduleReconnect()` instead of surfacing a hard error —
   _unless_ the run is already terminal (then a clean EOF is the expected close: do
   nothing). Keep `setSseError` only for the terminal-give-up case so `status` can still
   reach `"error"` after backoff exhaustion is not applicable (backoff is unbounded in
   attempts but capped in delay; a permanent outage keeps retrying quietly — matches the
   legacy `ChatScreen.tsx:693-706` behavior the audit cites as the reference, flow doc
   line 100).
4. On successful reconnect (first `onMessage` or `onOpen` after a reconnect), reset
   `reconnectRef.current.attempt = 0` and `setIsReconnecting(false)` (FR-4).
5. Add the subscription effect's cleanup to also `clearTimeout` any pending reconnect
   timer (prevent a leaked timer firing post-unmount — NFR-6).
6. Extend `RunSessionStatus` (type at 56-61) with `"reconnecting"` and the `status` memo
   (258-279): when `activeRunId !== null && isReconnecting` → `"reconnecting"` (takes
   precedence over `"streaming"`/`"connecting"`, below `"error"`). Add `isReconnecting`
   to the memo deps (272-279). Export the phase so `RunDestination` can render a subtle
   `aria-live="polite"` chip (FR-5, NFR-7) — the chip itself is a tiny presentational
   addition in the host binder / cockpit chrome, not new data.

No api-types change. No new port. No new subscription (NFR-4). `activeRunId` untouched
(NFR-5).

### C. Worker — out-of-band dispatch so approval-resume never starves (FR-6, FR-7)

**File:** `services/ai-backend/src/runtime_worker/loop.py`.

The machinery already exists: `_handle_claim_with_limit` (165-168) acquires
`self._semaphore` (init 121, `max_parallel_runs` default 4) then dispatches; `_dispatch`
(218-244) routes `run_requested`/`run_cancel_requested`/`approval_resolved` to their
handlers (228-239). The **only** defect is `run_forever` (170-176) → `run_once`
(124-132): `run_once` `await`s `_handle_claim(claim)` _inside_ the claim before
returning, so the next claim (the approval resume) cannot happen until the run finishes.

**Change — convert `run_forever` to capacity-gated task dispatch** (the pattern
`run_until_idle` already uses at 142-144, but streaming instead of batched):

```
async def run_forever(self, *, poll_interval_seconds: float = 1.0) -> None:
    while True:
        await self._semaphore.acquire()          # gate on capacity (FR-7: never
        claim = await self._claim_next()          # claim what we can't run)
        if claim is None:
            self._semaphore.release()
            await asyncio.sleep(poll_interval_seconds)
            continue
        asyncio.create_task(self._run_claim_releasing(claim))
```

where a new `_run_claim_releasing` runs `await self._handle_claim(claim)` in a
`try/finally: self._semaphore.release()`. Do **not** acquire the semaphore a second time
inside `_handle_claim`/`_handle_claim_with_limit` for this path (double-acquire would
re-serialize); keep `_handle_claim_with_limit` for `run_until_idle`'s existing callers
untouched, or refactor both onto `_run_claim_releasing`. Track spawned tasks in a set for
clean shutdown (add-done-callback discard) so `__main__` can await drain.

Why this fixes F6/starvation with default settings: with `max_parallel_runs=4`, one
in-flight run leaves three slots; the loop keeps claiming, so the enqueued
`approval_resolved` is claimed and dispatched on its own task immediately (subject only
to an available slot). Head-of-line blocking is gone because no claim `await`s another
command's completion (flow doc F2 line 116 recommends exactly this: "dispatch claims as
tasks bounded by the semaphore").

**Optional hardening (name it, do not silently do it) — strict priority lane.** To
guarantee approval/cancel dispatch even when all `max_parallel_runs` slots are saturated
by long runs, reserve headroom for control commands: either (a) size a small dedicated
semaphore for `approval_resolved`/`run_cancel_requested` and let `_dispatch` pick the
lane by `command_type`, or (b) add a type-filtered claim to the queue port so control
commands can be claimed ahead of `run_requested`. Option (b) is a **NEW-CONTRACT** on
`RuntimeQueuePort.claim_next` (a `command_types` filter) — call it out and defer unless
saturation is observed; Option (a) needs no contract change and can ship inside this
file. Default recommendation: ship task dispatch now (fixes the reported single-worker
starvation), add the reserved lane only if load testing shows saturation.

**Interaction with cancel (F3, out of scope but adjacent):** task dispatch also lets a
`run_cancel_requested` be claimed while its target run streams — but cancellation still
does not interrupt in-flight execution (`handlers/run.py` never re-checks status
mid-stream; flow doc F3). This plan does **not** fix cooperative cancellation; it only
removes the dispatch-starvation half. Note it so the reviewer does not expect cancel to
suddenly bite mid-run.

### Ordered, independently-shippable commits

1. **`feat(ai-backend): keepalive on the run SSE stream`** — adapter.py FR-1/FR-2 +
   unit tests. Contract-safe alone (comment frames inert to all clients).
2. **`feat(chat-transport): onClose on the SSE port + web reader`** — types.ts +
   sse.ts + WebTransport.ts + IpcTransport.ts + transport-bridge.ts. No behavior change
   until a consumer wires `onClose`.
3. **`feat(chat-surface): auto-reconnect + reconnecting state in useRunSession`** —
   FR-3/FR-4/FR-5, consumes commit 2. Cockpit chip (FR-5/NFR-7) can ride here or a
   follow-up.
4. **`feat(ai-backend): task-based worker dispatch (no approval-resume starvation)`** —
   loop.py FR-6/FR-7 + concurrency tests. Independent of 1–3.

## Descopes & rationale

- **None functionally** — the FRONT is pure hardening; no mockup field is faked and no
  UI is wired to absent data (HONEST-DATA is trivially satisfied: the only new UI is a
  `"reconnecting"` status derived from the existing session).
- **Strict priority lane for control commands → NEW-CONTRACT, deferred.** A guaranteed
  ahead-of-run claim for `approval_resolved`/`run_cancel_requested` under a saturated
  semaphore needs either a second semaphore (no contract change, shippable) or a
  type-filtered `RuntimeQueuePort.claim_next` (NEW-CONTRACT). Evidence: `loop.py:147-153`
  (`claim_next` has no type filter). Descope to a follow-up gated on observed
  saturation; task dispatch (commit 4) already resolves the reported single-worker case.
- **Cooperative cancellation mid-run → DESCOPE (separate finding F3).** Evidence:
  `docs/audit/flows/run-lifecycle-streaming.md` F3 (lines 118-120), `handlers/run.py`
  has no `CANCELLING` re-check. This plan removes cancel _dispatch_ starvation but not
  the fact that a running graph burns to completion. Explicitly out of scope.
- **Non-follow synthetic-heartbeat sequence bug → DESCOPE (unreachable).** Evidence:
  `adapter.py:56-63` + facade does not forward `follow`
  (`backend_facade/app.py:1050,1065`; flow doc line 105). Not on the reachable path;
  leave untouched.
- **Legacy `ChatScreen` reconnect convergence → DESCOPE (separate F5 effort).** The web
  chat already auto-reconnects (`ChatScreen.tsx:693-706`); converging it onto the
  cockpit projector is the F5 refactor, not this reliability fix.

## Test plan

Backend (`services/ai-backend`, run with that service's `.venv`):

- **T-A1 (FR-1, unit).** `RuntimeSseAdapter.stream` in follow mode against a fake bus
  that never fires: assert a `": keepalive\n\n"` comment frame is yielded within
  ~`HEARTBEAT_INTERVAL_SECONDS` and repeats, and that **zero** `event: runtime_event`
  frames are produced. Guards: silent idle stream regression (F7).
- **T-A2 (FR-2/NFR-3, unit).** Assert the keepalive frame contains no `data:`/`event:`/
  `id:` line and terminates with `\n\n`. Guards: framing corruption / accidental
  cursor-advancing heartbeat.
- **T-A3 (FR-1 terminal, unit).** After the fake bus reports a terminal run status, the
  loop returns and emits **no** keepalive post-close. Guards: keepalive-after-close.
- **T-C1 (FR-6, integration).** Two commands enqueued: a long-running `run_requested`
  (fake handler that blocks on an `asyncio.Event`) then an `approval_resolved`. Drive
  `run_forever` briefly; assert the approval handler is invoked **before** the run
  handler unblocks. Guards: the exact starvation in F2/line 96 — the regression this
  whole commit exists for.
- **T-C2 (FR-7, integration).** Enqueue `max_parallel_runs + 2` blocking runs; assert no
  more than `max_parallel_runs` handlers run concurrently and no command is claimed-then-
  re-parked (no lock-lease churn). Guards: unbounded task growth / capacity-gate regress.
- **T-C3 (NFR-2, integration).** Deliver the same `approval_resolved` command twice
  (redelivery); assert the checkpoint resumes at most once. Guards: double-resume.

Frontend (`packages/chat-transport`, `packages/chat-surface`, vitest):

- **T-B1 (sse.ts, unit).** Reader hits clean EOF → `onClose` fires exactly once; on
  `close()`-initiated abort → `onClose` does **not** fire. Guards: the silent-clean-EOF
  hole (`sse.ts:58-61`, flow doc line 100) and abort-vs-drop confusion.
- **T-B2 (useRunSession, unit).** Fake transport whose subscription invokes `onClose`
  after N events for a non-terminal run: assert (a) it re-subscribes with
  `query.after_sequence === <highest seq seen>`, (b) `status` transitions to
  `"reconnecting"` then back to `"streaming"` on the next event, (c) prior `events` are
  **not** discarded, (d) `activeRunId` unchanged and no reset-effect fire (proxy:
  `events` reference preserved across the reconnect). Guards: FR-3/FR-5/NFR-1/NFR-4/NFR-5.
- **T-B3 (backoff, unit with fake timers).** Repeated `onClose`/`onError` → delays follow
  `min(10000, 500·2^n)+jitter`, reset to floor after a successful event; no reconnect
  attempts once run status is terminal or `enabled=false`; pending timer cleared on
  unmount. Guards: FR-4/NFR-6 (backoff bound, stop condition, timer leak).
- **T-B4 (dedupe across reconnect, unit).** Redeliver the boundary `sequence_no` after
  reconnect; assert it is dropped and no duplicate appears in `events`. Guards: NFR-1
  no-dup.
- **T-B5 (transport wiring, unit).** `WebTransport`/`IpcTransport` forward `onClose`
  (and the IPC `"eof"`→`onClose` mapping, distinct from client `"closed"`). Guards:
  desktop clean-EOF reconnect + self-close misread as server drop.

## Risks & gotchas

- **Keepalive must be a _comment_, not a zero-data event.** A frame with `event:
runtime_event` and empty data would still be filtered by `sse.ts:109` (needs
  `dataLines.length > 0`), but a bare `:` comment is the only form that is inert across
  _all_ SSE consumers (incl. the legacy `agentApi` reader). Use `": keepalive\n\n"`
  verbatim; never attach an `id:`.
- **Do not reset on reconnect.** The reset-on-run-change effect (`useRunSession.ts:209-217`)
  is keyed on `activeRunId`; if reconnect is ever implemented by nudging `activeRunId`
  (or re-deriving it) it will wipe `events`/`seenSequence`/`latestSequence` and force a
  full replay — defeating NFR-1 and flickering the cockpit (NFR-5). Reconnect MUST drive
  a nonce inside the subscription effect only.
- **Backoff timer leaks.** The reconnect `setTimeout` must be cleared in the subscription
  effect cleanup AND on `enabled → false` / unmount, or a stale timer bumps the nonce
  after teardown (NFR-6). Cover in T-B3.
- **`onError` vs `onClose` semantics.** A hard network drop may surface as either an
  `onError` (fetch throws) or a clean `onClose` (server closed) depending on the proxy.
  Both must funnel into `scheduleReconnect` for a non-terminal run; only backoff-exhaust-
  never (permanent quiet retry) should ever paint the hard error chrome. Verify against
  a terminal run: a clean close there is _expected_ and must NOT reconnect (else an
  infinite reconnect loop on a completed run).
- **Task dispatch changes worker concurrency semantics globally.** Moving `run_forever`
  off serial dispatch means the desktop single worker now runs up to 4 commands at once.
  Verify handlers are isolated per `run_id` (they are — distinct runs, distinct
  checkpoints) and that shared process-wide caches (`mcp_discovery_cache`,
  `conversation_tool_ordinal_store`) are concurrency-safe under parallel access; the
  in-memory stores must tolerate concurrent mutation. This is the highest-blast-radius
  change — land it behind T-C1/T-C2 and smoke against the live desktop stack (the flow
  doc notes the in-process dev worker uses this exact path, line 25 / F2).
- **In-process dev worker restriction (F9).** The in-process worker only starts on an
  in-memory store (`runtime_api/app.py:791-799`); the desktop embedded-Postgres path runs
  the **out-of-process** worker (`runtime_worker/__main__.py`). Ensure the task-dispatch
  change is in `run_forever` (used by both entrypoints), not only a dev path, so the
  desktop deployment actually gets the fix.
- **Facade buffering.** Confirmed non-issue: `stream_run` proxies with `timeout=None`
  and yields per chunk (`backend_facade/app.py:1067,1080-1088`) — comment frames pass
  through. If a future nginx ingress is added in front, ensure `proxy_buffering off` for
  the stream route or keepalives get coalesced and the anti-idle property is lost.
