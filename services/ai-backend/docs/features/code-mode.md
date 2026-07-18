# Code mode (AC6 — Monty embedded interpreter)

Status: experimental, **gated OFF by default**, desktop-only. Implements the
Monty/interpreter portion of
[`docs/plan/desktop/agent-capabilities/06-ac6-monty-code-mode.md`](../../../../docs/plan/desktop/agent-capabilities/06-ac6-monty-code-mode.md).

Code mode gives the model a cheap programmable loop — calculations,
transformations, branching, and repeated calls to already-approved tools —
without spending a model turn per intermediate value. It runs a supported subset
of Python in [Pydantic Monty](https://pydantic-monty.mintlify.app/), an
independent Rust interpreter with no CPython `exec()`, behind a product-owned
port.

## What is built

All modules live under `src/agent_runtime/capabilities/interpreter/`:

| Module              | Responsibility                                                                                                                                                                                           |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| `contracts.py`      | Typed contracts + `InterpreterLimitProfiles` (`desktop_v1`) with hard-ceiling clamping.                                                                                                                  |
| `ports.py`          | `InterpreterPort`, `PolicyToolInvoker` (the shared seam), `InterpreterSnapshotStore`, `InterpreterEventSink`.                                                                                            |
| `monty_adapter.py`  | **Only** importer of `pydantic_monty` (lazy). Drives Monty's iterative `start`/`resume`, maps limits, captures stdout, converts snapshots, classifies errors.                                            |
| `snapshot_store.py` | Bounded, content-addressed snapshot persistence over the AC4 object store, with envelope binding.                                                                                                        |
| `service.py`        | Drive loop: resolve aliases, stamp limits, route every external call through `PolicyToolInvoker`, enforce the external-call and total-time ceilings, emit `interpreter.*` events, offload large results. |
| `code_mode_tool.py` | Model-facing `run_code_mode` `StructuredTool`.                                                                                                                                                           |
| `registration.py`   | `build_monty_interpreter(config) -> InterpreterPort                                                                                                                                                      | None`seam +`build_code_mode_tool(...)`. |

`services/ai-backend/execution/factory.py` is intentionally **not** edited yet.
It wires the seam later:

```python
port = build_monty_interpreter(config, snapshot_store=snapshot_store)
if port is not None:
    tool = build_code_mode_tool(port=port, policy_invoker=..., resolver=..., ...)
    # add `tool` to the model-visible tool list
```

## The external-call bridge (the key control)

The interpreter never calls a tool itself. When interpreted code calls a
declared external function, Monty **yields** a `FunctionSnapshot` (its iterative
`start`/`resume` API yields at _every_ external call — this is the per-call
interrupt the PRD flags QuickJS PTC as lacking). The service then:

1. persists the RAM-only snapshot to the object store (bounded, digest-bound);
2. emits `interpreter.external_call_requested` + `interpreter.suspended_for_approval`;
3. calls the **one** `PolicyToolInvoker.invoke(...)` — the same seam a direct
   tool call uses for permission, four-mode approval, budget, citation, and
   audit;
4. resumes the exact snapshot with the outcome:
   - `allowed` → the return value flows into interpreted code;
   - `rejected` / `denied` / `error` → a typed exception is surfaced into
     interpreted code (the tool did **not** run), so the program can branch.

A programmatic tool call therefore carries identical approval/budget/audit
semantics to a direct call — it cannot bypass them.

> The direct-path four-mode (`auto`/`ask`/`require`/`block`) policy engine is a
> separate, unfunded prerequisite (see the PRD "Undeclared dependency"). Until
> it lands, `PolicyToolInvoker` is exercised by fakes and code mode ships
> pure-compute-only; the seam shape is fixed so the real engine drops in without
> touching the bridge.

## Isolation and limits

No filesystem, network, environment, subprocess, clock, or randomness. `open`,
`eval`, `exec`, `__import__` and OS functions fail closed as
`unsupported_language_feature` (Monty surfaces them as "external" calls in
iterative mode; the adapter denies OS functions and a host-builtin denylist).

Limits (`desktop_v1`, clamped to hard ceilings, never raisable by model input):
source bytes, per-segment and total wall time, heap, allocations, recursion
depth, external-call count, snapshot bytes, result bytes, stdout/stderr. Monty
enforces duration/memory/allocation/recursion pre-emptively; the service adds
the external-call and total-time ceilings.

## Snapshots

RAM-only state is serialized only at a Monty suspension point via Monty's own
serializer (never pickle/marshal). Bytes go to the content-addressed object
store; a `SnapshotRef` carries adapter / ABI / source hash / limit-profile hash
/ invocation index. Resume prefers the live RAM session; after a worker restart
it reloads from bytes only after an envelope-compatibility check — an
incompatible snapshot fails closed (`snapshot_incompatible`), never blind-loaded.

## Configuration (all server-side)

```
RUNTIME_ENABLE_MONTY=false            # master gate (this build's flag)
ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop
RUNTIME_INTERPRETER_PROVIDER=monty
RUNTIME_MONTY_LIMIT_PROFILE=desktop_v1
```

`build_monty_interpreter` returns `None` — and `run_code_mode` is absent from
the model-visible tool list — unless every gate passes **and** `pydantic_monty`
is importable. A renderer flag can never enable it. With the seam returning
`None`, runtime behavior is byte-for-byte unchanged.

## Stable errors

`interpreter_unavailable`, `invalid_source`, `unsupported_language_feature`,
`external_function_unknown`, `external_function_denied`, `approval_expired`,
`resource_limit_exceeded` (with a `limit_kind`), `snapshot_invalid`,
`snapshot_incompatible`, `cancelled`, `interpreter_crash`, `result_invalid`.
Safe messages never contain source fragments, callback arguments, tool output,
host paths, or an adapter traceback.

## Events

`interpreter.started`, `interpreter.external_call_requested`,
`interpreter.suspended_for_approval`, `interpreter.resumed`,
`interpreter.limit_exceeded`, `interpreter.cancelled`, `interpreter.completed`,
`interpreter.failed`. Payloads carry only ids, counters, hashes, and byte
counts — never source, inputs, callback arguments, or tool output.

## Backout

Set `RUNTIME_ENABLE_MONTY=false`. The tool disappears from new runs; stored
chats remain readable (events and payload refs are product contracts, not Monty
objects). `CodeSandboxPort` (first-party code routines) is a separate capability
and is untouched.

## Monty library note

`start`/`resume` are synchronous and CPU-bound, so the adapter runs every
segment via `asyncio.to_thread` to keep the worker event loop responsive. An API
spike against `pydantic-monty==0.0.18` confirmed pure compute, per-call external
suspension, snapshot round-trip + cold recovery, duration/memory/allocation/
recursion limits, and host isolation all work as required.
