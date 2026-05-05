# Cluster: `agent_runtime/execution/`

**Total: 2,018 LOC across 7 files.** Runtime harness and contracts. Defines the typed `RuntimeEventEnvelope` and related Pydantic models, the model selection / provider validation logic, and the factory that builds a Deep Agents instance per run with the right tools, skills, MCP servers, prompt, model, and reasoning config.

## Role in the request lifecycle

`execution/factory.py` is called by [`runtime_worker/handlers/run.py`](../runtime-worker/handlers-run.md) once per claimed run. It composes the runtime via `deep_agent_builder.py` (concrete Deep Agents wiring) → `graph.py` (LangGraph export). `runtime.py` provides request-level invocation helpers used by both the worker and the in-process API mode. `contracts.py` is the source of truth for the typed event/state envelopes the worker emits and the persistence layer stores. `tool_outcomes.py` defines terminal status enums for tool calls, used by `runtime_worker/tool_call_ledger.py` to settle in-flight calls.

## Files in this cluster

| File                                                                                                                                                                   | LOC | Doc                                          |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --: | -------------------------------------------- |
| [`contracts.py`](../../../services/ai-backend/src/agent_runtime/execution/contracts.py) — Pydantic contracts for the runtime foundation.                               | 534 | [contracts.md](contracts.md) (standalone, L) |
| [`factory.py`](../../../services/ai-backend/src/agent_runtime/execution/factory.py) — Runtime factory for the Deep Agents harness with graph configuration.            | 423 | [execution-bundle.md](execution-bundle.md)   |
| [`runtime.py`](../../../services/ai-backend/src/agent_runtime/execution/runtime.py) — Request-level invocation helpers for runtime harnesses.                          | 345 | [execution-bundle.md](execution-bundle.md)   |
| [`deep_agent_builder.py`](../../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py) — Concrete Deep Agents construction for the runtime factory. | 240 | [execution-bundle.md](execution-bundle.md)   |
| [`graph.py`](../../../services/ai-backend/src/agent_runtime/execution/graph.py) — LangGraph export surface for local development and testing.                          | 147 | [execution-bundle.md](execution-bundle.md)   |
| [`models.py`](../../../services/ai-backend/src/agent_runtime/execution/models.py) — Model selection and provider validation with reasoning configuration.              | 110 | [execution-bundle.md](execution-bundle.md)   |
| [`tool_outcomes.py`](../../../services/ai-backend/src/agent_runtime/execution/tool_outcomes.py) — Typed terminal outcomes for tool calls with status enumerations.     |  59 | [execution-bundle.md](execution-bundle.md)   |

## Doc layout

- [contracts.md](contracts.md) — `contracts.py` (L, 534)
- [execution-bundle.md](execution-bundle.md) — `factory.py`, `runtime.py`, `deep_agent_builder.py`, `graph.py`, `models.py`, `tool_outcomes.py`

## Cross-cluster dependencies

**Imports from:**

- [`agent_runtime/capabilities/`](../capabilities/_index.md) — tool/skill/MCP loaders
- [`agent_runtime/context/memory/`](../context-memory/_index.md) — token budget + summarization wiring
- [`agent_runtime/delegation/subagents/`](../delegation-subagents/_index.md) — subagent definitions
- [`agent_runtime/persistence/`](../persistence/_index.md) — record types referenced by contracts
- LangGraph, Deep Agents SDK, LangChain Core
- Provider SDKs (anthropic, openai)

**Imported by:**

- [`runtime_worker/handlers/run.py`](../runtime-worker/handlers-run.md) — primary consumer
- [`runtime_worker/streaming_executor.py`](../runtime-worker/streaming-bundle.md)
- [`agent_runtime/api/events.py`](../agent-api/events.md) — uses RuntimeEventEnvelope
- [`runtime_adapters/`](../runtime-adapters/_index.md) — uses contracts as serialized record shape

## Use-case relevance

- [01-cold-start-first-message.md](../../use-cases/01-cold-start-first-message.md) — `factory.py` is invoked here for the first time.
- [10-single-subagent-delegation.md](../../use-cases/10-single-subagent-delegation.md), [11-multi-subagent-plus-tool.md](../../use-cases/11-multi-subagent-plus-tool.md) — subagent wiring in `deep_agent_builder.py`.
- [13-memory-compression-token-budget.md](../../use-cases/13-memory-compression-token-budget.md) — context-memory wiring.
