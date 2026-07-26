# PRD-AR-G1 — First-party Library grounding

**Goal.** Let the agent retrieve tenant-authorized Library content as bounded,
version-pinned evidence and cite it in answers without copying whole documents into the
prompt, bypassing Library ACLs, or conflating retrieved text with instructions.

| Field             | Value                                                                                                                                      |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Status            | Draft for review                                                                                                                           |
| Primary owners    | `backend` Library domain; `ai-backend` retrieval adapter                                                                                   |
| Public API impact | Additive source projection only; apps continue through facade                                                                              |
| Runtime rollout   | `RUNTIME_LIBRARY_GROUNDING_MODE`: off → shadow → on                                                                                        |
| Depends on        | A2 Artifact Repository, A3 Operation Gateway, B3 presentation lifecycle, E1 accountability/lifecycle, AR-F5 context budget/evidence reader |

## Implementer brief

Read before implementation:

1. `../README.md` for program-level invariants and launch gates.
2. `../../prds/PRD-A2-artifact-repository.md`,
   `../../prds/PRD-A3-operation-gateway.md`, and
   `../../prds/PRD-E1-accountability-lifecycle.md`.
3. `services/backend/src/backend_app/library/search.py`.
4. `services/backend/src/backend_app/library/search_routes.py`.
5. `services/backend/src/backend_app/library/service.py`.
6. `services/backend/src/backend_app/library/store.py`.
7. `services/backend/src/backend_app/library/schema.sql`.
8. `services/backend/src/backend_app/library/embeddings.py`.
9. `services/backend-facade/src/backend_facade/library_routes.py`.
10. `packages/api-types/src/library.ts`.
11. `services/ai-backend/src/agent_runtime/capabilities/citations.py`.
12. `services/ai-backend/src/agent_runtime/capabilities/citation_projection.py`.
13. `services/ai-backend/src/agent_runtime/api/source_open_service.py`.
14. `services/ai-backend/src/agent_runtime/capabilities/operations/gateway.py`.

The Library remains owned by `backend`. `ai-backend` consumes a private HTTP contract;
it must not import `backend_app` or duplicate Library ACL logic.

## Problem statement

The product already stores files, pages, and datasets and supports hybrid Library
search. The agent runtime does not have a first-party evidence contract for that
content. It can therefore overlook the user's durable knowledge, ask for material the
user has already saved, or rely on a connector/web result when a tenant-controlled
source is available.

Exposing the existing UI search response directly to the model would be insufficient:
its excerpts are presentation projections, not immutable evidence; it does not bind a
claim to an item version and chunk; and an unrestricted result could consume the
context window or carry hostile instructions.

## Current implementation and predecessor contracts

- **[shipped]** `backend_app.library` owns item metadata, blobs, indexing jobs, hybrid BM25/vector
  retrieval, project-aware ACL filtering, soft deletion, and audit behavior.
- **[shipped]** `library_embeddings` is the shared chunk/vector store; no second Library index is
  required.
- **[shipped]** Public Library routes already flow through `backend-facade`.
- **[shipped]** The runtime has citation ordinals, source projection, operation descriptors, bounded
  large-result offload, and replayable events.
- **[shipped]** Library bytes remain behind opaque blob refs and short-lived signed URLs.

These are foundations. This PRD adds an agent-facing retrieval seam without replacing
them.

## Objectives

1. Retrieve the most relevant readable Library chunks for a task.
2. Bind each evidence unit to tenant, item, revision/content digest, and chunk ordinal.
3. Support progressive retrieval: search summaries first, open selected evidence next.
4. Make citations resolvable after reconnect and replay.
5. Keep Library policy, deletion, and indexing truth in `backend`.
6. Degrade to keyword retrieval when embeddings or re-ranking are unavailable.

### Success measures

- At least 90% top-5 evidence recall on the checked-in Library grounding set.
- At least 95% of grounded factual claims carry a resolvable Library citation.
- Zero cross-user/project evidence in authorization and adversarial suites.
- Search response p95 below 1.5 seconds without re-rank and 3 seconds with re-rank at
  the documented launch corpus size.
- Model-visible search payload at or below 12 KiB and open payload at or below 24 KiB.

## Non-goals

