# Unused code and smells — `services/ai-backend`

Tech-debt and audit notes for the canonical AI backend. These documents are **not** product specs; canonical behavior lives under [`services/ai-backend/docs/`](../../../services/ai-backend/docs/).

## Scope

- **In scope:** [`services/ai-backend`](../../../services/ai-backend) (`src/runtime_api`, `src/runtime_worker`, `src/runtime_adapters`, `src/agent_runtime`).
- **Out of scope:** `services/backend`, `services/backend-facade`, frontend, and other monorepo apps.

## Last audited

| Document                                     | Last updated |
| -------------------------------------------- | ------------ |
| This README                                  | 2026-05-06   |
| Cluster files + [`artifacts/`](./artifacts/) | 2026-05-06   |

Refresh dates when you re-run tools and edit cluster files.

### Vulture inventory scale

A full pass at **`--min-confidence 60`** over `src/` produces **634** candidate lines (exit code 3). Most are **false positives** (Pydantic validators, Protocol stubs, OTEL hooks, nested FastAPI handlers) or **test-only** symbols — but the raw list is the starting point for triage.

Committed verbatim listings:

| Artifact                                                                                                                       | Lines (approx.) |
| ------------------------------------------------------------------------------------------------------------------------------ | --------------- |
| [`artifacts/vulture-min60-src-only.txt`](./artifacts/vulture-min60-src-only.txt)                                               | 634             |
| [`artifacts/cluster-runtime-api-vulture.txt`](./artifacts/cluster-runtime-api-vulture.txt)                                     | 139             |
| [`artifacts/cluster-runtime-worker-vulture.txt`](./artifacts/cluster-runtime-worker-vulture.txt)                               | 10              |
| [`artifacts/cluster-runtime-adapters-vulture.txt`](./artifacts/cluster-runtime-adapters-vulture.txt)                           | 6               |
| [`artifacts/cluster-agent-runtime-execution-vulture.txt`](./artifacts/cluster-agent-runtime-execution-vulture.txt)             | 54              |
| [`artifacts/cluster-agent-runtime-capabilities-vulture.txt`](./artifacts/cluster-agent-runtime-capabilities-vulture.txt)       | 131             |
| [`artifacts/cluster-agent-runtime-persistence-vulture.txt`](./artifacts/cluster-agent-runtime-persistence-vulture.txt)         | 140             |
| [`artifacts/cluster-agent-runtime-context-memory-vulture.txt`](./artifacts/cluster-agent-runtime-context-memory-vulture.txt)   | 42              |
| [`artifacts/cluster-agent-runtime-delegation-vulture.txt`](./artifacts/cluster-agent-runtime-delegation-vulture.txt)           | 45              |
| [`artifacts/cluster-agent-runtime-domain-services-vulture.txt`](./artifacts/cluster-agent-runtime-domain-services-vulture.txt) | 29              |
| [`artifacts/cluster-agent-runtime-ops-economics-vulture.txt`](./artifacts/cluster-agent-runtime-ops-economics-vulture.txt)     | 13              |
| [`artifacts/cluster-agent-runtime-observability-vulture.txt`](./artifacts/cluster-agent-runtime-observability-vulture.txt)     | 21              |
| [`artifacts/cluster-agent-runtime-cross-cutting-vulture.txt`](./artifacts/cluster-agent-runtime-cross-cutting-vulture.txt)     | 4               |

See [`artifacts/README.md`](./artifacts/README.md) for regeneration notes.

## Methodology

Each [`cluster-*.md`](./) file follows the same structure:

1. **Cluster boundary** — Paths and primary entrypoints.
2. **Static signals** — Automated checks scoped to that tree.
3. **Wiring-checked** — Manual notes after grep / reading call sites (especially for LangGraph, FastAPI, and protocols).
4. **Test-only usage** — Symbols exercised only from `tests/`, not from production `src/` wiring.
5. **Likely dead / high-confidence candidates** — Items worth a deliberate removal or fix PR.
6. **Smells** — Qualitative issues (duplication, unused parameters, incomplete features).

### Commands (from repo root)

