# Cluster 02 — agent_runtime.capabilities

**Last reviewed:** 2026-05-06 · **Revision:** `a1d79d7a61868a6a9ae774e3a46c875356b29b78`

## Cluster scope

Tools, MCP loading/middleware, skills, draft backend, citation ledger, and related registries under [`services/ai-backend/src/agent_runtime/capabilities/`](../../../services/ai-backend/src/agent_runtime/capabilities/).

## Entrypoints / wiring

- Worker run handler binds toolkits and MCP middleware before graph execution.
- [`CitationLedger`](../../../services/ai-backend/src/agent_runtime/capabilities/citations.py) is installed per run via contextvar in the worker.
- MCP discovery service is invoked from tool/MCP paths when surfacing auth cards.

## Likely unused or low-value symbols

| Location                                    | Symbol / issue                                      | Evidence                                                               | Confidence           | Action                                            |
| ------------------------------------------- | --------------------------------------------------- | ---------------------------------------------------------------------- | -------------------- | ------------------------------------------------- |
| `capabilities/citations.py`                 | Imports `RuntimeEventProducer`, `CitationStorePort` | Vulture ≥80% flags them as unused; both live under `if TYPE_CHECKING`. | Low (false positive) | No removal; optional `# noqa` or whitelist entry. |
| `capabilities/mcp/middleware/*`, `skills/*` | —                                                   | No ≥80% unused-function hits in per-directory Vulture pass.            | —                    | —                                                 |

## Test-only vs production

Many middleware paths are covered via unit tests with in-memory stores; Postgres-backed MCP registry behavior may differ in production.

## Code smells

- **TYPE_CHECKING vs Vulture:** Several capability modules use typing-only imports; team-wide policy could standardize on whitelist entries rather than chasing scanner noise.
- **Coupling:** Tools + MCP + skills share middleware stacks — unused middleware hooks would require tracing from `factory`/`run` handler, not grep of `capabilities/` alone.

## Follow-ups

- Re-run `vulture src ../../docs/unused-code/ai-backend/vulture_whitelist.py` after extending the whitelist for repeated TYPE_CHECKING patterns.

## Deep scan (Vulture min 50)

**Raw lines (this subtree):** 131 · See [SUPPLEMENT-deep-scan-vulture50.md](./SUPPLEMENT-deep-scan-vulture50.md).

### High-signal product / wiring gaps

| Item                                                                                                                            | Notes                                                                                                                                                                                                                                                                                                                                |
| ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`tool_budget_middleware.py`](../../../services/ai-backend/src/agent_runtime/capabilities/tool_budget_middleware.py)            | `ToolBudgetMiddleware` / `check_admit` appear **only in unit tests and docstrings** — **no import from `execution/factory.py` or worker handler `src/` paths**. Align with spec [B8-tool-budget.md](../../../services/ai-backend/docs/specs/usage/B8-tool-budget.md): either wire into runtime dependencies or treat as staged work. |
| [`anthropic_stream_adapter.py`](../../../services/ai-backend/src/agent_runtime/execution/providers/anthropic_stream_adapter.py) | `AnthropicCitationStreamAdapter` — **no production `src/` importers** (cluster 01); citations may never run through this class in default builds.                                                                                                                                                                                    |

### Noise (~100+ lines)

- **`mcp/constants.py`, `mcp/cards.py`, `tools/cards.py`, `skills/constants.py`** — dense nested key metadata; Vulture lists almost every assignment as “unused variable”.
- **`skills/constants.py`** — inner class `DeepAgents` flagged “unused class”; likely referenced as `....DeepAgents.*` elsewhere.
