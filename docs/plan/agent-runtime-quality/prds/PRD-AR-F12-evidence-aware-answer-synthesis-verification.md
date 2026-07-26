# PRD-AR-F12 — Evidence-aware answer synthesis and verification

**Goal:** Produce final answers that cover the user's requested deliverables, bind
material factual claims to authorized evidence, reconcile known conflicts and
freshness, communicate uncertainty honestly, and invoke a targeted repair only when
deterministic verification finds a concrete defect.

## Metadata

| Field                   | Value                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Status                  | Proposed                                                                                                                                                                                                                                                                                                                                                                                                              |
| Priority                | P1                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Wave                    | F — harness quality and efficiency                                                                                                                                                                                                                                                                                                                                                                                    |
| Primary owner           | `ai-backend` answer-synthesis and finalization middleware                                                                                                                                                                                                                                                                                                                                                             |
| Supporting owners       | Citation/evidence adapters, shared chat surface, F1 evaluation owners                                                                                                                                                                                                                                                                                                                                                 |
| Depends on              | [F1 evaluation and promotion](./PRD-AR-F1-harness-observability-evaluation-promotion.md), [F4 tool-use controller](./PRD-AR-F4-task-aware-tool-use-controller.md), [F5 context and evidence recall](./PRD-AR-F5-context-budgeting-compression-evidence-recall.md), [E1 accountability lifecycle](../../generative-surfaces-v2-1/prds/PRD-E1-accountability-lifecycle.md); source-specific grounding PRDs when enabled |
| Rollout flag            | `ANSWER_VERIFICATION_ENABLED`, with task-family and verification-profile cohorts                                                                                                                                                                                                                                                                                                                                      |
| Primary success measure | Lower unsupported-claim, missed-requirement, stale-source, and citation-error rates without adding a second model call to valid answers                                                                                                                                                                                                                                                                               |

## Implementer brief

Read before implementation:

1. `services/ai-backend/src/agent_runtime/execution/providers/citation_pipeline.py`.
2. `services/ai-backend/src/agent_runtime/execution/providers/citation_extraction.py`.
3. `services/ai-backend/src/agent_runtime/capabilities/citation_capturing_tool.py`.
4. `services/ai-backend/src/agent_runtime/capabilities/citation_projection.py`.
5. `services/ai-backend/src/agent_runtime/capabilities/citation_resolver.py`.
6. `services/ai-backend/src/agent_runtime/capabilities/citations.py`.
7. `services/ai-backend/src/agent_runtime/execution/`.
8. `services/ai-backend/src/agent_runtime/persistence/records/citations.py`.
9. `services/ai-backend/src/runtime_worker/`.
10. `services/ai-backend/tests/integration/citations/`.
11. F1, F4, F5, G1–G3, and E1.

F1 owns offline/shadow measurement, experiment records, and promotion. F5 owns context
allocation and evidence hydration. G1, G2, and G3 own Library, public-web, and
conversation evidence respectively. Existing citation capture owns source ordinals and
rendering. This PRD owns only the final-answer contract, deterministic finalization
checks, conflict/freshness presentation, and bounded repair decision.

## Desktop-first deployment and storage contract

The launch composition is the shipped `single_user_desktop` profile: Electron main supervises `backend`, `ai-backend`, `backend-facade`, and the local data services on loopback; the renderer still calls only the facade. This is a local application boundary, not a mandatory cloud dependency.

- Runtime-owned run/event/citation/artifact state must use existing `RuntimePorts`. Desktop defaults to the single-writer file adapter under `<userData>/agent-data/v1` with the in-process worker; tests use in-memory and future hosted deployments may use Postgres.
- Product configuration already owned by `backend` may use its existing embedded local Postgres database. This PRD must not add another database, daemon, queue, or network hop merely for desktop.
- Authorization is expressed as the signed-in local user, device capability grants, project/conversation scope, and provider credentials. Existing internal organization identifiers remain compatibility partition keys; they are not exposed as B2C team or administrator concepts.
- A future consumer web or opt-in sync product may add remote adapters behind the same ports and contracts. Sync, team administration, fleet policy, and always-online availability are not desktop launch dependencies.
- Feature flags resolve locally, work offline wherever no external provider is intrinsically required, and include a bounded disk/CPU/memory budget plus an immediate local backout path.

## Problem statement

Better retrieval and tool selection do not guarantee a better final answer. A run may
find the right sources and still:

- omit one of several requested deliverables;
- make a material factual claim without linking the source that supports it;
- cite a search snippet for a detail that only a full source could support;
- use a deleted, stale, superseded, or authorization-lost reference;
- flatten conflicting sources into unjustified certainty;
- present old information as current;
- hide partial completion or unresolved tool failures; or
- spend another full model call on every answer in the name of quality.

F1 can detect these failures in evaluation, but it does not own request-time response
construction. F4 can stop unnecessary calls, but it does not prove the answer covers
the task. Source PRDs return evidence, but none owns cross-source final synthesis.
Subagent verification applies only to child result envelopes.

The runtime needs an answer envelope that makes requirements, claims, evidence,
conflicts, freshness, uncertainty, and unresolved work inspectable before the
authoritative `final_response` event. Most answers must pass through one model
generation. A second model call is permitted only after deterministic checks identify
specific repairable failures.

## Current state and strengths to preserve

- Runtime events preserve monotonic ordering and emit one authoritative final response.
- Citation capture and projection bind tool results to stable ordinals and source
  metadata.
- Tool, subagent, artifact, approval, and error records expose structured outcomes
  without relying on prose.
- F4 defines task steps, expected evidence, budgets, and objective-satisfied decisions.
- F5 defines protected evidence refs and bounded hydration.
- G1–G3 define exact evidence refs, source digests, retrieval times, and reauthorization.
- F9 requires claim/evidence links for delegated child results.
- E1 governs retention, redaction, export, and deletion.

There is no composed final-answer schema, requirement ledger, cross-source conflict
view, freshness policy, deterministic verifier, or targeted repair loop.

## Objectives and outcomes

1. Compile explicit user deliverables and F4 completion criteria into a bounded answer
   requirement ledger.
2. Require material externally verifiable claims to carry authorized evidence refs or
   an explicit uncertainty/source-needed status.
3. Revalidate source existence, scope, digest, and citation locators before finalization.
4. Detect and surface known source conflicts, time mismatch, and unresolved operation
   failures.
5. Preserve clear distinctions among observed fact, source attribution, inference,
   estimate, recommendation, and unknown.
6. Run deterministic checks without another model call on the valid fast path.
7. Repair only the failed fields/sections, with one bounded call by default.
8. Emit a useful transparent partial answer when a defect cannot be repaired within
   policy or budget.
9. Evaluate correctness, requirement coverage, citation support, calibration, latency,
   and user corrections through F1.

### Launch gates

- No additional model call for at least 90% of eligible answers after cohort tuning.
- Zero unauthorized, deleted, expired, or digest-mismatched evidence rendered as valid.
- At least 30% lower unsupported material-claim rate on grounded-answer suites.
- At least 25% lower missed-explicit-deliverable rate on multi-requirement suites.
- At least 99% syntactic citation resolution for answers marked verified.
- p95 deterministic verification below 75 ms excluding evidence reauthorization I/O.
- p95 fast-path finalization overhead below 250 ms.
- No statistically meaningful regression in answer helpfulness, task success, first
  useful activity, or safety.

## Scope

- Answer requirement compilation
- Typed final-answer envelope and machine-readable claim manifest
- Claim classification and evidence-binding rules
- Source reauthorization, digest, locator, and freshness checks
- Known conflict-set reconciliation requirements
- Uncertainty and partial-completion disclosure
- Deterministic finalization checks
- One bounded targeted repair call by default
- Verified, degraded, and blocked final-response states
- Citation/provenance projection into existing chat rendering
- F1 evaluation, telemetry, rollout, and backout

## Non-goals

- Replacing F1 experiments, graders, or promotion gates
- Creating another evidence store, citation ledger, search index, or source resolver
- Retrieving new evidence after the tool-use budget is closed unless F4 explicitly
  reopens one bounded read step
- Running a generic model judge on every answer
- Storing chain-of-thought or private scratch reasoning
- Guaranteeing semantic truth from citation presence alone
- Rewriting user-authored quoted material
- Blocking ordinary social/conversational responses that require no external evidence
- Applying medical, legal, financial, or regulated-domain policy without a separately
  reviewed domain profile
- Letting finalization widen tools, permissions, cost, or effect authority

## Interfaces consumed

- Verified user request and current conversation turn.
- F4 task plan, explicit deliverables, expected evidence kinds, completion state,
  unresolved steps, tool budget, and stop reason.
