# Model invocation reliability operations

## Status and operating boundary

This is the desktop-first runbook for F10 model route planning, provider
attempts, bounded recovery, circuit health, and journal-derived metrics.

The canonical source is the tenant-scoped runtime event journal exposed through
`ModelInvocationStorePort`. It contains body-free invocation, route,
exclusion, admission, attempt-state, usage, recovery, and terminal records.
Metrics are a projection of validated records; they are not a second ledger and
cannot authorize a retry, fallback, circuit transition, or release.

The following implementation boundaries matter during an incident:

- `RunControlSnapshot.feature_modes.f10` is the persisted `off`, `shadow`, or
  `enforce` authority for a run. Signed release configuration and controlled
  restart are the existing deployment path for changing it.
- `ModelReliabilityReleaseControls` defines independent modes and kill switches
  for same-deployment retry, alternate route, equivalent route, and circuit
  influence. On this revision it is a typed composition input, not an
  environment-backed operator API. Do not invent or set `F10_*` variables.
- `ProcessLocalProviderCircuitHealth` is bounded process-local state.
  `DesktopProviderCircuitSnapshotStore` is an optional capped, atomic file
  adapter, but no production path or shutdown composition is assigned on this
  revision. Do not assume a snapshot exists.
- The OpenTelemetry projector is available for composition after a validated
  journal append or bounded replay. A deployment must wire that producer and an
  OTLP collector before treating the signals below as live alerts.

The provider itself is intrinsically online unless the selected deployment is
local. Route planning, journal replay, F1 fixture evaluation, and file-store
recovery remain local.

## Controls and safe behavior

### F10 feature mode

| Mode      | Behavior for a newly bound run                                                                                                                        | Operator use                                                      |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `off`     | The F10-owned route/recovery path has no authority. Existing model selection, authorization, privacy, region, provider-key, and budget checks remain. | Immediate backout to current primary-route behavior.              |
| `shadow`  | Records or compares F10 decisions without letting retry, alternate, equivalent, or circuit decisions broaden dispatch authority.                      | Diagnose route and failure classification before enforcement.     |
| `enforce` | Allows only the F10 behavior present in the immutable run snapshot and further narrowed by the independent recovery controls.                         | Enable after the F1 gate and composition/replay tests below pass. |

An active run keeps its immutable snapshot. A release change applies to a new
run after controlled API/worker restart; it does not rewrite a running or
suspended invocation.

### Independent recovery controls

Each recovery control resolves independently from `off`, `shadow`, or
`enforce`, then a same-purpose kill switch can narrow it to disabled:

| Control           | Enforce authority                                                    | Kill-switch result                          |
| ----------------- | -------------------------------------------------------------------- | ------------------------------------------- |
| retry             | Same-deployment retry proven safe before visible content/effects.    | No new retry.                               |
| alternate route   | Same-model, same-policy alternate deployment.                        | Primary only.                               |
| equivalent route  | Exact F1-qualified task-family/revision pair and product permission. | No cross-model fallback.                    |
| circuit influence | Open circuit can exclude a route; a bounded probe may be admitted.   | Circuit state cannot alter route admission. |

These fields exist in `ModelReliabilityReleaseControls`; the deployment adapter
that supplies them must be reviewed before operators use them. Until that
composition exists, the supported emergency action is signed `f10=off`
rollback, not an undocumented environment override.

## Metric safety and inventory

The registry is
[`model_invocation_metrics.py`](../../src/agent_runtime/observability/model_invocation_metrics.py).
Every attribute key and value is checked against a closed registry before the
OTel facade sees it. There are no run, user, invocation, model-call, attempt,
deployment, provider, model, region, prompt, output, endpoint, exception, or
free-text error labels.

`ModelInvocationMetricsProjector` deduplicates by the journal's stable record
identity and sequence in bounded process memory. One projector owns one run.
It never evicts a dedup key: exceeding the configured replay bound fails
explicitly rather than double-counting an older record. Replaying an
overlapping range through the same projector is exact once.

After the outer run terminal event is durable, call
`seal_terminal_replay()`. It audits missing finalizers, returns a content-free
run/sequence checkpoint, and rejects later records. The composition may then
discard that projector and allocate one for the next run. Do not rotate at an
individual model-invocation terminal—later calls in the same run still need
the existing dedup state. Do not rotate an active long-running run to escape
the record cap; preserve the incident and raise the reviewed bound only after
capacity analysis.

