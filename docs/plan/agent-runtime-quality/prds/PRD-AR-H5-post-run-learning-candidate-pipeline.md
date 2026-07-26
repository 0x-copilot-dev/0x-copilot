# PRD-AR-H5 — Post-run learning candidate pipeline

**Goal.** Turn eligible completed runs into bounded, evidence-backed proposals
for memories, skills, routines, and scheduled work through a durable asynchronous
pipeline. Extraction creates candidates only; it never changes future agent
behavior or activates automation.

## Metadata

| Field        | Value                                                                                                                          |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| Status       | Proposed; completion of existing dark infrastructure                                                                           |
| Priority     | P0                                                                                                                             |
| Owners       | `services/ai-backend` (eligibility, extraction, usage), `services/backend` (proposal persistence), facade/UI (review delivery) |
| Depends on   | AR-F1, AR-G3, Generative Surfaces A2 and E1                                                                                    |
| Rollout flag | `POST_RUN_LEARNING_CANDIDATES_ENABLED`, explicit desktop-user opt-in                                                           |
| UI impact    | Proposal inbox/toasts; no automatic mutation                                                                                   |

## Implementer brief

Read:

1. `services/ai-backend/src/runtime_worker/jobs/proposal_extractor.py`.
2. `services/ai-backend/tests/unit/runtime_worker/test_proposal_extractor.py`.
3. Run-completion handling in `services/ai-backend/src/runtime_worker/`.
4. Outbox/jobs and usage attribution in `services/ai-backend/src/agent_runtime/`.
5. `services/backend/src/backend_app/memory/`.
6. `services/backend/src/backend_app/routines/`.
7. `packages/api-types/src/memory.ts`.
8. AR-G3, AR-H2, AR-H6, AR-I1, and
   `../../prds/PRD-E1-accountability-lifecycle.md`.

## Problem statement

The repository contains a typed, cost-capped proposal extractor, but production
run completion does not enqueue or invoke it, backend has no internal ingestion
contract for generated proposals, and output cannot reach a live review flow.
The current extractor also supplies no exact supporting message/tool spans.
Treating the module's existence as shipped learning would be incorrect.

## Current implementation and predecessor contracts

- **[shipped]** `ProposalExtractor` bounds transcript characters/messages, proposal counts,
  body length, and per-run projected cost.
- **[shipped]** It uses the canonical model construction path and
  `Purpose.MEMORY_EXTRACTION`.
- **[shipped]** It returns typed memory/routine/cron candidates and treats malformed model
  output as no candidates.
- **[shipped]** Backend already has owner-only memory proposal decisions and audit semantics.
- **[shipped]** Durable run messages/events and citation ordinals provide the source
  records from which the new exact evidence refs will be built.

## Objectives and outcomes

1. Reliably enqueue one extraction job for every eligible completed run.
2. Attach exact, reauthorizable evidence spans to every candidate.
3. Persist candidates through an authenticated internal backend API without
   cross-service imports.
4. Enforce consent, sensitivity, retention, and cost before model invocation.
5. Deduplicate candidates while retaining contradictory evidence for review.
6. Route candidate types to their owning review workflows.

Launch gates:

- no candidate is accepted/published/activated automatically;
- duplicate run-completion delivery causes at most one logical extraction job and one
  candidate set; any provider retry is a distinct exactly-accounted model attempt;
- 100% of candidates have at least one valid source span;
- content never appears in info logs or usage/audit metadata;
- extractor failure cannot fail or delay the user's completed run.

## Non-goals

- Building a final `SKILL.md` (AR-H8).
- Accepting memories (AR-H6) or recalling them (AR-H7).
- Publishing skills (AR-H2).
- Activating routines (AR-I1).
- Historical backfill. AR-H9 owns consented memory/routine backfill; AR-H8 owns
  skill/procedure backfill. H5 processes current completed runs only.

## Interfaces consumed

