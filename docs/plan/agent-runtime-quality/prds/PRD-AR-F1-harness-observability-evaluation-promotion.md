# PRD-AR-F1 — Harness observability, evaluation, and promotion gates

**Status:** proposed\
**Priority:** P0\
**Owners:** AI Runtime, Applied AI, Security/Data Governance\
**Depends on:** [A1 contracts](../../generative-surfaces-v2-1/prds/PRD-A1-artifact-effect-contracts.md),
[A2 artifact repository](../../generative-surfaces-v2-1/prds/PRD-A2-artifact-repository.md),
[D2 built-ins/subagents](../../generative-surfaces-v2-1/prds/PRD-D2-builtins-subagents.md), and
[E1 accountability](../../generative-surfaces-v2-1/prds/PRD-E1-accountability-lifecycle.md)\
**Unblocks:** every other Agent Runtime Quality PRD

## Goal

Make harness changes measurable and promotable. A prompt, tool-selection policy,
context rule, model route, skill, or executor change must be evaluated against a
versioned task suite and explicit quality, safety, latency, and cost thresholds before
its default changes.

## Implementer brief

Read:

1. `services/ai-backend/src/agent_runtime/observability/usage_meter.py`.
2. `services/ai-backend/src/agent_runtime/api/events.py`.
3. `services/ai-backend/src/runtime_worker/stream_events.py`.
4. `services/ai-backend/src/agent_runtime/execution/factory.py`.
5. `services/ai-backend/src/agent_runtime/persistence/records/`.
6. `packages/audit-chain/`.
7. A1, A2, D2, and E1 above.

Preserve the existing durable run/event/usage model. This PRD adds a redacted
evaluation projection and offline runner; it does not create a second production event
store or put raw transcripts in metrics.

## Problem and current state

The runtime already persists ordered run events, tool/subagent records, citations,
usage, approvals, and artifact/effect references. These are strong ingredients, but
there is no canonical experiment unit that answers:

- Which harness revision produced this trajectory?
- Did it choose the right capabilities and stop at the right time?
- Are claims supported by retained evidence?
- Did a token or latency improvement reduce task success?
- Can the exact task be replayed without calling live third parties?
- What evidence authorized promotion or rollback?

Ad hoc prompt testing and aggregate production metrics cannot answer those questions.
They hide long-tail failures, task-family regressions, and unauthorized-tool discovery.

## Objectives

1. Version task cases, harness variants, tool fixtures, scorers, and promotion policy.
2. Produce consented, redacted trajectory manifests containing references rather than
   raw secrets or large payloads.
3. Support deterministic fixture replay and explicitly labelled live-provider runs.
4. Score task success, evidence quality, tool precision, safety, latency, and cost.
5. Compare candidate and control on the same case/model/tool fixtures.
6. Require signed promotion decisions with a reversible rollout revision.
7. Add negligible synchronous overhead to ordinary production runs.

## Non-goals

- Training models or automatically changing prompts.
- Replaying external writes.
- Treating an LLM judge as ground truth.
- Exporting customer data without an explicit policy and lawful basis.
- Replacing E1 usage, audit, retention, or legal-hold ownership.
- Building runtime selection policies owned by F2–F10.

## Interfaces consumed

- `RuntimeEventEnvelope`, operation trees, usage records, citations, artifacts, stages,
  decisions, receipts, and model/provider metadata.
- A2 immutable blobs/refs for large redacted fixtures and expected outputs.
- E1 retention, deletion, legal hold, audit export, and redaction services.
- Capability descriptors and policy revisions from A3/D1/D2.

## Interfaces exposed

### Domain contracts

