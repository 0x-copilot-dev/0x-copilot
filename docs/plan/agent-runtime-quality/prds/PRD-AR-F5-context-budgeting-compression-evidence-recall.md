# PRD-AR-F5 — Context budgeting, compression, and evidence recall

**Status:** proposed\
**Priority:** P1\
**Owners:** AI Runtime, Knowledge Platform, Data Governance\
**Depends on:** [A2 artifact repository](../../generative-surfaces-v2-1/prds/PRD-A2-artifact-repository.md),
[B1 agent-authored artifacts](../../generative-surfaces-v2-1/prds/PRD-B1-agent-authored-artifacts.md),
[E1 accountability](../../generative-surfaces-v2-1/prds/PRD-E1-accountability-lifecycle.md), and
[F1 evaluation](PRD-AR-F1-harness-observability-evaluation-promotion.md)

## Goal

Allocate model context deliberately across current intent, policy, relevant
instructions, plans, evidence, and history. Compress or offload large material without
losing source provenance, and let the agent retrieve exact evidence spans when a
summary is insufficient.

## Implementer brief

Read:

1. `services/ai-backend/src/agent_runtime/context/memory/token_budget.py`.
2. `services/ai-backend/src/agent_runtime/context/memory/summarization.py`.
3. `services/ai-backend/src/runtime_worker/tool_result_offload.py`.
4. `services/ai-backend/src/runtime_adapters/file/offload.py`.
5. `services/ai-backend/src/agent_runtime/context/memory/`.
6. `services/ai-backend/src/agent_runtime/capabilities/skills/`.
7. A2, B1, and E1.

Reuse the composed backend routes for memory, drafts, subagent output, workspace, and
large results. This PRD defines one selection/compression policy over those sources; it
does not create another blob store.

## Desktop-first deployment and storage contract

The launch composition is the shipped `single_user_desktop` profile: Electron main supervises `backend`, `ai-backend`, `backend-facade`, and the local data services on loopback; the renderer still calls only the facade. This is a local application boundary, not a mandatory cloud dependency.

- Runtime-owned run/event/citation/artifact state must use existing `RuntimePorts`. Desktop defaults to the single-writer file adapter under `<userData>/agent-data/v1` with the in-process worker; tests use in-memory and future hosted deployments may use Postgres.
- Product configuration already owned by `backend` may use its existing embedded local Postgres database. This PRD must not add another database, daemon, queue, or network hop merely for desktop.
- Authorization is expressed as the signed-in local user, device capability grants, project/conversation scope, and provider credentials. Existing internal organization identifiers remain compatibility partition keys; they are not exposed as B2C team or administrator concepts.
- A future consumer web or opt-in sync product may add remote adapters behind the same ports and contracts. Sync, team administration, fleet policy, and always-online availability are not desktop launch dependencies.
- Feature flags resolve locally, work offline wherever no external provider is intrinsically required, and include a bounded disk/CPU/memory budget plus an immediate local backout path.

## Problem and current strengths

The runtime already has token-budget helpers, continuity summaries, large-result
offload, scoped memory paths, and retrievable artifact routes. Deep Agents also
provides summarization and filesystem context. These mechanisms prevent individual
large outputs from blindly entering the transcript.

They do not yet form an explicit contract for which representation should enter a
model call, which content may be summarized, how much loss occurred, or how a model can
recover the exact source behind a compressed statement. Uncoordinated middleware can
summarize the same material twice or discard current constraints while retaining stale
tool traces.

## Objectives

1. Produce a deterministic context plan for every model invocation.
2. Protect non-negotiable policy/current-intent material from eviction.
3. Rank eligible context within verified scope and allocate explicit token budgets.
4. Offload large raw material to A2 and inject bounded previews/summaries.
5. Attach provenance, source spans, and lossiness to every compression.
6. Expose a read-only evidence-span retrieval capability.
7. Measure context size, cache interaction, recall, and summary-induced errors.

## Non-goals

- Learning durable facts or preferences from conversation.
- Ranking capability schemas or installing skills.
- Treating a summary as an authoritative source.
- Storing chain-of-thought.
- Circumventing source ACLs through an artifact ref.
- Replacing provider maximum-context enforcement.

## Interfaces consumed

- Current request, system/policy fragments, direct capability schemas, selected skills,
  active plan, conversation messages, operation results, citations, artifacts, and
  scoped memories.
- Model context limit and reserved output budget.
- Source authorization, retention, deletion, and legal-hold decisions.
- A2 blob/ref and B1 artifact provenance contracts.

