# Subagent control: inspect, steer, interrupt

**Status:** proposed — nothing here is implemented.
**Scope:** the `task` delegation path in `services/ai-backend`, the subagent event
projection in `runtime_worker`, and the Agents surface in `packages/chat-surface`.
**Bar:** `services/ai-backend/docs/CLAUDE.md` — module boundaries and file paths,
Pydantic contracts at full field-level shape, edge cases, security, observability,
tests. Two of its rules bind hardest here and are quoted where they bite:
_"Do not remove edge cases to simplify implementation. If an edge case is hard,
raise it."_ and _"Never bypass permission checks in `capabilities/` middleware."_

Every claim about our code carries a `file:line`. Where I could not verify
something it says **unverified** rather than guessing.

---

## 0. Premise check — what is actually true today

The brief that commissioned this document asserted five things. Two are wrong.
Building on the wrong two would have produced a PRD for work that already exists.

| #   | Asserted                                | Verdict                                       | Evidence                                                                                                                                                                                            |
| --- | --------------------------------------- | --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Dispatch works and a fleet card renders | **TRUE**                                      | `runtime_worker/stream_subagents.py:461-502` emits `subagent_fleet_started`; `packages/chat-surface/src/subagents/SubagentFleetCard.tsx:54`; projected at `subagents/subagentProjection.ts:109-158` |
| 2   | A user cannot inspect one child's work  | **PARTLY TRUE** — see §0.2                    | `FleetSubagentRow.tsx:43,58,82`; `SubagentCard.tsx:118-147`                                                                                                                                         |
| 3   | A user cannot steer one child           | **TRUE**, and deliberately so                 | `capabilities/middleware/runtime_tool_control.py:376-377`                                                                                                                                           |
| 4   | A user cannot interrupt one child       | **TRUE**                                      | `runtime_worker/run_cancellation.py:18-23`; only route is run-scoped, `runtime_api/http/routes.py:1183-1187`                                                                                        |
| 5   | There is no depth cap                   | **FALSE** — a depth cap ships and is enforced | `delegation/subagents/recursion.py:142-158`, called at `atlas_task_tool.py:307-309` and `:351-353`                                                                                                  |
| 6   | The parent blocks                       | **TRUE**                                      | `atlas_task_tool.py:373` — `await subagent.ainvoke(subagent_state, subagent_config)`                                                                                                                |

### 0.1 Depth is capped. Fan-out is not.

`DelegationDepthPolicy` is snapshotted once per agent build — i.e. once per run,
which is the snapshot point the PDP/PEP rule asks for (`atlas_task_tool.py:117`,
docstring at `:108-115`). Depth travels in the child's `RunnableConfig` under
`delegation_depth` (`recursion.py:59`, stamped at `atlas_task_tool.py:474`,
`:479`) because "that is the only channel that survives the parent -> child graph
invocation" (`recursion.py:12-15`).

- Default `MAX_DELEGATION_DEPTH = 1` (`constants.py:109`), ceiling
  `DELEGATION_DEPTH_MAX = 8` (`constants.py:131`), document value
  `subagents.max_delegation_depth: 1` (`hyperparameters/hyperparameters.json:53`).
- Refusal is a **value, not an exception**, with the reason stated in source:
  "the caller is a model-facing tool, and a raise there becomes an opaque runtime
  failure instead of something the model can read and route around"
  (`recursion.py:144-148`).
- Belt-and-braces: `SubagentRecursionPolicy.narrow_spec` strips `task` from a
  child's tool surface unless the spec sets `allow_nested_delegation`
  (`recursion.py:179-201`), applied at `atlas_task_tool.py:153`.
- Malformed config falls back to the packaged default, "which is the _most_
  restrictive useful value rather than the least" (`recursion.py:94-107`).

**Fan-out is a different story.** Three numbers look like fan-out caps and none of
them is one:

| Value                                        | Where                                            | Live?                                                                                                                                                                                                                               |
| -------------------------------------------- | ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Defaults.SUBAGENT_CONCURRENCY_LIMIT = 2`    | `constants.py:103`                               | Read only by `SubagentTask.concurrency_limit` (`contracts.py:160-162`) — a contract field with no live task-tool caller                                                                                                             |
| `DelegationAdmissionPolicy.max_children = 3` | `coordination.py:303`                            | The class's own docstring says `max_depth` "is the one field of this contract that the live `task` tool enforces today… The remaining fields belong to the batch planner below, which is still unwired" (`coordination.py:291-294`) |
| `execution.max_parallel_subagents: 4`        | `hyperparameters.json:40`, `settings.py:293-294` | **Zero consumers.** `grep max_parallel_subagents` over `runtime_worker/`, `execution/`, `delegation/` returns nothing                                                                                                               |

What _does_ bound `task` today is the wildcard tool budget:
`DefaultToolBudget` — `TOOL_NAME = "*"`, `MAX_CALLS_PER_RUN = 10`, and the cap is
"**per distinct tool name**, not per run in aggregate" (`tool_budgets.py:57-58,
67-69`), seeded identically into both store adapters
(`runtime_adapters/in_memory/runtime_api_store.py:268-270`,
`runtime_adapters/file/runtime_api_store.py:413-415`). Rejection happens before
invocation (`tool_budget_middleware.py:41-43`).

So the true statement is: **a run may dispatch at most 10 subagents in total, and
all 10 may be dispatched in a single assistant message.** There is no per-turn
fan-out cap and no concurrency cap. That is the gap this PRD closes, not depth.

### 0.2 What "inspect" gets you today

More than nothing, less than a transcript.

- The fleet card groups children and expands (`SubagentFleetCard.tsx:66-80`).
- A fleet row expands to an inline timeline of the child's activities
  (`FleetSubagentRow.tsx:43`, `:58`, `:82`).
- `SubagentCard` has a `<details>` disclosure showing either the activity
  timeline, the full result text, or an honest "Single-shot response — no inner
  tool calls." (`SubagentCard.tsx:118-147`).
- The Agents rail lists children with a jump-to-thread affordance
  (`SubagentCard.tsx:94-103`) and a jump-to-approval link for a paused child
  (`:109-117`).

What you cannot see is the child's **own** work: its model text, its tool
arguments, its tool results. `onJumpToThread` scrolls within the same thread
(`SubagentCard.tsx:99`); there is nothing to navigate _to_.

### 0.3 The self-referential "This run" chip — still there, narrower than reported

`AgentFleetList` is **not** the subagent fleet. It is a cross-**run** list:
"This run plus the OTHER runs (running or with held work)"
(`AgentFleetList.tsx:3`). Three findings:

1. **The chip is conditional.** The server skips a run with no pending items —
   "A quiet run, including the current run, has no fleet signal."
   (`surfaces_v2/pending_work.py:494-497`). So "This run" appears only when the
   open run itself has held work.
2. **When it appears, it is a dead control.** The row is a `<button>` with
   `aria-label={`Open run "…"`}` (`AgentFleetList.tsx:57-64`), and its handler
   early-returns on the current run: `if (agent.run_id !== stageRunId)
selectRun(agent.run_id);` (`RunDestination.tsx:3816-3821`). A focusable,
   labelled button that does nothing.
3. **The empty copy is both wrong and unreachable.** `"No other agents are
running."` (`AgentFleetList.tsx:48`) describes a list that includes this run —
   and the rail only mounts the component when `agents.length > 0`
   (`RunWorkspaceRail.tsx:597`), so that branch never renders at this callsite.

