# PR 30 — C12: Backup/Restore Documented and Tested in CI

**Spec ID:** C12 | **Track:** Deployment & DB | **Wave:** 8 (RBAC + Restore) | **Estimated effort:** M
**Depends on:** C2 (migrations) and ideally all schema changes have landed
**Required for:** all production deploys

---

## 1. Functional Specification

### 1.1 Goal

Document and _test_ the backup-and-restore procedure. Without a tested restore, "we have backups" is not a control. CLAUDE.md compliance review explicitly says "A control counts as implemented only when code, config, tests, and docs all support it."

### 1.2 User-visible behavior

- **Operator:** has a runbook covering each deployment profile.
- **CI:** runs a weekly restore drill against a tiny `pg_dump` fixture; failure pages on-call.
- **Auditor:** can ask "show me your restore drill" and we have a green CI run.

### 1.3 Out of scope

- Cross-region failover.
- Continuous PITR for all customers (we document; provisioning is per-deploy).
- DR exercises beyond restore.

---

## 2. Technical Specification

### 2.1 Architecture

Three deploy profiles → three restore approaches:

- **SaaS multi-tenant:** AWS RDS PITR (cloud-native point-in-time recovery).
- **Single-tenant managed (Helm/K8s):** `pg_basebackup` + WAL archiving to customer object storage.
- **Single-tenant self-hosted (Compose):** volume snapshot via OS / hypervisor.

CI exercises (3) with a tiny fixture. (1) and (2) covered by runbook + customer-side validation.

### 2.2 Schema changes

None.

### 2.3 Endpoints

None.

### 2.4 Code changes

**New CI workflow** `.github/workflows/postgres-restore-drill.yml`:

- Trigger: manual + scheduled weekly.
- Steps:
  1. Boot Postgres in container.
  2. Restore from `tests/fixtures/postgres-restore/baseline.dump.sql.gz`.
  3. Run `services/<svc>/scripts/restore_smoke.py` per service:
     - SELECT count(\*) per table; compare to `tests/fixtures/postgres-restore/manifest.yaml`.
     - Run RLS isolation smoke (small subset of C5 tests).
     - Run sweeper in dry-run mode (C8) to verify no policies trigger unexpected deletes.
  4. Fail workflow if any check fails.

**New scripts:**

- `services/backend/scripts/restore_smoke.py`
- `services/ai-backend/scripts/restore_smoke.py`

**New runbook** `docs/ci-cd/runbooks/postgres-restore.md`:

- Section per profile.
- For each: PITR enable steps, WAL archiving cadence, RPO/RTO targets, exact commands.
- Post-restore validation checklist:
  - Run sweeper dry-run.
  - Run RLS isolation smoke.
  - Verify session count = expected.
  - Verify worker can claim outbox.
  - Verify ai-backend `/v1/health` reports profile correctly.

**Backup fixture:**

- `tests/fixtures/postgres-restore/baseline.dump.sql.gz` — small (~1MB), version-controlled.
- `tests/fixtures/postgres-restore/manifest.yaml` — expected counts:
  ```yaml
  agent_conversations: 5
  agent_messages: 23
  agent_runs: 12
  runtime_events: 187
  ...
  ```
- Updated whenever a migration changes schema (CI fails if drift not committed).

### 2.5 Trust model & failure semantics

- Restore drill failure → page on-call.
- Manifest drift → CI fails with helpful diff message.

### 2.6 Tenant isolation

N/A directly. The fixture includes 2 orgs; smoke test asserts isolation post-restore.

### 2.7 Observability

- CI history is the observability surface.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] CI workflow runs to green at least once on the fixture.
- [ ] Manifest matches actual fixture counts (drift fails CI).
- [ ] Runbook covers all three profiles.
- [ ] Runbook includes documented RPO/RTO per profile.
- [ ] Post-restore validation checklist works on the fixture.

### 3.2 Test plan

**CI:**

- Workflow runs on every PR that touches `tests/fixtures/postgres-restore/` or any migration file.
- Weekly scheduled run on main.

**Manual exercise:**

- Operator runs each runbook section against a staging environment for the matching profile; documents result in PR description.

### 3.3 Compliance evidence produced

- A passing restore CI run is the evidence. Without it, "backup" is not a control.
- Runbook is the procedural evidence.
- Per-profile RPO/RTO documented.

### 3.4 Rollout plan

- Workflow + runbook ship together.
- Operator tests each profile against staging within 1 release.

### 3.5 Backout plan

N/A — pure docs + CI.

### 3.6 Definition of done

- [ ] CI workflow green.
- [ ] Runbook reviewed by an SRE-aligned engineer.
- [ ] Restore exercise performed against staging in at least one profile (Helm or Compose).
- [ ] Manifest committed; drift check active.

---

## 4. Critical files

- New: `.github/workflows/postgres-restore-drill.yml`
- New: `services/backend/scripts/restore_smoke.py`
- New: `services/ai-backend/scripts/restore_smoke.py`
- New: `tests/fixtures/postgres-restore/baseline.dump.sql.gz`
- New: `tests/fixtures/postgres-restore/manifest.yaml`
- New: `docs/ci-cd/runbooks/postgres-restore.md`
