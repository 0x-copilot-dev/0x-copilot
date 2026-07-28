# PRD-01 — Emit-side vocabulary parity, and consumer reachability

**Goal:** make `LedgerEventType ⊆ RuntimeApiEventType` a guarded invariant, so an
event a producer can emit is always an event the transport can carry and a
consumer can receive. Close the two boundaries PR #413 left unguarded.

**Closes:** GS-ARCH-07 (transport half), part of GS-ARCH-04.
**Depends on:** PR #413 (merged, `b4a2463e`).
**Scope:** `services/ai-backend`, `packages/api-types`. No facade, no apps.

## The defect

Every ledger emitter converts a ledger event-type _value_ to the wire enum:

```python
event_type=RuntimeApiEventType(str(event_type_value))   # _build_work_ledger_emitter
event_type=RuntimeApiEventType(event_type_value)        # _build_operation_ledger_emitter
```

`RuntimeApiEventType` does not contain every `LedgerEventType`. Verified:

| Value                           | `RuntimeApiEventType(...)` |
| ------------------------------- | -------------------------- |
| `artifact.presentation_decided` | OK                         |
| `gate.opened.v2`                | **raises `ValueError`**    |
| `gate.resolved.v2`              | **raises `ValueError`**    |

`agent_runtime/capabilities/workspace/effects.py::_blocked` emits
`LedgerEventType.GATE_OPENED_V2` with **no `try/except`**, so a workspace
operation that needs a grant raises out of its gate instead of opening one.
Dormant today only because the desktop runs `RUNTIME_ENABLE_DESKTOP_WORKSPACE=false`.

`_build_work_ledger_emitter`'s docstring states the opposite of the truth:

> "maps a ledger event-type _value_ … to the wire enum by value — both enums
> carry identical values"

## Decision required before implementation

The guard lands either way, but the two-value reconciliation is a product call:

| Option                                                      | Consequence                                                                                                                                                                                                                                  |
| ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A — add the pair to `RuntimeApiEventType`** (recommended) | Workspace gates reach the client; `projectCanvasLifecycle`'s `parked` branch becomes live; `test_api_type_contracts` then also requires them in the TS tuple, which is correct. Needs a payload/presentation review for two new wire events. |
| **B — remove the v2 emit**                                  | Workspace blocking stops emitting a gate at all. Cheaper, but GS-ARCH-07 and GS-ARCH-04 stay open, and `_blocked` still needs a non-raising path.                                                                                            |

Recommendation: **A**. The emit site is meaningful — it fires when a workspace
operation needs a grant — and a gate nobody can see is not a gate.

## Contract

**Invariant (new, named):** every `LedgerEventType` value is a valid
`RuntimeApiEventType` value. Equivalently: the ledger vocabulary is a subset of
the runtime transport vocabulary at _every_ boundary it crosses.

No new Pydantic contracts. `RuntimeApiEventType` gains two members under Option A:

```python
GATE_OPENED_V2 = LedgerEventType.GATE_OPENED_V2.value      # "gate.opened.v2"
GATE_RESOLVED_V2 = LedgerEventType.GATE_RESOLVED_V2.value  # "gate.resolved.v2"
```

Mirrored in `packages/api-types` by adding `...GATE_V2_EVENT_TYPES` to
`RUNTIME_LEDGER_V21_EVENT_TYPES` — referencing the named family, never
re-typing the literals (`test_event_literal_gate_v2_1` forbids inline
duplicates; that constraint is why the original code reached for indices).

## Implementation

| Area                                                         | Change                                                                                                                                                                                                                                                                    |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `runtime_api/schemas/common.py`                              | Option A: add the two members, sourced from `LedgerEventType` (matching the existing `GATE_OPENED = LedgerEventType.GATE_OPENED.value` style)                                                                                                                             |
| `packages/api-types/src/index.ts`                            | Add `...WORK_LEDGER_GATE_V2_EVENT_TYPES` to `RUNTIME_LEDGER_V21_EVENT_TYPES`; import the family alias alongside the existing three                                                                                                                                        |
| `runtime_worker/handlers/run.py::_build_work_ledger_emitter` | Correct the docstring. It currently asserts an equality that does not hold and would mislead the next author                                                                                                                                                              |
| `agent_runtime/capabilities/workspace/effects.py::_blocked`  | The gate emit must not be able to fail the operation it is gating. Wrap in the same best-effort shape `WorkLedgerEmitter.on_tool_result` already uses (log + swallow), so a vocabulary regression degrades to a missing gate event rather than a raised workspace failure |
| `agent_runtime/surfaces_v2/ledger_models.py`                 | No change — `LedgerEventType` stays the source of truth                                                                                                                                                                                                                   |

