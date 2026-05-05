# Decomp — `runtime_adapters/postgres/runtime_api_store.py`

Source: [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) — **2,344 LOC, XL.** The largest file in the codebase. Single class `PostgresRuntimeApiStore` plus four module-level helpers (`_Columns`, `_PoolEnv`, `_take_runtime_audit_chain_lock_async`, `_read_runtime_audit_chain_head_async`). Implements every persistence + event-store + queue port directly against `psycopg.AsyncConnection` + `psycopg_pool.AsyncConnectionPool`. Mirrors [in_memory/runtime_api_store.py](in-memory-runtime-api-store.md) **behaviorally**, but adds: connection pooling, RLS session vars, transactions, optimistic locking (C3), monotonic event sequence (H1), idempotent approval insert (H2), monotonic run cursor (H3), audit chain advisory lock, and SKIP LOCKED outbox claim.

## A. Top-level structure

### Module shell (lines 1–268)

| Symbol                                                  |   Lines | Purpose                                                                                                                                                                                      |
| ------------------------------------------------------- | ------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Module docstring                                        |    1–16 | Hazard-fix highlights: `FOR UPDATE` on `agent_runs` for event append (H1), `INSERT … ON CONFLICT (id) DO NOTHING` for approval idempotency (H2), monotonic `latest_sequence_no` UPDATE (H3). |
| Class `_Columns`                                        |  78–146 | **Column-name constant pool** (54 entries) for dict-row access. Mirrors the `_Fields` pool that `runtime_adapters/base.py` uses for payload keys.                                            |
| Class `_PoolEnv`                                        | 149–213 | Env-var keys + defaults for runtime DB pool tuning (C4 spec).                                                                                                                                |
| `_PoolEnv.env_int(name, default)`                       | 168–176 | Robust int parse with default fallback.                                                                                                                                                      |
| `_PoolEnv.env_float(name, default)`                     | 178–186 | Robust float parse with default fallback.                                                                                                                                                    |
| `_PoolEnv.build_pool_kwargs(*, role)`                   | 188–213 | Build psycopg pool `kwargs` with `dict_row` factory + `statement_timeout` + `lock_timeout` + `idle_in_transaction_session_timeout` + `application_name`.                                     |
| `_take_runtime_audit_chain_lock_async(conn, *, org_id)` | 216–238 | Compute SHA-256-derived int64 lock key → `pg_advisory_xact_lock(key)` to serialize per-org chain appends.                                                                                    |
| `_read_runtime_audit_chain_head_async(conn, *, org_id)` | 241–268 | Returns `(last_seq, last_signature)` for the org chain head; `(0, None)` for new chains. **Caller must hold the advisory lock.**                                                             |

### Class `PostgresRuntimeApiStore` (271–2344)

#### Pool + connection plumbing

| Symbol                                                                                                                             |   Lines | Purpose                                                                                                                                           |
| ---------------------------------------------------------------------------------------------------------------------------------- | ------: | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `__init__(database_url=None, *, pool=None, role="api", pool_min_size=None, pool_max_size=None, pool_acquire_timeout_seconds=None)` | 274–326 | Build or accept an `AsyncConnectionPool`; bind `PoolMetrics`; track ownership. Raises if neither `database_url` nor `pool` given.                 |
| `open()` / `close()` / `__aenter__` / `__aexit__`                                                                                  | 328–346 | Pool lifecycle (only when this store owns the pool).                                                                                              |
| `_tenant_connection(*, org_id=None, role=None)` (asynccontextmanager)                                                              | 348–381 | Acquire a pooled connection, stamp `app.current_org_id` and `app.role` GUCs (C5). Both vars `set_config(..., true)` so they're transaction-local. |
| `_role_connection(role)` (asynccontextmanager)                                                                                     | 383–399 | Acquire a connection without an org binding (cross-tenant operator paths: worker outbox claim, backfill jobs). Sets only `app.role`.              |
| `migrate()`                                                                                                                        | 401–423 | Delegate to `MigrationRunner.apply` on a worker thread; no-op when `RUNTIME_MIGRATIONS_AUTO_APPLY=false`.                                         |

#### Conversation CRUD

| Symbol                                                                  |   Lines | Purpose                                                                                |
| ----------------------------------------------------------------------- | ------: | -------------------------------------------------------------------------------------- |
| `create_conversation(request)`                                          | 425–475 | Idempotent on `(org, user, idempotency_key)`. SELECT-then-INSERT inside a transaction. |
| `get_conversation(*, org_id, user_id, conversation_id)`                 | 477–495 | Org+user-scoped lookup.                                                                |
| `list_conversations(*, org_id, user_id, limit, include_archived=False)` | 497–519 | Scoped list ordered by `updated_at DESC`.                                              |

#### Message CRUD

