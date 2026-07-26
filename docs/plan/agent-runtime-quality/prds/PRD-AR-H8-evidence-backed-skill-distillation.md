# PRD-AR-H8 — Evidence-backed skill distillation and backfill

**Goal.** Convert repeated, successfully verified work trajectories into
reviewable skill drafts with source evidence, capability requirements, failure
modes, and machine-runnable declarative evaluation fixtures. Distillation may draft a skill
version but can never publish it.

## Metadata

| Field        | Value                                                                                                                    |
| ------------ | ------------------------------------------------------------------------------------------------------------------------ |
| Status       | Proposed                                                                                                                 |
| Priority     | P2                                                                                                                       |
| Owners       | `services/ai-backend` (trajectory analysis/drafting), `services/backend` (candidate/draft ownership), facade/UI (review) |
| Depends on   | AR-F1, AR-G3, AR-H1, AR-H2, AR-H4 consolidation, AR-H5, AR-H6 deletion contract; optionally AR-H3 telemetry              |
| Rollout flag | `SKILL_DISTILLATION_ENABLED`, tenant opt-in                                                                              |
| UI impact    | Learned-procedure candidate queue, evidence viewer, draft diff/tests                                                     |

## Implementer brief

Read:

1. `services/ai-backend/src/runtime_worker/jobs/proposal_extractor.py`.
2. Runtime tool invocation, citation, artifact, approval, and operation records.
3. `services/ai-backend/src/agent_runtime/capabilities/skills/`.
4. `services/backend/src/backend_app/service.py` skill registry.
5. Backend memory/proposal and agent/routine domains.
6. AR-F1, AR-G3, AR-H2, AR-H5, and AR-H6.
7. Generative Surfaces A2, A3, B1, D2, and E1.

## Problem statement

A short H6 `kind=skill` user-capability memory is not a reusable procedure. A safe procedure
requires triggers, prerequisites, ordered steps, intended capabilities,
verification, and known failure paths. One successful chat may be accidental;
chat prose may omit critical tool arguments or include secrets. There is no
pipeline that clusters repeated evidence, reconstructs a safe procedure, tests
it against the source trajectory, and submits an AR-H2 draft.

## Current implementation and predecessor contracts

- **[shipped]** Runs preserve typed operations, tool events, citations, artifacts, approvals,
  and final outcomes.
- **[depends on]** AR-H5 produces evidence-linked procedural candidates.
- **[shipped]** Backend has versioned Markdown skills.
- **[depends on]** AR-H2 defines immutable publication authority.
- **[shipped]** Tool/capability policy is enforced at runtime; a skill cannot grant access.
- **[depends on]** AR-F1 supplies a promotion/evaluation plane.

## Objectives and outcomes

1. Prefer repeated, verified trajectories over unsupported chat summaries.
2. Remove secrets, user-specific IDs, and incidental paths while preserving
   necessary parameters and constraints.
3. Produce an Agent Skills-compatible package draft with tests and provenance.
4. Link each material procedure step to supporting source evidence.
5. Measure whether a proposed skill improves held-out task success before
   publication.
6. Support bounded, consented backfill over selected historical conversations.

Launch gates:

- every generated step has evidence or is labeled reviewer-authored;
- zero credentials/secrets in drafts or test fixtures;
- draft capability declarations cannot widen the publisher's policy;
- source replay/held-out evaluation meets configured success threshold;
- no automatic publish, replacement, or activation.

## Non-goals

- Importing third-party skills (AR-H1).
- Publishing/rollback (AR-H2).
- Online skill ranking (AR-H3).
- Consolidating or merging existing skills (AR-H4/H2).
- Memory extraction or recall (AR-H5/H6/H7).
- Fine-tuning on customer trajectories.

## Interfaces consumed

- AR-H5 procedure candidates and source refs.
- AR-G3 authorized historical message-evidence retrieval only.
- Exact operation/tool-result/artifact/citation/approval record resolvers owned by
  their runtime domains.
- AR-H1 `agent_generated` package intake and scanner report.
- AR-H6 normative source-deletion matrix.
- AR-F1 evaluation runners and scoring.
- AR-H2 draft-version API.
- AR-H4 exact-revision consolidation-proposal intake.

## Interfaces exposed

```text
SkillDistillationCandidate
  candidate_id
  tenant_id
  owner_user_id
  proposed_name
  trigger_summary
  message_evidence_refs[]
  operation_trajectory_refs[]
  capability_fingerprint
  outcome_evidence[]
  cluster_key
  support_count
  evidence_scope_ceiling
  source_acl_digest
  state

SkillDraftPackage
  distillation_output_id
  h2_draft_id?                         # set only after exact H1 ready_for_review handoff
  draft_origin: distillation
  candidate_id
  manifest
  skill_markdown_ref
  support_files[]
  evidence_map_ref
  declarative_evaluation_fixtures[]
  required_capability_classes[]
  forbidden_capabilities[]
  source_digest
  generator_model
  prompt_revision
  policy_revision
  scan_report_ref
  evaluation_report_ref?
```