## Interfaces exposed

```text
ContextCandidate
  candidate_id
  kind
  source_ref
  scope
  trust_label
  priority_class
  relevance_score?
  original_tokens
  representation_options[]

ContextRepresentation
  mode: full | excerpt | summary | reference | omitted
  content_ref?
  inline_content?
  token_count
  source_spans[]
  lossiness: none | extractive | abstractive
  generated_by?
  generated_at?

ContextPlan
  plan_id, run_id, model_call_id
  model_context_limit
  reserved_output_tokens
  fixed_tokens
  allocated_tokens
  candidate_decisions[]
  policy_revision
  plan_digest

EvidenceSpanRequest
  source_ref
  start?, end?, selector?
  max_bytes
```

Ports:

- `ContextCandidateProvider.list(context)`.
- `ContextBudgeter.plan(candidates, limits, policy)`.
- `CompressionService.compress(source_ref, target, purpose)`.
- `EvidenceResolver.resolve(typed_ref, selector, runtime_context)` implemented by each
  source domain.
- `EvidenceResolverRegistry.resolve(...)`, which dispatches only registered ref types
  after current authorization.
- `EvidenceReader.read_span(request, runtime_context)`.

Sole model-facing evidence hydration tool:

```text
read_evidence(source_ref, selector?, max_chars?)
```

G1 Library, G3 conversation history, operation/tool trajectories, artifacts, and
reviewed memory issue opaque typed refs and register resolvers behind this port. They
do not add competing model-visible `open_*_evidence` tools. Source-domain internal
ports may keep descriptive names, but all model requests traverse this one bounded,
audited reader.

Events:

- `context.plan.created.v1`
- `context.content.compressed.v1`
- `context.evidence.read.v1`
- `context.item.omitted.v1`

Events contain refs, sizes, reasons, and revisions—not bodies.

## Detailed design

### 1. Priority classes

Highest to lowest:

1. immutable safety, authority, and operation/effect protocol;
2. current user intent and directly applicable explicit constraints;
3. current approval/gate state;
4. active plan and unresolved operations;
5. selected reviewed skills and relevant source evidence;
6. recent conversation and continuity summary;
7. recalled memory/project context;
8. old completed tool traces and low-relevance history.

Lower classes cannot evict higher classes. Untrusted content remains clearly delimited
regardless of priority.

### 2. Budget calculation

`available = model_limit - reserved_output - fixed_system - direct_tool_schemas -
safety_margin`.

If fixed content exceeds the safe limit, fail before the model call with a diagnostic;
do not truncate policy or schemas silently. Variable classes receive configurable
minimum/maximum shares. The planner records every exclusion reason.

### 3. Representation selection

Prefer in order:

- full bounded content when small and highly relevant;
- exact source excerpt when a known span supports the task;
- source-linked abstractive summary for broad material;
- compact metadata/reference for retrievable raw content;
- omission when irrelevant, unauthorized, expired, or superseded.

An abstractive summary includes source refs, source digests, summarizer model/prompt
revision, and a statement that it is compressed evidence. It cannot originate a
citation unless the underlying source span is retained.

### 4. Compression

Small sources use bounded direct/extractive content. Larger sources are chunked by
structure where possible, summarized with bounded concurrency, then synthesized to a
target size. Chunks and synthesis preserve source-span mappings.

Compression is cacheable by `(source_digest, target_tokens, policy_revision,
summarizer_revision)`. Source changes create a new entry. Failure falls back to a safe
excerpt/reference, never unbounded raw content.

### 5. Evidence recall

`read_evidence` accepts only opaque refs already visible to the run or independently
authorized now. It returns a bounded exact span with provenance. It cannot enumerate
other artifacts or bypass deleted/expired source state.

The resolver registry treats the ref type as routing metadata, not authority. Each
source resolver reauthorizes the current subject, pins source revision/digest, applies
its retention/deletion state, and returns a common evidence envelope. Unknown,
cross-profile, stale, superseded, or deleted refs fail with typed results. A batch may
contain multiple source types, but per-source limits and aggregate byte/token caps
still apply.

### 6. Long conversations

Continuity summaries cover resolved history while a recent-turn window remains exact.
Active constraints, pending approvals, uncertain side effects, citations, and
unresolved user questions are structured fields, not left solely to prose
summarization.

## Security, local-profile boundaries, privacy, and audit