| Symbol                                                                    |   Lines | Purpose                                                      |
| ------------------------------------------------------------------------- | ------: | ------------------------------------------------------------ |
| `list_messages(*, org_id, conversation_id, limit, include_deleted=False)` | 521–543 | Ordered by `created_at ASC`.                                 |
| `append_message(message)`                                                 | 545–555 | Insert + bump conversation `updated_at`, in one transaction. |

#### Run lifecycle

| Symbol                                                   |   Lines | Purpose                                                                                                                                                                                                                                              |
| -------------------------------------------------------- | ------: | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `create_run_with_user_message(*, request, conversation)` | 557–689 | **The most complex method in the file** — 132 LOC. Idempotency check, message+run INSERT, conversation bump, all in one transaction. Uses three closure helpers (`_get_msg`, `_latest_msg_id`, `_latest_asst`) so the helpers see in-flight inserts. |
| `get_run(*, org_id, run_id)`                             | 691–700 | Org-scoped lookup.                                                                                                                                                                                                                                   |
| `update_run_status(*, run_id, status)`                   | 702–743 | **Optimistic-lock CAS (C3)**: SELECT `row_version`, UPDATE `WHERE id=? AND row_version=?`; raises `ConcurrentRunUpdateError` on miss.                                                                                                                |
| `set_run_latest_sequence(*, run_id, latest_sequence_no)` | 745–777 | **Monotonic cursor (H3)**: UPDATE `WHERE latest_sequence_no IS NULL OR latest_sequence_no < ?`. Out-of-order writes are no-ops. Falls back to SELECT to honor return contract on no-op.                                                              |

#### Approval CRUD

| Symbol                                         |   Lines | Purpose                                                                                                                                                            |
| ---------------------------------------------- | ------: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `record_approval_decision(*, record)`          | 779–804 | UPDATE the approval row's status + reason + decided_by + decided_at.                                                                                               |
| `create_approval_request(*, record)`           | 806–879 | **Atomic idempotent insert (H2)**: `INSERT … ON CONFLICT (id) DO NOTHING RETURNING id`; if no insert, fetch winner via JOIN to `agent_runs` for full record shape. |
| `get_approval_request(*, org_id, approval_id)` | 881–912 | Pending or resolved; JOINs `agent_runs` for `conversation_id` + `user_id`.                                                                                         |

#### Audit log (HMAC chain)

| Symbol                                   |   Lines | Purpose                                                                                                                                                      |
| ---------------------------------------- | ------: | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `write_audit_log(*, event_type, record)` | 914–988 | Hold advisory lock → read head → sign payload → INSERT row with `seq + 1` + chain fields. **All in one transaction**, advisory lock auto-released on commit. |

#### User-history deletion

| Symbol                                                 |    Lines | Purpose                                                                                                                                                                                                  |
| ------------------------------------------------------ | -------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `delete_user_history(*, org_id, user_id, reason=None)` | 990–1169 | Five-stage tombstone: legal-hold gate → archive convs → tombstone messages → cancel non-terminal runs → count retained events → write audit row (chain-locked) → write evidence row. Single transaction. |

#### Usage + pricing (B1, B2, B3, B4) — section fence at line 1171

| Symbol                                                                          |     Lines | Purpose                                                                                                                                                                |
| ------------------------------------------------------------------------------- | --------: | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `record_run_usage(record)`                                                      | 1175–1232 | **Idempotent INSERT (B1)**: `ON CONFLICT (run_id) DO NOTHING`.                                                                                                         |
| `record_model_call_usage(record)`                                               | 1234–1282 | Append-only INSERT (B2). Uniqueness by row id; caller dedupes.                                                                                                         |
| `update_run_usage_cost(*, run_id, cost_micro_usd, pricing_id, pricing_version)` | 1284–1302 | UPDATE cost stamp on usage row.                                                                                                                                        |
| `update_model_call_usage_cost(*, usage_id, …)`                                  | 1304–1322 | UPDATE cost stamp on per-call row.                                                                                                                                     |
| `upsert_pricing(record)`                                                        | 1324–1385 | **Window close + insert (B3)**: UPDATE prior active row's `effective_until`, then INSERT new active row. One transaction so readers never see zero or two active rows. |
| `lookup_pricing(*, provider, model_name, region, at)`                           | 1387–1410 | Pick row whose `[effective_from, effective_until)` window contains `at`, ordered by `effective_from DESC LIMIT 1`.                                                     |
| `list_runs_missing_cost(*, limit, cursor=None)`                                 | 1412–1433 | Cost-backfill: rows with `cost_micro_usd IS NULL`, `id > cursor`, ORDER BY id, LIMIT.                                                                                  |
| `upsert_user_daily_usage(row)`                                                  | 1435–1474 | INSERT … ON CONFLICT `(org, user, day, provider, model)` DO UPDATE SET (B4).                                                                                           |
| `upsert_org_daily_usage(row)`                                                   | 1476–1516 | INSERT … ON CONFLICT `(org, day, provider, model)` DO UPDATE SET.                                                                                                      |
| `query_user_daily_usage(*, org_id, user_id, start_day, end_day)`                | 1518–1538 | Day-DESC inclusive range.                                                                                                                                              |
| `query_org_daily_usage(*, org_id, start_day, end_day)`                          | 1540–1558 | Day-DESC inclusive range.                                                                                                                                              |
| `query_run_usage(*, org_id, run_id)`                                            | 1560–1575 | Single row read.                                                                                                                                                       |
| `query_run_usage_for_range(*, org_id, user_id, start, end)`                     | 1577–1623 | Three-way SQL: `org_id is None` (rollup loop, `_role_connection("worker")`), `user_id is not None` (PII-purged excluded), `org_id only` (no PII filter).               |
| `query_top_conversations(*, org_id, user_id, start, end, limit)`                | 1625–1649 | GROUP BY conversation_id, SUM(total_tokens), `pii_purged_at IS NULL`, top-N.                                                                                           |
| `query_model_call_usage_for_run(*, org_id, run_id)`                             | 1651–1667 | Per-run per-call rows, ordered by `created_at ASC`.                                                                                                                    |

