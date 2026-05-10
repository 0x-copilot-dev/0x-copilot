# PRD — Retention sweep replacement (`retention_until` everywhere + bounded sweep + evidence)

> **Status:** Draft (PRD)
> **Refactor target:** Audit finding [§1.5](../architecture/refactor-audit.md#15-custom-retention-sweep--5-level-policy-resolver) — _Custom retention sweep + 5-level policy resolver_.
> **Owner:** ai-backend.
> **Scope:** Backend only. No `api-types` change. No public API change. New persisted column on three tables; new audit / metric emission; significantly thinner sweeper SQL.
> **Companion specs to update on landing:** [10-agent-runtime-persistence-spec.md](../specs/10-agent-runtime-persistence-spec.md), [11-persistence-org-scoping-audit.md](../specs/11-persistence-org-scoping-audit.md).

---

## 0 · TL;DR

Today the retention subsystem is a per-tenant tombstone-or-delete loop in application code. Every 10 minutes the worker walks every org × kind, runs the 5-level policy resolver in Python, then dispatches one un-batched SQL per (org, kind) that scans the whole table, joins to legal holds, and updates / deletes rows whose `created_at + ttl < NOW()`. Five different per-kind strategies are baked into the postgres adapter:

| Kind               | Strategy                                                     | Tombstone fields                                                                | Hard-delete?                                                   |
| ------------------ | ------------------------------------------------------------ | ------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `messages`         | Tombstone                                                    | `status='deleted'`, blanked content, `deleted_at=NOW()`                         | No (grace promised, not implemented)                           |
| `events`           | Tombstone                                                    | redacted payloads blanked + `metadata_json_redacted = {retention_purged: true}` | No                                                             |
| `context_payloads` | Hard delete                                                  | n/a                                                                             | Yes — driven by `retention_until` column already (TTL ignored) |
| `checkpoints`      | Hard delete with "keep latest 10 per (thread_id, namespace)" | n/a                                                                             | Yes                                                            |
| `memory_items`     | Tombstone                                                    | `deleted_at=NOW()`, blanked `content_summary`                                   | No                                                             |

This works but suffers from four concrete problems:

1. **Unbounded scans.** Every sweep tick scans the entire row population per (org, kind). At million-row scale a single `UPDATE … WHERE created_at < ...` blocks for minutes and bloats `agent_messages` / `runtime_events` until vacuum catches up.
2. **Resolver runs per sweep tick instead of per write.** TTL is computed on every sweep pass against a flat in-memory dict built from a `SELECT *` of `retention_policies`. Cheap individually, redundant in aggregate.
3. **No evidence / metrics.** The `runtime_deletion_evidence` table exists in [migration 0001](../../migrations/0001_initial_runtime_persistence.sql#L413) since day one and is **never written by the sweeper**. OTel metrics are absent. The only output is a `_LOGGER.info("retention_swept", ...)` line.
4. **Promised grace period not implemented.** Code comments reference "30d grace then hard-delete" for tombstoned rows; that second pass does not exist. Tombstoned messages live forever.

Plus three smaller smells: the postgres adapter has zero direct test coverage for the sweep SQL; the `assistant` scope is in the type system but has zero precedence-walk tests; large orgs cannot opt in to dry-run with confidence because the dry-run rolls back inside one transaction (so the adapter sees the write IO load anyway).

The refactor — described phased, behavior-preserving — is:

1. **Stamp `retention_until` at write time** on `agent_messages`, `runtime_events`, `runtime_memory_items`, matching the pattern that already exists for `runtime_context_payloads`.
2. **Make policy upserts / deletes recompute `retention_until` for affected rows** so the resolver is no longer evaluated per sweep tick.
3. **Replace the per-kind sweep SQL with a uniform "find rows past `retention_until`, batched"** path. Tombstone semantics stay identical; the WHERE clause shrinks to a single timestamp comparison + the existing legal-hold join.
4. **Write `runtime_deletion_evidence` rows on every sweep** so compliance reviewers can answer "what got deleted, when, by what policy" without reading worker logs. Emit OTel metrics in the same place.
5. **Implement the documented 30-day grace then hard-delete** for `messages` / `events` / `memory_items` as a separate sweep kind (`*_TOMBSTONED`) so the second pass is queryable.
6. **Gap fixes:** tests for the postgres SQL paths; tests for the `assistant` scope; per-kind chunk size config; an audit event on policy mutation.

The 5-level resolver, the API surface, the privacy override, the legal-hold filter, and tombstone-vs-hard-delete semantics per kind are **all preserved.** The change is mostly mechanical: move TTL evaluation from "loop time" to "write / policy-change time," and make the sweep query trivial.

---

## 1 · Problem

### 1.1 What retention does today (verified in code)

Five files own the live behavior:

- [`agent_runtime/retention/policy_resolver.py`](../../src/agent_runtime/retention/policy_resolver.py) — pure resolver. Walks `CONVERSATION > ASSISTANT > USER > ORG > deployment_default` ([resolver.py:113-144](../../src/agent_runtime/retention/policy_resolver.py#L113)) and returns a `ResolvedPolicy(kind, ttl_seconds, source_scope)`. Layers per-user `privacy_settings.retention_days` overrides as synthesized USER-scope rows ([resolver.py:97-111](../../src/agent_runtime/retention/policy_resolver.py#L97)).
- [`agent_runtime/persistence/records/retention.py`](../../src/agent_runtime/persistence/records/retention.py) — `RetentionScope` (4 values), `RetentionKind` (5 values), `RetentionPolicyRecord`, `RetentionSweepOutcome`, `ResolvedPolicy`.
- [`runtime_worker/jobs/retention_sweeper.py`](../../src/runtime_worker/jobs/retention_sweeper.py) — the `RetentionSweeperLoop`. `start()` spawns one asyncio task; `_run()` waits `RETENTION_SWEEP_INTERVAL_SECONDS` (default 600) between passes. `sweep_once()` ([sweeper.py:131-182](../../src/runtime_worker/jobs/retention_sweeper.py#L131)) iterates `list_retention_orgs()` × `_SWEEP_KINDS` and calls `persistence.sweep_retention_kind(...)` per (org, kind).
- [`runtime_adapters/postgres/runtime_api_store.py:3057-3325`](../../src/runtime_adapters/postgres/runtime_api_store.py) — postgres implementation of `list_retention_orgs`, `list_retention_policies`, `upsert_retention_policy`, `delete_retention_policy`, `sweep_retention_kind`, and the five private `_sweep_*` per-kind methods.
- [`runtime_api/http/retention_routes.py`](../../src/runtime_api/http/retention_routes.py) — `RetentionAdminRoutes` (admin CRUD) + `RetentionAdminRoutes.effective` (member-readable resolver view). Mounted under two sister routers at `/v1/retention/*`.

Plus four files that read retention concepts:

- [`agent_runtime/api/workspace_defaults_service.py`](../../src/agent_runtime/api/workspace_defaults_service.py) — Settings retention slider writes three ORG-scope policies (MESSAGES, EVENTS, CHECKPOINTS) atomically ([workspace_defaults_service.py:232-263](../../src/agent_runtime/api/workspace_defaults_service.py#L232)).
- [`agent_runtime/persistence/encryption.py:394`](../../src/agent_runtime/persistence/encryption.py#L394) — decrypt path lets non-envelope content through with a comment "e.g. retention sweeper rewrote the column to a placeholder."
- [`agent_runtime/persistence/message_copy.py:7`](../../src/agent_runtime/persistence/message_copy.py#L7) — fork path resets `created_at = NOW()` so "the retention sweeper sees the fork's age" rather than inheriting the original's age.
- [`agent_runtime/capabilities/tools/privacy.py:44-46`](../../src/agent_runtime/capabilities/tools/privacy.py#L44) — privacy snapshot carries `retention_days` which becomes `privacy_user_retention_days` in the resolver.

Plus one DB-only invariant baked into [migration 0001](../../migrations/0001_initial_runtime_persistence.sql):

- `runtime_legal_holds(org_id, scope, resource_id, released_at)` table — used as a join filter in every sweep SQL.
- `runtime_deletion_evidence(...)` table — declared with `messages_tombstoned INTEGER NOT NULL DEFAULT 0` plus the standard org+user+created_at index. **Never written by current code.**

Plus one CRUD migration:

- [migration 0012](../../migrations/0012_retention_policies.sql) — creates `retention_policies` with `idx_retention_policies_unique` over `(org_id, scope, COALESCE(resource_id, ''), kind)` and `idx_retention_policies_org_kind`.

### 1.2 What the sweeper actually does each tick (verified)

For each org returned by `list_retention_orgs()` (a `UNION` of distinct `org_id` across the five affected tables — [postgres adapter:3057-3073](../../src/runtime_adapters/postgres/runtime_api_store.py#L3057)):

1. `list_retention_policies(org_id)` returns all policies for the org.
2. `RetentionPolicyResolver(org_id, policies, deployment_defaults, privacy_user_retention_days=...)` is constructed.
3. For each kind in the order `(CONTEXT_PAYLOADS, CHECKPOINTS, MESSAGES, EVENTS, MEMORY_ITEMS)`:
   a. `resolver.resolve(kind=kind)` returns a `ResolvedPolicy`.
   b. If `ttl_seconds is None` AND kind is not `CONTEXT_PAYLOADS` → skip (no policy = no sweep).
   c. Else `persistence.sweep_retention_kind(org_id, kind, ttl_seconds, dry_run)` runs one SQL.
4. Per-(org, kind) result is logged. No table is updated.

The five `_sweep_*` SQLs from [postgres adapter:3146-3295](../../src/runtime_adapters/postgres/runtime_api_store.py#L3146) are summarized below. Full table-of-impact at [§2](#2--map-of-every-system-the-retention-subsystem-touches).

| Kind               | SQL action                             | WHERE shape                                                                                                                         | Tombstone columns set / row deletion                                                                                              |
| ------------------ | -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `MESSAGES`         | `UPDATE agent_messages`                | `org_id=? AND status<>'deleted' AND created_at < NOW() - interval(ttl_seconds) AND NOT EXISTS (legal_hold)`                         | `status='deleted'`, `content_text='[deleted by retention policy]'`, `content_json='[]'`, `metadata_json='{}'`, `deleted_at=NOW()` |
| `EVENTS`           | `UPDATE runtime_events`                | `org_id=? AND created_at < NOW() - interval(ttl_seconds) AND NOT EXISTS (legal_hold)`                                               | `payload_json_redacted='{}'`, `metadata_json_redacted = jsonb_build_object('retention_purged', true)`                             |
| `CONTEXT_PAYLOADS` | `DELETE FROM runtime_context_payloads` | `org_id=? AND retention_until IS NOT NULL AND retention_until < NOW()`                                                              | (entire row) — TTL is ignored; `retention_until` is the source of truth                                                           |
| `CHECKPOINTS`      | `DELETE FROM runtime_checkpoints`      | `org_id=? AND ROW_NUMBER() OVER (PARTITION BY thread_id, checkpoint_namespace) > 10 AND created_at < NOW() - interval(ttl_seconds)` | (entire row)                                                                                                                      |
| `MEMORY_ITEMS`     | `UPDATE runtime_memory_items`          | `org_id=? AND deleted_at IS NULL AND created_at < NOW() - interval(ttl_seconds)`                                                    | `deleted_at=NOW()`, `content_summary='[deleted by retention policy]'`                                                             |

All five take the tenant-scoped postgres connection. Dry-run wraps the SQL in a transaction with `force_rollback=True` so the adapter sees the IO and gets an accurate rowcount but no rows change.

### 1.3 What the API surface exposes today (verified)

Three admin routes (require `admin:retention` + `runtime:use`) and one member-readable route (requires only `runtime:use`):

| Method + path                               | Body / query                   | Response                      | Behavior                                                          |
| ------------------------------------------- | ------------------------------ | ----------------------------- | ----------------------------------------------------------------- |
| `GET /v1/retention/policies`                | `?org_id=&user_id=`            | `RetentionPolicyListResponse` | Lists all policies for org                                        |
| `POST /v1/retention/policies`               | `RetentionPolicyUpsertRequest` | `RetentionPolicyView`         | Upserts keyed by `(org_id, scope, resource_id, kind)`; idempotent |
| `DELETE /v1/retention/policies/{policy_id}` | `?org_id=&user_id=`            | `{"status":"deleted"}`        | Removes one policy by id                                          |
| `GET /v1/retention/effective`               | `?org_id=&user_id=`            | `RetentionEffectiveResponse`  | Per-kind effective TTL view; consumed by the Privacy & data panel |

`POST` validates that ORG-scope policies have `resource_id=null` and non-ORG scopes have non-null `resource_id` ([retention_routes.py:148-161](../../src/runtime_api/http/retention_routes.py#L148)). No other invariant checks.

The "effective" route ([retention_routes.py:58-107](../../src/runtime_api/http/retention_routes.py#L58)) re-uses the same `RetentionPolicyResolver` the sweeper uses, so the displayed value is always the value that gets applied. Per-resource overrides (per-user, per-conversation, per-assistant) are intentionally NOT surfaced here — they are visible only via `GET /v1/retention/policies` (admin scope).

### 1.4 Why this is a problem

| Concern                                              | Evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Unbounded scans on growing tables**                | Each sweep tick runs one `UPDATE … WHERE created_at < ...` per (org, kind), with no `LIMIT`, no chunking. At ~10M `runtime_events` rows per org (a year of moderate use), that's a sequential UPDATE blocking on the row-level locks for minutes, bloating the table. [postgres adapter:3297-3324](../../src/runtime_adapters/postgres/runtime_api_store.py#L3297) — single `_execute_sweep` runs the SQL and returns the rowcount, no chunking.             |
| **Resolver re-runs per sweep tick**                  | `RetentionPolicyResolver` is rebuilt every 10 minutes per org, used for one resolve per kind, then discarded. The TTL of an existing row never changes after insertion (policies upsert with `ON CONFLICT DO UPDATE` — line 3093-3125), so "evaluate at insert / policy-change time and stamp the answer" is strictly cheaper.                                                                                                                               |
| **No evidence row written**                          | `runtime_deletion_evidence` declared in [migration 0001:413-426](../../migrations/0001_initial_runtime_persistence.sql#L413), with `messages_tombstoned INTEGER NOT NULL DEFAULT 0` etc. Sweeper never inserts. Compliance reviewers cannot answer "show me what was deleted on 2026-04-12" without parsing worker logs.                                                                                                                                     |
| **No metrics**                                       | The only output is `_LOGGER.info("retention_swept", ...)` ([sweeper.py:168-180](../../src/runtime_worker/jobs/retention_sweeper.py#L168)). No OTel counter, no histogram, no `runtime_audit_log` event.                                                                                                                                                                                                                                                      |
| **Promised hard-delete grace not implemented**       | Sweep doc-comment in [postgres adapter:3134-3145](../../src/runtime_adapters/postgres/runtime_api_store.py#L3134) references the C8 spec's "tombstone then hard-delete after grace." MESSAGES, EVENTS, MEMORY_ITEMS stay tombstoned forever today.                                                                                                                                                                                                           |
| **Postgres SQL has no test coverage**                | Adapter sweep paths are not exercised by [tests/unit/runtime_worker/test_retention_sweeper.py](../../tests/unit/runtime_worker/test_retention_sweeper.py) — that test uses a `_FakePersistence` stub. The postgres adapter has zero test coverage for sweep behavior; the in-memory adapter stubs `sweep_retention_kind` to return `(0, 0, 0)` ([async_runtime_api_store.py:619-622](../../src/runtime_adapters/in_memory/async_runtime_api_store.py#L619)). |
| **`assistant` scope shipped without resolver tests** | [test_policy_resolver.py](../../tests/unit/agent_runtime/retention/test_policy_resolver.py) covers conversation > user > org plus tenant isolation plus 4 privacy-override cases. No test exercises the assistant scope's precedence position.                                                                                                                                                                                                               |
| **Dry-run still incurs IO**                          | Dry-run runs the same SQL inside a `force_rollback` transaction. The DB still does the update planning, lock acquisition, and rowcount tally. For a multi-million-row table this is the same blast radius as a live sweep.                                                                                                                                                                                                                                   |
| **Kind-shaped postgres logic**                       | Five separate `_sweep_*` private methods, each with its own SQL, its own legal-hold filter, its own column rewrite. Adding a sixth retention kind means writing a sixth bespoke SQL.                                                                                                                                                                                                                                                                         |

### 1.5 Why it exists (inferred)

- **The C8 spec landed before the schema had `retention_until` everywhere.** Only `runtime_context_payloads` got the column ([0001:335](../../migrations/0001_initial_runtime_persistence.sql#L335)) because that table's TTL was driven by the writer (the context manager). For everything else, "scan-by-`created_at`" was the path of least resistance.
- **Tombstoning vs. hard-deleting is a real compliance distinction.** MESSAGES / EVENTS / MEMORY_ITEMS keep an evidence row so deletion is observable. CONTEXT_PAYLOADS and CHECKPOINTS are reproducible state and large; hard delete is fine.
- **The sweeper was opt-in (`RETENTION_SWEEP_ENABLED=false`) so missing pieces (evidence rows, hard-delete grace) didn't block ship.** That choice is still right; we'll keep it.
- **The privacy user override (PR 8.0.5)** was added by layering synthetic USER-scope rows in the resolver. That works — but it means the effective TTL for a row depends on whether the user had a privacy override set _at the moment the sweeper ran_. This is fine in practice (privacy panels rarely change) but conceptually fragile for the same reason as item 1.4.2 above: TTL should be stamped at write time, then it's stable.

---

## 2 · Map of every system the retention subsystem touches

This is the surface area to keep in mind when refactoring. Each file below is either deleted, modified, or read.

### 2.1 Core retention machinery (will modify, not delete)

| File                                                                                                                           | Role today                                                                                            | Refactor verdict                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/retention/policy_resolver.py`](../../src/agent_runtime/retention/policy_resolver.py)                           | 5-level resolver + privacy override + deployment defaults                                             | **Keep entirely.** This is the one piece of the subsystem that is unambiguously good — pure logic, no DB, no clock, easy to test. The refactor calls it from new places (insert-time stamping, policy-change stamping) without changing it.                                                                                                                                                                                                                                                                             |
| [`agent_runtime/persistence/records/retention.py`](../../src/agent_runtime/persistence/records/retention.py)                   | `RetentionScope`, `RetentionKind`, `RetentionPolicyRecord`, `RetentionSweepOutcome`, `ResolvedPolicy` | **Modify.** Add `RetentionKind.MESSAGES_TOMBSTONED`, `EVENTS_TOMBSTONED`, `MEMORY_ITEMS_TOMBSTONED` for the second-pass hard-delete kinds. Add `RetentionDeletionEvidenceRecord` here (mirroring the existing 0001 table). Keep `ResolvedPolicy` shape.                                                                                                                                                                                                                                                                 |
| [`runtime_worker/jobs/retention_sweeper.py`](../../src/runtime_worker/jobs/retention_sweeper.py)                               | Sweep loop                                                                                            | **Modify.** `_SWEEP_KINDS` extends to include the three new TOMBSTONED kinds. Loop body adds: per-kind chunk size from env, OTel metric emission, evidence-row insert, and an audit event. Resolver is removed from the per-tick path (it stops being needed because TTL is stamped at write time — the sweep just compares timestamps).                                                                                                                                                                                |
| [`runtime_adapters/postgres/runtime_api_store.py`](../../src/runtime_adapters/postgres/runtime_api_store.py) (lines 3057-3325) | Five per-kind `_sweep_*` SQLs + CRUD on `retention_policies`                                          | **Modify, significantly.** All five sweep SQLs collapse to one shape: `WHERE retention_until < NOW() AND NOT EXISTS (legal_hold) ORDER BY retention_until LIMIT chunk`. Per-kind logic narrows to (a) which table, (b) tombstone columns vs `DELETE`, (c) which legal-hold scope. New methods: `_recompute_retention_until_for_policy(org_id, scope, resource_id, kind)`, `_stamp_retention_until_for_inserts(...)`. Existing `upsert_retention_policy` / `delete_retention_policy` gain a follow-up call to recompute. |

### 2.2 Persistence ports (will modify)

| File                                                                                                                               | Role today                                                                      | Refactor verdict                                                                                                                                                                                                                                                              |
| ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/api/ports.py`](../../src/agent_runtime/api/ports.py) (sync)                                                        | `list_retention_policies`, `upsert_retention_policy`, `delete_retention_policy` | **Modify.** Upsert / delete methods grow a `recompute_retention_until: bool = True` keyword so the test harness can opt out. Sweep methods do not exist on the sync port (they don't today either — sweep is async-only).                                                     |
| [`agent_runtime/api/async_ports.py`](../../src/agent_runtime/api/async_ports.py) (async)                                           | Same plus `list_retention_orgs`, `sweep_retention_kind`                         | **Modify.** `sweep_retention_kind(...)` gains `chunk_size: int` keyword (default 10_000). Add `recompute_retention_until_for_policy(...)` method called by upsert / delete after the policy row is written.                                                                   |
| [`runtime_adapters/in_memory/runtime_api_store.py`](../../src/runtime_adapters/in_memory/runtime_api_store.py) (sync)              | Stores policies in a dict                                                       | **Modify.** Add a stamping pass on policy upsert / delete that walks the in-memory rows and sets `retention_until` (test fidelity matters here so the resolver-walk → stamp behavior is observable in unit tests).                                                            |
| [`runtime_adapters/in_memory/async_runtime_api_store.py`](../../src/runtime_adapters/in_memory/async_runtime_api_store.py) (async) | Stubs sweep                                                                     | **Modify.** Stop stubbing — implement an actual in-memory sweep that respects the new `retention_until` columns and writes evidence rows. The sweep logic is small: filter rows by `retention_until < now`, exclude legal-held resources, write evidence row, return outcome. |

### 2.3 Schema (will add a migration)

| File                                                                                                       | Role today                                                                                                              | Refactor verdict                                                                                                                                                                                                                                                         |
| ---------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`migrations/0001_initial_runtime_persistence.sql`](../../migrations/0001_initial_runtime_persistence.sql) | Declares `agent_messages`, `runtime_events`, `runtime_memory_items`, `runtime_legal_holds`, `runtime_deletion_evidence` | **Read only.** `runtime_deletion_evidence` shape is reused as-is; the refactor finally writes to it.                                                                                                                                                                     |
| [`migrations/0012_retention_policies.sql`](../../migrations/0012_retention_policies.sql)                   | `retention_policies` table                                                                                              | **Read only.** Schema is correct; no change.                                                                                                                                                                                                                             |
| `migrations/0027_retention_until_columns.sql` (new)                                                        | n/a                                                                                                                     | **Create.** Adds `retention_until TIMESTAMPTZ NULL` to `agent_messages`, `runtime_events`, `runtime_memory_items`. Adds partial indexes `idx_<table>_retention_until_active` filtered by `retention_until IS NOT NULL` so the sweep only scans rows that have a TTL set. |
| `migrations/0028_retention_until_backfill.sql` (new, idempotent)                                           | n/a                                                                                                                     | **Create.** One-shot data migration that walks each org, builds the resolver, and stamps `retention_until = created_at + ttl_seconds * INTERVAL '1 second'` on every existing row. Skips rows where the resolver returns `None`. Idempotent: re-running is a no-op.      |

The original audit suggested `pg_partman` partitioning. We are explicitly _not_ doing that in this refactor for three reasons:

1. **Tombstone semantics are required for `messages` / `events` / `memory_items`** (compliance evidence). `pg_partman` gives `DROP PARTITION` (hard delete only). You'd need a tombstone column AND partitioning, doubling complexity.
2. **Legal hold can attach mid-partition.** Dropping a partition would silently drop rows that should be held. Per-row legal-hold filter is required regardless.
3. **The `keep latest 10 per (thread_id, namespace)`** rule for checkpoints does not align to a time-partition boundary.

`pg_partman` is the right answer when scale demands it (`runtime_events` past tens of millions per org), but it should layer on top of the `retention_until`-driven sweep, not replace it. We treat partitioning as a Phase 5 follow-up, after the simpler refactor proves out.

### 2.4 API surface (no change)

| File                                                                                     | Role today                          | Refactor verdict                                                                                                                                                                                                              |
| ---------------------------------------------------------------------------------------- | ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`runtime_api/http/retention_routes.py`](../../src/runtime_api/http/retention_routes.py) | Admin CRUD + `/effective`           | **Keep entirely.** The 4 endpoints, the request / response shapes, the auth scopes — all unchanged. The behavior that "POST recomputes the resolver" stays; the recompute now also stamps `retention_until` on affected rows. |
| [`runtime_api/schemas/retention.py`](../../src/runtime_api/schemas/retention.py)         | Request / response Pydantic schemas | **Keep entirely.** `RetentionPolicyUpsertRequest`, `RetentionPolicyView`, `RetentionPolicyListResponse`, `RetentionEffectivePolicyEntry`, `RetentionEffectiveResponse` unchanged.                                             |

### 2.5 Workspace defaults integration (no change)

| File                                                                                                           | Role today                                                               | Refactor verdict                                                                                                                                                                                        |
| -------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/api/workspace_defaults_service.py`](../../src/agent_runtime/api/workspace_defaults_service.py) | Settings retention slider writes 3 ORG-scope policies in one transaction | **Keep entirely.** The slider's behavior is unchanged. The recompute happens as a side effect of `upsert_retention_policy` (via the port change in §2.2), so the slider gains the recompute "for free." |

### 2.6 Privacy override integration (no change)

| File                                                                                                                 | Role today                                                     | Refactor verdict                                                                                                                                                                                   |
| -------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/capabilities/tools/privacy.py`](../../src/agent_runtime/capabilities/tools/privacy.py) (lines 44-46) | Privacy snapshot carries `retention_days` consumed by resolver | **Keep entirely.** The synthesis of USER-scope policies happens inside the resolver. The recompute path (now also called when a privacy snapshot changes — see §2.7) re-stamps affected user rows. |
| [`agent_runtime/api/user_policies_resolver.py`](../../src/agent_runtime/api/user_policies_resolver.py)               | (read-only — no retention logic)                               | **Read only.** No change.                                                                                                                                                                          |

### 2.7 Privacy snapshot recompute trigger (new wire-up)

When a user updates their privacy panel and `retention_days` changes, today nothing happens to existing rows; only future sweep cycles see the new value. After this refactor, existing rows would also keep their old `retention_until` unless we recompute. The fix:

- Add a `recompute_retention_until_for_user(org_id, user_id)` method on the async port.
- Call it from whatever path persists privacy snapshot changes. (Verify in code which path that is — currently it appears to flow through `agent_runtime/api/user_policies_resolver.py` but the actual write path needs grep — see [§Open questions](#7--open-questions).)

### 2.8 Encryption / message-copy interactions (no change, document)

| File                                                                                                    | Role today                                     | Refactor verdict                                                                                                                                                                                                                                |
| ------------------------------------------------------------------------------------------------------- | ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/persistence/encryption.py:394`](../../src/agent_runtime/persistence/encryption.py#L394) | Decrypt path lets non-envelope content through | **Keep entirely.** The placeholder string `'[deleted by retention policy]'` continues to be written by tombstones — encryption fallback already handles it.                                                                                     |
| [`agent_runtime/persistence/message_copy.py:7`](../../src/agent_runtime/persistence/message_copy.py#L7) | Fork path resets `created_at = NOW()`          | **Modify.** Also reset `retention_until` (recompute via resolver scoped to the new conversation_id). Without this, a fork inherits the original's `retention_until` even though `created_at` is reset, leading to inconsistent TTL bookkeeping. |

### 2.9 Tests (will gain coverage)

| File                                                                                                                             | Role today                                                | Refactor verdict                                                                                                                                                                                                                                                                                                                                          |
| -------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`tests/unit/agent_runtime/retention/test_policy_resolver.py`](../../tests/unit/agent_runtime/retention/test_policy_resolver.py) | Resolver precedence + tenant isolation + privacy override | **Modify.** Add: assistant-scope precedence cases (3 — assistant alone, assistant beats user, conversation beats assistant), four-way ordering test, recompute-on-policy-change test (resolver returns same result before / after the upsert helper).                                                                                                     |
| [`tests/unit/runtime_worker/test_retention_sweeper.py`](../../tests/unit/runtime_worker/test_retention_sweeper.py)               | Sweep loop with `_FakePersistence`                        | **Modify.** Add tests for: chunked sweep (LIMIT honored), evidence-row insertion, OTel metric emission, second-pass hard-delete after grace, dry-run produces evidence "would have" without writing the row.                                                                                                                                              |
| [`tests/unit/runtime_api/test_retention_routes.py`](../../tests/unit/runtime_api/test_retention_routes.py)                       | Admin route CRUD                                          | **Modify.** Add tests for: `/v1/retention/effective` happy path, missing scopes (`runtime:use` only — should succeed for /effective, fail for /policies), recompute-fires-on-upsert (use fake adapter that records `recompute_retention_until_for_policy` calls).                                                                                         |
| `tests/integration/postgres/test_retention_postgres_sweep.py` (new)                                                              | n/a                                                       | **Create.** First test that exercises the actual postgres SQL. Spins a docker postgres in CI, applies migrations, seeds 1 org with 100 messages spanning 2 years, runs a 365-day sweep, asserts the right rows tombstoned + evidence row written. Repeat for events / memory_items / context_payloads / checkpoints. Also exercises legal-hold exclusion. |

---

## 3 · Functionalities that must be preserved

Each line below is a behavior the current code provides. The refactor must keep every one. Each gets a pinned test in §6.

### 3.1 Policy storage and CRUD

1. `RetentionPolicyRecord` shape: `(id, org_id, scope, resource_id, kind, ttl_seconds: PositiveInt, created_by_user_id, created_at, updated_at)`. Unchanged.
2. Composite uniqueness: `(org_id, scope, COALESCE(resource_id, ''), kind)`. Same primary unique index.
3. `POST /v1/retention/policies` is idempotent — resubmitting the same `(scope, kind, resource_id)` updates the existing row's `ttl_seconds` and `updated_at`, NOT the `id`.
4. `POST /v1/retention/policies` rejects ORG-scope with non-null `resource_id` (HTTP 400) and non-ORG scope with null `resource_id` (HTTP 400).
5. `DELETE /v1/retention/policies/{id}` removes one row scoped to `(org_id, id)`.
6. `GET /v1/retention/policies` returns all policies for one org, sorted by `created_at ASC`.
7. Tenant isolation: a policy with `org_id=A` is invisible to a query with `org_id=B`. Enforced at SQL WHERE.

### 3.2 Resolver semantics

8. Specificity walk: `CONVERSATION > ASSISTANT > USER > ORG > deployment_default`. First non-empty hit wins.
9. ORG scope ignores `resource_id`. Other scopes require non-null `resource_id`.
10. `ResolvedPolicy(kind, ttl_seconds, source_scope)` returned shape unchanged.
11. `source_scope is None` exactly when the value came from `DEPLOYMENT_DEFAULT_TTL_SECONDS`.
12. Deployment defaults: 365 days for MESSAGES + EVENTS on SaaS profiles; `None` for everything else (no default, no sweep).
13. Privacy user override: per-user `retention_days` from privacy snapshot is layered as a synthetic USER-scope policy across every kind, BUT an explicit C8 USER-scope row for the same `(user_id, kind)` always wins.
14. Per-user override `retention_days <= 0` is silently ignored (no policy synthesized).

### 3.3 Per-kind sweep semantics

15. **MESSAGES tombstone fields:** `status='deleted'`, `content_text='[deleted by retention policy]'`, `content_json='[]'`, `metadata_json='{}'`, `deleted_at=NOW()`. Already-tombstoned rows (`status='deleted'`) are not re-tombstoned.
16. **EVENTS tombstone fields:** `payload_json_redacted='{}'`, `metadata_json_redacted=jsonb_build_object('retention_purged', true)`. Re-running over the same row is a no-op (idempotent JSONB write).
17. **MEMORY_ITEMS tombstone fields:** `deleted_at=NOW()`, `content_summary='[deleted by retention policy]'`. Already-tombstoned rows are not re-tombstoned.
18. **CONTEXT_PAYLOADS:** hard delete, driven by the row's `retention_until` column. (After this refactor, the same pattern applies to messages/events/memory_items, but those tombstone instead of deleting.)
19. **CHECKPOINTS:** hard delete with "keep latest 10 per (thread_id, checkpoint_namespace) within the TTL window." This is the only kind that has shape-specific selection logic; the `LIMIT chunk` pattern preserves it via an inner SELECT with the ROW_NUMBER window.
20. Legal hold exclusion: every kind's sweep skips rows whose containing resource (conversation_id for messages, run_id for events, user_id for memory_items, etc.) has an active `runtime_legal_holds` row (`released_at IS NULL`).

### 3.4 Sweeper loop semantics

21. Off by default. `RETENTION_SWEEP_ENABLED=true` opts in.
22. Default 600s interval. `RETENTION_SWEEP_INTERVAL_SECONDS` overrides.
23. Dry-run mode (`RETENTION_SWEEP_DRY_RUN=true`) logs would-have-been counts and writes no rows.
24. Per-kind failure does not crash the loop — caught, logged at WARNING, loop continues.
25. Stop signal is graceful: the asyncio.Event triggers an early wake from the timeout, the loop exits the `while not self._stop.is_set()` cleanly.
26. Only async backend supports sweep. Sync port (in-memory dev) does not declare these methods.

### 3.5 API surface

27. Four routes: list / upsert / delete / effective.
28. Admin routes (list / upsert / delete) require `admin:retention` + `runtime:use`.
29. `/effective` requires `runtime:use` only — any tenant member can read their org's effective TTL.
30. `/effective` always returns one entry per `RetentionKind` (5 entries), with `ttl_seconds=null` when the resolver returns no value (no policy + no deployment default for that kind).
31. `source_policy_id` populated on `/effective` only when `source_scope='org'` (the FE links it to a specific row).

### 3.6 Cross-cutting

32. Tenant isolation: every query filters by `org_id`.
33. Workspace defaults retention slider writes three policies (`MESSAGES`, `EVENTS`, `CHECKPOINTS`) atomically.
34. Fork preserves the "fork's age" behavior: a forked message's `created_at` is `NOW()` so retention sweeps the fork on its own age, not the original's.
35. Encryption fallback handles tombstone placeholder strings without raising.

---

## 4 · User flows the retention subsystem covers

These are the human-driven and system-driven paths that touch retention. Each must work identically before and after.

### 4.1 Admin sets an org-wide retention policy (settings UI)

1. Admin opens Settings → Privacy & data → Retention slider.
2. FE issues a workspace-defaults PUT that resolves into three `POST /v1/retention/policies` calls (kind = `messages`, `events`, `checkpoints`; scope = `org`; same `ttl_seconds`).
3. Each POST upserts the row and returns the `RetentionPolicyView`.
4. **New behavior:** each upsert triggers `recompute_retention_until_for_policy(org_id, scope='org', resource_id=None, kind)` which UPDATEs every existing row in the affected table for that org with `retention_until = created_at + ttl_seconds * INTERVAL '1 second'` (subject to most-specific resolver — a conversation-scope override on the same kind would win and stay).
5. FE re-fetches `GET /v1/retention/effective` to display the new effective TTL summary; resolver returns the new ORG-scope value.

**Preserved behaviors:** 1, 2, 3, 4, 32, 33.

**New behavior:** existing rows have their `retention_until` recomputed eagerly. Without this, the slider's "set retention to 30 days" wouldn't take effect on existing rows until the next policy-change pass — which currently never happens.

### 4.2 Admin sets a per-conversation override (admin tooling, no UI today)

1. Admin (via API or future tool) issues `POST /v1/retention/policies` with `scope=conversation`, `resource_id=<conversation_id>`, `kind=messages`, `ttl_seconds=...`.
2. POST upserts the row; recompute fires for conversation-scope only — UPDATEs `agent_messages WHERE conversation_id=?`.
3. Subsequent sweeps respect the new TTL on those messages.

**Preserved behaviors:** 1, 2, 3, 4, 8, 9.

### 4.3 Admin deletes a policy

1. Admin issues `DELETE /v1/retention/policies/{id}`.
2. The row is removed.
3. **New behavior:** delete triggers `recompute_retention_until_for_policy(...)` for the (scope, resource_id, kind) that was just removed. The resolver re-runs for affected rows, finds the next-most-specific policy (or deployment default), stamps that.

**Preserved behaviors:** 5.

### 4.4 Member views their effective retention summary (Privacy & data panel)

1. Member opens Privacy & data panel.
2. FE issues `GET /v1/retention/effective?org_id=X`.
3. Route runs the resolver, returns one entry per kind, with `source_scope` so the FE can label "deployment default" vs "your admin set this."
4. FE renders 5 rows: messages, events, context_payloads, checkpoints, memory_items.

**Preserved behaviors:** 27, 28, 29, 30, 31. **Unchanged surface.**

### 4.5 User adjusts personal `retention_days` via the Privacy panel (PR 8.0.5)

1. User opens Privacy panel and sets "Delete my chats after N days."
2. FE persists this in the privacy snapshot.
3. **New behavior:** the persistence path calls `recompute_retention_until_for_user(org_id, user_id)` which:
   - Re-resolves TTL for every row in the affected tables where `user_id = ?` and the row is NOT covered by a more-specific (conversation-scope) policy.
   - UPDATEs `retention_until` accordingly.
4. Subsequent sweeps respect the new value.

**Preserved behaviors:** 13, 14.

**New behavior:** the user's preference takes effect on existing rows immediately, not "next time the resolver happens to run during a sweep."

### 4.6 Sweeper runs (every 10 minutes, all orgs)

1. `RetentionSweeperLoop._run()` wakes after the interval.
2. `sweep_once()` walks `list_retention_orgs()` × `_SWEEP_KINDS` (now 8 kinds — 5 original + 3 TOMBSTONED for the second-pass hard-delete).
3. For each (org, kind):
   - **Old:** load policies, build resolver, resolve TTL, dispatch SQL with `created_at + ttl < NOW()`.
   - **New:** dispatch one SQL with `WHERE retention_until < NOW() ORDER BY retention_until LIMIT chunk`. Loop until the chunk returns 0 rows. No resolver involved.
4. Per-kind: legal-hold exclusion is the same join as today.
5. **New:** each chunk that touched rows writes one `runtime_deletion_evidence` row + emits OTel counter+histogram.
6. Errors are caught, WARNING-logged, loop continues.

**Preserved behaviors:** 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26.

**New behavior:** chunked scans, evidence rows, metrics.

### 4.7 Tombstoned row hard-deletes after grace (new — implements documented promise)

1. The new sweeper kind `MESSAGES_TOMBSTONED` runs the same loop.
2. SQL: `DELETE FROM agent_messages WHERE status='deleted' AND deleted_at < NOW() - INTERVAL '30 days' AND NOT EXISTS (legal_hold)`.
3. Same chunking, same legal-hold exclusion, same evidence row insertion, same metric emission.
4. Repeat for `EVENTS_TOMBSTONED`, `MEMORY_ITEMS_TOMBSTONED`.

**Preserved behaviors:** 20.

**New behavior:** the documented hard-delete grace becomes real.

### 4.8 Conversation forks (existing fork flows)

1. User forks a conversation.
2. `message_copy` resets `created_at = NOW()` so retention treats the fork on its own age.
3. **New behavior:** also resets `retention_until`. Computed from the resolver scoped to the new conversation (i.e. if no conversation-scope override exists for the fork, falls back to user / org / default).

**Preserved behaviors:** 34.

### 4.9 Legal hold attaches mid-life

1. Operator inserts a `runtime_legal_holds` row for `(org_id, scope='conversation', resource_id=<id>, released_at=NULL)`.
2. The next sweep tick excludes all rows under that hold.
3. When `released_at` is set, the next sweep includes them again. If a held row's `retention_until < NOW()` by that point, it is swept on the next pass.

**Preserved behaviors:** 20.

### 4.10 Compliance reviewer asks "what was deleted?"

1. Reviewer queries `runtime_deletion_evidence` directly (or via SIEM export — out of scope).
2. Each row carries: `(org_id, user_id, created_at, sweep_run_id, kind, tombstoned_count, hard_deleted_count, dry_run flag, source_scope_summary)`.
3. Reviewer can correlate to the policy that drove the deletion via the `policy_id_at_sweep_time` field.

**Preserved behaviors:** none — this is a brand-new flow that the schema declared but the code never serviced.

---

## 5 · Refactor plan

### 5.1 Goals

- **Move TTL evaluation from sweep-time to write-time / policy-change-time.** The resolver still exists and is canonical; it just runs less often.
- **Make the sweep SQL trivial and uniform.** One shape: "find rows where `retention_until < NOW()` and not legal-held, batched."
- **Implement the documented but missing 30-day grace then hard-delete** for tombstoned MESSAGES / EVENTS / MEMORY_ITEMS.
- **Write `runtime_deletion_evidence` rows on every sweep** so the compliance answer "what was deleted?" is queryable in SQL, not log scraping.
- **Emit OTel metrics** (`retention_swept_rows_total{kind, action, dry_run}`, `retention_sweep_duration_seconds{kind}`).
- **Cover the postgres SQL with integration tests** — first ones to exist for this subsystem.
- **Cover the assistant scope** in resolver tests.
- **No public API surface change. No frontend wire change. No new env var beyond a chunk-size config.**

### 5.2 Non-goals

- **Switching to `pg_partman` partitioning.** Tracked as Phase 5 follow-up after the simpler refactor proves out. Reasoning in §2.3.
- **Removing tombstone semantics for messages / events / memory_items.** Compliance evidence requires the tombstone phase.
- **Replacing the 5-level resolver with something simpler.** The five levels reflect real product requirements (per-conversation overrides, per-user privacy, per-assistant skin tone for retention, org defaults, deployment defaults). The resolver itself is good code.
- **Surfacing per-resource overrides in `/effective`.** Out of scope; this is a deliberate product decision in [retention_routes.py:71-74](../../src/runtime_api/http/retention_routes.py#L71).
- **Removing the opt-in flag `RETENTION_SWEEP_ENABLED`.** Stay opt-in. Customers who haven't reviewed the policy table shouldn't have rows tombstoned on upgrade.

### 5.3 Phased rollout

Each phase ships independently; each is reversible until the next one builds on it.

#### Phase 1 — Evidence + metrics on the existing sweeper (no behavior change)

**Why first:** zero schema risk, immediate visibility win, lets us measure the "before" state.

- Modify `RetentionSweeperLoop.sweep_once()` to:
  - Wrap each `sweep_retention_kind` call with an OTel timer.
  - Accept a returned `RetentionSweepOutcome` (already exists) and emit `retention_swept_rows_total{kind, action='tombstone'|'delete', dry_run}` counter.
  - On non-zero outcome, insert one `runtime_deletion_evidence` row.
- Add `RetentionDeletionEvidenceRecord` to records.
- Add a port method `insert_retention_deletion_evidence(record)` on `AsyncPersistencePort`. Implement on both adapters.
- Tests: sweeper test asserts evidence row is inserted on non-empty outcome and is NOT inserted on empty outcome.

**Surface modified:** `runtime_worker/jobs/retention_sweeper.py`, `agent_runtime/persistence/records/retention.py`, `agent_runtime/api/async_ports.py`, both adapters, sweeper tests.

**Rollout safety:** opt-in flag remains; behavior of existing sweep is unchanged. Even with `RETENTION_SWEEP_DRY_RUN=true` we'll write evidence rows tagged `dry_run=true`.

#### Phase 2 — Add `retention_until` columns + backfill (no app behavior change yet)

**Why second:** schema migration with no logic dependency. Backfill is idempotent.

- New migration `0027_retention_until_columns.sql`:
  - `ALTER TABLE agent_messages ADD COLUMN retention_until TIMESTAMPTZ`.
  - `ALTER TABLE runtime_events ADD COLUMN retention_until TIMESTAMPTZ`.
  - `ALTER TABLE runtime_memory_items ADD COLUMN retention_until TIMESTAMPTZ`.
  - `CREATE INDEX CONCURRENTLY idx_<table>_retention_until_active ON <table>(org_id, retention_until) WHERE retention_until IS NOT NULL`. Partial — keeps the index small.
- New migration `0028_retention_until_backfill.sql`:
  - Idempotent (UPDATE only WHERE retention_until IS NULL).
  - For each org, build a temp table from `retention_policies`, then a CTE that resolves the most-specific TTL per (kind, resource), then `UPDATE` the affected table.
  - Fix-up note: this is an unbounded UPDATE — for shops with very large tables we'll need to chunk. Phase-2 ships with chunking baked in (LIMIT N within a loop), driven by a config var `RETENTION_BACKFILL_CHUNK=10000`.

**Surface modified:** new migrations only.

**Rollout safety:** column additions are backwards-compatible; existing code is unaware of the new column. The backfill writes data but no read path consumes it yet. If the backfill is paused / killed mid-run, re-running picks up where it left off.

#### Phase 3 — Stamp `retention_until` at write time + on policy change (still no sweep change)

**Why third:** the column starts being maintained correctly going forward. Sweep still uses old SQL, so behavior stays identical.

- App code stamps `retention_until` on insert into `agent_messages`, `runtime_events`, `runtime_memory_items`. Done by the existing repository write path — needs the resolver.
- New port method `recompute_retention_until_for_policy(org_id, scope, resource_id, kind)` and `recompute_retention_until_for_user(org_id, user_id)`.
- `upsert_retention_policy` and `delete_retention_policy` call the recompute method as part of the same transaction.
- Privacy snapshot persistence path calls `recompute_retention_until_for_user` (find this path — see [§7](#7--open-questions)).
- `message_copy.py` resets `retention_until` along with `created_at`.
- Tests: every write path that inserts into those three tables sets `retention_until` (or NULL when resolver returns no value); upsert / delete on policies fires the recompute; privacy snapshot change fires the recompute.

**Surface modified:** every insert site for the three tables; both adapters' `upsert_retention_policy` / `delete_retention_policy`; `message_copy.py`; the privacy snapshot persistence path.

**Rollout safety:** the sweep still uses `created_at + ttl < NOW()`. Even if the new column gets stamped wrong for some edge case, the sweep is unaffected. We can run Phase 3 in production for a week and compare `retention_until` to the live resolver's answer for spot-check confidence.

#### Phase 4 — Switch the sweep SQL to read `retention_until` + chunk

**Why fourth:** the column is now reliably populated. Cutover is safe.

- `_sweep_messages` / `_sweep_events` / `_sweep_memory_items` SQL becomes:
  ```sql
  WITH due AS (
    SELECT id FROM <table>
     WHERE org_id = ?
       AND retention_until IS NOT NULL
       AND retention_until < NOW()
       AND <kind-specific not-already-tombstoned filter>
       AND NOT EXISTS (legal_hold subquery)
     ORDER BY retention_until
     LIMIT ?
  )
  UPDATE <table> SET <tombstone columns> FROM due WHERE <table>.id = due.id
  RETURNING <table>.id;
  ```
  (Or `DELETE` with the same CTE for `context_payloads` / `checkpoints`.)
- Loop in the sweeper: call once with chunk_size, repeat until rows-affected = 0 (per kind, per org).
- The CHECKPOINTS "keep latest 10 per (thread_id, namespace)" rule stays — it's not driven by `retention_until`. CHECKPOINTS sweep keeps its bespoke SQL but gains chunking.
- Remove resolver invocation from the sweep loop.
- Tests: chunked sweep terminates; LIMIT honored; legal hold still excluded; tombstone semantics identical to old SQL (golden-test against Phase 1 outputs).

**Surface modified:** `runtime_adapters/postgres/runtime_api_store.py` (the four affected `_sweep_*` methods); `runtime_worker/jobs/retention_sweeper.py` (loop until zero rows); both in-memory adapters (in-memory sweep also reads `retention_until`).

**Rollout safety:** behavior change but mechanical. Compare evidence-row counts pre / post — should be identical for runs that don't span a policy change. For runs that span a policy change, post-refactor will sweep fewer rows (only those whose `retention_until` was recomputed in Phase 3) — that's the correct new behavior; document it.

**Reversibility:** keep Phase 1's SQL paths under a feature flag (`RETENTION_SWEEP_USE_RETENTION_UNTIL=true`) for one release. Default true once we're confident; remove the flag in the release after.

#### Phase 5 — Implement the 30-day grace then hard-delete

**Why fifth:** independent of Phase 4 mechanics; can be Phase 4.5 if Phase 4 finishes early.

- Add `RetentionKind.MESSAGES_TOMBSTONED`, `EVENTS_TOMBSTONED`, `MEMORY_ITEMS_TOMBSTONED` to the enum.
- Add three new `_sweep_*_tombstoned` methods that hard-delete rows where `<tombstone_marker> = true AND deleted_at < NOW() - INTERVAL '30 days' AND NOT EXISTS (legal_hold)`.
- Wire them into `_SWEEP_KINDS`.
- Grace period configurable per kind via `RETENTION_TOMBSTONE_GRACE_DAYS_<kind>` env (default 30).
- Tests: tombstoned + 31 days old → hard-deleted; tombstoned + 29 days old → not deleted; legal hold attached on day 25 → not deleted on day 31.

**Surface modified:** records, sweeper, postgres adapter, both in-memory adapters, sweeper tests.

**Rollout safety:** opt-in within opt-in. Default `RETENTION_TOMBSTONE_GRACE_DAYS_MESSAGES=0` keeps behavior identical to today (no second-pass delete); flipping to 30 enables the documented behavior. Customers can choose when to enable.

#### Phase 6 — Postgres integration tests

Already mentioned in §2.9. Lands as part of Phase 1 → 4 progression but the bulk of the SQL coverage lives here.

#### Phase 7 (deferred — future work) — `pg_partman` for `runtime_events`

Not in this PRD's scope. Note in the spec for future consideration once event volumes pass 50M / org. Partition strategy: monthly, drop partitions where ALL rows have `retention_until < NOW() - 30 days` AND no legal-hold ever attached.

### 5.4 Why this approach (the alternatives we considered)

| Alternative                                                     | Pros                       | Cons                                                                                                                                               | Decision                                                                            |
| --------------------------------------------------------------- | -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `pg_partman` partitioning instead of `retention_until` columns  | DB-native TTL; cheap drops | Hard delete only (loses tombstone evidence); legal hold mid-partition forces per-row filter anyway; checkpoints' "keep latest 10" rule doesn't fit | Rejected for this refactor; tracked as Phase 7                                      |
| Postgres `tg_*` triggers to set `retention_until` on INSERT     | Removes app-level stamping | Hides logic; schema migration to install triggers; harder to test                                                                                  | Rejected — keeping it in app code makes the resolver path obvious                   |
| Store the resolved TTL on each policy row (denormalize)         | Even simpler sweep         | Stamping at write time is identical effort and avoids the "recompute on policy change" being a separate code path                                  | Rejected — `retention_until` is per-row and direct; the policy row stays normalized |
| Job framework (Celery / dramatiq / arq) instead of asyncio loop | Reuses external scheduler  | Adds infra; the existing loop works; not the bottleneck                                                                                            | Rejected — out of scope                                                             |
| Just delete the sweeper and require customers to ship their own | Smallest code footprint    | Compliance regression — the product loses a feature buyers ask about                                                                               | Rejected                                                                            |

### 5.5 What we are NOT changing

- The 5-level resolver code, its precedence order, its privacy-override layering.
- The HTTP API: paths, methods, request / response shapes, auth scopes.
- The opt-in flag `RETENTION_SWEEP_ENABLED`, default false.
- Tombstone-vs-hard-delete-per-kind. The kinds that tombstone today still tombstone after the refactor; the kinds that hard-delete today still hard-delete.
- Legal-hold semantics: same join, same `released_at IS NULL` filter, same scope mapping.
- The `/effective` endpoint's intentional choice not to surface per-resource overrides.

---

## 6 · Acceptance criteria

### 6.1 Behavioral parity

- [ ] Every test in [tests/unit/agent_runtime/retention/test_policy_resolver.py](../../tests/unit/agent_runtime/retention/test_policy_resolver.py) passes unchanged.
- [ ] Every test in [tests/unit/runtime_worker/test_retention_sweeper.py](../../tests/unit/runtime_worker/test_retention_sweeper.py) passes unchanged.
- [ ] Every test in [tests/unit/runtime_api/test_retention_routes.py](../../tests/unit/runtime_api/test_retention_routes.py) passes unchanged.
- [ ] All 35 preserved behaviors enumerated in §3 have a pinned test (existing or new); each is named in the PR description.

### 6.2 New behavior

- [ ] `runtime_deletion_evidence` rows are written on every non-empty sweep outcome (verifiable by integration test count).
- [ ] OTel metrics `retention_swept_rows_total` and `retention_sweep_duration_seconds` are emitted (verifiable by metric introspection in the test).
- [ ] `retention_until` is set on insert for `agent_messages`, `runtime_events`, `runtime_memory_items` (verifiable by writing a row and reading the column).
- [ ] `recompute_retention_until_for_policy` is called from `upsert_retention_policy` and `delete_retention_policy` (verifiable by spy in unit test).
- [ ] `recompute_retention_until_for_user` is called from the privacy snapshot persistence path (verifiable by spy).
- [ ] `MESSAGES_TOMBSTONED` / `EVENTS_TOMBSTONED` / `MEMORY_ITEMS_TOMBSTONED` second-pass hard-delete works when grace > 0; is a no-op when grace = 0 (default).

### 6.3 Quality

- [ ] At least one integration test per kind exercises the actual postgres SQL (against a docker postgres in CI). Five integration tests minimum.
- [ ] Backfill migration is idempotent (re-running on a backfilled DB is a no-op).
- [ ] No regression in sweep duration on a representative test fixture (1 org × 100k rows × 5 kinds). Compare Phase 1 baseline (logged metric) to Phase 4 implementation.
- [ ] No new env vars besides `RETENTION_BACKFILL_CHUNK` and `RETENTION_TOMBSTONE_GRACE_DAYS_<kind>`.

### 6.4 Documentation

- [ ] [10-agent-runtime-persistence-spec.md](../specs/10-agent-runtime-persistence-spec.md) updated to describe the new columns + the recompute behavior.
- [ ] [11-persistence-org-scoping-audit.md](../specs/11-persistence-org-scoping-audit.md) updated if the audit story changes (it shouldn't — `org_id` filter remains everywhere).
- [ ] [refactor-audit.md §1.5](../architecture/refactor-audit.md#15-custom-retention-sweep--5-level-policy-resolver) marked as "Phases 1–6 landed" once shipped.

---

## 7 · Open questions

These need code-level verification before / during implementation. Each blocks at most one phase.

1. **Privacy snapshot persistence path.** The resolver consumes `privacy_user_retention_days`, but where does that snapshot get persisted today? `agent_runtime/api/user_policies_resolver.py` reads it; the write path is unclear from what I've read. Phase 3 needs to know which file to modify to call `recompute_retention_until_for_user`. **Blocking Phase 3.**

2. **Multi-statement transaction for upsert + recompute.** Should the recompute happen in the same DB transaction as the policy upsert, or after-commit? Same-transaction is correct (atomic) but holds locks longer. Recommendation: same transaction with a small `LIMIT chunk` recompute; if the recompute is large, do same-transaction up to N rows then commit-and-continue with another transaction. **Blocking Phase 3 implementation.**

3. **`agent_messages` insert sites.** How many places insert into `agent_messages`? Need to grep for `INSERT INTO agent_messages` and the equivalent ORM-style writers. Each one needs to stamp `retention_until`. A wrapper at the persistence-port level would be safer than per-call-site. **Blocking Phase 3 design.**

4. **CHECKPOINTS rule applicability.** The "keep latest 10 per (thread_id, namespace)" rule is currently enforced in the sweep SQL. With `retention_until` stamped at write, does this rule belong on the column (i.e. only the 11th-latest gets a `retention_until`)? Or does it stay in the sweep query? Recommendation: keep in sweep SQL — the rule is structural, not policy-driven. **Blocking Phase 4.**

5. **Backfill blast radius.** Migration 0028 is potentially unbounded. For a customer with 100M rows in `runtime_events`, the backfill could take hours. Is that acceptable as part of upgrade? Or should the backfill run as a worker job over multiple days? Recommendation: ship as a chunked worker job (`RetentionBackfillJob`) opt-in via `RETENTION_BACKFILL_ENABLED=true`, separate from the migration. The migration just adds the column. **Blocking Phase 2 design.**

6. **Dry-run interaction with `runtime_deletion_evidence`.** Should dry-run write evidence rows tagged `dry_run=true`? Recommendation: yes — it's the only audit trail for "what would have been deleted." **Blocking Phase 1 design.**

7. **Per-tenant chunk size.** Should chunk_size be configurable per-org? Some tenants may want larger chunks for faster throughput, others smaller for less lock contention. Recommendation: not in this PRD; fixed default with env override. Per-tenant tuning is a future feature if needed. **Non-blocking.**

8. **Workspace-defaults race with admin policy upserts.** If an admin uses the slider AND another admin uses the API to set a different policy at the same time, today both get `ON CONFLICT DO UPDATE` and the last writer wins. Same after refactor. Document but don't change. **Non-blocking.**

---

## 8 · Risks

| Risk                                                                                | Likelihood | Impact | Mitigation                                                                                                                                                                                      |
| ----------------------------------------------------------------------------------- | ---------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backfill (0028) blocks production for large tenants                                 | Medium     | High   | Ship backfill as separate opt-in worker job with chunked progress; not part of the migration's RUN block                                                                                        |
| `retention_until` stamping omitted from a write site → those rows are never swept   | Medium     | Medium | Add a Phase-3 invariant test that runs after every write path's tests: scan the table after write and assert `retention_until IS NOT NULL` (where the resolver returned a value)                |
| Recompute on policy upsert holds row locks too long, blocks user writes             | Medium     | Medium | Chunk the recompute (`LIMIT N`) within the upsert transaction; commit and continue if N is reached                                                                                              |
| Phase 4 sweep SQL behaves differently from Phase 1 (golden-test miss)               | Low        | High   | Run both in shadow during Phase 4 rollout; compare evidence-row counts and outcomes; only flip the flag once shadow agrees for a week                                                           |
| Tombstoned row hard-delete (Phase 5) deletes something a customer expected to keep  | Low        | High   | Default grace = 0 (off). Only enable on customer request; document as a destructive operation. Optionally require a second flag `RETENTION_HARD_DELETE_CONFIRMED=true` to enable beyond grace=0 |
| `MESSAGES_TOMBSTONED` second-pass deletes rows that the first pass didn't tombstone | Very Low   | Medium | Hard-delete WHERE clause includes the tombstone-marker column so it can never delete a non-tombstoned row                                                                                       |
| Privacy snapshot recompute fires on every keystroke if the path is naive            | Medium     | Low    | Recompute should fire only on persisted change to `retention_days`; debounce / equality-check in the persistence path                                                                           |
| Composite unique key collision because we add new kinds (`*_TOMBSTONED`)            | None       | None   | The TOMBSTONED kinds exist only on the sweep dispatch enum, NOT on the `retention_policies` table — no row ever has `kind='messages_tombstoned'`. Document this clearly                         |
| Test infra cost (docker postgres in CI)                                             | Low        | Low    | Use the existing test postgres harness if one exists; otherwise the integration tests run only on a `tests/integration/` path that's opt-in for local + always-on for CI                        |

---

## 9 · Unit testing requirements

### 9.1 Resolver (`tests/unit/agent_runtime/retention/test_policy_resolver.py`)

Existing tests stay. Add:

- **Assistant scope:**
  - `assistant_policy_used_when_no_conversation`
  - `conversation_beats_assistant`
  - `assistant_beats_user`
  - `assistant_beats_org`
- **Four-way precedence:** create one conversation, one assistant, one user, one org policy on the same kind; assert conversation wins.
- **Recompute helper purity:** the recompute function should be deterministic — calling it twice with the same inputs returns the same `retention_until` for every row.

### 9.2 Sweeper loop (`tests/unit/runtime_worker/test_retention_sweeper.py`)

Existing tests stay. Add:

- `evidence_row_inserted_on_non_empty_outcome`
- `evidence_row_not_inserted_on_zero_outcome`
- `dry_run_writes_evidence_row_with_dry_run_flag`
- `chunked_sweep_loops_until_zero_rows` (mock adapter returns 100 rows, then 100, then 0; assert 3 calls)
- `otel_metric_emitted_per_kind` (counter incremented; histogram observed)
- `tombstoned_kinds_in_sweep_kinds_when_grace_configured`
- `tombstoned_sweep_skipped_when_grace_zero`

### 9.3 Routes (`tests/unit/runtime_api/test_retention_routes.py`)

Existing tests stay. Add:

- `effective_route_returns_one_entry_per_kind`
- `effective_route_member_scope_succeeds_admin_routes_fail_when_only_runtime_use`
- `upsert_calls_recompute_retention_until` (spy adapter)
- `delete_calls_recompute_retention_until` (spy adapter)
- `effective_with_no_org_policy_returns_deployment_default_with_source_scope_null`

### 9.4 Adapters (`tests/unit/runtime_adapters/...`)

Existing in-memory adapter tests stay. Add:

- In-memory:
  - `insert_into_agent_messages_stamps_retention_until` (with org policy)
  - `insert_with_no_policy_leaves_retention_until_null`
  - `recompute_retention_until_updates_existing_rows`
  - `legal_hold_excluded_from_sweep` (in-memory implements the filter)
- Postgres (NEW — first SQL-level tests):
  - `tests/integration/postgres/test_retention_postgres_sweep.py` covering: messages tombstone, events tombstone, context_payloads delete, checkpoints delete with keep-10 rule, memory_items tombstone, legal-hold exclusion, chunk loop, dry-run rolls back.

### 9.5 Privacy override + recompute integration

- `privacy_snapshot_change_calls_recompute_retention_until_for_user`
- `privacy_retention_days_zero_does_not_trigger_recompute`
- `c8_user_policy_beats_privacy_override_in_recompute`

### 9.6 Fork

- `fork_resets_both_created_at_and_retention_until`
- `fork_with_conversation_scope_policy_uses_fork_conversation_id_for_resolver`

---

## 10 · Diff summary (estimate)

Approximate LOC change once all phases land:

| File                                                               | Net                                                                                                         |
| ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| `agent_runtime/retention/policy_resolver.py`                       | 0 (untouched logic; same lines)                                                                             |
| `agent_runtime/persistence/records/retention.py`                   | +30 (new TOMBSTONED kinds, evidence record)                                                                 |
| `runtime_worker/jobs/retention_sweeper.py`                         | +60 (chunk loop, evidence write, metric emission) / -30 (resolver removal from per-tick path)               |
| `runtime_adapters/postgres/runtime_api_store.py`                   | +120 (recompute methods, evidence insert, new SQL paths) / -180 (per-kind sweep SQLs collapse to one shape) |
| `runtime_adapters/in_memory/runtime_api_store.py`                  | +50 (stamping + recompute)                                                                                  |
| `runtime_adapters/in_memory/async_runtime_api_store.py`            | +60 (real sweep instead of stub)                                                                            |
| `agent_runtime/api/async_ports.py`                                 | +20 (new methods)                                                                                           |
| `agent_runtime/api/ports.py`                                       | +10                                                                                                         |
| `agent_runtime/persistence/message_copy.py`                        | +5                                                                                                          |
| New: `migrations/0027_retention_until_columns.sql`                 | +30                                                                                                         |
| New: `migrations/0028_retention_until_backfill.sql`                | +60                                                                                                         |
| New: `tests/integration/postgres/test_retention_postgres_sweep.py` | +400                                                                                                        |
| Existing tests (resolver, sweeper, routes)                         | +200 (added cases)                                                                                          |

Net: roughly +800 LOC of tests, +200 LOC of production code, -200 LOC of replaced production code. Behavior surface: identical except for evidence rows + grace-period hard delete (both opt-in).

---

## 11 · Out of scope (explicitly)

- `pg_partman` partitioning — Phase 7 future work.
- Per-tenant chunk-size tuning — future work.
- Surfacing per-resource overrides in `/v1/retention/effective` — deliberate product decision.
- Replacing the asyncio-loop with an external scheduler — future work.
- Adding new retention kinds (e.g. `tool_invocations`, `subagent_records`) — separate PRDs if needed.
- Removing the `RETENTION_SWEEP_ENABLED` opt-in — stays opt-in.
- Cross-tenant retention reporting — out of scope; SIEM consumes evidence rows.
- A Pydantic-2 strict mode pass on retention records — not retention-specific; lands separately.

---

_This PRD reflects the documented behavior and the code as of 2026-05-10. Update or supersede when implementation begins._
