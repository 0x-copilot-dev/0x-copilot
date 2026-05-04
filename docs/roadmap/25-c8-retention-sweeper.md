# PR 25 — C8: Retention Sweeper + Checkpoint Pruning

**Spec ID:** C8 | **Track:** Deployment & DB | **Wave:** 7 (Operations) | **Estimated effort:** L
**Depends on:** C2
**Required for:** all bank/gov deploys with mandated data retention

---

## 1. Functional Specification

### 1.1 Goal

The schema already has `runtime_context_payloads.retention_until` and `runtime_legal_holds` and `runtime_deletion_evidence` — but no job actually enforces retention. `runtime_checkpoints` grows unbounded. This PR adds a per-tenant policy table, a background sweeper, and tombstone-then-hard-delete semantics that respect legal holds and write deletion evidence.

### 1.2 User-visible behavior

- **Org admin:** sets retention policies per kind (messages, events, context_payloads, checkpoints, memory_items) per scope (org/user/conversation/assistant).
- **End user:** old conversations gracefully archive; legal-hold conversations preserved.
- **Operator:** `runtime_deletion_evidence` populated for every batch.

### 1.3 Out of scope

- Per-row retention overrides (only per-policy).
- Cold-storage archival (out of scope; left as a hook).
- Customer-facing "Restore deleted conversation" UI.

---

## 2. Technical Specification

### 2.1 Architecture

- New `retention_policies` table; per (org, scope, resource_id, kind), defines TTL.
- Most-specific policy wins: conversation > user > org > deployment-default.
- Deployment defaults: SaaS = 365d for messages/events; single_tenant = no default (no-op until customer sets policies).
- Sweeper runs every `RETENTION_SWEEP_INTERVAL_SECONDS` (default 600).
- Per-table strategy:
  - `runtime_context_payloads`: hard delete where `retention_until < now()`.
  - `runtime_checkpoints`: keep latest N per `(thread_id, namespace)` (default 10) + anything in policy window.
  - `runtime_events`, `agent_messages`: tombstone (status='deleted', content blanked) first; hard delete after grace period (default 30d). Preserves audit invariants.
- Respects `runtime_legal_holds`: held resources are skipped; no deletion_evidence row.

### 2.2 Schema changes

Migration `services/ai-backend/migrations/0011_retention_policies.sql`:

```sql
CREATE TABLE retention_policies (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL,
    scope        TEXT NOT NULL CHECK (scope IN ('org','user','conversation','assistant')),
    resource_id  TEXT,                                  -- NULL when scope='org'
    kind         TEXT NOT NULL CHECK (kind IN ('messages','events','context_payloads','checkpoints','memory_items')),
    ttl_seconds  BIGINT NOT NULL CHECK (ttl_seconds > 0),
    created_at   TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX idx_retention_policies_unique
    ON retention_policies (org_id, scope, COALESCE(resource_id, ''), kind);
```

### 2.3 Endpoints

- `GET /v1/retention/policies?org_id=` (admin)
- `POST /v1/retention/policies` (admin)
- `PATCH /v1/retention/policies/{id}` (admin)
- `DELETE /v1/retention/policies/{id}` (admin)

(Admin scope per A10. Until A10, soft-check via roles header.)

### 2.4 Code changes

**New** `services/ai-backend/src/runtime_worker/jobs/retention_sweeper.py`:

- Per kind, per org:
  1. Resolve effective TTL by walking specificity (conversation → user → org → deployment default).
  2. Find candidate rows older than TTL.
  3. Filter out resources with active legal holds.
  4. Tombstone or delete (per kind strategy).
  5. Write `runtime_deletion_evidence` row (counts) + `runtime_audit_log` row.
- Chunked (default 1000 rows per batch); rate-limited; resumable.
- Hooked into `runtime_worker/loop.py` as a separate cron-like driver.

**New** `services/ai-backend/src/agent_runtime/retention/policy_resolver.py`:

- Given `(org_id, conversation_id|user_id|assistant_id|None, kind)`, returns effective `ttl_seconds`.

**Per-kind handlers:**

- `services/ai-backend/src/runtime_worker/jobs/retention/messages.py` — tombstone with content blanking.
- `services/ai-backend/src/runtime_worker/jobs/retention/events.py` — tombstone.
- `services/ai-backend/src/runtime_worker/jobs/retention/context_payloads.py` — hard delete (no audit need; storage URI only).
- `services/ai-backend/src/runtime_worker/jobs/retention/checkpoints.py` — keep latest N + window.
- `services/ai-backend/src/runtime_worker/jobs/retention/memory_items.py` — tombstone via `deleted_at`.

**Endpoints:** `services/ai-backend/src/runtime_api/http/retention_routes.py` — admin CRUD.

### 2.5 Trust model & failure semantics

- Sweeper failure (DB error mid-batch) → next run resumes from cursor; partial progress recorded.
- Legal hold check is per-resource per-batch; hold added during sweep is honored next iteration.
- Hard-delete grace defaults conservative (30d).

### 2.6 Tenant isolation

- Policies scoped by `org_id`; sweeper iterates per `org_id`.
- Can never delete one org's data based on another org's policy.

### 2.7 Observability

- Audit: `retention.policy.created/updated/deleted`, `retention.swept{kind, count}`.
- Metrics: `retention_sweep_rows_total{kind, action=tombstoned|deleted|skipped_legal_hold}`, `retention_sweep_duration_seconds{kind}`.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] Set policy `messages: ttl=7d`; age messages 8d; sweeper tombstones them.
- [ ] Place legal hold on a conversation; sweeper skips it; no `runtime_deletion_evidence` row written for held items.
- [ ] Tombstone preserves audit chain: `runtime_audit_log` rows referencing tombstoned messages remain intact.
- [ ] org_a's policy never deletes org_b's data.
- [ ] Sweeper is idempotent — running twice in a row deletes nothing the second time.

### 3.2 Test plan

**Unit:**

- Policy resolution: most-specific wins.
- Per-kind handler correctness.

**Integration:**

- Full sweep with mixed policies + holds.
- Restore drill: pg_dump → run sweeper → restore from dump → sweeper produces same delete set.

**Tenant-isolation:**

- Cross-tenant policy leakage test.

**Concurrency:**

- Two sweepers running in parallel respect per-org cursor and don't double-process (advisory lock per org).

### 3.3 Compliance evidence produced

- `runtime_deletion_evidence` populated for every delete batch.
- Per-tenant policy documented; defaults per profile.
- Test asserts no audit rows are deleted.

### 3.4 Rollout plan

- Sweeper disabled by default (no policies seeded ⇒ no-op).
- Per-deployment opt-in via seed migration.

### 3.5 Backout plan

Stop the sweeper.

### 3.6 Definition of done

- [ ] Migration 0011 applied.
- [ ] Sweeper running.
- [ ] Per-kind handlers + tests.
- [ ] Admin endpoints live.
- [ ] `docs/security/data-retention.md` written.

---

## 4. Critical files

- New: `services/ai-backend/migrations/0011_retention_policies.sql` (+ rollback)
- New: `services/ai-backend/src/runtime_worker/jobs/retention_sweeper.py`
- New: `services/ai-backend/src/runtime_worker/jobs/retention/{messages,events,context_payloads,checkpoints,memory_items}.py`
- New: `services/ai-backend/src/agent_runtime/retention/policy_resolver.py`
- New: `services/ai-backend/src/runtime_api/http/retention_routes.py`
- Modify: [services/ai-backend/src/runtime_worker/loop.py](../../services/ai-backend/src/runtime_worker/loop.py) — start sweeper.
- New: `docs/security/data-retention.md`