The deeper problem is that the Agents tab renders `AgentFleetList` (peer **runs**)
directly above `AgentsTab` (this run's **subagents**) with no scope heading
between them (`RunWorkspaceRail.tsx:594-613`). One word, "agent", two populations.

### 0.4 What already exists that a naive design would rebuild

| Capability                           | Where                                                                                                                                                                                           | Note                                                                                      |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Run-level steer, end to end          | `POST /v1/agent/runs/{id}/steer` (`routes.py:1192-1197`, facade `app.py:1942-1964`), `RunSteeringInbox` (`execution/run_steering.py:106-160`), composer wiring (`RunDestination.tsx:1787-1831`) | Delivery is at the model step, never mid-tool (`run_steering.py:12-20`)                   |
| A per-child scope key                | `runtime_tool_control.py:1033-1045` — returns `f"subagent:{supervisor_task_call_id}"`                                                                                                           | This is the address a per-child control needs; it already exists                          |
| Deterministic parent↔child linkage   | `atlas_task_tool.py:447-497`, read by `stream_parts.py:79-90`                                                                                                                                   | Replaces a FIFO heuristic that broke on ≥2 concurrent children (`atlas_task_tool.py:5-8`) |
| Per-child events on the run stream   | envelope fields `parent_task_id` / `task_id` / `subagent_id` at `runtime_api/schemas/events.py:3012-3014`; stamped at `stream_tools.py:301-315`, `stream_events.py:348-367`                     | The data for a child transcript is already durable and ordered                            |
| Cancel closes open children honestly | `stream_subagents.py:84-125`, called from `handlers/run.py:938, 990, 1028`                                                                                                                      | Prevents the "1 live forever" cockpit                                                     |
| A typed refusal vocabulary           | `SubagentErrorCode` incl. `CONCURRENCY_LIMIT_EXCEEDED` (`constants.py:59-70`)                                                                                                                   | The code exists; no message and no emitter do                                             |

---

## 1. User stories

Each story is a person and an outcome, with acceptance criteria a test could
assert. IDs are referenced by the phase plan in §13.

### S1 — I want to know what a child is actually doing, not that it exists

> As someone whose agent just dispatched four researchers, I want to open one of
> them and read what it searched, what it read, and what it concluded — so I can
> tell a child that found nothing from a child that found the wrong thing.

**Acceptance**

- Opening child `T` renders every event on the run stream where
  `task_id == T` or `parent_task_id == T`, in `sequence_no` order.
- The child view shows the child's model text, its tool calls with arguments and
  results, and its terminal summary — the same renderers the parent thread uses.
- A child with zero inner events renders "Single-shot response — no inner tool
  calls.", the copy that already exists at `SubagentCard.tsx:142`, not an empty
  panel.
- Opening a child costs no new SSE subscription while the run is live.

### S2 — I want to stop one child without killing the whole run

> As someone watching one researcher grind on a bad lead while three others make
> progress, I want to stop just that one — so the run keeps the work the other
> three already did.

**Acceptance**

- A running child row exposes a Stop control.
- Stopping child `T` produces a `subagent_completed` frame for `T` with
  `status = "cancelled"` and a user-visible summary.
- Sibling children keep running; the run status stays `RUNNING`.
- The parent's `task` tool call for `T` returns a model-readable string saying it
  was interrupted by the user, and the parent continues its turn.
- Stopping a child that has already completed is a no-op that reports "already
  finished", never an error and never a second terminal frame.

### S3 — I want to know what a stopped child already did

> As someone who just stopped a child that had been running for ninety seconds, I
> want to know whether it already wrote files — so I know whether "stopped" means
> "nothing happened".

**Acceptance**

- The cancelled child's terminal frame states how many tool calls it completed.
- If the child wrote to a granted folder, those writes appear in the Changes tab
  and remain revertible under the existing undo route
  (`runtime_api/http/host_write_undo.py:103-121`).
- Nothing in the product claims a stopped child's completed side effects were
  undone.

### S4 — I want to redirect one child mid-flight

> As someone who sees a child searching the wrong repository, I want to send it a
> correction without stopping it or interrupting the other three.

**Acceptance**

- A running child row exposes a "Send a note" control that accepts text.
- The note is delivered at the child's next model step, framed by the same
  `<user_steering>` block the run-level steer uses (`run_steering.py:92-103`).
- The note is delivered to **that child only** — the supervisor and siblings do
  not receive it.
- A note addressed to a child the worker is not executing reports "not
  delivered", never a silent success. This mirrors the honesty rule at
  `run_steering.py:21-29`.
- The note is a durable transcript fact whether or not it was delivered.

### S5 — I want the fan-out bounded before it happens

> As someone paying for tokens on my own BYOK key, I want a runaway plan to be
> refused at dispatch, not discovered on the bill.

**Acceptance**

- A single assistant message requesting more than the configured number of
  children dispatches exactly the configured number; the surplus calls return a
  typed refusal.
- The refusal names the limit and tells the model what to do instead, the way the
  depth refusal already does (`constants.py:170-182`).
- The refusal is a tool result the model can read, never a raised exception.
- The cap value is resolved once at run start and a mid-run settings change
  cannot raise it for a run already in flight.

### S6 — I want to know why my agent refused to delegate

> As someone whose agent said "I'll handle this myself" after I asked it to
> parallelise, I want to see that the depth limit refused it — so I know it is a
> setting, not a mood.

**Acceptance**

- A depth or fan-out refusal emits a user-visible frame, not only a model-visible
  string.
- The frame names the limit, its current value, and where it is configured.
- Today's depth refusal is model-visible only (`recursion.py:152-158` returns a
  string into the tool result) and draws no card. That is the gap.

### S7 — I want the Agents panel to tell me about _this_ run's agents

> As someone opening the Agents tab, I want it to answer "what is my agent's team
> doing right now", not "which of my other conversations has a pending card".

**Acceptance**

- The Agents panel's primary content is this run's subagent tree.
- The cross-run peer list is either removed from this panel or placed under an
  explicit second heading that names its scope.
- No row in the panel is a control that does nothing when clicked.
- The panel's empty copy describes the population it actually lists.

### S8 — I want to see which child is expensive

> As someone with four children running, I want to see at a glance which one has
> burned the most tokens and the most wall clock.

**Acceptance**

- Each child row shows elapsed time while running and duration when terminal
  (already true: `FleetSubagentRow.tsx:45-49`).
- Each child row shows its token rollup when the worker correlated one. The
  payload field already exists — `usage?: AssistantSubagentUsageRollup` on
  `subagent_completed` (`packages/api-types/src/index.ts:3807-3810`) — and is not
  rendered anywhere in `packages/chat-surface/src/subagents/`.
- A child whose usage was not correlated shows nothing rather than a zero.

### S9 — I want a child's failure to be legible

> As someone whose fleet came back with three successes and one failure, I want
> to know what the fourth one hit.

**Acceptance**

- A `failed` or `timed_out` child row shows the typed reason, not a generic
  sentence.
- The reason is derived from a `SubagentErrorCode` (`constants.py:59-70`), not
  from model prose. This is the failure class recorded in
  `project_error_copy_is_model_paraphrase`.

### S10 — I want the run's Stop to still mean stop

> As someone who hits Stop while four children are running, I want all four to
> stop and all four cards to close.

**Acceptance**

- Unchanged from today: run cancel cancels the task executing the run, which is
  the only thing that stops an in-process child (`run_cancellation.py:18-23`).
- Every open child gets a terminal frame inside the run's sealed prefix
  (`stream_subagents.py:84-125`).
- Adding per-child stop must not weaken this. A test asserts both paths.

### S11 — I want a child not to be a second door to my files

> As someone who attached one folder, I want a subagent to have exactly the
> access I granted the run — not more, and not a different folder I never chose.

**Acceptance**

- A child's filesystem authority is the parent's, or narrower, never wider.
- A child's host writes are recorded in the same journal the parent's are, with a
  real `authorized_root`, and are revertible.
- A child's write attributes to a `tool_call_id` the user can locate in the UI.

### S12 — I want to leave and come back

> As someone who closed the laptop mid-fleet, I want to reopen the run and see
> where each child got to.

**Acceptance**

- Reopening a run replays child state from the event stream with no server-side
  session per child.
- A child open in the detail view at disconnect reopens to the same child.
- Resume uses the existing `?after_sequence=N` cursor
  (`routes.py:539`, `:685`) with no new streaming primitive.

---

## 2. Product decisions

**D1 — A child is not a session. It is a filtered view of the parent run's
stream.** OpenCode's child is a `Session` row with a `parentID` and its own URL;
ours is an in-process LangGraph invocation inside one run. We do not adopt their
model. We do not need to: the envelope already carries `task_id`,
`parent_task_id` and `subagent_id` at the top level
(`runtime_api/schemas/events.py:3012-3014`), and the worker already stamps
`parent_task_id` on a child's inner tool events (`stream_tools.py:301-315`). The
child transcript is a **query**, not a resource.

**D2 — Dispatch stays blocking.** Background delegation requires a job registry,
a promotion path, and the ability to forge a new turn from a completion while the
agent is idle. We have none of those, and the last is a run-lifecycle capability,
not a UI one. §4 prices it and defers it.

**D3 — Per-child interrupt is a cooperative flag, not a task cancel.** A child
runs inside the parent's tool call on the same asyncio task
(`atlas_task_tool.py:373`), so cancelling it by cancelling a task means
cancelling the run. The interrupt therefore sets a flag the child's own
middleware reads at its next model step and its next tool call, and the `task`
tool returns a typed interrupted result. This is Hermes's semantics, stated in
their source as "Does not hard-kill the worker thread (Python can't); sets the
child's interrupt flag which propagates to in-flight tools"
(`/Users/parthpahwa/Documents/work/hermes-agent/tools/delegate_tool.py:184-206`).
We adopt the semantics and say so in the copy.

**D4 — Interrupt does not undo.** Work a child completed before the flag was read
stays done, and its host writes stay in the journal and stay revertible. The
product copy says "Stopped — 3 tool calls already finished", never "cancelled" as
if nothing happened. This is the `write_journal` honesty rule applied: "An honest
'cannot undo this one' is a usable answer; a missing row is not."
(`capabilities/desktop/write_journal.py:79`, surrounding note `:236-244`).

**D5 — Per-child steering reuses the run-steer mailbox, keyed by scope.** The
mailbox lives on the live-run handle (`run_cancellation.py:61-81`); the drain is
scope-gated already (`runtime_tool_control.py:376-377`); and the scope key
`subagent:{supervisor_task_call_id}` already exists
(`runtime_tool_control.py:1033-1045`). We add a keyed mailbox, not a second
delivery lane. Two registries that must agree on a lifetime is named in our own
source as "a correctness hazard, not merely duplication"
(`run_cancellation.py:11-16`).

**D6 — Fan-out is capped per assistant message, and the cap is snapshotted at run
start.** Not per run — the per-run bound already exists as the wildcard tool
budget of 10 (`tool_budgets.py:69`) and is the wrong instrument, because it
cannot distinguish ten children at once from ten children over ten turns. The new
cap is per-tick, defaults to 4, and is refused as a value.

**D7 — Refusals become visible.** A depth or fan-out refusal today is a string in
a tool result and nothing else. It gets a frame. Users read cards, not tool
results.

**D8 — Children do not get their own workspace grant, and they do not get a
worktree.** §8 gives the mechanism. Short version: the filesystem backend is
composed once per run (`execution/factory.py:319-333`) from roots the worker
resolved once (`:311`), and the packaged app does not bundle git
(`write_journal.py:9-14`). Per-child _narrowing_ is already expressible
(`factory.py:2889-2934`); per-child _widening_ is not, and must not become
expressible.

**D9 — The Agents panel is about this run.** Cross-run peers move behind a named
second scope or leave. The self-referential row goes.

**D10 — No new streaming primitive.** Child inspection, child interrupt receipts
and child steer notes all ride the existing per-run ordered stream and its
`?after_sequence` cursor. A second subscription would break the one-projector
invariant `subagentProjection.ts:8-16` was written to hold.

**D11 — Every new model-visible surface lands in all three occupancy
declarations.** `capabilities/operations/conformance.py`,
`builtin_operation_catalog.json`, `operation_descriptors.json` — or
`OperationConformanceGate.validate_current()` fails at worker startup
(`runtime_worker/loop.py:645`). Note that this PRD adds **no** new model-visible
tool: interrupt and steer are user-facing, not model-facing. `task` and
`subagent_dispatch` are already declared
(`builtin_operation_catalog.json:221-237`).

