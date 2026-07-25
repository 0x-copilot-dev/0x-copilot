# Generative Surfaces lifecycle operations

## Status and scope

This runbook is the operational contract for the D13 lifecycle metric
foundation. It applies to the `ai-backend` OpenTelemetry exporter and its
existing lifecycle seams:

- D10/D11 retention planning;
- the existing retention sweeper's failure paths;
- D12 repair/reconciliation planning;
- D7 receipt-export verification; and
- the concrete RBAC and audit-list identity-denial boundaries.

It does **not** claim that a physical cleanup executor, repair executor,
scheduled audit sampler, dashboard, or alert-delivery integration has shipped.
There is one bounded D12 **planning-only** worker loop for existing
claimed/indeterminate effect claims; it is disabled by default and records only
redacted candidate/withheld decisions. This repository exports OpenTelemetry
signals. The deployment owner must connect the existing OTLP exporter to their
collector and configure the alert rules below before treating them as active
production alerts.

### D12 planning-only runner

The loop is opt-in and is not an execution path:

```bash
SURFACES_V2=true
ARTIFACT_EFFECTS_V2=true
REPAIR_PLANNING_ENABLED=true
```

`REPAIR_PLANNING_INTERVAL_SECONDS` (default `600`),
`REPAIR_PLANNING_MAX_CLAIMS` (default `100`, max `500`),
`REPAIR_PLANNING_PAGE_SIZE` (default `100`, max `500`),
`REPAIR_PLANNING_MAX_EVENTS_PER_RUN` (default `2000`, max `10000`), and
`REPAIR_PLANNING_QUIET_SECONDS` (default `120`, max `604800`) bound each poll.

It is composed only in the real runtime-worker entrypoint for supported
in-memory and Postgres worker configurations. The standalone file worker
remains intentionally unsupported because the file backend is single-process;
the file snapshot adapter is nevertheless durable and parity-tested. The loop
has no queue, cleanup, deletion, approval, apply, resend, or effect-executor
dependency. It never emits a public event. Incomplete enumeration, an unknown
reference, a tenant mismatch, a nonterminal owner, an unavailable legal-hold
authority, or lack of a reconcile-capable executor becomes a safe withheld
decision; a collector-wide persistence failure is a safe aggregate failure.

Only the effect-claim source family is registered today. Artifact/outbox/temp,
receipt/source, usage-edge, and audit-sampling families must not be described
as automated until each exposes a bounded, tenant-scoped trusted enumeration
port and its own planning collector.

## Data-safety contract

The registry lives in
[`lifecycle_metrics.py`](../../src/agent_runtime/observability/lifecycle_metrics.py).
Every label is a closed lowercase token. Metrics never include a tenant, user,
run, artifact, connector, path, raw reference, payload, exception message, or
provider response. Unknown input is collapsed to `other` or `unknown` rather
than becoming a new time series.

The automated canary at
[`test_lifecycle_metrics.py`](../../tests/unit/agent_runtime/observability/test_lifecycle_metrics.py)
asserts both the registry and emitted labels stay safe.

## Signal inventory

