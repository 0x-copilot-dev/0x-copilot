# PRD-AR-H4 — Skill usage, staleness, and curation

**Goal.** Keep the published skill catalog useful as it grows by recording
privacy-safe activation/outcome telemetry and producing recoverable, reviewed
recommendations to pin, patch, supersede, consolidate, or archive skills. The
system must never silently rewrite or delete a user's procedures.

## Metadata

| Field        | Value                                                                                                                                    |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Status       | Proposed                                                                                                                                 |
| Priority     | P2                                                                                                                                       |
| Owners       | `services/backend` (lifecycle/source of truth), `services/ai-backend` (runtime observations), facade and shared chat surface (review UX) |
| Depends on   | AR-H2, AR-H3, AR-F1; Generative Surfaces A2, A3, E1                                                                                      |
| Rollout flag | `SKILL_CURATION_ENABLED`, desktop user opt-in                                                                                            |
| UI impact    | Skills inventory, review queue, version diff, archive/restore                                                                            |

## Implementer brief

Read before implementation:

1. `services/backend/src/backend_app/service.py` (`SkillRegistryService`).
2. `services/backend/src/backend_app/contracts.py` skill records/cards.
3. `services/backend/src/backend_app/audit_reader.py`.
4. `services/ai-backend/src/agent_runtime/capabilities/skills/`.
5. `services/ai-backend/src/agent_runtime/execution/factory.py`.
6. `packages/chat-surface` skill destinations and host adapters.
7. `../README.md`, AR-H2, AR-H3, and `../../prds/PRD-E1-accountability-lifecycle.md`.

## Problem statement

On-demand loading controls prompt size but does not control catalog quality.
Over time, similar skills accumulate, procedures become stale after tool/API
changes, support files break, and low-value cards reduce retrieval precision.
Raw “last used” data is insufficient: an old emergency procedure can remain
critical, while a frequently activated skill can still reduce task success.

## Current implementation and predecessor contracts

- **[shipped]** Backend owns durable, scoped, versioned skill records and audit events.
- **[shipped]** ai-backend exposes compact cards and loads full content only when selected.
- **[shipped]** Skill manifests include compatibility and intended-tool metadata.
- **[shipped]** Runtime operations, citations, artifacts, and usage records can support
  outcome attribution.
- **[depends on]** Published content remains governed by AR-H2; this PRD must not create a
  second mutation path.

## Objectives and measurable outcomes

1. Attribute view, activation, completion, correction, and failure signals to
   an exact immutable skill revision.
2. Detect stale, broken, duplicated, or harmful skills without reading unrelated
   conversations or project content.
3. Produce reviewable lifecycle proposals; require the normal AR-H2 authority
   to publish content changes.
4. Make archive reversible and preserve historical run replay.
5. Improve skill-selection success while reducing irrelevant full-skill loads.

Launch gates:

- zero automatic content mutation or hard deletion;
- 100% of observations reference local account, skill ID, revision ID, and content
  digest;
- archive/restore replay preserves historical revision resolution;
- no material task-success regression in AR-F1 suites;
- every curation proposal exposes evidence and a deterministic reason code.

## Non-goals

- Importing external packages (AR-H1).
- Publishing or rolling back revisions (AR-H2).
- Online ranking and task profiles (AR-H3).
- Learning candidates from chats (AR-H5).
- Training a model on personal trajectories.

## Interfaces consumed

- AR-H2 immutable skill revisions and review decisions.
- AR-H3 activation records and ranked candidate sets.
- AR-F1 task outcome and experiment attribution.
- E1 local audit, retention, deletion, and export contracts.

## Interfaces exposed

```text
POST /internal/v1/skill-usage-observations:batch
```

The endpoint is backend-owned and accepts only the per-install service token plus
verified local-account headers over loopback. The body carries observations, not
identity authority, and is bounded by count and bytes.

```text
SkillUsageObservation
  observation_id
  local_account_id
  skill_id
  skill_revision_id
  skill_content_digest
  run_id
  operation_id?
  signal: card_shown | viewed | activated | completed | failed |
          user_corrected | user_disabled
  task_family
  timestamp
  safe_metrics

SkillLifecycleProposal
  proposal_id
  local_account_id
  skill_id
  expected_active_revision_id
  expected_content_digest
  expected_skill_row_version
  action: pin | unpin | patch | supersede | consolidate | archive | restore
  reason_codes[]
  evidence_refs[]
  proposed_patch_ref?
  candidate_skill_revisions[]         # skill_id, revision_id, content_digest
  state: pending | accepted | rejected | expired | withdrawn
  created_by
  expected_policy_revision
  idempotency_key
  expires_at?
```

