# Cluster: `runtime_worker/`

**Total: 5,601 LOC across 17 files.** The worker process. Claims queued runtime commands (run / cancel / approval), drives LangGraph execution, projects stream chunks into persisted typed events, runs the daily usage rollup loop, and emits worker-side audit records. The single most logic-dense cluster — `handlers/run.py` alone is 997 LOC.

## Role in the request lifecycle

The API layer enqueues runtime commands; this process claims them. `loop.py` is the outer claim loop with bounded concurrency. `handlers/run.py` consumes a queued run, builds the runtime via [`agent_runtime/execution/factory.py`](../execution/_index.md), and streams it through `streaming_executor.py`. As LangGraph emits stream chunks, `stream_events.py` maps them into persisted `RuntimeEventEnvelope` rows; `stream_tools.py`, `stream_subagents.py`, `stream_messages.py` are projection helpers per event family. `tool_call_ledger.py` tracks in-flight tool calls so orphans can be settled on failure. `run_metrics.py` extracts per-call usage. `handlers/approval.py` resumes a paused run after an approval decision lands. `handlers/cancel.py` terminates an in-flight run.

## Files in this cluster

### Handlers (3 files, 1,341 LOC)

| File                                                                                                                                                                  | LOC | Doc                                                       |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --: | --------------------------------------------------------- |
| [`handlers/run.py`](../../../services/ai-backend/src/runtime_worker/handlers/run.py) — Queued run command handling and execution via LangGraph.                       | 997 | [handlers-run.md](handlers-run.md) (standalone, XL)       |
| [`handlers/approval.py`](../../../services/ai-backend/src/runtime_worker/handlers/approval.py) — Queued approval-resolution command handling with runtime resumption. | 286 | [handlers-approval.md](handlers-approval.md) (standalone) |
| [`handlers/cancel.py`](../../../services/ai-backend/src/runtime_worker/handlers/cancel.py) — Apply a queued cancellation request to terminate in-flight runs.         |  58 | [loop-and-deps.md](loop-and-deps.md)                      |

### Streaming projection (8 files, 2,556 LOC)

| File                                                                                                                                                                           | LOC | Doc                                                     |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --: | ------------------------------------------------------- |
| [`stream_events.py`](../../../services/ai-backend/src/runtime_worker/stream_events.py) — Map runtime stream chunks into persisted runtime API events.                          | 624 | [stream-events.md](stream-events.md) (standalone, L)    |
| [`stream_tools.py`](../../../services/ai-backend/src/runtime_worker/stream_tools.py) — Tool call projection helpers for runtime stream events.                                 | 564 | [stream-tools.md](stream-tools.md) (standalone, L)      |
| [`stream_subagents.py`](../../../services/ai-backend/src/runtime_worker/stream_subagents.py) — Subagent lifecycle projection helpers for runtime stream events.                | 449 | [stream-subagents.md](stream-subagents.md) (standalone) |
| [`stream_messages.py`](../../../services/ai-backend/src/runtime_worker/stream_messages.py) — Message and payload helpers for runtime stream projection.                        | 299 | [streaming-bundle.md](streaming-bundle.md)              |
| [`streaming_executor.py`](../../../services/ai-backend/src/runtime_worker/streaming_executor.py) — Shared streaming loop used by both run and approval handlers.               | 254 | [streaming-bundle.md](streaming-bundle.md)              |
| [`tool_call_ledger.py`](../../../services/ai-backend/src/runtime_worker/tool_call_ledger.py) — In-flight tool call tracking for run-level reconciliation and failure recovery. |  89 | [streaming-bundle.md](streaming-bundle.md)              |
| [`stream_parts.py`](../../../services/ai-backend/src/runtime_worker/stream_parts.py) — Typed helpers for LangGraph stream part metadata and namespace parsing.                 |  61 | [streaming-bundle.md](streaming-bundle.md)              |

### Audit / observations (2 files, 599 LOC)