**D12 — Non-goal: nested delegation beyond depth 1 by default.** Raising the
default multiplies token spend against the user's own BYOK key, which is why the
default is "deliberately the smallest useful value" (`constants.py:104-109`). This
PRD does not change it.

---

## 3. Depth and fan-out caps

### 3.1 Depth — what changes

Almost nothing. The enforcement is correct. Three additions:

1. **A user-visible frame.** `DelegationDepthPolicy.refusal` returns a
   `SubagentError` (`recursion.py:142-158`); today `atlas_task_tool` discards
   everything but `safe_message` (`:264-268`). The tool instead emits a
   `subagent_refused` frame carrying the typed code before returning the string.
2. **The value is surfaced.** The refusal names `max_depth` already
   (`constants.py:178-182`); the frame carries it as a field so the UI can say
   where to change it.
3. **Nothing else.** `narrow_spec` stays, the ceiling stays at 8
   (`constants.py:131`), the fallback-to-most-restrictive stays
   (`recursion.py:106-107`).

### 3.2 Fan-out — what is added

**The value.** `max_children_per_tick`, default **4**, floor 1, ceiling
`FANOUT_MAX = 8` (matching `_MAX_DIRECT_CHILDREN = 8` at `coordination.py:32`, so
the planner and the live path cannot disagree about "how many children is too
many"). Document key `subagents.max_children_per_tick` in
`hyperparameters/hyperparameters.json` next to `max_delegation_depth`.

**Where it is enforced.** In `atlas_task_tool`, in the same position as the depth
check — before the gateway, before subagent resolution, before any state is
prepared. The depth check's comment states the principle: "a call this deep is not
work to review, it is work that must not start" (`atlas_task_tool.py:304-306`).

**The hard part, stated rather than simplified away.** The model emits N `task`
tool calls in **one** assistant message; LangGraph's tool node invokes them as N
separate calls to our function. A per-tick cap therefore needs state shared
across those N invocations, and the graph "now runs a turn's tool calls
concurrently" (`tool_budget_guard.py:169-171`). Three consequences:

- The counter must be keyed by `(run_id, execution_scope, model_turn)`.
  `model_turn` is already in state as `runtime_control_model_turn`
  (`runtime_tool_control.py:352`); `execution_scope` is already computed
  (`:1033-1045`).
- The read-then-charge pair must be lock-guarded, exactly as `ToolBudgetGuard`
  does for the same reason: "two callers that both read 'one left'"
  (`tool_budget_guard.py:169-171`).
- **Which** of N concurrent calls is refused is not deterministic. We do not
  pretend otherwise: the refusal message says "this turn already dispatched the
  maximum", not "your fourth call was rejected".

**Failure mode.** A value, never an exception —
`SubagentErrorCode.CONCURRENCY_LIMIT_EXCEEDED` already exists
(`constants.py:61`) with no message and no emitter. This PRD supplies both. The
message follows the depth refusal's shape: name the limit, name the recovery.

> Delegation refused: this turn already dispatched {max} subagents, which is the
> configured maximum. Wait for them to finish and delegate again, or complete
> this part with your own tools.

**Interaction with the existing budget.** Both apply. The wildcard row bounds
`task` at 10 per run (`tool_budgets.py:69`); the new cap bounds it at 4 per tick.
A run may therefore dispatch 4 + 4 + 2 across three turns, and the 11th call is
refused by the budget with its own message
(`tool_budget_middleware.py:66-80`). The two refusals are distinguishable by
code, which matters for S6.

### 3.3 What the model is told at the cap

| Cap            | Code                                             | Message source                                                      | Visible to model | Visible to user                                                      |
| -------------- | ------------------------------------------------ | ------------------------------------------------------------------- | ---------------- | -------------------------------------------------------------------- |
| Depth          | `DEPTH_LIMIT_EXCEEDED` (`constants.py:62`)       | `Messages.Delegation.depth_limit_exceeded` (`constants.py:170-182`) | today ✅         | today ❌ → new frame                                                 |
| Fan-out        | `CONCURRENCY_LIMIT_EXCEEDED` (`constants.py:61`) | new                                                                 | new              | new frame                                                            |
| Per-run budget | `TOOL_BUDGET_EXCEEDED`                           | `ToolBudgetReject.safe_message` (`tool_budget_middleware.py:66-80`) | today ✅         | unverified — I did not trace whether a budget rejection draws a card |

---

## 4. Background vs blocking dispatch

### 4.1 Today

Blocking, unconditionally. `await subagent.ainvoke(subagent_state,
subagent_config)` at `atlas_task_tool.py:373`; the synchronous twin at `:343`.
The parent's graph node is suspended for the child's whole lifetime. There is no
job registry, no promotion, no completion queue.

Both references we studied have a background lane. OpenCode's is opt-in behind
`OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS`
(`/Users/parthpahwa/Documents/work/opencode/packages/opencode/src/tool/task.ts:98-102`)
and its registry is explicitly not durable — "process restart or owner-scope
closure loses status and interrupts live work"
(`opencode/packages/core/src/background-job.ts:113-119`). Hermes's is mandatory
for top-level delegation and durable, SQLite-backed with delivery retries
(`hermes-agent/tools/async_delegation.py:76-86`), and its re-entry rail needs the
ability to forge a new turn from a completion while the agent is idle
(`async_delegation.py:9-24`).

### 4.2 Decision: defer, and say why

The blocker is not the tool. It is that a background completion must re-enter the
run as a **new turn**, and our run lifecycle has no such notion. Three of our own
constraints make this a program, not a slice:

1. **The seal.** A run's terminal event seals `[1..N]` and a client's stream
   closes on it; nothing after the seal is ever delivered
   (`runtime_worker/run_cancellation.py:38-46`). A background child that finishes
   after its parent's turn ends would append events after the seal — "durable but
   invisible", the exact failure the seal module was written about
   (`runtime_worker/handlers/steer.py:10-16`).
2. **The claim boundary.** A run is reachable exactly while this process is
   inside its claim (`run_cancellation.py:103-108`). A background child outliving
   the claim is unreachable for cancel _and_ for steer.
3. **Message-role alternation.** Hermes's own note is that completions surface as
   a new turn "never spliced between a tool result and an assistant message"
   because that "keeps strict message-role alternation legal and the prompt cache
   intact" (`hermes-agent/tools/async_delegation.py:9-24`). We would need the same
   discipline and we have no seam for it.

### 4.3 What would change in the event stream if a child ran in the background

Recorded now so the deferral is priced, not hand-waved. If background dispatch
ships later:

- `subagent_started` is unchanged — it already fires at **dispatch**, off the
  supervisor's tool-call chunk (`stream_subagents.py:687-711`), not at first
  output. Nothing about it assumes the parent is blocked.
- The `task` tool's return changes from the child's result to a promotion
  acknowledgement, so `task_tool_result_payloads` (`stream_subagents.py:714`)
  would no longer be the source of `subagent_completed`. That event needs a second
  producer, keyed on the job rather than on the tool result.
- `subagent_fleet_finished` decrements on `subagent_completed`
  (`stream_subagents.py:505-537`); it would need to survive the parent turn
  ending, i.e. the fleet bookkeeping moves off the per-run stream processor's
  in-memory dicts (`stream_subagents.py:71-77`) onto something durable.
- The run's terminal event can no longer be emitted while a child is open, or
  the seal swallows the child's tail. Either the run stays non-terminal
  (changing what "done" means in the cockpit) or completions arrive on a new run.

**Consequence:** background dispatch is out of scope for this PRD, and any future
attempt starts by deciding the seal question, not by editing `atlas_task_tool`.

---

## 5. Per-child control

### 5.1 Inspect

Covered in §6 (addressing) and §7 (surface). No new backend capability.

### 5.2 Interrupt

**Affordance.** A Stop control on a running child row, in both the inline fleet
card and the Agents rail. Confirmation is not required; the action is reversible
in the sense that matters — the run continues.

**Wire contract.** A new route, sibling of cancel and steer:

```
POST /v1/agent/runs/{run_id}/subagents/{task_id}/interrupt
```

Facade forwards it exactly as it forwards cancel and steer
(`backend_facade/app.py:1925-1964`), with the same identity overwrite: the body
never carries an identity, and `requested_by_user_id` is stamped from the verified
session (`routes.py:715-716`).

**Mechanism.** The same out-of-band shape cancel and steer use — enqueue a durable
command, claim it, join to the executing task through `LiveRunRegistry`
(`run_cancellation.py:102-108`). The command deposits into a per-child interrupt
set on the live-run handle. Two readers:

1. `RuntimeControlMiddleware.before_model` — when the current execution scope is
   `subagent:{task_id}` and that id is flagged, the child does not make another
   model call.
2. The tool-call wrapper — a flagged child's next tool call is refused before
   invocation, the same position `ToolBudgetReject` occupies
   (`tool_budget_middleware.py:41-43`).

The `task` tool then returns a typed interrupted result to the parent instead of
the child's output, so the parent reads a string it can route around, consistent
with `recursion.py:144-148`.

**What interrupt means for work already done.** D4. Concretely:

- Completed tool calls stay completed. An in-flight tool call is **not** torn
  down — that is cancellation's job and it already owns it
  (`run_steering.py:14-18`). A child interrupted at 40% of a 30-second tool call
  stops after that call returns.
- Host writes already captured stay in the journal with their real
  `authorized_root` and stay revertible (`write_journal.py:206-211`,
  `runtime_api/http/host_write_undo.py:103-121`).
- The terminal frame reports the count of completed tool calls so S3's acceptance
  criterion has a source.

**Idempotence.** Interrupting a child that already reached a terminal status is a
no-op reported as `already_terminal`, never an error and never a second frame.
Deduplication is the existing one in `append_task_lifecycle_event`
(`stream_subagents.py:98-101`).

**Miss is not an error.** In a multi-worker deployment the interrupt claim may
land on a process that is not executing the run. It reports not-delivered, the
same honesty `RunCancellationOutcome` and the steer handler are built around
(`run_cancellation.py:26-31`, `handlers/steer.py:50-55`).

### 5.3 Steer

**Affordance.** A "Send a note" control on a running child row, opening a bounded
text input. Same bound as the run-level steer:
`SteeringMessage.MAX_TEXT_LENGTH = 4000` (`run_steering.py:81`), mirrored in the
client so an over-long note costs a round trip, exactly as `RunDestination.tsx`
does today (`:1802-1808`).

**Wire contract.**

```
POST /v1/agent/runs/{run_id}/subagents/{task_id}/steer
```

**Mechanism.** `RunSteeringInbox` becomes keyed. Today it is one deque
(`run_steering.py:119-123`); it becomes a mapping from execution scope to deque,
with the supervisor's scope as one key among several. The drain is already
scope-gated and already refuses to deliver to a child
(`runtime_tool_control.py:376-377`); the change is that a child now drains its
own key instead of nothing.

The existing comment at `runtime_tool_control.py:368-372` is the spec for why
this must be keyed and not global:

> "A subagent inherits this middleware and the run's context binding, so an
> unscoped drain would hand the user's course correction to whichever child
> happened to reach a model step first, and the supervisor — the one holding the
> plan the user is correcting — would never see it."

**Delivery point.** Unchanged: the child's `before_model` seam, after the previous
tool node settles, before the next provider dispatch (`run_steering.py:12-20`).
Delivery to a child that never makes another model call (it was about to finish)
is a miss, reported as such.

**Consume-once.** Unchanged (`run_steering.py:146-160`). The mailbox bound
`MAX_PENDING = 16` (`run_steering.py:119`) becomes per key, not per run, so one
chatty child cannot starve the supervisor's mailbox.

**Durability.** The note is appended as a `subagent_steered` frame at accept time,
under the coordinator's non-terminal check — not by the handler. This is the seal
rule the steer handler already applies and explains
(`handlers/steer.py:10-16`): an event emitted on the claim side races the run's
terminal event and can become durable but invisible.

### 5.4 What we deliberately do not build

- **Pause further spawning.** Hermes has it (`p` in `/agents`,
  `hermes-agent/ui-tui/src/components/agentsOverlay.tsx:724-733`) backed by
  module-global state (`tools/delegate_tool.py:154-169`). Process-global is wrong
  for a multi-tenant service, and run-scoped is more work than it looks. Deferred,
  and the fan-out cap in §3 covers most of the need.
- **Resume a specific child.** OpenCode's `task_id` parameter continues a prior
  child session (`opencode/packages/opencode/src/tool/task.ts:136-138`). We have
  no child identity that survives the parent's turn. Blocked by D1/D2.
- **Promote a foreground child to background.** OpenCode's Ctrl-B
  (`opencode/packages/tui/src/routes/session/index.tsx:1022-1035`). Blocked by
  §4.

---

## 6. How a child's transcript is addressed and fetched

### 6.1 The data is already there

| Fact                                     | Field                     | Where written                                                                        |
| ---------------------------------------- | ------------------------- | ------------------------------------------------------------------------------------ |
| This event belongs to child `T`          | `task_id`                 | `stream_subagents.py:443-444` (progress), `:687-711` (start), `:714+` (result)       |
| This event happened **inside** child `T` | `parent_task_id`          | `stream_tools.py:301-315`; `stream_events.py:348-367`; `stream_subagents.py:381-386` |
| Which subagent definition ran            | `subagent_id`             | envelope field `events.py:3014`; resolved at `stream_subagents.py:354-368`           |
| Which fleet the child belongs to         | `payload.parent_fleet_id` | `stream_subagents.py:400`, `:420-422`                                                |

All three id fields are top-level on `_RuntimeEventBase`
(`runtime_api/schemas/events.py:3012-3014`) — not buried in `payload` — so a
server-side filter is a `WHERE`, not a JSON scan.

### 6.2 The gap: no filter parameter

`get_events` accepts `after_sequence` and identity only (`routes.py:532-549`).
`stream_run` accepts `after_sequence` and `follow` (`routes.py:679-704`). Neither
can express "just this child".

### 6.3 Decision: a filtered replay, not a filtered stream

```
GET /v1/agent/runs/{run_id}/events?after_sequence=N&subagent_task_id=T
```

- **Replay is filtered server-side.** Returns the same
  `RuntimeEventReplayResponse` (`events.py:3175-3182`) with events narrowed to
  `task_id == T OR parent_task_id == T`, preserving `sequence_no` order and the
  existing `has_more` semantics.
- **The live stream is NOT filtered.** The client already holds the whole run's
  ordered event array (`subagentProjection.ts:8-16`); filtering it client-side is
  a `.filter()`, and a second filtered SSE subscription would be a second
  projector — the thing that module exists to prevent. D10.
- **Resume is the existing cursor.** `?after_sequence=N` unchanged. A child view
  reopened after a disconnect replays filtered from the client's high-water mark.

### 6.4 Why not a per-child cursor

Because `sequence_no` is per **run** (`events.py:3110`, and
`ConversationCardEventsResponse` says "every run numbers from 0",
`events.py:3160-3162`). A per-child sequence would be a second ordering to keep
consistent with the first, and the run's is the one the seal is defined over.
A child view's cursor is a run cursor that happens to be reading a subset.

### 6.5 Edge case the filter must not get wrong

An approval raised **inside** a child is stamped with the child's
`parent_task_id` on the approval record so the resume can find it
(`stream_events.py:676-684`), and the interrupt event is re-keyed to
`task_id = parent_task_id` (`stream_events.py:977-1004`). A naive filter would
therefore show a child's approval card in the child view **and** the parent view.
That is correct and intended — OpenCode makes the same call, hoisting a child's
permission prompt to the parent (`opencode/packages/tui/src/routes/session/index.tsx:234-241`)
— but it must be a decision, not an accident. **Decision: the approval card
renders in both places, and answering it in either resolves it.**

---

## 7. The Agents panel redesign

### 7.1 What is wrong with the current panel

`RunWorkspaceRail.tsx:594-613` composes the Agents body as:

```tsx
{pendingV2 !== undefined && pendingV2.agents.length > 0 ? (
  <AgentFleetList agents={…} currentRunId={…} onOpenRun={…} />
) : null}
<AgentsTab subagents={…} … />
```

Four defects:

1. **Two populations, one word.** `AgentFleetList` is peer **runs**
   (`AgentFleetList.tsx:3`); `AgentsTab` is this run's **subagents**
   (`AgentsTab.tsx:117-121`). No heading separates them.
2. **A dead control.** §0.3 — the "This run" row's click handler no-ops
   (`RunDestination.tsx:3816-3821`).
3. **Wrong empty copy in an unreachable branch.** §0.3.
4. **No controls.** `AgentsTab` passes `onJumpToSubagent` and nothing else
   (`RunWorkspaceRail.tsx:604-612`); `SubagentCard` renders a jump button and a
   jump-to-approval link and no action (`SubagentCard.tsx:94-117`).

### 7.2 Target structure

```
Agents  ·  [N live]
├─ THIS RUN                                    ← scope heading, always present
│  ├─ ▸ supervisor                             ← the parent, as a row
│  │  ├─ ● researcher-a   0:42   ⏹  ✎          ← running: Stop + Note
│  │  ├─ ✓ researcher-b   1:08   1.2k tok      ← terminal: usage (S8)
│  │  ├─ ⏸ researcher-c   paused · approval →  ← existing pause chrome
│  │  └─ ✗ researcher-d   depth_limit_exceeded ← typed reason (S9)
│  └─ (empty) "Subagents run here when Copilot dispatches parallel work."
└─ OTHER CONVERSATIONS                         ← only when non-empty (see 7.4)
   └─ ▸ "Refactor the parser"  2 waiting  →
```

**Buildable specifics.**

- **The tree replaces the flat list.** `AgentsTab` currently renders `ordered`
  from `mergeOrderedSubagents` (`AgentsTab.tsx:73`). The projection already
  carries what a tree needs: `parent_fleet_id` groups children
  (`subagentProjection.ts:47-79`), and `parent_task_id` on the payload
  (`api-types/src/index.ts:3790-3792`) gives nesting for a depth-2 run. The
  supervisor row is synthesised client-side; it has no `task_id`.
- **Sort is dispatch order, not recency.** `subagentsByRecency`
  (`AgentsTab.tsx:36`) reorders a fleet as children finish, which makes a
  four-child fleet jump under the cursor. Hermes sorts "by depth then index to
  match spawn order regardless of network reordering"
  (`hermes-agent/ui-tui/src/lib/subagentTree.ts:17-47`). We already have dispatch
  order: `task_ids` on the fleet bookend
  (`stream_subagents.py:493-497`) is emitted in dispatch order and the client
  keeps it (`subagentProjection.ts:52-53`).
- **Row controls.** Two, both only on a non-terminal row:
  - `⏹` Stop → `POST …/subagents/{task_id}/interrupt`. `aria-label`
    "Stop {name}". Disabled with a tooltip while an interrupt is in flight.
  - `✎` Note → opens an inline bounded input → `POST …/subagents/{task_id}/steer`.
    Both are additive props on `SubagentCard` and `FleetSubagentRow`, defaulting to
    `undefined` = not rendered, the same pattern `onJumpToThread` already uses
    (`SubagentCard.tsx:94`). A host that does not wire them gets today's DOM.
- **Usage.** Render `view.usage` when the projection carried one; render nothing
  when it did not. The field exists on the payload
  (`api-types/src/index.ts:3807-3810`) and is documented as "Absent when the
  provider did not return stable message ids for the subagent" — so absence is a
  real state, not a zero.
- **Typed failure reason.** `subagentCardViewModel` gains a `reasonCode` read from
  the terminal payload, mapped to copy in `subagents/labels.ts` beside the
  existing `pauseShortLabel` / `pauseJumpLabel` (`SubagentCard.tsx:19-23`). No
  model prose in a status line.
- **Open a child.** Clicking a row's name opens the child detail view (§6).
  Distinct from clicking the row body, which still expands the inline timeline
  (`FleetSubagentRow.tsx:82`).

### 7.3 The child detail view

A panel, not a route — we have no child URL and D1 says we should not invent one.

- Header: name, status pill, elapsed/duration, usage when present, and the child's
  objective (`view.task`, already projected — `FleetSubagentRow.tsx:63-66`).
- Body: the filtered event list from §6.3, rendered with the **same** renderers
  the parent thread uses. No second renderer family.
- Footer: Stop and Note, the same two controls as the row.
- Back: returns to the tree, preserving scroll.
- Empty: "Single-shot response — no inner tool calls." — the copy that already
  exists (`SubagentCard.tsx:142`).

### 7.4 The cross-run list

Two options, and this PRD picks one.

**Picked: move it out of the Agents panel.** The panel's own source already
records the precedent — the cross-run pending **queue** was removed from the
Approvals panel for exactly this reason: "It answered 'what is parked anywhere?'
inside a surface scoped to one conversation… Cross-conversation work belongs on
the nav rail's Chats badge, where a global count is expected."
(`RunWorkspaceRail.tsx:619-623`). The cross-run _fleet_ is the same shape of
mistake and gets the same treatment.

**Consequences.**

- `AgentFleetList` loses its only rail callsite. It is not deleted — its
  cross-run semantics may be wanted on a global surface later — but the rail stops
  mounting it, and the `pendingV2.agents` / `pendingV2.currentRunId` props on
  `RunWorkspaceRail` become unused for this panel.
- The dead "This run" row goes with it. `AgentFleetList.tsx:72-79` and the no-op
  branch at `RunDestination.tsx:3818` are both removed rather than fixed, because
  the fix for a self-referential row on a run-scoped surface is not to make it
  clickable.
- If a reviewer prefers to keep it: it must carry the heading "OTHER
  CONVERSATIONS", must exclude `currentRunId` server-side (a one-line change at
  `pending_work.py:494-497`, which already has the skip branch), and its empty
  copy must become reachable.

