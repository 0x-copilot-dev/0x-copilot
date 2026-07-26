# PRD-AR-H9 — Governed historical learning backfill

**Goal:** Let a user run a bounded, consented, resumable local learning pass over
selected retained conversations so old chats can propose memory
facts, preferences, project conventions, and routines without silently changing
memory, publishing skills, or activating automation.

## Metadata

| Field                   | Value                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Status                  | Proposed                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Priority                | P2                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Wave                    | H — skills, memory, and learning                                                                                                                                                                                                                                                                                                                                                                                                        |
| Primary owners          | Backend learning-job/proposal control plane; AI-backend historical evidence and extraction worker                                                                                                                                                                                                                                                                                                                                       |
| Supporting owners       | Facade/shared review UI, privacy controls, F1 evaluation owners                                                                                                                                                                                                                                                                                                                                                                         |
| Depends on              | [G3 conversation evidence](./PRD-AR-G3-conversation-history-search-evidence-recall.md), [H5 post-run learning candidates](./PRD-AR-H5-post-run-learning-candidate-pipeline.md), [H6 durable memory](./PRD-AR-H6-durable-memory-store-review-index.md), [I1 governed routines](./PRD-AR-I1-agent-proposed-routines-automation.md), [E1 accountability lifecycle](../../generative-surfaces-v2-1/prds/PRD-E1-accountability-lifecycle.md) |
| Related, not owned      | [H8 skill distillation/backfill](./PRD-AR-H8-evidence-backed-skill-distillation.md)                                                                                                                                                                                                                                                                                                                                                     |
| Rollout flag            | `HISTORICAL_LEARNING_BACKFILL_ENABLED`, explicit desktop-user opt-in                                                                                                                                                                                                                                                                                                                                                                    |
| Primary success measure | Selected historical conversations yield reviewable, evidence-linked proposals within the approved cost/scope, with zero automatic acceptance or activation                                                                                                                                                                                                                                                                              |

## Implementer brief

Read before implementation:

1. `services/ai-backend/src/runtime_worker/jobs/proposal_extractor.py`.
2. `services/ai-backend/src/runtime_worker/`.
3. `services/ai-backend/src/agent_runtime/persistence/`.
4. `services/ai-backend/src/runtime_adapters/`.
5. `services/ai-backend/src/agent_runtime/capabilities/citations.py`.
6. `services/ai-backend/src/agent_runtime/persistence/records/citations.py`.
7. `services/backend/src/backend_app/memory/`.
8. `services/backend/src/backend_app/routines/`.
9. `services/backend/src/backend_app/jobs/`.
10. `services/backend-facade/src/backend_facade/`.
11. `packages/api-types/src/memory.ts`.
12. G3, H5, H6, H8, I1, and E1.

H5 owns the live post-run extraction schema, evidence requirements, model construction,
and candidate routing. H6 owns memory proposal decisions and accepted memory. I1 owns
routine proposal review and activation. H8 owns historical skill distillation. This
PRD adds the historical selection/estimate/consent/job/checkpoint control plane and a
run-trajectory evidence type needed to learn from verified tool outcomes.

## Problem statement

Live post-run learning starts only after H5 is enabled. Users may already have months
or years of retained conversations containing:

- stable preferences they explicitly repeated;
- project conventions confirmed across multiple tasks;
- facts the user asked the product to remember;
- recurring research/reporting workflows; and
- schedules or repeatable workflows that may be useful as routine proposals.

G3 can search exact prior messages on demand, but search is not a learning job. H8 can
backfill reusable skills, but it intentionally excludes memory and routines. Reusing
H5 by simply looping over every historical conversation would be unsafe and expensive:

- users may not have consented to learning when the chat was created;
- private, ephemeral, deleted, expired, or user-excluded chats may be ineligible;
- message text alone cannot prove that a procedure or external action succeeded;
- old connectors, identities, policy, or project scopes may no longer be valid;
- repeated extraction can create duplicate or contradictory proposals;
- a long job must survive crashes, pause promptly, and honor deletion;
- estimated model cost must be visible before work starts; and
- another signed-in account on the device must not gain access to private history.

The product needs an explicit backfill lifecycle: estimate first, confirm exact scope
and budget, enumerate only authorized retained sources, extract with H5, checkpoint,
deduplicate against live learning, and route proposals to existing review queues.

## Current state and strengths to preserve

- Conversation, message, run, event, tool, citation, artifact, approval, and usage
  records provide richer evidence than transcript prose alone.
