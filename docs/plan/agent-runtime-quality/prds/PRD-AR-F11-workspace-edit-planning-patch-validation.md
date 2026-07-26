# PRD-AR-F11 — Workspace edit planning, patch sets, and validation

**Goal:** Make repository edits faster and more reliable by discovering the right
targets once, applying one bounded multi-file patch set to the durable workspace
overlay, validating the exact proposed tree, and presenting one reviewable staged
change without creating a second workspace or host-write path.

## Metadata

| Field                   | Value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Status                  | Proposed                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Priority                | P1                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Wave                    | F — harness quality and efficiency                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Primary owner           | `ai-backend` workspace capability and edit controller                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Supporting owners       | Desktop workspace authority, shared workspace UI, evaluation owners                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Depends on              | [C1 workspace overlay](../../generative-surfaces-v2-1/prds/PRD-C1-workspace-overlay.md), [C2 workspace broker commit](../../generative-surfaces-v2-1/prds/PRD-C2-workspace-broker-commit.md), [C3 workspace product integration](../../generative-surfaces-v2-1/prds/PRD-C3-workspace-product-integration.md), [D3 sandbox adapter](../../generative-surfaces-v2-1/prds/PRD-D3-sandbox-adapter.md), [F1 evaluation and promotion](./PRD-AR-F1-harness-observability-evaluation-promotion.md), [F4 tool-use controller](./PRD-AR-F4-task-aware-tool-use-controller.md), [F6 safe concurrency](./PRD-AR-F6-capability-concurrency-safe-batching.md) |
| Rollout flag            | `WORKSPACE_PATCH_SET_ENABLED`, with tenant and task-family cohorts                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Primary success measure | Ten-or-more-file refactors complete with fewer model/tool turns and no regression in patch correctness, reviewability, or host-write safety                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |

## Implementer brief

Read before implementation:

1. `services/ai-backend/src/agent_runtime/capabilities/workspace/`.
2. `services/ai-backend/src/agent_runtime/capabilities/sandbox/`.
3. `services/ai-backend/src/agent_runtime/api/workspace_coordinator.py`.
4. `services/ai-backend/src/agent_runtime/api/workspace_approval_service.py`.
5. `services/ai-backend/src/runtime_worker/`.
6. `services/ai-backend/tests/unit/agent_runtime/capabilities/workspace/`.
7. `services/ai-backend/tests/unit/agent_runtime/capabilities/sandbox/`.
8. `services/ai-backend/tests/unit/architecture/test_workspace_effect_route.py`.
9. C1, C2, C3, D3, F1, F4, and F6.

C1 is the sole mutable virtual workspace and already defines durable overlay entries,
preimages, `WorkspaceChangeSet`, and exact staging. C2 is the sole host filesystem
authority and owns prepare/commit/reconcile. C3 owns approval and product presentation.
D3 may validate an immutable merged snapshot and return a declarative patch, but it
cannot mutate the host. This PRD adds edit planning, atomic patch-set application to C1,
validation orchestration, and repair policy only.

## Problem statement

The safe workspace path is intentionally explicit, but the model-facing edit loop still
encourages repeated file reads and one-file mutations. A cross-cutting change can
devolve into:

1. search for one symbol;
2. read one file;
3. edit one file;
4. discover a second dependency;
5. reread already seen context;
6. repeat edits and diagnostics file by file; and
7. leave several independently staged changes that are hard to review as one intent.

For a change touching `f` files, this can require `O(f)` model-visible mutation calls
and additional read/repair turns. The complexity of reading and writing `f` files
cannot disappear, but model round trips and workspace-tool calls can be reduced toward
`O(1)` for a well-bounded patch plan.

Naively adding a generic patch or shell tool would undermine the existing architecture.
A patch may be based on stale bytes, apply only partially, hide unexpected files, run an
untrusted formatter, or bypass the exact digest approved by the user. The optimized
path must therefore remain an overlay operation with exact preimages, deterministic
application, bounded validation, explicit diagnostics, and C2-owned host commit.

## Current state and strengths to preserve