#### Row → record coercion (classmethods)

| Symbol                                   |     Lines | Purpose                                                                                                                                                                                                      |
| ---------------------------------------- | --------: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `_run_usage_record(row)`                 | 1669–1723 | dict_row → `RuntimeRunUsageRecord`.                                                                                                                                                                          |
| `_model_call_record(row)`                | 1725–1764 | dict_row → `RuntimeModelCallUsageRecord`.                                                                                                                                                                    |
| `_pricing_record(row)`                   | 1766–1796 | dict_row → `ModelPricingRecord`.                                                                                                                                                                             |
| `_user_daily_row(row)`                   | 1797–1817 | dict_row → `UsageDailyUserRow`.                                                                                                                                                                              |
| `_org_daily_row(row)`                    | 1818–1838 | dict_row → `UsageDailyOrgRow`.                                                                                                                                                                               |
| static `_coerce_datetime(value)`         | 1839–1842 | Pass-through datetime, parse ISO string.                                                                                                                                                                     |
| static `_coerce_date_to_datetime(value)` | 1844–1852 | Date → midnight-UTC datetime.                                                                                                                                                                                |
| `_conversation_record(row)`              | 2143–2157 | dict_row → `ConversationRecord`.                                                                                                                                                                             |
| `_message_record(row)`                   | 2159–2186 | dict_row → `MessageRecord`.                                                                                                                                                                                  |
| `_run_record(row)`                       | 2188–2220 | dict_row → `RunRecord` (re-builds `safe_error` envelope).                                                                                                                                                    |
| `_event_envelope(row)`                   | 2222–2263 | dict_row → `RuntimeEventEnvelope`. **Falls back to `RuntimeEventPresentationProjector.activity_kind_for` / `presentation_metadata` when stored columns are NULL** — backward-compat for pre-projection rows. |

#### Event store

| Symbol                                                 |     Lines | Purpose                                                                                                                                                                                        |
| ------------------------------------------------------ | --------: | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `append_event(event)`                                  | 1854–1954 | **Per-run-serialized append (H1)**: `SELECT … FROM agent_runs … FOR UPDATE`, `MAX(sequence_no)+1`, INSERT. The UNIQUE on `runtime_events(run_id, sequence_no)` is the load-bearing safety net. |
| `list_events_after(*, org_id, run_id, after_sequence)` | 1956–1975 | Replay-after-cursor; `sequence_no > after_sequence`, ordered ASC.                                                                                                                              |
| `get_latest_sequence(*, run_id)`                       | 1977–1986 | `MAX(sequence_no) OR 0`.                                                                                                                                                                       |

#### Outbox / queue

| Symbol                                                                               |     Lines | Purpose                                                                                                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------------ | --------: | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `enqueue_run(command)`                                                               | 1988–1997 | Delegate to `_enqueue_command`.                                                                                                                                                                                                                                                          |
| `enqueue_cancel(command)`                                                            | 1999–2008 | Same.                                                                                                                                                                                                                                                                                    |
| `enqueue_approval_resolved(command)`                                                 | 2010–2021 | Same (carries `approval_id` in payload).                                                                                                                                                                                                                                                 |
| `claim_next(*, worker_id, lock_expires_at)`                                          | 2023–2077 | **CTE + SKIP LOCKED + lease-takeover**: pick one row whose `(status IN pending/retry) OR (status=claimed AND lock_expires_at <= now())`, with `available_at <= now()`, ordered by `available_at ASC, created_at ASC`, FOR UPDATE SKIP LOCKED LIMIT 1. UPDATE atomically marks `claimed`. |
| `mark_complete(*, result)` / `mark_retry(*, result)` / `mark_dead_letter(*, result)` | 2079–2092 | Thin shells over `_mark_outbox`.                                                                                                                                                                                                                                                         |
| `_enqueue_command(*, command_id, command_type, org_id, aggregate_id, payload)`       | 2094–2125 | INSERT row with status=pending, attempts=0.                                                                                                                                                                                                                                              |
| `_mark_outbox(*, result, status_value)`                                              | 2127–2140 | UPDATE status + clear lock + COALESCE `available_at`.                                                                                                                                                                                                                                    |