- F5 selected context and protected `EvidenceReader` refs.
- Existing citation ledger, source ordinals, source-open resolver, and citation renderer.
- G1 Library, G2 web, G3 conversation, workspace, artifact, and connector evidence
  metadata when those sources are active.
- F9 verified subagent result envelopes.
- Typed tool/operation outcomes, approval state, artifact refs, and error categories.
- Model/provider capability and structured-output support.
- E1 retention, deletion, legal-hold, redaction, and audit decisions.
- F1 experiment registration, scorers, and release thresholds.

## Interfaces exposed

### Runtime ports

```text
AnswerRequirementCompiler.compile(request, task_plan) -> AnswerRequirementLedger
AnswerEnvelopeParser.parse(model_output, profile) -> AnswerEnvelope
AnswerEvidenceResolver.resolve(bindings, runtime_context) -> ResolvedEvidenceSet
AnswerVerifier.verify(envelope, requirements, evidence, run_state) -> VerificationReport
AnswerRepairController.decide(report, budget) -> RepairDecision
AnswerFinalizer.finalize(envelope, report) -> FinalResponsePayload
```

### Model contract

The model produces a typed envelope through provider-native structured output where it
is reliable, or through a strictly parsed tagged envelope otherwise:

```text
AnswerEnvelope
  answer_text
  requirement_results[]
  claims[]
  conflict_resolutions[]
  limitations[]
  unresolved_items[]
  presentation_hints
  envelope_revision
```

The final user-visible answer remains ordinary Markdown/text plus existing citation
rendering. Machine fields are not exposed as raw JSON unless the user requests a
structured artifact.

### Events

```text
answer.requirements.compiled.v1
answer.verification.started.v1
answer.verification.completed.v1
answer.repair.requested.v1
answer.repair.completed.v1
answer.finalized.v1
answer.degraded.v1
```

Events contain IDs, counts, profile revisions, safe reason codes, durations, and
digests. They exclude request text, answer text, claims, source excerpts, hidden model
reasoning, and raw repair prompts.

## Core contracts and state model

```text
AnswerRequirementLedger
  ledger_id
  run_id
  requirements[]
  source_request_digest
  task_plan_revision?
  profile_revision
  ledger_digest

AnswerRequirement
  requirement_id
  kind: deliverable | format | constraint | evidence | freshness |
        comparison | action_result | caveat
  description_ref
  required: bool
  expected_evidence_kind?
  completion_source: explicit_request | task_plan | operation_result

AnswerRequirementResult
  requirement_id
  status: satisfied | partial | unsatisfied | not_applicable
  answer_span_ids[]
  evidence_refs[]
  explanation_code?

AnswerClaim
  claim_id
  answer_span_id
  kind: observed | attributed | inference | estimate |
        recommendation | user_provided | unknown
  materiality: material | supporting | incidental
  evidence_bindings[]
  confidence_label: high | medium | low | not_applicable
  freshness_requirement?

AnswerEvidenceBinding
  evidence_ref
  source_digest
  source_span_or_locator
  relationship: supports | contradicts | contextualizes
  retrieved_or_observed_at
  valid_at?

EvidenceConflictResolution
  conflict_set_id
  evidence_refs[]
  resolution: prefer_newer | prefer_primary | scoped_difference |
              unresolved | not_material
  answer_span_ids[]
  explanation_code

AnswerVerificationReport
  report_id
  envelope_digest
  requirement_ledger_digest
  evidence_snapshot_digest
  profile_revision
  status: passed | repairable | degraded | blocked
  failures[]
  warnings[]
  verified_claim_count
  unsupported_claim_count
  citation_error_count
  freshness_error_count
  conflict_error_count
  checked_at

AnswerVerificationFailure
  failure_id
  code
  requirement_id?
  claim_id?
  answer_span_id?
  affected_evidence_refs[]
  repair_action: add | remove | caveat | cite | reconcile |
                 reformat | disclose_partial | none

AnswerRepairAttempt
  repair_attempt_id
  original_envelope_digest
  failure_ids[]
  model_route
  prompt_revision
  usage
  result_envelope_digest?
  status
```

Finalization state:

```text
generating → parsed → verifying
  → passed → finalized
  → repairable → repairing → verifying
  → degraded → finalized
  → blocked
```

`degraded` means the answer remains useful but must explicitly disclose limitations.
`blocked` is reserved for cases where showing the draft would violate policy, expose
unauthorized content, or materially misrepresent an effect outcome.