- Completed run/message/event snapshots and conversation retention policy.
- E1 usage meter, local audit, redaction, deletion, and outbox patterns.
- AR-G3 stable conversation-message evidence references.
- Exact operation, tool-result, citation, artifact, and approval record resolvers owned
  by their respective runtime domains.
- User learning/privacy preference from the local backend.

## Interfaces exposed

```text
LearningExtractionJob
  job_id
  local_account_id
  conversation_id
  run_id
  run_terminal_sequence
  transcript_revision
  policy_revision
  state: queued | claimed | completed | skipped | failed
  claim_attempt
  lease_until?
  created_at

LearningModelAttempt
  model_attempt_id
  job_id
  provider_attempt_no
  request_digest
  model_id
  state: started | succeeded | failed | ambiguous
  usage_ledger_id
  input_tokens, output_tokens, billed_units, estimated_cost
  started_at, completed_at?

LearningCandidateBatch
  batch_id
  job_id
  extractor_model
  prompt_revision
  policy_revision
  source_digest
  model_attempt_ids[]
  candidates[]
  safe_usage

LearningCandidate
  candidate_id
  kind: fact | preference | project_convention | user_capability | procedure |
        routine
  title
  proposed_body
  confidence
  trigger_hint?: manual | schedule | event
  trigger_spec?
    schedule_expression?
    time_zone?
    event_type?
    event_filter_ref?
  scope_hint
  scope_ceiling
  project_id_ceiling?
  sensitivity_ceiling[]
  message_evidence_refs[]
  trajectory_evidence_refs[]
  sensitivity_labels[]
  dedupe_key
  status: pending | routed | rejected | expired
```

`message_evidence_refs` are G3 message/span refs only.
`trajectory_evidence_refs` are typed opaque refs to exact operation, tool-result,
citation, artifact, and approval records and are hydrated only through those owning
record resolvers. Neither form duplicates raw transcript/tool content in audit/event
rows, and no tool or operation record is disguised as a conversation-message ref.

Kinds are semantically closed:

- `user_capability` means a fact about what the user can do, such as language or
  technical proficiency; it routes to H6's existing memory `kind=skill`;
- `procedure` means a reusable multi-step method; it never enters memory `kind=skill`
  and routes only to H8;
- `routine` routes to I1 and is not a memory item. Scheduled/event-driven work remains
  `kind=routine`; `trigger_hint` and the bounded typed `trigger_spec` describe the
  proposed manual/schedule/event trigger without creating a second candidate kind.

## Detailed design

### 1. Eligibility and consent gate

Before enqueue and again before model invocation:

- user learning feature is enabled;
- run source/type is eligible;
- conversation is not ephemeral, private-excluded, deleted, or beyond
  retention;
- connector/tool classes are permitted for learning;
- no pending local-data deletion or consent revocation;
- cost and rate quotas permit extraction.

Policy is backend-owned and fetched over authenticated internal HTTP. A stale
allow decision cannot override a current deny.

### 2. Durable dispatch

Run completion appends `LearningExtractionJob` in the same durable transaction
or outbox boundary as the terminal run event. Worker consumers claim with a
lease. Idempotency key:

```text
(local_account_id, run_id, run_terminal_sequence, extractor_policy_revision)
```

Re-extraction under a newer policy or prompt is an explicit replay with a new
job lineage, not an overwrite.

### 3. Evidence-preserving transcript

The builder chooses a bounded set of user messages, assistant final output, and
high-value tool/citation records. Each excerpt carries an opaque source ref and
ordinal. Content is redacted/classified before inference. Trimming is
source-aware: recent messages alone are not sufficient when the successful
procedure relies on earlier constraints.

Message excerpts are obtained through G3. Tool/operation trajectories are independently
selected from exact typed runtime records after current authorization, retention, and
digest checks. The builder records record kind, immutable revision/digest, and ordinal;
it never asks G3 to return tool results that G3 intentionally excludes.

### 4. Structured extraction