| Metric                                          | Type      | Labels                               | Journal fact and limitation                                                                                                                                     |
| ----------------------------------------------- | --------- | ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `model_invocation_route_plans_total`            | Counter   | `purpose`, `fallback_policy`         | One validated `invocation_planned`.                                                                                                                             |
| `model_invocation_route_exclusions_total`       | Counter   | `reason`                             | One count per closed reason in `route_excluded`.                                                                                                                |
| `model_invocation_policy_exclusions_total`      | Counter   | `dimension`                          | Region, privacy, and explicit BYOK-required/disallowed exclusions only.                                                                                         |
| `model_invocation_attempts_total`               | Counter   | `attempt_kind`, `decision`, `reason` | Admission fact classified as primary, retry, fallback, or unknown.                                                                                              |
| `model_invocation_recoveries_total`             | Counter   | `kind`, `outcome`                    | Retry, alternate-route, or crash-reconciliation record.                                                                                                         |
| `model_invocation_terminal_total`               | Counter   | `outcome`, `reason`                  | Completed, failed, or terminal ambiguous result.                                                                                                                |
| `model_invocation_ambiguous_total`              | Counter   | `source`                             | Ambiguous attempt state, failure class, recovery, or terminal fact.                                                                                             |
| `model_invocation_attempt_latency_seconds`      | Histogram | `attempt_kind`, `usage_source`       | Duration on the exactly-once usage finalizer.                                                                                                                   |
| `model_invocation_fallback_latency_seconds`     | Histogram | `outcome`                            | End-to-end terminal duration only when alternate-route recovery was admitted. It is not time-to-first-token.                                                    |
| `model_invocation_reported_tokens_total`        | Counter   | `attempt_kind`, `token_kind`         | Provider-reported token fields only, including failed finalized attempts.                                                                                       |
| `model_invocation_reported_cost_microusd_total` | Counter   | `attempt_kind`                       | Provider-reported per-attempt cost only.                                                                                                                        |
| `model_invocation_missing_finalization_total`   | Counter   | `attempt_kind`                       | Admitted attempt without usage finalization at a terminal or explicit crash/recovery replay boundary.                                                           |
| `model_invocation_circuit_events_total`         | Counter   | `event`                              | `opened` from open-circuit exclusion; `probed` only when that deployment is later admitted; `recovered` only when that probe owns a successful terminal result. |

Do not call missing-finalization detection on an ordinary live prefix: an
in-flight attempt is expected to lack final usage. Call it after a terminal
replay or when process loss makes an open attempt a recovery incident.

Suggested initial alerts are:

- any sustained increase in `model_invocation_missing_finalization_total`;
- any terminal `outcome="ambiguous"`;
- a retry/fallback attempt rate that exceeds the reviewed rollout baseline;
- a sudden region, privacy, or BYOK exclusion-rate change; and
- opened circuits without later recovered facts.

Do not page on absence of a metric until the journal projector and OTLP
collector have a documented heartbeat.

## Route and failure diagnosis

1. Stop admitting new runs if privacy, region, BYOK, duplicate output, or
   ambiguous provider state may be involved.
2. Through the facade and an authenticated user session, replay the run's
   persisted events. Use `after_sequence` only from the highest sequence
   already verified; never call `backend` or `ai-backend` directly from an app.
3. Verify exactly one run-control snapshot and the expected F10 mode, route
   policy revision, descriptor-set revision, requirements digest, and route
   digest.
4. Read the route facts in order:
   `invocation_planned` → all `route_eligible`/`route_excluded` records →
   `attempt_admission`.
5. For a rejected route, use only the closed exclusion reasons. In particular:
   `region_mismatch`, `privacy_incompatible`, `byok_required`, and
   `byok_disallowed` are policy results, not provider outages. Do not work
   around them by selecting a less restricted fallback.
6. For a provider failure, inspect closed dispatch/stream state, failure class,
   visible-text/tool-content/effect booleans, recovery outcome, usage
   finalization, and terminal attribution. Do not inspect or add exception
   text.
7. Confirm every admitted attempt has a distinct stable identity and one usage
   finalizer, including failed attempts where usage was reported. Confirm one
   terminal attempt owns the user-visible result.
8. If sequence, record digest, scope, snapshot, attempt ordering, or terminal
   attribution conflicts, stop. Preserve an authorized export and repair the
   canonical journal path; never edit `events.jsonl` or synthesize a metric.

