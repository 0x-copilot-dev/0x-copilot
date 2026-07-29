# PRD-AR-F10 — Model invocation reliability and routing

**Status:** implemented\
**Priority:** P2\
**Owners:** Model Platform, AI Runtime, Security/Privacy, FinOps\
**Depends on:** [E1 accountability](../../generative-surfaces-v2-1/prds/PRD-E1-accountability-lifecycle.md) and
[F1 evaluation](PRD-AR-F1-harness-observability-evaluation-promotion.md)

## Goal

Choose and invoke models under explicit capability, BYOK, privacy, region, cost, and
reliability policy. Retry or fall back only when semantically safe, keep each attempt
attributable, and never duplicate external effects by retrying an entire run blindly.

## Implementer brief

Read:

1. `services/ai-backend/src/agent_runtime/execution/models.py`.
2. `services/ai-backend/src/agent_runtime/execution/provider_kwargs.py`.
3. `services/ai-backend/src/agent_runtime/api/model_catalog.py`.
4. `services/ai-backend/src/agent_runtime/api/model_enablement.py`.
5. `services/ai-backend/src/agent_runtime/observability/usage_meter.py`.
6. `services/ai-backend/src/runtime_worker/run_metrics.py`.
7. `services/ai-backend/src/runtime_worker/loop.py`.
8. `services/backend/src/backend_app/provider_keys/`.
9. E1 and F1.

Preserve the current strengths: a provider allowlist shared with the model catalog,
request/default resolution, reasoning-depth budgets, user-key availability checks,
BYOK precedence, custom-endpoint handling, training opt-out, region pinning that fails
closed, provider stream adapters, and per-call usage records.

## Desktop-first deployment and storage contract

The launch composition is the shipped `single_user_desktop` profile: Electron main supervises `backend`, `ai-backend`, `backend-facade`, and the local data services on loopback; the renderer still calls only the facade. This is a local application boundary, not a mandatory cloud dependency.

- Runtime-owned run/event/citation/artifact state must use existing `RuntimePorts`. Desktop defaults to the single-writer file adapter under `<userData>/agent-data/v1` with the in-process worker; tests use in-memory and future hosted deployments may use Postgres.
- Product configuration already owned by `backend` may use its existing embedded local Postgres database. This PRD must not add another database, daemon, queue, or network hop merely for desktop.
- Authorization is expressed as the signed-in local user, device capability grants, project/conversation scope, and provider credentials. Existing internal organization identifiers remain compatibility partition keys; they are not exposed as B2C team or administrator concepts.
- A future consumer web or opt-in sync product may add remote adapters behind the same ports and contracts. Sync, team administration, fleet policy, and always-online availability are not desktop launch dependencies.
- Feature flags resolve locally, work offline wherever no external provider is intrinsically required, and include a bounded disk/CPU/memory budget plus an immediate local backout path.

## Problem and current strengths

Provider errors are not equivalent. A pre-dispatch timeout may be safe to retry; a
partially streamed response may require a new attempt with disclosed discontinuity; a
whole-run retry after tool effects can duplicate work. Fallback can also violate an
explicit user-selected model, BYOK requirement, data region, context size, tool
calling, reasoning, or media capability.

The runtime has robust model selection and policy kwargs, plus worker command retry.
It needs a single model-invocation policy that distinguishes attempt-level provider
recovery from run/effect recovery and records why any alternate route was eligible.

## Objectives

1. Define model capability and deployment metadata as a versioned catalog.
2. Resolve an ordered eligible route set under user/provider-privacy/region/BYOK policy.
3. Classify provider failures into safe retry, safe fallback, terminal, and ambiguous.
4. Bound attempts, deadline, tokens, and spend across the route set.
5. Persist an invocation and attempt ledger before each call.
6. Preserve stream semantics and clearly identify restarts/fallbacks.
7. Use F1 evidence to approve route-policy changes by task family.

## Non-goals

- Automatically choosing the cheapest model regardless of user intent or quality.
- Falling back from an explicit user/BYOK route without allowed policy.
- Retrying tool operations or external effects.
- Normalizing away material model capability differences.
- Persisting plaintext provider keys/endpoints in invocation records.
- Treating provider marketing metadata as sufficient qualification.

## Interfaces consumed

- `ModelSelection`, `ModelConfig`, model catalog/enablement, reasoning depth, provider
  keys/endpoints in ephemeral runtime context, privacy/region policies.
- Provider adapters, context/tool requirements, UsageMeter, run deadline/budget.
- F1 qualification reports and E1 audit/retention.

