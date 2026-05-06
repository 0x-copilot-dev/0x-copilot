# Cluster: `agent_runtime.delegation`

## Cluster boundary

- **Paths:** [`services/ai-backend/src/agent_runtime/delegation/`](../../../services/ai-backend/src/agent_runtime/delegation/) (subagents: definitions, runner, handoff, contracts).
- **Primary entrypoints:** [`subagents/runner.py`](../../../services/ai-backend/src/agent_runtime/delegation/subagents/runner.py), [`subagents/definitions.py`](../../../services/ai-backend/src/agent_runtime/delegation/subagents/definitions.py).

## Static signals

| Tool                          | Scope                          | Result (2026-05-06)                                                                                                                        |
| ----------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Ruff `F401`, `F841`           | `src/agent_runtime/delegation` | No findings                                                                                                                                |
| Vulture `--min-confidence 80` | same                           | No 80%+ hits                                                                                                                               |
| Vulture `--min-confidence 60` | same                           | Heavy Pydantic surface on [`contracts.py`](../../../services/ai-backend/src/agent_runtime/delegation/subagents/contracts.py) and constants |

## Wiring-checked

- **`list_tasks`** on runner — flagged at 60%; may be admin/debug API — grep callers across `src/` and tests.

## Test-only usage

- Contract models exercised heavily in unit tests; production wiring flows through worker execution path.

## Likely dead / high-confidence candidates

- **`handoff.build_task`** at 60% — verify dynamic invocation from graph vs truly unused.

## Smells

- **Large `contracts.py`** — same validator-noise pattern as other Pydantic-heavy modules.

## Cross-cluster links

- Execution builds subagents — [cluster-agent-runtime-execution.md](./cluster-agent-runtime-execution.md).
- Persistence records for subagents — [cluster-agent-runtime-persistence.md](./cluster-agent-runtime-persistence.md).

## Extended vulture inventory

Verbatim [Vulture](https://github.com/jendrikseipp/vulture) lines for this cluster’s paths (`vulture src --min-confidence 60` from `services/ai-backend`; **45** lines):

- [`artifacts/cluster-agent-runtime-delegation-vulture.txt`](./artifacts/cluster-agent-runtime-delegation-vulture.txt)

Merged output for all of `src/` (**634** lines): [`artifacts/vulture-min60-src-only.txt`](./artifacts/vulture-min60-src-only.txt).

These lists are **candidate** unused symbols — many entries are Pydantic validators, Protocol signatures, OTEL hooks, or FastAPI/RBAC decorators. Use as a triage queue, not an automatic delete list. Regenerate: [`README.md`](./README.md), [`artifacts/README.md`](./artifacts/README.md).