Metrics identify the failure family, not the affected user or route. Use the
authorized journal replay for scoped diagnosis rather than adding identifiers
to OTel labels.

## Ambiguous attempt handling

An admitted attempt left open by process loss, an unknown provider acceptance
state, usage with uncertain completion, visible content followed by transport
loss, or an unknown provider failure is ambiguous.

1. Do not retry the attempt, replay the whole run, concatenate another stream,
   or resend a tool/effect.
2. Preserve the original invocation, attempt, provider-request digest (when
   present), lifecycle facts, and usage finalization state.
3. If a reviewed provider-specific status adapter can reconcile the exact
   request without exposing a raw provider request ID, record the reconciled
   completed or failed outcome through the normal journal path.
4. Otherwise record honest ambiguous recovery and terminal failure. If usage
   may be incomplete, keep that fact; do not replace it with zero-cost
   provider-reported usage.
5. Return a partial/retry-safe product outcome according to the owning product
   contract. Automatic replacement after visible text, tool-call content, or
   external effects is forbidden.
6. Run the projector's missing-finalization audit only at this recovery
   boundary. Investigate any emitted count; do not manually insert a usage row.

Worker queue retry is not model retry authority. It may restart work only when
the owning fence proves failure before model-handler entry.

## Desktop circuit snapshot corruption and recovery

The optional file adapter writes an atomic, `0600`, digest-protected envelope,
caps bytes and entries, and returns `None` for an absent, oversized, corrupt,
incompatible, or digest-mismatched file. Returning `None` restores no circuit
state; it does not broaden route eligibility beyond the ordinary primary
policy.

On suspected corruption:

1. Confirm whether the host composition actually configured
   `DesktopProviderCircuitSnapshotStore` and obtain its host-owned path. There
   is no F10 snapshot environment variable or standard path on this revision.
2. Stop new-run admission and quit through the normal desktop lifecycle.
3. Preserve a read-only forensic copy under the same local access controls.
   Never print the file or credential fingerprints into logs or a ticket.
4. Restart normally. A rejected snapshot loads as empty bounded circuit state;
   the canonical invocation journal remains untouched.
5. Verify primary-route behavior and a new canary before reopening admission.
   Let normal health observations rebuild circuit state.
6. If corruption repeats, keep circuit influence disabled through the reviewed
   control path and investigate disk, permissions, atomic rename, and shutdown
   behavior. Do not hand-edit the envelope, digest, timestamps, entry keys, or
   runtime journal.

The circuit snapshot is disposable health continuity, not an invocation,
usage, billing, or audit source.

## BYOK isolation

Circuit keys partition provider, deployment, region, credential mode, and—only
for BYOK—an opaque SHA-256 credential fingerprint. Deployment and keyless
circuits cannot carry a fingerprint; BYOK circuits require one.

- A user-key authentication failure can affect only that fingerprint scope.
  It must never open a deployment-global circuit.
- Never put plaintext keys, key hints, endpoint URLs, provider request IDs, or
  fingerprints into metric labels or ordinary logs.
- A BYOK route may not fall back to deployment credentials when policy requires
  BYOK. A deployment route may not consume a user key when BYOK is disallowed.
- When a BYOK incident appears global, first verify credential-mode isolation
  in the authorized circuit/journal facts. Do not infer global provider health
  from aggregate metrics.
- Key rotation creates a new credential scope. Do not rewrite old snapshot or
  invocation facts to make them refer to the new key.

## Offline, suspend, and quit behavior

### Offline

The desktop file journal, route replay, metrics projection, circuit reducer,
and F1 fixture suite work offline. OTel export is fail-soft when no collector is
configured. A remote provider call does not work offline merely because the
control plane does.

Classify an offline provider failure from typed adapter facts. Retry only when
it is proven pre-dispatch/pre-content and admitted by the immutable budget and
current recovery controls. Unknown acceptance is ambiguous.

### Suspend and resume

There is no F10-specific OS suspend callback on this revision. Do not assume an
in-flight socket, deadline, or snapshot write pauses safely. After resume:

1. replay the canonical journal before considering another provider attempt;
2. treat an admitted, nonterminal pre-suspend attempt as ambiguous unless a
   provider-specific status check proves its outcome;
3. retain the original deadline and aggregate attempt budget; and
4. never use wake/resume as authority to replay a model call or external
   effect.

### Normal quit and force quit

