# Cluster 10 — runtime_adapters

**Last reviewed:** 2026-05-06 · **Revision:** `a1d79d7a61868a6a9ae774e3a46c875356b29b78`

## Cluster scope

Adapter factory plus in-memory and Postgres implementations under [`src/runtime_adapters/`](../../src/runtime_adapters/), including [`async_wrappers.py`](../../src/runtime_adapters/async_wrappers.py).

## Entrypoints / wiring

- [`RuntimeAdapterFactory.from_settings`](../../src/runtime_adapters/factory.py) — sync ports for `in_memory` backend only.
- [`RuntimeAdapterFactory.async_from_settings`](../../src/runtime_adapters/factory.py) — async ports for `in_memory_async` + `postgres`.
- Lifespan owners call `await store.open()` / `await store.migrate()` **outside** the factory (see docstring), not inside `async_from_settings`.

## Likely unused or low-value symbols

| Location                        | Symbol / issue                                      | Evidence                                                                          | Confidence | Action                                                                                                                                                                      |
| ------------------------------- | --------------------------------------------------- | --------------------------------------------------------------------------------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `factory.py`                    | Parameter `migrate: bool = True` on `from_settings` | Never read in method body; no caller passes it explicitly (grep `from_settings`). | **High**   | **Remove** parameter (breaking change only if external callers exist — repo-internal appears safe) **or** implement migration hook for sync backend if that was the intent. |
| `postgres/runtime_api_store.py` | `args` unused (Vulture spot)                        | Single hotspot inside large file (~900+ LOC touched rarely).                      | Medium     | Inspect line ~463 — likely SQL helper callback signature; rename `_args`.                                                                                                   |

### Coverage interpretation

- **Postgres `runtime_api_store.py` ~35%** and **share stores ~22%** in pytest-cov output reflect branch-heavy SQL paths and newer share features — **not** automatic deletion candidates without proving no production route reaches them.
- **`in_memory/share_store.py` low coverage** — similar: exercised primarily via focused tests today.

## Test-only vs production

CI heavily uses in-memory / `in_memory_async`; Postgres-specific SQL often needs docker integration or targeted adapter tests.

## Code smells

- **Megaclass stores:** `postgres/runtime_api_store.py` bundles persistence, queue, events — navigability and dead-branch risk increase over time.
- **Dead kwargs:** Unused `migrate` on sync factory misleads readers into expecting automatic migrations.

## Follow-ups

- When touching factory API, align docstring with actual migration responsibilities (`async_from_settings` callers vs `from_settings`).

## Deep scan (Vulture min 50)

**Raw lines (this subtree):** 6 · See [SUPPLEMENT-deep-scan-vulture50.md](./SUPPLEMENT-deep-scan-vulture50.md).

### Notes

- **`in_memory/share_snapshot_store.py`** — `InMemoryShareSnapshotStore` flagged unused class — **tests import** it; `src/` scan does not see test references.
- **`share_snapshot_store`** vs factory wiring — confirm production uses snapshot port where fork/share features enabled.