| File                                                                                                                                                                           | LOC | Doc                                                    |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --: | ------------------------------------------------------ |
| [`run_metrics.py`](../../../services/ai-backend/src/runtime_worker/run_metrics.py) — Extract token usage from LangChain AIMessage objects and model call responses.            | 607 | [run-metrics.md](run-metrics.md) (standalone, L)       |
| [`tool_observations.py`](../../../services/ai-backend/src/runtime_worker/tool_observations.py) — Build branch-scoped context for prior tool observations with redaction state. | 328 | [audit-and-observations.md](audit-and-observations.md) |
| [`audit.py`](../../../services/ai-backend/src/runtime_worker/audit.py) — Worker-side audit emission for privileged runtime outcomes and actions.                               | 271 | [audit-and-observations.md](audit-and-observations.md) |

### Loop / process (5 files, 802 LOC)

| File                                                                                                                                                                            | LOC | Doc                                  |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --: | ------------------------------------ |
| [`loop.py`](../../../services/ai-backend/src/runtime_worker/loop.py) — Async worker loop for durable runtime commands and run orchestration.                                    | 243 | [loop-and-deps.md](loop-and-deps.md) |
| [`usage_rollup_loop.py`](../../../services/ai-backend/src/runtime_worker/usage_rollup_loop.py) — Background loop that recomputes daily usage rollups for user and org tracking. | 207 | [loop-and-deps.md](loop-and-deps.md) |
| [`dependencies.py`](../../../services/ai-backend/src/runtime_worker/dependencies.py) — Default dependency factories for local runtime worker execution.                         | 142 | [loop-and-deps.md](loop-and-deps.md) |
| [`__main__.py`](../../../services/ai-backend/src/runtime_worker/__main__.py) — Runtime worker process entrypoint with initialization and loop startup.                          | 110 | [loop-and-deps.md](loop-and-deps.md) |

## Doc layout

- [handlers-run.md](handlers-run.md) — `handlers/run.py` (XL, 997)
- [stream-events.md](stream-events.md) — `stream_events.py` (L, 624)
- [run-metrics.md](run-metrics.md) — `run_metrics.py` (L, 607)
- [stream-tools.md](stream-tools.md) — `stream_tools.py` (L, 564)
- [stream-subagents.md](stream-subagents.md) — `stream_subagents.py` (M, 449) — promoted (state machine)
- [handlers-approval.md](handlers-approval.md) — `handlers/approval.py` (M, 286) — promoted (interrupt logic)
- [streaming-bundle.md](streaming-bundle.md) — `streaming_executor`, `stream_messages`, `stream_parts`, `tool_call_ledger`
- [audit-and-observations.md](audit-and-observations.md) — `audit.py`, `tool_observations.py`
- [loop-and-deps.md](loop-and-deps.md) — `loop.py`, `dependencies.py`, `__main__.py`, `usage_rollup_loop.py`, `handlers/cancel.py`

## Cross-cluster dependencies

**Imports from:**

- [`agent_runtime/execution/`](../execution/_index.md) — runtime factory + invocation
- [`agent_runtime/api/`](../agent-api/_index.md) — event producer + presentation
- [`agent_runtime/persistence/`](../persistence/_index.md) — outbox / records / optimistic helpers
- [`agent_runtime/capabilities/`](../capabilities/_index.md) — middleware fires during runs
- [`agent_runtime/delegation/subagents/`](../delegation-subagents/_index.md) — subagent runner is invoked here
- [`runtime_adapters/`](../runtime-adapters/_index.md) — store ports
- LangGraph + Deep Agents SDK
- LangChain core (AIMessage, tool message types)

**Imported by:** nothing else inside `services/ai-backend/src/` — this is the leaf process.

## Use-case relevance

Almost every use-case touches this cluster. Primary anchors:

- [01-cold-start-first-message.md](../../use-cases/01-cold-start-first-message.md) — `handlers/run.py` + `streaming_executor`.
- [04-ask-a-question-single.md](../../use-cases/04-ask-a-question-single.md), [05-…consecutive](../../use-cases/05-ask-a-question-consecutive.md) — `stream_events.py` interrupt projection + `handlers/approval.py`.
- [08-user-cancels-mid-stream.md](../../use-cases/08-user-cancels-mid-stream.md) — `handlers/cancel.py` + `tool_call_ledger.py` orphan settlement.
- [10-single-subagent-delegation.md](../../use-cases/10-single-subagent-delegation.md), [11-multi-subagent-plus-tool.md](../../use-cases/11-multi-subagent-plus-tool.md) — `stream_subagents.py`.