### 7.5 The badge

`agentsBadge` stays as-is — "N live" while any child runs, else the total
(`RunWorkspaceRail.tsx:1237-1252`) — but `countRunning` must count only this
run's subagents, which it already does (`:1222-1230`, over `SubagentSnapshotMap`).
No change. Recorded so nobody "fixes" it to include peer runs.

---

## 8. Isolation: can a child get its own worktree?

**No, and two independent things stop it.**

### 8.1 The filesystem backend is composed once per run

`_composed_deep_backend(...)` is built once in `acreate_agent_runtime`
(`execution/factory.py:319-333`) from `granted_host_roots`, resolved once by the
worker (`factory.py:311`, `runtime_worker/workspace_backend_wiring.py:165-219`).
The roots are threaded into **both** the deepagents rule set and
`HostFilesystemFloor` from one value "so they cannot disagree"
(`factory.py:2375-2424`, `:2676-2681`). Children inherit the parent's
already-policy-wrapped tools — Deep Agents hands a subagent that declares no
`tools` the parent's list, and our own recursion module documents that this is the
mechanism by which a child's posture is "its parent's — equal to it, never
looser" (`recursion.py:25-31`).

A per-child directory is therefore not a permission edit; it is a **different
backend**, which means a different agent build per child.

### 8.2 The packaged app has no git

