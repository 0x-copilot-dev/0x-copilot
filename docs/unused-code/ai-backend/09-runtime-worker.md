# Cluster 09 — runtime_worker

**Last reviewed:** 2026-05-06 · **Revision:** `a1d79d7a61868a6a9ae774e3a46c875356b29b78`

## Cluster scope

Queued command consumer, streaming mappers, handlers, background jobs, and worker audit helpers under [`src/runtime_worker/`](../../src/runtime_worker/).

## Entrypoints / wiring

- [`runtime_worker/__main__.py`](../../src/runtime_worker/__main__.py) — CLI entry (`python -m runtime_worker`); **not imported** by tests as a module body.
- [`runtime_worker/loop.py`](../../src/runtime_worker/loop.py) — dequeue / retry / dead-letter loop.

## Likely unused or low-value symbols

| Location                     | Symbol / issue     | Evidence                                                      | Confidence | Action                                                                    |
| ---------------------------- | ------------------ | ------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------- |
| `runtime_worker/__main__.py` | Entire module body | **0%** pytest coverage — expected for subprocess entrypoints. | Low        | Do **not** treat as dead; smoke-test via CLI or integration if concerned. |

Per-directory Vulture ≥80% on `src/runtime_worker/` produced **no** additional unused-function hits in the automated pass (noise concentrates in port Protocols elsewhere).

## Test-only vs production

Jobs (`encrypt_existing_columns`, retention sweeper, approval expiry) may run only in deployed environments — unit tests cover subsets.

## Code smells

- **`usage_rollup_loop.py` ~58% coverage** — suggests periodic loop branches under-tested; review idle/shutdown paths.
- **Streaming modules** (`stream_events.py`, `stream_tools.py`, …) are large — redundant event-shape handling can creep in across providers.

## Follow-ups

- Add a lightweight integration or subprocess test for `__main__` argument parsing if regressions become painful.

## Deep scan (Vulture min 50)

**Raw lines (this subtree):** 10 · See [SUPPLEMENT-deep-scan-vulture50.md](./SUPPLEMENT-deep-scan-vulture50.md).

### Tooling conflict

| Item                                                                                             | Notes                                                                                                                                                                                                       |
| ------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`streaming_executor.py`](../../../services/ai-backend/src/runtime_worker/streaming_executor.py) | **Ruff `F401`** on `UsageAttributionResolver` while the name appears in annotations — align `TYPE_CHECKING` imports or Ruff config. **Vulture** also flags the class file under observability (cluster 07). |

### Other

- Large “unused method” hits on streaming helpers often reflect **optional branches** (subagent / tool payloads) — pair with coverage gaps, not deletion.