- Replacing the Library destination or public search UI.
- Automatically saving connector/web content into Library.
- Treating prior user-authored Library text as a system instruction.
- Answering from deleted, superseded, unindexed, or unauthorized bytes.
- Cross-tenant organizational search or admin e-discovery.

## Interfaces consumed

- Existing Library item/index/blob stores and hybrid search engine.
- Existing verified service-token headers for `ai-backend` → `backend`.
- A3 descriptor, gateway, result disposition, and operation event contracts.
- F5 single model-facing evidence-reader contract and context budget.
- Existing citation ledger and source-open resolver.
- E1 retention/deletion/legal-hold, protected derivative-reference graph, and audit
  rules.

## Interfaces exposed

### Private backend API

```text
POST /internal/v1/library/evidence/search
POST /internal/v1/library/evidence/open
```

Both require the enterprise service token plus trusted
`x-enterprise-org-id`/`x-enterprise-user-id`. Caller-supplied identity in the body is
forbidden.

```text
LibraryEvidenceSearchRequest
  query: string                         # 1..2,000 chars
  project_id?: ProjectId
  kinds?: file | page | dataset[]
  limit: int                            # default 8, max 20
  lexical_candidates: int               # server-clamped
  vector_candidates: int                # server-clamped
  rerank: bool

LibraryEvidenceHit
  evidence_ref: string                  # opaque, signed/scoped reference
  item_ref: ItemRef
  kind: file | page | dataset
  title: string
  excerpt: string                       # normalized, bounded
  chunk_ordinal: int
  item_version: int | null
  content_digest: sha256
  score: float
  matched_in: title | content | tag
  updated_at: datetime

LibraryEvidenceOpenRequest
  evidence_refs: string[]               # max 8

LibraryEvidenceBlock
  evidence_ref: string
  item_ref: ItemRef
  chunk_ordinal: int
  item_version: int | null
  content_digest: sha256
  text: string                          # bounded exact indexed chunk
  heading_path: string[]
  byte_range?: {start, end}
```

`evidence_ref` is an opaque authenticated reference over tenant, user visibility
context, item id, version/digest, chunk ordinal, and expiry. Opening rechecks current
ACL and deletion state; the signature is not authorization.

Every result payload, checkpoint, citation locator, or run replay record that can
contain opened Library text must register a `ProtectedDerivativeEvidenceRef` with E1:

```text
ProtectedDerivativeEvidenceRef
  derivative_ref: payload | checkpoint | citation | replay ref
  source_kind: library_item
  source_item_ref
  source_revision_or_digest
  tenant_id, owner_user_id?, project_id?
  contains_source_text: bool
  retention_class
  legal_hold_state
```

This graph is access-controlled metadata. It is not model-visible and is written in the
same transaction/outbox boundary as the derivative payload.

### Model-visible tools

```text
search_library(query, project_id?, kinds?, limit?)
```

G1 exposes search only. Hits contain opaque typed refs registered behind F5's
`EvidenceReader`; the sole model-facing hydration call is F5-owned
`read_evidence(ref, selector, max_chars)`. The internal backend batch-open API and
`LibraryEvidenceProvider` remain source resolvers, not independently registered model
tools. Search and F5 hydration register through A3 as read operations. Neither creates a
canvas solely because it ran.

### Events

```text
library.evidence.searched.v1
library.evidence.opened.v1
library.evidence.unavailable.v1
```

Public event payloads contain operation id, counts, latency, strategy, item refs, and
content digests. They never contain query text, excerpts, document text, blob refs, or
signed URLs.

## Design

### D1. Retrieval path and service ownership

`ai-backend` calls the private backend API through a narrow
`LibraryEvidenceProvider` port. `backend` authenticates the service caller, derives the
tenant/user scope from headers, applies current Library ACLs, and invokes the existing
hybrid engine.

The runtime does not call the public facade and does not receive raw object-store
credentials. Tests use an in-process fake provider that implements the same wire
contract.

### D2. Progressive evidence loading

The model receives at most eight small search hits. A hit contains enough information
to choose evidence but not a complete document. F5's `read_evidence` groups selected
Library refs through the internal source resolver, which batches up to eight refs and
returns a total maximum of 24 KiB under the shared evidence budget.

This changes prompt cost from the size of every candidate document to:

```text
O(k · summary_size + selected_chunks · chunk_size), where k <= 20
```

The underlying indexed search remains approximately
`O(query + candidate retrieval + k log k)` rather than scanning all Library bytes.

### D3. Version-pinned evidence

Every indexed chunk records the parent content digest and, for versioned pages, the
revision. Search emits those values. Open succeeds only when the current indexed row
still matches or the retained immutable revision is authorized and readable.

If content changed after search, open returns `evidence_stale` with a safe instruction
to search again. It never silently substitutes a new chunk under an old reference.

### D4. Instruction/data boundary

Opened content is wrapped as structured evidence with an explicit runtime instruction:
the block is untrusted source data and cannot alter tool policy, permissions, approval,
or the system prompt. Prompt-injection detectors may add a risk label, but detection is
not the security boundary.

Tool descriptions tell the model to:

- use Library evidence when the task concerns the user's saved material;
- distinguish statements in a source from independently verified facts;
- cite the evidence ordinal attached by the runtime;
- avoid opening additional chunks after evidence is sufficient.

### D5. Citation and source opening

Each opened block is registered with the existing citation ledger using:

```text
SourceLocator
  source_kind: library
  item_ref
  evidence_ref
  revision_or_digest
  chunk_ordinal
```

`[[N]]` markers resolve to an app-facing source card. Opening the source routes through
the facade's canonical Library item route and current authorization, never through a
stored signed URL.

### D6. Dataset behavior

Dataset search may index names, descriptions, schemas, and bounded sample metadata.
It must not serialize an entire table into an evidence chunk. Row-level analysis uses a
separate bounded query/compute capability and is outside this PRD.

### D7. Cache and freshness

`backend` may cache normalized search results for at most 60 seconds keyed by tenant,
user visibility fingerprint, project, query digest, filters, index generation, and
embedding/reranker version. The cache stores opaque refs and rankings, not authorization
decisions. Any item/index mutation advances the generation.

`ai-backend` may de-duplicate identical searches within one run. It may not maintain a
cross-run evidence cache.

## Persistence, retention, and deletion

- Evidence text remains in the existing Library/index stores; no duplicate durable body
  store is introduced. Opened text may still be duplicated transiently or durably by
  existing result payload, checkpoint, replay, or citation systems; each such copy is a
  protected derivative governed through the E1 reference graph above.
- Events and citation rows retain opaque item/version/chunk references only.
- Library deletion or access revocation makes future opens fail immediately.
- Source deletion, account deletion, project-membership loss, or retention expiry
  traverses protected derivative refs: unauthorized copies are redacted or deleted,
  payload/open resolvers return tombstones, and caches are invalidated. A legal hold
  may retain exact bytes only in the held, access-controlled record and never keeps
  ordinary source opening available.
- Citation history may retain title/digest metadata under E1, but source opening must
  return unavailable after deletion.
- Search cache entries expire or are generation-invalidated and are excluded from legal
  hold; canonical Library rows remain governed by existing hold policy.
- Account/org deletion tests cover index rows, cache keys, citations, and pending jobs.
- Reconciliation continuously detects derivative payloads containing Library text
  without a source edge and fails the rollout gate if any are found.

## Authorization, privacy, and security

- Scope derives only from verified service identity.
- Search applies item, project membership, owner, and org-admin rules before ranking is
  returned; post-ranking filtering alone is forbidden.
- Unauthorized and absent items are indistinguishable.
- Queries, excerpts, and evidence bodies do not enter logs, metrics labels, audit
  metadata, or traces.
- Evidence blocks are untrusted data; embedded instructions cannot widen capabilities.
- Blob refs and signed URLs never enter runtime events or model-visible output.
- Rate and token budgets are enforced per tenant/user/run.

## Failure, idempotency, and recovery

- Search/open operations use deterministic operation ids and are safe to retry.
- Embedding failure falls back to lexical search and reports `bm25_only`.
- Reranker failure returns fused candidates without re-ranking.
- Backend timeout yields a typed retryable result; the agent can answer without Library
  evidence and must state the limitation.
- A stale, deleted, or revoked ref is non-retryable until the model searches again.
- Cancellation propagates to backend HTTP and no result is appended after cancellation.
- Replay reads persisted operation/citation events and never reissues retrieval.