Evidence map:

```text
procedure_step_id -> source run/message/operation/result refs[]
```

H5 `kind=procedure` is the only learned-candidate kind accepted here. H6 memory
`kind=skill` means user capability and is rejected at this boundary.

## Detailed design

### 1. Candidate qualification

A candidate qualifies through either:

- explicit user request to learn/save the completed workflow; or
- repeated-pattern policy: at least a tenant-configured number of successful
  trajectories with compatible goals/capabilities and verified outcomes.

Failures followed by a corrected successful path are valuable evidence.
Unverified “looks good” final responses do not count as success.

### 2. Trajectory normalization

Build a redacted normalized representation:

- goal/constraints and task family;
- capability IDs and parameter _shapes_, not credentials;
- result/evidence refs and verification outcome;
- failure/retry branches;
- user corrections/accepted final behavior;
- relevant environment/tool schema revisions.

G3 supplies exact message spans only. Operation, tool-result, artifact, citation, and
approval evidence is opened through the owning exact-record resolvers with current ACL,
retention, revision/digest, and source-state checks. H8 never manufactures those records
from transcript prose or asks G3 to return excluded tool data.

Replace record IDs, URLs, filesystem paths, people, and tenant names with typed
parameters unless they are intentionally constant and approved.

### 3. Clustering and contradictions

Use deterministic capability/task fingerprints plus bounded lexical/semantic
similarity to find related candidates. Clustering is suggestive only. Split
workflows when required capabilities or verification differ materially.
Conflicting source trajectories are shown to the reviewer and block automatic
draft synthesis unless the model produces explicit conditional branches.

### 4. Draft synthesis

A bounded auxiliary model receives normalized evidence, the canonical skill
authoring schema, and tool vocabulary. It produces:

- frontmatter: name, description, version draft, compatibility, intended tools;
- When to Use / When Not to Use;
- prerequisites and parameter definitions;
- procedure with decision points;
- pitfalls/failure recovery;
- verification and rollback/stop conditions;
- referenced templates/examples only when supported.

The model cannot invoke tools during synthesis. Output is parsed strictly and written
to an immutable A2 artifact. `ai-backend` submits that exact artifact/digest through
H1's internal `agent_generated` intake. Only a `ready_for_review` scanner report for
that exact package digest may be handed to H2 as an unpublished package-backed draft;
H8 cannot call H2 with inline generated text or bypass H1.

### 5. Declarative evaluation fixtures

Generate redacted, machine-runnable declarative evaluation fixtures from source
trajectories and at least one negative
case:

- should trigger / should not trigger;
- correct capability selection;
- expected effect class/approval boundaries;
- expected artifact/evidence result;
- failure/retry behavior;
- no authority widening.

Deterministic validators run first. Optional model evaluation uses the same
eligible provider policy and AR-F1 experiment records.

The evaluator interprets closed-schema expected inputs, capability selections,
approval/effect classes, and output assertions. It cannot execute package scripts,
shell commands, `npx`, `uvx`, MCP servers, network calls, browser actions, workspace
writes, or real effects. Any simulation uses recorded/fake adapters with no credentials
and is classified as evaluation, never package execution.

### 6. Reviewer experience

The reviewer sees:

- why the procedure was proposed;
- supporting and conflicting source spans;
- generalized parameters;
- intended capabilities and approval requirements;
- full package diff;
- scan and evaluation results;
- detected tenant/user-specific content.

Actions: edit draft, request regeneration, reject, split, request consolidation with an
existing skill, or send to AR-H2 publish review. A consolidation request creates an H4
proposal bound to the distillation output and the existing skill's exact revision and
digest. Only H4's reviewed workflow and H2's draft/lifecycle contracts may synthesize
or publish the merged result; H8 itself never merges or supersedes a skill. Rejection
reasons remain local evaluation feedback.

### 7. Historical backfill

Backfill is a separately authorized job with tenant/user/conversation filters,
time range, maximum conversations, cost cap, and dry-run estimate. It reads
only retained, searchable AR-G3 message evidence plus separately authorized exact
operation-trajectory records. Jobs are resumable, deduplicate against live candidates,
and create proposals only. No global default backfill.

## Persistence, retention, and deletion

Backend owns candidate/draft metadata and AR-H2 package versions. Large
evidence/evaluation artifacts use A2 refs. ai-backend owns bounded job state
and usage. Source deletion follows H6's normative matrix: exact copies are
deleted/redacted; pending candidates and unsupported unpublished drafts are withdrawn;
partially supported drafts are marked incomplete and revalidated. A published
trajectory-derived skill with insufficient remaining support moves through H2 to
`review_required`, and H3 excludes it from automatic selection/load until an authorized
reviewer reapproves or re-sources an exact revision. Source-deleted provenance retains
only a non-content tombstone unless legal hold governs the protected source. Backfill
checkpoints are deleted after completion/retention.