- G3 defines exact scoped message evidence refs and deletion-aware opening.
- H5 defines typed candidates, evidence-preserving extraction, cost accounting, and
  propose-only routing for completed runs.
- H6 has owner-visible memory proposal decisions and correction/deletion controls.
- I1 defines explicit review before a routine can become active.
- H8 defines a separate, consented historical skill-distillation path.
- Queued workers, durable events, monotonic run sequences, ai-backend's default
  file-native desktop store, and the backend's embedded local PostgreSQL database
  provide reusable recovery patterns without a cloud dependency.

There is no historical-learning job aggregate, metadata-only estimate, source cursor,
cross-live/backfill dedupe boundary, or authorized run-trajectory evidence resolver
composed in production.

## Objectives and outcomes

1. Show a dry-run estimate of eligible conversations, approximate tokens, model calls,
   cost range, duration, and expected proposal classes before processing content.
2. Bind execution to an exact user-confirmed scope, time range, candidate kinds, cost
   cap, and consent/policy revision.
3. Enumerate eligible retained history with stable resumable cursors and no request-path
   full-history scan.
4. Use exact G3 message refs for statements and a separate exact run-trajectory ref for
   operations, results, citations, approvals, and outcomes.
5. Reuse H5 extraction, validation, candidate kinds, and routing rather than building a
   second learning model path.
6. Deduplicate against both live H5 candidates and earlier backfill jobs while
   preserving contradictions for review.
7. Route memory proposals to H6 and typed routine proposals to I1; never accept,
   publish, enable, or activate them.
8. Pause, cancel, resume, recover, and delete jobs without losing accounting or
   reprocessing completed source revisions.
9. Honor source deletion, consent withdrawal, connector revocation, and retention
   changes before each model call and proposal write.

### Launch gates

- Zero source processed outside the confirmed local-account/project/conversation/time
  scope.
- Zero private/ephemeral/deleted/expired source processed.
- Zero proposal accepted as memory or activated as a routine automatically.
- Actual billable cost cannot exceed the confirmed cap.
- At least 99.9% of completed source units process once per extractor/source revision.
- Every proposal resolves to at least one exact retained G3 message ref; routine
  proposals additionally require outcome-bearing trajectory evidence unless the source
  is an explicit user-authored routine request.
- Cancel/pause/consent withdrawal prevents admission of a new model call within 10
  seconds under normal worker health.
- Cross-account and unauthorized-private-history tests have zero leakage.

## Scope

- Metadata-only historical-learning estimate
- User-confirmed backfill jobs and immutable job revisions
- Local-account/project/conversation/time filters
- Candidate-kind filters for fact, preference, project convention, and routine;
  routines carry H5's typed manual/schedule/event trigger hint/spec
- Indexed eligible-source enumeration and stable cursors
- Exact G3 message evidence refs
- Exact authorized run-trajectory evidence refs
- H5 extraction reuse, batching, quotas, dedupe, and proposal routing
- Durable claim, heartbeat, checkpoint, pause, resume, cancel, and completion
- Progress, cost, skipped-source, and proposal-review UI
- Deletion, consent withdrawal, local export, audit, rollout, and backout

## Non-goals

- Skill distillation or historical skill backfill; H8 owns both
- Searching prior conversations for an immediate answer; G3 owns that
- Runtime memory recall; H7 owns that
- Accepting memory, publishing a skill, or activating a routine
- Training or fine-tuning on personal historical content
- Inferring sensitive traits, identity, health, politics, biometrics, credentials, or
  secrets
- Reprocessing all local history by default
- Reading content belonging to another signed-in account on the device
- Reconstructing hidden chain-of-thought or provider-private reasoning
- Reviving deleted, expired, or source-revoked content from logs, embeddings, or caches
- Treating a historical connector permission as current authority

## Interfaces consumed

- G3 authorized conversation/message enumeration, exact evidence refs, content digests,
  revisions, and deletion/tombstone behavior.
- Existing AI-backend run, event, tool, operation, citation, artifact, approval,
  checkpoint, and usage persistence.
- H5 extraction schema, provider/model policy, redaction, candidate validator, dedupe
  key, usage attribution, and backend proposal-ingestion contract.
- H6 memory proposal inbox and accepted-memory duplicate/contradiction search.
- I1 typed routine proposal intake and review.
- Backend local-account learning consent, user preferences, quotas, and identity.
- E1 retention, deletion, local export, audit, and outbox behavior.
- F1 evaluation records for extractor/backfill quality.

## Interfaces exposed

### Public APIs through the facade