```text
EvaluationCase
  case_id, suite_id, revision
  task_family
  input_ref
  fixture_catalog_ref
  expected_assertions[]
  allowed_capabilities[]
  forbidden_capabilities[]
  scorer_set_id
  sensitivity

HarnessVariant
  variant_id, revision
  prompt_plan_revision
  capability_policy_revision
  context_policy_revision
  model_route_revision
  feature_flags

TrajectoryManifest
  trajectory_id, run_id?
  case_id?, variant_id
  ordered_step_refs[]
  evidence_refs[]
  usage_summary
  redaction_policy_revision
  manifest_digest

EvaluationResult
  evaluation_run_id, case_id, variant_id
  scorer_results[]
  hard_gate_failures[]
  total_cost, model_turns, tool_calls
  end_to_end_ms, first_useful_answer_ms
  result_digest

PromotionDecision
  decision_id, candidate_variant, control_variant
  suite_revisions[]
  thresholds_revision
  report_ref
  status: approved | rejected | rolled_back
  actor, decided_at, rationale
```

All IDs are opaque. Digests cover canonical JSON and referenced fixture revisions.

### Ports

- `TrajectoryProjector.project(run_id, policy) -> TrajectoryManifest`
- `EvaluationCaseRepository`
- `FixtureToolExecutor`
- `EvaluationRunner.run(case, variant, mode)`
- `Scorer.score(case, trajectory)`
- `PromotionGate.evaluate(candidate_report) -> GateDecision`

### Internal APIs and events

- `POST /internal/v1/evaluations/runs` starts an authorized offline evaluation.
- `GET /internal/v1/evaluations/runs/{id}` returns status and report refs.
- `harness.evaluation.started.v1`
- `harness.evaluation.completed.v1`
- `harness.promotion.decided.v1`

Public/facade APIs are deferred until an operator UI is approved. Event payloads carry
IDs, revisions, scores, and refs—never raw prompts, tool results, credentials, or
artifact bodies.

## Detailed design

### 1. Online projection

Production execution emits its normal records. An asynchronous projector reads eligible
runs from an outbox and creates a minimal manifest:

- normalized model and tool steps;
- canonical capability IDs, not display names;
- argument/result digests and redacted fixture refs;
- source/citation relationships;
- usage and timing aggregates;
- policy, prompt, model, tool-catalog, and code revisions.

Projection failure never fails the user run. It is retryable and observable.

### 2. Case and fixture model

Cases use synthetic or explicitly approved redacted data by default. Tool fixtures are
closed: the same canonical request returns the same versioned response or configured
error. A fixture miss fails the case instead of reaching a network client.

Expected assertions support:

- exact structured output;
- predicate over an artifact/result;
- required/forbidden capability calls;
- citation support and source coverage;
- approval/effect invariants;
- maximum turns, schema tokens, cost, or latency;
- deterministic test/query exit status.

### 3. Scoring

Deterministic scorers run first and own hard gates. Optional model graders receive
redacted bounded inputs and must record model/prompt revision and rationale. A model
grader cannot override a tenant-isolation, unauthorized-call, unsupported-claim, or
effect-safety failure.

Reports show distributions and confidence intervals, not only averages. Median and p95
are mandatory for latency, cost, and call counts.

### 4. Experiment assignment

Online experiments are off by default. When enabled, assignment is deterministic from
an opaque stable subject key and experiment revision. Eligibility is evaluated
server-side. Effectful or high-sensitivity tasks remain control-only until explicitly
approved. The run persists its assigned variant before the first model call.

### 5. Promotion

A candidate is promotable only when:

- all hard safety/conformance gates pass;
- no protected task family regresses beyond its threshold;
- quality lower bound is acceptable;
- cost and latency budgets are within policy;
- the report includes case/suite/code revisions and missing-data disclosure.

Promotion changes a versioned configuration pointer. Rollback restores the preceding
pointer without data migration.

## Security, tenancy, privacy, and audit

- Projection starts only after org/user eligibility, consent, retention, and legal-hold
  checks.
- Redaction happens before evaluation persistence or model judging.
- Tenant-derived fixture content remains tenant-scoped and encrypted; cross-tenant
  aggregation contains only approved statistics.
- Secrets, cookies, raw connector arguments, physical paths, and provider keys are
  prohibited fields.
- Deletion cascades from source runs to derived manifests/embeddings unless legal hold
  requires a tombstoned retained record.
- Every case mutation, evaluation start, report publication, and promotion decision is
  audited.

## Performance and complexity budgets

