# Cluster 04 — agent_runtime.delegation.subagents

**Last reviewed:** 2026-05-06 · **Revision:** `a1d79d7a61868a6a9ae774e3a46c875356b29b78`

## Cluster scope

Subagent definitions, contracts, runner, and handoff logic under [`src/agent_runtime/delegation/subagents/`](../../src/agent_runtime/delegation/subagents/).

## Entrypoints / wiring

- Deep Agents / graph tooling invokes the runner when delegating to child agents.
- Worker streaming maps subagent events for SSE (`runtime_worker/stream_subagents.py`).

## Likely unused or low-value symbols

Per-directory Vulture (`--min-confidence 80`) reported **no** unused functions/classes in this subtree at this revision.

## Test-only vs production

Delegation paths are covered heavily in unit tests; timeout/deadline edge cases depend on worker clock injection.

## Code smells

- **Contract surface:** Large Pydantic contracts (`contracts.py`) can carry fields that exist for forward compatibility — absence from Vulture does not prove every field is read; schema usage reviews belong with API/schema clusters when mirrored to HTTP.

## Follow-ups

- When adding new subagent tools, trace both **runner** and **stream projection** so half-wired features do not linger.

## Deep scan (Vulture min 50)

**Raw lines (this subtree):** 45 · See [SUPPLEMENT-deep-scan-vulture50.md](./SUPPLEMENT-deep-scan-vulture50.md).

### Notes

- **`delegation/subagents/constants.py`** — nested path/error code strings flagged line-by-line; overwhelmingly **false positives** for shared-key style.
- **`contracts.py`** — several `_normalize_*` private methods flagged unused — may be called only from validators / model rebuild paths Vulture misses; confirm before deletion.
- **`handoff.py`** — `build_task` reported unused method — verify runner actually invokes expected entrypoint.
