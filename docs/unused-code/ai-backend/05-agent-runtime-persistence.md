# Cluster 05 — agent_runtime.persistence

**Last reviewed:** 2026-05-06 · **Revision:** `a1d79d7a61868a6a9ae774e3a46c875356b29b78`

## Cluster scope

Persistence ports, records, schema/migrations helpers, encryption, and metrics under [`src/agent_runtime/persistence/`](../../src/agent_runtime/persistence/).

## Entrypoints / wiring

- [`runtime_adapters/factory.py`](../../src/runtime_adapters/factory.py) binds concrete stores implementing [`PersistencePort`](../../src/agent_runtime/persistence/ports.py) and related ports.
- Worker and API call into stores via `agent_runtime.api` async/sync facades.

## Likely unused or low-value symbols

| Location                      | Symbol / issue                                                        | Evidence                                                                               | Confidence           | Action                                                                                         |
| ----------------------------- | --------------------------------------------------------------------- | -------------------------------------------------------------------------------------- | -------------------- | ---------------------------------------------------------------------------------------------- |
| `persistence/ports.py`        | Many parameter names inside **Protocol / abstract** method signatures | Vulture lists dozens of “unused” names (`scope_id`, `payload_id`, share kwargs, etc.). | Low (false positive) | Treat as **intentional port surface**; adapters implement subsets.                             |
| `persistence/pool_metrics.py` | `options` unused in callbacks                                         | Vulture 100% on several lines.                                                         | Medium               | Inspect hooks — likely signature compatibility with psycopg; rename to `_options` or document. |

## Test-only vs production

In-memory store tests dominate unit coverage; Postgres adapter coverage is lower globally (see Cluster 10).

## Code smells

- **Port megatypes:** `ports.py` aggregates many concerns (events, queue, shares, drafts). Unused-parameter noise from scanners is a symptom of wide interfaces — splitting ports would shrink blast radius but is a large refactor.
- **Encryption / KMS paths:** [`_aws_kms_client.py`](../../src/agent_runtime/persistence/_aws_kms_client.py) may be thinly exercised unless CI enables KMS-style configs.

## Follow-ups

- Prefer `_`-prefixed params in **non-Protocol** internal helpers where names exist only for readability.
- Consider documenting which port methods are **postgres-only** vs **in-memory-only** to interpret coverage gaps.

## Deep scan (Vulture min 50)

**Raw lines (this subtree):** 140 · See [SUPPLEMENT-deep-scan-vulture50.md](./SUPPLEMENT-deep-scan-vulture50.md).

### Notes

- **`persistence/constants.py`** — extremely dense column/key registry; nearly every assignment triggers “unused variable” — **ignore without cross-file `rg`**.
- **`ports.py`** — continues to dominate raw line count (Protocol params).
- **`schema/migrate.py`** — `render_manifest` flagged unused — **false positive** (tests import it).
- **`pool_metrics.py`** — small `_observe_*` helpers flagged — verify actually registered as psycopg callbacks.
