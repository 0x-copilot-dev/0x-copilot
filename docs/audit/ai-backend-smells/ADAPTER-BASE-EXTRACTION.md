# Adapter base extraction — implementation record + method-group ledger

**Companion to [ADAPTER-COLLAPSE-REALITY.md](ADAPTER-COLLAPSE-REALITY.md).** That
document argued the honest win is _not_ the specced "one SQL impl" collapse but a
**shared in-memory-view + domain-policy base that the `file` and `in_memory`
stores both extend** — `file` = `in_memory` + a JSONL persistence sidecar. This
document records the base being built and tracks the remaining method groups so
the extraction can continue "method-group by method-group" without re-deriving
the plan each time.

Postgres is untouched and deliberately does **not** extend the base: it keeps no
materialized view and round-trips genuinely different SQL (RLS fences, `ON
CONFLICT` retry loops). This is a `file`↔`in_memory` dedup only.

## The base

`runtime_adapters/_materialized_store.py` → `MaterializedViewStoreBase`. Both
dict-backed stores now extend it. It owns the shared domain policy; the two
backends differ only in the hooks it leaves open:

- `_state_guard()` — the serialization boundary around a back-office-state
  read/write. `in_memory` inherits the no-op default (`nullcontext()`, no lock,
  exactly as before); `file` overrides it to its shared `asyncio.Lock`
  (`_state_lock`).
- per-table `_persist_*(...)` hooks — the JSONL durability sidecar. `in_memory`
  inherits the no-op; `file` overrides each to append to the owning ledger.

A method group is "extracted" when its identical policy moves onto the base and
both stores delete their copies (keeping, for `file`, only the one-line persist
hook). The concrete `__init__`s stay the single source of truth for the dicts;
the base declares them as bare annotations only.

## Shipped (this PR)

| Port                            | Methods                                                                           | Shape                                                                                                                  |
| ------------------------------- | --------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `ContextOccupancyStorePort`     | `append_context_occupancy`, `list_context_occupancy`                              | append-only, idempotent on `(model_call_id, attempt_ordinal)`, off-critical-path (§6.4)                                |
| `UsageAttributionEdgeStorePort` | `append_usage_attribution_edge`, `list_usage_attribution_edges_for_usage_records` | append-only immutable relation, idempotent on the edge's natural identity, fails closed on a foreign/missing usage row |

Both are the same clean shape: idempotent append + tenant-scoped, deterministic
read; `file` adds one `_state_guard()` wrap and one `_persist_*` line. Chosen
first because they are append-only, off the run's critical path, and each has a
dedicated cross-backend parity test that pins the behaviour identically across
backends.

**Verification.** The existing cross-backend parity tests
(`test_context_occupancy_stores.py`, `test_usage_attribution_edge_stores.py`)
pass identically as the parity oracle. New file-store tests
(`test_materialized_store_base.py`) pin the two things `file` alone owns and that
a future "simplification" of the hooks could silently drop: the durability
_economy_ (a redelivered append writes no second ledger line) and the _lock
fence_ (append and read both wait on `_state_lock` — a mis-wire back to the no-op
guard passes every single-threaded test but fails these). Each new test was
mutation-probed: reverting the hook to the base no-op makes it fail.

## Remaining method groups — ledger

Measured from source (shared public method names on both stores, with per-backend
body LOC). "Same shape" = extractable exactly like the two shipped slices (one
`_state_guard` wrap + one `_persist_*`, no cross-row atomicity). "Needs a new
hook" = a concurrency/atomicity primitive the current two hooks do not model.

### Tier A — same shape, low risk (next PRs)

Keyed dict/list + single `_state_lock` + one ledger sidecar; append / upsert /
read only. Each has an existing per-backend test; extract one group per PR,
parity-verified.

| Group                  | Methods (examples)                                                                          | Notes                                                            |
| ---------------------- | ------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| pricing                | `upsert_pricing`, `lookup_pricing`                                                          | `pricing_rows` list + ledger; upsert closes the prior active row |
| daily-usage rollups    | `upsert_{user,org,connector,subagent,purpose}_daily_usage`, `query_*_daily_usage`           | keyed-dict upsert + ledger; the reads are pure filters           |
| model-call usage       | `record_model_call_usage`, `update_model_call_usage_cost`, `query_model_call_usage_*`       | `model_call_usage` list; one update-in-place                     |
| run usage              | `record_run_usage`, `query_run_usage`, `query_run_usage_for_range`, `update_run_usage_cost` | `run_usage` dict + ledger                                        |
| workspace defaults     | `get_workspace_defaults`, `upsert_workspace_defaults`                                       | one-row upsert                                                   |
| tool-invocation ledger | `record_tool_invocation`, `count_tool_invocations_for_runs`                                 | upsert by `invocation_id`                                        |

### Tier B — needs a new hook or careful design (own PR each, own heavy verification)

The current two hooks do not model these; each needs a designed primitive plus
crash-consistency / sequencing / lock-semantics tests (and, being desktop-default,
a live desktop-journeys pass).

| Group                           | What it needs beyond the two hooks                                                                                                                        |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| events                          | **per-run sequencing** — monotonic gapless `sequence_no`, `EventRedeliveryResolver`, `latest_sequence_no` cursor, batch atomicity                         |
| conversations / messages / runs | **per-conversation lock** (not `_state_lock`) + idempotency fingerprints + `updated_at` bumps + soft-delete semantics                                     |
| approval batches                | **per-batch atomic flip** (`record_item_decision_and_maybe_lock_batch` — exactly-once `PENDING→RESUMING`), `forward_approval_request` atomic parent→child |
| budgets                         | **CAS on `row_version`** + reservation lifecycle (`charge`/`reserve`/`consume`/`reap`)                                                                    |
| retention                       | per-kind sweep strategies, whole-collection ledger rewrites, cascades (`sweep_retention_kind`, `backfill`, `recompute`)                                   |
| legal holds                     | hold state **and** audit-chain append made visible together under one lock                                                                                |
| erasure                         | `delete_user_history`, `tombstone_artifacts_for_org_deletion` — multi-table cascades; the file store delegates to `SessionEraser` / artifact lifecycle    |
| queue (outbox)                  | `enqueue_*` / `claim_next` / `mark_*` — a distinct sub-store; extract as its own materialized-view base if pursued                                        |

### Not extractable (stay per-backend)

Lifecycle (`open` rebuilds the file store's view from disk; `in_memory` is a
no-op), and every file-only concern (`repair`, `export_import`, `_catalog_index`,
signed manifests, object-store GC). These are the local-first machinery
[ADAPTER-COLLAPSE-REALITY.md](ADAPTER-COLLAPSE-REALITY.md) §3 says consolidation
cannot delete.

## Rule for each future slice

1. One method group, parity-verified against its existing cross-backend test
   before moving on (the test is the oracle; behaviour must be byte-identical).
2. For Tier B, design the missing hook on the base first, add crash-consistency /
   sequencing / lock-semantics tests, and run the live desktop-journeys after a
   re-stage — this is a desktop-default persistence path.
3. Never fold a Tier B group in "because it's nearby." A regression in the
   shipping desktop store is the outage class this whole effort exists to shrink.
