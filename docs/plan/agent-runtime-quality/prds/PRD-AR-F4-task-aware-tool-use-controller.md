# PRD-AR-F4 — Task-aware tool-use controller

**Status:** implemented\
**Priority:** P1\
**Owners:** AI Runtime, Applied AI, Product Safety\
**Depends on:** [A3 operation gateway](../../generative-surfaces-v2-1/prds/PRD-A3-operation-gateway.md),
[D2 built-ins/subagents](../../generative-surfaces-v2-1/prds/PRD-D2-builtins-subagents.md), and
[F1 evaluation](PRD-AR-F1-harness-observability-evaluation-promotion.md)\
**Integrates with:** [F3 capability discovery](PRD-AR-F3-policy-aware-capability-discovery.md)

## Goal

Improve tool-use correctness and speed with explicit task-family policies, visible
objectives, adaptive budgets, duplicate-work detection, and deterministic stopping
signals. Replace accumulating prompt admonitions with a measured runtime policy.

## Implementer brief

Read:

1. `services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py`.
2. `services/ai-backend/src/agent_runtime/capabilities/tool_budget_middleware.py`.
3. `services/ai-backend/src/agent_runtime/capabilities/tool_budget_guard.py`.
4. `services/ai-backend/src/agent_runtime/persistence/records/tool_budgets.py`.
5. `services/ai-backend/src/agent_runtime/execution/factory.py`.
6. `services/ai-backend/src/agent_runtime/observability/usage_meter.py`.
7. A3 and D2.

Keep the existing research discipline: distinct queries, limited duplicate searches,
progress checkpoints, per-tool caps, and citations. Generalize it by task family
without weakening hard operation budgets or approval policy.

## Desktop-first deployment and storage contract

The launch composition is the shipped `single_user_desktop` profile: Electron main supervises `backend`, `ai-backend`, `backend-facade`, and the local data services on loopback; the renderer still calls only the facade. This is a local application boundary, not a mandatory cloud dependency.

- Runtime-owned run/event/citation/artifact state must use existing `RuntimePorts`. Desktop defaults to the single-writer file adapter under `<userData>/agent-data/v1` with the in-process worker; tests use in-memory and future hosted deployments may use Postgres.
- Product configuration already owned by `backend` may use its existing embedded local Postgres database. This PRD must not add another database, daemon, queue, or network hop merely for desktop.
- Authorization is expressed as the signed-in local user, device capability grants, project/conversation scope, and provider credentials. Existing internal organization identifiers remain compatibility partition keys; they are not exposed as B2C team or administrator concepts.
- A future consumer web or opt-in sync product may add remote adapters behind the same ports and contracts. Sync, team administration, fleet policy, and always-online availability are not desktop launch dependencies.
- Feature flags resolve locally, work offline wherever no external provider is intrinsically required, and include a bounded disk/CPU/memory budget plus an immediate local backout path.

## Problem and current strengths

The web profile already instructs the agent to plan a few distinct searches, stop after
repeated low-yield calls, checkpoint progress, and respect enforced tool budgets. This
is stronger than a generic “use tools carefully” prompt.

One fixed policy is not suitable for every workflow. Five calls may be excessive for a
single record lookup and insufficient for a paginated export. Prompt-only rules cannot
reliably detect equivalent queries, repeated URLs, unchanged errors, or calls that do
not resolve a stated uncertainty.

## Objectives

1. Classify tasks into a small versioned set of policy families.
2. Require a concise plan and tool-use objective for expensive/multi-call work.
3. Enforce hard model/tool/cost/time budgets and softer family-specific guidance.
4. Detect duplicate requests, overlapping evidence, unchanged retries, and low yield.
5. Produce structured continue/stop/replan/escalate feedback.
6. Expose progress without revealing private reasoning.
7. Evaluate policies on fixed task families before changing defaults.

## Non-goals

- Reading private chain-of-thought.
- Guaranteeing that a selected tool is semantically correct.
- Replacing A3 authorization, D1 effect classification, or approvals.
- Ranking the capability catalog; F3 owns discovery.
- Fetching/searching web content; the research broker owns retrieval.
- Running durable cross-run goals or routines.