- C1 implements a merged base-plus-overlay view, durable overlay revisions, exact
  preimages, and multi-path `WorkspaceChangeSet` staging.
- Agent reads after a workspace edit observe the overlay, not stale base bytes.
- C2 fails closed on host drift and consumes a one-use commit permit for the exact
  reviewed change set.
- C3 gives web and desktop one staged-review flow and keeps host mutation out of web
  and renderer processes.
- D3 has immutable snapshot, quota, artifact, and declarative patch seams suitable for
  validation.
- Existing `write_file` and `edit_file` remain useful for genuinely surgical changes.
- Run events, operations, tool ordinals, artifacts, and approvals provide lineage for
  measuring edit behavior.

No shipped component currently owns repository-wide target planning, one atomic
structured patch-set call, deterministic validation profiles, or measured escalation
from surgical edits to patch sets.

## Objectives and outcomes

1. Discover the smallest relevant target set without repeatedly scanning the repository.
2. Bind every changed or deleted file to its exact merged-view preimage digest.
3. Apply a multi-file create/replace/delete/move patch atomically to one C1 overlay
   revision.
4. Validate the exact proposed merged tree with deterministic format, syntax, type, and
   test checks under explicit budgets.
5. Feed concise, structured diagnostics into at most a bounded number of repair turns.
6. Produce one exact C1 `WorkspaceChangeSet` and one C3 review intent per logical edit.
7. Preserve reliable one-file edits and define when the controller escalates or falls
   back.
8. Measure tool calls, model turns, latency, changed-file precision, validation success,
   and user correction against the existing edit path.

### Launch gates

- Zero direct host writes or alternate workspace commit paths.
- Zero partial overlay application for an invalid patch set.
- Zero silent overwrite when a preimage or overlay manifest revision has changed.
- At least 30% lower median model-visible workspace calls on the ten-or-more-file
  refactor suite.
- At least 20% lower median edit-loop wall time on the same suite, excluding
  user-approval wait.
- No statistically meaningful regression in task success, tests passed, diff precision,
  approval accuracy, or post-approval conflict handling.
- At least 95% of successful patch-set tasks reach review with no more than one
  model-authored repair patch.

## Scope

- Repository target discovery over the currently granted merged workspace view
- Compact edit plans and exact target inventories
- Structured multi-file patch manifests
- Atomic patch application into C1
- Preimage and overlay-revision validation
- Deterministic formatter, syntax, typecheck, lint, and test profiles
- D3-backed validation when isolation or toolchain execution is required
- Structured diagnostics and bounded repair patches
- Fallback to existing surgical edit/full-file replacement paths
- Conflict handling before review and drift handling at commit
- F1 evaluation, telemetry, rollout, and backout

## Non-goals

- Replacing C1 overlay storage, C2 host authority, C3 approval UI, or D3 isolation
- Writing directly to a user workspace, Git worktree, or host path
- Running arbitrary model-supplied shell commands
- Automatically committing to Git, staging Git changes, pushing, or creating a pull
  request
- Inventing a new approval, effect, artifact, or audit system
- Building a language server, compiler, formatter, or test runner
- Guaranteeing atomicity for external systems invoked by tests
- Automatically rebasing a user-approved change after host drift
- Loading the whole repository or every changed file into a model prompt

## Interfaces consumed

- C1 merged workspace reads, base/overlay manifest revision, immutable content refs,
  overlay transaction, and `WorkspaceChangeSet` staging.
- C2 prepare/commit/reconcile only after ordinary C3 review and approval.
- C3 workspace stage projection and approval state.
- D3 immutable snapshot execution and `SandboxPatchManifest` import.
- Existing workspace search/glob/grep/read capabilities over the granted merged view.
- Project instructions and validation configuration discovered through
  [G4 workspace instructions](./PRD-AR-G4-scoped-workspace-instruction-discovery.md)
  when available.
- F4 task plan, tool-call budget, duplicate-call signals, and stop/escalate decision.
- F6 dependency-safe read concurrency.
- F1 experiment, trace, scorer, and promotion contracts.

## Interfaces exposed

