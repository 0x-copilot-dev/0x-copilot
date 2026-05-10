# Refactor PRD — Async-only ports (Phase E)

**Status:** Draft
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §1.2](../architecture/refactor-audit.md#12-sync-ports--async-ports--async_wrappers-3-layers-for-1)
**Plan reference (existing):** `hazy-kindling-minsky` — referenced in [`async_ports.py`](../../src/agent_runtime/api/async_ports.py) docstring; this refactor is **Phase E** of that plan, the final retirement step.

---

## 1. Problem

The runtime persistence layer carries three coexisting Protocol families plus a runtime bridge:

1. **Sync ports** — [`agent_runtime/api/ports.py`](../../src/agent_runtime/api/ports.py) (389 LOC). Defines `PersistencePort`, `EventStorePort`, `RuntimeQueuePort` as `@runtime_checkable` Protocols with `def` (sync) methods.
2. **Async ports** — [`agent_runtime/api/async_ports.py`](../../src/agent_runtime/api/async_ports.py) (763 LOC). Defines `AsyncPersistencePort`, `AsyncEventStorePort`, `AsyncRuntimeQueuePort` as `@runtime_checkable` Protocols with `async def` methods. **Already a superset of the sync surface** — adds ~25 methods covering usage (B1, B2), pricing (B3), rollups (B4, PR 7.2), budgets (B7, B8), retention sweep (C8), audit export (C9).
3. **Bridge** — [`runtime_adapters/async_wrappers.py`](../../src/runtime_adapters/async_wrappers.py) (638 LOC). `SyncToAsyncPersistence` / `SyncToAsyncEventStore` / `SyncToAsyncQueue` wrap sync ports via `asyncio.to_thread`. Three `adapt_*_to_async()` factory functions probe whether a port is sync or async via `inspect.iscoroutinefunction` and pass through if already async.
4. **Async-shim adapter** — `AsyncInMemoryRuntimeApiStore` ([`runtime_adapters/in_memory/async_runtime_api_store.py`](../../src/runtime_adapters/in_memory/async_runtime_api_store.py)) is a thin `async def` wrapper over `InMemoryRuntimeApiStore` that immediately calls the sync method without yielding. Architecture-doc admission: _"thin async wrapper, no real awaits."_

The drift is already costly:

- The async port has methods the sync port does not. The bridge papers over this with `# type: ignore[no-untyped-def]` and untyped `**kwargs` (see [`async_wrappers.py:385-533`](../../src/runtime_adapters/async_wrappers.py#L385-L533)) — the bridge calls `self._port.record_run_usage(record)` which works only because `runtime_checkable` Protocol matching is permissive. **A typed Protocol mismatch is being suppressed at every call site.**
- Every new persistence operation must be defined three times: sync Protocol, async Protocol, bridge wrapper. Drift between them is silent.
- Tests construct in-memory sync stores inline (38 files; no shared fixture), then optionally wrap them in `AsyncInMemoryRuntimeApiStore` to test async consumers. There is no canonical async-native fake.
- The wrappers file itself states the intent: _"Once Phase E retires the sync adapter, these wrappers go away with it."_ This PRD is Phase E.

### Symptoms (today)

- ~1,400 LOC across the three Protocol layers + bridge for what should be one async Protocol family.
- Type checker disabled for ~150 LOC of the bridge.
- Test setup fragmented — async tests pay a sync→async wrap that production never pays.
- New developers ask "which Protocol do I extend?" and the answer is "depends where you are."

### What this is NOT

- Not a database / schema change.
- Not a switch from `psycopg` to anything else.
- Not a behavior change. The full-system refactor audit ([refactor-audit.md](../architecture/refactor-audit.md)) lists nine other refactors with behavior implications; this one is purely a layer collapse.
- Not a change to the wider [`persistence/ports.py`](../../src/agent_runtime/persistence/ports.py) surface (DraftStorePort, ShareStorePort, CitationStorePort, etc.) — those are already async-native single-Protocol ports. Out of scope.

---

## 2. Goal and non-goals

### Goal

Collapse to **one async Protocol family** for `PersistencePort`, `EventStorePort`, `RuntimeQueuePort`. Delete the sync Protocols, the bridge file, and the no-op async-shim adapter. Make the in-memory adapter async-native. Preserve every behavior and every test currently passing.

### Non-goals

- Reduce the surface of `PersistencePort`. (The 60+ method spread is a separate refactor — see [refactor-audit §2.1](../architecture/refactor-audit.md#21-9-persistence-ports--17-record-types).)
- Switch the test framework, fixture style, or mocking strategy beyond what the refactor strictly requires.
- Touch `persistence/ports.py` (Draft / Share / Citation / etc.). Those are already async-native.
- Add Postgres real-instance integration tests. The current test pattern (in-memory fakes + a small set of Postgres adapter tests) stays.

### Success criteria

- `agent_runtime/api/ports.py` deleted.
- `runtime_adapters/async_wrappers.py` deleted.
- `runtime_adapters/in_memory/async_runtime_api_store.py` deleted.
- `agent_runtime/api/async_ports.py` renamed to `agent_runtime/api/ports.py` and Protocol classes renamed (`AsyncPersistencePort` → `PersistencePort`, etc.).
- `RuntimePorts` dataclass and `RuntimeAdapterFactory.from_settings` deleted; only `AsyncRuntimePorts` and `async_from_settings` remain. (Or: rename `AsyncRuntimePorts` → `RuntimePorts`, `async_from_settings` → `from_settings`.)
- `InMemoryRuntimeApiStore` rewritten with `async def` methods.
- All 38 test files using sync stores updated to construct the async-native in-memory store directly. `pytest-asyncio` marks added where missing.
- Full test suite passes (`make test` and per-service `pytest`) with no skipped or xfailed tests that previously passed.
- No `# type: ignore` comments in the new ports surface.
- A representative latency benchmark before/after shows no regression in: SSE event delivery, run-create p99, approval-resolve p99.

---

## 3. Systems touched

Inventory derived from grep on the entire `services/ai-backend/` tree.

### 3.1 Files deleted

| File                                                                                                                       | LOC | Reason                                                                    |
| -------------------------------------------------------------------------------------------------------------------------- | --- | ------------------------------------------------------------------------- |
| [`agent_runtime/api/ports.py`](../../src/agent_runtime/api/ports.py)                                                       | 389 | Sync Protocol family — superseded by async                                |
| [`runtime_adapters/async_wrappers.py`](../../src/runtime_adapters/async_wrappers.py)                                       | 638 | Bridge — no consumers once sync ports are gone                            |
| [`runtime_adapters/in_memory/async_runtime_api_store.py`](../../src/runtime_adapters/in_memory/async_runtime_api_store.py) | TBD | "Thin wrapper, no real awaits" — replaced by async-native in-memory store |

### 3.2 Files renamed

| From                                        | To                                    | Notes                                |
| ------------------------------------------- | ------------------------------------- | ------------------------------------ |
| `agent_runtime/api/async_ports.py`          | `agent_runtime/api/ports.py`          | Class names also drop `Async` prefix |
| `RuntimeAdapterFactory.async_from_settings` | `RuntimeAdapterFactory.from_settings` | Old `from_settings` deleted          |
| `AsyncRuntimePorts` dataclass               | `RuntimePorts`                        | Old `RuntimePorts` deleted           |

### 3.3 Files modified — production consumers

**API service layer (6 files):**

- [`agent_runtime/api/service.py`](../../src/agent_runtime/api/service.py) — `RuntimeApiService` constructor; drop sync-port type union.
- [`agent_runtime/api/events.py`](../../src/agent_runtime/api/events.py) — `RuntimeEventProducer` drops the `adapt_*_to_async` normalization at construction time.
- [`agent_runtime/api/share_service.py`](../../src/agent_runtime/api/share_service.py)
- [`agent_runtime/api/conversation_fork.py`](../../src/agent_runtime/api/conversation_fork.py)
- [`agent_runtime/api/self_fork.py`](../../src/agent_runtime/api/self_fork.py)
- [`agent_runtime/api/workspace_defaults_service.py`](../../src/agent_runtime/api/workspace_defaults_service.py)

**Worker (5 files):**

- [`runtime_worker/loop.py`](../../src/runtime_worker/loop.py) — drop the `adapt_*_to_async()` calls at constructor; type the ports as async-only.
- [`runtime_worker/handlers/run.py`](../../src/runtime_worker/handlers/run.py)
- [`runtime_worker/handlers/cancel.py`](../../src/runtime_worker/handlers/cancel.py)
- [`runtime_worker/handlers/approval.py`](../../src/runtime_worker/handlers/approval.py)
- [`runtime_worker/streaming_executor.py`](../../src/runtime_worker/streaming_executor.py)

**Worker background jobs (4 files):**

- [`runtime_worker/usage_rollup_loop.py`](../../src/runtime_worker/usage_rollup_loop.py)
- [`runtime_worker/jobs/retention_sweeper.py`](../../src/runtime_worker/jobs/retention_sweeper.py)
- [`runtime_worker/jobs/approval_expiry_sweeper.py`](../../src/runtime_worker/jobs/approval_expiry_sweeper.py)
- [`runtime_worker/audit.py`](../../src/runtime_worker/audit.py)

**Domain / context / observability (3 files):**

- [`agent_runtime/context/memory/subagent_trace.py`](../../src/agent_runtime/context/memory/subagent_trace.py)
- [`agent_runtime/observability/usage_attribution.py`](../../src/agent_runtime/observability/usage_attribution.py)
- [`runtime_worker/tool_observations.py`](../../src/runtime_worker/tool_observations.py) — currently imports the sync `EventStorePort`.

**Adapter factory + entrypoints (3 files):**

- [`runtime_adapters/factory.py`](../../src/runtime_adapters/factory.py) — delete sync branch, rename async branch to default.
- [`runtime_api/app.py`](../../src/runtime_api/app.py) — `RuntimeApiAppFactory.default_service` currently has a fallback to `from_settings` for the sync `in_memory` backend. Delete that branch.
- [`runtime_worker/__main__.py`](../../src/runtime_worker/__main__.py) — `RuntimeWorkerEntrypoint.amain` similarly. Delete sync fallback.

### 3.4 Files modified — in-memory adapter

- `runtime_adapters/in_memory/runtime_api_store.py` — Rewrite all methods as `async def`. Internal storage stays the same (process-local dict / list); methods do not need to `await` anything but the signatures change.

### 3.5 Files modified — tests (38 files using sync stores)

**Heaviest usage:**

- `tests/unit/runtime_worker/test_runtime_worker.py` (26 constructions)
- `tests/unit/agent_runtime/persistence/test_drafts.py` (17)
- `tests/unit/runtime_api/test_approval_forwarding_hardening.py` (15)
- `tests/unit/runtime_api/test_approval_undo.py` (11)
- `tests/unit/runtime_worker/test_usage_rollup_loop.py` (9)
- `tests/unit/runtime_worker/test_run_usage.py` (8)
- `tests/unit/runtime_api/test_approval_forwarding.py` (8)
- `tests/unit/agent_runtime/capabilities/test_draft_backend.py` (8)

**Other consumers (1–5 constructions each):** approval routes, runtime API routes, budgets, worker audit, citations, streaming executor isolation, conversation fork, self-fork, etc.

**Pattern change required per test:**

- Replace inline `InMemoryRuntimeApiStore()` with the async-native equivalent.
- Add `@pytest.mark.asyncio` where the test function is now `async`.
- Replace `store.create_conversation(...)` with `await store.create_conversation(...)`.
- Drop any `AsyncInMemoryRuntimeApiStore(InMemoryRuntimeApiStore())` wrapping in the 7 test files using that pattern.

### 3.6 Footprint summary

- **Deletions:** 3 files (~1,200 LOC plus the in-memory async shim).
- **Renames:** 3 (file + 3 Protocol classes + 1 dataclass + 1 factory method).
- **Modifications, production:** 21 files.
- **Modifications, tests:** 38 files (mostly mechanical — add `await` and `@pytest.mark.asyncio`).

Total: ~62 files touched. **No public HTTP contracts change. No DB schema changes. No event format changes.**

---

## 4. Functionalities served (port surface inventory)

Every method on the current async ports must keep its contract. This section is the canonical list — **no method on this list may regress in behavior**. Method names are taken from [`async_ports.py`](../../src/agent_runtime/api/async_ports.py).

### 4.1 `AsyncPersistencePort` (60 methods)

**Conversations / messages / runs (lifecycle):**

- `create_conversation`, `get_conversation`, `get_conversation_for_org`, `list_conversations`, `list_messages`, `append_message`, `insert_forked_conversation`, `update_conversation_connectors`, `update_conversation`, `soft_delete_conversation`, `restore_conversation`
- `create_run_with_user_message`, `get_run`, `get_active_run_for_conversation`, `update_run_status`, `set_run_latest_sequence`

**Approvals (PR 1.4 / 1.4.1):**

- `record_approval_decision`, `create_approval_request`, `forward_approval_request`, `get_approval_request`, `list_assigned_approvals`, `list_pending_expired_approvals`, `list_pending_approvals_for_membership_audit`

**Audit (PR 7.1, C9):**

- `write_audit_log`, `list_audit_log_events`, `list_audit_log_for_export`

**History deletion (compliance):**

- `delete_user_history`

**Workspace defaults (PR 1.6):**

- `get_workspace_defaults`, `upsert_workspace_defaults`

**Usage / pricing (B1, B2, B3, B4, PR 7.2):**

- `record_run_usage`, `record_model_call_usage`, `update_run_usage_cost`, `update_model_call_usage_cost`
- `upsert_pricing`, `lookup_pricing`, `list_runs_missing_cost`
- `upsert_user_daily_usage`, `upsert_org_daily_usage`, `upsert_connector_daily_usage`
- `query_user_daily_usage`, `query_org_daily_usage`, `query_connector_daily_usage`
- `query_model_call_usage_for_range`, `query_run_usage`, `query_run_usage_for_range`
- `query_top_conversations`, `query_model_call_usage_for_run`, `query_latest_run_usage_for_conversation`
- `query_compression_events_for_run`, `query_last_completed_tool_connector_slug`

**Budgets (B7, B8):**

- `lookup_budgets_for_run`, `charge_budget`, `reserve_budget`, `consume_budget_reservation`, `reap_expired_budget_reservations`
- `list_budgets`, `list_tool_budgets_for_org`, `get_budget`, `create_budget`, `update_budget`, `delete_budget`

**Retention (C8):**

- `list_retention_orgs`, `list_retention_policies`, `upsert_retention_policy`, `delete_retention_policy`, `sweep_retention_kind`

### 4.2 `AsyncEventStorePort` (3 methods)

- `append_event` — monotonic per-run sequence_no allocation. Implementations MUST serialize concurrent appends per run_id.
- `list_events_after` — replay after a sequence cursor.
- `get_latest_sequence` — return latest persisted sequence_no for a run.

### 4.3 `AsyncRuntimeQueuePort` (7 methods)

- `enqueue_run`, `enqueue_cancel`, `enqueue_approval_resolved` — three command types.
- `claim_next` — worker claim with `lock_expires_at`.
- `mark_complete`, `mark_retry`, `mark_dead_letter` — three terminal transitions.

### 4.4 Methods present only on async, not sync

Detected from cross-reading [`ports.py`](../../src/agent_runtime/api/ports.py) vs [`async_ports.py`](../../src/agent_runtime/api/async_ports.py):

`record_run_usage`, `record_model_call_usage`, `update_run_usage_cost`, `update_model_call_usage_cost`, `upsert_pricing`, `lookup_pricing`, `list_runs_missing_cost`, `upsert_user_daily_usage`, `upsert_org_daily_usage`, `upsert_connector_daily_usage`, `query_user_daily_usage`, `query_org_daily_usage`, `query_connector_daily_usage`, `query_model_call_usage_for_range`, `query_run_usage`, `query_run_usage_for_range`, `query_top_conversations`, `query_model_call_usage_for_run`, `query_latest_run_usage_for_conversation`, `query_compression_events_for_run`, `query_last_completed_tool_connector_slug`, `lookup_budgets_for_run`, `charge_budget`, `reserve_budget`, `consume_budget_reservation`, `reap_expired_budget_reservations`, `list_budgets`, `list_tool_budgets_for_org`, `get_budget`, `create_budget`, `update_budget`, `delete_budget`, `list_retention_orgs`, `list_retention_policies`, `upsert_retention_policy`, `delete_retention_policy`, `sweep_retention_kind`, `list_audit_log_events`, `list_audit_log_for_export`.

The bridge calls these via `self._port.<method>(...)` with `# type: ignore[no-untyped-def]` — i.e. the sync `InMemoryRuntimeApiStore` MUST have implemented them too (otherwise tests would AttributeError). **Implication:** these methods already exist on the sync in-memory store; the sync Protocol simply doesn't declare them. The sync Protocol is a stale subset, and the test fakes have been quietly carrying the async surface all along. This makes the refactor _easier_: the in-memory store already has every method we need; we only need to flip the signatures.

---

## 5. User flows covered

Every flow in [docs/architecture/](../architecture/) — single-turn, multi-turn-tool, SSE resume, cancel, citations, thinking, MCP add, MCP auth, usage metrics — touches one or more of these ports. The flows below name the touch points to make regression-test coverage explicit.

### Flow 1 — Single-turn message ([f1](../architecture/f1-single-turn.puml))

- API: `create_run_with_user_message`, `enqueue_run`, `append_event` (RUN_QUEUED).
- Worker: `claim_next`, `append_event` (RUN_STARTED, MODEL_DELTA × N, FINAL_RESPONSE, RUN_COMPLETED), `set_run_latest_sequence`, `mark_complete`.
- SSE: `list_events_after` per replay tick.

### Flow 2 — Multi-turn tool ([f2](../architecture/f2-multi-turn-tool.puml))

- All of Flow 1, plus: `append_event` (TOOL_CALL, TOOL_RESULT, PRESENTATION_UPDATED), `record_run_usage`, `record_model_call_usage`, `charge_budget`.

### Flow 3 — SSE resume ([f3](../architecture/f3-sse-resume.puml))

- `list_events_after(after_sequence=N)` repeatedly.
- `get_latest_sequence` for terminal-status checks.

### Flow 4 — Run cancellation ([f4](../architecture/f4-cancel.puml))

- API: `enqueue_cancel`, `append_event` (RUN_CANCELLING).
- Worker (cancel handler): `claim_next`, `update_run_status(CANCELLED)`, `append_event` (RUN_CANCELLED), `mark_complete`.
- Worker (active run handler): `get_run` (cooperative status check on next loop tick).

### Flow 5 — Citations ([f5](../architecture/f5-citations.puml))

- Same persistence path as Flow 2; citations themselves go to `CitationStorePort` + `ConversationToolOrdinalStorePort` (out of scope — already async-native).
- `append_event` (SOURCE_INGESTED, CITATION_MADE).

### Flow 6 — Reasoning / thinking ([f6](../architecture/f6-thinking.puml))

- Same as Flow 2, plus: `append_event` (REASONING_SUMMARY_DELTA, REASONING_SUMMARY, MODEL_CALL_STARTED, MODEL_CALL_COMPLETED).
- Pricing: `lookup_pricing` for the run's effective row at write time.

### Flow 7 — MCP add ([f7](../architecture/f7-mcp-add.puml))

- No direct port touch in ai-backend (registry lives in backend). Subsequent run start re-queries via `MembershipResolver` (HTTP) — out of scope.

### Flow 8 — MCP auth in-chat ([f8](../architecture/f8-mcp-auth.puml))

- API: `create_approval_request`, `append_event` (MCP_AUTH_REQUIRED), `update_run_status(AWAITING_APPROVAL)`, `enqueue_approval_resolved` (on user click).
- Worker (approval handler): `claim_next`, `get_approval_request`, `record_approval_decision`, `append_event` (APPROVAL_RESOLVED), `update_run_status(RUNNING)`, `mark_complete`.

### Flow 9 — Usage / token metrics ([f9](../architecture/f9-usage-metrics.puml))

- `query_latest_run_usage_for_conversation`, `query_compression_events_for_run`, `query_model_call_usage_for_run`, `lookup_pricing` — for the in-chat `/context` slash.
- `query_user_daily_usage`, `query_org_daily_usage`, `query_connector_daily_usage` — for the Usage page.
- `query_run_usage_for_range`, `query_model_call_usage_for_range` — for the rollup loop's recompute window.
- `upsert_user_daily_usage`, `upsert_org_daily_usage`, `upsert_connector_daily_usage` — rollup writes.

### Background loops

- **Approval expiry sweeper:** `list_pending_expired_approvals`, `list_pending_approvals_for_membership_audit`, `enqueue_approval_resolved` (synthetic rejection).
- **Retention sweeper:** `list_retention_orgs`, `list_retention_policies`, `sweep_retention_kind`.
- **Usage rollup loop:** `query_run_usage_for_range`, `query_model_call_usage_for_range`, plus the daily upserts above.
- **Budget reservation reaper:** `reap_expired_budget_reservations`.
- **Audit export pump (C9):** `list_audit_log_for_export`.

**No flow above changes shape under this refactor.** Every method called stays callable, with the same parameters and return type. Only the call site adds `await` and the dispatch goes directly to the async method instead of through the bridge's `to_thread`.

---

## 6. Behaviors that must be preserved (regression contract)

Direct extracts from the async port docstrings and the architecture flow diagrams. Each item is a test invariant.

### Persistence semantics

- **`create_conversation` is idempotent** (returns existing row on retry).
- **`create_run_with_user_message`** is idempotent (returns existing run on retry; the boolean return signals "freshly created").
- **`set_run_latest_sequence` is monotonic** — a write with a lower value than the currently persisted value is a no-op (never rewinds).
- **`forward_approval_request`** runs parent-update + child-insert in one transaction.
- **`update_conversation_connectors`** is RFC 7396 merge-patch semantics: keys present overwrite (including `None` to pause); keys absent are left untouched.
- **`upsert_pricing`** closes the previous active row by setting `effective_until` when superseded.
- **`record_run_usage`** is idempotent on `run_id`.
- **`record_model_call_usage`** is idempotent on the row's UUID id.
- **`charge_budget`** is idempotent on `(budget_id, run_id)` via CAS on `row_version` AND `last_charged_run_id`. Returns `ChargeOutcome.IDEMPOTENT_NOOP` / `APPLIED` / `EXHAUSTED_RETRIES`.
- **`reserve_budget`** is idempotent on `(budget_id, run_id)` (returns `None` on retry path).
- **`get_conversation_for_org`** ignores user ownership — admin-override path.
- **`list_conversations(include_deleted=False)`** filters by `deleted_at IS NULL`.

### Event store semantics

- **`append_event`** serializes concurrent appends per `run_id`; returned `sequence_no` is monotonically increasing without gaps. Implementation today uses `SELECT … FOR UPDATE` on `agent_runs` plus `UNIQUE(run_id, sequence_no)`.
- **`list_events_after`** returns events with `sequence_no > after_sequence` in order.
- **`get_latest_sequence`** never returns a sequence higher than the latest committed event.

### Queue semantics

- **`claim_next`** is exclusive — a claim cannot be returned to two workers within the lock_expires_at window. Implementation today uses Postgres `SELECT … FOR UPDATE SKIP LOCKED` semantics.
- **`enqueue_*`** is durable — once the call returns, the command will be claimed by some worker (modulo cancel / dead-letter).
- **Three command types only**: `RUN_REQUESTED`, `RUN_CANCEL_REQUESTED`, `APPROVAL_RESOLVED`. Anything else is a `VALIDATION_ERROR`.

### Cross-cutting

- **Postgres `application_name` tagging**: the pool sets `application_name = "ai-backend-{role}"` so `pg_stat_activity` is greppable by API vs worker. `RuntimeAdapterFactory.from_settings` (the new one) must keep the `role: str = "api"` parameter and pass it to the store.
- **In-memory adapters are tenant-isolated**: cross-org access returns `None`, never raises.
- **Audit log immutability**: `write_audit_log` only appends; `list_audit_log_for_export` returns rows ordered by `(created_at, id)` ascending so the SIEM cursor is monotonic.

### Test invariants (existing)

Every test currently passing must keep passing. No test may be deleted that asserts a behavior above.

---

## 7. Refactor plan

Six PRs. Each lands independently, with green CI, before the next starts. No item is reversible-in-place: there is no toggle / feature flag — sync ports are dev-only and the team has already decided to retire them. But each PR is bounded so a regression rolls back cleanly.

### PR 1 — Add the missing methods to the sync `Protocol` (preparation, not a behavior change)

Today's bridge calls many methods on the sync port that the sync Protocol does not declare (the `# type: ignore[no-untyped-def]` block in `async_wrappers.py:385-533`). The implementations exist on `InMemoryRuntimeApiStore`; only the Protocol declaration is stale. **Add the missing method declarations to `agent_runtime/api/ports.py`** so the sync Protocol matches reality. This:

- Removes the `# type: ignore` comments from the bridge.
- Surfaces any actual mismatches (run mypy / pyright after the change).
- Costs nothing at runtime.
- Lets PR 2+ proceed with confidence that the sync surface is fully described.

**Risk:** Low. No runtime behavior change.

### PR 2 — Make `InMemoryRuntimeApiStore` async-native

Rewrite [`runtime_adapters/in_memory/runtime_api_store.py`](../../src/runtime_adapters/in_memory/runtime_api_store.py) so every method is `async def`. The body of each method does not need to `await` anything (process-local dict / list operations are atomic), but the signatures change. **Keep the file at the same path; keep the class name `InMemoryRuntimeApiStore`.** Provide a deprecation shim only if PR 3+ rolls out gradually (probably not needed).

Update the four to seven test files that use `AsyncInMemoryRuntimeApiStore(InMemoryRuntimeApiStore())` to construct `InMemoryRuntimeApiStore()` directly and `await` its methods.

**Risk:** Medium — touches many tests (~38 files), but mechanical.

### PR 3 — Delete `AsyncInMemoryRuntimeApiStore`

After PR 2, the wrapper `AsyncInMemoryRuntimeApiStore` ([`async_runtime_api_store.py`](../../src/runtime_adapters/in_memory/async_runtime_api_store.py)) has no purpose. Update `factory.async_from_settings`'s `in_memory_async` branch to instantiate the now-async `InMemoryRuntimeApiStore` directly. Delete the wrapper file.

**Risk:** Low.

### PR 4 — Convert all sync-port consumers to async-port type hints + drop the bridge

For each of the ~21 production files in [§3.3](#33-files-modified--production-consumers):

- Change type hints from `PersistencePort | AsyncPersistencePort` (or `PersistencePort`) to `AsyncPersistencePort`.
- Remove the `adapt_*_to_async()` calls in constructors — the port is async natively.
- Add `await` to every call site.

This is the biggest mechanical PR. Recommend splitting into "API process" + "worker process" for review.

**Risk:** Medium. Wide but mechanical. CI catches missing `await`s as `coroutine was never awaited` warnings → fail the suite.

### PR 5 — Delete the bridge and the sync factory

After PR 4, [`async_wrappers.py`](../../src/runtime_adapters/async_wrappers.py) has no consumers. Delete the file. Delete `RuntimeAdapterFactory.from_settings`. Delete the `RuntimePorts` dataclass. Delete the sync-fallback branches in [`runtime_api/app.py`](../../src/runtime_api/app.py) and [`runtime_worker/__main__.py`](../../src/runtime_worker/__main__.py).

**Risk:** Low. Pure deletion.

### PR 6 — Rename async ports + factory to drop the `Async` prefix

- Move `agent_runtime/api/async_ports.py` → `agent_runtime/api/ports.py` (overwriting the now-empty old file from the deletion).
- Rename `AsyncPersistencePort` → `PersistencePort`, etc.
- Rename `AsyncRuntimePorts` → `RuntimePorts`.
- Rename `RuntimeAdapterFactory.async_from_settings` → `from_settings`.
- Update every import.

After this PR there is one Protocol family, with the natural names, and no lingering "async" qualifier in the codebase.

**Risk:** Low. Mechanical rename; modern IDE refactor handles ~95% of it.

### Total

6 PRs. Estimated wall clock: **~1 person-week** for an engineer who knows the codebase. **~2 person-weeks** for someone who doesn't.

---

## 8. Why this refactor (justification)

### Direct savings

- **~1,200 LOC deleted** outright (sync ports + bridge + async-shim). Plus another ~200 LOC of `# type: ignore` and union-type plumbing across consumers.
- **One Protocol family to maintain instead of three.** Every new persistence operation = one method declaration, one implementation, one fake update.
- **Type checker re-enabled across the bridge surface** (~150 LOC of `# type: ignore[no-untyped-def]` deleted).

### Indirect / structural benefits

- **Eliminates a known drift hazard.** The async port today carries ~25 methods the sync port does not. Either drift (a) silently grows when developers add to async only and forget sync, or (b) silently disappears when sync gets a stale subset that the bridge papers over. Both are happening.
- **Removes the "which Protocol do I extend?" question** for new contributors. The CLAUDE.md rule "Use Pydantic at every IO/domain boundary" is harder to apply when the boundary itself is split.
- **Sets up [refactor-audit §2.1 (port consolidation)](../architecture/refactor-audit.md#21-9-persistence-ports--17-record-types).** That refactor — collapsing 9 persistence ports into ~4 topical repositories — is much harder to do across a sync/async split. With one Protocol family, the next refactor becomes mechanical.
- **Removes the per-call thread-pool overhead in the bridge path.** In production this is moot (Postgres adapter is async-native), but the `to_thread` overhead in dev / tests using `in_memory_async` disappears. Tests run measurably faster.

### What this refactor does NOT buy

- **No production latency improvement.** Production already uses the async-native Postgres adapter; the bridge is bypassed via `_is_async_port` detection.
- **No reduction in `RuntimeApiService`'s 2.4k LOC.** That's [refactor-audit §2.7](../architecture/refactor-audit.md#27-runtimeapiservice-at-24k-loc), separate effort.
- **No new functionality.** Pure layer collapse.

### Why now

Three reasons:

1. **The team already planned this** — the wrappers file's docstring names this as Phase E, and [`async_ports.py`](../../src/agent_runtime/api/async_ports.py)'s docstring references the `hazy-kindling-minsky` plan. The refactor was _anticipated_ when the bridge was written; this is finishing what's started.
2. **Drift is accelerating.** Every new feature (B7 budgets, B8 tool budgets, C9 audit export, PR 7.2 connector attribution) added methods to async-only and lengthened the sync→async gap. Each addition makes the eventual cleanup more painful.
3. **It unlocks the next refactor.** The port consolidation in [refactor-audit §2.1](../architecture/refactor-audit.md#21-9-persistence-ports--17-record-types) is the structural payoff; collapsing the sync/async split first makes that work mechanical instead of multi-dimensional.

---

## 9. Risks and mitigations

| Risk                                                                                                                 | Likelihood | Impact                  | Mitigation                                                                                                                                                       |
| -------------------------------------------------------------------------------------------------------------------- | ---------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Missing `await` in a converted call site → silent coroutine drop                                                     | Medium     | High (silent data loss) | Run with `PYTHONDEVMODE=1` and `python -W error::RuntimeWarning` in CI to make "coroutine was never awaited" fatal. Already a pytest plugin pattern.             |
| Test fixture explosion: tests that share a sync store across helpers need them all converted at once                 | Medium     | Medium                  | Convert one test file at a time within PR 4; CI catches per-file failures.                                                                                       |
| Postgres adapter currently relies on `application_name="ai-backend-{role}"` for `pg_stat_activity`                   | Low        | Medium                  | Preserved explicitly in §6 invariants; verified by an existing test (assumed; verify).                                                                           |
| `runtime_checkable` Protocol isinstance checks return True for sync stores even after the rename, masking bugs       | Medium     | Low                     | Don't add new isinstance checks; use type hints only. The bridge's `_is_async_port` probe (which used `inspect.iscoroutinefunction`) is deleted with the bridge. |
| External consumers importing `PersistencePort` from `agent_runtime.api.ports` (sync) will break at the rename        | Low        | Low                     | This is an internal-only Protocol; no external consumer per repo grep. CI catches any stragglers.                                                                |
| Test count (38 files using sync stores) understated — there may be more in `tests/integration/` or `tests/contract/` | Low        | Medium                  | PR 1 should grep the whole tree before starting.                                                                                                                 |
| Behavior drift introduced during the `# type: ignore` removal in PR 1                                                | Low        | Medium                  | PR 1 ships first as preparation; CI must stay green before PR 2 starts.                                                                                          |

### Rollback plan

Each PR is independently revertable. Worst case rollback is to PR 4 (the bulk consumer change); reverting it restores the bridge with no other PRs needing to revert.

---

## 10. Open questions

1. **Are there integration tests under `tests/integration/` that hit a real Postgres?** The async-port refactor leaves the Postgres adapter untouched, but if integration tests construct sync ports, they need conversion too. _(Verify by `grep -r "InMemoryRuntimeApiStore" services/ai-backend/tests/`.)_
2. **Does the test suite actually catch missing-await regressions today?** Confirm pytest is configured to fail on `RuntimeWarning: coroutine was never awaited`. If not, add it as part of PR 1.
3. **Is `RuntimeAdapterFactory.from_settings` referenced anywhere outside the ai-backend tree?** The shared `packages/service-contracts` package shouldn't import it (per workspace rules), but verify.
4. **The deprecation shim question for PR 2:** is anyone constructing `InMemoryRuntimeApiStore` from outside `services/ai-backend/`? Per [root CLAUDE.md](../../../../CLAUDE.md) service-boundary rules, no — but verify with `grep -r "from runtime_adapters" packages/`.
5. **Should PR 6 happen at all?** Renaming `AsyncFoo` → `Foo` is good hygiene but requires updating every import in the codebase. The alternative is to live with the `Async` prefix forever. Recommendation: do it, but it's the cheapest item to defer if calendar pressure forces a cut.

---

## 11. References

- [refactor-audit.md §1.2](../architecture/refactor-audit.md#12-sync-ports--async-ports--async_wrappers-3-layers-for-1) — the audit that motivated this PRD.
- [`agent_runtime/api/ports.py`](../../src/agent_runtime/api/ports.py) — sync Protocol family (to be deleted).
- [`agent_runtime/api/async_ports.py`](../../src/agent_runtime/api/async_ports.py) — async Protocol family (to be renamed to `ports.py`).
- [`runtime_adapters/async_wrappers.py`](../../src/runtime_adapters/async_wrappers.py) — bridge (to be deleted).
- [`runtime_adapters/factory.py`](../../src/runtime_adapters/factory.py) — adapter selection (to be simplified to async-only).
- Architecture flow diagrams [f1](../architecture/f1-single-turn.puml)–[f9](../architecture/f9-usage-metrics.puml) — coverage map per port surface.
- Plan reference: `hazy-kindling-minsky` (named in `async_ports.py` docstring; this PRD is the explicit Phase E retirement step).