## Authorization, privacy, and supply-chain security

- Candidate scope cannot exceed the intersection of all source trajectories
  and reviewer authority.
- H5's immutable `EvidenceScopeCeiling` and source ACL digest are copied into the H1
  package provenance and H2 draft. Review can narrow them; any widening requires a new,
  authorized evidence basis and fresh H1/H2 review.
- Shared/org publication requires the relevant AR-H2 release role.
- Redaction occurs before synthesis/evaluation calls.
- The entire generated package—not only support files—obeys AR-H1
  `agent_generated` path/type/size/secret/license/executable scanning.
- The draft may declare required capabilities but cannot install/connect them.
- Sensitive connector results may be excluded from distillation by policy.
- Audit job, sources, model/prompt/policy, scan/eval, reviewer, and final
  disposition without logging content.

## Performance and cost

- Live runs enqueue only AR-H5 candidates; distillation is asynchronous.
- Cluster lookup is indexed; no unbounded all-pairs trajectory comparison.
- Each job has source/run/token/output/tool-schema/cost limits.
- Auxiliary calls use an approved task model and hard daily tenant budgets.
- Backfill exposes estimate and progress; default concurrency is low and
  preemptible behind interactive workloads.
- Target: draft available within five minutes for an explicit request, absent
  provider backlog.

## Failure, retry, and recovery

- Job key includes candidate/source digest and synthesis policy revision.
- Provider failure retries boundedly; draft creation is atomic.
- Validation failure leaves a rejected evaluation report; H1 scan failure leaves a
  quarantined/rejected package and no H2 draft.
- Source revision/deletion before synthesis aborts or rebuilds the digest.
- Duplicate explicit and repeated candidates converge to a related-candidate
  group, not duplicate published skills.
- Backfill leases/checkpoints support restart and cancellation.

## Observability and quality

Track qualification sources, support counts, cluster/split/consolidation-request rates,
redaction blocks, scan findings, validation pass, reviewer edits/rejections,
time/cost per draft, publish conversion, later skill activation/success/correction,
and source-deletion invalidation. AR-F1 compares tasks with no skill, existing
skill, and proposed skill.

## Rollout and backout

1. Explicit user-request candidates only; internal tenants.
2. Generate evidence/normalization preview without model synthesis.
3. Enable draft synthesis and deterministic validation.
4. Enable AR-F1 held-out evaluation and publish handoff.
5. Enable repeated-pattern qualification.
6. Offer bounded historical backfill behind explicit admin/user action.

Backout stops new jobs and cancels backfill. Existing unpublished drafts remain
reviewable/exportable or may be withdrawn. Published versions are unchanged.

## Implementation slices

1. Candidate/source/trajectory contracts.
2. Normalizer, redaction, capability fingerprint, and clustering.
3. Synthesis schema/prompt and strict parser.
4. Immutable A2 output, AR-H1 `agent_generated` scan, and exact AR-H2 package-backed
   draft persistence.
5. Fixture generation and deterministic validators.
6. AR-F1 evaluation integration.
7. Review UI and publish handoff.
8. Resumable dry-run backfill.

## Test plan

- Explicit and repeated qualification boundaries.
- Successful, failed-then-corrected, and unverified trajectories.
- Secret/PII/path/record-ID generalization.
- Conflicting workflows and conditional split.
- Cross-tenant/source-scope intersection.
- Malformed/oversize/injected model output.
- Whole generated-package path/type/size/secret/license/executable scan with exact
  artifact/package/draft digest binding.
- Trigger/no-trigger/effect-boundary fixtures.
- Declarative fixture evaluator cannot run scripts, `npx`/`uvx`, MCP, network, or real
  effects.
- Source deletion, duplicate job, crash/retry, cancel/backfill resume.
- Attempted direct publish or capability grant must fail.
- Consolidation request creates an exact-revision H4 proposal; H8 cannot create a merged
  draft, supersede pointer, or lifecycle decision.

## Definition of done

- [ ] Qualified trajectories produce evidence-mapped unpublished drafts.
- [ ] Drafts are redacted, scanned, validated, and capability-bounded.
- [ ] Reviewers can inspect every supporting/conflicting source.
- [ ] No distillation path can publish or grant authority.
- [ ] Historical backfill is opt-in, bounded, resumable, and propose-only.
- [ ] AR-F1 evaluation gates pass before publish handoff.
- [ ] Shared program DoD passes.

## Guardrails

- A chat summary is not a skill.
- One unverified success is not evidence of a reusable procedure unless the
  user explicitly asks for a draft.
- Never preserve secrets or tenant-specific identifiers as procedure constants.
- Never turn a skill into executable authority.
- Never train or publish automatically from customer trajectories.

## Open decisions

1. Minimum repeated-support count by task family.
2. Whether reviewer-authored steps without trajectory evidence are allowed and
   how they are labeled.
3. Retention period for unpublished distillation evidence maps.