| Metric                                                  | Type             | Labels                                     | Existing producer                       | Meaning / limitation                                                                                                                                                                  |
| ------------------------------------------------------- | ---------------- | ------------------------------------------ | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `surfaces_lifecycle_plan_total`                         | Counter          | `planner`, `outcome`                       | Retention and repair planners           | Invocations that completed, rejected invalid input, or raised unexpectedly. It is not a scheduled-job heartbeat.                                                                      |
| `surfaces_lifecycle_plan_decisions_total`               | Counter          | `planner`, `candidate_kind`, `disposition` | Retention and repair planners           | Aggregate decision rate only; it identifies no candidate.                                                                                                                             |
| `surfaces_lifecycle_plan_duration_seconds`              | Histogram        | `planner`                                  | Retention and repair planners           | In-process planning latency, not graph-enumeration or cleanup latency.                                                                                                                |
| `surfaces_lifecycle_retention_lag_seconds`              | Histogram        | `candidate_kind`, `stage`                  | Retention planner                       | Due-time lag observed in a supplied planning snapshot (`tombstone_due` or eligible `physical_gc_due`). It is emitted only when a planner is called.                                   |
| `surfaces_lifecycle_reconcile_backlog_snapshot_items`   | Observable gauge | `state`                                    | Repair planner and opt-in D12 loop      | Latest process-local count of D12 **effect-reconciliation** candidates/withheld rows. It resets after restart; durable candidate/withheld rows remain in the planning snapshot store. |
| `surfaces_lifecycle_retention_execution_failures_total` | Counter          | `kind`                                     | Existing retention sweeper              | Sweeper call failures by closed retention family. Existing `retention_swept_rows_total` and `retention_sweep_duration_seconds` remain the success and latency signals.                |
| `surfaces_lifecycle_audit_verification_total`           | Counter          | `format`, `outcome`                        | Receipt export v2 verifier              | Offline/route verification attempts and success/failure. The failure reason is intentionally not a label.                                                                             |
| `surfaces_lifecycle_authorization_denials_total`        | Counter          | `boundary`, `reason`, `enforcement`        | Runtime RBAC; audit-list identity check | Denial telemetry for these two implemented boundaries only. It is not a claim that every cross-tenant owner check is instrumented.                                                    |

## Alert policy

The following rules use PromQL-shaped notation for clarity. Configure equivalent
rules in the deployment's existing OTLP metrics backend; do not add a dashboard
or cloud-vendor dependency solely for this runbook.

| Alert                                | Condition                                                                                                  | Severity / owner                              | Immediate remediation                                                                                                                                                                                                     |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `LifecycleRetentionSweepFailure`     | `increase(surfaces_lifecycle_retention_execution_failures_total[15m]) >= 1`                                | Page Runtime Data Lifecycle on-call           | Check worker health and persistence connectivity. Set `RETENTION_SWEEP_DRY_RUN=true` before any manual retry if deletion behavior is uncertain. Do not manually delete rows or blobs.                                     |
| `LifecycleAuditVerificationFailure`  | `increase(surfaces_lifecycle_audit_verification_total{outcome="failed"}[15m]) >= 1`                        | Page Security/Audit on-call and Runtime owner | Preserve the signed bundle and relevant key version. Do not rewrite the bundle, resend an effect, or rotate away the old key. Re-run the offline verifier with the key ring, then open an incident if it remains invalid. |
| `LifecyclePlannerFailureBurst`       | `increase(surfaces_lifecycle_plan_total{outcome!="succeeded"}[15m]) >= 5`                                  | Warning to Runtime Lifecycle owner            | Inspect the trusted snapshot collector/inputs. Treat `failed` as a code or dependency issue; treat `rejected_input` as a caller contract breach. Do not relax fail-closed planner gates.                                  |
| `LifecycleReconcileBacklog`          | `max_over_time(surfaces_lifecycle_reconcile_backlog_snapshot_items{state="candidate"}[30m]) > 0` for 30m   | Warning to Effect Reconciliation owner        | If the opt-in D12 planner is enabled, inspect only redacted candidate evidence and use a separately authorized reconcile path; never apply or resend an uncertain effect.                                                 |
| `LifecycleRetentionLag`              | `histogram_quantile(0.95, rate(surfaces_lifecycle_retention_lag_seconds_bucket[30m])) > 86400`             | Warning to Data Lifecycle owner               | This is a planning-snapshot signal, not proof that deletion is stuck. Verify planner scheduling and complete enumeration/legal-hold state first. Keep physical deletion disabled until those checks pass.                 |
| `LifecycleAuditListIdentityMismatch` | `increase(surfaces_lifecycle_authorization_denials_total{boundary="audit_list_identity"}[15m]) >= 1`       | Security on-call                              | Review service-token caller configuration and gateway identity propagation. Do not expose whether the requested tenant/user exists.                                                                                       |
| `LifecycleRbacDenyAnomaly`           | Establish baseline, then alert on an agreed sustained deviation (for example `>5x` the 7-day rate for 15m) | Security + Runtime owner                      | RBAC denies can be expected. Compare the fixed `reason`/`enforcement` labels to the baseline, then investigate policy or client rollout changes without adding identity/path labels.                                      |