```text
POST   /v1/learning/backfills/estimates
GET    /v1/learning/backfills/estimates/{estimate_id}
POST   /v1/learning/backfills
GET    /v1/learning/backfills
GET    /v1/learning/backfills/{backfill_id}
POST   /v1/learning/backfills/{backfill_id}/pause
POST   /v1/learning/backfills/{backfill_id}/resume
POST   /v1/learning/backfills/{backfill_id}/cancel
DELETE /v1/learning/backfills/{backfill_id}
GET    /v1/learning/backfills/{backfill_id}/progress
GET    /v1/learning/backfills/{backfill_id}/proposal-links
POST   /v1/learning/evidence/run-trajectories/open
```

Creating a backfill requires an unexpired estimate plus an exact confirmation digest.
The facade derives the local account from the verified session. It never accepts
trusted identity, cost used, proposal count, or consent status from request bodies.

### Internal backend APIs consumed by AI-backend workers

```text
POST /internal/v1/learning/backfill-estimates/claim
POST /internal/v1/learning/backfill-estimates/{estimate_id}/heartbeat
POST /internal/v1/learning/backfill-estimates/{estimate_id}/complete
POST /internal/v1/learning/backfill-estimates/{estimate_id}/release
POST /internal/v1/learning/backfill-jobs/claim
POST /internal/v1/learning/backfill-jobs/{backfill_id}/heartbeat
POST /internal/v1/learning/backfill-jobs/{backfill_id}/checkpoint
POST /internal/v1/learning/backfill-jobs/{backfill_id}/complete
POST /internal/v1/learning/backfill-jobs/{backfill_id}/release
POST /internal/v1/learning/backfill-jobs/{backfill_id}/usage
```

Candidate batches use the H5 backend-ingestion contract with
`origin=historical_backfill`, `backfill_id`, and source-unit lineage. Backend owns job
state; AI-backend workers pull claims through authenticated internal HTTP. Backend does
not call an AI-backend private route.

### AI-backend source ports

```text
HistoricalSourceEnumerator.estimate(scope, filters) -> SourceEstimate
HistoricalSourceEnumerator.page(scope, filters, cursor) -> HistoricalSourcePage
RunTrajectoryEvidenceIssuer.issue(source_unit, scope) -> RunTrajectoryRef
RunTrajectoryEvidenceReader.open(ref, runtime_context) -> RunTrajectoryEvidence
HistoricalLearningExtractor.extract(packet, policy) -> LearningCandidateBatch
```

### Events

```text
learning.backfill.estimated.v1
learning.backfill.confirmed.v1
learning.backfill.started.v1
learning.backfill.progressed.v1
learning.backfill.paused.v1
learning.backfill.cancelled.v1
learning.backfill.completed.v1
learning.backfill.failed.v1
learning.backfill.proposals_available.v1
```

Events contain job IDs, safe filters, counts, budget/usage aggregates, status, and reason
codes. They exclude message text, trajectory content, proposal bodies, queries, file
paths, connector payloads, and model prompts.

## Core contracts and state model

```text
HistoricalLearningScope
  local_account_id
  project_ids[]
  conversation_ids[]
  created_after?
  created_before?
  include_archived: bool
  candidate_kinds: fact | preference | project_convention | routine[]
  exclude_source_classes[]
  scope_digest

HistoricalLearningEstimate
  estimate_id
  local_account_id
  scope
  policy_revision
  source_watermark
  eligible_conversations
  eligible_runs
  estimated_input_tokens_low
  estimated_input_tokens_high
  estimated_model_calls
  estimated_cost_low
  estimated_cost_high
  estimated_duration
  skip_reason_counts
  state: queued | claimed | completed | failed | expired
  lease_owner?
  lease_epoch
  lease_expires_at?
  failure_code?
  expires_at
  confirmation_digest

HistoricalLearningBackfill
  backfill_id
  local_account_id
  estimate_id
  confirmation_digest
  scope
  consent_snapshot_ref
  policy_revision
  extractor_model_route
  extractor_prompt_revision
  source_watermark
  source_cursor?
  budget
  usage
  counts
  state: awaiting_confirmation | queued | claimed | running |
         pausing | paused | cancelling | cancelled |
         completed | completed_with_skips | failed | expired
  lease_owner?
  lease_epoch
  lease_expires_at?
  created_at
  started_at?
  completed_at?

HistoricalLearningSourceUnit
  source_unit_id
  backfill_id
  conversation_id
  conversation_revision
  run_id?
  run_terminal_sequence?
  source_digest
  message_evidence_refs[]
  run_trajectory_ref?
  state: pending | claimed | extracted | routed |
         skipped | source_deleted | failed
  attempt
  skip_or_error_code?
  candidate_batch_id?

RunTrajectoryRef
  opaque_ref
  account_scope_digest
  user_visibility_digest
  project_id?
  conversation_id
  run_id
  event_sequence_start
  event_sequence_end
  trajectory_digest
  issued_at
  expires_at

RunTrajectoryEvidence
  run_id
  terminal_status
  objective_or_request_message_refs[]
  operation_refs[]
  tool_result_refs[]
  citation_refs[]
  artifact_refs[]
  approval_and_effect_receipt_refs[]
  user_correction_message_refs[]
  final_answer_message_ref?
  verification_outcomes[]
  model_tool_policy_revisions
  observed_at
  trajectory_digest
```

