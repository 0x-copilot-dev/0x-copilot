# Cluster: `agent_runtime.capabilities`

## Cluster boundary

- **Paths:** [`services/ai-backend/src/agent_runtime/capabilities/`](../../../services/ai-backend/src/agent_runtime/capabilities/) (tools, skills, MCP, citations, loaders, middleware).
- **Primary entrypoints:** [`tools/loader.py`](../../../services/ai-backend/src/agent_runtime/capabilities/tools/loader.py), [`mcp/loader.py`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/loader.py), [`skills/manifest.py`](../../../services/ai-backend/src/agent_runtime/capabilities/skills/manifest.py), [`tool_budget_middleware.py`](../../../services/ai-backend/src/agent_runtime/capabilities/tool_budget_middleware.py).

## Static signals

| Tool                          | Scope                            | Result (2026-05-06)                                                                                                                                                                        |
| ----------------------------- | -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Ruff `F401`, `F841`           | `src/agent_runtime/capabilities` | No findings                                                                                                                                                                                |
| Vulture `--min-confidence 80` | same                             | No 80%+ hits                                                                                                                                                                               |
| Vulture `--min-confidence 60` | same                             | **130 lines** in [`artifacts/cluster-agent-runtime-capabilities-vulture.txt`](./artifacts/cluster-agent-runtime-capabilities-vulture.txt); dominated by Pydantic card models and constants |

## Wiring-checked

- **`ToolBudgetMiddleware`** — Per grep of `services/ai-backend/src`, the **class is not imported** outside its module and [`deep_agent_builder.py`](../../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py) docstring/spec references. [`tool_budget_middleware.py`](../../../services/ai-backend/src/agent_runtime/capabilities/tool_budget_middleware.py) notes injection into `RuntimeDependencies` as future work. **Tests** exercise it ([`tests/unit/agent_runtime/capabilities/test_tool_budget_middleware.py`](../../../services/ai-backend/tests/unit/agent_runtime/capabilities/test_tool_budget_middleware.py)). **Smell: middleware specified but not wired into production execution path** until explicitly integrated.

- **`draft_backend._ToolRuntimeProxy`**, `edit`/`grep` on draft backend — may mirror Deep Agents filesystem operations; verify LangGraph/deep-agent wiring before removal.

## Test-only usage

- `ToolBudgetMiddleware` — tests + docs/spec references.

## Likely dead / high-confidence candidates

- **Production wiring gap:** If product requirements mandate tool budgets, missing wiring is **dead feature surface** (implementation exists, runtime path may not invoke `check_admit`).

## Smells

- **Duplicate card/constant patterns** across `mcp/cards.py`, `tools/cards.py`, `capabilities/citations.py` — maintenance burden; consider shared validation helpers (already partially centralized via constants classes).
- **High Vulture noise** — treat this cluster as **grep-first** for removal decisions.

## Cross-cluster links

- Execution builds tool lists — [cluster-agent-runtime-execution.md](./cluster-agent-runtime-execution.md).
- MCP OAuth/token ownership ultimately lives in core backend — boundary per workspace architecture; do not duplicate vault logic here.

## Extended vulture inventory

Verbatim [Vulture](https://github.com/jendrikseipp/vulture) lines for this cluster’s paths (`vulture src --min-confidence 60` from `services/ai-backend`; **130** lines):

- [`artifacts/cluster-agent-runtime-capabilities-vulture.txt`](./artifacts/cluster-agent-runtime-capabilities-vulture.txt)

Merged output for all of `src/` (**639** lines): [`artifacts/vulture-min60-src-only.txt`](./artifacts/vulture-min60-src-only.txt).

These lists are **candidate** unused symbols — many entries are Pydantic validators, Protocol signatures, OTEL hooks, or FastAPI/RBAC decorators. Use as a triage queue, not an automatic delete list. Regenerate: [`README.md`](./README.md), [`artifacts/README.md`](./artifacts/README.md).