`write_journal.py:9-14` states it flatly, as the first of three reasons the undo
journal is content-addressed rather than a shadow git tree:

> "**No git.** The packaged app bundles Python and PostgreSQL; it does not bundle
> git."

So OpenCode's worktree model is doubly unavailable to us on the desktop. (For
completeness: OpenCode does **not** give subagents worktrees either. `Session.create`
takes its directory from instance state and accepts no directory argument
(`opencode/packages/opencode/src/session/session.ts:668-690`); worktrees are a
separate user-level primitive the composer offers, and the composer is replaced on
a child session (`opencode/packages/app/src/pages/session/composer/session-composer-region.tsx:143-160`).)

### 8.3 What children share, and what breaks

Children share the parent's grant. Consequences, each with its mechanism:

| Shared thing               | Mechanism                                                                                                                                                                                                                                                                       | Breaks how                                                                                                                                                                                                                                                                                                                                     |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Granted roots              | one backend, `factory.py:319-333`                                                                                                                                                                                                                                               | Two children writing the same file race. Last write wins; both land in the journal as separate records with the same `path`, and revert collapses them — "for each affected PATH the OLDEST record in the selected set wins" (`write_journal.py:317-324`). So undo is _correct_ but a user cannot undo child A's edit while keeping child B's. |
| The undo journal           | `HostFilesystemFloor` captures at six methods (`host_floor.py:378-453`)                                                                                                                                                                                                         | Works for children — the floor is in the path, so a child's write is captured.                                                                                                                                                                                                                                                                 |
| `tool_call_id` attribution | `RuntimeCallContext.current()` (`write_journal.py:266-281`), a ContextVar re-bound per model-visible call (`execution/call_identity.py:164-183`)                                                                                                                                | **A child's write attributes to the child's inner tool call**, not the parent's `task` call. That id appears in no parent-visible transcript row today, so a Changes-tab group for a child write has no anchor. **Unverified by execution** — derived from the ContextVar's bind semantics, not from a live run. Phase 0 must confirm it.      |
| Path translation           | `HostPathToolMiddleware` is installed through the harness profile's universal factories "so subagents — which inherit the parent's filesystem permissions — are covered by the same translation rather than keeping a second, untranslated door" (`host_tool_paths.py:162-167`) | Holds. Do not regress it.                                                                                                                                                                                                                                                                                                                      |

### 8.4 The narrowing that does exist

`_subagents_with_fs_permissions` attaches per-definition `FilesystemPermission`
rules to any `SubagentDefinition` with non-empty `fs_permissions`, and passes
through untouched anything without them so "the deepagents middleware applies the
parent agent's permissions" (`factory.py:2889-2903`, rules built `:2923-2930`).

**This narrows path globs against the same roots.** It cannot point a child at a
different directory, and it must not be extended to try — a child's authority
floored at the parent's is the invariant, and §7 of the security invariants doc
(`services/ai-backend/docs/architecture/04-security-invariants.md:118-129`) is
what it protects.

### 8.5 Decision

Children share the parent's workspace grant. We do not build per-child isolation.
We do fix attribution (§8.3 row 3) so a child's write is locatable, because an
unattributable write in the Changes tab is the "two systems that disagree, with
the trusted one wrong" posture.

---

## 9. Contracts

### 9.1 Python — new

```python
# agent_runtime/delegation/subagents/fanout.py                          (NEW)

class DelegationFanoutPolicy:
    """Per-tick child admission, snapshotted once and enforced in-process.

    Built at graph-build time (run start) so a mid-run configuration change
    cannot retro-authorize a dispatch the run did not start with — the same
    snapshot-then-enforce shape ``DelegationDepthPolicy`` uses
    (recursion.py:69-107) and ``ToolUsePolicySnapshot`` before it.
    """

    __slots__ = ("_max_children_per_tick", "_lock", "_charged")

    def __init__(self, max_children_per_tick: int = Defaults.MAX_CHILDREN_PER_TICK) -> None: ...

    @classmethod
    def snapshot(cls) -> "DelegationFanoutPolicy":
        """Read the ceiling from the hyperparameter document, once.

        The document import is function-local for the same cycle reason
        ``DelegationDepthPolicy.snapshot`` documents (recursion.py:86-92).
        An unreadable or out-of-range value falls back to the packaged
        default, which is the most restrictive useful value.
        """

    @property
    def max_children_per_tick(self) -> int: ...

    def admit(self, *, execution_scope: str, model_turn: int) -> "SubagentError | None":
        """Charge one child against this tick; return the typed refusal or None.

        Lock-guarded read-then-charge: a turn's tool calls run concurrently, so
        two callers that both read "one left" would both dispatch
        (tool_budget_guard.py:169-171).
        """
```

```python
# agent_runtime/delegation/subagents/constants.py                    (EXTENDED)

class Defaults:
    MAX_CHILDREN_PER_TICK = 4          # NEW

class Limits:
    FANOUT_MAX = 8                     # NEW — equals coordination._MAX_DIRECT_CHILDREN

class Messages:
    class Delegation:
        @classmethod
        def fanout_limit_exceeded(cls, *, max_children: int) -> str: ...   # NEW
```

```python
# agent_runtime/execution/run_steering.py                          (MODIFIED)

class RunSteeringInbox:
    """Per-run mailbox, now keyed by execution scope.

    ``MAX_PENDING`` becomes per key so one chatty child cannot starve the
    supervisor's mailbox.
    """

    SUPERVISOR_SCOPE: Final[str] = "supervisor"     # value-identical to
                                                    # RuntimeControlMiddleware.SUPERVISOR_SCOPE
    MAX_PENDING: Final[int] = 16                    # per key, was per inbox

    def deposit(self, message: SteeringMessage, *, scope: str = SUPERVISOR_SCOPE) -> bool: ...
    def drain(self, *, scope: str = SUPERVISOR_SCOPE) -> tuple[SteeringMessage, ...]: ...
    def pending(self, *, scope: str = SUPERVISOR_SCOPE) -> int: ...
```

```python
# agent_runtime/execution/subagent_interrupt.py                          (NEW)

class SubagentInterruptSet:
    """Which children of a run the user has asked to stop, in this process.

    Lives on ``LiveRunHandle`` beside ``steering`` for the reason that module
    already states about two registries agreeing on one lifetime
    (run_cancellation.py:11-16). A flag that outlived the registration would
    stop a child of a run this process is no longer executing.
    """

    def request(self, task_id: str) -> None: ...
    def is_requested(self, task_id: str) -> bool: ...
    def requested(self) -> frozenset[str]: ...
```

