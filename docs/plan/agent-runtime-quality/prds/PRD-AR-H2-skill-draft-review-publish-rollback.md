# PRD-AR-H2 — Skill draft, review, publish, and rollback

**Goal.** Replace mutable, immediately active skill edits with immutable revisions and
an explicit draft→review→publish workflow, so a user can inspect exact content/tool
permissions, publish atomically, and roll back without losing history.

| Field           | Value                                                                                                                                      |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Status          | Draft for review                                                                                                                           |
| Primary owner   | `backend` skill registry                                                                                                                   |
| UI owners       | `packages/chat-surface`, web and desktop host binders                                                                                      |
| Runtime rollout | Runtime reads only the active published revision                                                                                           |
| Depends on      | A3 Operation Gateway, B2 fixed renderers/editors, E1 accountability/lifecycle; AR-H1 only for imported or `agent_generated` package drafts |
| Blocks          | AR-H3 skill discovery/ranking and later skill distillation                                                                                 |

## Implementer brief

Read before implementation:

1. `../README.md`.
2. `PRD-AR-H1-external-skill-package-ingestion-quarantine.md`.
3. `../../prds/PRD-A3-operation-gateway.md`.
4. `../../prds/PRD-B2-artifact-renderers-editors.md`.
5. `../../prds/PRD-E1-accountability-lifecycle.md`.
6. `services/backend/src/backend_app/contracts.py` skill contracts.
7. `services/backend/src/backend_app/service.py` (`SkillRegistryService`).
8. `services/backend/src/backend_app/store.py` skill adapters/audit.
9. `services/backend/src/backend_app/app.py` skill routes.
10. `services/backend-facade/src/backend_facade/app.py` skill routes.
11. `services/backend/migrations/0001_backend_baseline.sql` (`skills` and
    `skill_audit_events`).
12. `services/ai-backend/src/agent_runtime/capabilities/skills/virtual.py`.
13. `services/ai-backend/src/agent_runtime/capabilities/skills/manifest.py`.
14. `services/ai-backend/src/agent_runtime/capabilities/skills/policy.py`.
15. `packages/api-types/src/skills.ts`.
16. `packages/chat-surface/src/destinations/skills/` if present at implementation time;
    otherwise follow the destination SSOT rules in the package instructions.

Preserve existing skill ids and active behavior through migration. Do not make runtime
availability depend on a UI session.

## Problem statement

The current skill row stores one mutable markdown body. Creation can default to enabled;
editing replaces the body and increments an integer version; no immutable revision body,
review decision, active pointer, rollback command, or draft state exists. Audit records
the action/version but cannot reconstruct the exact reviewed content.

This is inadequate for imported or agent-proposed workflows. A skill can influence
model behavior and request tools repeatedly, so publication must bind exact
instructions, assets, manifest, allowed tools, compatibility, provenance, and scope.
Rollback must be atomic and evidence-preserving.

## Current implementation and predecessor contracts

- **[shipped]** Stable skill ids/names, account scope, preloaded/user source types,
  enable/disable, local audit events, and internal card/bundle routes already exist.
- **[shipped]** Runtime loads compact cards then full markdown on demand.
- **[shipped]** Manifest parsing and allowed-tool subset checks already exist.
- **[shipped]** The packaged desktop supervises the facade/backend locally, stamps
  verified local-account identity, and persists current skills in its embedded
  PostgreSQL product database.
- **[depends on]** H1 provides immutable package bytes and scan reports for
  package-backed imports/generated drafts.
- **[depends on]** Fixed artifact/rendering and E1 audit/lifecycle patterns are
  predecessor contracts.

## Objectives

1. Store every authored/imported change as an immutable revision.
2. Keep drafts invisible to runtime until an explicit authorized publish.
3. Review exact markdown, asset tree, manifest, tool delta, provenance, and findings.
4. Publish with compare-and-set semantics and atomically update the active pointer.
5. Roll back by publishing a prior immutable revision as the active revision.
6. Pin each run to the revision it loaded for deterministic behavior and attribution.

### Success measures

- Zero draft/unreviewed revision returned by runtime card/bundle endpoints.
- 100% publish/rollback audit linkage to exact revision and content digest.
- Same run sees one pinned skill revision even if a later publish occurs.
- Publish/rollback p95 below 500 ms excluding package scan.
- Migration preserves all existing skill bodies and enabled states with no behavior
  change when the rollout flag is off.