- Synchronous production instrumentation overhead: p95 below 3 ms excluding existing
  persistence, with no extra network call.
- Manifest size: at most 256 KiB inline; larger material is referenced through A2.
- Projection work is `O(E)` in eligible run events and single-pass.
- Fixture lookup is `O(1)` by canonical request digest.
- Evaluation concurrency is bounded by provider, org, and global semaphores.
- The runner enforces per-case model-turn, tool-call, token, wall-time, and dollar caps.

## Failure, idempotency, and recovery

- Projection idempotency key: `(run_id, projection_policy_revision)`.
- Evaluation idempotency key binds suite, case revisions, variant, fixtures, seed, and
  runner revision.
- Same key/same digest replays; same key/different digest conflicts.
- A partial evaluation resumes unfinished cases; completed case results are immutable.
- Provider outage yields `inconclusive`, never an implicit candidate pass.
- Fixture drift is impossible in-place; updates create a new fixture revision.
- A failed promotion write leaves the prior configuration pointer active.

## Observability and quality gates

Required dashboards:

- eligible/projected/skipped runs by reason;
- redaction failures and prohibited-field detections;
- case completion, flake, timeout, and fixture-miss rates;
- score distributions by task family and variant;
- tool precision, duplicate-call rate, unsupported claims, user corrections;
- input/schema/output tokens, cache metrics, model turns, cost, and latency;
- promotion and rollback history.

Alerts cover hard-gate regression, cross-tenant access attempts, missing policy
revisions, projection backlog, and evaluation spend anomalies.

## Rollout and backout

1. Land contracts, ports, and synthetic fixtures with production projection disabled.
2. Project only synthetic/dev runs.
3. Enable opt-in redacted production projection for internal tenants.
4. Run shadow candidate evaluations; no online assignment.
5. Enable read-only task-family experiments.
6. Require promotion reports for selected harness flags.
7. Make the gate mandatory for all default changes.

Backout disables projection/assignment and restores the previous variant pointer.
Existing reports remain readable under their schema versions.

## Implementation slices

1. Contracts, repositories, golden JSON fixtures, and prohibited-field tests.
2. Async trajectory projector and redaction policy.
3. Fixture executor and deterministic runner.
4. Deterministic scorers and report aggregation.
5. Optional model-grader adapter with UsageMeter purpose.
6. Experiment assignment and immutable run binding.
7. Promotion decision workflow, dashboards, and runbooks.

## Test plan

- Golden manifest for model/tool/subagent/approval/artifact journeys.
- Cross-tenant read and case-enumeration negatives.
- Secret, PII, path, and connector-argument redaction adversarial suite.
- Fixture executor canary proving zero live network/effect calls.
- Same-key replay and changed-digest conflict.
- Partial batch crash/resume without duplicate provider calls.
- Hard-gate scorer cannot be overridden by a model grader.
- Source-run deletion cascades to projections and indexes.
- Variant assignment is stable and feature-off preserves control.
- Promotion rollback restores prior revision atomically.
- Load test for projection overhead and bounded evaluation concurrency.

## Definition of done

- A fixed synthetic suite evaluates at least connector selection, web evidence,
  bulk filtering, parallel reads, conflicting writes, long context, and subagents.
- Every result is attributable to exact case, fixture, variant, model, prompt, tool,
  policy, scorer, and code revisions.
- No evaluation path can dispatch an external effect.
- Safety regressions block promotion; report and decision are auditable.
- Production projection is consented, redacted, deletable, and feature-gated.
- Operational dashboards, cost limits, backout, and incident runbook are shipped.

## Guardrails and open decisions

Guardrails:

- Never optimize solely for fewer tokens or calls.
- Never retain chain-of-thought; record observable actions and concise rationales only.
- Never silently convert production traffic into training data.
- Never let a model judge decide authorization or effect safety.

Open decisions:

1. Which synthetic suites are mandatory for the first promotion gate?
2. Is tenant-derived evaluation material ever allowed outside its tenant boundary?
3. Which model-graded dimensions provide enough value to justify cost and variance?
4. What statistical threshold and minimum sample size govern online promotion?
