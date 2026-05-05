# Cluster: `agent_runtime/delegation/subagents/`

**Total: 1,504 LOC across 5 files.** Subagent delegation: how the supervisor agent hands off a task to a narrowed-scope subagent, how the subagent's lifecycle (queued → running → completed / failed) is tracked in async durable state, and how its result is returned to the supervisor.

## Role in the request lifecycle

When the supervisor's tool call invokes a subagent, [`handoff.py`](../../../services/ai-backend/src/agent_runtime/delegation/subagents/handoff.py) constructs the compact task spec (objective, summary, constraints, narrowed tool/skill allowlist computed as set-intersection of supervisor capabilities ∩ subagent definition). [`runner.py`](../../../services/ai-backend/src/agent_runtime/delegation/subagents/runner.py) creates the durable task record, returns a task_id immediately, and asynchronously drives execution. [`definitions.py`](../../../services/ai-backend/src/agent_runtime/delegation/subagents/definitions.py) is the registry of available subagent profiles. [`contracts.py`](../../../services/ai-backend/src/agent_runtime/delegation/subagents/contracts.py) defines the Pydantic envelopes (definition, handoff, result, lifecycle state). [`runtime_worker/stream_subagents.py`](../runtime-worker/stream-subagents.md) projects subagent stream events back to the supervisor's run.

## Files in this cluster

| File                                                                                                                                                                                      | LOC | Doc                                                                     |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --: | ----------------------------------------------------------------------- |
| [`contracts.py`](../../../services/ai-backend/src/agent_runtime/delegation/subagents/contracts.py) — Pydantic contracts for subagent definitions, handoffs, results, and lifecycle state. | 524 | [contracts.md](contracts.md) (standalone, L)                            |
| [`runner.py`](../../../services/ai-backend/src/agent_runtime/delegation/subagents/runner.py) — Async subagent lifecycle orchestration with deterministic in-memory state.                 | 473 | [runner.md](runner.md) (standalone — promoted, lifecycle state machine) |
| [`definitions.py`](../../../services/ai-backend/src/agent_runtime/delegation/subagents/definitions.py) — Provider-backed catalog for compact subagent definitions.                        | 189 | [subagents-bundle.md](subagents-bundle.md)                              |
| [`constants.py`](../../../services/ai-backend/src/agent_runtime/delegation/subagents/constants.py) — Constants and safe messages for subagent delegation.                                 | 182 | [subagents-bundle.md](subagents-bundle.md)                              |
| [`handoff.py`](../../../services/ai-backend/src/agent_runtime/delegation/subagents/handoff.py) — Compact handoff construction for supervisor-to-subagent delegation.                      |  79 | [subagents-bundle.md](subagents-bundle.md)                              |

## Doc layout

- [contracts.md](contracts.md) — `contracts.py` (L, 524)
- [runner.md](runner.md) — `runner.py` (M, 473, promoted)
- [subagents-bundle.md](subagents-bundle.md) — `definitions.py`, `handoff.py`, `constants.py`

## Cross-cluster dependencies

**Imports from:**

- [`agent_runtime/capabilities/`](../capabilities/_index.md) — capability narrowing (skills/tools allowlist intersection)
- [`agent_runtime/persistence/records/subagents.py`](../persistence/_index.md) — task/result records
- [`agent_runtime/execution/contracts.py`](../execution/contracts.md) — runtime event types
- [`agent_runtime/context/memory/`](../context-memory/_index.md) — virtual memory file projection (consumed by `subagent_trace.py`)

**Imported by:**

- [`agent_runtime/execution/factory.py`](../execution/_index.md) — wires subagents into the runtime
- [`agent_runtime/execution/deep_agent_builder.py`](../execution/_index.md)
- [`runtime_worker/stream_subagents.py`](../runtime-worker/stream-subagents.md) — projects lifecycle events
- [`runtime_worker/handlers/run.py`](../runtime-worker/handlers-run.md) — drives subagent execution

## Use-case relevance

- [10-single-subagent-delegation.md](../../use-cases/10-single-subagent-delegation.md) — primary anchor.
- [11-multi-subagent-plus-tool.md](../../use-cases/11-multi-subagent-plus-tool.md) — concurrent delegation.
- [14-subagent-fails-output-contract.md](../../use-cases/14-subagent-fails-output-contract.md) — `runner.py` failure path + `contracts.py` validation.
