# FINDING — batch admission hangs 300s under load (fixed)

**Status:** root-caused and fixed. Owned by the F6 batch-concurrency work.
**Found:** 2026-07-29, diagnosing the red `ci-ai-backend` on `main`.
**Fixed:** 2026-07-30.

## What was red

`ci-ai-backend` on `main` at the #426 merge: **17 failed, 7594 passed in 3634s
(1:00:34)**. The normal runtime of that job is ~380s.

Eleven of the seventeen took **exactly 300.0x seconds each** — that is
`BatchCoordinatorBounds.DEFAULT_ADMISSION_WAIT_SECONDS`, and it accounts for
~3300s of the hour. The job was not slow; it was eleven separate 5-minute hangs.

## Root cause — two gates with contradictory ordering requirements

A planned batch child crosses two gates, in this order
(`runtime_tool_control.py:498` → `:544`):

1. the run's Step-2 tool admission (`RunSerialAdmission.async_permit`), then
2. F6's coordinator (`BatchExecutionCoordinator.run_child` → the segment gate).

Gate 1 serializes in **arrival** order. Gate 2 admits only the **plan's** current
segment and parks everything else. Nothing made those two orders agree:

- Gate 1's exclusive lane is one `asyncio.Lock`, whose queue is the order
  coroutines happen to reach it. The framework starts a turn's tool coroutines
  together, so that order is the scheduler's choice — it is _not_ input order,
  and under CPU contention observably is not. Measured directly, in an
  unconfigured run: `waves == [(1,), (2,), (0,)]`.
- `RunBatchAdmission.grant_for` returned `None` for any segment of width 1, so
  every child of a **serial** plan took that exclusive lane.

So a child of segment 1 that reached gate 1 first parked on gate 2 **while
holding the one lock segment 0's child needed to arrive at all**. Segment 0 could
never settle, the cursor could never advance, and neither coroutine moved until
the 300s admission budget expired — at which point the parked child was refused
`REFUSED_DEADLINE` and surfaced as
`BatchChildExecutionError: NOT_ADMITTED` (`graph_admission.py:604`).

The general statement: **a barrier over N children cannot be satisfied behind a
gate that admits fewer than N of them.**

### Why it presented as intermittent

It is not intermittent in the deadlock; it is intermittent in the arrival order.
Plan-order arrival works, every other order stalls. CPU contention only changes
how often the scheduler picks a different order — which is why the whole suite on
a small CI runner hit it and an unloaded laptop did not.

It also self-clears after each stall: the timed-out child releases the lock, the
next one runs, and the batch completes minus the refused children. That is why
one test could burn 300s (one stall) and another 600s (two).

### Deterministic reproduction

Arrival order was the only variable. Driving three planned children through the
real `awrap_tool_call` nesting, one scheduler turn apart, with the admission
budget shortened so a stall shows up immediately:

| plan                        | arrival order | before the fix                      | after   |
| --------------------------- | ------------- | ----------------------------------- | ------- |
| 3 serial segments           | `0,1,2`       | all ran                             | all ran |
| 3 serial segments           | `1,0,2`       | child 1 `NOT_ADMITTED`              | all ran |
| 3 serial segments           | `2,1,0`       | children 1 **and** 2 `NOT_ADMITTED` | all ran |
| 1 parallel segment, width 3 | any           | all ran                             | all ran |

This is now a test —
`test_step10_gate.py::TestAPlannedChildIsAdmittedWhateverOrderItArrivesIn` —
parametrized over four arrival orders, driving the real middleware so it cannot
pass while the composition it protects has drifted. Reverting the one-line
`grant_for` guard reds all three out-of-order cases in ~15s.

The original load-based reproduction (6 concurrent pytest runs of
`test_step10_gate.py` + `test_runtime_tool_control_batch.py`, ~50% hit rate) still
works and was used to confirm the fix under contention:

- **72 runs** of that exact command, 12 rounds of 6: all green, slowest 0.83s.
- **192 runs** of the whole concurrency + admission surface at 24-way on 12 cores
  (2× oversubscribed): all green, slowest 11.4s — CPU starvation, nowhere near
  the 300s budget. The first 144-run pass at that width is what surfaced the
  `test_approval_serialisation.py` order assertions below.
- Full `ai-backend` suite: 7773 passed, 127 skipped, in ~122s.