## Non-goals

- External package scanning (H1), skill ranking (H3), or automatic skill learning.
- Executing a skill during review.
- Multi-reviewer governance.
- Editing preloaded/system skill bodies; they remain release-managed.
- Mutable rewrite or deletion of published revision history.

## Interfaces consumed

- Existing skill identity/scope/manifest/audit contracts.
- H1 `ready_for_review` package and findings, conditionally required only for imported
  or `agent_generated` package-backed drafts.
- Existing local-account session and facade identity stamping.
- Runtime private card/bundle HTTP seam.
- E1 local retention, export, deletion, audit, and repair requirements.

## Interfaces exposed

### Persistence model

```text
skills
  skill_id, local_account_id, stable_name, display_name
  scope, source_type
  lifecycle_state: draft_only | active | disabled | archived | review_required |
                   deleted
  active_revision_id?, disabled_at?, created_at, updated_at
  curation_pinned_at?, superseded_by_skill_id?
  row_version

skill_revisions
  revision_id, skill_id, revision_number
  parent_revision_id?, source_package_id?
  markdown_ref, asset_manifest_ref?
  content_digest, manifest_digest, asset_tree_digest?
  description, allowed_tools[], compatibility[], metadata
  evidence_scope_ceiling?
  provenance_ref?, scanner_report_id?
  author_kind: user | agent | import | system_migration
  author_user_id?, author_run_id?
  created_at

skill_drafts
  draft_id, skill_id, base_revision_id?
  candidate_revision_id
  status: editing | ready_for_review | changes_requested | approved | published | abandoned
  draft_version, created_by, created_at, updated_at

skill_review_decisions
  decision_id, draft_id, candidate_revision_id
  decision: approve | request_changes | reject
  actor_user_id, policy_snapshot_ref, comment_ref?
  created_at

skill_publication_events
  publication_id, skill_id, from_revision_id?, to_revision_id
  action: initial_publish | publish | rollback | disable | enable |
          archive | restore | pin | unpin | mark_review_required |
          clear_review_required | supersede
  actor_user_id, idempotency_key, created_at
```

Published revisions are immutable. Draft editing creates new candidate revisions or
append-only draft snapshots; it never modifies a published row.

This is the canonical skill lifecycle state machine. H3 reads it; H4 may propose and
invoke these commands after review but may not create parallel archive/pin/supersession
state. Every lifecycle mutation compares the expected `active_revision_id`,
`content_digest`, `row_version`, and policy revision and records one publication event.

### Public APIs

```text
POST /v1/skills/drafts
POST /v1/skills/{skill_id}/drafts
GET  /v1/skills/{skill_id}/drafts/{draft_id}
PUT  /v1/skills/{skill_id}/drafts/{draft_id}
POST /v1/skills/{skill_id}/drafts/{draft_id}/submit
POST /v1/skills/{skill_id}/drafts/{draft_id}/decisions
POST /v1/skills/{skill_id}/drafts/{draft_id}/publish
GET  /v1/skills/{skill_id}/revisions
GET  /v1/skills/{skill_id}/revisions/{revision_id}
POST /v1/skills/{skill_id}/rollback
POST /v1/skills/{skill_id}/disable
POST /v1/skills/{skill_id}/enable
POST /v1/skills/{skill_id}/lifecycle-actions
```

Every mutation accepts `idempotency_key`; draft edit also requires `If-Match`/expected
draft version. Publish requires expected active revision and exact approved candidate
revision/digests.

```text
SkillDraftCreateRequest
  skill_id?: string
  base_revision_id?: string
  package_import_id?: string
  markdown?: string
  display_name?: string
  scope?: personal | project
  idempotency_key: string

SkillPublishRequest
  draft_id: string
  candidate_revision_id: string
  expected_active_revision_id?: string
  expected_content_digest: sha256
  expected_manifest_digest: sha256
  decision_id: string
  idempotency_key: string

SkillRollbackRequest
  target_revision_id: string
  expected_active_revision_id: string
  reason?: string
  idempotency_key: string

SkillLifecycleActionRequest
  action: archive | restore | pin | unpin | mark_review_required |
          clear_review_required | supersede
  expected_active_revision_id: string
  expected_content_digest: sha256
  expected_skill_row_version: int
  expected_policy_revision: string
  replacement_skill_id?: string
  reviewed_proposal_id?: string
  idempotency_key: string
```