### Runtime ports

```text
WorkspaceTargetPlanner.plan(request, runtime_context) -> WorkspaceEditPlan
WorkspacePatchSetValidator.validate(manifest, runtime_context) -> ValidatedPatchSet
WorkspacePatchSetApplier.apply(validated_patch_set) -> WorkspaceChangeSetRef
WorkspaceValidationCoordinator.run(change_set_ref, profile) -> ValidationReport
WorkspaceRepairController.decide(report, attempt_state) -> RepairDecision
```

### Model-visible capability

```text
apply_workspace_patch_set(
  intent,
  base_manifest_revision,
  operations[],
  expected_result?
)
```

The model-facing schema is closed and bounded. It does not accept a host path, shell
command, environment map, approval decision, commit permit, or unscoped blob reference.
Existing search/read tools remain the source of target bytes. Validation is
harness-driven after application rather than a sequence the model must remember to run.

### Events

```text
workspace.edit_plan.created.v1
workspace.patch_set.validated.v1
workspace.patch_set.applied.v1
workspace.validation.started.v1
workspace.validation.completed.v1
workspace.repair.requested.v1
workspace.edit_ready_for_review.v1
workspace.edit_blocked.v1
```

Events contain IDs, digests, file counts, check names, outcomes, durations, and bounded
reason codes. File bodies, raw patches, host paths, command output, and secrets remain
behind protected refs.

## Core contracts and state model

```text
WorkspaceEditPlan
  edit_plan_id
  run_id
  objective_digest
  base_manifest_revision
  target_files[]
  dependency_edges[]
  expected_creates[]
  expected_deletes[]
  validation_profile_id
  discovery_evidence_refs[]
  budget
  plan_digest

WorkspaceTarget
  virtual_path
  kind: source | test | config | generated | documentation
  reason_code
  preimage_digest?
  content_ref?
  symbol_or_span_hints[]

WorkspacePatchSet
  patch_set_id
  edit_plan_id
  base_manifest_revision
  operations[]
  expected_changed_paths[]
  expected_result
  patch_digest

WorkspacePatchOperation
  operation_id
  kind: create | replace | delete | move | hunks
  virtual_path
  destination_virtual_path?
  expected_preimage_digest?
  content_ref?
  hunks[]
  mode_change?

WorkspacePatchHunk
  before_anchor_digest
  after_anchor_digest
  old_span_digest
  replacement_ref
  expected_match_count: 1

ValidationProfile
  profile_id
  revision
  checks[]
  per_check_timeout
  total_timeout
  output_limit
  allowed_toolchain
  allow_network: false

ValidationReport
  report_id
  change_set_ref
  merged_manifest_digest
  profile_revision
  checks[]
  overall: passed | failed | timed_out | unavailable | cancelled
  diagnostics_ref?
  output_artifact_refs[]
  started_at
  completed_at

WorkspaceEditAttempt
  edit_attempt_id
  run_id
  state: discovering | planned | applying | validating |
         needs_repair | ready_for_review | blocked | cancelled
  plan_ref
  patch_set_refs[]
  active_change_set_ref?
  validation_report_refs[]
  repair_count
  fallback_reason?
```

`WorkspacePatchSet` is an input to C1, not a second durable workspace record. C1 remains
canonical for overlay entries, bytes, revisions, stages, and change sets.

## Invariants

- Every operation resolves within the current granted virtual workspace root.
- Replace, delete, move, and hunk operations require the exact current merged preimage
  digest.
- A patch set applies all operations to one new overlay revision or applies none.
- The resulting changed-path set must equal the declared expected changed-path set.
- Validation reads the exact merged manifest created by patch application.
- Formatter-produced changes are declarative D3 patches imported into C1 and therefore
  create a new change-set revision.
- A passing validation report is valid only for its exact merged manifest digest and
  validation-profile revision.
- The review surface binds the final C1 change-set digest, including formatter and
  repair changes.
- Approval never authorizes a rebase or a different patch.
- C2 remains the only component that can mutate host files.

## Detailed design

### 1. Admission and path selection

