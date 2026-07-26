# PRD-AR-F2 — Cache-aware prompt assembly

**Status:** proposed\
**Priority:** P1\
**Owners:** AI Runtime, Model Platform, Security\
**Depends on:** [D2 built-ins/subagents](../../generative-surfaces-v2-1/prds/PRD-D2-builtins-subagents.md),
[E1 accountability](../../generative-surfaces-v2-1/prds/PRD-E1-accountability-lifecycle.md), and
[F1 evaluation](PRD-AR-F1-harness-observability-evaluation-promotion.md)

## Goal

Create a deterministic, provider-aware prompt assembly contract that preserves a large
stable prefix across ordinary turns, isolates tenant- and authorization-sensitive
material, and makes every prompt fragment attributable without logging prompt bodies.

## Implementer brief

Read:

1. `services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py`.
2. `services/ai-backend/src/agent_runtime/execution/factory.py`.
3. `services/ai-backend/src/agent_runtime/execution/provider_kwargs.py`.
4. `services/ai-backend/src/agent_runtime/execution/models.py`.
5. `services/ai-backend/src/agent_runtime/capabilities/skills/`.
6. `services/ai-backend/src/agent_runtime/capabilities/mcp/`.
7. `services/ai-backend/src/agent_runtime/context/memory/`.

Keep current conditional capability guidance, permission-filtered tools, and provider
adapters. Do not make prompt caching a requirement for a model/provider that does not
support it.

## Problem and current strengths

The runtime assembles an authorized per-run agent and already avoids including absent
desktop capabilities. It conditionally adds workspace/code guidance and loads
independent registries concurrently. What is not yet a first-class contract is the
ordering, mutability, digest, and cache eligibility of each prompt fragment.

Timestamps, approval state, dynamic tool descriptions, memory, and policy text can
invalidate an otherwise reusable prefix. Untracked ordering changes also make
regression diagnosis difficult. Provider prompt caches are exact-prefix mechanisms:
semantic equivalence does not produce a hit.

## Objectives

1. Represent prompt construction as typed, ordered fragments.
2. Separate stable, scoped-context, volatile, and current-turn material.
3. Generate deterministic canonical bytes for identical assembly inputs.
4. Apply cache controls only through explicit provider adapters.
5. Bind cache eligibility to model, tenant-safe policy, capability, and prompt revisions.
6. Emit body-free cache and fragment metrics through UsageMeter.
7. Preserve current authorization and instruction precedence.

## Non-goals

- Sharing user-specific prompt content across tenants.
- Caching model outputs or tool results.
- Selecting relevant skills/tools/memories; other PRDs own retrieval.
- Guaranteeing latency or billing savings from a provider.
- Persisting provider cache identifiers as durable product state.
- Moving authorization decisions into prompt text.

## Interfaces consumed

- Authorized capability descriptors and revision.
- Selected skill cards/bodies and revision.
- scoped project/workspace context and memory refs.
- current run, approval, model, provider, locale, and user-message state.
- provider feature metadata and UsageMeter.

## Interfaces exposed

```text
PromptFragment
  fragment_id
  kind: stable | scoped_context | volatile | current_turn
  precedence
  content
  content_digest
  scope: global | tenant | org | user | conversation | run
  sensitivity
  cache_eligibility
  source_revision

PromptAssemblyPlan
  plan_id, plan_revision
  provider, model
  task_family
  task_family_profile_revision
  task_family_locked
  ordered_fragments[]
  stable_prefix_digest
  complete_system_digest
  capability_revision
  policy_revision
  cache_strategy

ProviderCacheStrategy
  provider, model_pattern
  supported
  breakpoint_rules[]
  ttl_class?
  minimum_prefix_tokens?
  max_breakpoints?
```

Ports:

- `PromptFragmentProvider.fragments(context)`.
- `PromptAssembler.assemble(fragments, strategy)`.
- `ProviderPromptDecorator.decorate(messages, plan)`.
- `PromptAssemblyObserver.record(plan, provider_response_metadata)`.

The model constructor receives rendered messages plus provider kwargs. Persisted
conversation messages remain provider-neutral.

Events:

- `prompt.assembled.v1`: plan/revision/digests/token counts only.
- `prompt.cache_observed.v1`: provider/model, write/read/miss/unsupported, token counts.

## Detailed design

### 1. Precedence and layers

Canonical order:

1. runtime identity and immutable safety contract;
2. operation/effect/tool protocol;
3. task-family static guidance;
4. authorized capability and skill index revisions;
5. scoped project/workspace/user context;
6. current plan, approval, and run state;
7. current user turn and ephemeral overlays.

Within a layer, fragments sort by `(precedence, fragment_id)`. Duplicate IDs conflict.
No dictionary/set iteration order may affect rendered bytes.

Task-family guidance is `scoped_context` by default, even when its source text is
static. F4 may select a different task family on the next request, so the fragment
normally sits after the reusable stable prefix. It may join the stable prefix only when
the conversation is explicitly locked to an immutable task-family profile.

### 2. Stable prefix

The stable prefix may contain only fragments whose bytes and authority meaning remain
valid for the cache key. Its digest input includes:

- assembly schema and policy revision;
- provider/model family;
- static capability bridge schema revision;
- fixed harness profile revision;
- tenant-safe localization revision.

For a profile-locked conversation, the digest also includes task family, task-family
profile revision, and the lock revision. An unlocked task-family fragment never
contributes to `stable_prefix_digest`; it always contributes to
`complete_system_digest`.