#### Insert helpers

| Symbol                                         |     Lines | Purpose                                                                   |
| ---------------------------------------------- | --------: | ------------------------------------------------------------------------- |
| `_insert_message(conn, message)` (classmethod) | 2266–2304 | INSERT into `agent_messages` with all columns.                            |
| `_insert_run(conn, run)` (classmethod)         | 2307–2344 | INSERT into `agent_runs` (`row_version=1`, `latest_sequence_no` initial). |

### Module-level constants & singletons

- `_Columns` (78–146): 54 column-name string constants. **Mandatory for dict-row access**; the file's contract per [services/ai-backend/CLAUDE.md](../../../services/ai-backend/CLAUDE.md): "No inline duplication of repeated keys".
- `_PoolEnv` (149–213): env keys + defaults: `RUNTIME_DB_POOL_MIN_SIZE=5`, `RUNTIME_DB_POOL_MAX_SIZE=50`, `RUNTIME_DB_POOL_ACQUIRE_TIMEOUT_SECONDS=5.0`, `RUNTIME_DB_STATEMENT_TIMEOUT_MS=10000`, `RUNTIME_DB_LOCK_TIMEOUT_MS=3000`, `RUNTIME_DB_IDLE_IN_TXN_TIMEOUT_MS=30000`. `SERVICE_NAME="ai-backend"`.

## B. Feature inventory

| Domain                                                                       | Symbols                                                                                                  |  LOC |
| ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | ---: |
| **Pool tuning + RLS plumbing**                                               | `_PoolEnv` (and helpers), `__init__`, `open/close`, `_tenant_connection`, `_role_connection`, `migrate`  | ~270 |
| **Conversation CRUD**                                                        | `create_conversation`, `get_conversation`, `list_conversations`                                          |  ~95 |
| **Message CRUD**                                                             | `list_messages`, `append_message`, `_insert_message`                                                     |  ~70 |
| **Run lifecycle CRUD + idempotency + optimistic locking + monotonic cursor** | `create_run_with_user_message`, `get_run`, `update_run_status`, `set_run_latest_sequence`, `_insert_run` | ~270 |
| **Approval CRUD with H2 idempotent insert**                                  | `record_approval_decision`, `create_approval_request`, `get_approval_request`                            | ~135 |
| **Audit chain (HMAC + advisory lock)**                                       | `_take_runtime_audit_chain_lock_async`, `_read_runtime_audit_chain_head_async`, `write_audit_log`        | ~130 |
| **User-history deletion (legal-hold gate, tombstone, evidence row)**         | `delete_user_history`                                                                                    | ~180 |
| **Usage + pricing (B1/B2/B3/B4)**                                            | 14 methods, 1175–1667                                                                                    | ~495 |
| **Event store (per-run FOR UPDATE serialization)**                           | `append_event`, `list_events_after`, `get_latest_sequence`                                               | ~135 |
| **Outbox / queue (SKIP LOCKED claim)**                                       | `enqueue_*`, `claim_next`, `mark_*`, `_enqueue_command`, `_mark_outbox`                                  | ~150 |
| **Row → record coercion**                                                    | 11 classmethods + 2 statics                                                                              | ~325 |

## C. Functional spec per domain

### Pool tuning + RLS plumbing (C4 + C5)

**`_PoolEnv.build_pool_kwargs(role)`** (188–213) returns:

```python
{
  "row_factory": dict_row,
  "options": (
    f"-c statement_timeout={statement_timeout_ms} "       # default 10000
    f"-c lock_timeout={lock_timeout_ms} "                  # default 3000
    f"-c idle_in_transaction_session_timeout={idle_in_txn_ms} "  # default 30000
    f"-c application_name=ai-backend:{role}"               # greppable in pg_stat_activity
  ),
}
```

These are **session-level GUCs** stamped on every checkout, not connection params.

**`_tenant_connection`** (348–381): acquires a pool connection, then stamps two transaction-local GUCs:

- `app.current_org_id = org_id` if provided
- `app.role = role` (defaults to `self._role` — `"api"` or `"worker"`)

