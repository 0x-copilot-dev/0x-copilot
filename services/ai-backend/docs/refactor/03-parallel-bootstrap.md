# Refactor PRD — Parallel `create_agent_runtime` bootstrap (Phase 1)

**Status:** Draft
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §4.4](../architecture/refactor-audit.md#44-sequential-bootstrap-in-create_agent_runtime)
**Roadmap entry:** [`00-roadmap.md` P3](00-roadmap.md#phase-1--performance-wins-no-structural-change)

---

## 1. Problem

[`agent_runtime/execution/factory.py`](../../src/agent_runtime/execution/factory.py)'s `create_agent_runtime` (602 LOC) is invoked from the worker on every run start ([f1](../architecture/f1-single-turn.puml), [f2](../architecture/f2-multi-turn-tool.puml), [f5](../architecture/f5-citations.puml), [f6](../architecture/f6-thinking.puml), [f8](../architecture/f8-mcp-auth.puml)) and approval-resume ([f8](../architecture/f8-mcp-auth.puml)). Per the architecture index it "validates context, resolves authorized capabilities, assembles the system prompt, applies workspace + user policy model kwargs, and hands off to the Deep Agents builder."

The seven discovery calls it performs are described in [refactor-audit §4.4](../architecture/refactor-audit.md#44-sequential-bootstrap-in-create_agent_runtime):

1. `MembershipResolver.resolve(...)` — workspace / org membership for the calling identity. HTTP to `backend` ([C4](../architecture/05-runtime-services.puml) → backend).
2. `UserPoliciesResolver.resolve(...)` — per-user policies (training opt-out, region routing, etc.). HTTP to `backend`.
3. `tool_registry.list_available_tools(context)` — authorized `ToolCard[]`. Local + permission filter ([C6](../architecture/04-capabilities.puml)).
4. `mcp_registry.list_available_servers(context)` — authorized `McpServerCard[]`. HTTP to `backend` for registry rows + auth state.
5. `subagent_catalog.list_available_subagents(context)` — authorized `SubagentDefinition[]` ([C7](../architecture/09-delegation.puml)).
6. `skill_registry.load_skill_directories(context)` — file-system + virtual skill directories.
7. `SuggestibleConnectorsResolver.resolve(...)` — catalog hints from backend for the system prompt's "suggested connectors" block.

Hypothesis from the diagrams: these are awaited sequentially in the factory body. The audit's claim is that 1–7 are mutually independent except for an identity-context dependency: resolvers (1) and (2) likely gate the others because the listing endpoints filter by membership and policy.

### Symptoms (today)

- Cumulative bootstrap latency on every run start. If each of the 5 listing/discovery calls takes p50 80ms and p99 250ms (typical for HTTP-to-backend or scoped registry reads), serial cost is **5×p50 ≈ 400ms** before the model receives a token. Parallel cost is **max(p50) ≈ 80ms**, with p99 dominated by the slowest single dependency.
- Approval-resume runs ([f8](../architecture/f8-mcp-auth.puml)) pay this latency a second time per turn.
- Subagent runs that build their own runtime via the same factory pay it a third time.
- The architecture index explicitly notes that **ai-backend caches nothing across turns: every `create_agent_runtime` re-queries the registry** — so this latency is paid in full on every turn.

### What this is NOT

- Not a behavior change. Every call still happens; nothing is cached past the run boundary.
- Not a change to permission semantics. Membership and policy resolution still gate listing.
- Not a new caching layer. (A `/v1/agent/conversations/{id}/context` cache is a separate concern — see [refactor-audit §4.9](../architecture/refactor-audit.md#49-conversationcontextbuilder-per-context-query).)
- Not a refactor of `factory.py`'s overall shape. The 602-LOC orchestrator stays. Extraction of the system-prompt assembler is a separate refactor (not yet a PRD).
- Not multi-tool parallel execution. That is a related but distinct change ([refactor-audit §4.7](../architecture/refactor-audit.md#47-multi-tool-parallel-execution-verify)).

---

## 2. Goal and non-goals

### Goal

Restructure the bootstrap inside `create_agent_runtime` into two parallel stages:

- **Stage A — identity context.** `MembershipResolver` and `UserPoliciesResolver` run in parallel (`asyncio.gather`), produce the `AgentRuntimeContext` augmentations the listing calls need.
- **Stage B — capability fan-out.** All listing / discovery calls run in parallel via `asyncio.gather` once Stage A has resolved.

Drop p50 bootstrap latency from `~sum(individual_calls)` to `~max(stage_a) + max(stage_b)`.

### Non-goals

- Cache resolver output past the run boundary. (Audit invariant: per-turn re-query.)
- Cache resolver output within a turn beyond what the resolver protocol already does internally. If a resolver implements its own short-lived cache, fine; this PRD does not introduce one.
- Change the public signature of `create_agent_runtime` or `AgentRuntimeContext`.
- Re-order side effects observable to capabilities, the prompt assembler, or the builder.
- Touch the listing methods themselves. Their signatures and internal logic are unchanged.
- Add concurrency between subagent factory invocations and the supervisor's. (Subagents construct via the same factory but on a different code path; out of scope.)

### Success criteria

- p50 of `create_agent_runtime` (timer wrapping the function body) drops by **at least 50%** on a representative run-start workload in staging.
- p99 drops to within ~1.2× of the slowest individual dependency (Stage B's max).
- No change to any observable side effect of any of the 7 calls. Specifically: the system prompt is byte-identical for a fixed input; the same `ToolCard` / `McpServerCard` / `SubagentDefinition` / skill set is used by the builder.
- No new permission paths. Stage B never starts before Stage A completes.
- A failure in any Stage B call surfaces with the same exception type and the same traceability (run/trace/correlation IDs) as before.
- Observability events (`MODEL_CALL_STARTED` upstream, no new event types added) preserve their current ordering.
- Full ai-backend test suite passes: `cd services/ai-backend && PYTHONPATH=src:../../packages/service-contracts/src .venv/bin/python -m pytest`.

---

## 3. Systems touched

### 3.1 Files changed

| File                                                                                                    | Change                                                                                                                                                                                                                                                          |
| ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/execution/factory.py`](../../src/agent_runtime/execution/factory.py)                    | Replace serial awaits in the bootstrap section with two `asyncio.gather` blocks (Stage A, Stage B). Keep the post-fan-out assembly (prompt build, kwargs, model wiring, builder kickoff) sequential. Add a tracing span around each stage.                      |
| [`agent_runtime/execution/contracts.py`](../../src/agent_runtime/execution/contracts.py) **(probably)** | If the existing `RuntimeDependencies` / `AgentRuntimeContext` shape requires Stage A outputs to be set as attributes on the context before Stage B can read them, no change. If Stage A outputs flow as locals into Stage B, no change. Verify before touching. |

### 3.2 Files added

| File                                                                                        | Purpose                                                                                                                                                                                                                     |
| ------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `services/ai-backend/tests/unit/agent_runtime/execution/test_factory_bootstrap_parallel.py` | New unit tests that lock the parallel-stage contract: A→B ordering, intra-stage independence, exception propagation, byte-identical system prompt, byte-identical capability lists. See [§7](#7-unit-testing-requirements). |

### 3.3 Files deleted

None.

### 3.4 Verification before implementation

Hypotheses from the diagrams must be confirmed in code before this PR is opened:

1. **Confirm the seven calls are awaited sequentially today.** Read `factory.py`'s bootstrap region and grep for `await` in the function body.
2. **Confirm dependency ordering.** Specifically: does any of `tool_registry`, `mcp_registry`, `subagent_catalog`, `skill_registry`, `SuggestibleConnectorsResolver` need values produced by `MembershipResolver` or `UserPoliciesResolver` to be already attached to a context object? Any other inter-dependencies — e.g. does the suggestible-connectors resolver need the MCP server list?
3. **Confirm none of the seven calls have side-effects that are order-sensitive.** Logging messages, tracing spans, or counter increments that would change observable behavior if reordered.
4. **Confirm `provider_kwargs.workspace_model_kwargs` and `user_policy_model_kwargs` only run after Stage A.** They consume the resolved policies; they must not move into Stage B.

These checks gate the PR. If any hypothesis fails, update this PRD before continuing.

---

## 4. Design

### 4.1 Two-stage parallel bootstrap

Pseudocode for the relevant region of `create_agent_runtime`:

```python
async def create_agent_runtime(context: AgentRuntimeContext, deps: RuntimeDependencies) -> AgentRuntime:
    # ... unchanged: input validation, error mapping, trace setup ...

    # Stage A — identity context. Both calls are pure resolvers; they cannot
    # depend on the listing surfaces below.
    async with tracer.span("factory.bootstrap.stage_a"):
        membership, user_policies = await asyncio.gather(
            deps.membership_resolver.resolve(context),
            deps.user_policies_resolver.resolve(context),
        )

    enriched = context.with_resolved(membership=membership, user_policies=user_policies)

    # Stage B — capability fan-out. All calls take `enriched` and produce
    # independent results.
    async with tracer.span("factory.bootstrap.stage_b"):
        tools, servers, subagents, skills, suggested = await asyncio.gather(
            deps.tool_registry.list_available_tools(enriched),
            deps.mcp_registry.list_available_servers(enriched),
            deps.subagent_catalog.list_available_subagents(enriched),
            deps.skill_registry.load_skill_directories(enriched),
            deps.suggestible_connectors_resolver.resolve(enriched),
        )

    # ... unchanged: prompt assembly, kwargs, model wiring, builder kickoff ...
```

`context.with_resolved(...)` is illustrative — if the codebase already attaches resolved values to a mutable runtime context object, that path stays. If it threads them as locals, the locals stay locals.

### 4.2 Why exactly two stages

- Stage A's two resolvers do not depend on each other (membership lookup does not consult policies and vice versa). They parallelize cleanly.
- Stage B's five listing calls each filter by the identity context, so they cannot start until Stage A completes.
- Inside Stage B, no listing is documented as needing another listing's output. (For example: `subagent_catalog.list_available_subagents` does not consume the tool list.) Verify in code per [§3.4](#34-verification-before-implementation); if any cross-listing dependency exists, this becomes Stage B1 (independent) and Stage B2 (dependent), still parallelized within each.
- Three stages would not gain anything: the assembly that follows (system prompt, kwargs, builder) is inherently sequential because each step consumes the previous step's output.

### 4.3 Exception semantics

`asyncio.gather` raises the first exception encountered and propagates it; pending tasks are cancelled. This matches the current sequential semantics where the first failing call aborts bootstrap. Two implications:

1. **No swallowed errors.** A failed `MembershipResolver` still aborts the run with the same `RuntimeErrorCode`.
2. **Cancelled siblings.** A failure in one Stage B call cancels the others. Each call's `try/except` boundary inside its own implementation must already handle `asyncio.CancelledError` cleanly. **Verify** that listing implementations don't leave half-written state on cancellation.

If either of those checks fails, gate the parallelism with `asyncio.gather(..., return_exceptions=True)` and re-raise after collecting — same end-state but no in-flight cancellations. This is a deviation from default semantics, so prefer the strict mode unless verification surfaces a specific implementation problem.

### 4.4 Observability

- Add a tracing span per stage (`factory.bootstrap.stage_a`, `factory.bootstrap.stage_b`) so OTel traces visibly show the parallelism. Each individual call already has its own child span; this just gives a parent.
- Add a structured log at the end of each stage: `bootstrap_stage_complete` with `stage` (`a` / `b`), `duration_ms`, `task_count`. Useful for dashboards once the change is in.
- No new event types on the run event stream. This is a worker-internal optimization; clients see no new envelopes.

### 4.5 Concurrency behavior under load

- Stage B fans out to up to 5 in-flight calls per active run. With `RUNTIME_MAX_PARALLEL_RUNS=N` configured worker concurrency, the worst-case in-flight count multiplies.
- Verify connection-pool sizing on the asyncpg pool ([C3](../architecture/07-adapters.puml) — `application_name=worker`) tolerates `5 × max_parallel_runs` simultaneous queries during the spike. If the pool is currently sized for "one query per run," bump it.
- Verify the backend-facing httpx client used by `MembershipResolver` / `UserPoliciesResolver` / `SuggestibleConnectorsResolver` is constructed once per process (not per call). Per-call clients would amplify connection setup latency and potentially exhaust ephemeral ports. (Likely already a singleton; check and confirm.)
- The MCP registry call goes to `backend`. Ensure the same backend instance can absorb the increased peak QPS; coordinate before rollout.

---

## 5. Behaviors that must be preserved

Pulled from [`refactor-audit.md` § Behaviors that must be preserved](../architecture/refactor-audit.md#behaviors-that-must-be-preserved). Each gets at least one test in [§7](#7-unit-testing-requirements).

| Behavior                                                                                                                                                                                | Pinned by                                                                                                                                                                  |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Permission decisions are gated by membership + policy. No listing call returns rows past an unauthorized scope.                                                                         | Test: when `MembershipResolver` raises `PermissionError`, no Stage B call is invoked (verified by mock call counts).                                                       |
| ai-backend re-queries the registry on every `create_agent_runtime` (no cache). [refactor-audit §4.4](../architecture/refactor-audit.md#44-sequential-bootstrap-in-create_agent_runtime) | Test: two consecutive runs with identical context invoke each resolver twice. No memoization.                                                                              |
| The system prompt is byte-identical for fixed input.                                                                                                                                    | Test: build the system prompt before/after via the same fixture; assert string equality.                                                                                   |
| `RuntimeErrorCode` mapping for resolver failures is preserved.                                                                                                                          | Test: each known failure mode (membership 403, policies 5xx, tool registry IOError, MCP registry timeout) raises the same typed `AgentRuntimeError` as before parallelism. |
| Trace IDs flow through every parallel call.                                                                                                                                             | Test: capture spans; assert each Stage B span has the correct parent (`factory.bootstrap.stage_b`) and the request's `trace_id`.                                           |
| `workspace_model_kwargs` / `user_policy_model_kwargs` see the resolved policies.                                                                                                        | Test: resolved policy fields are visible to the kwargs assembly that follows Stage A.                                                                                      |
| Cancellation (run cancel mid-bootstrap) does not deadlock.                                                                                                                              | Test: cancel the parent task during Stage B; assert no pending tasks remain after `asyncio.gather` raises `CancelledError`.                                                |

---

## 6. Acceptance criteria

1. `factory.py`'s bootstrap region uses two `asyncio.gather` calls. No `await` chains of length > 1 in the discovery section.
2. Tracing shows `factory.bootstrap.stage_a` and `factory.bootstrap.stage_b` spans with the expected parent / child structure on a real run.
3. New tests in `tests/unit/agent_runtime/execution/test_factory_bootstrap_parallel.py` pass; existing `test_runtime_factory.py` tests pass unchanged.
4. Latency benchmark in staging: median `create_agent_runtime` duration drops at least 50% on a workload of ≥100 runs covering simple and tool-heavy turns.
5. No change to: returned `AgentRuntime` shape, system-prompt content for fixed input, capability list contents, error envelopes for the seven failure modes.
6. No `# type: ignore` introduced.
7. Per [`docs/CLAUDE.md`](../CLAUDE.md), update the matching spec under `docs/specs/` if `factory.py` has one. (Verify: there is a [`runtime-contracts.md`](../architecture/runtime-contracts.md) but no per-file spec; if a factory spec exists, update it. Otherwise, add a one-paragraph note to `runtime-contracts.md` about Stage A / Stage B.)

---

## 7. Unit testing requirements

All tests live in `tests/unit/agent_runtime/execution/test_factory_bootstrap_parallel.py` and use the existing fake registry / resolver fixtures. Each test maps to a row in [§5](#5-behaviors-that-must-be-preserved).

| Test                                     | What it asserts                                                                                                                                                                                                                                                                        |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_stage_a_runs_before_stage_b`       | Use mocks that record invocation order; assert no Stage B mock was entered before both Stage A mocks completed.                                                                                                                                                                        |
| `test_stage_a_calls_run_in_parallel`     | Both Stage A resolvers wait on the same `asyncio.Event`. The function does not deadlock — proving they were awaited concurrently rather than sequentially.                                                                                                                             |
| `test_stage_b_calls_run_in_parallel`     | Same pattern as above with five Stage B mocks gated on a single event.                                                                                                                                                                                                                 |
| `test_membership_failure_short_circuits` | Mock `MembershipResolver` to raise; assert no Stage B mock was invoked. Failure is wrapped in the expected `AgentRuntimeError`.                                                                                                                                                        |
| `test_stage_b_failure_cancels_siblings`  | One Stage B mock raises after a delay, others sleep longer. Assert the slow ones were cancelled (received `CancelledError`) and the run's overall duration is bounded by the failing call, not the slowest. Final exception is the originating one with original message and code.     |
| `test_no_caching_across_runs`            | Call `create_agent_runtime` twice with identical context. Each resolver / registry mock receives exactly two calls.                                                                                                                                                                    |
| `test_system_prompt_is_byte_identical`   | Snapshot the system prompt for a fixed fixture; assert string equality vs a snapshot saved before the change. Use the same fixture in `tests/unit/agent_runtime/agent/test_runtime_factory.py` if one is already there for prompt assembly.                                            |
| `test_capability_set_is_byte_identical`  | Snapshot the resolved tools / servers / subagents / skills / suggested-connectors lists; assert deep equality vs a pre-change snapshot.                                                                                                                                                |
| `test_typed_errors_are_preserved`        | Parametrized: membership 403, policies 5xx, tool registry IOError, MCP registry timeout, skill registry not-found, suggestible-connectors timeout — each maps to the same `AgentRuntimeError(code=...)` as before.                                                                     |
| `test_tracing_spans_have_parents`        | Capture spans via the test tracer harness; assert each Stage B child span has parent `factory.bootstrap.stage_b`. Each Stage A child span has parent `factory.bootstrap.stage_a`.                                                                                                      |
| `test_cancellation_propagates_cleanly`   | Start `create_agent_runtime` as a task, cancel it during Stage B, assert the wrapper task ends in `asyncio.CancelledError` and no resolver mock was left in an inconsistent state (the resolver implementations themselves are out of scope, but assert their `__aexit__` was called). |
| `test_kwargs_see_resolved_policies`      | Resolved `user_policies` includes `training_opt_out=True`; assert the kwargs assembled after Stage A reflect that.                                                                                                                                                                     |

Run with:

```bash
cd services/ai-backend && \
  PYTHONPATH=src:../../packages/service-contracts/src \
  .venv/bin/python -m pytest \
  tests/unit/agent_runtime/execution/test_factory_bootstrap_parallel.py
```

---

## 8. Risks

| Risk                                                                                                                                                                 | Likelihood | Mitigation                                                                                                                                                                                                                                           |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hidden inter-dependency in Stage B (e.g. subagent listing depending on tool listing) silently broken by the fan-out.                                                 | Medium     | The verification step in [§3.4](#34-verification-before-implementation) is a hard gate. Byte-identity tests on the capability set catch it post-hoc.                                                                                                 |
| Backend QPS amplification — Stage B sends three near-simultaneous HTTP requests per run (MCP registry, suggestible connectors, possibly subagents).                  | Medium     | Verify backend's per-instance QPS budget tolerates `5 × max_parallel_runs`. If not, add a token-bucket rate limit at the resolver level (out of scope of this PR — flag as a follow-up).                                                             |
| Connection-pool starvation on the asyncpg pool when all listing calls hit Postgres at once.                                                                          | Low–Medium | Confirm pool sizing in [`runtime_adapters/postgres/`](../../src/runtime_adapters/postgres/) tolerates the new peak. The role-tagged `application_name=worker` makes this auditable in `pg_stat_activity`.                                            |
| `asyncio.gather` cancellation leaves half-written state in a listing implementation (e.g. a partially populated cache).                                              | Low        | Read each listing implementation; if any holds mutable cross-call state without a finally block, fix it as part of this PR or fall back to `return_exceptions=True` per [§4.3](#43-exception-semantics).                                             |
| Tracing-span parent / child relationship breaks (Stage B children attach to the wrong parent if the tracer's contextvar is not propagated through `asyncio.gather`). | Low        | OTel + `contextvars.copy_context` works correctly with `asyncio.gather` by default; verify with the `test_tracing_spans_have_parents` test. If the test fails, wrap each gather call in `tracer.start_as_current_span(...)` instead of `async with`. |
| Silent regression in p99 due to the slowest Stage B call dominating once others are fast.                                                                            | Inherent   | This is the intended trade-off — p99 is bounded by the slowest dependency. If that's `MembershipResolver` (HTTP), accept the bound and address it separately by reducing membership latency, not by re-serializing.                                  |
| Future contributor adds an 8th call to Stage B without realizing it must be in the gather.                                                                           | Low        | Add a comment in `factory.py` above each gather block: "all calls in this block must be independent — see docs/refactor/03-parallel-bootstrap.md."                                                                                                   |
| The change ships before the [§3.4](#34-verification-before-implementation) checks are completed and an unsafe parallelization slips in.                              | Medium     | PR description must explicitly enumerate which checks were run and link the relevant grep / read commands. Reviewers reject the PR if the checks are not documented.                                                                                 |

---

## 9. Rollback plan

This change does not introduce a feature flag. It is a tight, locally-scoped restructuring with strong test coverage; rollback is a `git revert`.

If a regression appears post-deploy:

1. **Revert.** The PR is a single commit (or squash to one). `git revert <sha>` and re-deploy. Behavior returns to pre-change state.
2. **No data migration to undo.** Nothing in this change writes to the database, the queue, or any persistent store.
3. **No client-visible API change.** Clients see no new endpoints, headers, event types, or status codes — so no client-side rollback step.

Optional: gate behind `RUNTIME_FACTORY_BOOTSTRAP_PARALLEL` (default `true`) for the first week of rollout if the staging benchmark surfaces any concern. Default to `true`; flip to `false` to fall back to serial bootstrap. Remove the flag in the next PR after the rollout window.

---

## 10. Open questions

These do not block the PRD itself but should be resolved during implementation:

- Are there any listing calls **not** in the seven enumerated above? If `factory.py` calls additional discovery I haven't seen in the diagrams, they need triage (Stage A, Stage B, or post-fan-out).
- Does `SuggestibleConnectorsResolver` ever reuse `mcp_registry`'s output? Per [f7](../architecture/f7-mcp-add.puml) the discovery service synthesizes catalog cards from backend; if the resolver calls the MCP registry first, it has a Stage B internal dependency and must come _after_ the gather. **Verify in code.**
- Is there a per-process `httpx.AsyncClient` shared across resolvers, or do they construct their own? If the latter, parallelizing magnifies setup cost and we need to consolidate first.
- Does `[atlas_task_tool.py](../../src/agent_runtime/execution/atlas_task_tool.py)` get wired up as part of bootstrap? It's flagged separately ([refactor-audit §5.4](../architecture/refactor-audit.md#54-atlas_task_toolpy-in-execution)) for a layer move; if its initialization is in the bootstrap region, decide whether it parallelizes with Stage B or stays sequential.

---

_This PRD is implementable as soon as [§3.4](#34-verification-before-implementation) is complete. Do not open the PR before then — diagram-derived hypotheses are not a substitute for reading the code._
