# PRD-AR-H6 — Durable memory store, review, and index

**Goal.** Complete the product memory domain with a desktop-first adapter for the
already bundled local PostgreSQL database, durable proposal decisions, local search
indexing, retention/deletion, and a reviewable user experience. The local backend
remains the only source of truth for learned facts, preferences, user capabilities,
and project conventions.

## Metadata

| Field        | Value                                                                                                  |
| ------------ | ------------------------------------------------------------------------------------------------------ |
| Status       | Proposed; desktop durability completion                                                                |
| Priority     | P0                                                                                                     |
| Owners       | `services/backend`, facade, `packages/api-types`, `packages/chat-surface`; ai-backend is a client only |
| Depends on   | Generative Surfaces A2 and E1; AR-H5 for automatic proposals                                           |
| Rollout flag | `DURABLE_MEMORY_STORE_ENABLED`                                                                         |
| UI impact    | Memory inventory, proposal review, inspect/edit/delete/export                                          |

## Implementer brief

Read:

1. `services/backend/src/backend_app/memory/`.
2. `services/backend/src/backend_app/memory/schema.sql`.
3. `services/backend/src/backend_app/memory/store.py`.
4. `services/backend/src/backend_app/memory/service.py`.
5. `services/backend/src/backend_app/memory/search.py`.
6. `services/backend/src/backend_app/memory/indexer.py`.
7. `services/backend/src/backend_app/app.py` memory composition.
8. `services/backend-facade/src/backend_facade/memory_routes.py`.
9. `packages/api-types/src/memory.ts`.
10. Shared Memory UI under `packages/chat-surface`.
11. AR-H5, AR-H7, and `../../prds/PRD-E1-accountability-lifecycle.md`.

## Problem statement

The memory API, schema, service, proposal state machine, audit, SSE, and UI contracts
are extensive, but the packaged desktop composition still leaves
`InMemoryMemoryStore` as an accepted gap. Search is an in-memory lexical implementation
with a no-op vector path, so a local service restart loses memory.

The desktop already bundles and supervises PostgreSQL for backend-owned product records
such as identity, OAuth, token-vault metadata, projects, connectors, and skills. H6
should add the missing memory adapter to that same local database rather than introduce
a second canonical SQLite/file ledger. This does not change ai-backend's shipped
desktop default: runs, events, queues, artifacts, and checkpoints remain in
`FileRuntimeApiStore` below `<userData>/agent-data/v1`, with content-addressed objects
and disposable SQLite indexes.

## Current implementation and predecessor contracts

- **[shipped]** Typed kinds `fact | preference | skill`; `skill` means a user
  capability such as “can program in Python,” not a reusable procedure.
- **[shipped]** Scopes `user | workspace`, with optional `project_id` narrowing a
  workspace item; for the B2C product these project existing wire values as
  `personal | project` without inventing a third persisted enum during migration.
- **[shipped]** Owner/project ACL logic and not-found behavior already exist.
- **[shipped]** Pending proposal → accepted/rejected/snoozed lifecycle.
- **[shipped]** Soft deletion, last-used timestamp, audit events, and SSE contracts.
- **[depends on]** A single intended embedding/indexing plane through Library jobs rather than a
  second memory-only vector database.

This PRD preserves the existing `kind=skill` user-capability semantics.
Reusable procedures are H5 `kind=procedure` and are governed by AR-H8/H2; they must
never be materialized as memory `kind=skill`.

## Objectives and outcomes

1. Persist items, proposals, revisions, and audit rows transactionally in the existing
   embedded local PostgreSQL backend database.
2. Provide cursor-stable, account/project-safe lexical/vector/hybrid local search.
3. Make proposal accept/reject atomic and idempotent.
4. Complete soft-delete, retention, embedding cleanup, local export, and delete-all.
5. Preserve behavioral equivalence in the in-memory adapter for tests and keep the
   store port reusable by a future hosted B2C deployment.
6. Expose user-facing provenance, confidence, sensitivity, expiry/review date,
   and source state.

Launch gates:

- restart, crash-tail-recovery, backup, and restore tests preserve records and proposal
  decisions;
- zero cross-account/project leaks in CRUD/search/SSE/export;
- deletion removes searchability and embeddings within the declared SLA;
- accepted proposal plus memory creation is atomic;
- every migration/backfill is restartable and idempotent.

## Non-goals

- Runtime retrieval/injection (AR-H7).
- Candidate extraction (AR-H5).
- Full chat-history search (AR-G3).
- Executable skill publication (AR-H2/AR-H8).
- A new ai-backend memory database.