- Candidate providers derive profile/user/project scope from verified runtime context.
- Every read and summary rechecks the underlying source ACL and retention state.
- Prompt-injection screening and untrusted labels survive compression.
- Summarizers receive the minimum source content and no connector credentials.
- Sensitive sources may prohibit auxiliary-model compression by policy.
- Deletion cascades to summaries, indexes, previews, and cached chunks unless held.
- Evidence reads and compression model calls are usage-metered and audited by refs.

## Performance and complexity budgets

- Planning is `O(C log C)` for `C` bounded candidates; initial cap 500.
- Request-path planning p95 below 15 ms excluding tokenization/compression.
- Compression never runs synchronously if an existing bounded excerpt can answer the
  current call; async precomputation is preferred.
- Inline context from one external result defaults below 8,000 tokens.
- Evidence read defaults below 16 KiB and has a hard 64 KiB ceiling.
- Summarization has per-run token, cost, concurrency, and wall-time budgets.
- Context must retain configured output/safety margin under tokenizer variance.

## Failure, idempotency, and recovery

- Context planning is deterministic for candidate/policy/tokenizer revisions.
- Compression idempotency binds source digest and all generation revisions.
- Worker restart reuses completed immutable summaries and discards incomplete ones.
- Missing/deleted source yields `source_unavailable`, not stale cached content.
- Summarizer failure falls back to excerpt/reference and emits a reason.
- Token-count underestimation triggers one smaller replan before model invocation.
- A provider context-length rejection retries only with the recorded emergency policy,
  never by dropping protected classes.

## Observability and quality gates

Measure:

- tokens by class and representation;
- compression/offload/cache hit rate and latency/cost;
- omitted candidates by reason;
- evidence-span reads following summaries;
- context-limit errors and emergency replans;
- unsupported claim, stale-context, missed-constraint, and summary-loss rates;
- task success and provider cache effects versus control.

F1 must include long-conversation, large-tool-output, conflicting-fact, and pending
approval cases.

## Rollout and backout

1. Shadow-plan existing model calls and compare allocation.
2. Adopt plan metadata while preserving existing context bytes.
3. Route large tool results through canonical representations.
4. Enable source-linked compression for low-risk sources.
5. Add `read_evidence`.
6. Enable full variable-context selection by task family.
7. Retire overlapping summarization decisions after conformance.

Backout restores existing context assembly; immutable summaries/artifacts remain
readable and deletable.

## Implementation slices

1. Candidate/representation/plan contracts and golden journeys.
2. Providers for conversation, tool results, artifacts, skills, and memory.
3. Deterministic planner and shadow telemetry.
4. Source-linked compression service and cache.
5. Evidence reader and model-facing bounded tool.
6. Long-conversation structured continuity integration.
7. Enforcement, dashboards, and migration of legacy summaries.

## Test plan

- Protected policy/current constraint cannot be evicted.
- Unauthorized candidate is absent from plan and cannot be read by guessed ref.
- Large result becomes artifact + bounded representation.
- Summary citations resolve to exact retained source spans.
- Deleted source invalidates summary/evidence reads.
- Injection content remains untrusted after summary.
- Tokenizer undercount follows one bounded replan.
- Compression crash/retry does not duplicate charges or publish partial output.
- Conflicting recent fact outranks stale summary.
- Pending/uncertain effect state survives conversation compression.
- Library and conversation refs hydrate through the same `read_evidence` schema while
  retaining source-specific ACL, revision, deletion, and citation behavior.
- Cross-profile and legal-hold/deletion matrix.

## Definition of done

- Every model call has a reconstructable context plan with inclusion/omission reasons.
- Large content is bounded and source-addressable.
- Abstractive summaries declare lossiness and exact generation/source revisions.
- Evidence recall cannot bypass source ACL, retention, or deletion.
- Exactly one model-facing evidence hydration tool is registered.
- F1 demonstrates acceptable constraint retention, groundedness, latency, and cost.
- Feature flags, local diagnostics, backout, and troubleshooting guide are complete.

## Guardrails and open decisions

Guardrails:

- Never truncate immutable safety/authority content.
- Never cite a summary without resolvable underlying evidence.
- Never place untrusted retrieved content in the system-policy tier.
- Never retain private reasoning as a continuity artifact.

Open decisions:

1. Which sources may use auxiliary models for compression?
2. What default class allocations should each model family receive?
3. Should evidence spans be immutable artifacts or views over retained sources?
4. How should user-pinned context interact with hard context limits?