Use provider-native structured output where available, with strict local
validation. The model must quote source-ref IDs, not raw URLs/paths, for every
candidate. Reject:

- no-evidence candidates;
- unsupported source IDs;
- secrets/credentials;
- instructions masquerading as policy;
- speculative identity/sensitive traits;
- candidates outside allowed kinds/size.
- a `procedure` mislabeled as `user_capability` or vice versa;
- trigger fields on non-routine candidates, unsupported trigger kinds, invalid
  schedule/time-zone syntax, inline event payloads, or unbounded event filters;
- a proposed scope, project, or sensitivity posture broader than the evidence-derived
  ceiling.

Confidence is a model signal only and is not treated as calibrated probability.

### 5. Deduplication and contradiction

Calculate a deterministic dedupe key from candidate kind, normalized subject,
scope ceiling, and bounded content fingerprint. Search pending/accepted relevant
records in the candidate's authorized scope. Similar candidates are linked;
they are not silently dropped when evidence conflicts. The review workflow
shows “supports,” “updates,” or “contradicts” an existing record.

### 6. Backend ingestion and routing

Add authenticated internal batch ingestion to backend. It validates the per-install
service identity and local-account header, revalidates size/kind/evidence envelope, and
atomically stores the batch plus proposal rows.

Routing:

- facts/preferences/project conventions/user capabilities → AR-H6 proposal inbox;
- procedures → AR-H8 skill-distillation queue and never H6;
- routines, including schedule/event trigger hints → AR-I1 proposal intake.

Backend persists an immutable `EvidenceScopeCeiling` with every proposal. It is derived
from the intersection of all source visibility, project membership, retention,
sensitivity, and caller authority:

```text
EvidenceScopeCeiling
  maximum_scope: personal | project
  allowed_project_id?
  sensitivity_ceiling[]
  source_acl_digest
  derived_at
```

The user may narrow this ceiling. Widening requires an explicit republishing action
with suitable non-private project evidence; editing a proposal cannot silently turn
personal evidence into a project-visible memory or procedure.

Backend returns durable proposal IDs; ai-backend records only IDs/counts in the
job result.

### 7. Notifications

After persistence, backend emits proposal-available SSE/activity events.
Notification payloads contain a safe gist and proposal ID; full evidence/body
requires an authorized fetch. Notification failure does not repeat extraction.

## Persistence, retention, deletion, and future sync

ai-backend owns job state, source digest, and model usage in its shipped desktop-default
file-native store below `<userData>/agent-data/v1`; no embedded PostgreSQL round trip is
required for run completion or job claiming. Backend owns candidates/proposals and
decisions through H6's adapter in the already bundled local backend database. Raw extracted text
exists only in protected proposal bodies/evidence sources. Conversation or exact
trajectory-source deletion invalidates evidence and deletes or withdraws pending
candidates; accepted downstream records follow H6/H8's normative source-deletion
matrix.

Large evidence payloads use content-addressed filesystem refs. Stable IDs, revisions,
and an optional local outbox form the future consumer-sync seam, but extraction,
review, and deletion work fully offline and do not require a cloud account.

## Security, privacy, and audit

- Derive identity from verified run state, never job payload fields alone.
- Classify/redact before external model calls.
- Local-account scope is carried through the loopback service-auth channel and
  rechecked.
- Never learn credentials, authentication tokens, private keys, biometric,
  health, political, or other highly sensitive traits.
- Audit eligibility result, model/prompt/policy revision, candidate IDs, route,
  and reviewer decisions; do not audit proposal body.
- BYOK/local-model selection, training opt-out, and model eligibility apply to the
  auxiliary call. Offline mode skips cloud extraction or uses an enabled local model.

## Performance and cost

- User run completion path: outbox append only, p95 under 20 ms incremental.
- Default job input remains bounded by policy; record actual estimated and provider
  tokens for every model attempt.
