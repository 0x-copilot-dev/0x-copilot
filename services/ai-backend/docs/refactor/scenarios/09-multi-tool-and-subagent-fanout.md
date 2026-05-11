# Multi-tool Chains, Tool Grouping, and Subagent Fan-out

**Coverage:**

- S4 — Sequential multi-tool chain in one turn
- Tool grouping — parallel tool calls within a single turn (provider-native `parallel_tool_calls`)
- 3–4 parallel subagent fan-out — supervisor dispatches multiple subagents concurrently
- Subagent + tool combo — subagent makes its own tool calls inside its execution

**Roadmap PR dependencies:**

- [P3 Parallel `create_agent_runtime` bootstrap](../00-roadmap.md#phase-1--performance-wins-no-structural-change) — should reduce TTFB.
- [P4 Per-event DB ops consolidation](../00-roadmap.md#phase-1--performance-wins-no-structural-change) — high-event-volume runs amplify the win.
- [P7 Batch citation ingestion](../00-roadmap.md#phase-2--decoupling-foundation--hygiene) — multi-tool turns are where batching matters.
- Multi-tool parallel verification is on the [verify-first list](../00-roadmap.md#out-of-scope-items-verify-first-list) — "Is multi-tool parallel execution enabled in StreamingExecutor?"

**Status:**

- Sequential multi-tool: testable in dev (chains of `suggest_mcp_connector` calls).
- Parallel tool calls: testable only if `parallel_tool_calls` is enabled in the OpenAI integration AND the model decides to use it.
- Subagent fan-out: DEFERRED until `SubagentDefinition`s are seeded.

---

## Scenario 9.1 — Sequential multi-tool chain (S4)

### Goal

The agent decides to call multiple tools in sequence to satisfy a complex prompt. Verify the tool budget is respected, each call's lifecycle is clean, and the model integrates results in the final response.

### Preconditions

- Stack healthy.
- Default tool budget per task ≥ 3 (verify in config or by running and reading the captured `budget_warning` threshold).

### Execution

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"S4 sequential chain"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")

# Force a chain: three different connectors mentioned, each not connected.
PROMPT="I want to connect three tools to Enterprise Search: Linear (for tickets), Notion (for docs), and GitHub (for code). Suggest the install card for each one, one at a time."

RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"$PROMPT\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")

curl -s -N --max-time 120 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/stream?after_sequence=0&follow=true" > sse.txt
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/events?after_sequence=0" > replay.json
```

### Deterministic invariants

| #     | Assertion                                                                                                                                                                     |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1 | Baseline.                                                                                                                                                                     |
| INV-2 | `tool_call_started` count is 2–4 (model judgment varies; we want > 1 to prove chaining works).                                                                                |
| INV-3 | Each `call_id` appears in exactly one `tool_call_started`, ≥1 `tool_call_delta`, exactly one `tool_call_completed`, and ≥1 `tool_result`.                                     |
| INV-4 | The `tool_call_delta` events for one `call_id` are contiguous in the sequence_no order. They do NOT interleave with deltas from another `call_id` (sequential, not parallel). |
| INV-5 | Each `mcp_auth_required` event (one per suggestion) has a distinct `payload.server_id` (`seed:linear`, `seed:notion`, `seed:github`).                                         |
| INV-6 | Final run status `completed`. The model produces a final response that references all three connectors.                                                                       |
| INV-7 | No `budget_warning` event for 2–4 tool calls (budget should be ≥ 5 by default).                                                                                               |
| INV-8 | Per-call telemetry: at least one `RuntimeModelCallUsageRecord` per provider round-trip. (Once 6.2 INV-by_call-empty is fixed, verify the count here.)                         |

### Content rubric (judge)

D1, D2, D3, D5, D6. Pass threshold **8 / 10**.

- **D1 Correctness:** Response covers all three connectors (Linear, Notion, GitHub), each with a Connect-to-set-up acknowledgment.
- **D2 Tool choice:** `suggest_mcp_connector` (only) was used. NOT `ask_a_question`.
- **D3 Tool restraint:** Each connector got one suggest call, not multiple.
- **D5 Surface honesty:** Response doesn't claim any of the connectors are now connected; doesn't invent ticket/doc/code listings.
- **D6 Brevity:** Response is a short list (3 sentences or 3 bullets), not an essay.

### Pass criteria

All invariants AND judge ≥ 8/10.

### Common failure modes

- Model batches all three into one `suggest_mcp_connector` call with multiple `server_id`s in args → INV-2 may fail.
- Model verbally announces three connections but only calls the tool once → INV-2 fails. Soft-fail (model judgment, not system).
- The tool budget surprisingly is < 3 → INV-7 fails as budget_warning fires. Adjust budget config or accept the budget-warning path.

---

## Scenario 9.2 — Parallel tool grouping (provider-native parallel_tool_calls)

### Goal

Verify that when a single user turn warrants multiple independent tool calls, the model emits them **concurrently** (OpenAI Responses API `parallel_tool_calls`, Anthropic parallel tool use). The runtime must:

1. Accept multiple `tool_call_started` events in close timing.
2. Execute each tool independently — `tool_call_delta` streams may interleave across `call_id`s.
3. Aggregate all results before the next model turn.

### Preconditions

- Stack healthy.
- `parallel_tool_calls` enabled in the LangGraph / Deep Agents builder (per the [verify-first list](../00-roadmap.md#out-of-scope-items-verify-first-list)).
- Multiple read-only tools available. `suggest_mcp_connector` is the only builtin that's plausibly safe to call multiple times in one turn.

### Status

DEPENDENT on `parallel_tool_calls` configuration. Verify in code first (see `StreamingExecutor` / Deep Agents builder for the kwarg).

### Execution

A prompt that benefits from parallelism — three independent suggestions:

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"S? parallel tool calls"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")

PROMPT="In parallel — don't wait between them — suggest install cards for Linear, Notion, AND GitHub. Issue all three suggestions at the same time."

RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"$PROMPT\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")

curl -s -N --max-time 120 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/stream?after_sequence=0&follow=true" > sse.txt
```

### Deterministic invariants

| #     | Assertion                                                                                                                                                                                                                                                                                            |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1 | Baseline.                                                                                                                                                                                                                                                                                            |
| INV-2 | Three `tool_call_started` events fire, each with a distinct `call_id`.                                                                                                                                                                                                                               |
| INV-3 | The three `tool_call_started.created_at` timestamps are within 500ms of each other (parallel emission). If they span > 2 seconds, treat as sequential.                                                                                                                                               |
| INV-4 | `tool_call_delta` events for the three `call_id`s interleave in the stream — verify by ordering events by sequence_no and confirming that consecutive deltas can have different `call_id` values. (In strictly sequential execution all of call_id=A's deltas would come before any of call_id=B's.) |
| INV-5 | All three `tool_call_completed` events fire before the next model turn (model resumes after all tools resolved).                                                                                                                                                                                     |
| INV-6 | Wall-clock duration from first `tool_call_started` to last `tool_call_completed` is meaningfully less than 3× the duration of a single tool call. (Suggest: parallel-duration ≤ 1.5× single-call duration.)                                                                                          |
| INV-7 | Total events count is the sum of three independent tool lifecycles + lifecycle envelopes — no extra "join" event types.                                                                                                                                                                              |
| INV-8 | The model's next turn sees all three tool results in its messages (verify via `final_response` content matching all three connectors).                                                                                                                                                               |

### Content rubric (judge)

Same as 9.1 (D1, D2, D3, D5, D6) — pass threshold 8/10.

### Diagnostic checks

| #      | Check                                                                                                           | What it tells you                                               |
| ------ | --------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| DIAG-1 | Inspect Deep Agents / LangGraph builder for `parallel_tool_calls=True` setting.                                 | If False, parallelism is disabled and the model serializes.     |
| DIAG-2 | Inspect OpenAI Responses API kwargs in `provider_kwargs.py` for `parallel_tool_calls`.                          | Same check at the provider layer.                               |
| DIAG-3 | Verify the tool runner uses `asyncio.gather` (or equivalent) when multiple tool calls are received in one turn. | If it loops sequentially with `await`, parallelism is degraded. |

### Pass criteria

- If parallelism is enabled: all invariants hold AND judge ≥ 8/10.
- If parallelism is disabled (DIAG-1 / DIAG-2 / DIAG-3 confirm `False`): file a separate finding requesting it be enabled. This scenario fails by configuration, not by code regression.

---

## Scenario 9.3 — Subagent fan-out: 3–4 parallel subagents (DEFERRED)

### Goal

A complex research prompt that the supervisor decomposes into 3–4 parallel subagent tasks (e.g. one per data source). Verify:

1. Supervisor emits 3–4 task() tool calls in one turn.
2. Subagent runner executes them concurrently — each gets its own `task_id`, `subagent_id`, lifecycle events.
3. `SUBAGENT_FLEET_STARTED` fires once for the fleet; each child gets `SUBAGENT_STARTED`.
4. The fleet's lifecycle ends with `SUBAGENT_FLEET_FINISHED` after all children complete.
5. Supervisor receives `SubagentResult` from each and synthesizes one final answer.

### Preconditions

- At least 3 `SubagentDefinition`s seeded (e.g. `linear-researcher`, `notion-researcher`, `github-researcher`, `web-grounder`).
- Each subagent's `model_profile` configured (can be the same small model).
- Optionally, MCP connectors authenticated for the subagents to actually call tools (richer scenario).

### Status

**DEFERRED.** No subagent definitions exist in `make dev` (verified — no `SubagentDefinition(...)` constructor calls anywhere in the codebase). To unblock:

1. Add a dev-mode seeder that registers 3–4 `SubagentDefinition`s with simple personas (e.g. "linear researcher: search Linear and summarize results").
2. Register the seeder in the `DynamicSubagentCatalog.providers` chain.
3. Verify via `GET /v1/agent/conversations/{id}/subagents` after a run that lists become non-empty.

### Execution (intended)

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"S? subagent 4-way fanout"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")

PROMPT="Research customer escalations from Q3 across our data sources. Use a fleet of subagents — one for Linear tickets, one for Notion docs, one for GitHub issues, and one for web context. Run them in parallel. Give me a unified action plan with citations."

RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"$PROMPT\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")

curl -s -N --max-time 300 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/stream?after_sequence=0&follow=true" > sse.txt
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/events?after_sequence=0" > replay.json
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/conversations/$CONV_ID/subagents" > subagents.json
```

### Deterministic invariants

| #      | Assertion                                                                                                                                                                                                                                                  |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1  | Baseline.                                                                                                                                                                                                                                                  |
| INV-2  | Exactly one `subagent_fleet_started` event.                                                                                                                                                                                                                |
| INV-3  | Exactly 3 or 4 `subagent_started` events (matching the number of definitions the supervisor chose). Each has a distinct `task_id` and `subagent_id`.                                                                                                       |
| INV-4  | All `subagent_started` events fire within 1s of each other (parallel kick-off).                                                                                                                                                                            |
| INV-5  | `subagent_progress` events appear during execution for each subagent (at least one each). They interleave in the stream — verify by ordering events by sequence_no and checking that consecutive `subagent_progress` events can have different `task_id`s. |
| INV-6  | Each subagent has exactly one `subagent_completed` event with its `task_id` and a `SubagentResult` payload containing `response`, `execution_summary`, `plan_summary`.                                                                                     |
| INV-7  | Exactly one `subagent_fleet_finished` event AFTER all child `subagent_completed` events.                                                                                                                                                                   |
| INV-8  | `subagents.json` projection has 3–4 entries, each with `status == "completed"`, `parent_task_id` set to the supervisor's task call, and `fleet_id` matching across siblings.                                                                               |
| INV-9  | Citations from the subagents' tool calls land in the supervisor's `final_response.payload.citations` (proves the shared CitationLedger from Scenario 5.2 INV-SA-3).                                                                                        |
| INV-10 | The supervisor's final response is emitted after `subagent_fleet_finished` — not interleaved with subagent execution.                                                                                                                                      |
| INV-11 | Wall-clock: parallel-fleet duration < sum of individual subagent durations. (Parallel speedup proxy.) Suggest ≤ 1.3 × longest-single-subagent-duration.                                                                                                    |

### Content rubric (judge)

D1, D2, D3, D4, D5, D6. Pass threshold **9 / 12**.

- **D1 Correctness:** The final action plan references findings from all 4 (or all 3) subagents.
- **D2 Tool choice:** The supervisor chose the right subagents for the data sources mentioned.
- **D3 Tool restraint:** The supervisor did NOT call all subagents twice; each subagent did NOT spawn its own subagents (no infinite recursion).
- **D4 Faithfulness:** Citations from the fleet are integrated correctly — no marker references a subagent's intermediate scratchpad rather than a real source.
- **D5 Surface honesty:** If a subagent returned empty, the response notes it (e.g. "No Notion docs found") rather than fabricating.
- **D6 Brevity:** Final plan is a focused action list — not 5 sections of redundant restatement of each subagent's intermediate output.

### Pass criteria

All invariants hold AND judge ≥ 9/12.

### Diagnostic checks

| #      | Check                                                                                                                                              | What it tells you                                                                                     |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| DIAG-1 | If subagents always run sequentially (INV-4 fails), check `AsyncSubagentLifecycle` for whether tasks are awaited individually vs `asyncio.gather`. | Sequential bug at runner.                                                                             |
| DIAG-2 | If `subagent_fleet_started` doesn't fire, the event emission for fleet boundary is broken; check `runtime_worker/stream_subagents.py`.             | Stream channel misroute.                                                                              |
| DIAG-3 | If citations from subagents don't appear in the supervisor's final list, the shared ledger isn't actually shared.                                  | Critical bug in [Citation infrastructure](../00-roadmap.md#phase-4--targeted-decoupling) — block P14. |

---

## Scenario 9.4 — Subagent nested tool use (DEFERRED)

### Goal

Within a single subagent's execution, the subagent makes its own tool calls. Verify:

1. Tool calls inside a subagent have `source == "tool"` with the `subagent_id` populated on the envelope.
2. Tool budget for a subagent is independent of the supervisor's budget (or shared — document the actual behavior).
3. `SubagentResult.execution_summary` accurately reports the count of tool calls the subagent made.

### Preconditions

- 1 `SubagentDefinition` seeded (`linear-researcher`).
- Linear MCP authenticated (so its tools succeed).

### Status

DEFERRED — requires both subagent seeding AND authenticated MCP.

### Deterministic invariants

| #     | Assertion                                                                                                                                                                                                                                                   |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1 | At least one `tool_call_started` event has `subagent_id` populated.                                                                                                                                                                                         |
| INV-2 | All `tool_call_*` events with a given `subagent_id` resolve their lifecycle before the corresponding `subagent_completed` fires.                                                                                                                            |
| INV-3 | `subagent_completed.payload.execution_summary.tool_calls_count` equals the count of `tool_call_started` events with this `subagent_id`.                                                                                                                     |
| INV-4 | If the supervisor's tool budget is shared with subagents, total `tool_call_started` (supervisor + all subagents) must respect the limit, with `budget_warning` firing at the cap. If unshared, each gets its own budget. **Document the actual semantics.** |
| INV-5 | Citations ingested during the subagent's tool calls are part of the conversation-scoped ledger (per 5.2 INV-SA-3).                                                                                                                                          |

---

## Scenario 9.5 — Subagent failure isolation (DEFERRED)

### Goal

When one subagent in a fleet fails, the supervisor still aggregates results from the others rather than aborting the whole fleet.

### Preconditions

- ≥ 2 subagent definitions seeded.
- One of them is configured to deliberately fail (e.g. invalid tool args, or a stub that throws).

### Status

DEFERRED.

### Deterministic invariants

| #     | Assertion                                                                                                                                                                                 |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1 | The failing subagent emits `subagent_completed` with `status == "failed"` (or `subagent_failed` if a distinct event exists) and a typed `safe_message`.                                   |
| INV-2 | Other subagents in the fleet continue to `completed`.                                                                                                                                     |
| INV-3 | `subagent_fleet_finished` fires only after all children reach terminal (success OR fail).                                                                                                 |
| INV-4 | The supervisor's final response either (a) integrates results from successful subagents AND notes the failure, or (b) safely degrades. It does NOT pretend the failed subagent succeeded. |
| INV-5 | Run terminal is `run_completed`, not `run_failed`. Subagent failure ≠ run failure.                                                                                                        |

### Content rubric

D5. Pass threshold **1 / 2**.

- **D5 Surface honesty:** Response acknowledges the failed data source. Does NOT silently drop or hallucinate.

---

## Combined pass / fail rollup

| Scenario                          | In dev?                            | Pass gate                                                              |
| --------------------------------- | ---------------------------------- | ---------------------------------------------------------------------- |
| 9.1 sequential chain              | Yes                                | All invariants + judge 8/10                                            |
| 9.2 parallel tool calls           | Only if `parallel_tool_calls=True` | INV-3, INV-4 (interleaved deltas), INV-6 (wall-clock speedup)          |
| 9.3 3–4 parallel subagent fan-out | No (DEFERRED)                      | All invariants + judge 9/12. **Block P14, P19, P21** until this works. |
| 9.4 subagent nested tools         | No (DEFERRED)                      | Document budget semantics; verify citation namespace.                  |
| 9.5 subagent failure isolation    | No (DEFERRED)                      | Failure does not poison the fleet.                                     |

### Where this file pins refactor work

The parallel-subagent contract (9.3) is the single highest-stakes invariant in the entire system. Several Phase 5 refactors ([P17 Checkpointer](../00-roadmap.md#phase-5--major-library-swaps--structural-shifts), [P19 Repository pattern](../00-roadmap.md#phase-5--major-library-swaps--structural-shifts), [P21 HITL interrupts](../00-roadmap.md#phase-5--major-library-swaps--structural-shifts)) touch subagent execution. None should land until 9.3 is reachable and pinned green.

### Action item for the team

**Seed at least one `SubagentDefinition` in the dev path.** Without it, the entire delegation subsystem is unverifiable end-to-end. A trivial seed like:

```python
SubagentDefinition(
    slug="dev-researcher",
    display_name="Dev Researcher",
    persona="A research subagent for verifying delegation flows.",
    model_profile=ModelSelectionRequest(id="gpt-5.4-mini"),
    fs_permissions=...,
)
```

…would unblock Scenarios 9.3, 9.4, 9.5, 7.2 (non-empty path), and 5.2 (subagent-shared citation namespace). Highest-leverage dev-infra change in the inventory.