Do not include time, run IDs, credentials, approval state, user messages, retrieved
content, or live authorization decisions.

Org/user-specific stable material may be cached only under an equivalently scoped
provider request and never reused as a global prefix.

### 3. Provider adapters

Each provider adapter declares supported cache controls and validates outgoing payloads.
The generic assembler never writes provider-specific fields. Unsupported routes receive
the same semantic prompt without cache metadata.

Decorators operate on a deep copy of the outbound request. They do not mutate durable
messages or the assembly plan. Provider response metadata is normalized into
cache-write/read/miss/unsupported counters when available.

### 4. Tool schemas

Direct tool schemas are a separate deterministic block. A change to the authorized
direct toolset changes its revision and expected cache prefix. Deferred-capability
bridges from F3 should remain stable while the authorized catalog revision is placed
later as compact context.

### 5. Prompt inspection

Privileged development inspection can show fragment IDs, sizes, source revisions,
scope, cache eligibility, and digests. Body access follows the source data's
authorization and is never available through normal telemetry.

## Security, tenancy, privacy, and audit

- Scope is derived from verified runtime context, never model or caller labels.
- A fragment cannot declare a broader cache scope than its source.
- A runtime assertion rejects global-cache eligibility for tenant/user/conversation
  content.
- Secrets and credential material are forbidden prompt fragments.
- Retrieved skill, memory, connector, and workspace content is labelled untrusted and
  cannot precede the immutable safety contract.
- Prompt-plan revisions and feature changes are audited; bodies are not.
- Deletion affects source data and future assemblies; provider cache expiry remains an
  external deployment consideration documented in privacy notices.

## Performance and complexity budgets

- Assembly is single-pass `O(total prompt bytes + tool schema bytes)`.
- Digesting must not serialize a fragment more than once.
- Local assembly p95 below 10 ms for a 100 KiB prompt, excluding tokenization.
- Token counting may use an accurate provider tokenizer asynchronously; request-path
  fallback uses a bounded estimator.
- No additional model round trip.
- Stable-prefix byte identity target: at least 95% across consecutive ordinary turns
  with unchanged policy/capabilities.

## Failure, idempotency, and recovery

- Assembly is a pure deterministic function for fixed inputs.
- Unsupported/invalid cache metadata falls back to an undecorated request, with a
  metric; semantic prompt bytes remain unchanged.
- A duplicate fragment ID, invalid scope, or forbidden secret classification fails
  closed before the model call.
- Provider rejection retries once without optional cache metadata only when the
  request has not produced output and retry policy permits.
- Cache miss is normal, not an error.
- Persisted plan metadata permits diagnosis after provider caches expire.

## Observability and quality gates

Measure by provider/model/task family:

- tokens/bytes by fragment kind;
- stable-prefix and full-system digest churn;
- cache write/read/miss/unsupported tokens;
- provider-reported input processing and latency;
- assembly failures and fallback reasons;
- task success and instruction-following versus control.

F1 promotion requires no protected-task regression and no tenant-scope violation. A
lower input bill alone is not sufficient.

## Rollout and backout

1. Shadow-build plans alongside current prompt construction; compare semantic sections
   and token counts without sending.
2. Enforce deterministic assembly while leaving cache decoration off.
3. Enable decoration for one supported provider/model and internal tenants.
4. Expand by provider after cache-read telemetry and F1 results.
5. Make typed assembly the only prompt construction path.

One flag disables provider decoration; another returns to legacy rendering until final
cutover. Backout never changes persisted conversation content.

## Implementation slices

1. Contracts, scope validator, canonical renderer, and golden prompt fixtures.
2. Adapt existing static/runtime/tool/workspace prompts into fragment providers.
3. Shadow comparison and digest/token telemetry.
4. First provider cache decorator and response metadata normalization.
5. Additional supported adapters.
6. Remove legacy string concatenation after conformance.

## Test plan

- Golden byte-for-byte plans for each harness profile.
- Same inputs in randomized registry order produce identical bytes/digests.
- Time/current approval changes only volatile/current-turn fragments.
- Tool/policy revision invalidates the appropriate prefix.
- Switching an unlocked request from one F4 task family to another cannot reuse the
  first family's guidance; a locked-family revision change invalidates its prefix.
- Cross-tenant fragment cannot receive global cache eligibility.
- Prompt injection in retrieved content cannot precede safety/tool protocol.
- Unsupported provider receives semantically identical undecorated messages.
- Decorator deep-copy test proves persisted messages are unchanged.
- Provider cache rejection follows bounded fallback and UsageMeter attribution.
- Long skill/MCP indexes respect context caps.

## Definition of done

- Every system-prompt byte is attributable to a typed fragment and revision.
- Stable/contextual/volatile/current-turn ordering is deterministic.
- Supported provider adapters report real cache outcomes; unsupported models work
  unchanged.
- No cross-tenant or stale-authorization reuse is possible by construction and test.
- F1 shows acceptable answer quality and a measurable prefix-reuse benefit.
- Feature flags, dashboards, runbook, and backout are shipped.

## Guardrails and open decisions

Guardrails:

- Cacheability never weakens instruction precedence or live permission checks.
- Do not add filler to cross a provider's minimum cache size.
- Do not log prompt bodies to debug misses.
- Provider-specific metadata stays outside provider-neutral domain records.

Open decisions:

1. Which provider/model combinations are supported in the first release?
2. Should org-scoped static context be a separate cacheable prefix or always contextual?
3. Which tokenizer is authoritative for activation and observability?
4. How long should plan metadata be retained relative to source conversations?
