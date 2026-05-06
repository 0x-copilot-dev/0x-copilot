# Cluster: `agent_runtime.context` (memory)

## Cluster boundary

- **Paths:** [`services/ai-backend/src/agent_runtime/context/`](../../../services/ai-backend/src/agent_runtime/context/) (especially [`memory/`](../../../services/ai-backend/src/agent_runtime/context/memory/)).
- **Primary entrypoints:** [`memory/backends.py`](../../../services/ai-backend/src/agent_runtime/context/memory/backends.py), [`memory/contracts.py`](../../../services/ai-backend/src/agent_runtime/context/memory/contracts.py), [`memory/policy.py`](../../../services/ai-backend/src/agent_runtime/context/memory/policy.py), [`memory/subagent_trace.py`](../../../services/ai-backend/src/agent_runtime/context/memory/subagent_trace.py).

## Static signals

| Tool                          | Scope                       | Result (2026-05-06)                                                  |
| ----------------------------- | --------------------------- | -------------------------------------------------------------------- |
| Ruff `F401`, `F841`           | `src/agent_runtime/context` | No findings                                                          |
| Vulture `--min-confidence 80` | same                        | No 80%+ hits                                                         |
| Vulture `--min-confidence 60` | same                        | Dominated by Pydantic validators on contracts/backends and constants |

## Wiring-checked

- **`MemoryAccessRequest`** and policy helpers flagged “unused” — likely framework callbacks or future authorization hooks; grep before removal.

## Test-only usage

- Subagent trace `edit`/`grep` methods parallel Deep Agents filesystem API — may mirror protocol surface; verify delegation tests.

## Likely dead / high-confidence candidates

- None isolated without defeating Pydantic/protocol noise — prefer targeted coverage gaps over Vulture.

## Smells

- **Overlap with persistence memory records** — ensure naming and scope boundaries stay clear between context memory and `persistence/records/memory.py`.

## Cross-cluster links

- Delegation uses memory routing — [cluster-agent-runtime-delegation.md](./cluster-agent-runtime-delegation.md).
- Persistence stores memory metadata — [cluster-agent-runtime-persistence.md](./cluster-agent-runtime-persistence.md).

## Extended vulture inventory

Verbatim [Vulture](https://github.com/jendrikseipp/vulture) lines for this cluster’s paths (`vulture src --min-confidence 60` from `services/ai-backend`; **42** lines):

- [`artifacts/cluster-agent-runtime-context-memory-vulture.txt`](./artifacts/cluster-agent-runtime-context-memory-vulture.txt)

Merged output for all of `src/` (**639** lines): [`artifacts/vulture-min60-src-only.txt`](./artifacts/vulture-min60-src-only.txt).

These lists are **candidate** unused symbols — many entries are Pydantic validators, Protocol signatures, OTEL hooks, or FastAPI/RBAC decorators. Use as a triage queue, not an automatic delete list. Regenerate: [`README.md`](./README.md), [`artifacts/README.md`](./artifacts/README.md).