## Interfaces consumed

- User request metadata and explicit intent.
- Capability descriptors, cost/latency/rate-limit metadata, and operation outcomes.
- Tool/model budgets and UsageMeter.
- Citation/source refs, canonical URLs, result digests, error classes.
- F3 discovery telemetry when deferred mode is active.

## Interfaces exposed

```text
TaskPolicyProfile
  profile_id, revision
  task_family
  planning_requirement
  model_turn_limit
  tool_call_limits
  cost_limit, wall_time_limit
  duplicate_policy
  low_yield_policy
  checkpoint_interval
  escalation_policy

RunToolPlan
  plan_id, run_id, profile_revision
  objective
  steps[]
  success_evidence[]
  created_by: model | deterministic
  status

ToolUseIntent
  operation_id
  plan_step_id?
  uncertainty_or_objective
  expected_evidence_kind
  canonical_request_fingerprint

ToolUseFeedback
  disposition: continue | stop | replan | ask_user | blocked
  reason_code
  budget_remaining
  duplicate_of?
  new_evidence_count
```

Ports:

- `TaskPolicyResolver.resolve(request_context)`.
- `ToolUseController.before_operation(intent, descriptor, state)`.
- `ToolUseController.after_operation(outcome, evidence, state)`.
- `DuplicateDetector.compare(current, prior)`.
- `ProgressProjector.project(plan_state)`.

Events:

- `tool_policy.profile_selected.v1`
- `tool_policy.intent_recorded.v1`
- `tool_policy.feedback.v1`
- `tool_policy.budget_exhausted.v1`

## Detailed design

### 1. Task families

Initial closed set:

- public research;
- connected-record lookup;
- library grounding;
- file/workspace analysis;
- pure calculation/transformation;
- artifact drafting;
- effect proposal;
- code/test diagnosis;
- delegated analysis;
- unknown/general.

Classification is deterministic where explicit route/tool/user intent exists. An
optional bounded classifier may choose among the closed set but cannot grant tools,
increase budgets, or downgrade effect policy. Low confidence uses `unknown/general`.

### 2. Planning contract

Profiles can require a public plan before the first high-cost call. The plan contains
short task steps and expected evidence—not private reasoning. Simple one-call tasks
skip planning.

The model states a compact `uncertainty_or_objective` for governed calls. The controller
uses it for observability and duplicate/low-yield feedback; it does not accept the text
as authorization.

### 3. Budget hierarchy

Effective budgets are the minimum of:

- platform safety maximum;
- profile/user plan and user policy;
- run/task profile;
- capability/connector limit;
- model-declared smaller budget.

Hard limits are enforced outside the model. Soft thresholds produce a warning/replan
signal. Approval waiting time does not consume active tool execution time but remains
within run deadline policy.

### 4. Duplicate and yield detection

Canonical fingerprints normalize capability ID and validated arguments while excluding
idempotency keys and safe ordering noise. Research results additionally track canonical
URLs and evidence spans. Errors track normalized class and retry hints.

Reason codes:

- `exact_duplicate`;
- `semantic_query_overlap`;
- `same_sources_no_new_evidence`;
- `same_error_without_changed_input`;
- `budget_low`;
- `objective_satisfied`;
- `policy_requires_user_input`.

Semantic similarity is advisory; exact duplicates and repeated errors can be enforced.

### 5. Stop/replan feedback

After each operation, the controller updates evidence count, unresolved steps, spend,
and duplicate history. It returns bounded structured feedback to the model. A hard stop
ends further calls but still allows a transparent final response. A safety/policy block
cannot be overridden by a new plan.

### 6. Progress UX

Project plan state into existing typed run/activity events. Show step labels, status,
evidence count, blocking approval, and budget state. Do not display hidden reasoning or
raw connector arguments.

## Security, local-profile boundaries, privacy, and audit

- Profile and budget selection derive from verified profile/user/runtime context.
- The model cannot request a higher budget or different local profile.
- Intent and feedback text are treated as model-generated untrusted data and size
  bounded.