### Runtime private contracts

Internal cards and bundles add:

```text
active_revision_id
content_digest
catalog_generation
```

Runs pin `skill_id@active_revision_id` when assembling capabilities. A bundle load by
name must include or resolve the pinned revision, not whatever becomes active later.

### Events

```text
skill.draft.created.v1
skill.draft.submitted.v1
skill.review.decided.v1
skill.revision.published.v1
skill.revision.rolled_back.v1
skill.disabled.v1
skill.enabled.v1
```

Events/audit carry ids, versions, digests, scope, decision, and safe finding counts.
Markdown, comments, asset bodies, and secret findings stay behind refs.

## Design

### D1. Authoring entry points

Drafts can originate from:

- a user-authored `SKILL.md`;
- an H1 `ready_for_review` package;
- an agent proposal tied to a completed run;
- migration of an existing active skill.

H1 is not a blanket dependency for manual user-authored text or legacy migration.
Imported bytes and H8-generated packages must reference an H1
`ready_for_review` report for the exact package digest/scanner policy; they cannot be
converted to inline/manual drafts to bypass quarantine.

Agent-originated operations may call `create_skill_draft` through A3 as an internal
reversible operation. They cannot approve, publish, enable, widen scope, or add tools
outside the current policy. The tool result states “draft created; not active.”

### D2. Draft editing and validation

Every edit:

1. requires expected draft version;
2. parses the manifest and validates asset closure;
3. computes canonical content/manifest/asset-tree digests;
4. derives the requested tool set and compatibility;
5. creates an immutable candidate revision;
6. advances the draft pointer transactionally.

Editing after an approval invalidates that approval and returns the draft to editing.
The stable skill name cannot change after the skill identity exists. A name collision
at initial creation is a conflict.

### D3. Review projection

The review surface shows:

- source/provenance and package scan status;
- rendered markdown as untrusted text;
- exact diff against base/active revision;
- added/removed/unchanged allowed tools;
- scope and compatibility change;
- asset file tree, media types, sizes, and digests;
- blocking/warning findings;
- author/run identity and timestamps.

The renderer never executes examples, scripts, HTML, remote assets, or links
automatically. Review comments are separately stored and are not appended to skill
instructions.

### D4. Authorization and decision policy

- Personal draft: the signed-in owner may review and publish after explicit confirmation.
- Project draft: the owner may publish it into a local project after an explicit scope
  preview; this does not share it with another account or device.
- Imported/agent-generated drafts always require a deliberate review action even on a
  single-user machine; self-review is a UX safety gate, not multi-reviewer separation of
  duties.
- Added tools are checked against the local capability catalog, current user grants,
  H3 task-profile policy, and runtime compatibility.
- Imported/generated evidence scope may be narrowed but cannot be widened beyond the H1
  package's immutable `scope_ceiling`; broader publication requires a new authorized
  evidence basis, new H1 package/report, and fresh review.
- Approval authorizes exact candidate revision/digests only; any change invalidates it.

### D5. Atomic publication

Within one store transaction:

1. lock/compare skill row version and expected active revision;
2. load exact candidate revision and valid decision;
3. revalidate package revocation, findings, scope, and allowed tools;
4. append publication record and immutable audit;
5. set `active_revision_id`, state, display metadata, and catalog generation;
6. mark draft published;
7. enqueue cache/index invalidation.

If any check fails, none of the state changes. Runtime readers see old or new active
revision, never a partial mix.

### D6. Rollback

Rollback is a publication action targeting a prior immutable revision. It requires the
same authorization and exact expected-active compare-and-set as publish. It does not
delete or mutate the bad revision. The publication chain records from/to and reason ref.

An H1-revoked source may not be selected for rollback by default. A developer-mode
exception must be local, explicit, time-bounded, and visibly disable effectful tools.

### D7. Enable/disable

Disable changes runtime availability, not revision history. Enable restores the existing
active revision after policy/revocation revalidation. A skill with no published
revision cannot be enabled.

Archive, restore, pin/unpin, review-required, and supersede use this same command
service and state row:

- `archived` excludes the skill from new H3 selections while retaining exact revision
  replay; restore returns to `active` only after policy/revocation revalidation;
