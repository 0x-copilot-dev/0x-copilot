# Cluster: `agent_runtime.observability`

## Cluster boundary

- **Paths:** [`services/ai-backend/src/agent_runtime/observability/`](../../../services/ai-backend/src/agent_runtime/observability/).
- **Primary entrypoints:** [`otel.py`](../../../services/ai-backend/src/agent_runtime/observability/otel.py), [`logging.py`](../../../services/ai-backend/src/agent_runtime/observability/logging.py), [`TelemetryBootstrap`](../../../services/ai-backend/src/agent_runtime/observability/otel.py) (class in same module).

## Static signals

| Tool                          | Scope                             | Result (2026-05-06)                                                                                                                                                                                                        |
| ----------------------------- | --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Ruff `F401`, `F841`           | `src/agent_runtime/observability` | No findings                                                                                                                                                                                                                |
| Vulture `--min-confidence 80` | same                              | **100%:** [`otel.py`](../../../services/ai-backend/src/agent_runtime/observability/otel.py) — `parent_context` param unused in `SafeAttributeSpanProcessor.on_start` (~69); `timeout_millis` unused in `force_flush` (~92) |
| Vulture `--min-confidence 60` | same                              | OTEL hooks (`on_start`, `on_end`, `shutdown`, `instrument_psycopg`) — **SDK callbacks**                                                                                                                                    |

## Wiring-checked

- **`SafeAttributeSpanProcessor`** — methods satisfy OpenTelemetry `SpanProcessor` interface; unused parameters are **forward-compat / signature match** — typically **keep** or prefix with `_` per style guide.

## Test-only usage

- `reset_for_tests` helpers — intentional.

## Likely dead / high-confidence candidates

- **`parent_context` / `timeout_millis`** — cosmetic unused params in OTEL overrides; safe cleanup as **`_parent_context`**, **`_timeout_millis`** if linters should stay quiet (no behavior change).

## Smells

- **Broad exception swallow** patterns — verify against logging policy (typed metadata only).

## Cross-cluster links

- HTTP middleware logging — [cluster-runtime-api.md](./cluster-runtime-api.md).

## Extended vulture inventory

Verbatim [Vulture](https://github.com/jendrikseipp/vulture) lines for this cluster’s paths (`vulture src --min-confidence 60` from `services/ai-backend`; **21** lines):

- [`artifacts/cluster-agent-runtime-observability-vulture.txt`](./artifacts/cluster-agent-runtime-observability-vulture.txt)

Merged output for all of `src/` (**639** lines): [`artifacts/vulture-min60-src-only.txt`](./artifacts/vulture-min60-src-only.txt).

These lists are **candidate** unused symbols — many entries are Pydantic validators, Protocol signatures, OTEL hooks, or FastAPI/RBAC decorators. Use as a triage queue, not an automatic delete list. Regenerate: [`README.md`](./README.md), [`artifacts/README.md`](./artifacts/README.md).