F4 classifies an edit as `surgical` or `patch_set`.

Use the existing surgical path when all are true:

- one known file is affected;
- the expected edit has one unambiguous anchor;
- no rename, generated output, or coordinated test/config update is expected; and
- the file and replacement fit the surgical-edit budget.

Select the patch-set path when any is true:

- two or more coordinated files are expected;
- a symbol rename or cross-module refactor is requested;
- create/delete/move operations must be reviewed as one intent;
- a deterministic generator/formatter is part of the change;
- one surgical edit failed due to ambiguous or stale anchors; or
- F1 evidence shows the task family is more reliable through patch sets.

The controller may escalate after one failed surgical mutation. It may not alternate
between modes indefinitely.

### 2. Target discovery

Target discovery executes a bounded plan over the granted merged view:

1. load scoped workspace instructions;
2. inspect repository metadata and supported symbol/index information;
3. issue batched name/text/symbol searches;
4. expand direct import/reference neighbors to a bounded depth;
5. identify colocated tests and configuration declared by project rules;
6. read exact candidate spans or bounded files; and
7. freeze a target inventory at one base manifest revision.

Search results are deduplicated by virtual path and content digest. F6 may overlap
independent reads. The planner stops when required target classes are covered or the
discovery budget is exhausted. Exhaustion is reported; it is not interpreted as proof
that no other target exists.

Generated/vendor/binary/lock files default to excluded unless project instructions or
the requested task explicitly require them. The model sees concise target summaries and
exact relevant spans, not the full repository.

### 3. Patch authoring

The model produces one structured manifest. File bytes and large replacements use
protected refs. Hunk anchors include digests of exact surrounding context, not only
line numbers. Paths are normalized before validation.

The validator rejects:

- undeclared or duplicate paths;
- absolute paths, traversal, symlink escapes, and case-collision aliases;
- missing or mismatched preimages;
- overlapping or ambiguous hunks;
- multiple moves from one source;
- a move destination that already exists without an explicit replacement policy;
- binary changes outside the allowed artifact/import path;
- file, hunk, line, or byte limits; and
- changes forbidden by workspace grants or project policy.

No model text is interpreted as a shell command or patch-parser directive.

### 4. Deterministic atomic application

Patch validation and application occur against the same C1 manifest revision. The C1
store transaction:

1. locks or compare-and-sets the overlay manifest revision;
2. rechecks every preimage;
3. applies operations in a deterministic normalized-path order to a temporary manifest;
4. verifies changed paths and result digests;
5. writes immutable content refs;
6. publishes the new overlay revision and one `WorkspaceChangeSet`; and
7. revises the C3 stage for the logical edit intent.

An error before publish discards the temporary manifest. Operation order cannot produce
a partially visible tree.

### 5. Validation planning

Validation profiles are repository-owned or product-curated configuration, never raw
model commands. Resolution precedence is:

1. tenant security policy;
2. repository-owned validated configuration;
3. recognized project metadata;
4. product defaults.

The profile may contain:

- cheap structural and parse checks;
- formatter check or formatter apply;
- generated-file consistency check;
- targeted unit tests based on changed paths;
- typecheck/lint for affected packages; and
- an optional broader test gate.

The controller runs cheap deterministic checks first. Independent checks may run in
parallel only when F6 classifies them as read-only and resource-safe. A failing cheap
check can prevent expensive checks when the later result would not change the repair
decision.

### 6. Isolated execution and formatter changes

Any validation that executes repository toolchain code uses D3 with:

- immutable snapshot of the exact merged C1 manifest;
- deny-by-default network;
- no host credentials or workspace handles;
- CPU, memory, process, output, and wall-clock quotas;
- a fixed validated command/profile; and
- artifact-backed diagnostics.

Check-only commands produce a `ValidationReport`. An allowed formatter or generator may
produce a complete `SandboxPatchManifest`. That patch is validated against the same D3
input digest, imported through C1, and becomes a new staged change-set revision. The
system validates the resulting final manifest again where the profile requires it.

### 7. Diagnostics and bounded repair