Content, tool arguments, and user messages are never embedded in usage rows.
Evidence references point to ACL-protected records.

`local_account_id`, `skill_revision_id`, and content digest use the canonical H2/H3
identity model. Per-person attribution is unnecessary in the desktop-first product:
the observation belongs to the active local account and contains no per-person
analytics dimension.

## Detailed design

### 1. Observation collection

ai-backend emits observations at existing runtime seams:

- card considered/shown after AR-H3 ranking;
- full bundle loaded;
- task completed/failed;
- user correction or skill disablement explicitly linked to the run;
- validation failure or missing support file.

`ai-backend` appends observations to its own local transactional outbox with the runtime
event, then an authenticated dispatcher posts bounded batches to backend
`POST /internal/v1/skill-usage-observations:batch`. Backend revalidates
account/revision
attribution and persists idempotently by `observation_id` before acknowledging.
There is no shared cross-service outbox table, database connection, sibling-service
import, or direct backend-table write. On desktop the source outbox uses ai-backend's
default file-native store under `<userData>/agent-data/v1`; the destination reuses the
embedded backend product database. Backend stores aggregates and the bounded raw
observation window required by the user's retention setting.

### 2. Deterministic lifecycle analyzer

The first release uses deterministic rules only:

- `broken_reference`: referenced file/revision cannot resolve;
- `compatibility_mismatch`: required tool/platform revision is unavailable;
- `unused`: no activation within the user-configured window;
- `low_yield`: sufficient sample size and completion delta below threshold;
- `high_correction`: correction rate above threshold;
- `duplicate_candidate`: high card/body similarity plus overlapping triggers;
- `superseded_dependency`: depended-on capability/version retired.

An unused signal may propose archive but cannot apply it. Minimum sample sizes
prevent small-N quality judgments.

### 3. Optional assisted consolidation

A bounded auxiliary model may draft a consolidation only after deterministic
candidate selection. It receives:

- published revisions of the candidate skills;
- referenced support files within those packages;
- redacted aggregate outcome summaries;
- explicit target format and maximum output size.

It cannot invoke tools or publish. Output is an AR-H2 draft with a unified diff,
source mapping, copied-support-file manifest, and validation results. The
reviewer sees what would be lost or renamed.

### 4. Pin, archive, restore, and supersede

- Pin prevents automated archive recommendations and tool-driven deletion; it
  does not freeze owner-authorized edits.
- Archive invokes H2's canonical `archive` transition, which removes the skill/card
  from future H3 discovery while retaining packages, audit, and historical resolution.
- Restore invokes H2's canonical `restore` transition for the same immutable revision;
  required content/policy changes instead create an H2 draft.
- Supersede invokes H2's typed replacement-pointer transition; runtime and UI can
  explain redirects without rewriting old run records.
- Hard deletion follows the user's retention and explicit local-data deletion choices.

H4 stores proposals and decisions, not archive markers, pin flags, active pointers, or
replacement pointers. Accepted lifecycle commands are sent to backend's H2 command
service with the proposal's expected active revision, digest, skill row version, policy
revision, and idempotency key. Any drift returns conflict and requires a refreshed
proposal/review.

### 5. Review workflow

The Skills review queue shows action, reason, affected revisions, usage window,
quality metrics with sample size, support-file changes, and exact diff.
Accepted content changes create an AR-H2 draft. Accepted archive/pin/restore/supersede
operations use H2's backend-owned lifecycle commands and exact compare-and-set contract.
Rejection records an optional reason used to tune deterministic thresholds, never to
train automatically.

## Persistence, retention, deletion, and future sync

Backend owns observations, aggregates, proposals, and decisions in the existing
embedded local product database. H2 owns canonical archive markers, pins, lifecycle
state, and replacement pointers. Raw observations use a bounded user-configurable
retention; aggregates may outlive raw rows only when they cannot reconstruct task
content. Conversation/run deletion removes or tombstones evidence references.

“Delete local data” removes observations, aggregates, proposals, decisions, and H2
lifecycle state after a clear confirmation and optional export. A future consumer sync
adapter may replicate immutable proposal/decision events, but the analyzer operates
locally, remains usable offline, and never depends on a cloud scheduler or analytics
pipeline.

