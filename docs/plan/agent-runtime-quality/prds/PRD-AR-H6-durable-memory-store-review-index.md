# PRD-AR-H6 — Durable memory store, review, and index

**Goal.** Complete the product memory domain with a production Postgres adapter,
durable proposal decisions, scoped search indexing, retention/deletion, and a
reviewable user experience. Backend remains the only source of truth for
learned facts, preferences, user capabilities, and project conventions.

## Metadata

| Field        | Value                                                                                                  |
| ------------ | ------------------------------------------------------------------------------------------------------ |
| Status       | Proposed; production completion                                                                        |
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

The memory API, schema, service, proposal state machine, audit, SSE, and UI
contracts are extensive, but the application defaults to
`InMemoryMemoryStore`; no production `PostgresMemoryStore` is implemented or
injected. Search is an in-memory lexical implementation with a no-op vector
path. Process restart or horizontal deployment therefore invalidates the
durability implied by the product surface.

## Current implementation and predecessor contracts

- **[shipped]** Typed kinds `fact | preference | skill`; `skill` means a user
  capability such as “can program in Python,” not a reusable procedure.
- **[shipped]** Scopes `user | workspace`, with optional `project_id` narrowing a
  workspace item; `project` is not a third scope enum.
- **[shipped]** Owner/project/admin ACL logic with cross-tenant not-found behavior.
- **[shipped]** Pending proposal → accepted/rejected/snoozed lifecycle.
- **[shipped]** Soft deletion, last-used timestamp, audit events, and SSE contracts.
- **[depends on]** A single intended embedding/indexing plane through Library jobs rather than a
  second memory-only vector database.

This PRD preserves the existing `kind=skill` user-capability semantics.
Reusable procedures are H5 `kind=procedure` and are governed by AR-H8/H2; they must
never be materialized as memory `kind=skill`.

## Objectives and outcomes

1. Persist items, proposals, and audit rows transactionally in Postgres.
2. Provide cursor-stable, ACL-safe lexical/vector/hybrid search.
3. Make proposal accept/reject atomic and idempotent.
4. Complete soft-delete, retention, embedding cleanup, export, and legal hold.
5. Preserve behavioral equivalence in the in-memory adapter for tests/local use.
6. Expose user-facing provenance, confidence, sensitivity, expiry/review date,
   and source state.

Launch gates:

- restart and multi-instance tests preserve records and proposal decisions;
- zero cross-tenant/project ACL leaks in CRUD/search/SSE/export;
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

- Verified identity and project membership.
- Existing Library indexing jobs and embedding store.
- E1 audit, retention, legal hold, and SIEM export.
- AR-H5 internal proposal ingestion.

## Interfaces exposed

Extend the canonical record:

```text
MemoryItem
  id
  tenant_id
  owner_user_id
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
Content lives behind an access-controlled payload ref so a deletion/legal obligation
can make exact bytes unavailable while preserving immutable decision metadata and
non-content digests.

Add internal, service-authenticated endpoints:

```text
POST /internal/v1/memory/proposal-batches
POST /internal/v1/memory/search
POST /internal/v1/memory/{id}/touch
```

Apps continue to call facade public routes only.

## Detailed design

### 1. Postgres adapter

Implement `PostgresMemoryStore` against `memory/schema.sql` with:

- transaction-context equivalence with the in-memory adapter;
- optimistic version check on updates;
- stable cursor ordering with `(sort_field, id)` tie-break;
- soft-delete filtering by default;
- atomic proposal decision and accepted-memory link;
- append-only audit within the same transaction;
- database constraints for tenant/scope/kind/status invariants.

Schema migrations use the service's canonical migration mechanism; importing
SQL at runtime is not a substitute.

### 2. Composition

Backend chooses the adapter from the service's normal store configuration.
Production/Postgres configuration fails closed if it cannot construct the
Postgres adapter; it must not silently fall back to memory. Tests explicitly
choose in-memory. Health exposes adapter kind and migration readiness without
record content.

### 3. Proposal ingestion and decisions

AR-H5 sends batches with an idempotency key and source/policy revisions.
Backend validates:

- authenticated service and matching tenant/user headers;
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
    scope: user | workspace
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

Memory embeddings live in the existing Library embedding table with
`target_kind=memory`. Implement the missing memory handler in the index worker.
Search flow:

1. derive readable scope/project set from verified identity;
2. apply tenant/deleted/expiry/sensitivity filters before ranking;
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

## Persistence, retention, and deletion

- Backend Postgres is canonical.
- Soft deletion immediately hides records and queues embedding deletion.
- Retention sweep hard-deletes after policy unless legal hold applies.
- Source conversation or trajectory deletion updates source state and executes the
  normative derivative-state matrix below.
- Account/tenant deletion cascades items, proposals, outbox rows, embeddings,
  audit per legal policy, and cached search material.
- Export includes content, scope, sources, versions, and decisions the caller
  is entitled to see.

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
Legal hold can retain exact bytes only in the held access-controlled source/derivative
record. It never restores search, source opening, memory recall, skill selection, or
ordinary reviewer access. Source-deletion events are idempotent, tenant-scoped, and
propagated through a durable backend outbox to indexes, H7 caches, H8 drafts, and H2
lifecycle commands.

## Authorization, privacy, and audit

- User-scoped rows: owner read/write.
- Workspace rows: tenant read; owner or designated admin write.
- Project rows: project membership read; owner/project role write.
- `scope=workspace, project_id=NULL` means tenant workspace visibility;
  `scope=workspace, project_id=<id>` narrows visibility to that project. The database
  rejects `scope=user` with a project id and rejects any invented `scope=project`.
- Admin compliance read does not imply write to private user memory.
- Every list/search route applies post-rank reauthorization.
- Audit records lifecycle and scope changes without placing body text in
  general log metadata.
- Sensitive labels can make a record user-only and non-retrievable by default.

## Performance and capacity

- CRUD p95 under 150 ms in-region.
- Search p95 under 500 ms for indexed tenants; hard result and excerpt limits.
- Indexed queries use tenant/deleted/scope filters; no full-table app scan.
- Index enqueue is asynchronous but transactional; searchable-state lag SLO is
  60 seconds.
- Per-record body remains bounded; supporting large content belongs in
  artifacts/Library with refs.
- Rate-limit proposal ingestion and user search per tenant.

## Failure, retry, and recovery

- Database transaction rollback leaves no half-accepted proposal.
- Outbox retries index/SSE delivery idempotently.
- Index failure leaves canonical record readable by direct CRUD and exposes
  degraded search.
- Duplicate batch/decision IDs replay safely.
- Cursor invalidation after deletion returns a valid next page, not leaked
  tombstones.
- Migration jobs use checkpoints and can be restarted.

## Observability

Track adapter kind, CRUD/search latency, Postgres errors, proposal backlog and
decision rate, index queue age/failures, vector degradation, soft-delete to
embedding-delete lag, source-deleted records, ACL denials, and export/legal-hold
jobs. Health checks verify schema and worker support for `target_kind=memory`.

## Rollout and backout

1. Land Postgres adapter and migrations dark.
2. Run adapter contract suite against in-memory/Postgres.
3. Dual-read/shadow compare an internal fixture dataset; do not dual-write
   user content without a reviewed migration.
4. Enable new proposal ingestion.
5. Enable tenant cohorts and verify restart/horizontal behavior.
6. Enable index/vector path after lexical correctness.

Backout stops new traffic to the Postgres feature surface only through a
documented maintenance posture; it must not fall back to an empty in-memory
store. Preserve data and provide a forward-fix/export path.

## Implementation slices

1. Schema migration and `PostgresMemoryStore`.
2. Cross-adapter behavior, transaction, and concurrency tests.
3. Production composition and health.
4. Internal proposal batch API/idempotency.
5. Index worker memory handler and hybrid search adapter.
6. Source/sensitivity/version contract extensions.
7. Shared review/control UX.
8. Retention/deletion/export/legal-hold integration.

## Test plan

- Full store contract suite across adapters.
- Restart, two-process concurrency, and optimistic conflict tests.
- Tenant/user/workspace/project/admin ACL matrices.
- Search prefilter and post-rank authorization.
- Atomic accept and duplicate/contradictory decision replay.
- Candidate/evidence/scope-ceiling/row-version drift invalidates acceptance; exact
  reviewer edit digest and immutable revision are auditable.
- User-private evidence cannot be widened to workspace/project visibility through
  proposal editing.
- Index outage/degradation and recovery.
- Source/account/tenant deletion cascades and legal hold.
- SSE/outbox duplicate and reconnect behavior.
- Migration forward/backward compatibility and large tenant pagination.

## Definition of done

- [ ] Production uses Postgres memory persistence with no silent fallback.
- [ ] Proposal decisions are atomic, idempotent, and audited.
- [ ] Lexical/hybrid search is ACL-safe and operationally monitored.
- [ ] User controls cover inspect/edit/delete/export/provenance.
- [ ] Retention, deletion, source deletion, and legal hold are tested.
- [ ] AR-H7 can consume a stable internal search contract.
- [ ] Shared program DoD passes.

## Guardrails

- Backend is the sole durable product memory owner.
- Never create a second ai-backend memory catalog.
- Never treat memory `kind=skill` as a procedure or executable skill; it remains a user
  capability fact.
- Never use embeddings as an authorization filter.
- Never silently fall back to process memory in a durable deployment.

## Open decisions

1. Default review/expiry policy by memory kind.
2. Which deployment profiles enable vector search by default.
