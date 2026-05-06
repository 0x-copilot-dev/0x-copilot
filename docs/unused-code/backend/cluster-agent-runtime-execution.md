# Cluster: `agent_runtime.execution` + `agent_runtime.prompts`

## Cluster boundary

- **Paths:**
  - [`services/ai-backend/src/agent_runtime/execution/`](../../../services/ai-backend/src/agent_runtime/execution/)
  - [`services/ai-backend/src/agent_runtime/prompts/`](../../../services/ai-backend/src/agent_runtime/prompts/)
- **Primary entrypoints:** [`factory.py`](../../../services/ai-backend/src/agent_runtime/execution/factory.py), [`graph.py`](../../../services/ai-backend/src/agent_runtime/execution/graph.py), [`deep_agent_builder.py`](../../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py), [`runtime.py`](../../../services/ai-backend/src/agent_runtime/execution/runtime.py).

## Static signals

| Tool                          | Scope                                                      | Result (2026-05-06)                                                                                                                                                                           |
| ----------------------------- | ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Ruff `F401`, `F841`           | `src/agent_runtime/execution`, `src/agent_runtime/prompts` | No findings                                                                                                                                                                                   |
| Vulture `--min-confidence 80` | same                                                       | **100%:** unused parameter `files` in `upload_files` / `aupload_files` ([`deep_agent_builder.py`](../../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py) ~125, ~131) |
| Vulture `--min-confidence 60` | same                                                       | Many protocol/signature and Pydantic-style hits on [`contracts.py`](../../../services/ai-backend/src/agent_runtime/execution/contracts.py)                                                    |

## Wiring-checked

- **`DeepAgentsBackend` protocol** — `download_files` / `upload_files` / async variants are interface requirements for Deep Agents; “unused” methods on the **Protocol** are **false positives**.
- **`AnthropicCitationStreamAdapter`** — **test-only** in production tree; used in [`tests/unit/agent_runtime/execution/test_citation_substitution.py`](../../../services/ai-backend/tests/unit/agent_runtime/execution/test_citation_substitution.py). Not wired into live streaming path unless explicitly imported elsewhere — **confirm product intent** (tests-only adapter vs future citation streaming).
- **`runtime_run_handle`** ([`runtime.py`](../../../services/ai-backend/src/agent_runtime/execution/runtime.py)) — referenced from **tests** (`test_runtime_observability`, `test_runtime_contracts`), not from worker/runtime wiring grep of `src/` alone.

## Test-only usage

- `AnthropicCitationStreamAdapter`, `runtime_run_handle` — tests and harness utilities.

## Likely dead / high-confidence candidates

1. **`upload_files` / `aupload_files` parameter `files`** — Vulture 100% unused inside stub bodies on `DeepAgentsBackend` protocol methods in [`deep_agent_builder.py`](../../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py). Protocol-compliant stubs often omit parameter use; consider **`_files` prefix or `typing.Protocol` structural typing** cleanup — cosmetic, not behavioral deletion.

## Smells

- **`contracts.py` validator noise** — same as API schemas: huge surface for false positives.
- **Citation adapter not referenced from `src/` outside tests** — smell if product expects Anthropic citation substitution in production paths.

## Cross-cluster links

- Tool execution and middleware interlock with [cluster-agent-runtime-capabilities.md](./cluster-agent-runtime-capabilities.md).
- Graph uses persistence ports via worker/factory — [cluster-runtime-adapters.md](./cluster-runtime-adapters.md).

## Extended vulture inventory

Verbatim [Vulture](https://github.com/jendrikseipp/vulture) lines for this cluster’s paths (`vulture src --min-confidence 60` from `services/ai-backend`; **54** lines):

- [`artifacts/cluster-agent-runtime-execution-vulture.txt`](./artifacts/cluster-agent-runtime-execution-vulture.txt)

Merged output for all of `src/` (**634** lines): [`artifacts/vulture-min60-src-only.txt`](./artifacts/vulture-min60-src-only.txt).

These lists are **candidate** unused symbols — many entries are Pydantic validators, Protocol signatures, OTEL hooks, or FastAPI/RBAC decorators. Use as a triage queue, not an automatic delete list. Regenerate: [`README.md`](./README.md), [`artifacts/README.md`](./artifacts/README.md).