```python
# runtime_worker/run_cancellation.py                               (MODIFIED)

@dataclass(slots=True)
class LiveRunHandle:
    run_id: str
    task: asyncio.Task[object]
    steering: RunSteeringInbox
    interrupts: SubagentInterruptSet          # NEW
    cancel_requested: bool = False
```

```python
# runtime_api/schemas/runs.py                                      (EXTENDED)

class SteerSubagentRequest(RuntimeContract):
    """Redirect one child of a run already executing.

    ``requested_by_user_id`` is stamped from the verified session by the route
    before the coordinator sees it, exactly as ``SteerRunRequest`` does
    (runs.py:583-585), so a caller cannot steer someone else's child.
    """

    text: str = Field(min_length=1, max_length=SteeringMessage.MAX_TEXT_LENGTH)
    requested_by_user_id: str


class SteerSubagentResponse(RuntimeContract):
    """The accepted note and where it landed in the run's ledger.

    ``delivered`` is intentionally absent, for the reason ``SteerRunResponse``
    states (runs.py:609-612): acceptance and delivery are separate facts.
    """

    run_id: str
    task_id: str
    status: AgentRunStatus
    steer_id: str
    sequence_no: NonNegativeInt
    accepted_at: datetime


class InterruptSubagentRequest(RuntimeContract):
    """Stop one child. Carries no target beyond the path parameters."""

    requested_by_user_id: str


class InterruptSubagentOutcome(StrEnum):
    REQUESTED = "requested"              # flag set in this process
    ALREADY_TERMINAL = "already_terminal" # the child had finished
    NOT_REACHABLE = "not_reachable"      # this process is not executing the run
    UNKNOWN_TASK = "unknown_task"        # no such child on this run


class InterruptSubagentResponse(RuntimeContract):
    """What one interrupt request may honestly claim.

    Mirrors ``RunCancellationOutcome``'s discipline (run_cancellation.py:85-98):
    report what was observed, never what was hoped.
    """

    run_id: str
    task_id: str
    outcome: InterruptSubagentOutcome
    sequence_no: NonNegativeInt | None = None
    requested_at: datetime
```

```python
# runtime_api/schemas/commands.py                                  (EXTENDED)

class RuntimeSubagentSteerCommand(RuntimeContract):
    org_id: str
    run_id: str
    task_id: str
    steer_id: str
    text: str
    requested_by_user_id: str
    enqueued_at: datetime

class RuntimeSubagentInterruptCommand(RuntimeContract):
    org_id: str
    run_id: str
    task_id: str
    requested_by_user_id: str
    enqueued_at: datetime
```

```python
# runtime_api/schemas/common.py — RuntimeApiEventType                (EXTENDED)

SUBAGENT_STEERED   = "subagent_steered"     # NEW — the note, at accept time
SUBAGENT_REFUSED   = "subagent_refused"     # NEW — depth / fan-out refusal frame
```

No new `approval_kind`. No new model-visible tool. The `subagent_completed`
frame carries interruption via its existing `status` field, using
`Values.Status.CANCELLED` (`constants.py:76`), which the client already maps
(`SubagentCard.tsx:168-169`, `:187-188`).

### 9.2 Python — payload shapes

```python
# subagent_steered payload
{
    "task_id": str,             # the child
    "steer_id": str,
    "text": str,                # <= 4000, the user's own words
    "requested_by_user_id": str,
}

# subagent_refused payload
{
    "reason_code": str,         # SubagentErrorCode value
    "limit_name": str,          # "max_delegation_depth" | "max_children_per_tick"
    "limit_value": int,
    "safe_message": str,        # <= Limits.SAFE_MESSAGE_MAX_LENGTH (constants.py:122)
    "requested_subagent_name": str | None,
}
```

`subagent_completed` gains no field. An interrupted child sets
`status = "cancelled"` and `summary` to a bounded sentence naming the completed
tool-call count; `duration_ms` is already stamped (`stream_subagents.py:62-65`).

### 9.3 TypeScript — `packages/api-types`

```ts
// Additions to RuntimeApiEventType union (index.ts:746-769) and the
// literal array (index.ts:840-863) — both, or the guard drifts.
| "subagent_steered"
| "subagent_refused"

export interface SubagentSteeredPayload {
  task_id: string;
  steer_id: string;
  text: string;
  requested_by_user_id: string;
  [key: string]: unknown;
}

export interface SubagentRefusedPayload {
  reason_code: string;
  limit_name: string;
  limit_value: number;
  safe_message: string;
  requested_subagent_name?: string;
  [key: string]: unknown;
}

// RuntimeEventPayloadMap (index.ts:3938-3974)
subagent_steered: SubagentSteeredPayload;
subagent_refused: SubagentRefusedPayload;

export interface InterruptSubagentResponse {
  run_id: string;
  task_id: string;
  outcome: "requested" | "already_terminal" | "not_reachable" | "unknown_task";
  sequence_no?: number;
  requested_at: string;
}
```

### 9.4 TypeScript — `packages/chat-surface`

```ts
// SubagentCardProps (SubagentCard.tsx:29-48) — additive, default undefined
readonly onInterrupt?: (taskId: string) => Promise<void>;
readonly onSteer?: (taskId: string, text: string) => Promise<void>;
readonly onOpenChild?: (taskId: string) => void;
readonly interruptPending?: boolean;

// FleetSubagentRowProps (FleetSubagentRow.tsx:20-35) — same four.

// SubagentCardViewModel — additive
readonly reasonCode: string | null;      // typed failure reason (S9)
readonly usage: SubagentUsageView | null;// null when uncorrelated (S8)
readonly completedToolCalls: number | null;

// New — the child detail panel
export interface SubagentDetailProps {
  readonly view: SubagentCardViewModel;
  readonly events: readonly RuntimeEventEnvelope[];  // pre-filtered by the host
  readonly onBack: () => void;
  readonly onInterrupt?: (taskId: string) => Promise<void>;
  readonly onSteer?: (taskId: string, text: string) => Promise<void>;
}
```

Every prop is optional and defaults to not-rendered, so a host that wires none of
them gets today's DOM byte-for-byte. That is the compatibility rule the rail
already applies to its conditional inputs (`RunDestination.tsx:4283-4300`).

---

## 10. Edge cases

Raised, not removed. Where an edge case is hard it says so.

1. **Two concurrent `task` calls race the fan-out counter.** Lock-guarded
   read-then-charge (§3.2). Which of N is refused is not deterministic and the
   copy does not claim otherwise.
2. **A child is interrupted between `subagent_started` and its first model
   step.** The flag is read at `before_model`, so the child stops before calling
   the provider. The terminal frame reports zero completed tool calls.
3. **A child is interrupted while inside a 30-second tool call.** The tool
   completes. §5.2. The frame's count includes it.
4. **A child finishes between the user's click and the command being claimed.**
   `already_terminal`. No second frame — dedup is
   `append_task_lifecycle_event`'s (`stream_subagents.py:98-101`).
5. **The run is cancelled while a per-child interrupt is queued.** Run cancel
   wins; `close_open_subagents_as_cancelled` closes every open child
   (`stream_subagents.py:84-125`) and the queued child command becomes a
   `not_reachable` no-op.
6. **The interrupt/steer claim lands on a process not executing the run.**
   `not_reachable`, reported, never inferred (`run_cancellation.py:26-31`).
7. **A steer is deposited for a child that never makes another model call.** It
   sits in that child's key until the run's registration is released, then is
   discarded with the handle. Not delivered, and the response never claimed it
   was.
8. **A steer for the supervisor and a steer for a child arrive together.** Two
   keys, two deques, no interaction. The supervisor's drain is unchanged.
9. **`MAX_PENDING` is hit on one child's key.** Deposit returns `False` and the
   handler reports throttled — a value, not a raise, because "the caller is a
   queue handler whose command is already durable"
   (`run_steering.py:139-143`).
10. **A depth-2 run (someone raised `max_delegation_depth`).** A grandchild's
    events carry `parent_task_id` = the child's task id, so the §6 filter on a
    child returns the grandchild's events too. **Decision: correct — a child's
    view includes its own subtree.** The tree in §7.2 nests accordingly.
11. **A fleet where one child is refused at dispatch.** `_maybe_emit_fleet_started`
    counts payloads from tool-call chunks (`stream_subagents.py:461-475`), which
    are emitted before our refusal runs, so the fleet's declared `total` would
    include a child that never started. **This is a real defect in the design and
    must be handled:** the refused child emits `subagent_refused` **and** a
    terminal `subagent_completed` with `status = "failed"` so
    `_maybe_emit_fleet_finished`'s remaining-set decrements
    (`stream_subagents.py:505-537`) and the fleet card can reach `done`.
    Without this the fleet card spins forever. Same failure class as
    `close_open_subagents_as_cancelled`.
12. **A child's approval appears in two views.** §6.5 — intended, decided.
13. **Two children write the same file.** §8.3 — undo collapses to one restore of
    the content preceding both. Documented, not fixed.
14. **A child's host write attributes to an id with no parent-visible row.**
    §8.3 row 3, unverified. Phase 0 gate.
15. **A client resumes a child view after the run ended.** The filtered replay
    still works — the events are durable. Controls are hidden for a terminal run,
    the same rule the composer applies via `ACTIVE_RUN_STATUSES`
    (`RunDestination.tsx:1763-1767`).
16. **A model-supplied `task_id`.** There is no such parameter and this PRD does
    not add one. `TaskToolSchema` is deepagents' (`atlas_task_tool.py:433`) and
    the child's identity is the tool call id the runtime assigns
    (`atlas_task_tool.py:335-337`). A user-supplied `task_id` on the interrupt
    route is validated against the run's own emitted children, never trusted.
17. **A fan-out cap of 1.** Legal (floor 1). A model asking for 3 gets 1 dispatched
    and 2 refusals. Not a degenerate case; it is how a cautious deployment ships.
