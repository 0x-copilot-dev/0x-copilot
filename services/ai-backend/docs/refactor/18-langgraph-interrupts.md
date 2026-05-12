# Refactor PRD — LangGraph human-in-the-loop interrupts (P21 / Phase 5) — **RETRACTED**

**Status:** RETRACTED 2026-05-11 after verification spike. See [spike report](spikes/phase-5-verification.md#p21--langgraph-interrupts--verified-evidence).
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §3](../architecture/refactor-audit.md#3-library-replacements) (custom approval lifecycle row)
**Roadmap slot:** [P21](00-roadmap.md#phase-5--major-library-swaps--structural-shifts)

---

## Why retracted

The original PRD's stated goal — _"Replace the bespoke approval-interrupt mechanism with LangGraph's `interrupt()` primitive"_ — describes work that has **already shipped**.

Verification evidence:

- **`langgraph.types.interrupt` is in production today** in two paths:
  - [`agent_runtime/capabilities/mcp/middleware/auth_mcp.py:11`](../../src/agent_runtime/capabilities/mcp/middleware/auth_mcp.py) — `interrupt_handler: Callable[[dict[str, Any]], object] = langgraph_interrupt` (the default handler).
  - [`agent_runtime/capabilities/tools/builtin/ask_a_question.py:10`](../../src/agent_runtime/capabilities/tools/builtin/ask_a_question.py).
- **`astream_runtime_resume`** in [`agent_runtime/execution/runtime.py`](../../src/agent_runtime/execution/runtime.py) uses `Command(resume=resume)` — full LangGraph-native resume path.
- [`runtime_worker/handlers/approval.py`](../../src/runtime_worker/handlers/approval.py) imports `astream_runtime_resume` directly.
- `action_interrupt_events = {APPROVAL_REQUESTED, MCP_AUTH_REQUIRED}` in [`runtime_worker/streaming_executor.py`](../../src/runtime_worker/streaming_executor.py) is **not** a competing interrupt mechanism. It is the worker-side event-type recognition that tells the streaming executor "the graph paused; emit `AWAITING_APPROVAL` status and stop draining." That's integration glue between LangGraph's pause and the worker's status projection — not duplication.

The team migrated to LangGraph interrupts before this PRD was drafted. The PRD was a diagram-era misreading.

## Adjacent open question (not P21's scope)

The current `runtime_checkpointer()` defaults to `InMemorySaver`. If a worker dies while a graph is paused mid-interrupt, the LangGraph view of where the graph paused is lost — though the approval row in Postgres survives.

The genuine product question is: **do we need durable graph state to survive worker restart, given that approval rows are already durable?** If yes, swap `InMemorySaver` → `langgraph.checkpoint.postgres.PostgresSaver` (separate small PR, surfaces in the [P17 spike findings](spikes/phase-5-verification.md#revised-p17-plan) as well). If no, the current implementation is correct.

Open that decision separately. It's not a refactor — it's a product call.

---

_The original pre-spike PRD content follows for archival reference only._

---

---

## Retraction-risk disclaimer

This PRD was drafted from architecture diagrams without reading the source. Two diagram-derived Phase 4 PRDs were retracted after code review ([`11-citations-consolidation.md`](11-citations-consolidation.md), [`12-worker-stream-cleanup.md`](12-worker-stream-cleanup.md)). Notably: the P15 retraction found that `ApprovalRecognisers` — which this PRD's §1 lists as a target for deletion — is **not** a stream pattern matcher. It's a synchronous tool-args projector for approval card param rows. It is well-designed; nothing to delete.

That single finding already invalidates one of this PRD's premises. The rest of the approval lifecycle (`StreamingExecutor.action_interrupt_events`, the approval row as durable rendezvous, the `APPROVAL_RESOLVED` queue command) may equally be well-designed and not warrant replacement.

**Realistic outcomes for this PRD after verification spike (§2):**

- **Withdrawn.** Custom interrupt mechanism is well-fitted to the multi-fire + cross-process-resume requirements; LangGraph's `interrupt()` is for inline same-process cases and doesn't actually match the durable-rendezvous shape this codebase needs.
- **Partial.** Use LangGraph `interrupt()` for a narrow case (single-shot approval where resume happens in the same worker process), keep custom path for token-rotation + cross-process resume.
- **Full replacement.** Only if §2 confirms every load-bearing behavior survives.

The §2 verification matrix is the gate. Do not bias the spike toward replacement.

---

---

## 1. Problem

The approval lifecycle today is custom-built across multiple modules:

- **`StreamingExecutor.action_interrupt_events`** — a set of event types that, when emitted, short-circuit the stream loop: `{APPROVAL_REQUESTED, MCP_AUTH_REQUIRED}` (per [refactor-audit](../architecture/refactor-audit.md) and [C2 worker](../architecture/03-runtime-worker.puml)).
- **Approval row** in the persistence layer — the durable rendezvous between user click and worker resume; survives worker restart and SSE drop.
- **`RuntimeApprovalHandler`** in the worker — handles `APPROVAL_RESOLVED` queue commands; re-runs `StreamingExecutor` with resumed graph state.
- **`ApprovalRecognisers`** in [`runtime_worker/approval_recognisers.py`](../../src/runtime_worker/approval_recognisers.py) — pattern-recognizes approval requests from the LangGraph stream (per [refactor-audit §5.2](../architecture/refactor-audit.md#52-approvalrecognisers-in-the-worker)).
- **MCP auth handoff** in [`agent_runtime/capabilities/mcp/middleware/auth_mcp.py`](../../src/agent_runtime/capabilities/mcp/middleware/auth_mcp.py) — emits `MCP_AUTH_REQUIRED` event when an unauthenticated MCP tool is invoked.
- **`AWAITING_APPROVAL`** run status — a real state with documented semantics.

LangGraph has first-class human-in-the-loop primitives — [`interrupt()` and resume](https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/) — that are the canonical way to pause a graph mid-execution, persist its state, and resume from a separate trigger. The functionality overlaps significantly with what's hand-rolled here.

### What's tricky about replacing this

Approvals are not a single-shot interrupt. From [f8](../architecture/f8-mcp-auth.puml):

- **Multi-fire** — token rotation mid-run fires `MCP_AUTH_REQUIRED` again; the same approval cycle must work multiple times in a single graph execution.
- **Separate-command resume** — the resume is via an `APPROVAL_RESOLVED` queue command, not inline continuation. The user might click "Approve" from a different browser tab, hours later, after the worker has restarted.
- **Durable rendezvous** — the approval row must persist across worker restarts. The user might approve before the worker picks the next command.
- **Two event types share the path** — `APPROVAL_REQUESTED` (user-facing approval card) and `MCP_AUTH_REQUIRED` (OAuth handoff) both interrupt; both resume the same way.
- **Run state visibility** — clients see `AWAITING_APPROVAL` and expect explicit semantics (cannot send new messages on the conversation while a run is pending, etc.).

If LangGraph's interrupt mechanism doesn't preserve any one of these — particularly multi-fire and separate-command resume — replacement silently breaks approvals.

### What this is NOT

- Not a UX change. Users see the same approval cards, click the same buttons.
- Not a change to `MCP_AUTH_REQUIRED` semantics. OAuth flow against `backend` is unchanged.
- Not a change to the approval persistence schema. The row continues to be the durable rendezvous.
- Not [P17 LangGraph Checkpointer](14-langgraph-checkpointer.md). P17 is the storage; P21 is what uses it.
- Not [P15 worker stream cleanup](00-roadmap.md#phase-4--targeted-decoupling). P15 moves `ApprovalRecognisers` upstream (typed events emitted at source) regardless of whether P21 ships.

---

## 2. Verification required before approval

This is the second-highest-risk PRD in the audit (after [P19 repository collapse](16-repository-collapse.md)). Each row can flip the design.

| Question                                                                                                                                                     | How to answer                                      | If answer is X, then PRD shape changes how                                                                                                                   |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Does LangGraph's `interrupt()` survive worker process restart when paired with a Postgres `Checkpointer`?                                                    | LangGraph docs at the pinned version + spike test. | If no: the entire premise of this PRD is invalid; keep current implementation.                                                                               |
| Can a single graph execution fire `interrupt()` multiple times?                                                                                              | Spike + docs.                                      | If no: token-rotation mid-run breaks; keep custom path for that case.                                                                                        |
| Can `interrupt()` be resumed via a separate process (i.e. the resumer is a different worker than the interrupter)?                                           | Spike + docs.                                      | If no: must resume via the original worker, which contradicts the queue-claim-anywhere model. Fatal.                                                         |
| What's the latency overhead of `interrupt()` vs custom `action_interrupt_events` short-circuit?                                                              | Benchmark.                                         | If significant: keep custom path for high-throughput cases.                                                                                                  |
| Does `interrupt()` integrate with Deep Agents at our pinned version?                                                                                         | Read Deep Agents source.                           | If Deep Agents wraps LangGraph in a way that hides interrupts: we may need a Deep Agents upgrade first.                                                      |
| What does the `interrupt()` API look like? Sync vs async? Where does the resume value enter the graph?                                                       | LangGraph docs + spike.                            | Determines whether the auth-tool / approval-tool calls `interrupt()` directly (clean) or whether we wrap it.                                                 |
| How does LangGraph express "this interrupt is approval-resolved with decision=APPROVE"? Does it pass a `Command(resume=...)` value? Free-form payload?       | LangGraph docs.                                    | The resume payload must carry: decision (`APPROVE`/`DENY`/`FORWARD`/`UNDO`), approver identity, signed auth tokens (for MCP).                                |
| Can the approval row stay as the source of truth, with LangGraph just being the _mechanism_? Or does LangGraph want to own the persistence too?              | Design call after spike.                           | Recommended: approval row stays primary; LangGraph is mechanism only. If LangGraph forces its model: write a thin sync between the two.                      |
| What happens if `interrupt()` is called and the user never resolves? Is there a timeout / expiry path in LangGraph, or do we keep `ApprovalExpirySweeper`?   | LangGraph docs.                                    | Almost certainly: keep `ApprovalExpirySweeper`. LangGraph leaves expiry to the application.                                                                  |
| How does `MCP_AUTH_REQUIRED` resume signal that auth is now valid? Does the tool re-attempt automatically, or does it re-enter the graph at a specific node? | Spike.                                             | Determines whether MCP middleware needs a "post-auth retry" branch in the graph or whether `interrupt()`'s resume mechanism naturally re-runs the tool call. |

---

## 3. Goal and non-goals

### Goal

Replace the bespoke approval-interrupt mechanism with LangGraph's `interrupt()` primitive, backed by [P17's](14-langgraph-checkpointer.md) Postgres Checkpointer. Keep the approval row as the durable rendezvous and source of truth; LangGraph becomes the _graph-execution mechanism_ that pauses + resumes. Delete `StreamingExecutor.action_interrupt_events` and `ApprovalRecognisers` if [P15](00-roadmap.md#phase-4--targeted-decoupling) hasn't already.

### Non-goals

- Change UX semantics. Users see the same cards, the same buttons, the same `AWAITING_APPROVAL` state.
- Change the approval row schema or the queue-command model. `RuntimeApprovalResolvedCommand` continues to exist.
- Change how `backend` handles MCP OAuth. The token vault + backend roundtrip per [f8](../architecture/f8-mcp-auth.puml) is unchanged.
- Replace `ApprovalExpirySweeper`. Expiry / TTL is application policy; LangGraph leaves it to us.
- Change `RuntimeApprovalHandler` API. Its body changes (LangGraph resume call replaces the bespoke `StreamingExecutor` re-run); its public method stays.

### Success criteria

- `StreamingExecutor.action_interrupt_events` removed _or_ reduced to a stream-wakeup signal only (no interrupt logic).
- Approval-emitting tools / middleware call `interrupt()` directly when raising an approval (or, equivalently, the typed event-emitter does it on their behalf — see [P15](00-roadmap.md#phase-4--targeted-decoupling)).
- `RuntimeApprovalHandler.handle()` reduced to: load approval row → construct LangGraph resume payload → call LangGraph's `Command(resume=...)` against the thread.
- All five flow behaviors below preserved (§5).
- Latency on approval-resume path matches or beats current.
- Multi-fire (token rotation) covered by an integration test.
- The diff to the approval row schema is **zero**.

---

## 4. Systems touched

**Pending spike.** Provisional inventory.

### 4.1 Files possibly deleted

| File                                                                                         | Condition                                                                       |
| -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| [`runtime_worker/approval_recognisers.py`](../../src/runtime_worker/approval_recognisers.py) | If [P15](00-roadmap.md#phase-4--targeted-decoupling) hasn't already deleted it. |
| `StreamingExecutor.action_interrupt_events` set                                              | Definitely; replaced by LangGraph interrupt semantics                           |
| Any "post-interrupt re-run" bespoke logic in `StreamingExecutor`                             | Yes; LangGraph resume handles this                                              |

### 4.2 Files changed

| File                                                                                                                       | Change                                                                                                                                                         |
| -------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`runtime_worker/streaming_executor.py`](../../src/runtime_worker/streaming_executor.py)                                   | Remove interrupt short-circuit; rely on LangGraph's pause                                                                                                      |
| [`runtime_worker/handlers/approval.py`](../../src/runtime_worker/handlers/approval.py)                                     | Body becomes a LangGraph resume call                                                                                                                           |
| [`agent_runtime/capabilities/mcp/middleware/auth_mcp.py`](../../src/agent_runtime/capabilities/mcp/middleware/auth_mcp.py) | Calls `interrupt()` (or emits typed event that triggers it) when auth is needed                                                                                |
| Any approval-emitting tool/middleware (TBD inventory)                                                                      | Same: calls `interrupt()` for user approvals                                                                                                                   |
| [`agent_runtime/api/service.py`](../../src/agent_runtime/api/service.py)                                                   | `record_approval_decision()` writes the approval row + enqueues `APPROVAL_RESOLVED`. Worker's resume now talks to LangGraph thread; service surface unchanged. |
| [`agent_runtime/execution/deep_agent_builder.py`](../../src/agent_runtime/execution/deep_agent_builder.py)                 | Verifies Checkpointer is attached (per P17); confirms `interrupt()` is reachable from tool/middleware code                                                     |

### 4.3 Files added

| File                                                        | Purpose                                                                                                 |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `agent_runtime/api/approval_resume.py`                      | Thin module: load approval row → build LangGraph resume `Command(resume=...)` → return                  |
| `agent_runtime/capabilities/approvals/interrupt_helpers.py` | Helper that `interrupt()`s and atomically writes the approval row (`UNIQUE(run_id, approval_id)`-keyed) |

---

## 5. Behaviors preserved

Pulled from [refactor-audit § Behaviors that must be preserved](../architecture/refactor-audit.md#behaviors-that-must-be-preserved). Each pinned by integration test.

### Approval rendezvous

- **`AWAITING_APPROVAL` is a real run state.** Clients observe it via `RUN_STATUS` queries and SSE event status. Cannot send new conversation messages while a run is in this state (or, if allowed, the policy is unchanged).
- **Approval row is durable.** Survives worker restart. Survives SSE drop. The row persists from request emission until resolution + worker resume completes.
- **Resume via separate `APPROVAL_RESOLVED` command.** Not inline continuation. The user might be on a different device, hours later. The resume worker is whichever worker claims the next `APPROVAL_RESOLVED` command — not necessarily the original worker.

### Multi-fire

- **Token rotation mid-run.** Same MCP server's tokens expire while the graph is executing. `MCP_AUTH_REQUIRED` fires again. Same approval cycle: pause graph, user re-authenticates, resume. Same single run continues.
- **Multiple approval prompts in one run.** A graph might emit `APPROVAL_REQUESTED` from one tool and `MCP_AUTH_REQUIRED` from another later. Each pauses independently; each resumes independently.

### Stream semantics

- **Stream pauses cleanly.** No partial `MODEL_DELTA` after interrupt. The SSE adapter sees a clean `MCP_AUTH_REQUIRED` / `APPROVAL_REQUESTED` event and the stream goes quiet (or closes if the client is `follow=false`).
- **Stream resumes after `APPROVAL_RESOLVED`.** New events flow from the same `run_id`, with continuing `sequence_no` monotonicity.

### Approval expiry

- `ApprovalExpirySweeper` continues to run. An unresolved approval past TTL flips the approval row to `EXPIRED`; the run's continuation is up to policy (typically `RUN_FAILED` with a typed error).

### Event ordering

- `APPROVAL_REQUESTED` / `MCP_AUTH_REQUIRED` emitted first; then the graph pauses.
- On resume: `APPROVAL_RESOLVED` event emitted; then the resumed tool call produces its `TOOL_CALL` / `TOOL_RESULT` events; then graph continues.

---

## 6. Phasing

### Phase A — Spike

§2. **Hard prerequisite is [P17](14-langgraph-checkpointer.md) completion in production.** Without Postgres Checkpointer, interrupts cannot be durable. Spike with `PostgresSaver` in staging. Output: signed answers to every §2 question + a worked example of MCP-auth-interrupt-and-resume via `interrupt()`.

### Phase B — Coexistence

Add LangGraph-interrupt path behind a feature flag (`RUNTIME_USE_LANGGRAPH_INTERRUPTS=true|false`). Flag-off uses today's path; flag-on uses LangGraph. Both paths in tree. Integration tests cover both.

### Phase C — Staged rollout

10% / 50% / 100% by org_id (or by run_id hash). Watch metrics: approval-resolve p99, multi-fire success rate, approval-row freshness, SSE event ordering correctness.

### Phase D — Retirement

After 100% for at least two weeks: delete `action_interrupt_events`, the bespoke interrupt short-circuit, and (if not already gone via [P15](00-roadmap.md#phase-4--targeted-decoupling)) `ApprovalRecognisers`. Update `f8` flow diagram.

### Phase E — Verification

Run the entire f8 integration test suite, plus new multi-fire and worker-restart tests (see §8). Latency benchmark unchanged from [Phase 1 baseline](00-roadmap.md#phase-1--performance-wins-no-structural-change) on approval-resume p99.

---

## 7. Risks

| Risk                                                                                                                                              | Severity | Mitigation                                                                                                                                                                                               |
| ------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| LangGraph `interrupt()` doesn't support multi-fire in one graph execution                                                                         | Critical | Spike confirms. If unsupported: keep custom path for token-rotation case (hybrid).                                                                                                                       |
| `interrupt()` resume requires the same worker process — breaks queue-claim-anywhere                                                               | Critical | Spike confirms. If true: this PR is dead until LangGraph supports cross-process resume.                                                                                                                  |
| Checkpointer state grows unbounded across approvals                                                                                               | Medium   | Configure Checkpointer retention (LangGraph supports thread expiry). Tie to existing `ApprovalExpirySweeper`.                                                                                            |
| `interrupt()` performance regresses approval-emit-to-pause latency                                                                                | Medium   | Benchmark. The current path is in-process boolean check; `interrupt()` writes checkpoint = inherently slower. Acceptable if p99 stays under SLO.                                                         |
| Stream ordering changes — SSE sees a new event order around approval                                                                              | Medium   | Integration tests pin the order from §5.                                                                                                                                                                 |
| Mismatch between approval-row state and LangGraph thread state                                                                                    | High     | Approval row remains primary. Resume reads row; constructs LangGraph resume payload from it. If LangGraph thread state is missing: emit `RUN_FAILED` with a typed error rather than silently re-running. |
| Approval-row writes during interrupt require row to be created _before_ checkpointer captures pause — race                                        | Medium   | `interrupt_helpers.py` writes the row in the same transaction as the `interrupt()` call, or in a transaction that commits before `interrupt()` yields. Verify in spike.                                  |
| MCP auth retry on resume: graph re-enters at the right node?                                                                                      | High     | Spike includes a worked MCP auth round-trip. If LangGraph resumes mid-tool, the tool's auth-state check must pass on resume.                                                                             |
| `record_approval_decision()` returning 202 before worker has resumed creates a window where the user thinks they approved but nothing's happening | Medium   | Unchanged from today's behavior. Worker latency target: < 5s from `APPROVAL_RESOLVED` enqueue to `APPROVAL_RESOLVED` event emission.                                                                     |
| Deep Agents wrapping hides `interrupt()` semantics                                                                                                | Medium   | Spike verifies tool/middleware code can reach LangGraph's `interrupt()`. If Deep Agents abstracts it away: PR depends on a Deep Agents upgrade first.                                                    |

---

## 8. Unit testing requirements

Per [`docs/CLAUDE.md`](../CLAUDE.md). Every behavior in §5 pinned.

### New tests (Phase B)

- **`test_interrupt_pauses_graph.py`** — tool calls `interrupt()`; assert graph state is checkpointed; assert `AWAITING_APPROVAL` run status.
- **`test_resume_continues_graph.py`** — checkpoint present; `APPROVAL_RESOLVED` command processed; graph resumes; subsequent events fire on the same `run_id` with continuing `sequence_no`.
- **`test_resume_across_worker_restart.py`** — interrupt; kill worker process; start new worker process; deliver `APPROVAL_RESOLVED`; assert resume succeeds; assert no event-ordering anomaly.
- **`test_multi_fire_token_rotation.py`** — graph fires `MCP_AUTH_REQUIRED` twice in the same run (initial + token expiry); both cycles pause and resume correctly.
- **`test_two_distinct_approvals_in_one_run.py`** — different tools fire approvals at different graph nodes; each pauses independently; each resumes independently.
- **`test_approval_expiry.py`** — `ApprovalExpirySweeper` marks unresolved approval EXPIRED; run transitions to `RUN_FAILED` with the right error code.
- **`test_event_order_around_approval.py`** — fixed order: `MCP_AUTH_REQUIRED` → (graph paused, no events) → `APPROVAL_RESOLVED` → `TOOL_CALL` → `TOOL_RESULT` → next model deltas.
- **`test_approval_row_is_source_of_truth.py`** — when checkpoint says one thing and approval row says another (simulated drift), the row wins; if row missing, run fails fast.

### Regression — existing f8 integration tests

All current flow [f8](../architecture/f8-mcp-auth.puml) tests pass byte-identically. Add explicit assertions on `AWAITING_APPROVAL` state transitions if not already pinned.

### Performance

- Approval-resolve p99 ≤ baseline from [Phase 1](00-roadmap.md#phase-1--performance-wins-no-structural-change).
- Approval-emit-to-pause p99 ≤ 200ms (vs whatever current is; spike measures).

---

## 9. Rollback plan

Feature-flagged. `RUNTIME_USE_LANGGRAPH_INTERRUPTS=false` is the rollback. Old code lives in tree through Phase C.

After Phase D (deletion):

- `git revert` the deletion PR. Old approval logic restored (assuming P17 checkpointer remains).
- Approval rows continue to exist; the rollback worker reads them and re-runs `StreamingExecutor` the old way.
- **Critical:** the deletion PR must be its own self-contained revertible unit. Do not bundle other changes.

---

## 10. Dependencies on other roadmap items

- **Hard depends on:** [P17 LangGraph Checkpointer](14-langgraph-checkpointer.md). Cannot ship without durable interrupts; checkpointer is the durability.
- **Soft depends on:** [P15 worker stream cleanup](00-roadmap.md#phase-4--targeted-decoupling). If `ApprovalRecognisers` is already deleted by P15, this PR's diff shrinks. If not, this PR deletes it.
- **Independent of:** P18 (partitioning), P19 (repository collapse), P20 (LiteLLM providers).
- **Should land before:** [P22 RuntimeApiService split](00-roadmap.md#phase-6--coordinator-split-do-last). Coordinator's approval surface shrinks once interrupts are LangGraph-native; splitting after that is cleaner.

---

## 11. Open questions tracked from §2

(Filled in during spike.)

- [ ] LangGraph `interrupt()` survives worker restart (with `PostgresSaver`)?
- [ ] Multi-fire in one execution supported?
- [ ] Cross-process resume supported?
- [ ] Latency overhead of `interrupt()` vs current short-circuit?
- [ ] Deep Agents compatibility at pinned version?
- [ ] `interrupt()` API shape — sync vs async, resume value, payload type?
- [ ] Resume payload format documented?
- [ ] Approval row stays primary or LangGraph forces ownership?
- [ ] Expiry / TTL story — keep `ApprovalExpirySweeper`?
- [ ] MCP auth retry on resume — graph re-enters at the right node?
