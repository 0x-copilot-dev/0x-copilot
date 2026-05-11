# Refactor PRD — Lifecycle ledger + tool error policy

**Status:** Draft
**Author:** runtime architecture, May 2026
**Tracks:** stuck-subagent leak ([repro](#11-real-world-repro)) + tool-exception-bypasses-LLM bug ([repro](#12-real-world-repro))

---

## 1. Problem

Two bugs surfaced in the same run. They look unrelated; they aren't.

### 1.1 Real-world repro — subagent stuck "running" forever

Run `8475dbace42f4e34a2d2fb1555a542e0`. Three subagents dispatched in parallel via the supervisor's `task` tool. Two finished. The third's inner `web_search` raised, the run was marked failed, and the FE shows the third subagent at 569 s and **still running**.

Event histogram for the run:

| event_type            | count             |
| --------------------- | ----------------- |
| `subagent_started`    | 3                 |
| `subagent_completed`  | **2**             |
| `tool_call_started`   | 1                 |
| `tool_call_completed` | 1                 |
| `tool_result`         | 1 (status=failed) |
| `run_failed`          | 1                 |

Three starts, two completes. The frontend correctly reports what it received — it just never received a terminal event for subagent #3.

`SUBAGENT_COMPLETED` is emitted from exactly one place: [`runtime_worker/stream_tools.py:207-223`](../../src/runtime_worker/stream_tools.py#L207-L223), inside the supervisor's `task`-tool result handler. When the inner subagent's tool throws, the parent run is failed _before_ the `task` tool's result ever bubbles back to the supervisor — so the only emission site never fires for the in-flight subagent.

### 1.2 Real-world repro — agent loop bypassed on tool exception

Same run. The `web_search` tool raised; the runtime translated that to:

```
tool_result        status=failed   error_code=tool_exception
tool_call_completed status=failed
run_failed
```

The LLM never saw a `ToolMessage` describing the failure. It can't reason "retry with a different query," it can't pick a different tool, it can't give up gracefully — because the agent loop short-circuits to `run_failed` before the next model step. Today every uncaught tool exception is fatal regardless of whether it was a transient connect error, a validation problem, or a genuine policy violation.

### 1.3 Why these are the same bug

Terminal-state emission is **opportunistic, not invariant**. Each `*_STARTED` is paired with `*_COMPLETED` only on the happy path; failure paths short-circuit to `run_failed` without closing nested lifecycles or returning failure to the agent. There is no data structure that tracks "what's still open on this run," and there is no chokepoint where "the run is ending" gets to enforce invariants.

### Symptoms (today)

- Subagent / tool / model lifecycle terminal events are emitted from the _success_ code path of each handler. Failure paths skip them.
- "Is this subagent still running?" is inferred by the FE from event-pair matching. Brittle and easy to break by adding a new lifecycle.
- All exceptions from tool execution → `tool_exception` → `run_failed`. There is no policy distinguishing "transient" from "validation" from "policy violation" — the runtime treats them identically.
- LSP violation: a tool that catches its own errors and returns a string lets the agent continue; a tool that raises ends the run. Tools are not substitutable in their failure semantics.

### What this is NOT

- Not a wire-format change for SSE events.
- Not a state-machine rewrite.
- Not a new retry mechanism — the per-tool `RetryingTool` already absorbs transient network errors at the wrapper layer ([refactor #13 follow-up](./13-per-run-sequence.md) is unrelated).
- Not a generalization of the event bus.

---

## 2. Goal and non-goals

### Goal

Establish two invariants that hold for every run, regardless of how it ends:

1. **Every started lifecycle entity has exactly one terminal event.** Reconciled by a per-run ledger drained at termination.
2. **Tool exceptions are observable input to the LLM by default.** A small typed-exception API opts specific failures into "fail the run" instead.

### Non-goals

- No SSE wire-format change.
- No FE event-handling change in the same PR. The FE simplification (§4.5) is an opt-in follow-up that this PRD enables but does not require.
- No per-tool retry-policy fields. Retry policy is owned by the existing `RetryingTool` wrapper layer; this PRD does not add a parallel knob.
- No turning `LifecycleLedger` into a generic event bus. It tracks paired `*_STARTED` / `*_COMPLETED` only.

### Success criteria

- The §1.1 repro (web_search raises during fan-out subagents) produces:
  - `SUBAGENT_COMPLETED status=failed` for the in-flight subagent
  - `RUN_FAILED` carrying the typed cause
  - No "stuck running" subagents on the FE
- The §1.2 repro produces a `ToolMessage` injected into the agent state with `(error_class, sanitized_message, structured_hints)`. The next LLM step receives it. Run is not failed.
- Every `*_STARTED` event in every existing test suite is paired with a `*_COMPLETED` event by run end. New regression test asserts the invariant globally.
- Sanitizer test: representative exceptions with file paths, hex IDs, connection strings, and stack traces produce sanitized messages with none of those leaking; the actionable parts (validation field names, retry-after, status code) survive.
- All existing tests pass without skipped/xfailed regressions.
- New observability metric `lifecycle_open_at_run_terminate{kind=...}` is `0` for the green path and equals the synthesized-event count for failed/cancelled runs.

---

## 3. Systems touched

Inventory derived from grep against `services/ai-backend/`.

### 3.1 Files added

| File                                                              | Purpose                                                                                                  |
| ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `agent_runtime/observability/lifecycle_ledger.py`                 | `LifecycleLedger`, `LifecycleKind`, `OpenLifecycleEntry`                                                 |
| `agent_runtime/api/run_termination.py`                            | `RunTerminationCoordinator`, `TerminationReason`                                                         |
| `agent_runtime/execution/tool_error_policy.py`                    | `ToolErrorPolicy` Protocol, `DefaultToolErrorPolicy`, `ToolErrorClassification`                          |
| `agent_runtime/execution/tool_error_sanitizer.py`                 | `ErrorSanitizer`, `ErrorHintExtractor` — strip-internals, extract-actionable                             |
| `agent_runtime/execution/tool_errors.py`                          | `RunFatalToolError` base + `BudgetExceeded`, `AuthDenied`, `PolicyViolation`, `TenantIsolationViolation` |
| `tests/unit/agent_runtime/observability/test_lifecycle_ledger.py` | Ledger unit tests                                                                                        |
| `tests/unit/agent_runtime/api/test_run_termination.py`            | Coordinator + reconciliation unit tests                                                                  |
| `tests/unit/agent_runtime/execution/test_tool_error_policy.py`    | Policy classification + sanitizer + hint extractor                                                       |
| `tests/integration/test_subagent_failure_reconciliation.py`       | End-to-end repro of §1.1 (fan-out subagents, inner tool throws)                                          |
| `tests/integration/test_tool_exception_surfaces_to_llm.py`        | End-to-end repro of §1.2 (tool throws, LLM sees ToolMessage, next step proceeds)                         |

### 3.2 Files removed

_(none in this PRD)_

The legacy `tool_exception` shape stays — it just becomes the SURFACE_TO_LLM payload. No call sites lose their handler; some gain a typed alternative.

### 3.3 Files changed

| File                                                                                                                       | Change                                                                                                                                                                                                                                                          |
| -------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`runtime_worker/stream_tools.py`](../../src/runtime_worker/stream_tools.py)                                               | Tool-result handler calls `ledger.close(TOOL_CALL, ...)`. Tool-call-started handler calls `ledger.open(...)`. The single `SUBAGENT_COMPLETED` emission at L207-223 stays as the success-path emission; reconciliation handles the failure path.                 |
| [`runtime_worker/stream_subagents.py`](../../src/runtime_worker/stream_subagents.py)                                       | Subagent-started handler calls `ledger.open(SUBAGENT, ...)`.                                                                                                                                                                                                    |
| [`runtime_worker/streaming_executor.py`](../../src/runtime_worker/streaming_executor.py)                                   | Wrap the run's main `try` in `coordinator.terminate(...)` on every exception path. Today's `append_event(RUN_FAILED)` calls become `coordinator.terminate(terminal_status=FAILED, cause=exc)`.                                                                  |
| [`agent_runtime/api/events.py`](../../src/agent_runtime/api/events.py)                                                     | `RuntimeEventProducer` constructs and owns the per-run `LifecycleLedger`. Exposes a typed accessor for the coordinator. No public-API change.                                                                                                                   |
| [`agent_runtime/execution/runtime.py`](../../src/agent_runtime/execution/runtime.py)                                       | Tool-execution wrapper routes exceptions through `ToolErrorPolicy.classify(exc, tool=tool)`. SURFACE_TO_LLM ⇒ build sanitized `ToolMessage`, append to agent state, emit `TOOL_RESULT status=failed`, return to graph. FAIL_RUN ⇒ call `coordinator.terminate`. |
| [`agent_runtime/capabilities/tool_budget_guard.py`](../../src/agent_runtime/capabilities/tool_budget_guard.py)             | When the budget enforcement is `HARD` and admission is rejected, raise `BudgetExceeded(...)` instead of returning the safe-message string. Soft-warn paths stay as today (return value, not raise).                                                             |
| [`agent_runtime/capabilities/auth_gate.py`](../../src/agent_runtime/capabilities/auth_gate.py)                             | When `check(...)` denies, raise `AuthDenied(...)` from the call site that today swallows the result and rejects silently.                                                                                                                                       |
| [`agent_runtime/capabilities/mcp/middleware/auth_mcp.py`](../../src/agent_runtime/capabilities/mcp/middleware/auth_mcp.py) | OAuth-required errors raise `AuthDenied`; transient remote-MCP errors keep raising plain `Exception` (default policy: SURFACE_TO_LLM, "OAuth required" is a hint the LLM can act on).                                                                           |
| [`agent_runtime/execution/tool_outcomes.py`](../../src/agent_runtime/execution/tool_outcomes.py)                           | New `ToolErrorOutcome` enum (`SURFACE_TO_LLM`, `FAIL_RUN`). Existing `tool_exception` outcome retained.                                                                                                                                                         |

### 3.4 Files unchanged but documented

| File                                                                                                   | Note                                                                                                |
| ------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| [`agent_runtime/capabilities/retrying_tool.py`](../../src/agent_runtime/capabilities/retrying_tool.py) | Stays. Default policy SURFACE*TO_LLM applies \_after* `RetryingTool` exhausts attempts.             |
| [`runtime_api/schemas/events.py`](../../src/runtime_api/schemas/events.py)                             | Wire format unchanged. New synthesized terminal events use existing `event_type` + `status=failed`. |

---

## 4. Design

### 4.1 `LifecycleLedger` — single source of truth for "what's open"

```python
class LifecycleKind(StrEnum):
    SUBAGENT  = "subagent"
    TOOL_CALL = "tool_call"
    MODEL_CALL = "model_call"

@dataclass(frozen=True)
class OpenLifecycleEntry:
    kind: LifecycleKind
    entity_id: str
    parent_task_id: str | None
    started_at: datetime
    payload_snapshot: Mapping[str, Any]   # used to construct synthesized terminal payload

class LifecycleLedger:
    """Per-run open-lifecycle ledger. Drained at termination."""

    async def open(self, entry: OpenLifecycleEntry) -> None: ...
    async def close(self, *, kind: LifecycleKind, entity_id: str) -> OpenLifecycleEntry | None: ...
    async def open_entries(self) -> Sequence[OpenLifecycleEntry]: ...
```

Properties:

- **One ledger per run.** Owned by `RuntimeEventProducer`, lifetime equals run lifetime.
- **Idempotent close.** Closing an unknown `(kind, entity_id)` is a logged no-op — defensive against duplicate `*_COMPLETED` deliveries.
- **Open with same key replaces previous.** Logs a warning. Duplicate `*_STARTED` is a producer bug we want surfaced, not silently doubled.
- **Single async-lock.** No fine-grained sharding needed; ledger sees ~10s of operations per run.
- **Not a generic event log.** Only paired lifecycle events.

### 4.2 `RunTerminationCoordinator` — single chokepoint for ending a run

Today, "the run is ending" happens by direct `append_event(RUN_FAILED)` from many sites. There is nowhere to hang invariants. Make it a method:

```python
class TerminationReason(StrEnum):
    NORMAL_COMPLETION = "normal_completion"
    TOOL_FATAL_ERROR  = "tool_fatal_error"
    EXECUTION_ERROR   = "execution_error"
    CANCELLED         = "cancelled"
    APPROVAL_TIMEOUT  = "approval_timeout"
    BUDGET_EXCEEDED   = "budget_exceeded"

class RunTerminationCoordinator:
    async def terminate(
        self,
        *,
        run: RunRecord,
        terminal_status: AgentRunStatus,
        reason: TerminationReason,
        cause: BaseException | None = None,
    ) -> None:
        # 1. Drain the ledger — synthesize terminal events for every open entry.
        for entry in await self.ledger.open_entries():
            await self._emit_synthesized_terminal(entry, terminal_status, reason)
        # 2. Emit the run's own terminal event.
        await self.event_producer.append_api_event(
            run=run,
            event_type=self._terminal_event_for(terminal_status),
            payload=self._terminal_payload(reason, cause),
        )
```

- **Idempotent.** A second `terminate` call after the first is a no-op (ledger drained, run row already in terminal state).
- **Defensive last resort.** If `_emit_synthesized_terminal` raises, log + continue draining — never let one stuck child block the others. Guard the run-terminal emission in its own `try/except` and force a minimal `run_failed{reason="coordinator_error"}` if even that fails.
- **Migrated call sites.** Every `append_event(RUN_FAILED|RUN_CANCELLED|RUN_COMPLETED)` becomes `coordinator.terminate(...)`. The legacy direct-emission helpers become package-private and unused.

### 4.3 `ToolErrorPolicy` — single decision point for tool failures

```python
class ToolErrorOutcome(StrEnum):
    SURFACE_TO_LLM = "surface_to_llm"   # default — LLM sees the error, decides next step
    FAIL_RUN       = "fail_run"          # typed policy violation, run terminates

@dataclass(frozen=True)
class ToolErrorClassification:
    outcome: ToolErrorOutcome
    error_class: str                  # public-safe class name (sanitized)
    sanitized_message: str            # safe to send to the model
    structured_hints: Mapping[str, Any]   # actionable, machine-readable
    audit_trace: str | None           # full text — backend audit only, never to LLM

class ToolErrorPolicy(Protocol):
    def classify(self, exc: BaseException, *, tool: BaseTool) -> ToolErrorClassification: ...
```

Default policy:

```python
class DefaultToolErrorPolicy:
    def classify(self, exc, *, tool):
        if isinstance(exc, RunFatalToolError):
            return ToolErrorClassification(
                outcome=ToolErrorOutcome.FAIL_RUN,
                error_class=type(exc).__name__,
                sanitized_message=exc.safe_summary,
                structured_hints={},
                audit_trace=traceback.format_exc(),
            )
        return ToolErrorClassification(
            outcome=ToolErrorOutcome.SURFACE_TO_LLM,
            error_class=type(exc).__name__,
            sanitized_message=ErrorSanitizer.sanitize(exc),
            structured_hints=ErrorHintExtractor.extract(exc),
            audit_trace=traceback.format_exc(),
        )
```

Where the policy is invoked (single call site, in [`runtime.py`](../../src/agent_runtime/execution/runtime.py)):

```python
try:
    return await tool.ainvoke(args)
except asyncio.CancelledError:
    raise   # cancellation never goes through the policy
except BaseException as exc:
    classification = self.tool_error_policy.classify(exc, tool=tool)
    if classification.outcome is ToolErrorOutcome.FAIL_RUN:
        await self.coordinator.terminate(
            run=run,
            terminal_status=AgentRunStatus.FAILED,
            reason=TerminationReason.TOOL_FATAL_ERROR,
            cause=exc,
        )
        raise
    return self._build_failed_tool_message(classification)   # ToolMessage back to graph
```

### 4.4 `ErrorSanitizer` and `ErrorHintExtractor` — what the LLM sees

The LLM gets enough to fix its call; the prompt does not become an exfiltration channel.

`ErrorSanitizer.sanitize(exc) -> str`:

- **Strips:** repo paths (`/Users/`, `/opt/`, `/var/`, project root), long hex IDs (run_id / conversation_id / org_id patterns matched by `[0-9a-f]{16,}`), connection-string fragments (`postgresql://`, `password=`, `token=`, `Bearer ...`), full traceback frames.
- **Preserves:** the exception's short message (first line), validation context, retry-after, status codes.
- **Caps:** 2 KB output. Prevents runaway tracebacks bloating the agent context.

`ErrorHintExtractor.extract(exc) -> Mapping[str, Any]` — structured, machine-readable. Per-type extractors:

| Exception                             | Extracted hints                                               |
| ------------------------------------- | ------------------------------------------------------------- |
| `pydantic.ValidationError`            | `{"invalid_args": [...], "expected": {...}, "got": {...}}`    |
| `httpx.HTTPStatusError`               | `{"status_code": int, "retry_after_seconds": int \| None}`    |
| `httpx.ConnectError` / `TimeoutError` | `{"category": "transport", "transient": true}`                |
| `ddgs.DDGSException`                  | `{"category": "search_provider", "all_engines_failed": true}` |
| `ToolBudgetWarn`                      | `{"category": "budget_warning", "remaining_calls": int}`      |
| _unknown_                             | `{}` (LLM still gets sanitized message + class name)          |

Extractors are pure functions on the exception; adding a new extractor doesn't touch policy or sanitizer.

### 4.5 (Bonus) FE simplification — opt-in follow-up

Once §4.2 reconciliation is in production, every `(kind, entity_id)` has a most-recent terminal event. The FE's `subagentCounts.running` calculation in `apps/frontend/src/features/chat` becomes a per-entity status projection rather than `count(STARTED) - count(COMPLETED)`. This deletes the lossy event-pair counter and one class of UI drift.

Out of scope for this PR; it can ship as a follow-up after the backend invariant is bedded in.

---

## 5. Migration / rollout

Five phases, each independently revertable. Phase 1 + 2 fix §1.1; Phase 3 + 4 fix §1.2.

### Phase 1 — Plumb the ledger (no behavior change)

- Add `LifecycleLedger`. Per-run instance owned by `RuntimeEventProducer`.
- Each handler that emits `*_STARTED` calls `ledger.open(...)`; each that emits `*_COMPLETED` calls `ledger.close(...)`.
- New observability metric `lifecycle_open_at_run_terminate{kind}` — emit at run terminal events. Should be `0` on green runs.
- Tests: ledger drains to empty on every existing test path.
- **No reconciliation yet.** Goal is to validate the ledger correctly tracks what's actually open.

### Phase 2 — `RunTerminationCoordinator` + reconciliation (fixes §1.1)

- Add coordinator. Migrate every `append_event(RUN_FAILED|RUN_CANCELLED|RUN_COMPLETED)` to `coordinator.terminate(...)`.
- Coordinator drains the ledger first, then emits the run terminal event.
- §1.1 repro is fixed: the third subagent gets `SUBAGENT_COMPLETED status=failed`.
- Tests: failed/cancelled/timed-out runs with open subagents → terminal events synthesized; metric drops to `0`.

### Phase 3 — `ToolErrorPolicy` + sanitizer + hint extractor (fixes §1.2)

- Add policy + default impl + sanitizer + extractor. Wrap tool execution in [`runtime.py`](../../src/agent_runtime/execution/runtime.py) to route exceptions through the policy.
- SURFACE_TO_LLM ⇒ emit `TOOL_RESULT status=failed` with sanitized payload, build `ToolMessage`, return to graph (run continues).
- FAIL_RUN ⇒ call `coordinator.terminate(reason=TOOL_FATAL_ERROR)`.
- §1.2 repro is fixed: `web_search` exception becomes a `ToolMessage` the LLM can react to.
- Tests: stub LLM that records its inputs; assert tool-message injection happens; assert raw paths/IDs/traces don't leak.

### Phase 4 — Typed exception migration

- Audit every site today raising into "exception → run dies" semantics. Two outcomes per site:
  - Genuine policy violation ⇒ raise typed `RunFatalToolError` subclass.
  - Transient / recoverable ⇒ leave as plain `Exception`; default policy SURFACE_TO_LLM kicks in.
- Initial typed exceptions: `BudgetExceeded`, `AuthDenied`, `PolicyViolation`, `TenantIsolationViolation`.
- Add a lint or test asserting any new "fatal" tool error subclasses `RunFatalToolError`.

Audit candidates (concrete):

| Site                               | Today                                    | After                                                                                                                                    |
| ---------------------------------- | ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `tool_budget_guard.py` HARD reject | Returns safe-message string              | Raises `BudgetExceeded`; coordinator terminates run                                                                                      |
| `auth_gate.py` deny                | Caller swallows + returns failure object | Raises `AuthDenied` at the boundary                                                                                                      |
| `auth_mcp.py` OAuth required       | Surfaces as opaque tool result           | Raises `AuthDenied` _only_ when the OAuth flow is unrecoverable; transient remote-MCP errors stay plain `Exception` so the LLM can retry |
| MCP tool unknown tool name         | Surfaces as `tool_exception`             | Stays plain `Exception` — LLM sees and corrects                                                                                          |

### Phase 5 — Bonus FE simplification (separate follow-up PR)

- FE subagent counters derived from latest per-entity status instead of unmatched starts.
- Deletes `subagentCounts.running` event-pair math.
- Independently revertable; doesn't gate the backend changes.

---

## 6. Testing strategy

### Unit

- `LifecycleLedger`: open / close happy path; double-open warns and replaces; close-unknown is no-op + warns; `open_entries()` snapshot is consistent under concurrent close.
- `RunTerminationCoordinator`: drains ledger before terminal event; idempotent (`terminate` twice is safe); per-entry emission failure does not block siblings; coordinator-level emission failure falls back to minimal `run_failed{reason=coordinator_error}`.
- `ToolErrorPolicy.classify`: non-typed → SURFACE_TO_LLM; typed `RunFatalToolError` subclasses → FAIL_RUN; `CancelledError` re-raised before reaching policy.
- `ErrorSanitizer`: representative inputs (file paths, hex IDs, connection strings, multi-frame tracebacks) — none of those leak; class name + short message + validation field names + retry-after survive; cap honored.
- `ErrorHintExtractor`: per-extractor table-driven tests for each registered exception type.

### Integration

- **Repro of §1.1.** Fan-out 3 subagents; one inner tool throws; assert all three subagents have terminal events; assert `RUN_FAILED` carries cause.
- **Repro of §1.2.** Tool throws; assert `ToolMessage` injected into agent state; assert next LLM step receives it; assert run is not failed.
- **Typed `BudgetExceeded` from a stub tool** ⇒ `RUN_FAILED reason=BUDGET_EXCEEDED`; sibling subagents reconciled with terminal events.
- **Cancellation mid-flight** ⇒ ledger drained, all open subagents/tool-calls get terminal events, no `ToolMessage` injection.

### Regression

- Full existing test suite passes without skipped/xfailed regressions.
- Existing tool-error tests adapt to new sanitizer; assertions on raw exception text update to assert on sanitized output.

---

## 7. Risks and open questions

### Risks

- **Loud-to-quiet behavior shift.** A current tool that raises `ValueError` because of a logic bug today fails the run loudly; after this PR the LLM sees it and tries again quietly. Mitigation: Phase 4 audit is **mandatory**, not optional. Anything that _should_ fail the run gets a typed exception or stays SURFACE_TO_LLM with the bug surfaced via metrics.
- **LLM context bloat.** Every tool error injects tokens. Mitigation: 2 KB sanitizer cap; structured hints as compact JSON; runtime-level cap on consecutive tool errors per agent step (proposed: 5; configurable).
- **Coordinator becomes a single point of failure.** If `terminate` raises, the run row never reaches a terminal state. Mitigation: defensive double-try in §4.2; observability metric on coordinator failures.
- **Sanitizer false negatives.** Real exceptions in production may carry strings the regex set doesn't catch. Mitigation: sanitizer ships behind a feature flag for one week with both raw and sanitized output logged side-by-side; spot-check before flipping default.

### Open questions

- `structured_hints` shape in the `ToolMessage`: JSON string vs Anthropic-style typed `tool_result` block? Probably JSON string for cross-provider consistency, but worth a 30-min spike against both Anthropic and OpenAI tool-result shapes.
- Per-step max-consecutive-tool-errors safety cap value — empirical question; ship with conservative default and tune.
- Should the FE expose the redacted `audit_trace` of a failed call in an admin debug view? Useful for support; needs RBAC. Scope it as a separate decision.

---

## 8. Out of scope

- LangGraph upgrade or `ToolNode` replacement.
- New tool retry library (`RetryingTool` already shipped).
- Run state-machine rework.
- SSE wire-format changes.
- Backend audit-log RBAC for surfacing `audit_trace` to admins.