Diagnostics are normalized to:

```text
path, span?, check_id, severity, stable_error_code, bounded_message, related_refs[]
```

The model receives only diagnostics relevant to changed targets plus enough current
source context to repair them. Raw logs remain in an artifact.

Default repair budget:

- at most two model-authored repair patches;
- no more than the original changed-file and byte caps;
- no widening to unrelated paths without a new target-plan revision; and
- no automatic repair after a security, permission, sandbox-integrity, or preimage
  failure.

Each repair is another atomic C1 overlay revision. Prior attempts remain traceable but
only the current exact change set reaches review.

### 8. Fallback behavior

Fallback is typed:

- `stale_plan`: refresh the affected exact spans once and replan against a new base
  revision;
- `ambiguous_hunk`: use a full-file replacement only for bounded text files after an
  exact reread;
- `unsupported_file`: publish a safe artifact or ask the user instead of mutating;
- `validation_unavailable`: show unvalidated status and require policy-approved manual
  review, or block when validation is mandatory;
- `budget_exhausted`: retain the overlay draft and explain remaining failures;
- `scope_expansion_required`: ask for a new grant or user decision.

There is no fallback to direct host write, arbitrary shell, blind string replacement,
or approval bypass.

### 9. Conflict and rebase

There are two distinct conflicts:

- **Before review:** C1 manifest/preimage changed since planning. The controller may
  refresh exact affected bytes and create a new patch-set revision. It must rerun
  validation.
- **After approval or during commit:** the host differs from the approved preimage. C2
  reports drift and performs zero mutation. The approved stage remains historical. A
  rebase is a new C1 plan, patch, validation report, stage revision, and approval.

The model cannot represent a conflict as success. Three-way merge may be offered later
only as a proposed overlay patch with explicit conflict markers and normal review.

### 10. Review and commit

C3 shows:

- logical intent;
- expected versus actual changed paths;
- creates, deletes, moves, and mode changes;
- exact diff and content digests;
- validation profile and check outcomes;
- skipped/unavailable checks;
- repair count and remaining warnings; and
- preimage/conflict status.

Approval binds the final C1 `WorkspaceChangeSet` digest. Commit proceeds only through
A4/A5, C3, and C2. This PRD never holds or transmits a commit permit.

## Ownership and service boundaries

| Responsibility                                                    | Owner                  |
| ----------------------------------------------------------------- | ---------------------- |
| Edit-mode selection, target plan, patch validation, repair policy | AI backend             |
| Overlay bytes, manifest revisions, preimages, change set, stage   | C1 in AI backend       |
| Isolated toolchain execution and declarative output patch         | D3 adapter             |
| Approval presentation and decision flow                           | C3/shared chat surface |
| Host prepare, commit, drift detection, reconcile, undo            | C2 desktop authority   |
| Evaluation corpus, experiments, promotion evidence                | F1                     |

No deployable imports another service's source. Desktop receives only C2 contracts.
Apps use facade/shared host ports and never receive mutation credentials.

## Persistence, retention, and deletion

- Persist edit-plan metadata, patch/change-set refs, validation-report refs, state,
  reason codes, and lineage with the run.
- Patch bodies, file bodies, and validation logs use C1/A2 protected refs and inherit
  source sensitivity.
- C1 remains canonical for overlay bytes and revisions; this PRD must not duplicate
  them in a new database.
- Abandoned temporary manifests are janitor-cleaned after a short safety window.
- Run/conversation/workspace deletion cascades through edit attempts, protected patch
  refs, validation snapshots, diagnostics, and uncommitted overlay revisions subject
  to C1/E1 rules.
- Host preimages and commit journals remain C2-owned.
- Legal hold may retain records but cannot reactivate a stage, grant, or commit permit.

## Authentication, authorization, security, and audit

- Derive tenant, user, workspace grant, and virtual root from verified runtime context.
- Reauthorize each read and patch application; planning-time access is not durable
  authority.
- Patch operations can narrow only to paths already permitted by C1/grant policy.
- Project instructions, file content, diagnostics, and test output are untrusted
  content and cannot alter the harness or validation allowlist.