## Interfaces consumed

- Verified local-account identity and project selection.
- Existing Library indexing jobs and embedding store.
- E1 local audit, retention, export, deletion, and repair.
- AR-H5 internal proposal ingestion.

## Interfaces exposed

Extend the canonical record:

```text
MemoryItem
  id
  local_account_id
  scope: user | workspace
  project_id?
  kind: fact | preference | skill
  active_revision_id
  lifecycle_state: active | superseded | deleted
  row_version
  created_at
  updated_at
  last_used_at?
  deleted_at?

MemoryItemRevision
  revision_id
  memory_id
  parent_revision_id?
  title
  body_ref
  body_digest
  payload_state: available | redacted | deleted
  tags[]
  subject_ref?
  confidence?
  sensitivity_labels[]
  source_refs[]
  source_state: live | partially_deleted | deleted
  verification_state: supported | review_required | reconfirmed
  evidence_scope_ceiling
  candidate_id?
  candidate_revision?
  candidate_digest?
  evidence_digest?
  review_after?
  expires_at?
  created_by
  created_at
  content_digest
```

Every edit, accepted proposal, correction, scope change, and supersession appends an
immutable `MemoryItemRevision` and atomically advances `active_revision_id`. Historical
revisions are never overwritten. Search, export, audit, H7 recall, and touch attribution
name the exact `revision_id` and `content_digest`, not a mutable integer version.
Content lives behind a local payload ref so deletion can make exact bytes unavailable
while preserving immutable decision metadata and non-content digests during the chosen
undo window.

Add internal, service-authenticated endpoints:

```text
POST /internal/v1/memory/proposal-batches
POST /internal/v1/memory/search
POST /internal/v1/memory/{id}/touch
```

Apps continue to call facade public routes only.

## Detailed design

### 1. Embedded local PostgreSQL adapter

Implement `PostgresMemoryStore` behind the existing `MemoryStore` port and add its
tables through the backend migration mechanism. In `single_user_desktop`, this is the
PostgreSQL 17 process already staged and lifecycle-managed by Electron under the OS app
data directory; it is not a remote service or new runtime dependency.

The adapter provides:

- transaction-context equivalence with the in-memory adapter;
- optimistic row-version checks and stable `(sort_field,id)` cursors;
- soft-delete filtering by default;
- atomic proposal decision, accepted item/revision link, audit append, and index-outbox
  append in one database transaction;
- constraints for account/scope/kind/status invariants;
- bounded connection pool and queries tuned for one desktop user;
- no ai-backend database writes or sibling-service source imports.

Small structured memory bodies may live in PostgreSQL. Large evidence excerpts,
attachments, exports, or copied source payloads use content-addressed filesystem refs
below the OS-owned app-data directory, written with temp + fsync + digest verification
and atomic rename. Introducing a canonical SQLite database for H6 would create a third
truth alongside the existing backend database and ai-backend file store, so it is out
of scope.

### 2. Composition

For `single_user_desktop`, `backend_app.desktop_app` must explicitly inject
`PostgresMemoryStore` using its existing shared local connection pool; it may never
silently fall back to `InMemoryMemoryStore`. Tests explicitly choose in-memory or
Postgres. A future hosted consumer composition can reuse the same port/schema without
changing local behavior. Health exposes adapter kind and migration/index readiness
without record content.

### 3. Proposal ingestion and decisions

AR-H5 sends batches with an idempotency key and source/policy revisions.
Backend validates:

- authenticated per-install service and matching local-account header;
- supported type/size/scope hint;
- evidence-ref format and candidate uniqueness;
- sensitivity/expiry policy.

Proposal storage assigns an immutable `candidate_revision` and computes
`candidate_digest`, `evidence_digest`, and `scope_ceiling_digest`. An acceptance request
must bind all four plus the expected proposal row version:

```text
MemoryProposalDecisionRequest
  proposal_id
  expected_candidate_revision
  expected_candidate_digest
  expected_evidence_digest
  expected_scope_ceiling_digest
  expected_proposal_row_version
  reviewer_edit
    title, body, tags
    scope: personal | project           # translated at the current wire boundary
    project_id?
  reviewer_edit_digest
  idempotency_key
```

Accept may edit title/body/tags and may narrow scope/project. It cannot exceed the
immutable H5 `EvidenceScopeCeiling`; widening requires a separate authorized manual
publication/reconfirmation with a new evidence basis. The backend stores the exact
reviewer-edited revision and a deterministic diff from the candidate. Any candidate,
evidence, ceiling, or row-version drift invalidates the decision and returns conflict.
In one transaction acceptance creates the item/revision, decides the proposal, links
the exact accepted revision, appends audit, and enqueues index work through a
transactional outbox. Repeated same decision replays; contradictory decisions conflict.