- pin is lifecycle metadata that suppresses automated archive proposals but does not
  grant authority or freeze owner edits;
- `review_required` excludes automatic H3 selection and load until an authorized
  reviewer clears it against an exact active revision;
- supersede sets a typed replacement pointer without changing old revision history.

Every command requires an idempotency key and exact expected active revision, content
digest, row version, and policy revision. Stale commands return conflict.
Trusted backend deletion/revocation consumers may automatically apply
`mark_review_required` as a safety-only narrowing action using the exact source event
and compare-and-set fields. Only an authorized human review may clear it; no automated
path may publish, restore, enable, or clear the state.

### D8. Runtime pinning and cache invalidation

Run creation snapshots visible cards and their active revision ids. `load_skill` asks
for the pinned revision. Publication advances `catalog_generation` and invalidates
process caches for future runs; active runs remain deterministic.

If a skill is emergency-disabled while a run is active, effectful tool calls must
recheck applicable policy. The loaded instructions remain in history, but no new
authority derives from them.

### D9. Compatibility migration

For every existing skill row:

- create revision 1 from current markdown/manifest fields;
- set `active_revision_id` to revision 1;
- preserve skill id/name/scope/source/enabled/version timestamps;
- label author `system_migration`;
- preserve existing public response projection.

Current create/update endpoints remain compatibility adapters during rollout:

- create always makes a draft and never receives a synthetic publication decision;
- update makes a draft; a synthetic decision is permitted only for an explicitly
  allowlisted, dated migration of pre-existing manually authored user-scoped rows;
- synthetic decisions are forbidden for H1 imports, `agent_generated` or H8 drafts,
  project-scoped publication, newly created post-migration content, and any change that
  widens scope or allowed tools;
- each permitted synthetic decision records migration batch, original row digest,
  actor `system_migration`, policy snapshot, and sunset date; the route is removed at
  final cutover.

No mode may update the old body and revision tables independently.

## Persistence, retention, deletion, backup, and future sync

- Published revisions, publication records, and review decisions are append-only.
- Draft snapshots may be compacted after abandonment only when no decision, audit, or
  active revision references them; final candidate digest/metadata remains.
- Deleting the local account removes private drafts and inactive personal/project
  skills after a user-visible export/delete confirmation.
- Skill delete becomes soft-delete/tombstone plus runtime disable. Physical collection
  waits for the chosen undo window and ref-graph, audit, package, and run-snapshot
  checks.
- Run receipts retain skill/revision/digest attribution but not full markdown.
- Package/asset refs are reference-counted across revisions and collected only when
  unreachable.
- Canonical lifecycle rows remain in the existing embedded PostgreSQL backend database;
  immutable markdown/assets are content-addressed filesystem blobs below OS app data.
  This reuses the shipped desktop backup/migration boundary rather than introducing a
  second SQLite database solely for skills.
- A future consumer sync adapter may replicate immutable revisions and publication
  events through an outbox. Local authoring/publishing must remain fully usable offline,
  and sync conflict resolution creates a new draft instead of rewriting history.

## Authorization, privacy, supply chain, and security

- Verified local-session identity determines the account; renderer payload scope cannot
  broaden it.
- Every read/mutation returns 404 for ids owned by another local account.
- Publication rechecks H1 scan/revocation and tool availability.
- Markdown/assets are untrusted and never rendered as executable HTML/code.
- No skill can grant tools; `allowed_tools` only narrows a separately authorized
  runtime capability set.
- Bodies, comments, paths, source URLs, and finding details remain out of logs/events.
- Audit records who drafted, reviewed, published, rolled back, disabled, and enabled,
  with exact digests and policy snapshot.

## Performance and capacity

- Draft markdown max 1 MiB; package/assets retain H1 limits.
- Edit validation p95 below 300 ms for a normal text-only skill.
- Publish/rollback transaction p95 below 500 ms.
- Revision list is keyset-paginated, default 25/max 100.
- Diffs above 1 MiB total input use a bounded local-service summary plus downloadable exact
  revisions; no unbounded JSON/SSE.
- Runtime card listing remains compact and index-backed; no revision-body join.

## Failure, idempotency, and recovery

- Same idempotency key/same request digest returns the same command result; different
  digest conflicts.
- Optimistic edit or expected-active mismatch returns 409 with current ids, no merge.
- Crash before transaction commit has no publication; after commit, durable outbox
  retries invalidation/events.