- D3 receives no ambient credentials, host paths, browser state, or unrestricted
  network.
- Secret-like material is blocked from inline patch/event/log fields.
- Audit plan creation, patch validation/application, validation profile/check outcome,
  repair, fallback, stage revision, approval linkage, conflict, commit, and backout.
- Audit stores digests and protected refs, not file bodies or raw tool output.

## Performance and complexity budgets

Let:

- `P` be indexed workspace paths;
- `R` be search results inspected;
- `F` be files in the patch set;
- `H` be total patch/replacement bytes;
- `V` be bytes processed by the selected validation commands.

Budgets:

- Indexed target lookup is `O(log P + R)` per query; no interaction-path full-tree
  content scan.
- Plan dedupe/order is `O(R log R)` at worst.
- Patch validation/application is `O(F log F + H)`.
- Change-set generation is `O(F + H)` over changed content, not repository size.
- Validation is toolchain-defined `O(V)` or worse where the compiler/test system
  requires it; the PRD records empirical duration rather than hiding it behind local
  Big-O.
- Target-plan assembly p95 below 200 ms after search results are available.
- Atomic patch application p95 below 500 ms for 100 text files and 1 MiB total patch
  bytes, excluding blob upload.
- Cheap structural validation begins within 250 ms of overlay publish.
- Default caps: 100 files, 2 MiB patch bytes, 20 MiB materialized changed content, two
  repair patches, four parallel read-only checks, and 10 minutes total validation.
- Fast path adds no dedicated planning-model call and one model-visible mutation call.
- Ten-or-more-file evaluation reports p50/p95 model turns, tool calls, time to first
  staged diff, time to review-ready, total validation time, prompt tokens, and cost.

These are starting budgets. F1 evidence may tighten them by task family. A lower call
count is not a win if changed-file precision or test success falls.

## Failure, idempotency, and recovery

- Patch-set admission is idempotent by tenant, run, base manifest revision, and patch
  digest.
- Reusing an idempotency key with different bytes fails.
- Atomic apply either returns the existing change-set ref or creates one revision.
- A worker crash before C1 publish leaves no visible partial patch.
- A crash after publish reconstructs state from change-set/stage refs and resumes
  validation without reapplying.
- Validation is idempotent by merged manifest digest, profile revision, toolchain
  revision, and policy; only deterministic/read-only results may be safely reused.
- Lost sandbox responses reconcile through the D3 job/result contract.
- Cancellation stops new checks, requests cancellation of active D3 work, preserves
  the staged overlay, and marks incomplete validation.
- Formatter/repair output is never trusted after source snapshot mismatch.
- C2 owns all ambiguous host-commit reconciliation; the edit controller never retries
  a host effect.

## Observability and quality gates

Metrics:

- edit mode selected and reason;
- discovery queries, paths considered/read, repeated reads, and discovery time;
- patch files/bytes/hunks and validation/application failure class;
- workspace calls and model turns before first valid change set;
- repair count, fallback reason, and paths added after initial plan;
- check queue/run time, pass/fail/timeout/unavailable, and diagnostics count;
- stage revisions before approval and user-edited/rejected rate;
- host preimage conflict and post-approval drift rate;
- task success, changed-file precision/recall, tests passed, regressions introduced,
  tokens, latency, and cost.

Trace lineage:

```text
run → task plan → target plan → patch set → C1 overlay/change set/stage
    → validation snapshot/job/report → repair patch → approval → C2 commit/reconcile
```

F1 evaluation includes:

- exact one-line edit;
- ambiguous repeated text in one file;
- coordinated API rename across at least 10 files;
- schema change plus implementation/tests/docs;
- file create/move/delete;
- generated/formatter output;
- stale overlay during planning;
- host drift after approval;
- failing targeted and broad tests;
- large repository with irrelevant similarly named files; and
- adversarial path/instruction/test-output fixtures.

Promotion requires comparison with the existing surgical path. Report bootstrap
confidence intervals, not only averages.

## Rollout and backout