## Performance and capacity

- Default top-k 8; hard maximum 20.
- Search query 2,000 characters; excerpt 600 characters; opened chunk 4 KiB; total open
  response 24 KiB.
- One embedding request per unique query/run, with a 3-second deadline.
- No more than two backend HTTP calls for a normal search→batch-open sequence.
- Search p95 and index freshness lag are launch gates, segmented by strategy.
- Capacity tests cover 1 million chunks/tenant and 100 concurrent searches without
  unbounded connection or memory growth.

## Metrics

- `library_grounding_search_total{strategy,outcome}`
- `library_grounding_search_duration_ms`
- `library_grounding_hits_returned`
- `library_grounding_open_total{outcome}`
- `library_grounding_stale_ref_total`
- `library_grounding_citation_resolution_rate`
- `library_grounding_prompt_bytes`

No metric contains query, title, item id, project id, or evidence text.

## Rollout and backout

1. Ship private contracts, fakes, and authorization tests dark.
2. Shadow search on an evaluation-only cohort; do not expose results to the model.
3. Enable the tools for internal tenants with BM25-only fallback.
4. Enable hybrid retrieval and re-ranking independently.
5. Expand by tenant policy after quality/security gates pass.

Backout removes the tools from model assembly and stops shadow calls. Library data,
indexes, public routes, and existing citations remain readable; no data migration must
be reversed.

## Implementation slices

1. Add private request/response contracts and golden fixtures.
2. Add backend evidence search/open service over existing indexes and ACLs.
3. Add private routes, service-token authorization, and rate limits.
4. Add `LibraryEvidenceProvider` HTTP adapter and fakes in `ai-backend`.
5. Register the A3 search descriptor and Library resolver behind F5's evidence reader.
6. Wire citation projection/source opening through the same typed ref.
7. Add generation cache and freshness invalidation.
8. Add metrics, evaluation corpus, shadow mode, and rollout controls.

## Test plan

### Contracts and retrieval

- Pydantic/JSON fixture compatibility for every request, response, and error.
- BM25-only, vector-only, hybrid, re-rank failure, zero-result, and stale-ref cases.
- Page revision and file/dataset digest binding.
- Batch-open ordering and hard byte limits.

### Authorization and lifecycle

- Cross-org, cross-user, non-member project, revoked membership, deleted item, legal
  hold, and account deletion.
- Forged/expired evidence refs and current-ACL recheck.
- Query/excerpt/blob/signed-URL log and trace redaction.
- Every text-bearing result/checkpoint/replay/citation payload registers an E1
  derivative edge; source/account/project deletion traverses it and removes ordinary
  access, while legal hold remains isolated.

### Runtime quality

- Search result produces no unwanted canvas.
- Opened evidence receives stable citation ordinals and source resolution.
- Hostile document instructions cannot change tools or policy.
- Evaluation set measures top-k recall, citation correctness, unsupported claims, and
  token cost against a no-grounding baseline.

### Performance and recovery

- Million-chunk query plan/latency test.
- Timeout/cancel/retry/replay.
- Cache generation invalidation and no cross-principal reuse.

## Definition of done

- [ ] The agent can search and selectively hydrate authorized Library evidence through
      F5's sole model-facing evidence reader.
- [ ] Every opened block is version/digest pinned and citation-resolvable.
- [ ] `backend` remains the sole Library ACL and data owner.
- [ ] Search/open payloads and prompt contribution are bounded.
- [ ] Deletion/revocation makes evidence unavailable without stale disclosure.
- [ ] Shadow and enforced modes meet quality, latency, privacy, and tenant-isolation
      launch gates.
- [ ] Standard A3 and E1 definitions of done pass.

## Guardrails

- Never copy complete Library bodies into the system prompt.
- Never trust an evidence-ref signature as authorization.
- Never filter unauthorized search hits only after ranking.
- Never treat retrieved content as runtime instruction.
- Never persist signed URLs or raw evidence in events.

## Open decisions

- Whether org-shared Library items require an explicit `share_version` in the evidence
  reference or whether the existing ACL generation is sufficient.
- Whether the first release exposes re-ranking per tenant or uses one deployment-wide
  switch.