Both via `set_config(..., true)` (= transaction-local). Comment at 364–366: "During Stage 1+2 the policies are dormant — `ENABLE ROW LEVEL SECURITY` has not been applied — so setting the vars is harmless and lets us instrument logs to confirm every checkout flows through here."

**`_role_connection`** (383–399): used for cross-tenant ops (worker outbox claim, rollup loop scan). Only sets `app.role`. The `runtime_outbox_events.tenant_or_worker` policy keys off `app.role='worker'` to grant access without an org binding.

### Run lifecycle (C3 optimistic locking)

**`update_run_status`** (702–743) is the load-bearing CAS:

1. SELECT `row_version` for the run.
2. Compute `timestamps` via `StatusTransition.timestamp_updates(status, already_started=...)`.
3. UPDATE `SET status=?, started_at=?, completed_at=?, …, row_version = row_version + 1 WHERE id=? AND row_version=expected_version RETURNING *`.
4. If no row returned: `ConcurrentRunUpdateError(run_id, expected_version)`.

Caller (`runtime_worker/handlers/run.py`) wraps with `with_optimistic_retry` to refetch + retry on conflict.

**`set_run_latest_sequence`** (745–777) — H3 invariant: cursor never goes backwards.

```sql
UPDATE agent_runs
SET latest_sequence_no = %s
WHERE id = %s
  AND (latest_sequence_no IS NULL OR latest_sequence_no < %s)
RETURNING *
```

If no row returned (we tried to set a smaller value) → fallback SELECT to honor the return contract.

**`create_run_with_user_message`** (557–689) — single transaction, all in `_tenant_connection`:

1. **Idempotency check** (578–607): if `idempotency_key` provided, SELECT existing run JOIN message; on hit, validate `(conversation_id, user_input)` fingerprint or raise `IDEMPOTENCY_CONFLICT` (409).
2. **Closure helpers** (611–651): `_get_msg`, `_latest_msg_id`, `_latest_asst` — same connection / transaction so they see in-flight inserts. Comment at 609–610.
3. **Build user message** via `RuntimeAdapterHelpers.amessage_for_run_request` (653–659).
4. **INSERT** message + run (only insert message if NOT regenerating an existing one — `request.regenerate_from_message_id is None` checked at 673, 676, 685).
5. **Bump conversation** `updated_at`.
6. Return `(run, message, created=True)`.

### Approval CRUD (H2)

**`create_approval_request`** (806–879) is atomic-upsert:

```sql
INSERT INTO runtime_approval_requests (...)
VALUES (...)
ON CONFLICT (id) DO NOTHING
RETURNING id
```

If insert succeeds → return `record`. If no rows returned (someone else won) → SELECT existing row JOIN agent_runs and return that. **No check-then-insert race window** (docstring 811–816).

The fallback SELECT joins `agent_runs` to populate `conversation_id` + `user_id` — these aren't stored on the approval row directly.

### Audit chain

**`write_audit_log`** (914–988) sequence:

1. Acquire transaction-local advisory lock for `(audit_log, org_id)` via `pg_advisory_xact_lock` (line 935). Lock key = high 8 bytes of `sha256("audit_chain:runtime_audit_log:<org_id>")` interpreted as signed int64 (235–237).
2. Read head: `last_seq, prev_hash` (936–938).
3. Build canonical signing payload (939–953) — `audit_id`, `org_id`, `user_id`, `actor_type`, `action=event_type`, `resource_type`, `resource_id`, `run_id`, `trace_id`, `outcome`, `metadata`, `created_at`, `__event_type__`.
4. `signer.sign(prev_hash, payload)` (954) → `(prev_hash, signature, key_version)`.
5. INSERT row with full chain fields + `seq+1` (955–988).

**Tamper-evidence invariants:**

- Chain is per-(table, org_id).
- Concurrent appends within the same org serialize on the advisory lock (228–229: "Two concurrent appends would otherwise both read the same prev_hash and fork the chain.").
- Lock auto-releases at transaction commit, so lock scope == insert atomic unit.
- Cross-org chain key collisions are theoretically possible but harmless (extra serialization, never lost integrity) — comment 226–229.

### User-history deletion

**`delete_user_history`** (990–1169) — single transaction, six steps:

1. **Legal-hold gate** (1008–1034): SELECT from `runtime_legal_holds` matching `(scope='org' AND resource_id=org_id) OR (scope='user' AND user_id=user_id) OR (scope='conversation' AND resource_id IN (... conversations for this user))`. Any match → 409 CONFLICT. **Known TOCTOU hazard tracked at 999–1002**: another writer can insert a hold between this SELECT and the UPDATEs.
2. **Archive conversations** (1035–1043): `UPDATE … SET status='archived', archived_at=COALESCE(archived_at, now), updated_at=now WHERE … AND status<>'archived'`. `cur.rowcount` → `conversations_archived`.
3. **Tombstone messages** (1044–1057): `UPDATE … SET status='deleted', deleted_at=COALESCE(deleted_at, now), content_text='[deleted by user request]' WHERE … AND deleted_at IS NULL`. `messages_tombstoned`.
4. **Cancel non-terminal runs** (1058–1067): `WHERE status NOT IN ('cancelled', 'completed', 'failed', 'timed_out')`. `runs_cancelled`.
5. **Count retained events** (1068–1080): `SELECT COUNT(*)` from `runtime_events JOIN agent_runs` for the org+user. **Events are never deleted.**
6. **Sign + insert audit row + evidence row** (1081–1160): hold chain lock → read head → sign deletion payload → INSERT into `runtime_audit_log` → INSERT into `runtime_deletion_evidence`.

### Usage + pricing (B1/B2/B3/B4)

**B1 — `record_run_usage`** (1175–1232): `INSERT … ON CONFLICT (run_id) DO NOTHING`. Worker retries are safe.

**B2 — `record_model_call_usage`** (1234–1282): plain INSERT. Each row has a unique UUID `id`; **caller dedupes** ("upstream dedupe (one row per AIMessage id) is the worker's job", 1239–1241).

**B3 — `upsert_pricing`** (1324–1385): one transaction, two writes:

1. `UPDATE model_pricing SET effective_until = new.effective_from WHERE provider/model/region match AND effective_until IS NULL AND effective_from < new.effective_from` — closes prior active row.
2. `INSERT new row`.

Invariant: at most one active row per `(provider, model_name, region)` triple — enforced by the partial unique index on `effective_until IS NULL`. The transaction prevents readers from seeing zero or two active rows.

**B3 — `lookup_pricing`** (1387–1410):

```sql
SELECT * FROM model_pricing
 WHERE provider=? AND model_name=? AND region=?
   AND effective_from <= ?
   AND (effective_until IS NULL OR effective_until > ?)
 ORDER BY effective_from DESC
 LIMIT 1
```

**B4 — daily rollup upserts** (1435–1516): both user and org variants use `INSERT … ON CONFLICT … DO UPDATE SET` with the appropriate composite key.

**Range query — `query_run_usage_for_range`** (1577–1623) has three SQL branches:

| Inputs               | Connection                   | SQL filter                                | PII guard                        |
| -------------------- | ---------------------------- | ----------------------------------------- | -------------------------------- |
| `org_id is None`     | `_role_connection("worker")` | `completed_at BETWEEN`                    | none (rollup loop, cross-tenant) |
| `org_id` + `user_id` | `_tenant_connection(org_id)` | `org_id`+`user_id`+`completed_at BETWEEN` | `pii_purged_at IS NULL`          |
| `org_id` only        | `_tenant_connection(org_id)` | `org_id`+`completed_at BETWEEN`           | none                             |

Comment at 1612–1615: rollup-loop scan uses worker role connection so the `tenant_or_worker` precedent applies once Stage 3 of C5 enables RLS broadly.

**`query_top_conversations`** (1625–1649): GROUP BY conversation_id, ordered by SUM(total_tokens) DESC. Always filters `pii_purged_at IS NULL`.

### Event store (H1)

**`append_event`** (1854–1954) — per-run serialized append, single transaction:

1. `SELECT org_id FROM agent_runs WHERE id = ? FOR UPDATE` — acquires the run row lock. Concurrent appends for the same run block here.
2. `SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM runtime_events WHERE run_id=?` — read next sequence inside the lock.
3. Build envelope; resolve `activity_kind` via `RuntimeEventPresentationProjector.activity_kind_for` if absent.
4. INSERT into `runtime_events` with full envelope.

The UNIQUE on `runtime_events(run_id, sequence_no)` is **the load-bearing safety net** (docstring 8–11): if it ever fires, the lock pattern is broken.

### Outbox / queue (SKIP LOCKED)

**`claim_next`** (2023–2077) — single CTE + UPDATE:

```sql
WITH next_event AS (
    SELECT id FROM runtime_outbox_events
    WHERE (status IN ('pending', 'retry')
           OR (status = 'claimed' AND lock_expires_at <= now()))
      AND available_at <= now()
    ORDER BY available_at ASC, created_at ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED
)
UPDATE runtime_outbox_events outbox
SET status = 'claimed', attempts = attempts + 1,
    locked_by = %s, lock_expires_at = %s, updated_at = now()
FROM next_event
WHERE outbox.id = next_event.id
RETURNING outbox.*
```

**Lease takeover** (2040): a row whose `lock_expires_at <= now()` is reclaimable even though its status is `claimed`. This recovers from worker crashes without a separate sweeper.

**Ordering**: `available_at ASC, created_at ASC` — backoff-aware FIFO.