## The fix

**Every planned child is granted, and a granted call never takes the exclusive
lane.** Three changes:

- `graph_admission.py` — `grant_for` no longer bails on `width < 2`. A width-one
  grant buys no overlap; it buys _what the child waits behind_ — its own cohort
  rather than every other tool call in the run. Only work no durable plan
  accounts for is answered `None`.
- `parallel_admission.py` — the resolver stops discarding width-one grants. Every
  other fail-closed rule is untouched: no port, a raising port, a foreign type,
  and a grant for a different call are all still serial.
- `context.py` — a granted call whose cohort the bounded table cannot represent
  is answered by `_unrepresentable_cohort_lane` rather than by the exclusive lock
  it used to fall back to.

Width is not widened anywhere:

- A serial segment holds exactly one operation (`planner.py:54`), so its cohort
  semaphore of one is exactly as narrow as the lock it replaces.
- The lightswitch still takes the run's exclusive lock on the whole group's
  behalf, so granted work still cannot overlap ungranted work — and holds it for
  longer than the old lane did, not shorter.
- The coordinator's segment gate and the run permit table still decide how many
  bodies run, both strictly narrower than the grant's width.

A serial plan now also runs its children in **plan order whatever order they
arrive in**, which is the property whose absence was the bug, and is asserted
directly.

### The one contract addition

`ParallelAdmissionGrant.width_enforced_by_grantor: bool = False`. The gate's
cohort table is finite (`MAX_TRACKED_COHORTS = 32`) while a turn may plan up to
100 operations, so a grant the table cannot hold has to be answered somehow. The
old answer — the exclusive lock — is fail-closed on width and _open on liveness_:
it reintroduces exactly this stall for a grantor whose members wait on each
other. The field lets a grantor state that it applies the width itself; F6 sets
it because its coordinator does, and the default keeps every other grantor's
behaviour byte-identical.

## Separately: assertions that asserted more than the system guarantees

Four assertions across three files pinned an **arrival sequence** that nothing
produces. All of them are the same mistake and all are now membership or shape
assertions:

| file                                | was                                 | now                                       |
| ----------------------------------- | ----------------------------------- | ----------------------------------------- |
| `test_step10_gate.py`               | `clock.waves == [(0, 1, 2)]`        | one wave whose members are `{0,1,2}`      |
| `test_step10_gate.py`               | `clock.waves == [(0,), (1,), (2,)]` | three waves of one, covering `{0,1,2}`    |
| `test_approval_serialisation.py` ×2 | `probe.arrived == list(range(3))`   | `sorted(probe.arrived) == list(range(3))` |

Where the run is unconfigured or handed back to the pre-F6 path, the only gate is
the Step-2 exclusive lock, whose queue is arrival order _at the lock_ — the
framework's scheduling of coroutines it started together. Where the run is a
planned parallel segment, the members are unordered by construction. In neither
case is input order promised, and under 2× CPU oversubscription it is observably
not what happens.

These were real test bugs independent of the hang, and they are the shape of the
six non-hang failures in the red job — the reported
`assert [1, 0, 2] == [0, 1, 2]` is `TestPendingInterruptCountIsAFrameworkProperty`
in `test_approval_serialisation.py`.

The one place an order _is_ now asserted is the new regression test, where the
coordinator's segment barrier genuinely guarantees plan order.

## Ruled out during diagnosis

Each of these was read and is correct — recorded so nobody re-checks them:

- **`_settle` always pumps** — every settle path ends in `self._pump(batch)`
  (`batch_coordinator.py:1220`).
- **`_stop` refuses waiters** — `_refuse_waiters` (`:1260`) runs before pending
  children are settled, so a failure-policy stop strands nothing.
- **No permit leak** — `_run_admitted` uses `async with self._permits.acquire(...)`.
- **No shared singleton across tests** — `_admission()` builds a fresh
  coordinator and permit manager per call.
- **Not `pytest-randomly`** — CI's plugin list is `anyio, asyncio, langsmith`.
- **Not the 32-yield quiescence windows.** `_QUIESCENCE_YIELDS` and
  `_ConcurrencyProbe.YIELDS` count scheduler _turns_, not elapsed time, and the
  admission path's await count does not grow under load. They were the second
  hypothesis and they were not implicated.