## Interfaces exposed

```text
ModelDeploymentDescriptor
  deployment_id
  provider, model_name
  capabilities:
    streaming, tools, structured_output, reasoning, vision, audio
  max_input_tokens, max_output_tokens
  regions[]
  credential_modes[]
  privacy_features[]
  price_revision
  health_state
  descriptor_revision

ModelInvocationRequirements
  task_family
  required_capabilities[]
  minimum_context
  user_selection?
  byok_policy
  region
  training_opt_out
  max_cost, deadline
  fallback_policy

ModelRoutePlan
  route_plan_id, revision
  eligible_deployments[]
  exclusion_reasons[]
  max_attempts
  route_digest

ModelInvocation
  invocation_id, run_id, purpose
  requirements_digest
  route_plan_id
  status
  terminal_attempt_id?

ModelAttempt
  attempt_id, invocation_id, ordinal
  deployment_id
  request_digest
  started_at, completed_at?
  first_token_at?
  failure_class?
  provider_request_ref?
  usage_record_id?
```

Ports:

- `ModelRoutePlanner.plan(requirements, runtime_context)`.
- `ModelInvocationExecutor.invoke(plan, request)`.
- `ProviderFailureClassifier.classify(exception/response)`.
- `ProviderHealthTracker.observe(attempt)`.

Events:

- `model.invocation.planned.v1`
- `model.attempt.started.v1`
- `model.attempt.failed.v1`
- `model.invocation.rerouted.v1`
- `model.invocation.completed.v1`

## Detailed design

### 1. Eligibility

Resolve deployments by intersection:

- supported provider/model and enabled catalog entry;
- required tool/stream/context/reasoning/media capabilities;
- user explicit model and fallback preference;
- BYOK/deployment credential availability;
- privacy/training opt-out support and workspace/user policy;
- configured data region;
- user allow/deny policy and budget;
- current health/circuit state.

An explicit model defaults to no cross-model fallback unless product policy and the
user-facing contract say otherwise. Region mismatch continues to fail closed.

### 2. Route order

Use deterministic policy, not an unconstrained model:

1. exact selected/default deployment;
2. same model/provider alternate deployment in the same region/credential mode;
3. approved equivalent model routes qualified by F1 and policy.

Cost may break ties within the same qualification tier. The planner persists all
exclusion reason codes without secrets.

### 3. Failure taxonomy

Initial closed classes:

- `pre_dispatch_transient`: connect/DNS/rate-limit before accepted request;
- `provider_overloaded`;
- `request_invalid`;
- `auth_invalid`;
- `region_unavailable`;
- `policy_incompatible`;
- `context_exceeded`;
- `stream_interrupted_before_content`;
- `stream_interrupted_after_content`;
- `ambiguous_provider_state`;
- `cancelled`;
- `deadline_exceeded`.

Retry/fallback matrix is checked in and versioned. Unknown maps to terminal/ambiguous,
not retry.

### 4. Attempt execution

Persist `ModelAttempt.started` before dispatch. Apply per-attempt timeout within the
remaining invocation/run deadline and budget. Merge provider kwargs from verified
policy at the last responsible moment; never serialize the resulting API key.

An alternate attempt gets a new attempt ID and UsageMeter record. Aggregate billing
includes failed attempts where the provider reports usage.

### 5. Streaming

No attempt's tokens are silently concatenated with a replacement attempt. If failure
occurs before user-visible content, fallback may replace it transparently while events
retain attempt identity. After visible content, default is terminal partial output with
a retry offer; automatic restart requires an explicit product contract and emits a
visible discontinuity.

Tool calls emitted by a model create operation state outside this invocation. Once any
external operation is admitted, retrying the whole model/run history must account for
that durable observation and cannot replay the operation.

### 6. Circuit breaking

Provider health tracks bounded rolling outcomes per installation/provider endpoint,
without user payloads. Open circuits exclude new automatic routes for a cooldown while allowing
local diagnostic probes. BYOK auth failure is user-key-specific and must not open a global
provider circuit.

### 7. Context recovery

Context-limit errors may request one F5 replan if protected content remains intact and
the model route still qualifies. They are not solved by silently selecting a
larger-context model unless fallback policy permits it.

## Security, local-profile boundaries, privacy, and audit

- Keys remain ephemeral runtime values sourced from TokenVault; records store key
  source class/hint at most.