`RunTrajectoryEvidence` contains typed records and protected refs, not hidden
chain-of-thought, raw secrets, full connector payloads, or copied transcript bodies.

## State-machine invariants

- An estimate performs no learning model call and creates no proposal.
- Estimate claims use leases; only a completed estimate can supply a confirmation
  digest.
- A job cannot enter `queued` without matching estimate/confirmation/scope/policy
  digests and current consent.
- The confirmed scope, source watermark, candidate kinds, budget, and extractor route
  are immutable. Changes create a new estimate/job.
- At most one live lease exists per backfill job and source unit.
- Checkpoints advance only with the current lease epoch and a monotonic source cursor.
- A source unit routes candidates at most once per extractor/source revision.
- Usage reservation occurs before every model call; actual usage settles afterward.
- Paused/cancelled/revoked jobs cannot admit a new model call.
- Proposal routing never changes H6 memory state or I1 routine activation state.
- A run-trajectory ref is evidence capability, not authorization; every open
  reauthorizes current visibility and source state.

## Detailed design

### 1. Estimate and source watermark

An estimate is a background metadata job so the product request path never scans
history. The AI-backend enumerator uses indexed conversation/run metadata to count
eligible current revisions and bounded token-size statistics without opening message
bodies or invoking a model.

The estimate displays:

- exact filters and candidate kinds;
- eligible conversation/run count;
- exclusions by safe reason code;
- token/model-call/cost low-high range;
- expected duration and concurrency;
- provider/data-processing policy;
- retention of proposals and job metadata; and
- controls available during and after processing.

`source_watermark` freezes the maximum conversation/update and terminal event sequence
eligible for the job. New conversations continue through live H5 and do not join the
backfill silently.

Estimates expire after a short configured period. Starting after policy, consent,
pricing, model route, or material source-count drift requires a new estimate.

### 2. Consent and confirmation

Backfill requires:

- desktop feature enablement;
- the source owner's affirmative learning setting;
- explicit confirmation of scope, candidate kinds, provider, and maximum cost;
- an authorized project/conversation relationship;
- no pending deletion or learning-processing restriction; and
- policy permission for each source class.

A user can backfill only their own eligible local history. Private conversations remain
excluded unless the user explicitly includes them in the confirmed scope. Switching
accounts, signing out, or beginning local-data deletion pauses the job and invalidates
its next admission check.

Confirmation creates a signed/digested snapshot. UI text cannot imply that proposals
will be saved automatically.

### 3. Indexed enumeration

The enumerator pages in deterministic order:

```text
(conversation_updated_at, conversation_id, run_terminal_sequence)
```

Required indexes cover local account/project, retention state, conversation time,
archived/private/ephemeral classification, run terminal state, and terminal sequence.
The cursor is opaque and bound to scope/watermark/policy.

Per source unit, eligibility is rechecked before content read:

- visible to the confirmed source owner;
- within project/conversation/time filters;
- not ephemeral, private-excluded, deleted, expired, or corrupted;
- terminal and supported run type where trajectory evidence is required;
- source class permitted for learning;
- connector/tool evidence permitted for extraction;
- not already processed under the same source/extractor revision; and
- budget remains.

An ineligible item is counted with a safe reason code and skipped without a model call.

### 4. Exact message evidence

Statements attributed to the user or assistant use G3 evidence refs that bind:

- local-account/project visibility;
- conversation/message ID;
- normalized exact span;
- message/content revision and digest;
- source timestamp; and
- expiry.

The extraction packet uses bounded exact spans selected under H5 policy. The candidate
must cite ref IDs, never restate a raw hidden source in audit metadata. Opening a
proposal reauthorizes the G3 ref and returns tombstone/stale state honestly.

### 5. Separate run-trajectory evidence