## Claim and evidence policy

### Material claims

A claim is material when a reasonable user could make a decision, take an action, or
judge task completion differently if it were false. Examples include dates, prices,
versions, legal/policy statements, quoted facts, test results, file modifications,
external action outcomes, and comparative conclusions.

Material externally verifiable claims require one of:

- a valid evidence binding;
- explicit attribution to the user;
- an `inference` or `estimate` label with supporting evidence and limitations; or
- an honest `unknown/source needed` statement.

Recommendations need evidence for their factual premises, not a citation pretending
the preference itself is a fact. Common conversational statements do not require
artificial citations.

### Evidence strength

Profiles declare which evidence kinds can support which claims. Examples:

- search snippets can support discovery, not detailed page claims;
- exact extracted page spans can support bounded web claims;
- tool success receipts support action completion;
- proposed/staged effects support only “prepared for review,” not “completed”;
- test output supports the exact snapshot/toolchain it ran against;
- recalled memory supports personalization but not current external facts;
- prior conversation evidence supports “previously said,” not present-day truth.

The verifier checks type compatibility mechanically. It does not infer authorization or
truth from the model's prose.

## Detailed design

### 1. Requirement compilation

The compiler extracts only externally inspectable requirements:

- explicit requested artifacts, sections, files, formats, and counts;
- user constraints such as “do not browse,” “use these sources,” or “do not change X”;
- F4 expected outputs/evidence and terminal state;
- required disclosure for failed, partial, staged, or approval-held work.

Compilation uses deterministic patterns and typed F4 fields first. It may use the
primary model's structured output to map answer spans to requirements, but it does not
invoke a separate planning model. Ambiguous preferences remain a single broad
requirement rather than invented subrequirements.

The ledger excludes private reasoning steps. Users may inspect a human-readable
completion summary, not hidden scratch work.

### 2. Synthesis profile selection

Task profiles:

- `conversational`: ordinary streaming; no claim manifest unless citations are used;
- `grounded_fast`: one typed answer envelope, buffered until deterministic checks pass;
- `grounded_streaming`: stream answer text while validating citations incrementally;
  only additive caveat/citation correction is allowed after display;
- `effect_summary`: derive action status from operation/stage/receipt records;
- `high_assurance`: bounded buffered answer with stricter evidence and freshness rules.

Profile selection is deterministic from task family, source/effect use, user policy,
and model capability. The model cannot select a weaker profile.

Buffered profiles may emit progress/activity events immediately but do not publish
unverified text as the authoritative final response. The first release enables targeted
repair only for buffered profiles, avoiding replacement of text already shown to the
user.

### 3. Single-call answer envelope

The primary synthesis call receives:

- the bounded requirement ledger;
- F4 completion/unresolved state;
- F5-selected evidence and citation handles;
- typed operation/artifact/approval outcomes;
- known conflict/freshness metadata; and
- the closed answer-envelope schema.

It returns answer text and mappings in one call. Claim mappings reference answer span
IDs and opaque evidence refs; they do not copy full source bodies. Unsupported
structured output fails parsing and follows a typed fallback rather than silently
discarding verification.

### 4. Evidence reauthorization and locator validation

Before verification, source resolvers:

1. derive current profile/user/project scope from runtime context;
2. reauthorize every evidence ref;
3. check source existence, deletion/tombstone, expiry, version, and digest;
4. validate the cited span/locator against the retained source;
5. return source class, observed/retrieved time, and freshness metadata; and
6. record contradicting refs already known to the retrieval result.

F5/G-source services remain canonical. The answer layer retains refs and a verification
snapshot digest, not source bodies or a new index.

### 5. Deterministic verification

The verifier performs:

- envelope schema and size validation;
- required requirement-result presence;
- answer-span existence and non-overlap rules;
- required deliverable/format checks;
- claim type/materiality/evidence rule checks;
- evidence-ref authorization, digest, locator, and source-kind checks;
- citation marker-to-binding resolution;
- effect-status consistency against stages/receipts;
- freshness deadline and “as of” disclosure checks;
- known conflict-set coverage;
- limitation/unresolved-state disclosure;
- no raw internal refs, secrets, or hidden error content in answer text; and
- output renderer safety and maximum-size checks.