Electron's normal `before-quit` path stops facade → ai-backend → backend and
then the embedded database. The Python supervisor uses `SIGTERM` with bounded
`SIGKILL` escalation. The circuit snapshot is durable only if the host has
explicitly composed and awaited its save; the adapter's existence alone does
not guarantee that.

After force quit or process crash, reopen the file store normally and replay
the journal. Any open provider attempt is ambiguous. Do not delete lock files,
journal rows, or usage records to make the run appear terminal.

## Backout to primary or off

For a suspected retry, fallback, or circuit incident:

1. Stop new-run admission.
2. If the independent release controls are composed, assert the narrowest
   relevant kill switch: equivalent route, alternate route, retry, then circuit
   influence. Confirm the resolved decision is primary-only.
3. If those controls are not composed, use the existing authenticated local
   release rollback to the immediate verified predecessor whose
   `feature_modes.f10` is `off`. Do not set undocumented environment variables.
4. Controlled-restart API and worker through the desktop supervisor.
5. Start a new canary and verify `f10=off`, one primary admission, no recovery
   admission, one usage finalizer, and one terminal attempt.
6. Existing active runs keep their snapshots. Cancel them through the normal
   run-cancellation path if they must not continue.
7. Preserve the redacted journal and F1 report under normal retention. Never
   change the user's selected model or provider key merely to simulate
   backout.

`RUNTIME_HARNESS_RELEASE_CONFIG_PATH` and
`RUNTIME_LOCAL_RELEASE_CONTROL_ENABLED` are the existing release-control
settings. See the environment reference and harness release runbook for their
verification, loopback authentication, rollback, and restart requirements.

## F1 qualification evidence

Use the existing `TrajectoryProjector`,
`ModelInvocationTrajectoryScorer`, fixture-only suite runner, paired promotion
evaluator, and signed release process. Do not create a second F10 evaluator.

The current operational corpus provides hard body-free checks for:

- `provider_pre_content_failure`: deterministic route order, BYOK route
  credential mode, region exclusion, safe same-deployment retry, exactly two
  attempts, provider-reported usage, and successful terminal lineage; and
- `provider_ambiguous_failure`: one attempt, ambiguous lifecycle/recovery,
  terminal failure, and no blind second attempt.

Those cases are necessary but not sufficient to enable equivalent-model
fallback. Before `equivalent_route` can enforce for a task family, the same
exact candidate/control revision set must also contain reviewed cases for
primary and subagent lineage, all region/privacy/BYOK exclusions, cache
rejection, visible-output interruption, budget exhaustion, circuit/BYOK
isolation, restart recovery, and feature-off parity. If that evidence is
absent, inconclusive, unpaired, or fails a hard gate, keep equivalent fallback
off.

Promotion evidence must show:

- `model_invocation_trajectory_passed` for every applicable hard assertion;
- no missing record/status/decision/reason/state/failure/recovery/credential/
  exclusion/usage fact;
- contiguous route ordinals and the exact allowed attempt range;
- zero live-effect dispatches in fixture evaluation;
- no protected-family, safety, groundedness, constraint, cost, or latency
  regression under the versioned paired thresholds; and
- exact code, model, prompt, tool, policy, fixture, and scorer revisions.

A paired report is evidence, not activation authority. Review it, create and
sign the release manifest outside the runtime, activate it through the existing
release path, controlled-restart, and verify a new canary.

## Validation

From the service root:

```bash
cd services/ai-backend

.venv/bin/python -m pytest \
  tests/unit/agent_runtime/observability/test_model_invocation_metrics.py \
  tests/unit/agent_runtime/execution/model_invocation \
  tests/unit/agent_runtime/harness_quality/test_evaluation.py \
  tests/unit/agent_runtime/harness_quality/test_operational_corpus_scoring.py

.venv/bin/ruff check \
  src/agent_runtime/observability/model_invocation_metrics.py \
  tests/unit/agent_runtime/observability/test_model_invocation_metrics.py

.venv/bin/ruff format --check \
  src/agent_runtime/observability/model_invocation_metrics.py \
  tests/unit/agent_runtime/observability/test_model_invocation_metrics.py
```

Before enforcement or backout is considered complete, also run the full
`ai-backend` suite, compile validation, API-types typecheck, and
`git diff --check`. Verify no log or metric sample contains a key, endpoint,
prompt/output, raw provider error, request ID, or runtime identity.