Do **not** page on `surfaces_lifecycle_reconcile_backlog_snapshot_items` or
`surfaces_lifecycle_retention_lag_seconds` until the responsible scheduled
planner is deployed and has a documented heartbeat. A missing metric today can
mean “no planner invocation”, not “zero backlog”.

## Incident playbooks

### Retention/deletion failure

1. Acknowledge `LifecycleRetentionSweepFailure` and identify the closed `kind`
   label; do not use metrics to infer a tenant or path.
2. Check worker readiness and database/persistence health.
3. If a manual retry is required, first enable the existing dry-run mode and
   inspect its retained evidence rows. Do not bypass legal holds, manually
   delete shared blobs, or convert a logical tombstone directly into physical
   deletion.
4. Recover the dependency, rerun through the normal sweeper, and confirm the
   failure counter stops increasing. Record the incident in the deployment's
   normal operational system.

### Indeterminate external effect / reconciliation backlog

1. Treat any candidate as _non-executable_. The D12 planner only proposes a
   future reconciliation candidate; it cannot approve, apply, enqueue, or
   resend work.
2. Verify the stage is terminal, the quiet period elapsed, evidence is present,
   and legal-hold/enumeration guards pass.
3. The planning runner does not authorize or invoke reconciliation. Follow the
   separately approved reconciliation procedure; never turn a metric or
   candidate into a direct external effect.
4. If the process restarted, wait for the next trusted planning invocation.
   The gauge is process-local, while the redacted planning snapshot/cursor is
   durable on file/Postgres backends.

### Audit verification failure

1. Preserve the original export bytes and the current/Historical key versions.
2. Run the offline receipt verifier. A verifier failure must be treated as a
   failed integrity check even if the exported receipt looks plausible.
3. Investigate export generation, key rotation, and chain ordering. Do not
   repair the source ledger or regenerate the bundle in place.
4. Escalate to Security/Audit. Resolve only after an independently verified
   bundle succeeds and the incident record is retained.

### Authorization-denial anomaly

1. Determine whether the fixed boundary is `rbac` or `audit_list_identity`.
2. For `audit_list_identity`, inspect only trusted service-token and gateway
   configuration; the endpoint intentionally returns a generic 403.
3. For `rbac`, compare fixed `reason` and `enforcement` labels with the normal
   rollout baseline. Correct scopes/roles or MFA state; do not weaken RBAC to
   suppress the signal.

## Validation before enabling alerts

Run the contract canary and the affected service suite from the service root:

```bash
cd services/ai-backend
.venv/bin/python -m pytest \
  tests/unit/agent_runtime/observability/test_lifecycle_metrics.py \
  tests/unit/agent_runtime/surfaces_v2/test_retention_planning.py \
  tests/unit/agent_runtime/surfaces_v2/test_repair_reconciliation.py \
  tests/unit/runtime_worker/test_repair_planning.py

.venv/bin/python -m pytest
```

The first test is the gate for label safety. It must remain green before a new
metric, label, label value, or producing seam is introduced.

## Remaining implementation work

This foundation deliberately leaves these items for later PRs:

- durable snapshot collection and scheduled D10 retention planning;
- bounded trusted collectors for the remaining D12 repair families;
- a durable global backlog gauge or queue depth source;
- authorization and execution of tombstone/physical-GC/repair actions;
- scheduled receipt/audit verification sampling;
- alert-rule provisioning, dashboarding, paging integration, and SLO ownership
  in the deployment environment; and
- operation/stage/artifact success-rate instrumentation beyond the existing
  ledger and retention-sweeper signals.

Those items must be implemented and tested before claiming D13, D11, or D12 is
production-complete end-to-end.
