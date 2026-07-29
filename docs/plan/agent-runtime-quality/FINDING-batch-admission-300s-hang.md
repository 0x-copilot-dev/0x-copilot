# FINDING — batch admission hangs 300s under load (main is red)

**Status:** open, reproducible, not fixed. Owned by the F6 batch-concurrency work.
**Found:** 2026-07-29, diagnosing the red `ci-ai-backend` on `main`.

## What is red

`ci-ai-backend` on `main` at the #426 merge: **17 failed, 7594 passed in 3634s
(1:00:34)**. The normal runtime of that job is ~380s.

Eleven of the seventeen took **exactly 300.0x seconds each** — that is
`BatchCoordinatorBounds.DEFAULT_ADMISSION_WAIT_SECONDS`, and it accounts for
~3300s of the hour. The job is not slow; it is eleven separate 5-minute hangs.

The remaining six are assertion failures in the same files
(`assert 2 == 3`, `assert [1, 0, 2] == [0, 1, 2]`, "a sibling's completed work
was lost when its neighbour failed").

## Reproduction

Deterministic enough to iterate on — roughly 50% of runs, ~3 of 6:

```bash
cd services/ai-backend
for i in 1 2 3 4 5 6; do
  (PYTHONPATH="src:../../packages/service-contracts/src" .venv/bin/python -m pytest \
     tests/unit/agent_runtime/capabilities/concurrency/test_step10_gate.py \
     tests/unit/agent_runtime/capabilities/middleware/test_runtime_tool_control_batch.py \
     -q 2>&1 | tail -1) &
done; wait
```

Observed: three runs print `27 passed in 0.74s`, three print
`1 failed, 26 passed in 300.7s`.

Two conditions are both required:

- **CPU contention.** A single run passes in 0.74s, every time.
- **Both files together.** `test_step10_gate.py` alone passes 5-way concurrent.
  It imports its helpers _from_ `test_runtime_tool_control_batch.py`, so the two
  share `_admission`, `_binding`, `_declarations`, `_FanoutModel`.

The full suite passes locally **unloaded**: 7611 passed in 126s. CI's runner is
small and the job runs the whole suite, which is why CI hits it and a laptop
does not.

## The failure

```
src/agent_runtime/capabilities/concurrency/graph_admission.py:604
BatchChildExecutionError: The batch child was not admitted and did not run.
```

A child waits the full admission budget and is then refused. Both gates in
`batch_coordinator.py` use `_wait_seconds(batch)` → 300s: the segment gate
(`~line 969`) and the permit request (`~line 1163`).

## Ruled out

Each of these was read and is correct, so they are not worth re-checking:

- **`_settle` always pumps** — every settle path ends in `self._pump(batch)`
  (`:1220`).
- **`_stop` refuses waiters** — it calls `_refuse_waiters` (`:1260`) before
  settling pending children, so a failure-policy stop does not strand a parked
  child. `_pump`'s `if batch.stopped: return` guard is therefore safe.
- **No permit leak** — `_run_admitted` uses `async with self._permits.acquire(...)`,
  the guarded path that releases on success, refusal, exception, and cancellation.
- **No shared singleton across tests** — `_admission()` constructs a fresh
  `BatchExecutionCoordinator` and `RunPermitManager` per call.
- **Not `pytest-randomly`** — CI's plugin list is `anyio, asyncio, langsmith`;
  ordering is deterministic file order.

## Where to look next

The two remaining candidates, in likelihood order:

1. **A lost wakeup between the segment gate and the permit gate.** A child that
   clears the segment cursor and then parks on a permit is holding a segment
   slot (`state.holds_slot`) while queued in a different structure. Whether
   every path that could free capacity pumps _both_ is the thing to instrument.
2. **The 32-yield quiescence windows.** `_QUIESCENCE_YIELDS` (test*step10_gate)
   and `_ConcurrencyProbe.YIELDS` (test_runtime_tool_control_batch) are both 32,
   each documented as "comfortably more turns than the admission path spends".
   Both docstrings argue widening is always safe and narrowing only under-reports
   — which is sound — but under contention the admission path can plausibly
   spend more than 32 turns, and that is exactly what the six \_assertion*
   failures look like. This does not explain the 300s hangs on its own.

Note `assert [1, 0, 2] == [0, 1, 2]` is a separate, smaller problem regardless
of the above: it asserts _arrival order within a parallel wave_, which the
system does not guarantee and never claimed to. That assertion should compare
membership, not order.

## Why this is filed rather than fixed

Diagnosed while landing unrelated surface work (#425, #427 — both green on
every job they triggered, including `ci-ai-backend` on #425). The reproduction
above is the hard part and is done; the fix needs instrumentation inside the
coordinator by someone who owns its concurrency model, and each iteration costs
300s. Guessing at those semantics would risk trading an intermittent hang for a
silent admission bug, which is strictly worse.