- Cache invalidation failure does not roll back truth; readers validate generation.
- Package revocation or policy drift between approval and publish blocks publish and
  requires a fresh decision.
- Retry/replay never creates duplicate revisions/publication records.
- Repair job detects active pointers without valid revisions, incomplete outbox, and
  projection drift; it never fabricates approval.
- Repair never fabricates lifecycle decisions or synthetic publication outside the
  constrained legacy migration above.

## Metrics

- `skill_draft_commands_total{command,outcome}`
- `skill_review_decisions_total{decision,scope}`
- `skill_publications_total{action,scope,outcome}`
- `skill_publish_duration_ms{action}`
- `skill_publish_conflicts_total{reason}`
- `skill_active_cache_generation_lag`
- `skill_repair_findings_total{class}`
- `skill_draft_age_seconds{status}`

No skill/user/name/body/id appears in metric labels.

## Rollout and backout

1. Land revision/draft stores and migrate existing skills dark.
2. Dual-read compare old row projection with active-revision projection.
3. Enable new draft/review APIs and UI while runtime still reads legacy projection.
4. Switch runtime internal cards/bundles to active revision ids.
5. Require explicit review/publish for imported/agent drafts.
6. Remove legacy auto-publish after cohort migration and drain.

Backout returns runtime reads to the legacy projection only while dual-write
compatibility is supported. New revisions/drafts remain intact. After final retirement,
backout means choosing a prior active revision or disabling the feature—not rewriting
history.

## Implementation slices

1. Define contracts, migrations, stores, golden fixtures, and reference graph.
2. Migrate current rows into revision 1 and add dual-read verification.
3. Implement draft edit/submit/decision services.
4. Implement atomic publish/rollback/enable/disable and outbox.
5. Add routes/facade/types and shared review UI.
6. Pin runtime cards/bundles per run and add generation invalidation.
7. Add lifecycle, local export, repair, metrics, rollout, and legacy retirement.

## Test plan

### State and contracts

- Every valid/invalid state transition; immutable revisions; manifest/tool/asset diffs;
  name collision; optimistic draft conflicts.
- Golden Pydantic/JSON/TypeScript fixtures and facade route behavior.

### Authorization and supply chain

- Local-account ownership, renderer identity forgery, second-account ids, explicit
  self-review, scope widening, tool widening, and package revocation after approval.
- Untrusted markdown/assets cannot execute or change policy.

### Publication/recovery

- Exact digest/decision binding, expected-active drift, duplicate commands, crash before
  and after commit, outbox retry, cache-generation lag, repair.
- Publish vs publish, publish vs rollback, disable vs load concurrency.
- Imported/generated/project-scoped/new content cannot use synthetic publication; only a
  dated allowlisted legacy manual-user migration fixture can.

### Runtime and lifecycle

- Draft never listed/loaded; published revision pinned within a run; future run sees new
  revision; disable behavior; rollback.
- H4 lifecycle commands fail on stale revision/digest/row-version/policy; H3 excludes
  disabled, archived, review-required, and deleted rows.
- Migration preserves behavior; local-account deletion, undo-window expiry,
  asset/package reference collection, backup/restore, and receipt attribution.

### UI/accessibility

- Exact diff/tool/scope/provenance/findings, keyboard/screen-reader review, conflict,
  stale approval, rollback, disabled states, raw fallback, web/desktop smoke.

## Definition of done

- [ ] Every skill change is an immutable candidate revision.
- [ ] Drafts cannot enter runtime cards/bundles before exact authorized publication.
- [ ] Publish and rollback are atomic, idempotent, audited, and digest-bound.
- [ ] Runs pin the active revision they started with.
- [ ] Existing skills migrate without behavior or identity loss.
- [ ] Deletion, export/restore, repair, concurrency, authorization, and UI launch suites
      pass.
- [ ] Legacy mutable/auto-active paths are retired for enabled cohorts.

## Guardrails

- No mutable rewrite of a published revision.
- No draft, scan completion, or agent output may self-publish.
- No approval survives candidate content/tool/scope change.
- No skill grants authority; allowed tools only narrow.
- No rollback by deleting history.

## Open decisions

- Default undo window for deleted skills and abandoned draft bodies.
- Whether project-scoped skills participate in future account sync at initial launch.