- Per-run and per-device daily dollar caps are hard pre-call gates.
- Jobs have laptop-aware bounded concurrency by provider, with battery/thermal
  backpressure.
- Dedup uses indexed candidates/memories; no full-history O(N) scan.
- Candidate delivery target: p95 within 30 seconds of run completion, not a
  synchronous guarantee.

## Failure, retry, and recovery

- Empty/ineligible/cost-blocked runs finish as explicit `skipped` reasons.
- Model/network failures retry only within bounded policy. A stable logical `job_id`
  deduplicates candidate persistence, while each provider invocation receives a unique
  `model_attempt_id` and writes exactly one usage-ledger row, including ambiguous or
  failed billable attempts. Retrying never overwrites or suppresses spend.
- Backend ingestion uses batch idempotency; ambiguous responses are reconciled
  by batch ID before retry.
- Poison jobs enter a DLQ with content-free diagnostics.
- A deleted or revoked source between enqueue and invocation causes skip.
- Process crash after inference but before persistence can rerun deterministically and
  dedupe candidate persistence by source/prompt/policy keys; any second provider call
  is a second accounted model attempt.

## Observability and evaluation

Metrics: eligible/enqueued/claimed/completed/skipped/failed, queue age, projected
and actual cost, candidates/run, no-evidence rejection, sensitive-content
rejection, dedupe/contradiction rates, reviewer accept/edit/reject/expiry,
source-deleted candidates, and end-to-end delivery latency. AR-F1 tracks
precision by kind against a reviewed corpus.

## Rollout and backout

1. Wire durable jobs with extractor disabled; verify one job per completion.
2. Shadow extraction with no persistence and sampled secure review.
3. Persist internal-only candidates.
4. Enable opt-in user proposal inbox.
5. Route procedures/routines after AR-H8/AR-I1 are ready.

Backout stops new enqueue/claims. Pending proposals remain reviewable or may be
expired by policy. Never auto-delete accepted downstream state.

## Implementation slices

1. Job/outbox schema, port, adapters, lease worker.
2. Eligibility policy internal API and caching.
3. Evidence-bounded transcript builder and redaction.
4. Structured extractor revision with source refs.
5. Backend internal batch ingestion/idempotency.
6. Proposal routing and notification projection.
7. Local retention/export/deletion integration.
8. Evaluation corpus, dashboards, and rollout tooling.

## Test plan

- Exactly one job for duplicate terminal events.
- Current-policy deny overrides enqueue-time allow.
- Renderer identity and loopback service-token spoofing denied; second-account records
  are not visible.
- Source-ref validation and source deletion races.
- Secret/PII/prompt-injection fixtures.
- Malformed/oversize/unsupported structured output.
- Routine routing for manual/schedule/event hints; invalid cron/time-zone/event specs
  and trigger fields on non-routine kinds are rejected.
- Cost cap prevents model invocation and usage row.
- Crash/retry around model call and backend ingestion.
- Crash before/after provider response accounts each attempted invocation once while
  persisting one logical candidate batch.
- Duplicate/contradictory candidate behavior.
- Deletion cascade, local export/restore, consent withdrawal, offline mode, and DLQ
  recovery.

## Definition of done

- [ ] Packaged desktop worker lifecycle constructs and consumes the extractor job.
- [ ] Backend persists idempotent evidence-backed proposals.
- [ ] Every accepted candidate path requires a separate owning decision.
- [ ] Consent, redaction, cost, and retention gates are enforced and tested.
- [ ] Failures do not affect completed runs.
- [ ] AR-F1 precision and safety launch gates pass.
- [ ] Shared program DoD passes.

## Guardrails

- Candidate generation is not memory, skill publication, or routine activation.
- Never infer a durable fact without cited source evidence.
- Never hide contradictory evidence through deduplication.
- Never run extraction synchronously in the user response path.
- Never log transcript or proposal content.

## Open decisions

1. Default eligible conversation classes for desktop users.
2. Which restricted data categories are always blocked versus user configurable.