These checks are deterministic. They can prove structural support and source integrity,
not general semantic truth. F1 offline scorers remain responsible for measuring deeper
entailment and helpfulness before promotion. A separately reviewed semantic verifier
may contribute offline or shadow telemetry, but it cannot trigger the repair loop
defined by this PRD or override a deterministic result.

### 6. Conflict and freshness reconciliation

Retrieval sources may supply conflict groups or version relationships. The answer must
either:

- choose a source through a declared deterministic rule such as newer authoritative
  revision;
- explain that sources apply to different scopes;
- present both values and mark the conflict unresolved; or
- omit the disputed claim.

“Newest” is based on source validity/effective dates, not search rank. A newer
unofficial page does not automatically outrank an older primary source.

Freshness profiles define:

- maximum source age by claim type;
- whether `retrieved_at` must be displayed;
- whether current verification is mandatory; and
- acceptable fallback wording when current evidence is unavailable.

The model cannot relabel stale evidence as current.

### 7. Targeted repair

A repair call is admitted only when the deterministic report is `repairable`. The
repair prompt contains:

- original envelope;
- failed requirement/claim/span IDs;
- stable failure codes;
- allowed repair actions;
- authorized evidence refs and only necessary bounded source spans;
- unchanged requirements and output schema; and
- remaining token/cost/deadline budget.

It does not receive unrelated conversation history or an invitation to rethink the
whole task. The model may add/remove/caveat/reconcile/reformat affected sections. It
cannot invoke effects, widen evidence scope, change operation outcomes, or invent refs.

Default maximum is one repair. F4 may permit one bounded evidence read before repair
only when a required source is missing, the user allowed research, and tool/cost/time
budgets remain. That read is an ordinary attributable operation, not an answer-layer
bypass.

The repaired envelope is fully reverified. A second failure becomes `degraded` or
`blocked`; it does not loop.

### 8. Degraded and partial answers

When repair is unavailable or unsuccessful, the finalizer preserves useful verified
content and adds a concise limitation:

- which requested item is incomplete;
- which source is unavailable/stale/conflicting;
- whether an action is staged, pending approval, indeterminate, failed, or completed;
- what was not verified; and
- the safest next step when one exists.

It removes or qualifies unsupported material claims. It never reports an empty
successful answer merely because parsing failed.

### 9. Citations and presentation

Citation rendering continues through the existing citation projection. The finalizer
provides:

- stable source ordinals;
- answer-span-to-citation mapping;
- source type/title/date where authorized;
- conflict/freshness badges when useful; and
- a safe verification summary for eligible product surfaces.

Deleted or authorization-lost sources render an honest unavailable tombstone. The
answer layer cannot expose signed URLs or protected refs directly.

### 10. Streaming behavior

Streaming must remain truthful:

- `conversational` output behaves as today.
- `grounded_fast` buffers final text but emits activity/progress events.
- `grounded_streaming` validates each citation when its marker closes and may stop the
  stream on a hard authorization/digest failure.
- A streamed answer cannot be silently replaced. Corrections are explicit follow-up
  finalization events and are excluded from the first repair rollout.

F1 measures time to first activity, first answer text, authoritative final response,
and repair completion separately.

## Ownership and service boundaries

| Responsibility                                                           | Owner                                          |
| ------------------------------------------------------------------------ | ---------------------------------------------- |
| Requirement ledger, answer schema, deterministic verifier, repair policy | AI backend                                     |
| Evidence selection/offload/hydration                                     | F5 and source owners                           |
| Library/web/history evidence and source lifecycle                        | G1/G2/G3 owning services                       |
| Citation ledger and source ordinal projection                            | Existing AI-backend citation path              |
| Offline/shadow evaluation and promotion                                  | F1                                             |
| Final-answer/citation presentation                                       | Shared chat surface through existing contracts |
| Retention, deletion, audit policy                                        | E1 and owning persistence domains              |

The answer verifier calls source-owner ports or authenticated service APIs. It does not
copy source indexes across services. Apps call the facade and cannot submit a
“verified” flag.

## Persistence, retention, and deletion

- Persist requirement-ledger digest, envelope digest, verification report, repair
  metadata, model/prompt/profile revisions, usage, and safe failure codes with the run.
- Final answer text remains in existing message/run persistence.
- Claims and requirement descriptions containing user content are protected
  payload/artifact refs, not ordinary events or analytics.
- Evidence bodies remain in their source stores; the verifier retains opaque refs,
  digests, and source-lifecycle snapshots only.
- Repair prompts/responses follow model-input retention policy and are not stored in
  audit rows.
