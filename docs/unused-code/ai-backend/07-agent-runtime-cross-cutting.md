# Cluster 07 — agent_runtime cross-cutting

**Last reviewed:** 2026-05-06 · **Revision:** `a1d79d7a61868a6a9ae774e3a46c875356b29b78`

## Cluster scope

Observability, budgets, pricing, retention helpers, deployment profile, prompts, and shared [`validation.py`](../../src/agent_runtime/validation.py). This cluster is **intentionally heterogeneous** (see plan rationale).

## Entrypoints / wiring

- **Budgets/pricing:** Worker run path and usage rollup loops consult budgets and pricing catalogs.
- **Observability:** FastAPI instrumentation hooks and DB statement metrics wrap adapters.
- **Retention:** API routes + worker sweepers call [`retention/policy_resolver.py`](../../src/agent_runtime/retention/policy_resolver.py).
- **Prompts:** Pulled through execution factory / Deep Agents builder paths.
- **Validation:** Imported broadly from schemas, capabilities, and persistence records (`ValueNormalizer`).

## Likely unused or low-value symbols

| Location                                                              | Symbol / issue                                | Evidence                                                | Confidence | Action                                                     |
| --------------------------------------------------------------------- | --------------------------------------------- | ------------------------------------------------------- | ---------- | ---------------------------------------------------------- |
| `observability/otel.py`                                               | `parent_context`, `timeout_millis`            | Vulture unused-variable inside instrumentation helpers. | Medium     | Confirm spans use parent context; drop bind or prefix `_`. |
| `observability/db_statement_metrics.py`                               | `args`                                        | Repeated unused binding in wrapper.                     | Medium     | Rename to `_args` if truly discarded.                      |
| `budgets/enforcer.py`, `estimator.py`, `period.py`, `reservations.py` | `cls` on methods that never touch class state | Vulture flags `_aggregate(cls, ...)` style.             | Low–medium | Consider `@staticmethod` where appropriate.                |
| `pricing/`, `retention/`, `deployment/`, `prompts/`                   | —                                             | No ≥80% Vulture hits in quick directory scan.           | —          | —                                                          |

## Test-only vs production

OTel exporters and DB metrics may not initialize in all unit tests — coverage gaps do not imply dead code.

## Code smells

- **Cross-cutting bucket:** When this file grows, split docs into observability vs economics (budgets/pricing) vs prompts/deployment per the plan’s escape hatch.
- **Unused `cls`:** Minor consistency smell; batch-fix in a hygiene PR.

## Follow-ups

- Correlate OTel variable findings with OpenTelemetry API expectations (sometimes parameters exist for future attributes).

## Deep scan (Vulture min 50)

**Raw lines (this subtree):** 35 · See [SUPPLEMENT-deep-scan-vulture50.md](./SUPPLEMENT-deep-scan-vulture50.md).

### Cross-links

| Item                                                                                                        | Notes                                                                                                                                                                                                                                             |
| ----------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`usage_attribution.py`](../../../services/ai-backend/src/agent_runtime/observability/usage_attribution.py) | `UsageAttributionResolver` class flagged unused while [`streaming_executor.py`](../../../services/ai-backend/src/runtime_worker/streaming_executor.py) imports it — **Ruff F401** disagrees with Vulture; resolve import / `TYPE_CHECKING` usage. |
| [`pricing/seed_loader.py`](../../../services/ai-backend/src/agent_runtime/pricing/seed_loader.py)           | `PricingSeedLoader` flagged unused class — **tests import** (`test_calculator_and_seeds.py`).                                                                                                                                                     |
| [`deployment/profile.py`](../../../services/ai-backend/src/agent_runtime/deployment/profile.py)             | Many optional deployment switches flagged unused — likely **future / doc-only** fields unless wired in settings loader.                                                                                                                           |