### 4. Search and index

Memory lexical/search metadata stays in the same embedded local PostgreSQL database,
using indexed account/scope/state columns and text search. Optional embedding bytes may
remain behind filesystem refs through the existing Library indexing contract; desktop
launch must not depend on a network vector database. Search flow:

1. derive readable personal/project set from verified local identity;
2. apply account/deleted/expiry/sensitivity filters before ranking;
3. obtain lexical and optional vector candidates;
4. fuse deterministically;
5. hydrate and reauthorize final records;
6. return bounded excerpts, score components, and stable refs.

Vector failure degrades to lexical search and emits a health signal. It never
broadens ACL results.

### 5. Contradiction and duplicate UX

Backend may flag related records via indexed similarity but does not merge
automatically. Proposal review shows existing items and supporting/conflicting
source refs. Accept can:

- create distinct memory;
- revise an owned existing item as a new immutable revision;
- supersede an old item;
- reject as duplicate/incorrect.

### 6. User controls

The shared Memory surface supports list/search/filter, source/provenance view,
proposal accept/edit/reject/snooze, manual create/edit, scope change, export,
soft delete, and “forget all learned from this conversation/source.” UI copy
distinguishes user-authored from agent-proposed/accepted state.

## Persistence, retention, deletion, backup, and future sync

- The backend's embedded local PostgreSQL memory rows are canonical on desktop;
  filesystem objects hold large payloads and are reachable only through database refs.
- Soft deletion immediately hides records and removes them from the rebuilt/search
  index.
- Retention sweep hard-deletes after the user's chosen undo/history window.
- Source conversation or trajectory deletion updates source state and executes the
  normative derivative-state matrix below.
- “Delete local data” cascades items, proposals, pending outbox work, embedding objects,
  local audit records, and cached search material.
- Export includes content, scope, sources, versions, and decisions for the active local
  account. Backup takes a consistent PostgreSQL/object snapshot.
- A future hosted B2C sync adapter consumes immutable event IDs/revisions and
  emits remote acknowledgements into a separate sync cursor. It is optional,
  conflict-aware, and never changes the local canonical format or blocks offline CRUD.

### Normative source-deletion matrix

This matrix is the shared H5/H6/H8 rule; implementations may be stricter but cannot
retain or auto-use more data than specified:

| Derived record                                                                   | On one supporting source deletion                                                                                         | On all supporting source deletion                | Ordinary runtime visibility                                                                                                         |
| -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------- |
| Exact message/tool/result excerpt, payload, cache, embedding, or copied evidence | Delete/redact the exact copy and invalidate its ref; retain only a non-content tombstone                                  | Delete/redact all exact copies                   | Never readable after source authorization/retention ends                                                                            |
| Pending H5 candidate/proposal                                                    | Mark source changed and recompute only from remaining authorized sources, otherwise withdraw                              | Withdraw/delete                                  | Never runtime-visible                                                                                                               |
| Accepted H6 memory revision                                                      | Set `partially_deleted`; create a new revision if remaining evidence still supports the body, otherwise `review_required` | Set `deleted` source state and `review_required` | Excluded from H7 automatic recall until explicit user reconfirmation creates a new `reconfirmed` revision                           |
| Unpublished H8/H2 generated draft                                                | Mark incomplete and revalidate exact evidence map; withdraw if an exact copied span cannot be removed safely              | Withdraw                                         | Never selectable                                                                                                                    |
| Published H2 skill derived from trajectories                                     | Remove/redact forbidden exact copies and set H2 `review_required` when remaining support is insufficient                  | Set H2 `review_required`                         | H3 excludes `review_required` from automatic selection/load until an authorized reviewer reapproves or re-sources an exact revision |

A user's independent manual authorship or explicit post-deletion reconfirmation may
create a new revision with new provenance; it does not resurrect deleted evidence.
An explicit user reconfirmation can preserve the remembered fact after deleting its
original chat, but it creates a new revision with manual provenance and never restores
the deleted evidence. Source-deletion events are idempotent, account-scoped, and
propagated through a durable local outbox to indexes, H7 caches, H8 drafts, and H2
lifecycle commands.

## Authorization, privacy, and audit

- Personal rows: active local account read/write.
- Project rows: active local account read/write within the selected local project.
- During wire compatibility, `scope=user` means personal and
  `scope=workspace,project_id=<id>` means project. The store rejects `scope=user` with a
  project id and rejects `scope=workspace,project_id=NULL` for newly created
  desktop-B2C content.