Use the service-local venv and the same `PYTHONPATH` as pytest ([`services/ai-backend/pyproject.toml`](../../../services/ai-backend/pyproject.toml)).

```bash
cd services/ai-backend
export PYTHONPATH=src:../../packages/service-contracts/src

# Unused imports and assigned-but-unused locals (currently clean on src/)
.venv/bin/python -m ruff check src --select F401,F841

# Dead-code candidates (many false positives; see below)
.venv/bin/pip install vulture
.venv/bin/vulture src --min-confidence 80

# Full candidate dump (committed under docs/unused-code/backend/artifacts/)
.venv/bin/vulture src --min-confidence 60 \
  | tee ../../docs/unused-code/backend/artifacts/vulture-min60-src-only.txt

# Optional: second path whitelist suppresses repeated false positives (see
# ../../docs/unused-code/ai-backend/vulture_whitelist.py). Pass it only when
# triaging; re-split artifacts if you change the command.
.venv/bin/vulture src ../../docs/unused-code/ai-backend/vulture_whitelist.py --min-confidence 60
```

**Optional:** install `basedpyright` in the venv and enable `reportUnusedImport`, `reportUnusedClass`, `reportUnusedFunction`, `reportUnusedVariable` in a **local** config overlay if you want type-checker unused reporting (not part of the default service `pyproject.toml` at the time of this audit).

### How to interpret “unused”

- **No tool proves full deadness** in a codebase with FastAPI route tables, Pydantic `model_validator` / `field_validator`, LangGraph registration, string-keyed tools, and optional adapters.
- **Vulture false positives (common):**
  - Pydantic private validators (`_normalize_*`) — invoked by the framework, not by direct Python calls.
  - Nested FastAPI route handlers (e.g. `healthz` inside `register_health_routes`) — registered via decorators.
  - `Protocol` method parameters in stub bodies — only signatures matter; parameter names can be flagged.
  - OTEL `SpanProcessor` / SDK hooks — called by OpenTelemetry, not project code.
  - Constants nested in `Keys`-style classes — referenced for documentation or future use; may appear unused to static analysis.
- **False negatives:** Code that is imported everywhere but never meaningfully executed; branches never hit by tests.

### Spanning symbols

Some symbols appear unused **inside** one directory but are only referenced from another cluster (e.g. worker calling a port implemented in adapters). After cluster-scoped runs, do a **repo-wide** search under `services/ai-backend` for the symbol name before treating it as dead.

### Cluster index

| File                                                                                   | Area                                 |
| -------------------------------------------------------------------------------------- | ------------------------------------ |
| [cluster-runtime-api.md](./cluster-runtime-api.md)                                     | HTTP API, SSE, schemas               |
| [cluster-runtime-worker.md](./cluster-runtime-worker.md)                               | Worker loop, streaming, jobs         |
| [cluster-runtime-adapters.md](./cluster-runtime-adapters.md)                           | In-memory and Postgres stores        |
| [cluster-agent-runtime-execution.md](./cluster-agent-runtime-execution.md)             | Graph, factory, providers, prompts   |
| [cluster-agent-runtime-capabilities.md](./cluster-agent-runtime-capabilities.md)       | Tools, skills, MCP, middleware       |
| [cluster-agent-runtime-persistence.md](./cluster-agent-runtime-persistence.md)         | Ports, records, schema, retention    |
| [cluster-agent-runtime-context-memory.md](./cluster-agent-runtime-context-memory.md)   | Context and memory                   |
| [cluster-agent-runtime-delegation.md](./cluster-agent-runtime-delegation.md)           | Subagents                            |
| [cluster-agent-runtime-domain-services.md](./cluster-agent-runtime-domain-services.md) | `agent_runtime/api` services         |
| [cluster-agent-runtime-ops-economics.md](./cluster-agent-runtime-ops-economics.md)     | Budgets, pricing, deployment profile |
| [cluster-agent-runtime-observability.md](./cluster-agent-runtime-observability.md)     | Logging, tracing, OTEL, metrics      |
| [cluster-agent-runtime-cross-cutting.md](./cluster-agent-runtime-cross-cutting.md)     | Settings, validation, package root   |