- Custom endpoints remain backend-validated and cannot be introduced by model output.
- User opt-out and region are one-way constraints; fallback cannot weaken them.
- Health metrics distinguish global deployment from user-specific credential failure.
- Invocation/attempt/route/policy decisions are audited without prompt bodies or keys.
- User profile-level budgets and route policies derive from verified identity.
- Provider request IDs are protected refs where they may reveal account information.

## Performance and complexity budgets

- Route planning `O(D)` over a bounded deployment catalog; p95 below 5 ms.
- Default maximum attempts: 2; hard platform maximum: 3.
- Total attempt deadlines cannot exceed the original invocation/run deadline.
- Failed-attempt spend counts against the same invocation budget.
- No hedge/parallel model calls in the first release.
- Circuit lookup/update p95 below 2 ms.
- Fallback may improve availability but must report added latency/cost separately.

## Failure, idempotency, and recovery

- Invocation idempotency binds run/model-call position, purpose, request digest, and
  route-plan revision.
- Attempt identity is allocated/persisted before dispatch.
- Worker crash with an open attempt becomes `ambiguous`; provider-specific request
  status may reconcile where supported.
- Do not blindly repeat ambiguous attempts after a tool call or visible stream.
- Same invocation key/same digest resumes/returns terminal state; changed digest
  conflicts.
- Circuit store failure defaults to the primary eligible route with bounded attempts;
  it cannot broaden eligibility.
- Usage finalization is exactly once per attempt record.

## Observability and quality gates

Measure:

- route plans and exclusions by reason;
- primary success, retries, fallbacks, terminal/ambiguous outcomes;
- time to first token, total latency, and added fallback latency;
- tokens/cost by attempt including failed attempts;
- circuits opened/probed/recovered;
- region/privacy/BYOK rejection correctness;
- partial-stream and context-replan outcomes;
- task success/quality by route and task family.

Alerts cover route-policy bypass, region/privacy mismatch, attempt storms, ambiguous
state, missing usage finalization, and provider degradation.

## Rollout and backout

1. Record descriptors, route plan, and attempt ledger while using current single route.
2. Classify failures in shadow; compare developer diagnoses.
3. Enable same-deployment safe retries for pre-content transient failures.
4. Enable same-model/same-policy alternate deployment routes.
5. Qualify limited equivalent-model fallback with F1 and explicit product policy.
6. Add circuit exclusion after health thresholds are proven.

Flags independently disable retries, alternate deployment, equivalent-model fallback,
and circuits. Backout returns to the exact primary route without modifying user model
settings.

## Implementation slices

1. Deployment/requirements/route/invocation/attempt contracts and golden fixtures.
2. Catalog adapter over existing model catalog/enablement.
3. Attempt ledger and UsageMeter integration.
4. Failure classifier and bounded same-route retry.
5. Alternate deployment and fallback planner.
6. Streaming discontinuity and ambiguous-state handling.
7. Circuit breaker, F1 qualification, dashboards, and runbook.

## Test plan

- Explicit model/no-fallback policy never changes model.
- BYOK user key precedence and no key leakage in records/logs.
- Region/opt-out incompatibility fails closed across all routes.
- Required tools/context/stream capability filters deployments.
- Pre-dispatch transient retry creates a second attempt, not a second invocation.
- Post-content stream failure is visibly partial and not silently replaced.
- Tool operation between model calls is not replayed by invocation retry.
- User-key auth failure does not open global circuit.
- Worker crash yields ambiguous attempt and bounded reconciliation.
- Attempt budgets/deadline aggregate correctly.
- Same-key replay and changed-digest conflict.
- F1 qualification blocks a lower-quality “equivalent” route.

## Definition of done

- Every model call has a persisted requirements/route/attempt lineage.
- Fallback never weakens explicit model, BYOK, region, privacy, capability, or budget
  constraints.
- Retry policy is failure-class- and stream-state-specific.
- Each attempt is independently metered and auditable.
- Worker/process failure does not cause blind model/run/effect replay.
- Flags, dashboards, qualification reports, backout, and incident runbook are complete.

## Guardrails and open decisions

Guardrails:

- Never treat whole-run worker retry as a model fallback mechanism.
- Never hide a cross-model change from the product contract.
- Never route around user privacy/region/BYOK choices.
- Never parallel-hedge expensive model calls without a separate approved design.

Open decisions:

1. Which model pairs, if any, qualify as equivalent for the first release?
2. Should explicit user selection expose an opt-in fallback preference?
3. Which provider request-status APIs support ambiguous-attempt reconciliation?
4. Should health/circuit state be process-local or shared at initial scale?