**Connection**: `_role_connection("worker")` (no tenant binding).

## D. Bugs / edge cases / invariants

- **H1 — event append serialization** (1854–1864 docstring): per-run lock via `agent_runs FOR UPDATE`; `runtime_events(run_id, sequence_no)` UNIQUE is the safety net.
- **H2 — approval idempotent insert** (811–816): `ON CONFLICT (id) DO NOTHING RETURNING id` + SELECT-on-miss. Two concurrent creators converge.
- **H3 — monotonic run cursor** (748–754): never rewind `latest_sequence_no`. Out-of-order writes are no-ops.
- **H4 — single-transaction multi-statement run create** (575–576): "if we release the connection mid-way we lose atomicity."
- **C3 — optimistic locking** (705–712): `row_version` CAS; `ConcurrentRunUpdateError` for caller retry.
- **C4 — pool guards** (190–213): server-side `statement_timeout`, `lock_timeout`, `idle_in_transaction_session_timeout`, `application_name`. Defends against runaway connections + greppable diagnostics.
- **C5 — RLS session vars** (348–399): every connection stamps `app.current_org_id` and `app.role`. Worker scope opens a different `_role_connection` to bypass tenant constraint where the worker policy permits.
- **Audit chain advisory lock** (216–238): per-org `pg_advisory_xact_lock`; auto-releases on commit. Prevents prev_hash race.
- **Audit chain payload binding** (911 docstring + 952): `__event_type__` baked into signed payload to bind action identity.
- **Legal-hold TOCTOU** (999–1002): explicit TODO. SELECT-then-UPDATE race; writer can insert a hold between the gate and the deletion. Tracked separately.
- **Tombstone vs delete invariant**: messages get content scrub (`'[deleted by user request]'`) but row preserved (1047–1048); events are never deleted (1068–1080).
- **Run cancel only if non-terminal** (1063): the deletion path won't downgrade `COMPLETED → CANCELLED`.
- **Pricing window invariant** (1325–1331): "the partial unique index on `effective_until IS NULL` requires that we close any prior active row before inserting … readers never see zero or two active rows."
- **PII purge fence** in usage queries (1600, 1641): purged rows excluded from user-scoped reads; included in org-only and cross-tenant rollup scans.
- **Backward-compat for projection columns** (2224–2237): `_event_envelope` falls back to `RuntimeEventPresentationProjector.activity_kind_for` / `presentation_metadata` when the stored columns are NULL — supports rows written before projection columns were populated.
- **`set_run_latest_sequence` no-op return** (769–776): when the UPDATE didn't fire, fallback SELECT returns the current row to honor the contract that this method always returns a `RunRecord`.
- **Run-create fingerprint is `(conversation_id, user_input)`** (591–600): same as in-memory adapter (parity).
- **Lease takeover via `lock_expires_at <= now()`** (2040): worker crash recovery without external sweeper.
- **Owned vs injected pool** (`_owns_pool`, 291, 325, 331, 338): `open()`/`close()` are no-ops when caller injected a pool — caller manages lifecycle. Important for tests that share a pool.
- **`migrate()` worker-thread offload** (419–423): `yoyo` is sync; `asyncio.to_thread` keeps the loop free.
- **`migrate()` no-op if pool injected** (414–418): "test harness sets up the schema independently."

## E. Hardcoded vs configurable

### Hardcoded

- All **table names**: `agent_conversations`, `agent_messages`, `agent_runs`, `runtime_events`, `runtime_outbox_events`, `runtime_approval_requests`, `runtime_audit_log`, `runtime_run_usage`, `runtime_model_call_usage`, `model_pricing`, `runtime_usage_daily_user`, `runtime_usage_daily_org`, `runtime_legal_holds`, `runtime_deletion_evidence`.
- All **column names** via `_Columns` (78–146).
- `SERVICE_NAME = "ai-backend"` (166) — used in `application_name`.
- Status literal sets: `('cancelled', 'completed', 'failed', 'timed_out')` (1063), `'archived'` (1038–1039), `'deleted'` (1047), `('pending', 'retry')` (2039).
- Tombstone text: `'[deleted by user request]'` (1048).
- Audit hard-coded fields when NULL: `actor_type='system'/'user'`, `outcome='success'`, `resource_type='runtime'/'user_history'`, `resource_id='unknown'`.
- Advisory lock key prefix: `"audit_chain:runtime_audit_log:"` (235).

### Configurable (via `_PoolEnv`)

- `RUNTIME_DB_POOL_MIN_SIZE` (default 5)
- `RUNTIME_DB_POOL_MAX_SIZE` (default 50)
- `RUNTIME_DB_POOL_ACQUIRE_TIMEOUT_SECONDS` (default 5.0)
- `RUNTIME_DB_STATEMENT_TIMEOUT_MS` (default 10000)
- `RUNTIME_DB_LOCK_TIMEOUT_MS` (default 3000)
- `RUNTIME_DB_IDLE_IN_TXN_TIMEOUT_MS` (default 30000)