18. **`hyperparameters.json` says one thing and the code another.** Already true
    for `subagents.timeout_seconds` and `concurrency_limit`, which
    `constants.Defaults` documents as inert because of an import cycle
    (`constants.py:88-99`). `max_children_per_tick` must be read through
    `HyperparameterLoader` the way `max_delegation_depth` is
    (`recursion.py:99-107`), or it joins them.

---

## 11. Security considerations

Checked against
`services/ai-backend/docs/architecture/04-security-invariants.md`.

**§2 Tenant isolation (`:31-44`).** Both new routes take identity from the
verified session only and overwrite any body-supplied id, exactly as cancel does
(`routes.py:715-716`). A run belonging to another org and a run that does not
exist must produce the **same** 404 — the rule
`runtime_api/http/host_write_undo.py:8-11` states. A `task_id` that is not a child
of this run is `unknown_task`, indistinguishable from a `task_id` that does not
exist anywhere.

**§3 Worker command integrity (`:48-60`).** The new commands are queue commands
and must be validated against authoritative persisted rows before acting, the way
`RuntimeApprovalHandler` verifies `approval.run_id == command.run_id`. The steer
handler's own precedent: it re-reads the run and checks
`run.user_id != command.requested_by_user_id` before depositing
(`handlers/steer.py:57-60`). Both new handlers do the same. A forged queue
payload must not be able to stop a child of someone else's run.

**§4 Untrusted inputs (`:64-77`).** The steer text is user input, not model
output, so it is not category 1 — but it is appended to a live provider request,
which is why it is bounded at 4000 (`run_steering.py:81`) and framed with an
explicit `<user_steering>` block naming the author
(`run_steering.py:56-71`). The per-child note reuses that framing verbatim; it
must not get its own. An unlabelled sentence appended to a child's transcript
"reads as either a tool result or a system instruction depending on position"
(`run_steering.py:58-63`).

**§5 Credential hygiene (`:80-96`).** Nothing here touches provider keys, the
broker token, or the service token. The new payloads carry no secrets. Stated so
a reviewer does not have to re-derive it.

**§6 Event redaction (`:100-114`).** The steer text is user-authored free text
that cannot be pre-classified, the same position user message text occupies —
"length-clipped in logs but not value-redacted, since it cannot be pre-classified
as sensitive" (`:112-114`). The `subagent_steered` payload inherits that carve-out
and no other. `subagent_refused` carries only enum values and a bounded
`safe_message` from our own `Messages` class — no model text, no paths.

**§7 Subagent history isolation (`:118-129`).** Unchanged and load-bearing.
Neither new control widens what a child receives: a note is one bounded message
addressed to that child, and an interrupt is a flag. Neither passes conversation
history. A reviewer should check that the child detail view (§7.3) does not become
a channel in the other direction — it renders events the child already emitted, to
the user, not to another child.

**§8 Audit log immutability (`:133-148`).** A per-child interrupt is a user action
against a run and belongs in the audit log for the same reason a revert does — "An
unlogged undo would be indistinguishable from the agent quietly writing again"
(`agent_runtime/api/host_write_undo_service.py:16-19`). New audit actions:
`subagent.interrupt`, `subagent.steer`. Without them, a compliance reader sees a
child that stopped early with no record of who stopped it.

**§10 Path traversal (`:165-171`).** Untouched — no path crosses either new
route.

**Not bypassing `capabilities/` middleware.** The interrupt refusal for a flagged
child's next tool call sits at the same position `ToolBudgetReject` occupies —
before invocation, inside the existing middleware chain
(`tool_budget_middleware.py:41-43`). It does not short-circuit the policy
middleware, and it is not a new dispatch path. `PolicyToolMiddleware` remains
`MIDDLEWARE_ORDER[0]` (`capabilities/mcp/middleware/policy_tool.py:1-30`).

---

## 12. Observability

**Events** (all on the existing per-run ordered stream, all inside the sealed
prefix):

| Event                                          | When                                             | Producer                                                                                       |
| ---------------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| `subagent_refused`                             | a `task` call is refused by depth or fan-out     | `atlas_task_tool`, before dispatch                                                             |
| `subagent_steered`                             | a per-child note is **accepted** (not delivered) | `RunCoordinator`, under the non-terminal check — never the handler (`handlers/steer.py:10-16`) |
| `subagent_completed` with `status="cancelled"` | an interrupted child stops                       | existing producer, existing dedup                                                              |

**Metrics** — none new. Counts are derivable from the events, and a second
counting surface is a second source of truth. Recorded as a decision so nobody
adds one "for the dashboard".

**Logs** — one line per undeliverable command, at the level the steer handler
already uses for the same case (`handlers/steer.py`, `_LOGGER`). No user text in
the log line: the `steer_id`, the `run_id`, the `task_id`, and the outcome.

**What must be observable after this ships, and by what:**

- "Did the fan-out cap fire in production?" → count `subagent_refused` with
  `limit_name = "max_children_per_tick"`.
- "Do users actually stop children?" → count cancelled `subagent_completed` whose
  run status stayed `RUNNING`.
- "Is a fleet card ever stuck?" → a `subagent_fleet_started` with no matching
  `subagent_fleet_finished` on a terminal run. This is checkable today and, per
  edge case 11, is exactly what a refused child would cause if we got it wrong.

---

## 13. Tests

Following `services/ai-backend/tests/CLAUDE.md` and the precedent set by
`tests/unit/runtime_worker/test_stop_cancels_subagent.py`, whose header states
the discipline this PRD's tests inherit: prove properties "where production runs
it rather than by calling a handler directly", substituting only the chat model.

### Backend — new

| Test                                                 | Asserts                                                                                                                                           | Story        |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| `test_fanout_cap_refuses_surplus_in_one_tick`        | N+1 concurrent `task` calls in one assistant message → exactly N children dispatched, surplus returns `CONCURRENCY_LIMIT_EXCEEDED` as a **value** | S5           |
| `test_fanout_cap_is_per_tick_not_per_run`            | 4 + 4 across two turns both succeed under a cap of 4                                                                                              | S5           |
| `test_fanout_cap_snapshotted_at_run_start`           | mutating the document mid-run does not raise the live cap                                                                                         | S5, D6       |
| `test_fanout_refusal_emits_frame_and_closes_fleet`   | a refused child emits `subagent_refused` **and** a terminal frame, and `subagent_fleet_finished` fires                                            | edge 11      |
| `test_depth_refusal_emits_frame`                     | today's string refusal now also draws a frame carrying `limit_value`                                                                              | S6           |
| `test_interrupt_stops_one_child_only`                | siblings keep running, run stays `RUNNING`, the child gets `status="cancelled"`                                                                   | S2           |
| `test_interrupt_does_not_tear_down_inflight_tool`    | a child interrupted mid-tool completes that tool, then stops                                                                                      | S3, edge 3   |
| `test_interrupt_already_terminal_is_noop`            | `already_terminal`, no second frame                                                                                                               | edge 4       |
| `test_interrupt_on_foreign_run_is_404`               | another org's run and a nonexistent run are indistinguishable                                                                                     | §11          |
| `test_interrupt_forged_command_rejected`             | a queue payload whose `requested_by_user_id` does not own the run does nothing                                                                    | §11          |
| `test_child_steer_reaches_only_that_child`           | supervisor and siblings do not see the note                                                                                                       | S4, D5       |
| `test_child_steer_delivered_at_model_step`           | a note deposited mid-tool arrives at the child's next `before_model`                                                                              | S4           |
| `test_child_steer_mailbox_bound_is_per_key`          | 16 notes on one child do not block a note to the supervisor                                                                                       | edge 9       |
| `test_child_steer_appended_at_accept_not_by_handler` | the `subagent_steered` frame's `sequence_no` precedes the run's terminal event                                                                    | §11, seal    |
| `test_filtered_replay_returns_child_subtree`         | filter on `T` returns `task_id == T` ∪ `parent_task_id == T`, in `sequence_no` order                                                              | S1, edge 10  |
| `test_filtered_replay_scoped_to_caller`              | filtering cannot read another org's run                                                                                                           | §11          |
| `test_run_cancel_still_closes_every_child`           | the S10 regression guard, run alongside the new per-child path                                                                                    | S10          |
| `test_child_host_write_attributes_to_locatable_id`   | a child's journal record carries an id the client can resolve                                                                                     | S11, edge 14 |

### Frontend — new

| Test                                   | Asserts                                                                                                                                                       | Story  |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| `SubagentTree.test.tsx`                | children render in dispatch order, not recency; a depth-2 child nests                                                                                         | S7     |
| `FleetSubagentRow.controls.test.tsx`   | Stop/Note render only for non-terminal rows; absent props ⇒ absent DOM                                                                                        | S2, S4 |
| `SubagentDetail.test.tsx`              | filtered events render with the thread's renderers; empty child shows the single-shot copy                                                                    | S1     |
| `subagentCardViewModel.reason.test.ts` | a typed `reason_code` maps to fixed copy; unknown code falls back without crashing                                                                            | S9     |
| `subagentCardViewModel.usage.test.ts`  | absent usage renders nothing, never a zero                                                                                                                    | S8     |
| `RunWorkspaceRail.agents.test.tsx`     | no cross-run rows in the Agents panel; no control whose handler is a no-op                                                                                    | S7     |
| `AgentsPanel.layout.test.ts`           | `getComputedStyle` against the real stylesheet, not a DOM-presence assertion — the disclosure and the new row controls have non-zero size at the rail's width | —      |

That last one is deliberate. A green DOM assertion is not a green screen: CSS once
clipped a disclosure to a fraction of its ink while the suite passed. Assert
layout against the real stylesheet.

### Desktop

One journey in `tools/desktop-journeys/` that dispatches a fleet, opens a child,
stops it, and asserts the run continued. Read
`tools/desktop-journeys/README.md` before writing it — phases share one boot, and
a stale route or run history breaks a _later_ phase with a symptom-shaped message.

---

## 14. Phased plan