## Authorization, privacy, and audit

- The signed-in local account may view usage for its skills.
- Personal/project skill lifecycle changes require the same explicit H2 review.
- IDs belonging to another account on the device return not-found semantics.
- Review evidence is reauthorized at read time.
- Audit records who proposed, reviewed, accepted, restored, or rejected an
  action, with before/after revision/digest and policy revision.
- Do not create behavioral advertising or cross-user adoption analytics.

## Performance and capacity

- Observation append: amortized O(1), p95 under 20 ms excluding outbox lag.
- Aggregate updates: asynchronous and idempotent.
- Deterministic scan: O(number of eligible revisions + referenced files);
  partition by local account and bound work per job.
- Similarity candidate generation must use indexed retrieval, not O(N²) full
  pair comparison for large catalogs.
- Curation must not add latency to the active agent turn.

## Failure, retry, and recovery

- Failed observation delivery retries through outbox; active runs continue.
- Analyzer jobs are lease-based and idempotent by `(local_account_id, policy_revision,
analysis_window)`.
- A failed assisted draft leaves no partial revision.
- Archive races with activation resolve against the revision snapshot captured
  at run assembly; new runs no longer discover the archived card.
- Backups/immutable revisions make restore deterministic.

## Observability

Track observation lag, analyzer duration, proposals by reason, acceptance and
restore rates, broken-reference count, catalog size, ranked-card precision,
full-load rate, and task-success delta. Alert on cross-account gate failures,
unexpected auto-mutation attempts, stuck proposals, and archive/restore
resolution errors. Metrics remain on-device by default; any product telemetry is
separately consented, redacted, and aggregate-only.

## Rollout and backout

1. Emit observations in shadow and validate account/revision/digest attribution.
2. Enable deterministic diagnostics without proposals.
3. Enable propose-only lifecycle review for opt-in desktop beta users.
4. Enable archive/pin/restore decisions.
5. Enable assisted consolidation only when the user opts in.

Backout disables new analysis/proposals. Existing decisions and archives remain
readable and reversible. No published version is automatically restored or
deleted during rollback.

## Implementation slices

1. Contracts, schema, outbox consumer, and retention.
2. Runtime observation emitters and revision/digest attribution.
3. Aggregate/analyzer service and reason codes.
4. Proposal/review APIs through facade.
5. Shared Skills review UI.
6. Archive/pin/restore/supersede commands.
7. Optional consolidation draft adapter.
8. AR-F1 evaluations and rollout dashboards.

## Test plan

- Local-account ownership isolation for observations/proposals/evidence.
- Service-token/header spoofing, bounded batch limits, ai-backend local-outbox retry,
  and an architecture test forbidding shared databases/cross-service source imports.
- Duplicate event and analyzer retry idempotency.
- Canonical account/revision/digest attribution, local export/deletion, and aggregate
  non-reconstructability.
- Minimum-sample and threshold boundary tests.
- Broken/escaping support-file references.
- Concurrent activation/archive/restore behavior.
- Stale active revision, digest, skill row version, or policy revision rejects the H2
  lifecycle command; duplicate idempotency replays exactly once.
- Consolidation preserves referenced files and rewrites internal paths.
- No model/tool execution in deterministic mode.
- No publish from assisted mode.
- Retention, deletion cascade, export, backup/restore, and offline tests.
- Replay of a historical run after skill archive/supersede.

## Definition of done

- [ ] Exact-revision observations are durable and privacy-safe.
- [ ] Deterministic lifecycle reasons are explainable and tested.
- [ ] Every mutation is an authorized reviewed action through AR-H2/backend.
- [ ] Archive is recoverable and historical replay remains valid.
- [ ] Assisted consolidation is opt-in, bounded, and draft-only.
- [ ] AR-F1 quality and safety gates pass.
- [ ] Shared program DoD in `../README.md` passes.

## Guardrails

- Never auto-publish, auto-patch, or hard-delete.
- Never use “unused” as proof that a skill is unimportant.
- Never let curation bypass scope ownership or release authority.
- Never flatten a package while dropping referenced support files.
- Never use skill telemetry for behavioral advertising or cross-user profiling.

## Open decisions

1. Default raw-observation retention for desktop installs.
2. Whether project-level pin/archive controls ship in the first release.
3. Minimum sample sizes for low-yield/high-correction recommendations.