- Canonical fingerprints use keyed digests when arguments could reveal private user data.
- Tool results and arguments remain behind protected operation refs.
- Profile changes, overrides, hard-stop events, and user budget changes are
  audited.
- Effect authorization and approval always remain A3/A4/A5/D1 responsibilities.

## Performance and complexity budgets

- Before/after controller overhead p95 below 5 ms excluding optional semantic checks.
- Exact duplicate lookup `O(1)` by fingerprint.
- URL/source overlap `O(U)` in bounded unique sources; retain at most 500 per run.
- Semantic duplicate checks consider at most the last 20 comparable intents.
- No auxiliary model call on the default request path.
- Plan and feedback injected into context remain below 1,000 tokens combined.

## Failure, idempotency, and recovery

- Controller state is derived from durable operations/events and can be rebuilt after a
  worker restart.
- `before_operation` is idempotent by operation ID and intent digest.
- Duplicate feedback never marks an actual operation completed.
- Controller failure falls back to hard platform budgets and conservative unknown
  profile; it cannot remove authorization checks.
- Optional classifier failure selects `unknown/general`.
- Exhausted hard budgets persist as terminal for the run unless an authorized human
  creates a new run with a new budget.

## Observability and quality gates

Measure by profile:

- plan use/skips and time to first useful operation;
- tool precision and successful argument validation;
- exact/semantic duplicate rates;
- new evidence per call and repeated-source rate;
- stops, replans, user questions, and budget exhaustion;
- model turns, tool calls, tokens, cost, p50/p95 latency;
- task success, citation quality, user corrections, and approval abandonment.

F1 compares controller versions with the same model/tool fixtures. A reduction in calls
must not reduce supported-answer quality.

## Rollout and backout

1. Resolve profiles and record shadow feedback without changing execution.
2. Enable exact-duplicate warnings for public research.
3. Enforce unchanged-error and hard-budget rules already represented by existing caps.
4. Add visible plans and low-yield feedback by task family.
5. Enable deterministic stops after F1 evidence.
6. Expand profiles and retire overlapping prompt-only wording.

Backout disables controller enforcement while retaining existing hard budget
middleware. Profile revision is recorded per run for diagnosis.

## Implementation slices

1. Profile/plan/intent contracts and deterministic resolver.
2. Durable state reducer over operation events.
3. Fingerprint and exact duplicate/error detector.
4. Feedback middleware and existing budget integration.
5. Progress event projection.
6. Source-overlap and optional semantic advisory.
7. F1 cases, dashboards, and policy authoring runbook.

## Test plan

- One-call lookup skips planning and succeeds.
- Paginated task receives a larger bounded family budget.
- Exact duplicate is detected despite JSON key ordering.
- Changed pagination cursor is not a duplicate.
- Repeated same error without input change stops; retryable error with backoff may retry.
- Same URLs/no new spans trigger low-yield feedback.
- Model cannot increase its budget or change profile.
- Worker crash/rebuild yields identical state and remaining budget.
- Approval pause does not duplicate a pending operation.
- Cross-profile fingerprints and plan records cannot be read.
- F1 regression suite covers premature stop and runaway loops.

## Definition of done

- Every governed tool call is associated with a selected profile and hard budget.
- Multi-call tasks expose a concise objective/plan and progress state.
- Exact duplicate and unchanged-error loops are mechanically controlled.
- Policies are versioned, auditable, reconstructable, and evaluated by task family.
- Existing effect, approval, citation, and usage semantics remain intact.
- Feature flags, local diagnostics, backout, and user-facing documentation are complete.

## Guardrails and open decisions

Guardrails:

- Public progress is not chain-of-thought.
- Never use a prompt instruction as the only budget control.
- Never auto-stop solely from an uncalibrated semantic similarity score.
- Never retry a possibly applied effect through this controller.

Open decisions:

1. Which task families require a visible plan in the first release?
2. Should users be able to request a smaller budget directly?
3. What evidence threshold defines “objective satisfied” for research?
4. Which low-yield conditions warn versus enforce by default?