Message text cannot prove that a tool call succeeded, a test passed, an artifact was
accepted, or an external effect committed. For those claims, AI-backend issues a
`RunTrajectoryRef` over exact retained typed records.

The trajectory builder includes only:

- user-visible task/objective refs;
- tool/operation identity, argument digest, effect class, and outcome refs;
- citations and source digests;
- artifact/stage/approval/receipt state;
- user correction or acceptance messages;
- terminal result and safe verification signals; and
- model/tool/policy revisions needed to interpret the run.

It excludes system/developer prompts, hidden reasoning, unredacted arguments, secrets,
credential material, and inaccessible payloads. A successful assistant final message
is not outcome proof by itself.

Routine proposals require:

- repeated compatible source units with verified outcomes; or
- an explicit user-authored request for a routine, including a schedule/event trigger,
  in exact message evidence.

This avoids learning a recurring automation from one accidental execution.

### 6. Extraction packet and H5 reuse

The backfill worker builds the same bounded H5 extraction input shape, with additional
historical provenance:

```text
origin=historical_backfill
backfill_id
source_unit_id
source_watermark
message_evidence_refs[]
run_trajectory_ref?
```

It uses H5 model construction, purpose, provider routing, prompt revision, redaction,
candidate schema, validation, and usage meter. Allowed candidate kinds are intersected
with the confirmed job kinds.

Historical content is untrusted. Instructions inside old messages cannot change the
extractor schema, consent, scope, budget, tools, or routing. Extraction has no tools and
cannot fetch more context autonomously.

### 7. Deduplication and contradiction

Use H5's canonical candidate dedupe key across:

- live H5 extraction;
- every H9 backfill job;
- pending H6/I1 proposals; and
- accepted relevant memory/routine records.

Source-unit idempotency:

```text
(local_account_id, conversation_id, conversation_revision,
 run_id?, run_terminal_sequence?, extractor_policy_revision,
 extractor_prompt_revision, candidate_kind_set_digest)
```

Exact duplicate evidence attaches to the existing proposal or increments support
metadata according to the owning domain contract. Similar but contradictory candidates
are linked as `supports`, `updates`, or `contradicts`; they are not silently merged.

Routine clustering uses a deterministic task/capability/trigger fingerprint followed
by bounded similarity. The similarity model can suggest a group but cannot activate a
routine or erase conflict.

### 8. Proposal routing

Routing is propose-only:

- `fact`, `preference`, `project_convention` → H6 proposal inbox;
- `routine` → I1 proposal intake with H5's typed manual/schedule/event trigger;
- `skill` → rejected as wrong H9 candidate kind and, if eligible, handled only by H8.

Backend validates service identity, source/job lineage, candidate kind, evidence refs,
scope, size, sensitivity, and dedupe before atomic persistence. It returns durable
proposal IDs. AI-backend stores only IDs, counts, usage, and source-unit state.

Review surfaces group proposals by backfill job but retain ordinary H6/I1 decisions.
Rejecting or deleting the backfill does not silently change accepted downstream
records; the user sees and controls those records through their owning product.

### 9. Scheduling, checkpoints, and budgets

Backend jobs are claimed by AI-backend workers with leases. A checkpoint contains:

- source cursor;
- processed/skipped/failed/routed counts;
- completed source-unit IDs or compact durable watermark;
- reserved/settled tokens and cost;
- proposal IDs/counts by kind; and
- current policy/consent observation.

Workers process small batches and checkpoint between batches. Concurrency is bounded per
device/account/provider and yields to interactive runs. A cost reservation must fit the
remaining cap before each model call. Price changes that would exceed the cap pause the
job for a new estimate/confirmation.

Electron power/suspend/app-lifecycle signals are part of admission: by default cloud
model backfill pauses on battery saver, thermal pressure, network loss, system suspend,
or app quit; it resumes only after the user preference and current consent are
revalidated. Local-model extraction may continue on battery only when the user enables
it. No background daemon is required while the desktop app is closed.

`pause` stops new batches and lets the current bounded call finish or cancel safely.
`cancel` stops new work, requests cancellation of current inference, and leaves already
created proposals reviewable or optionally bulk-rejectable. Resume revalidates every
control and continues from the cursor.

### 10. Progress and review UX

The shared surface shows:

- confirmed filters, source watermark, model/provider, and cost cap;
- eligible, processed, skipped, failed, and remaining estimates;
- actual tokens/cost and current rate;
- pause/resume/cancel/delete controls;
- exclusions by safe reason, not sensitive content;
- proposal counts/links by memory/routine class; and
- warnings for source-deleted, policy-changed, or incomplete jobs.

