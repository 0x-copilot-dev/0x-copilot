# Unused code and smell audit — ai-backend

This directory holds **cluster-scoped** notes from passes over `services/ai-backend` (`src/` + `tests/`). It is **documentation**, not an automated gate: findings mix static signals, coverage, manual tracing, and interpreted scanner output.

## Audit metadata

| Field         | Value                                                                      |
| ------------- | -------------------------------------------------------------------------- |
| Last reviewed | 2026-05-06                                                                 |
| Git revision  | `a1d79d7a61868a6a9ae774e3a46c875356b29b78`                                 |
| Environment   | `services/ai-backend/.venv` with `ruff`, `vulture`, `pytest-cov` for scans |

## Cluster index

| Doc                                                                                    | Scope                                                                            |
| -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| [01-agent-runtime-execution.md](./01-agent-runtime-execution.md)                       | `agent_runtime/execution/`                                                       |
| [02-agent-runtime-capabilities.md](./02-agent-runtime-capabilities.md)                 | `agent_runtime/capabilities/`                                                    |
| [03-agent-runtime-context-memory.md](./03-agent-runtime-context-memory.md)             | `agent_runtime/context/memory/`                                                  |
| [04-agent-runtime-delegation-subagents.md](./04-agent-runtime-delegation-subagents.md) | `agent_runtime/delegation/subagents/`                                            |
| [05-agent-runtime-persistence.md](./05-agent-runtime-persistence.md)                   | `agent_runtime/persistence/`                                                     |
| [06-agent-runtime-api-and-services.md](./06-agent-runtime-api-and-services.md)         | `agent_runtime/api/`                                                             |
| [07-agent-runtime-cross-cutting.md](./07-agent-runtime-cross-cutting.md)               | observability, budgets, pricing, retention, deployment, prompts, `validation.py` |
| [08-runtime-api.md](./08-runtime-api.md)                                               | `runtime_api/`                                                                   |
| [09-runtime-worker.md](./09-runtime-worker.md)                                         | `runtime_worker/`                                                                |
| [10-runtime-adapters.md](./10-runtime-adapters.md)                                     | `runtime_adapters/`                                                              |

**Supplement:** [SUPPLEMENT-deep-scan-vulture50.md](./SUPPLEMENT-deep-scan-vulture50.md) — full **Vulture min-50** inventory (635 lines), per-cluster counts, and interpretation (why most lines are false positives).

SQL under `services/ai-backend/migrations/` is **not** analyzed via import graphs.

## Why there are “so many” unused findings

A single low-threshold Vulture run emits **hundreds** of rows. Most are:

| Pattern                                       | Why Vulture complains                          | Reality                                        |
| --------------------------------------------- | ---------------------------------------------- | ---------------------------------------------- |
| Nested `Keys` / `Messages` / `Values` classes | Flags inner assignments (`AFTER_SEQUENCE = …`) | Referenced as `Keys.Field.AFTER_SEQUENCE` etc. |
| `Protocol` / ABC bodies                       | Ellipsis bodies and parameter names            | Contract only                                  |
| `TYPE_CHECKING` imports                       | Not imported at runtime                        | Intentional                                    |
| FastAPI handlers                              | Callable name “unused”                         | Mounted via decorator                          |
| Code only imported from `tests/`              | Not seen when scanning `src/`                  | Test-backed, not dead                          |

Use **SUPPLEMENT** + cluster sections **“Deep scan”** for counts; use **grep from app/worker/factory entrypoints** before deleting anything.

## Methodology

### 1. Ruff

```bash
cd services/ai-backend
PYTHONPATH=src:../../packages/service-contracts/src .venv/bin/ruff check src tests
```

**Latest scan:** `F401` — `UsageAttributionResolver` imported in `runtime_worker/streaming_executor.py` reported unused while it appears in annotations (tooling mismatch; consider `TYPE_CHECKING` split). Fix in code separately from this doc.

### 2. Vulture (coarse vs fine)

```bash
cd services/ai-backend
.venv/bin/vulture src --min-confidence 80
.venv/bin/vulture src --min-confidence 50 --sort-by-size
```

Pass [vulture_whitelist.py](./vulture_whitelist.py) as a **second PATH** (not `--whitelist`):

```bash
.venv/bin/vulture src ../../docs/unused-code/ai-backend/vulture_whitelist.py --min-confidence 80
```

Starter whitelist entries:

```bash
.venv/bin/vulture src --min-confidence 80 --make-whitelist
```

### 3. Coverage (pytest-cov)

```bash
cd services/ai-backend
PYTHONPATH=src:../../packages/service-contracts/src .venv/bin/python -m pytest \
  --cov=agent_runtime --cov=runtime_api --cov=runtime_worker --cov=runtime_adapters \
  --cov-report=term-missing --no-cov-on-fail -q
```

**Interpretation:** Low coverage ≠ dead code (Postgres-only branches, worker `__main__`, jobs). **0% on a module** + **zero imports from `src/`** is stronger evidence (e.g. `schemas/inbox.py`, `execution/state.py` — see cluster docs).

### 4. Manual tracing

Entrypoints:

- `services/ai-backend/src/runtime_api/app.py`
- `services/ai-backend/src/runtime_worker/__main__.py`
- `services/ai-backend/src/runtime_adapters/factory.py`

## Regenerating

Re-run the commands above; update **Audit metadata**, each cluster **Last reviewed**, and **SUPPLEMENT** if you capture a new Vulture dump.