Depth caps are already done (§0.1), so the cheap first slice is fan-out plus
refusal visibility. Interrupt is second because it needs no new UI surface beyond
one button. Inspection is third because it needs a panel.

### Phase 0 — Verify, do not build (½ day)

Three unverified claims gate the rest. Each is a live run, not a unit test.

1. Does a child's host write attribute to the child's inner `tool_call_id` or the
   parent's `task` call id? (§8.3 row 3.)
2. Does a tool-budget rejection draw a user-visible card today? (§3.3 row 3.)
3. Does `_maybe_emit_fleet_started` fire before a refusal could run, i.e. is edge
   case 11 real? Read `stream_subagents.py:461-475` against a live fleet.

Also: `run_cancellation.py:6` references `LiveBatchAdmissionRegistry`, which does
not exist anywhere in the repo (verified by grep). Fix the docstring or find what
it meant.

**Exit:** three answers, each with a citation or a transcript.

### Phase 1 — Bounded fan-out + visible refusals (S5, S6)

- `DelegationFanoutPolicy` + `Defaults.MAX_CHILDREN_PER_TICK` +
  `Limits.FANOUT_MAX` + the refusal message.
- Wire it into `atlas_task_tool` beside the depth check.
- `subagent_refused` event, both producers (depth and fan-out).
- The terminal frame for a refused child so the fleet card closes (edge 11).
- Delete or wire `execution.max_parallel_subagents` — a config key with zero
  consumers is a lie in a document users read.

**Why first:** no new route, no new UI surface, no new wire contract beyond one
event. It removes the only genuinely uncapped axis.

### Phase 2 — Per-child interrupt (S2, S3, S10)

- `SubagentInterruptSet` on `LiveRunHandle`; the queue command; the handler; the
  route; the facade forward.
- Middleware reads at `before_model` and before tool invocation.
- `task` returns a typed interrupted result.
- Stop control on `FleetSubagentRow` and `SubagentCard`, host-wired in
  `RunDestination`.
- Audit action `subagent.interrupt`.

**Why second:** one button, one route, and it is the control users ask for first.

### Phase 3 — Child transcript (S1, S12)

- `?subagent_task_id=` on the replay route.
- Client-side filter for the live stream (no second subscription).
- `SubagentDetail` panel + open-a-child from the row name.
- Typed failure reason and usage on the view model (S8, S9) — they land here
  because the detail header is where they are read.

### Phase 4 — Agents panel redesign (S7)

- Tree replaces the flat list; dispatch order replaces recency.
- Cross-run list leaves the panel (§7.4).
- Scope heading; empty copy that matches the population.
- The layout test.

### Phase 5 — Per-child steer (S4)

- Keyed `RunSteeringInbox`; the command; the handler; the route.
- `subagent_steered` at accept time.
- Note control on the row and in the detail footer.
- Audit action `subagent.steer`.

**Why last:** it is the most speculative of the five. A user who can inspect and
stop a child may not want to talk to one, and shipping 1–4 first tells us.

### Explicitly not in this plan

Background dispatch (§4), spawn pause (§5.4), child resume (§5.4), per-child
worktrees (§8), raising the default depth (D12).

---

## 15. Rejected alternatives

**Give each child a real run row, with `parent_run_id`.** This is OpenCode's model
and it is genuinely better for inspection — a child gets a URL, an event stream,
and a cursor for free. Rejected because a child is an in-process LangGraph
invocation inside one graph loop (`atlas_task_tool.py:373`), and making it a run
means the run lifecycle, the queue, the seal, and the cockpit's run binding all
have to learn about parents. That is a program. The filtered-replay design in §6
buys ~80% of the inspection value for a query parameter.

**Cancel a child by cancelling a task.** Rejected: there is no child task. The
child runs on the parent's task, which is why our own source says "The child stops
when — and only when — the task executing the parent stops"
(`run_cancellation.py:18-23`).

**Enforce fan-out in `coordination.py`'s planner.** The planner already has
`max_children` (`coordination.py:303`) and a full admission vocabulary. Rejected
because the planner "deliberately stops before dispatch"
(`coordination.py:3-5`) and has no product caller — wiring it would mean adopting
its whole batch-request contract on a path where the model emits N independent
tool calls, not one batch. The reference implementations agree: OpenCode also has
no batch parameter, and gets parallelism from the model emitting multiple tool_use
blocks (`opencode/packages/opencode/src/tool/task.ts` + `task.txt` usage note 1).

**Reuse the wildcard tool budget for fan-out.** Rejected: it is per run, not per
tick (`tool_budgets.py:57-58`), so it cannot tell 10-at-once from 10-over-10-turns
— which is the entire distinction fan-out is about.

**A second SSE subscription for the open child.** Rejected by D10 and by
`subagentProjection.ts:8-16`, which exists to hold the one-projector invariant.

**A global "pause spawning" switch.** Hermes's is module-global
(`hermes-agent/tools/delegate_tool.py:154-169`), which is wrong for a
multi-tenant service. A correct run-scoped version is more work than it looks and
the fan-out cap covers most of the need. Deferred, not rejected on merit.

**Making the "This run" row clickable.** Rejected. The row is self-referential on
a run-scoped surface; the fix is removal (§7.4), not a working no-op.

---

## 16. Open questions and unverified claims

**Unverified — must be answered before implementation.**

1. **Host-write attribution inside a child.** `RuntimeCallContext.current()`
   supplies `tool_call_id` (`write_journal.py:266-281`) and is a ContextVar
   re-bound per model-visible call (`execution/call_identity.py:164-183`). I
   infer a child's write attributes to the child's inner tool call, not the
   parent's `task` call — derived from bind semantics, **not from a live run**.
   Phase 0 gate.
2. **Whether a tool-budget rejection draws a card.** I traced the model-facing
   message (`tool_budget_middleware.py:66-80`) but not the event path.
3. **Whether `_maybe_emit_fleet_started` can count a child that a later refusal
   prevents.** Read from `stream_subagents.py:461-475`; not exercised.
4. **`run_cancellation.py:6` cites `LiveBatchAdmissionRegistry`.** No such symbol
   exists in `services/ai-backend` (verified by grep over the whole service).
   Either a stale docstring or a module that was removed.
5. **Whether `subagents.timeout_seconds: 120` (`hyperparameters.json:51`) is
   enforced on the live `task` path.** `Defaults.SUBAGENT_TIMEOUT_SECONDS = 120`
   (`constants.py:102`) is documented as one of two values the document cannot
   read (`constants.py:88-99`). I did not trace whether anything times a child
   out. **If nothing does, a wedged child holds the parent's tool node open with
   no bound**, which would make per-child interrupt more urgent, not less.
6. **Whether LangGraph runs a turn's `task` calls concurrently or serially.**
   `tool_budget_guard.py:169-171` says "the graph now runs a turn's tool calls
   concurrently", which I take as authoritative for the lock requirement in §3.2
   — but I did not verify it for the `task` tool specifically, and it changes how
   `max_children_per_tick` behaves (a concurrency cap versus a dispatch cap).

**Open product questions.**

7. Should a child's Stop offer "stop and keep what it found" versus "stop and
   discard"? This PRD assumes keep, because discard has no mechanism — the child's
   partial output is in the parent's graph state, not in a buffer we own.
8. Does the fan-out cap apply per **supervisor** or per **run**? This PRD says per
   execution scope per model turn, which means a depth-2 orchestrator gets its own
   budget. At the default depth of 1 the two are identical, so the decision is
   free today and expensive to change later.
9. Should `subagent_refused` be visible in Focus mode, or Studio only? Focus omits
   tool cards and subagents today (`project_focus_mode_activity`), so a refusal
   frame would be the first subagent-shaped thing Focus shows.

---

## Appendix A — Reference-implementation deltas worth keeping in view

Not requirements. Recorded because each is a design we considered and did not
take, with the file that would be the starting point if we changed our mind.

| Behaviour        | OpenCode                                                                  | Hermes                                                                         | Us                                                                                                            |
| ---------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| Child identity   | `Session` row with `parentID` (`session.ts:668-690`)                      | `AIAgent` on a thread pool with its own `session_id` (`delegate_tool.py:2126`) | tool call id inside one run (`atlas_task_tool.py:335-337`)                                                    |
| Dispatch         | foreground default, background behind a flag (`task.ts:98-102`)           | background mandatory at top level (`run_agent.py:7450-7468`)                   | blocking, always (`atlas_task_tool.py:373`)                                                                   |
| Depth default    | 1 (`task.ts:104-117`)                                                     | 1 (`delegate_tool.py:2827-2837`)                                               | 1 (`constants.py:109`)                                                                                        |
| Fan-out cap      | none found                                                                | `max_concurrent_children`, default 3 (`delegate_tool.py:482-520`)              | **none** → §3                                                                                                 |
| Refusal shape    | raise → `orDie` → tool error part (`task.ts:357`)                         | `tool_error(str)`                                                              | typed value (`recursion.py:144-158`)                                                                          |
| Cancel one child | API can, no UI affordance (`run-state.ts:77-85`)                          | `x` / `X` in `/agents` (`agentsOverlay.tsx:705-722`)                           | **no** → §5.2                                                                                                 |
| Steer one child  | no — composer replaced on a child (`session-composer-region.tsx:143-160`) | no                                                                             | **no** → §5.3                                                                                                 |
| Child transcript | its own session URL (`message-part.tsx:2003-2020`)                        | a tailable per-child log file (`delegation_live_log.py:1-28`)                  | filtered replay → §6                                                                                          |
| Fleet object     | none — N stacked cards                                                    | TUI overlay with a tree (`agentsOverlay.tsx`)                                  | a graphical fleet card (`SubagentFleetCard.tsx`)                                                              |
| Result budget    | last text part, verbatim, uncapped (`task.ts:200-214`)                    | two-cap budget with disk spill (`delegate_tool.py:1910-1963`)                  | `RESULT_RESPONSE_MAX_LENGTH = 12_000` (`constants.py:121`) — **unverified whether enforced on the live path** |