1. Record baseline edit traces and build F1 fixtures.
2. Ship target planning and patch validation in shadow; do not apply.
3. Enable atomic C1 patch sets for synthetic/internal read-only validation.
4. Enable patch sets for multi-file documentation and test-only changes.
5. Add D3 format/syntax/type/test profiles.
6. Enable bounded repair for selected code task families.
7. Expand tenant cohorts after quality, security, and latency gates.

Backout disables new patch-set admission. Existing C1 overlays, stages, validation
reports, and approvals remain inspectable. The runtime returns to existing
`write_file`/`edit_file` behavior; C1–C3/D3 and committed changes remain valid.

## Implementation slices

1. Edit plan, target, patch-set, validation, and event contracts
2. Deterministic path/preimage/hunk validator
3. Atomic multi-file C1 application adapter
4. F4 surgical-versus-patch-set admission controller
5. Validation profile registry and cheap in-process checks
6. D3 snapshot/check/report integration
7. Formatter patch import and change-set revision
8. Structured diagnostics and bounded repair
9. C3 review projection and accessibility states
10. F1 evaluation suite, dashboards, flags, runbooks, and rollout

## Test plan

### Unit

- Path normalization, case collisions, and traversal rejection
- Preimage, anchor, match-count, hunk-overlap, and changed-path validation
- Stable operation ordering and patch digest
- Surgical/patch-set admission and bounded escalation
- Diagnostic normalization, repair caps, and fallback decisions

### Store and concurrency

- Atomic 1/10/100-file apply on in-memory, file, and PostgreSQL C1 adapters
- Concurrent overlay revision change causes zero partial application
- Duplicate idempotency returns one change set
- Cancellation/crash at every prepublish/postpublish boundary

### Sandbox and validation

- Exact merged manifest reaches D3
- Network, credential, path, process, output, and timeout controls
- Formatter output imports only as a complete declarative patch
- Validation-report reuse only for identical manifest/profile/toolchain
- Malicious compiler/test output remains untrusted

### Integration

- Search/read → patch set → overlay → validation → C3 review → approval → C2 commit
- Repair changes the stage digest and requires review of final bytes
- Host drift after approval produces conflict and zero mutation
- Web path offers artifact/download behavior without local host mutation
- Desktop renderer cannot reach patch application or commit credentials directly

### Quality and performance

- Ten-or-more-file refactors across representative languages and repository sizes
- Changed-file precision/recall and functional/task success
- Model/tool turns, repeated reads, prompt bytes, time to review-ready, and cost
- No regression for exact one-file edits

## Definition of done

- Multi-file edits can be proposed in one bounded model-visible mutation call.
- Every changed/deleted/moved file is bound to an exact merged-view preimage.
- Patch application is atomic and creates one C1 `WorkspaceChangeSet` per logical
  revision.
- The exact final tree is validated and the report is bound to the reviewed digest.
- Repair and formatter changes create new overlay/stage revisions and cannot inherit an
  old approval.
- Conflict, fallback, cancellation, crash, deletion, and backout paths are tested.
- No code path writes host files outside C2.
- F1 gates demonstrate lower turns/latency without quality or safety regression on
  ten-or-more-file refactors.

## Guardrails

- Optimize model round trips, not authority checks or review detail.
- Never make patch text executable.
- Never accept line numbers alone as a preimage.
- Never partially apply a rejected patch set.
- Never treat formatter/test success as authorization to commit.
- Never run repository code outside a configured D3 validation profile.
- Never auto-rebase after approval or host drift.
- Never hide created, deleted, moved, generated, or formatter-changed files from review.
- Never replace C1 storage, C3 staging, or C2 commit with a convenience path.

## Open decisions

1. Which structured hunk representation offers the best provider reliability while
   retaining deterministic exact-match semantics?
2. Which languages and validation profiles are required for the first cohort?
3. Should full-file replacement have a lower byte cap than hunk-based patching?
4. Which repository indexes are guaranteed available versus built lazily?
5. When may a failed targeted test trigger a broader test, given the remaining budget?
6. Which task families should remain surgical even when several files are nearby?