- Conversation/run deletion cascades through ledgers, reports, repair records, cached
  resolutions, and protected payloads.
- Source deletion invalidates future resolution and updates existing UI through
  tombstone semantics; it does not rewrite historical final text silently.
- An explicitly enabled hosted retention lock may preserve required records but does not restore source authorization or
  permit new model processing when consent/policy is revoked.

## Authentication, authorization, security, and audit

- Runtime identity, not envelope fields, determines profile/user/project scope.
- Every evidence binding is reauthorized before finalization.
- Model-supplied `verified`, `fresh`, `supports`, confidence, or source-type labels are
  untrusted until resolved.
- Retrieved text, prior answers, tool output, and citations remain untrusted content and
  cannot alter system policy or schema.
- No source content, answer text, user request, or repair prompt in ordinary logs,
  metrics, or audit.
- Repair uses the same provider, BYOK, region, training, budget, redaction, and usage
  policies as other model calls.
- Audit profile change, verification outcome, repair admission/outcome, degraded/block
  decision, and finalization digest.
- High-stakes profiles fail closed when their mandatory verifier or evidence class is
  unavailable; generic profiles degrade transparently.

## Performance and complexity budgets

Let:

- `R` be answer requirements;
- `C` be declared claims;
- `E` be evidence bindings;
- `K` be known conflict edges;
- `A` be answer bytes.

Budgets:

- Local verification is `O(R + C + E + K + A)`.
- Evidence resolution batches by source service and ref type; network round trips are
  `O(S)`, where `S` is the small number of source services, not `O(E)`.
- Requirement compilation p95 below 10 ms excluding model generation.
- Local verification p95 below 75 ms for 50 requirements, 100 claims, 200 bindings, and
  a 64 KiB answer.
- Batched evidence reauthorization p95 below 200 ms when source services are healthy.
- Fast-path finalization overhead p95 below 250 ms.
- Default caps: 50 requirements, 100 material claims, 200 bindings, 20 conflict sets,
  64 KiB answer text, one repair call, and no more than 25% of the original synthesis
  token budget for repair.
- Repair admission target below 10% after prompt/profile tuning.
- Repair total latency p95 is reported separately and cannot be hidden in overall
  averages.

The program reports first activity, first text, authoritative final response, and total
task latency. Big-O does not substitute for provider/network tail measurements.

## Failure, idempotency, and recovery

- Requirement compilation is deterministic by request digest, F4 plan revision, and
  profile revision.
- Verification is idempotent by envelope, ledger, evidence snapshot, and profile
  digests.
- Repeated finalization returns the same final payload and does not emit duplicate
  authoritative messages.
- Source timeout yields typed `source_unavailable`, not unsupported success.
- An authorization/digest failure is hard and cannot be repaired by changing the ref.
- Parser failure may use one bounded normalization attempt only when it does not require
  another model call; otherwise emit a transparent degraded answer.
- Worker crash after synthesis resumes verification from protected envelope state.
- Worker crash after `final_response` reconciles against the terminal event sequence and
  does not publish again.
- Cancellation before finalization stops repair and records no authoritative answer
  unless existing run policy emits a cancellation summary.
- Provider timeout during repair does not erase the original verified portions.
- Lost repair response is reconciled through the canonical model invocation record
  before spending again.

## Observability and quality gates

Metrics:

- requirements by kind and satisfied/partial/unsatisfied;
- claims by kind/materiality and evidence bindings per claim;
- citation resolution, source deletion, digest mismatch, and wrong-source-class rate;
- stale-source and unresolved-conflict rate;
- effect-status mismatch and undisclosed partial outcome rate;
- verification passed/repairable/degraded/blocked;
- repair admission, success, token/cost, latency, and repeated-failure code;
- answer helpfulness, factual correctness, completeness, calibration, citation
  correctness, user correction, retry/regenerate, and abandonment;
- first activity/text/final latency and fast-path overhead.

Trace lineage:

```text
request → F4 task plan/requirements → selected evidence/operation outcomes
        → answer envelope → evidence resolution → verification report
        → optional repair → authoritative final response/citations
```

F1 suites include:

- one request containing several differently formatted deliverables;
- supported and unsupported factual claims;
- search snippet versus extracted-source evidence;
- stale and current versions of one source;
- contradictory primary sources;
- prior-conversation evidence mistaken for current truth;
- memory personalization conflicting with the current user request;
- effect staged/pending/indeterminate/completed distinctions;
- deleted/revoked evidence after synthesis;
- malformed citation locators and forged refs;
- partial tool failure with useful remaining results;
- no-evidence conversational answers; and
- adversarial source text instructing the verifier to skip checks.

Deterministic hard failures cannot be overridden by an offline model grader.

## Rollout and backout

1. Build requirement/envelope/report contracts and offline F1 fixtures.
2. Parse and verify in shadow while existing final responses remain authoritative.
3. Enable citation/source-integrity checks for buffered internal research answers.
4. Enable degraded finalization without repair.
5. Enable one targeted repair for selected grounded task families.
6. Add effect-summary and conflict/freshness profiles.
7. Expand consumer cohorts only after quality and latency gates.

Backout disables envelope enforcement and repair while retaining reports for evaluation.
Existing final responses, citation records, evidence refs, and F1 experiments remain
readable. No source store or task execution path changes during backout.

## Implementation slices

1. Requirement, claim, evidence-binding, conflict, and report contracts
2. F4 requirement compiler and profile selector
3. Provider envelope parser and bounded fallback
4. Batched source reauthorization and citation-locator adapter
5. Deterministic requirement/claim/effect/freshness/conflict verifier
6. Finalizer and verified/degraded event contracts
7. Targeted repair controller and usage accounting
8. Citation/shared-surface projection and accessibility states
9. F1 suites, dashboards, rollout flags, alerts, and runbook

## Test plan

### Unit

- Requirement compilation from explicit constraints and F4 fields
- Envelope/schema/span/size validation
- Claim/source-kind rules and citation-marker resolution
- Freshness deadlines, conflict decisions, and effect-state consistency
- Repair admission, field scoping, caps, and degraded fallback
- Secret/internal-ref output rejection

### Contract and integration

- Source resolver batches and reauthorizes Library/web/history/artifact/tool refs
- Deleted, stale, digest-mismatched, expired, and cross-profile refs fail
- Existing citation ordinals remain stable
- One final authoritative response after crash/replay
- Shared web/desktop surfaces render verified, degraded, unavailable-source, and
  unresolved-conflict states

### Quality

- Human-labeled requirement coverage and claim/evidence support
- Conflict/freshness and uncertainty calibration
- Unsupported-claim, miscitation, omission, and over-caveating rates
- Repair improves failed dimensions without degrading correct sections
- Conversational answers remain natural and do not gain gratuitous citations

### Performance and fault injection

- 1/10/100 claims and 1/3/10 source services
- Source timeout, partial batch failure, worker crash, provider timeout, cancellation
- Fast path makes one model call; repair path makes at most one additional call
- p50/p95 first activity, first text, finalization, and repair latency

## Definition of done

- Final grounded/effect answers use a versioned `AnswerEnvelope`.
- Explicit requested deliverables have inspectable satisfied/partial/unsatisfied state.
- Material claims have valid evidence, attribution, inference/estimate labeling, or an
  honest unknown.
- Evidence authorization, digest, locator, source class, freshness, and known conflicts
  are checked before authoritative finalization.
- Valid answers incur no second model call.
- Deterministic failures receive at most one bounded targeted repair by default.
- Unrepairable answers preserve useful verified content and disclose limitations.
- No evidence store, citation ledger, or F1 measurement system is duplicated.
- Security, deletion, replay, latency, and F1 quality gates pass.

## Guardrails

- Citation presence is not proof of truth; evaluate support separately.
- Do not call a generic model judge on every answer.
- Do not repair a valid answer merely to change style.
- Do not fetch new evidence after stop unless F4 explicitly admits one bounded read.
- Do not let a repair alter operation outcomes, user requirements, or authority.
- Do not hide conflict, staleness, partial completion, or indeterminate effects.
- Do not expose private reasoning, raw refs, source bodies, secrets, or repair prompts.
- Do not silently replace already streamed user-visible text.
- Do not mark a response verified when a mandatory source service failed.

## Open decisions

1. Which task families use buffered `grounded_fast` versus incrementally verified
   streaming in the first release?
2. Which deterministic claim-to-source compatibility matrix ships initially?
3. Which freshness defaults belong to generic profiles versus domain-specific policy?
4. Should users see a verification badge, or only citations and limitations?
5. When may a single evidence-read step precede repair?
6. Which high-assurance domains warrant a separately reviewed semantic verifier?