### Guard 1 — emit-side parity (the missing boundary)

New test, `tests/unit/runtime_api/test_ledger_emit_parity.py`:

```python
def test_every_ledger_event_type_is_wire_convertible() -> None:
    """Every value an emitter can pass to RuntimeApiEventType must convert.

    Emitters convert LedgerEventType values to the wire enum by value. A value
    present in one and absent from the other raises deep inside an emitter — or,
    on the client side, is dropped silently. This is the boundary PR #413 left
    unguarded.
    """
    unconvertible = [
        event_type.value
        for event_type in LedgerEventType
        if event_type.value not in {e.value for e in RuntimeApiEventType}
    ]
    assert unconvertible == []
```

### Guard 2 — consumer reachability (client side)

`projectCanvasLifecycle` branches on event types the transport may not carry, so
the branch compiles, tests pass with hand-built fixtures, and it is dead in
production. Extend `packages/api-types/src/ledgerTransportParity.test.ts`:

- assert every family the fold consumes (`OPERATION`, `ARTIFACT`, `EFFECT`, and
  under Option A `GATE_V2`) is transportable;
- **delete** the current `documents that the v2 gate pair is NOT transportable`
  case — it pins today's gap and must invert with the fix.

### Ordering

`test_api_type_contracts` asserts the TS tuple **equals** the backend enum, so
the Python and TypeScript edits must land in the same commit. Splitting them
reds `test-and-audit` on the intermediate commit.

## Edge cases

| Case                                                          | Behavior                                                                                                       |
| ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Replay of a run persisted before the v2 gates were wire-legal | Unaffected. Historical runs never contain these values, because the emit raised before persisting              |
| `SURFACES_V2=false`                                           | `_build_work_ledger_emitter` returns `None`; no emission path exists. Byte-identical to today                  |
| Ledger event added in future without a wire member            | Guard 1 fails at authoring time — the whole point                                                              |
| Wire event that is not a ledger event (`model_delta`, …)      | Unconstrained. The invariant is a subset relation, not equality — `RuntimeApiEventType` is legitimately larger |
| Gate emitted for a sealed run                                 | Rejected by `LedgerSealViolation` (PR #413). Gates are causal and must precede the seal                        |

## Security

- No new authority. Both values are already-safe, body-free payloads validated by
  `WorkLedgerVocabulary`.
- `gate.opened.v2` payload carries `gate_id`, `operation_id`, `gate_kind`,
  `capability`, `reason` — no path, no target bytes, no grant token. Confirm the
  presentation projector does not widen this when the type becomes wire-legal.
- Making `_blocked` best-effort must not make it _permissive_: the
  `GateResolution(allowed=False, …)` return is the authority; the emit is
  evidence. Losing evidence must never imply a grant.

## Observability

- `_blocked`'s swallow path logs at `warning` with `gate_id` + `operation_id`,
  matching `Messages.EMIT_RAISED`.
- No new metrics. Guard 1 is a build-time check; a runtime counter would imply
  the violation is expected.

## Tests

1. **Guard 1** — subset invariant; mutation-check by removing a member locally.
2. **Guard 2** — every consumed family transportable; the inverted v2-gate case.
3. `test_api_type_contracts` — still passes with the enum grown on both sides.
4. `test_event_literal_gate_v2_1` — still passes; the TS edit spreads a family.
5. Workspace unit — `_blocked` returns `GateResolution(allowed=False)` **even when
   the emitter raises** (inject a raising emitter). Asserts evidence loss never
   becomes a grant.
6. Real-topology — a run whose workspace op is blocked emits `gate.opened.v2`
   inside the sealed prefix and is received by an SSE client. Follow the
   `test_ledger_seal_invariant.py` pattern; a fake-emitter test would pass either way.

## Acceptance criteria

1. `LedgerEventType ⊆ RuntimeApiEventType` holds and is guarded.
2. Under Option A, an SSE client receives `gate.opened.v2` for a blocked
   workspace operation, and `projectCanvasLifecycle` reports `parked`.
3. A raising emitter cannot turn a denied gate into an allowed one.
4. `_build_work_ledger_emitter`'s docstring is true.
5. `ledgerTransportParity` no longer pins the v2 pair as unreachable.
6. ai-backend, api-types, chat-surface suites green; `lint-and-secrets` green
   (format with the pinned `prettier@3.8.3`).
