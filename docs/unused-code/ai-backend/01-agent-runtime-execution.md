# Cluster 01 — agent_runtime.execution

**Last reviewed:** 2026-05-06 · **Revision:** `a1d79d7a61868a6a9ae774e3a46c875356b29b78`

## Cluster scope

Graph construction, runtime factory, model/stream adapters, execution contracts, and Deep Agents wiring under [`services/ai-backend/src/agent_runtime/execution/`](../../../services/ai-backend/src/agent_runtime/execution/).

## Entrypoints / wiring

- [`runtime_worker/handlers/run.py`](../../../services/ai-backend/src/runtime_worker/handlers/run.py) builds and executes the LangGraph / Deep Agents loop.
- [`execution/factory.py`](../../../services/ai-backend/src/agent_runtime/execution/factory.py) resolves authorized runtime inputs for a run.
- [`execution/deep_agent_builder.py`](../../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py) is the single seam for `deepagents.create_deep_agent`.

## Likely unused or low-value symbols

| Location                          | Symbol / issue                                                      | Evidence                                                                           | Confidence           | Action                                                                               |
| --------------------------------- | ------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | -------------------- | ------------------------------------------------------------------------------------ |
| `execution/deep_agent_builder.py` | Protocol methods `upload_files` / `aupload_files` parameter `files` | Vulture reports “unused variable `files`” inside **Protocol** stub bodies (`...`). | Low (false positive) | Ignore for dead-code purposes; optional: rename to `_files` if Ruff ever flags them. |

No standalone modules under `execution/` showed **unused classes or functions** at Vulture ≥80% aside from Protocol noise.

## Test-only vs production

Heavy coverage via worker and factory tests; provider adapters differ by CI env (some branches only hit when specific providers run).

## Code smells

- **Protocol stubs vs tooling:** Static dead-code scanners confuse `Protocol` bodies with real implementations — document or whitelist (see [vulture_whitelist.py](./vulture_whitelist.py)).
- **Surface area:** `factory.py` and `graph.py` remain coordination hotspots; prefer localized changes over growing module-level helpers (per service engineering rules).

## Follow-ups

- If tightening typings on `DeepAgentsBackend`, keep Protocol shapes aligned with upstream `deepagents` to avoid drift.

## Deep scan (Vulture min 50)

**Raw lines (this subtree):** 54 · See [SUPPLEMENT-deep-scan-vulture50.md](./SUPPLEMENT-deep-scan-vulture50.md).

### High-signal candidates

| Item                                                                                                                                                | Notes                                                                                                                                                                                                              |
| --------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`execution/state.py`](../../../services/ai-backend/src/agent_runtime/execution/state.py)                                                           | `RuntimeMetadata` and `RuntimeMessage` / `RuntimeMessages` **appear unreferenced** from any other `services/ai-backend/src/` file. **0%** pytest coverage. Treat as **likely dead** or not-yet-wired graph typing. |
| [`execution/runtime.py`](../../../services/ai-backend/src/agent_runtime/execution/runtime.py)                                                       | Vulture: `runtime_run_handle` unused — confirm with `rg` / factory before removal.                                                                                                                                 |
| [`execution/providers/anthropic_stream_adapter.py`](../../../services/ai-backend/src/agent_runtime/execution/providers/anthropic_stream_adapter.py) | `AnthropicCitationStreamAdapter` has **no** `src/` imports outside its module; **tests** use it. May be **test-only** or an alternate code path not selected in default runs.                                      |

### Noise

- `DeepAgentsBackend` Protocol members reported as unused methods.
- `execution/contracts.py` — many “unused variable” hits are **named string constants** on nested registries; verify with `rg` on the public attribute path, not the bare name.
