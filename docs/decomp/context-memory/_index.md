# Cluster: `agent_runtime/context/memory/`

**Total: 1,845 LOC across 7 files.** The agent's scoped-memory layer and context-compression machinery. Defines memory access policy (read/write per role + path scope), token-budget thresholds, the deterministic compression fallback that runs when the SDK's summarization fails, and the supervisor-visible projection of subagent traces into virtual memory files.

## Role in the request lifecycle

When the Deep Agents SDK is about to call the model, [`token_budget.py`](../../../services/ai-backend/src/agent_runtime/context/memory/token_budget.py) computes whether the conversation needs compression. If it does, [`summarization.py`](../../../services/ai-backend/src/agent_runtime/context/memory/summarization.py) runs an SDK summarization with a deterministic fallback for failure modes. [`policy.py`](../../../services/ai-backend/src/agent_runtime/context/memory/policy.py) gates every memory read/write at runtime by role (user / assistant / application) + path scope. [`backends.py`](../../../services/ai-backend/src/agent_runtime/context/memory/backends.py) routes scoped reads to the right durable store. [`subagent_trace.py`](../../../services/ai-backend/src/agent_runtime/context/memory/subagent_trace.py) projects subagent stream events into virtual memory files the supervisor can read mid-run.

## Files in this cluster

| File                                                                                                                                                                                | LOC | Doc                                                    |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --: | ------------------------------------------------------ |
| [`subagent_trace.py`](../../../services/ai-backend/src/agent_runtime/context/memory/subagent_trace.py) — Project runtime events into virtual subagent files for supervisor access.  | 571 | [subagent-trace.md](subagent-trace.md) (standalone, L) |
| [`contracts.py`](../../../services/ai-backend/src/agent_runtime/context/memory/contracts.py) — Pydantic contracts for scoped memory and context compression.                        | 410 | [memory-bundle.md](memory-bundle.md)                   |
| [`summarization.py`](../../../services/ai-backend/src/agent_runtime/context/memory/summarization.py) — Summarization and offloading helpers around Deep Agents context compression. | 208 | [memory-bundle.md](memory-bundle.md)                   |
| [`backends.py`](../../../services/ai-backend/src/agent_runtime/context/memory/backends.py) — Scoped memory route planning and deterministic test stores.                            | 193 | [memory-bundle.md](memory-bundle.md)                   |
| [`policy.py`](../../../services/ai-backend/src/agent_runtime/context/memory/policy.py) — Read/write policy checks for scoped memory paths.                                          | 192 | [memory-bundle.md](memory-bundle.md)                   |
| [`constants.py`](../../../services/ai-backend/src/agent_runtime/context/memory/constants.py) — Constants, limits, and messages for context and memory management.                   | 151 | [memory-bundle.md](memory-bundle.md)                   |
| [`token_budget.py`](../../../services/ai-backend/src/agent_runtime/context/memory/token_budget.py) — Token budget metrics and threshold decisions for context compression.          |  60 | [memory-bundle.md](memory-bundle.md)                   |

## Doc layout

- [subagent-trace.md](subagent-trace.md) — `subagent_trace.py` (L, 571)
- [memory-bundle.md](memory-bundle.md) — `contracts.py`, `policy.py`, `summarization.py`, `backends.py`, `token_budget.py`, `constants.py`

## Cross-cluster dependencies

**Imports from:**

- [`agent_runtime/persistence/`](../persistence/_index.md) — memory record types
- [`agent_runtime/execution/contracts.py`](../execution/contracts.md) — RuntimeEventEnvelope (consumed by subagent_trace)
- Deep Agents SDK summarization primitives
- `tiktoken` or equivalent (token counting)

**Imported by:**

- [`agent_runtime/execution/factory.py`](../execution/_index.md) — wires memory + budget into the runtime
- [`runtime_worker/handlers/run.py`](../runtime-worker/handlers-run.md) — fires summarization when budget exceeded
- [`agent_runtime/delegation/subagents/runner.py`](../delegation-subagents/runner.md) — subagent_trace consumes lifecycle events from here

## Use-case relevance

- [13-memory-compression-token-budget.md](../../use-cases/13-memory-compression-token-budget.md) — primary anchor.
- [10-single-subagent-delegation.md](../../use-cases/10-single-subagent-delegation.md), [11-multi-subagent-plus-tool.md](../../use-cases/11-multi-subagent-plus-tool.md) — `subagent_trace.py` projection.