- Every list/search route applies post-rank reauthorization.
- Audit records lifecycle and scope changes without placing body text in
  general log metadata.
- Sensitive labels can make a record user-only and non-retrievable by default.

## Performance and capacity

- CRUD p95 under 40 ms on a reference laptop after warm open.
- Lexical search p95 under 100 ms for 100,000 memories; optional hybrid search p95
  under 300 ms, with hard result/excerpt limits.
- Indexed queries use account/deleted/scope filters; no full-table application scan.
- Index updates are local and asynchronous from the committed database transaction;
  searchable-state lag target is under 2 seconds.
- Per-record body remains bounded; supporting large content belongs in
  artifacts/Library with refs.
- Bound proposal ingestion, compaction, and embedding work per device; pause optional
  embedding/compaction work on battery or thermal pressure.

## Failure, retry, and recovery

- Database transaction rollback leaves no half-accepted proposal.
- Outbox retries index/SSE delivery idempotently.
- Index failure leaves canonical record readable by direct CRUD and exposes
  degraded search.
- Duplicate batch/decision IDs replay safely.
- Cursor invalidation after deletion returns a valid next page, not leaked
  tombstones.
- Import/migration jobs use checkpoints and digests; the previous schema/data stays
  readable until verification succeeds.

## Observability

Track adapter kind, CRUD/search latency, local database/repair errors, database/object
bytes, proposal backlog and decision rate, index queue age/failures, vector degradation,
soft-delete-to-object-collection lag, source-deleted records, authorization denials, and
export jobs. Telemetry remains on-device unless the user separately opts in. Health
checks verify migrations, database readiness, free-space quota, and index generation.

## Rollout and backout

1. Land `PostgresMemoryStore`, migrations, filesystem payload refs, and recovery
   fixtures dark.
2. Run the store contract suite against in-memory/Postgres.
3. Shadow-read a generated fixture dataset; do not dual-write personal content.
4. Enable new proposal ingestion.
5. Enable opt-in desktop cohorts and verify restart/crash/backup/restore behavior.
6. Enable index/vector path after lexical correctness.

Backout stops new mutations through a documented read-only maintenance posture; it must
not fall back to an empty in-memory store. Preserve the local database/object root and
provide a forward-fix/export path.

## Implementation slices

1. Schema migration, object-ref contracts, and `PostgresMemoryStore`.
2. Cross-adapter behavior, transaction, and concurrency tests.
3. Desktop composition, migration health, object-root provisioning, repair, and backup.
4. Internal proposal batch API/idempotency.
5. Index worker memory handler and hybrid search adapter.
6. Source/sensitivity/version contract extensions.
7. Shared review/control UX.
8. Retention/deletion/export/backup/future-sync outbox integration.

## Test plan

- Full store contract suite across adapters.
- Restart, database-unavailable/disk-full, concurrent-request, and optimistic-conflict
  tests.
- Local-account/personal/project authorization matrices and renderer identity forgery.
- Search prefilter and post-rank authorization.
- Atomic accept and duplicate/contradictory decision replay.
- Candidate/evidence/scope-ceiling/row-version drift invalidates acceptance; exact
  reviewer edit digest and immutable revision are auditable.
- Personal evidence cannot be widened to project visibility through
  proposal editing.
- Index outage/degradation and recovery.
- Source/account/delete-all cascades, backup/restore, and consent withdrawal.
- SSE/outbox duplicate and reconnect behavior.
- Migration forward/backward compatibility and 100,000-record pagination.

## Definition of done

- [ ] Packaged desktop uses its embedded local PostgreSQL memory adapter with no silent
      in-memory fallback.
- [ ] Proposal decisions are atomic, idempotent, and audited.
- [ ] Lexical/hybrid search is ACL-safe and operationally monitored.
- [ ] User controls cover inspect/edit/delete/export/provenance.
- [ ] Retention, deletion, source deletion, export, and backup/restore are tested.
- [ ] AR-H7 can consume a stable internal search contract.
- [ ] Shared program DoD passes.

## Guardrails

- Backend is the sole durable product memory owner.
- Never create a second ai-backend memory catalog.
- Never treat memory `kind=skill` as a procedure or executable skill; it remains a user
  capability fact.
- Never use embeddings as an authorization filter.
- Never silently fall back to process memory in a packaged desktop deployment.
- Never make cloud availability a prerequisite for memory recall.

## Open decisions

1. Default review/expiry policy by memory kind.
2. Whether local vector search ships by default or remains an opt-in download.
