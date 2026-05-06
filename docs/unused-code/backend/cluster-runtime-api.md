# Cluster: `runtime_api`

## Cluster boundary

- **Paths:** [`services/ai-backend/src/runtime_api/`](../../../services/ai-backend/src/runtime_api/) (FastAPI app, HTTP routers, Pydantic schemas, SSE helpers, auth helpers).
- **Primary entrypoints:** [`app.py`](../../../services/ai-backend/src/runtime_api/app.py) (`create_app`), route modules under [`http/`](../../../services/ai-backend/src/runtime_api/http/), [`routes/health.py`](../../../services/ai-backend/src/runtime_api/routes/health.py).

## Static signals

| Tool                          | Scope             | Result (2026-05-06)                                                    |
| ----------------------------- | ----------------- | ---------------------------------------------------------------------- |
| Ruff `F401`, `F841`           | `src/runtime_api` | No findings                                                            |
| Vulture `--min-confidence 80` | `src/runtime_api` | No 80%+ hits in this tree alone                                        |
| Vulture `--min-confidence 60` | `src/runtime_api` | Large volume — mostly **Pydantic validators** and schema field aliases |

Vulture at 60% flags hundreds of `_normalize_*` / `_redact_*` methods on schema models and unused-looking locals on computed JSON shapes. Treat these as **framework-driven**, not dead code, unless you confirm no Pydantic wiring.

## Wiring-checked

- **`healthz` / `readyz`** in [`routes/health.py`](../../../services/ai-backend/src/runtime_api/routes/health.py) — nested functions registered via `@app.get`; Vulture reports “unused function” — **false positive**.
- **RBAC decorator attributes** on routes (`__rbac_required_scopes__`, etc.) — set/read by RBAC helpers; **false positive** for attribute assignment on router functions.
- **`runtime_inbox_bus` / `runtime_in_process_worker`** on `FastAPI` state in [`app.py`](../../../services/ai-backend/src/runtime_api/app.py) — application state for lifespan/tests; may appear unused to static analysis.

## Test-only usage

- No cluster-wide pattern beyond normal route tests; schemas are exercised indirectly via API tests.

## Likely dead / high-confidence candidates

- **[`schemas/inbox.py`](../../../services/ai-backend/src/runtime_api/schemas/inbox.py)** — Vulture reported unused class `InboxEventEnvelopeSchema` at 60%. Confirm whether inbox SSE payloads still use this model or a duplicate elsewhere before deleting.

## Smells

- **Schema sprawl:** Many similar `_normalize_*` methods across `schemas/*.py`; legitimate for Pydantic but noisy for dead-code scans — prefer documenting “validators are intentional” over chasing Vulture output here.
- **Large response models** with locals flagged unused (`has_more`, breakdown fields, etc.) — often serialization-side; verify against OpenAPI / consumers before renaming.

## Cross-cluster links

- Retention HTTP schemas overlap domain retention policy — see [cluster-agent-runtime-persistence.md](./cluster-agent-runtime-persistence.md).
- Worker consumes run/stream contracts defined here — see [cluster-runtime-worker.md](./cluster-runtime-worker.md).

## Extended vulture inventory

Verbatim [Vulture](https://github.com/jendrikseipp/vulture) lines for this cluster’s paths (`vulture src --min-confidence 60` from `services/ai-backend`; **145** lines):

- [`artifacts/cluster-runtime-api-vulture.txt`](./artifacts/cluster-runtime-api-vulture.txt)

Merged output for all of `src/` (**639** lines): [`artifacts/vulture-min60-src-only.txt`](./artifacts/vulture-min60-src-only.txt).

These lists are **candidate** unused symbols — many entries are Pydantic validators, Protocol signatures, OTEL hooks, or FastAPI/RBAC decorators. Use as a triage queue, not an automatic delete list. Regenerate: [`README.md`](./README.md), [`artifacts/README.md`](./artifacts/README.md).