No progress event includes excerpts. Proposal evidence opens only after an authorized
user action and uses G3/run-trajectory resolvers.

## Ownership and service boundaries

| Responsibility                                                           | Owner                           |
| ------------------------------------------------------------------------ | ------------------------------- |
| Consent/policy, estimates/jobs, leases, budgets, audit, proposal routing | Backend                         |
| Historical source enumeration and exact message/trajectory resolution    | AI backend                      |
| Extraction execution, usage settlement, checkpoints                      | AI-backend worker               |
| Memory proposal review/accepted records                                  | H6 in backend                   |
| Routine proposal review/activation                                       | I1 in backend                   |
| Skill historical backfill                                                | H8                              |
| Public API aggregation and shared review surface                         | Facade, API types, chat surface |

Backend owns durable product-job and proposal state. AI-backend owns only runtime source
resolution and worker execution records. The AI-backend worker pulls authenticated
backend internal APIs; backend does not import or call AI-backend source code. Apps call
the facade only.

## Persistence, retention, deletion, backup, and future sync

- The backend's already bundled local PostgreSQL database stores estimates, job
  revisions/state, confirmation/consent refs,
  filters, watermarks, leases, checkpoints, aggregate usage, safe counts, proposal
  links, and audit linkage.
- AI-backend stores source-unit execution state only as needed for idempotency,
  trajectory-ref issuance, usage reconciliation, and run lineage in its desktop-default
  `FileRuntimeApiStore` below `<userData>/agent-data/v1`.
- Message and trajectory bodies remain in existing conversation/run/evidence stores.
  H9 persists refs/digests, not copied transcripts or operation payloads.
- Estimate metadata expires quickly if no job is confirmed.
- Completed job operational checkpoints have a shorter retention than proposals/audit.
- Deleting a pending job removes eligible unneeded unit state, cursors, cached source
  resolutions, and unreviewed proposals according to H6/I1 policy.
- Conversation/message/run deletion immediately prevents new processing, invalidates
  refs, cancels pending source units, and deletes or marks pending proposals
  `source_deleted`.
- Accepted memory/routines follow H6/I1 correction/deletion rules; source deletion
  cannot retain forbidden raw source text and leaves only permitted provenance
  tombstones.
- “Delete local data” cascades through estimates, jobs, units, refs, proposal links,
  usage metadata, notifications, and caches across both services.
- Backup/export snapshots database metadata together with reachable ai-backend
  file-store refs; disposable indexes are rebuilt.
- Future consumer sync may replicate confirmed job metadata and accepted downstream
  records after explicit opt-in, but raw source history is never uploaded merely to
  synchronize job progress, and desktop backfill remains fully local/offline-capable
  with a local model.

## Authentication, authorization, privacy, security, and audit

- Facade derives the local account from the verified session; internal workers use the
  per-install service token plus trusted local-account headers over loopback.
- The signed-in owner is rechecked for create, inspect, pause/cancel, source open,
  proposal decision, export, and delete.
- Recheck consent, account deletion, project membership, source visibility, retention,
  source class, provider policy, and budget before each model call and proposal write.
- Redact/classify before external model processing. Secret/credential and forbidden
  sensitive-trait candidates fail validation.
- Run-trajectory refs are opaque, scoped, expiring, digest-bound, and reauthorized when
  opened; a signature is not authorization.
- Historical content cannot grant tools, roles, connectors, permissions, memory scope,
  or routine authority.
- Audit estimate, confirmation, start, claim, policy/consent recheck, checkpoint,
  usage, pause/resume/cancel, proposal routing, source invalidation, export, delete, and
  completion.
- Ordinary logs/audit/events contain IDs, digests, counts, and reason codes only.
- Provider training/retention, BYOK/local-model choice, and data-processing settings are
  visible at confirmation and enforced at every extraction. Network loss pauses a cloud
  route; it never silently changes providers.

## Performance and complexity budgets

Let:

- `C` be eligible conversations/runs in the confirmed scope;
- `B` be the source-page batch size;
- `T` be selected bounded input tokens;
- `D` be candidate/proposal rows in the dedupe scope.

Budgets:

- Estimate/enumeration uses indexed pagination: `O(log C + B)` per page, not an
  interaction-path `O(C)` scan.
- Processing necessarily totals `O(C + T)` across the confirmed job.
- Dedupe lookup is `O(log D)` per candidate with indexed canonical keys; bounded
  similarity reranking operates only over a small prefiltered set.
- Estimate creation request p95 below 100 ms over loopback; metadata estimate becomes
  available p95 below 10 seconds for 100,000 indexed conversations.