### Configurable (constructor args)

- `database_url`, `pool` (mutually exclusive but at least one required).
- `role` (default `"api"`).
- Pool sizes overridable per-instance.

### From env (indirect)

- Audit chain key + key_version via `AuditChainSigner.from_env()` (lines 932, 1085).
- `RUNTIME_MIGRATIONS_AUTO_APPLY` via `MigrationRunner.auto_apply_enabled()` (412).

## F. External dependencies and coupling

### Internal `agent_runtime.*`

- `agent_runtime.api.constants.Messages` — error strings.
- `agent_runtime.observability.audit_chain.AuditChainSigner` — HMAC primitive.
- `agent_runtime.execution.contracts` — `RuntimeErrorCode`, `RuntimeErrorEnvelope`, `StreamEventSource`.
- `agent_runtime.persistence.constants.Values as PersistenceValues` — `EventType.*`, `AggregateType.AGENT_RUN`.
- `agent_runtime.persistence.pool_metrics.PoolMetrics` — OTel pool meters.
- `agent_runtime.persistence.records` — record dataclasses.
- `agent_runtime.persistence.schema.migrate.MigrationRunner` — yoyo runner.
- `agent_runtime.persistence.errors.ConcurrentRunUpdateError` — lazy import inside `update_run_status` (line 714).

### Internal `runtime_*`

- `runtime_adapters.base` — `RuntimeAdapterHelpers`, `StatusTransition`, `_Fields`. **Tight cross-adapter parity coupling.**
- `runtime_api.http.errors.RuntimeApiError` — HTTP error type.
- `runtime_api.schemas` — every record + command type.

### Stdlib / third-party

- `psycopg` + `psycopg.rows.dict_row` + `psycopg.types.json.Jsonb`.
- `psycopg_pool.AsyncConnectionPool`.
- `starlette.status` — HTTP status constants.
- `os`, `datetime`, `contextlib.asynccontextmanager`, `collections.abc`.
- Lazy `import hashlib` inside the audit lock helper (line 232).
- Lazy `import asyncio` inside `migrate()` (line 421).

## G. Suggested decomposition seams

The class is already a giant adapter. Cuts that would each be self-contained, mirror the in-memory adapter cuts, and follow existing comment fences:

1. **`postgres/conversation_store.py`** — `create_conversation`, `get_conversation`, `list_conversations`, `_conversation_record`. ~120 LOC.
2. **`postgres/message_store.py`** — `list_messages`, `append_message`, `_insert_message`, `_message_record`. ~100 LOC.
3. **`postgres/run_store.py`** — `create_run_with_user_message`, `get_run`, `update_run_status`, `set_run_latest_sequence`, `_insert_run`, `_run_record`. ~270 LOC. Includes the optimistic-lock CAS + monotonic cursor.
4. **`postgres/approval_store.py`** — three approval methods. ~135 LOC.
5. **`postgres/audit_store.py`** — `_take_runtime_audit_chain_lock_async`, `_read_runtime_audit_chain_head_async`, `write_audit_log`. ~130 LOC.
6. **`postgres/history_deletion.py`** — `delete_user_history` (currently inlines its own audit-chain sign+insert; could call into `audit_store`). ~180 LOC.
7. **`postgres/usage_store.py`** — the entire B1/B2/B3/B4 region (1175–1838) including coercers. ~600 LOC. The comment fence at 1171 already marks this. Could be split further by `(record/lookup/upsert)` vs `(query/range)`.
8. **`postgres/event_store.py`** — `append_event`, `list_events_after`, `get_latest_sequence`, `_event_envelope`. ~135 LOC.
9. **`postgres/outbox_store.py`** — enqueue*\*, claim_next, mark*\*, `_enqueue_command`, `_mark_outbox`. ~150 LOC.
10. **`postgres/pool.py`** — `_PoolEnv`, `_tenant_connection`, `_role_connection`, pool ownership tracking + metrics. ~150 LOC. The base for all the others.

The existing `_Columns` constant pool is the seam-of-seams: each cut would use only ~10–15 of the 54 column names. Splitting `_Columns` along those same boundaries would make the cuts visually clean.

The `RuntimeAdapterHelpers` / `StatusTransition` / `_Fields` already pulled into `runtime_adapters/base.py` proves the seam direction works — the in-memory adapter shows the same domain decomposition latent in its single class.

A natural mixin or composition pattern emerges: a `_PostgresStoreBase` providing `_tenant_connection` + `_role_connection` + `_pool` + `_metrics`, with each domain store as a separate class composed at the public adapter boundary.
