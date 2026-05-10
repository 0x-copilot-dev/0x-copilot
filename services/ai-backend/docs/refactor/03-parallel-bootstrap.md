# Refactor PRD — Parallel run-start resolvers in `create_run` (Phase 1)

**Status:** Partially shipped — see [§11 — Status by phase](#11-status-by-phase)
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §4.4](../architecture/refactor-audit.md#44-sequential-bootstrap-in-create_agent_runtime)
**Roadmap entry:** [`00-roadmap.md` P3](00-roadmap.md#phase-1--performance-wins-no-structural-change)

---

## 1. Problem

Verified in code at [`agent_runtime/api/service.py:634-653`](../../src/agent_runtime/api/service.py#L634-L653) (inside `RuntimeApiService.create_run`):

```python
workspace_overrides = await self._resolve_workspace_behavior_overrides(
    org_id=request.org_id
)
user_policies_json = await self._resolve_user_policies(
    org_id=request.org_id, user_id=request.user_id
)
suggested_connectors = await self._resolve_suggested_connectors(
    org_id=request.org_id,
    user_id=request.user_id,
    paused_connectors=request.request_context.paused_connectors,
)
```

These three awaits execute serially on every `POST /v1/agent/runs`. Each is HTTP-bound in production:

- [`_resolve_workspace_behavior_overrides`](../../src/agent_runtime/api/service.py#L1635) → `WorkspaceDefaults.get_record(org_id)` (Postgres in prod)
- [`_resolve_user_policies`](../../src/agent_runtime/api/service.py#L1595) → `HttpUserPoliciesResolver.resolve(org_id, user_id)` (HTTP to `backend`)
- [`_resolve_suggested_connectors`](../../src/agent_runtime/api/service.py#L1612) → `HttpSuggestibleConnectorsResolver.resolve(org_id, user_id, exclude_paused)` (HTTP to `backend`)

Each call's docstring confirms it's a one-shot run-start resolution. The three are **mutually independent** — none reads or mutates another's output:

- All three only read `request.org_id`, `request.user_id`, and `request.request_context.paused_connectors`. Those fields are stable across the block — nothing between the three awaits writes to `request`.
- Their results are consumed together at [`service.py:654`](../../src/agent_runtime/api/service.py#L654) by `_request_with_runtime_context(...)`. They do not flow into each other.

### Why the original PRD was wrong

The original draft of this PRD targeted `agent_runtime/execution/factory.py` (`create_agent_runtime`). Verification showed:

- `create_agent_runtime` is a **synchronous** function ([`factory.py:83`](../../src/agent_runtime/execution/factory.py#L83)).
- The resolvers named in the audit are **not** invoked there. They run upstream in `RuntimeApiService.create_run` and the resolved values are already on `runtime_context.suggested_connectors` etc. by the time the worker calls the factory.

The audit's claim about "sequential bootstrap" is real, but the call site is here in `service.py`, not in the factory. This PRD is the corrected version.

### Symptoms (today)

- Cumulative run-start latency on every `POST /v1/agent/runs`. With each HTTP call at p50 ~80–150ms and p99 ~250–400ms (typical for cross-service HTTP), serial cost is **~3 × p50 ≈ 250–450ms** before the run is enqueued. Parallel cost is **~max(p50)**, with p99 dominated by the slowest single dependency.
- Latency is paid on **every** run, including approval-resume runs and follow-up turns in the same conversation.
- The frontend sees this directly as "spinner" time between tapping send and the SSE stream opening.

### What this is NOT

- Not a behavior change. Every resolver still runs; nothing is cached past the run boundary.
- Not a change to permission semantics.
- Not a change to error semantics. Each resolver's existing failure mode (e.g. `MembershipResolverUnavailable` for membership) maps to the same `RuntimeApiError` it does today.
- Not a change to `factory.py`. The factory's sync structure is out of scope here; sync→async is part of [P5 async-only ports](01-async-only-ports.md).
- Not a new caching layer.

---

## 2. Goal and non-goals

### Goal

Replace the three serial `await` calls in `create_run` with a single `asyncio.gather` so the three independent HTTP / DB roundtrips run concurrently. Drop p50 of the run-start path by ~60–70% and p99 to within ~1.2× of the slowest single resolver.

### Non-goals

- Cache resolver output past the run boundary. (Audit invariant: per-turn re-query.)
- Change the public signature of `create_run`, `_resolve_*` helpers, or `CreateRunRequest`.
- Touch any of the three resolver implementations themselves.
- Change error envelopes. Each typed exception must continue to map to the same `RuntimeApiError` (same code, same HTTP status, same retryable flag).
- Re-order any side effect observable to downstream consumers (`_request_with_runtime_context`, persistence, event producer, queue).
- Parallelize anything else in `create_run`. The earlier `_apply_workspace_default_model` await stays sequential — it mutates `request` and the gather block depends on the post-mutation `request`.

### Success criteria

- `create_run` uses one `asyncio.gather(...)` for the three resolvers; no remaining serial chain of awaits between line ~625 (`_apply_workspace_default_model`) and line ~654 (`_request_with_runtime_context`).
- Run-start path latency: p50 of `RuntimeApiService.create_run` drops by ≥50% on a representative workload measured against an HTTP-backed mock (each resolver introduces a 100ms artificial delay; serial baseline ≈300ms, parallel target ≈100–110ms).
- No change to the resolved values that flow into `_request_with_runtime_context`. Snapshot tests assert byte-identity for `workspace_overrides`, `user_policies_json`, and `suggested_connectors`.
- Existing test suite passes unchanged: `cd services/ai-backend && PYTHONPATH=src:../../packages/service-contracts/src .venv/bin/python -m pytest`.
- Failure semantics preserved: when any one of the three raises, the same `RuntimeApiError` (same `RuntimeErrorCode`, same `http_status`, same `retryable`) is raised as before. The other two in-flight tasks are cancelled.

---

## 3. Systems touched

### 3.1 Files changed

| File                                                                     | Change                                                                                                                                                                                                                                                                            |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/api/service.py`](../../src/agent_runtime/api/service.py) | Replace serial awaits at [lines 634-653](../../src/agent_runtime/api/service.py#L634-L653) with a single `asyncio.gather(...)` of the three `_resolve_*` coroutines. No other change. `import asyncio` is already present at [line 5](../../src/agent_runtime/api/service.py#L5). |

### 3.2 Files added

| File                                                                 | Purpose                                                                                                                             |
| -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `tests/unit/agent_runtime/api/test_create_run_parallel_resolvers.py` | New focused unit tests pinning the parallel-gather contract: ordering, byte-identical results, exception propagation, cancellation. |

### 3.3 Files deleted

None.

### 3.4 Verification status

All hypotheses verified in code on 2026-05-10:

- ✅ `create_run` is `async` ([`service.py:603`](../../src/agent_runtime/api/service.py#L603)).
- ✅ Three serial awaits at lines 634-653.
- ✅ Three calls are mutually independent (each takes only `request.org_id`, `request.user_id`, `request.request_context.paused_connectors`; no inter-dependency).
- ✅ `request` is not mutated between the three awaits.
- ✅ Results are consumed together at line 654 (`_request_with_runtime_context`).
- ✅ All three resolver `Protocol`s define `async def resolve(...)` returning a JSON-serialisable value (verified in [`user_policies_resolver.py:75`](../../src/agent_runtime/api/user_policies_resolver.py#L75) and [`suggestible_connectors_resolver.py:59`](../../src/agent_runtime/api/suggestible_connectors_resolver.py#L59); `_resolve_workspace_behavior_overrides` reads from `WorkspaceDefaultsService.get_record` which is async).
- ✅ `import asyncio` already at [`service.py:5`](../../src/agent_runtime/api/service.py#L5) — no new import needed.
- ✅ The earlier ([`service.py:625`](../../src/agent_runtime/api/service.py#L625)) `request = await self._apply_workspace_default_model(request=request)` mutates the request and must remain serial before the gather block.

---

## 4. Design

### 4.1 The change (one-line diff in spirit)

```python
# Before — three serial awaits.
workspace_overrides = await self._resolve_workspace_behavior_overrides(
    org_id=request.org_id
)
user_policies_json = await self._resolve_user_policies(
    org_id=request.org_id, user_id=request.user_id
)
suggested_connectors = await self._resolve_suggested_connectors(
    org_id=request.org_id,
    user_id=request.user_id,
    paused_connectors=request.request_context.paused_connectors,
)

# After — one parallel gather.
(
    workspace_overrides,
    user_policies_json,
    suggested_connectors,
) = await asyncio.gather(
    self._resolve_workspace_behavior_overrides(org_id=request.org_id),
    self._resolve_user_policies(
        org_id=request.org_id, user_id=request.user_id
    ),
    self._resolve_suggested_connectors(
        org_id=request.org_id,
        user_id=request.user_id,
        paused_connectors=request.request_context.paused_connectors,
    ),
)
```

The block's prose comments (PR 4.3 / PR 8.0.5 / PR 4.4.7 references explaining each call) should be preserved in a single comment above the gather, not deleted. Future readers need to know what each line is.

### 4.2 Exception semantics

`asyncio.gather` raises the first exception encountered and cancels pending tasks. This matches today's behavior (the first failing await aborts the function) with one wrinkle: pending sibling coroutines now receive `asyncio.CancelledError` and may execute their `except` / `finally` blocks. Two implications:

1. **Resolver error mapping is preserved.** Each `_resolve_*` helper either returns a value or raises a typed exception; gather propagates the typed exception unchanged.
2. **Cancelled siblings.** None of the three resolvers hold cross-call state (verified — each is a pure resolve). Cancellation is safe.

If verification ever surfaces a resolver that holds half-written state across a yield point, switch to `asyncio.gather(..., return_exceptions=True)` and re-raise the first exception manually. Default mode is preferred; only deviate on evidence.

### 4.3 Observability

- No new event types on the run event stream. This is purely an internal optimization.
- Existing structured logs from each `_resolve_*` helper continue to fire on entry / exit.
- OTel span propagation through `asyncio.gather` works correctly with the OTel SDK's contextvars-based context (verified by a test in [§7](#7-unit-testing-requirements)).

### 4.4 Concurrency behavior under load

- The change introduces 3-way fan-out per `create_run` call. With `RUNTIME_MAX_PARALLEL_RUNS=N` workers and concurrent run-creates, peak in-flight HTTP-to-backend connections from this path triples.
- Verify the `httpx.AsyncClient` shared by the resolvers (constructed in `RuntimeApiAppFactory` per [`runtime_api/app.py:200-213`](../../src/runtime_api/app.py#L200-L213)) has a connection pool sized for the new peak. If it's at the default (~10 connections), bump to ≥30 before rolling out.
- The Postgres path (`workspace_overrides` reads from the local store) shares the same pool the rest of `create_run` uses. Three concurrent reads per run-create against a properly sized pool is well within budget.

---

## 5. Behaviors that must be preserved

| Behavior                                                                                                                                                                   | Pinned by                                                                                                                                                                                                                                   |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ai-backend re-queries the resolvers on every run (no cache).                                                                                                               | Test: two consecutive `create_run` calls invoke each resolver mock exactly twice (no memoization).                                                                                                                                          |
| Resolved values are byte-identical to pre-change. `workspace_overrides`, `user_policies_json`, `suggested_connectors` flow into `_request_with_runtime_context` unchanged. | Test: snapshot all three values from a fixed-input `create_run` call and assert deep equality vs the pre-change snapshot.                                                                                                                   |
| Typed errors are preserved. Each resolver's failure mode raises the same `RuntimeApiError` / `RuntimeErrorCode` as today.                                                  | Parametrized test: each known failure mode (workspace overrides 5xx, policies 5xx, suggestible-connectors 5xx) propagates with the same code + HTTP status + retryable flag.                                                                |
| Failure short-circuits the run-create. A resolver failure prevents persistence + queue side effects.                                                                       | Test: when one resolver raises, `persistence.create_run_with_user_message`, `event_producer.append_api_event`, `queue.enqueue_run` are never invoked.                                                                                       |
| Cancellation safety. `create_run` cancellation between awaits leaves no partial state.                                                                                     | Test: cancel the parent task during the gather; assert no persistence / queue calls happen and no resolver is left pending.                                                                                                                 |
| Trace context propagates through the gather to each resolver.                                                                                                              | Test: capture spans via the test tracer harness; assert each `_resolve_*` span has the expected parent (the active OTel span at gather time).                                                                                               |
| `_apply_workspace_default_model` runs strictly before the gather.                                                                                                          | Test: mock `_apply_workspace_default_model` to mutate `request.model`; assert all three resolvers see the post-mutation `request.model` (proxy: `request.org_id` is unaffected, but the ordering check itself uses an event recorder mock). |
| `_request_with_runtime_context` runs strictly after the gather and sees all three results.                                                                                 | Test: assert `_request_with_runtime_context` receives non-None values for all three named kwargs.                                                                                                                                           |

---

## 6. Acceptance criteria

1. `create_run` uses a single `asyncio.gather(...)` for the three resolver coroutines. The serial chain is gone.
2. New tests in `tests/unit/agent_runtime/api/test_create_run_parallel_resolvers.py` pass.
3. Existing run-start tests (`test_workspace_behavior_overrides.py`, `test_fastapi_runtime_api.py`, `test_tenant_isolation_runtime_api.py`) pass unchanged.
4. Latency benchmark (artificial 100ms-per-resolver mock): pre-change ≈300ms; post-change ≈100–110ms. The benchmark is the timing test in [§7](#7-unit-testing-requirements).
5. No new `# type: ignore`. No new imports (asyncio already present).
6. Update the spec under [`docs/specs/`](../specs/) if a spec covers `create_run`. (Verify; if no per-method spec exists, no spec update required for this small change. Per [`docs/CLAUDE.md`](../CLAUDE.md), we update specs when an invariant changes; this PR introduces no new invariant.)

---

## 7. Unit testing requirements

All tests live in `tests/unit/agent_runtime/api/test_create_run_parallel_resolvers.py`. Per [`tests/CLAUDE.md`](../../tests/CLAUDE.md), use mixins for fakes/builders and assert typed errors with safe public messages.

### Test surface

| Test                                             | Asserts                                                                                                                                                                                                                                                     |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_resolvers_run_in_parallel`                 | All three resolver mocks gate on a single `asyncio.Event`; the gather completes — proving they were awaited concurrently rather than sequentially. Times out on a serial implementation.                                                                    |
| `test_total_latency_is_max_not_sum`              | Each resolver mock sleeps 100ms. Total `create_run` duration is ≤180ms (max + ~80ms slack for surrounding async work). On a serial baseline this would be ≥300ms.                                                                                           |
| `test_resolved_values_byte_identical`            | Fixed-input `create_run` produces identical `workspace_overrides`, `user_policies_json`, `suggested_connectors` to a pre-change snapshot fixture.                                                                                                           |
| `test_no_caching_across_runs`                    | Two `create_run` calls with identical inputs invoke each resolver mock exactly twice.                                                                                                                                                                       |
| `test_workspace_overrides_failure_propagates`    | Mock `_workspace_defaults().get_record` to raise; assert `RuntimeApiError` with the same `RuntimeErrorCode` as pre-change.                                                                                                                                  |
| `test_user_policies_failure_propagates`          | Mock `_user_policies_resolver.resolve` to raise its known exception; assert `RuntimeApiError` mapping is unchanged. (The resolver itself swallows non-typed errors and returns `{}` — only typed errors propagate. Verify via the resolver's own contract.) |
| `test_suggestible_connectors_failure_propagates` | Same pattern, for `_suggestible_connectors_resolver.resolve`.                                                                                                                                                                                               |
| `test_failure_short_circuits_persistence`        | Resolver mock raises; assert `persistence.create_run_with_user_message`, `event_producer.append_api_event`, `queue.enqueue_run` were NOT invoked.                                                                                                           |
| `test_cancelled_siblings_are_clean`              | One resolver raises after a 50ms sleep; the other two sleep 200ms. Assert: total duration ≤120ms (proving siblings were cancelled, not awaited fully) and the originating exception's code is what the caller sees.                                         |
| `test_create_run_cancellation_propagates`        | Start `create_run` as a task, cancel during gather. Assert task ends in `asyncio.CancelledError` and no persistence / queue calls happened.                                                                                                                 |
| `test_apply_workspace_default_model_runs_first`  | An event recorder records the order of `_apply_workspace_default_model` and the gathered resolvers. Assert `_apply_workspace_default_model` completes before any resolver mock is entered.                                                                  |
| `test_request_with_runtime_context_runs_last`    | Assert `_request_with_runtime_context` is invoked exactly once, after all three resolvers complete, with the three resolved values as named kwargs.                                                                                                         |

### Test mixins

Reuse the pattern from [`tests/unit/runtime_api/test_workspace_behavior_overrides.py`](../../tests/unit/runtime_api/test_workspace_behavior_overrides.py):

- `RuntimeApiServiceFixtureMixin` — constructs `RuntimeApiService` with in-memory store + injected resolver fakes.
- `RecordingResolverMixin` — provides resolver fakes that record invocation order and gate on a shared `asyncio.Event` for parallelism tests.

### Run

```bash
cd services/ai-backend && \
  PYTHONPATH=src:../../packages/service-contracts/src \
  .venv/bin/python -m pytest \
  tests/unit/agent_runtime/api/test_create_run_parallel_resolvers.py
```

---

## 8. Risks

| Risk                                                                                                                     | Likelihood | Mitigation                                                                                                                                                                                                                                       |
| ------------------------------------------------------------------------------------------------------------------------ | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `httpx` connection pool exhaustion under load — three concurrent backend HTTP calls per run-create across N workers.     | Low–Medium | Verify pool size in `RuntimeApiAppFactory` and bump if at default. Document the new minimum in deployment notes.                                                                                                                                 |
| Backend QPS amplification — `backend` service sees 3× the simultaneous request rate from this path.                      | Low        | The total request count per run is unchanged; only timing changes. backend's bottleneck (if any) is requests/sec averaged, not concurrency. Coordinate with backend team if their per-instance concurrency cap is < 3 × ai-backend worker count. |
| OTel context propagation through `asyncio.gather` breaks the trace tree (parent / child relationship).                   | Low        | OTel SDK's contextvars propagation works correctly with `asyncio.gather` by default. Pinned by `test_trace_context_propagates`.                                                                                                                  |
| A future contributor adds a 4th resolver to the gather without realizing it must be independent.                         | Low        | Add an inline comment above the gather: "all coroutines in this gather must be independent — see `docs/refactor/03-parallel-bootstrap.md`."                                                                                                      |
| `request.request_context.paused_connectors` is mutated between the gather block today and isn't anymore (or vice versa). | Low        | Pinned by `test_resolved_values_byte_identical` — if any field flow changes, the snapshot diverges.                                                                                                                                              |
| Cancellation behavior in real backends (e.g. half-finished HTTP requests).                                               | Low        | `httpx.AsyncClient` cancels in-flight requests cleanly when the awaiter is cancelled. Pinned by `test_cancelled_siblings_are_clean`.                                                                                                             |
| The change ships before the test suite is wired in CI, so a regression slips into main.                                  | Low        | All new tests must pass locally and in CI before merge. PR description must list test names that ran green.                                                                                                                                      |

---

## 9. Rollback plan

The change is a 3-line code restructuring. Rollback is a `git revert`.

If a regression appears post-deploy:

1. **Revert.** Single commit; `git revert <sha>`. Behavior returns to the pre-change serial pattern.
2. **No data migration to undo.** No persistence schema changes.
3. **No client-visible API change.** No new endpoints, headers, event types, or status codes.

No feature flag is needed; the change is too small and the rollback boundary too clean.

---

## 10. Open questions / follow-ups

- **`httpx.AsyncClient` pool sizing.** Verify the configured pool size before merge. If at default, file a follow-up to bump it.
- **Future opportunity:** if the broader run-start path gains additional async resolvers (e.g. a future `_resolve_capabilities_summary`), they should join this gather rather than chain serially.
- **Subagent run-start.** Subagents that build their own runtime via the worker may have an analogous sequential pattern. Out of scope for this PRD; flag for follow-up if the worker exhibits the same shape.
- **Parallel sync calls inside `factory.py`.** A separate, smaller-impact opportunity ([refactor-audit §4.4 footnote](../architecture/refactor-audit.md#44-sequential-bootstrap-in-create_agent_runtime)). Defer until P5 (async-only ports) makes the factory async-able.

---

_This PRD is implementable today. All §3.4 verifications are complete._

---

## 11. Status by phase

| Phase                                        | Scope                                                                                                                                        | Status                                                                                                                                                  |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 11.a — `create_run` 3-resolver gather        | The original primary scope of this PRD: the three serial awaits in `RuntimeApiService.create_run`.                                           | **Shipped** — see [`agent_runtime/api/service.py:641-653`](../../src/agent_runtime/api/service.py#L641-L653). One `asyncio.gather` of three.            |
| 11.b — Factory 4-registry gather             | Originally deferred to "after P5 makes the factory async-able". P5 shipped (the factory is now `async`), so this landed alongside.           | **Shipped** — see [`agent_runtime/execution/factory.py:138-151`](../../src/agent_runtime/execution/factory.py#L138-L151). One `asyncio.gather` of four. |
| 11.c — Factory 5-way gather (`_skill_cards`) | The remaining sequential `await` in [`_assemble_harness`](../../src/agent_runtime/execution/factory.py#L198-L201) — independent of the four. | **This change.**                                                                                                                                        |

### 11.c — Factory 5-way gather

#### Problem

After 11.b, `acreate_agent_runtime` does a 4-way gather, then `_assemble_harness` issues one more `await _skill_cards(...)` before composing the system prompt. `_skill_cards` only depends on `runtime_dependencies.skill_registry` and `runtime_context` — both available at the start of `acreate_agent_runtime`. It can join the 4-way gather as a 5th branch.

Without this, the bootstrap pays `gather(4) + serial(_skill_cards)` per run — typically 30–80ms of waste depending on backend latency.

#### Change

Two edits in [`agent_runtime/execution/factory.py`](../../src/agent_runtime/execution/factory.py):

1. Extend the gather in `acreate_agent_runtime` to include `_skill_cards(...)` as the 5th branch.
2. `_assemble_harness` accepts a `skill_cards` parameter (drops its own `await _skill_cards(...)`).

The sync `create_agent_runtime` continues to compute `skill_cards` inline (it's sync) and pass to `_assemble_harness`. Both paths converge on the same `_assemble_harness` signature.

#### Behaviors preserved

- `_skill_cards` short-circuit semantics: `skill_registry is None` or missing `list_available_skills` → `()`. `asyncio.gather` schedules the coroutine, which returns the empty tuple immediately. Identical to current behavior.
- Order of fan-out tuple: tools, mcp_servers, subagents, skill_directories, **skill_cards**. The 5th slot is appended; the prior 4 keep their positions.
- Failure semantics: if any branch raises, the gather propagates and the existing `try / except AgentRuntimeError / except Exception` block in `_assemble_harness` catches and wraps unknown exceptions as `RuntimeErrorCode.RUNTIME_FACTORY_ERROR` (unchanged).
- The sync `create_agent_runtime` path is untouched in its execution semantics — it still computes skill cards inline before calling `_assemble_harness`.

#### Tests

A new file `tests/unit/agent_runtime/execution/test_parallel_bootstrap_skill_cards.py`:

- `test_factory_gathers_five_listings_concurrently` — five fakes each sleeping 50ms; total wall-clock under 150ms (would be ≥250ms serial).
- `test_skill_cards_default_to_empty_when_registry_absent` — `skill_registry=None` produces `()` for the 5th slot.
- `test_skill_cards_registry_without_list_method_returns_empty` — registry without `list_available_skills` produces `()`.
- `test_assemble_harness_signature_change_passes_skill_cards_through` — direct call to `_assemble_harness` with explicit `skill_cards` builds a harness whose `skill_cards` equals what was passed in.
- `test_branch_failure_propagates_runtime_error` — one fake raises mid-gather; bootstrap surfaces `AgentRuntimeError(RuntimeErrorCode.RUNTIME_FACTORY_ERROR)`.

#### Rollback

`git revert`. Single commit, no flag, no schema change.