- Job status/progress read p95 below 100 ms.
- Default page size 100 sources; extraction microbatch no larger than 10 source units.
- Default concurrency: one active backfill and at most two extraction calls per device,
  in a lower-priority pool distinct from interactive model capacity.
- Default job caps: 10,000 conversations, 90-day lookback, 5 million selected input
  tokens, 5,000 proposals before automatic pause, and explicit currency cost cap.
- Pause/cancel/consent revocation stops new model-call admission within 10 seconds under
  healthy control-plane conditions.
- Progress checkpoints at least every 100 sources, every 60 seconds, and before lease
  release.
- Estimate error is tracked; actual cost can be lower but never exceed the confirmed
  cap.

Provider latency and total job duration are reported empirically by source count/token
bucket. Big-O is not used as evidence that a background job is cheap.

## Failure, idempotency, and recovery

- Estimate is idempotent by local-account/scope/policy/pricing/model-route digest within
  its validity window. Estimate workers use leases and resume indexed enumeration from
  a durable cursor without opening source bodies.
- Job creation is idempotent by estimate and confirmation digest; conflicting reuse
  fails.
- Claims use leases and monotonic epochs. Stale workers cannot checkpoint, reserve
  spend, or route candidates.
- Source units use the canonical source/extractor idempotency key across live and
  historical learning.
- Worker crash resumes from the durable cursor and reconciles source-unit/proposal IDs
  before another model call.
- Lost extraction response reconciles through model invocation/usage records; it does
  not blindly spend again.
- Candidate-batch ingestion is atomic and idempotent by job/source/batch digest.
- A source revision change after enumeration returns `source_changed`; the unit is
  skipped or explicitly rebound under a new job, never silently substituted.
- Provider timeout/retry follows H5 typed retry policy and remaining budget.
- Local backend, consent, policy, durable store, or source authorization failure fails
  closed.
- Repeated malformed extraction eventually marks the source unit failed and continues
  only if job policy permits partial completion.
- Price/budget uncertainty pauses the job rather than overrunning the cap.
- Cancellation and deletion are resumable and record retained items/reasons.

## Observability and quality gates

Metrics:

- estimates requested/completed/expired and estimate error;
- jobs confirmed/queued/running/paused/cancelled/completed/failed;
- eligible/processed/skipped/source-deleted/failed source units;
- queue age, claim latency, lease expiry, checkpoint lag, and resume count;
- input tokens, model calls, cost estimated/reserved/settled, and cap utilization;
- candidates valid/rejected/routed/deduplicated/contradictory by kind;
- proposals reviewed/accepted/rejected/corrected and time to decision;
- exact evidence open success, stale/deleted/unauthorized rate;
- live-versus-backfill duplicate rate;
- privacy/policy block reasons with low-cardinality labels; and
- user pause/cancel/delete and opt-out after backfill.

Trace lineage:

```text
estimate/confirmation → backfill job/revision → source cursor/unit
  → G3 message refs + run-trajectory ref → H5 extraction/model usage
  → candidate batch → H6 memory proposal | I1 routine proposal → decision
```

F1 quality suites measure:

- preference/fact/project-convention precision and contradiction handling;
- routine recurrence/outcome qualification;
- proposal usefulness and reviewer correction;
- evidence-ref resolution and source-span support;
- sensitive/secret inference rejection;
- live/backfill dedupe;
- old source versus newer correction;
- partial/deleted/ephemeral/private histories; and
- cost/latency at 10, 1,000, and 10,000 source units.

No rollout advances with unauthorized processing, automatic acceptance/activation,
unbounded cost, or evidence-free proposals.

## Rollout and backout

1. Land contracts, indexed metadata estimator, and synthetic fixtures with no content
   reads.
2. Enable estimate-only for opt-in desktop dogfood.
3. Issue/open run-trajectory refs in test/shadow mode.
4. Run dry backfills that extract and score candidates without persistence.
5. Enable memory proposals for small user-selected conversation lists.
6. Add project/time-range jobs and routine proposals after I1 intake is ready.
7. Expand caps and desktop cohorts only after privacy, quality, cost, battery, and
   recovery gates.

Backout prevents new estimates/confirmations/claims and cancels or pauses active jobs.
Existing H6/I1 proposals remain reviewable/rejectable under their owning contracts.
Accepted records are not silently rolled back. Source refs, audit, usage, deletion, and
export controls remain available.

## Implementation slices

