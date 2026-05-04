# Postgres restore runbook (C12)

This runbook covers the documented restore procedure per deploy
profile, the post-restore validation checklist, and how the CI drill
proves the procedure actually works.

> **Why this exists.** Per the project's compliance-review rules
> ("a control counts as implemented only when code, config, tests,
> and docs all support it"), an untested backup is **not** a control.
> The CI workflow [`postgres-restore-drill.yml`](../../../.github/workflows/postgres-restore-drill.yml)
> is the load-bearing evidence; this runbook is the operator-side
> procedure.

---

## RPO / RTO targets per profile

| Profile                               | RPO        | RTO        | Backup mechanism                                           |
| ------------------------------------- | ---------- | ---------- | ---------------------------------------------------------- |
| `saas_multi_tenant` (managed)         | **5 min**  | **30 min** | RDS / Cloud SQL / Aurora point-in-time recovery (PITR)     |
| `single_tenant_managed` (Helm/K8s)    | **15 min** | **2 h**    | `pg_basebackup` + WAL archiving to customer object storage |
| `single_tenant_self_hosted` (Compose) | **24 h**   | **4 h**    | OS / hypervisor volume snapshot                            |

A breach of either RPO or RTO is a SEV-2; document the deviation in
the post-incident review along with the corrective action.

---

## Profile A — SaaS multi-tenant (RDS / Cloud SQL / Aurora PITR)

We rely on the cloud provider's native PITR. Customer data never
leaves the provider perimeter; we never produce a `pg_dump` ourselves.

### Steady-state controls (must be in place BEFORE a restore is needed)

- AWS RDS / Aurora: `BackupRetentionPeriod` ≥ **35 days**;
  `EnableCloudwatchLogsExports = ["postgresql"]`; storage encryption
  enabled with a customer-managed CMK from C6.
- Cloud SQL: `automatedBackups.enabled = true`,
  `pointInTimeRecoveryEnabled = true`,
  `backupConfiguration.transactionLogRetentionDays = 7`.
- Quarterly tabletop: an SRE picks a random target time within the
  retention window and walks through the restore commands below
  against staging.

### Restore steps

1. **Quarantine the source instance.** Revoke the application's
   security group from the breached primary so no further writes can
   land. Page on-call.
2. **Restore to a new instance.** Pick the target timestamp (5-minute
   granularity for RDS / Aurora; 1-second for Cloud SQL).
   ```bash
   # AWS RDS
   aws rds restore-db-instance-to-point-in-time \
       --source-db-instance-identifier <prod-id> \
       --target-db-instance-identifier <prod-id>-restore-$(date +%Y%m%d-%H%M) \
       --restore-time <ISO-8601 in retention window>
   ```
3. **Cut over.** Update Route53 / DNS / Helm values to point the
   `BACKEND_DATABASE_URL` and `RUNTIME_DATABASE_URL` env vars at the
   restored instance.
4. **Run the post-restore validation checklist** (below).
5. **Decommission the breached instance** ONLY after the post-restore
   checklist is green AND the SEV review approves it. Take a final
   forensic snapshot first.

---

## Profile B — Single-tenant managed (Helm / K8s)

Customer-operated Postgres in their own K8s cluster (cnpg, Crunchy,
or a managed service). We don't have direct access; we ship the
runbook so the customer's SRE can execute it.

### Steady-state controls

- WAL archiving to object storage (S3 / GCS / Azure Blob) using
  `archive_command` with at least `15-minute` archive cadence.
- `pg_basebackup` weekly to the same object storage location.
- Customer-side automation (cnpg `Backup`/`Cluster.backup`) is the
  recommended path; the runbook below works if customers run their
  own scripts.

### Restore steps

1. Stop the ai-backend / backend Deployments
   (`kubectl scale deploy/ai-backend deploy/backend --replicas=0`).
2. Stop the Postgres `StatefulSet`.
3. From the most recent base backup, restore to a new PVC:
   ```bash
   pg_basebackup -D /var/lib/postgresql/restore -X stream -P
   ```
4. Replay WAL up to the target time using `recovery.conf`:
   ```
   restore_command = 'aws s3 cp s3://<customer-bucket>/wal/%f %p'
   recovery_target_time = '<ISO-8601>'
   ```
5. Promote the restored volume; restart the Postgres `StatefulSet`
   pointed at the new PVC.
6. Restart ai-backend / backend Deployments (`--replicas=N`).
7. Run the post-restore validation checklist.

---

## Profile C — Single-tenant self-hosted (Docker Compose)

Customer runs `docker-compose.yml` on a single VM. Backup is the
operator's responsibility (volume snapshot or `pg_dump`); restore is
mechanical.

### Steady-state controls

- Operator's hypervisor / cloud-provider snapshot of the
  `postgres_data` volume; recommended cadence **daily**.
- Or: `pg_dump --format=custom > /backup/$(date +%Y%m%d).dump` to
  off-host storage; recommended cadence **daily**.

### Restore steps

```bash
# Stop the stack so no new writes land while we restore.
docker compose -f docker-compose.prod.yml down

# Option 1 — volume snapshot.
# (replace with your hypervisor's restore command)
pvesm restore --target-volume postgres_data --snapshot <id>

# Option 2 — pg_dump.
docker compose -f docker-compose.prod.yml up -d postgres
docker compose -f docker-compose.prod.yml exec -T postgres \
    pg_restore --clean --if-exists --no-owner -d enterprise \
    < /backup/<chosen>.dump

# Bring the rest of the stack back up.
docker compose -f docker-compose.prod.yml up -d
```

Then run the post-restore validation checklist.

---

## Post-restore validation checklist

Run **every** item against the restored instance before declaring the
incident over. The CI drill exercises these same checks on every PR
that touches a migration, the seed fixture, or the smoke scripts.

```bash
# 1. Schema is current.
cd services/backend && yoyo apply --batch \
    --database "$BACKEND_DATABASE_URL" migrations/
cd services/ai-backend && yoyo apply --batch \
    --database "$RUNTIME_DATABASE_URL" migrations/

# 2. Restore-smoke per service: per-table COUNT(*) matches the
#    operator-supplied manifest (or, in CI, the fixture manifest).
python services/backend/scripts/restore_smoke.py
python services/ai-backend/scripts/restore_smoke.py

# 3. C5 RLS isolation smoke — connect as enterprise_app, set the
#    org_a context, insert a row; switch to org_b; SELECT must
#    return zero rows for org_a's data.
cd services/ai-backend
PYTHONPATH=src:../../packages/service-contracts/src .venv/bin/python \
    -m pytest tests/integration/postgres -k tenant_isolation -x

# 4. Retention sweeper dry-run (C8) — verify no policy fires
#    unexpected deletes against restored rows.
cd services/ai-backend
.venv/bin/python -m runtime_worker.jobs.retention_sweeper \
    --database-url "$RUNTIME_DATABASE_URL" \
    --dry-run

# 5. Field-encryption invariants (C7) — count of v0 rows per table.
.venv/bin/python services/ai-backend/scripts/count_unencrypted_rows.py \
    --db-url "$RUNTIME_DATABASE_URL" --json

# 6. Sessions table is intact and the worker can claim outbox.
PGPASSWORD=$PGPASS psql -h <restored-host> -U <user> -d <db> -c \
    "SELECT count(*) FROM sessions WHERE revoked_at IS NULL;"
PGPASSWORD=$PGPASS psql -h <restored-host> -U <user> -d <db> -c \
    "SELECT count(*) FROM runtime_outbox_events WHERE locked_by IS NULL;"

# 7. /v1/health reports the deployment profile correctly.
curl -fsS http://<restored-backend>/v1/health | jq '.deployment_profile'
curl -fsS http://<restored-ai-backend>/v1/health | jq '.deployment_profile'
```

---

## CI drill

[`postgres-restore-drill.yml`](../../../.github/workflows/postgres-restore-drill.yml)
runs on:

- every PR that touches a migration, the seed fixture, the manifest,
  either `restore_smoke.py`, or the workflow itself;
- a weekly schedule (Sundays 06:00 UTC);
- `workflow_dispatch` for ad-hoc operator runs.

The job:

1. boots Postgres 16 in a service container;
2. applies every migration in both services;
3. loads
   [`tests/fixtures/postgres-restore/seed.sql`](../../../tests/fixtures/postgres-restore/seed.sql);
4. runs both `restore_smoke.py` scripts which compare per-table
   `COUNT(*)` against the manifest AND check cross-tenant isolation
   AND assert the seed wrote `encryption_version=0` (so the C7 phase-3
   strict-reads gate stays opt-in via env var, not implicit).

Failure pages on-call. Do **not** merge a PR with a red restore
drill — green it first by editing
[`tests/fixtures/postgres-restore/manifest.yaml`](../../../tests/fixtures/postgres-restore/manifest.yaml)
and/or
[`tests/fixtures/postgres-restore/seed.sql`](../../../tests/fixtures/postgres-restore/seed.sql)
to match the schema change.

## Compliance evidence

The restore drill is the canonical evidence presented in customer
audits. Show the auditor:

1. The most recent green run of `postgres-restore-drill` on `main`.
2. This runbook with the per-profile RPO / RTO table.
3. The post-incident report from the most recent quarterly tabletop
   (Profile A) or customer-side restore exercise (Profiles B / C).

Without all three, "we have backups" is not a control.