1. Estimate, scope, job, unit, cursor, budget, and event contracts
2. Backend embedded-local-PostgreSQL store, migrations, routes, leases, and audit
3. Indexed AI-backend historical source estimator/enumerator
4. Run-trajectory ref issuer, reader, redaction, and source-open projection
5. H5 extraction adapter with historical origin and shared idempotency
6. Cross-live/backfill dedupe and contradiction links
7. H6 memory and I1 typed-routine proposal routing
8. Progress/consent/pause/cancel/delete facade and shared UI
9. Retention/deletion/local-export/backup jobs and repair tooling
10. F1 evaluation, dashboards, alerts, feature flags, and staged rollout

## Test plan

### Unit

- Scope/confirmation/estimate/job digests and immutable fields
- Estimate claim/lease/cursor recovery and confirmation eligibility
- Eligibility, consent, policy, retention, candidate-kind intersection
- Cursor ordering, source watermark, source-unit idempotency, and budget reservation
- Run-trajectory redaction and digest/ref validation
- H5 candidate routing and skill-kind rejection
- Deduplication, contradiction, recurrence, and proposal linkage

### Store and concurrency

- PostgreSQL claim/lease/checkpoint/usage/proposal transaction behavior
- Multiple workers cannot process one unit twice
- Pause/cancel/revoke races at enumeration, model admission, and routing boundaries
- Crash after model call/before routing and after routing/before checkpoint
- 100,000-row indexed estimate/enumeration query plans

### Authorization and privacy

- Cross-account, renderer identity forgery, excluded-project, and private-chat denial
- A second signed-in account cannot open/process private history
- Ephemeral/deleted/expired/consent-withdrawn combinations
- Secret, credential, sensitive-trait, prompt-injection, and malicious tool-output
  fixtures
- Run-trajectory ref forgery, expiry, digest mismatch, deletion, and replay

### Integration

- Estimate → confirmation → claim → exact evidence → H5 extraction → H6 proposal
- Repeated verified workflow → normalized I1 routine proposal
- Skill-like historical candidate routes only to H8, never H9 persistence
- Live H5 and H9 backfill converge on one proposal/dedupe relationship
- Proposal review opens exact message and trajectory evidence through facade
- Source deletion cancels pending work and invalidates pending proposal evidence

### Performance and recovery

- 10/1,000/10,000 source jobs with token/cost caps
- Interactive workload is not starved by backfill concurrency
- Estimate accuracy, checkpoint recovery, cancellation latency, and bounded memory
- Provider timeout/network loss, local-backend outage, price change, worker/app restart,
  suspend/resume, battery saver, and thermal pressure

## Definition of done

- Users receive an estimate and confirm exact scope/kinds/provider/cost before content
  processing.
- Backfill enumerates only authorized retained history with resumable indexed cursors.
- Every candidate uses exact G3 message refs and, where outcome proof is required, a
  separately authorized exact run-trajectory ref.
- H5 owns extraction semantics and dedupe across live and historical learning.
- Facts/preferences/project conventions route only to H6 proposals; routines with
  manual/schedule/event trigger specs route only to I1 proposals; skills remain
  H8-owned.
- No job can accept memory, publish a skill, activate a routine, or widen authority.
- Pause, cancel, consent withdrawal, deletion, crash recovery, usage reconciliation,
  local export/backup, and backout pass tests.
- Cost never exceeds the confirmed cap and all model usage is attributable.
- F1 quality/privacy gates pass for representative historical data.

## Guardrails

- Historical retention is not learning consent.
- Another local account or renderer process is not authorized to read private history.
- Estimate first; no model call or proposal during estimation.
- Propose only; never accept memory or activate a routine.
- Skill backfill remains H8-owned.
- Use exact source refs; never copy whole transcripts into job or audit records.
- Message prose does not prove tool/effect success; use typed trajectory evidence.
- Do not reconstruct or retain hidden chain-of-thought.
- Do not process deleted, ephemeral, expired, source-revoked, or consent-revoked data.
- Do not use old connector scopes as current authority.
- Do not let similarity silently merge contradictions.
- Do not exceed the confirmed budget or silently expand the time/project/conversation
  scope.

## Open decisions

1. Which maximum lookback and source-count caps ship for desktop devices with different
   memory/CPU classes?
2. Which provider/model routes are permitted for historical processing by default?
3. What recurrence/support threshold qualifies a routine proposal from history?
4. Should a cancelled job offer one bulk-reject action for its still-pending proposals?
5. Which run/tool result classes are eligible for trajectory evidence in the first
   release?
6. What estimate drift threshold requires a fresh confirmation?
7. Whether cloud-model backfill should default to AC-power-only on laptops.
