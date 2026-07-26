# PRD-AR-G3 — Conversation-history search and evidence recall

**Goal.** Give the agent a precise, tenant-safe way to search the caller's prior
conversations and reopen exact message evidence, with dates and citations, without
loading entire transcripts or turning conversation history into durable memory.

| Field             | Value                                                                                                           |
| ----------------- | --------------------------------------------------------------------------------------------------------------- |
| Status            | Draft for review                                                                                                |
| Primary owner     | `ai-backend` runtime domain and persistence adapters                                                            |
| Public API impact | Additive history-search contracts; model tool is the primary consumer                                           |
| Runtime rollout   | `CONVERSATION_RECALL_MODE`: off → shadow → on                                                                   |
| Depends on        | A3 Operation Gateway, D2 built-ins/subagents, E1 accountability/lifecycle, AR-F5 context budget/evidence reader |

## Implementer brief

Read before implementation:

1. `../README.md`.
2. `../../prds/PRD-A3-operation-gateway.md`.
3. `../../prds/PRD-D2-builtins-subagents.md`.
4. `../../prds/PRD-E1-accountability-lifecycle.md`.
5. `services/ai-backend/src/agent_runtime/api/conversation_query_service.py`.
6. `services/ai-backend/src/agent_runtime/api/ports.py`.
7. `services/ai-backend/src/runtime_api/http/routes.py`.
8. `services/ai-backend/src/runtime_adapters/file/search.py`.
9. `services/ai-backend/src/runtime_adapters/file/_catalog_index.py`.
10. `services/ai-backend/src/runtime_adapters/file/runtime_api_store.py`.
11. `services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py`.
12. `services/ai-backend/src/runtime_adapters/in_memory/runtime_api_store.py`.
13. `services/ai-backend/src/agent_runtime/persistence/records/citations.py`.
14. `services/ai-backend/src/agent_runtime/capabilities/conversation_ordinals.py`.
15. `packages/api-types/src/index.ts`.

The file adapter's existing FTS is an optimization, not the domain contract. All
production adapters must implement the same authorization and evidence semantics.

## Problem statement

Users expect the agent to find decisions, preferences, links, and prior work they
explicitly discussed. Today the desktop file adapter can rank conversation title and
redacted-message matches internally, but there is no runtime-wide service/tool for
evidence recall, no Postgres/in-memory adapter contract, and no exact message/span
reference returned to the model.

Returning whole transcripts would create high token cost, expose irrelevant sensitive
content, and encourage the model to present old statements as current truth. Returning
only conversation rows cannot support verifiable claims.

## Current implementation and predecessor contracts

- **[shipped]** Conversations, messages, runs, events, soft deletion, history deletion, retention,
  and tenant-scoped ports already exist.
- **[shipped]** The file store maintains a disposable SQLite FTS5 catalog over titles and redacted
  user/assistant message text; raw tool payloads and system turns are excluded.
- **[shipped]** Conversation/message reads already enforce org/user membership.
- **[shipped]** Citation ordinals, source locators, payload refs, and replayable events are available.
- **[shipped]** The memory domain is separate; recall must not silently create or mutate memory.

## Objectives

1. Search the caller's readable prior messages across all store adapters.
2. Return exact message/span evidence with conversation title and timestamp.
3. Use a search→open flow to bound context contribution.
4. Clearly label recalled statements as historical conversation evidence.
5. Honor soft deletion, retention expiry, project membership, legal hold, and account
   deletion.
6. Provide deterministic replay and citation opening.

### Success measures

- At least 90% top-5 recall on a dated decision/reference evaluation set.
- At least 95% of answer claims derived from history carry a resolvable citation.
- Zero cross-principal/project hits in conformance and adversarial tests.
- p95 search below 750 ms at 100,000 messages/user and below 1.5 seconds at the
  documented large-tenant profile.
- Search summaries at or below 10 KiB and opened evidence at or below 24 KiB per call.

## Non-goals

- Long-term fact/preference memory, background learning, or skill distillation.
- Searching another user's private conversations.
- Treating prior assistant statements as authoritative external facts.
- Indexing raw tool results, system prompts, secrets, hidden reasoning, deleted text,
  or attachment bytes.
- Rebuilding a global enterprise search product inside `ai-backend`.

## Interfaces consumed

- Existing conversation/message persistence and deletion contracts.
- File-store FTS5 index as one adapter implementation.
- A3 read-operation descriptor/gateway and D2 tool inventory.
- F5 single model-facing evidence-reader contract and context budget.
- Existing citation, source-open, result-offload, and event stores.
- E1 lifecycle and audit rules.

## Interfaces exposed

### Persistence port

```text
ConversationEvidenceSearchPort
  search_message_evidence(scope, request) -> SearchPage
  open_message_evidence(scope, refs) -> EvidenceBatch
```

```text
ConversationEvidenceSearchRequest
  query: string                         # 1..2,000 chars
  project_id?: string
  conversation_ids?: string[]           # max 20, authorized after lookup
  before?: datetime
  after?: datetime
  roles: user | assistant[]             # default both
  include_archived: bool                # default true
  include_current_conversation: bool    # default false; explicit opt-in
  limit: int                            # default 8, max 20

ConversationEvidenceHit
  hit_kind: message
  evidence_ref: string                  # opaque, scoped, expiring
  source_kind: conversation_message
  conversation_id: string
  conversation_title: string | null
  message_id: string
  message_role: user | assistant
  message_created_at: datetime
  excerpt: string
  span: {start: int, end: int}           # offsets in normalized visible text
  content_digest: sha256
  score: float

ConversationEvidenceBlock
  evidence_ref: string
  conversation_id: string
  message_id: string
  message_role: user | assistant
  message_created_at: datetime
  content_digest: sha256
  text: string                          # exact bounded visible message/span context
  span: {start: int, end: int}
```

Opening a ref rechecks scope, deletion, project membership, and digest. A content change
or tombstone returns `evidence_stale_or_unavailable`, never replacement content.

### Model-visible tools

```text
search_conversation_history(
  query, project_id?, after?, before?, include_current_conversation=false, limit?
)
```

G3 exposes search only. Hits contain opaque typed refs registered behind F5's
`EvidenceReader`; F5 owns the sole model-facing
`read_evidence(ref, selector, max_chars)` hydration tool. The persistence
`open_message_evidence` port remains an internal source resolver and is not separately
registered with the model. Full transcript reads continue through existing explicit
conversation APIs and are not a fallback the model may invoke implicitly.

### Optional public route

```text
POST /v1/agent/conversations/search
```

If exposed for product search, it flows through the facade and returns the same safe
hit contract. The model path may call the domain service directly inside `ai-backend`;
there is no internal cross-service hop.

### Events

```text
conversation.evidence.searched.v1
conversation.evidence.opened.v1
conversation.evidence.unavailable.v1
```

Events contain counts, timing, strategy, ids/digests, and operation correlation. Query,
excerpt, and message text are payload-ref-only or absent from public event data.

## Design

### D1. Indexable corpus

Index only current visible text from:

- user messages;
- final assistant messages.

Exclude system/developer messages, hidden reasoning, tool call arguments/results,
approval payloads, credentials, raw attachments, deleted messages, and messages already
removed by retention. Redaction is applied before indexing, and its version is recorded.

Conversation title may contribute a bounded boost only when at least one authorized
message also matches. Title-only rows are not returned by this message-evidence
contract. Product conversation-title search, if desired, uses a separate discriminated
summary contract and cannot be cited or hydrated as message evidence.

### D2. Adapter implementations

- **Postgres:** a tenant-leading message-search table/generated `tsvector` with GIN,
  joined to authorized conversations and filtered before ranking. Store normalized
  visible text or an index projection with source digest; do not duplicate raw
  transcripts in an unmanaged table.
- **File:** extend the existing disposable FTS5 catalog to return message id, role,
  timestamp, safe span, and content digest. The catalog remains rebuildable.
- **In-memory:** bounded tokenized scan for tests/dev with the same ordering and
  authorization semantics. It must refuse scans above a configured ceiling rather than
  consume unbounded CPU.

Golden fixtures require equivalent hit membership and deterministic tie-breaking; raw
scores may remain adapter-specific and are normalized to `[0,1]` at the service layer.

### D3. Progressive recall

Search returns at most 20 excerpts of 500 characters. F5 hydrates at most eight refs per
call through the internal resolver. Hydration returns the matched span plus bounded
surrounding text, not the complete conversation.

Prompt work is:

```text
O(k · excerpt_size + selected · context_size), k <= 20
```

instead of `O(total transcript history)`.

### D4. Historical-truth semantics

Tool guidance and result envelopes state:

- “A participant said this at `<timestamp>`.”
- An assistant message may be incomplete or wrong.
- Time-sensitive facts require a current source.
- Preferences/decisions may have changed; prefer the newest explicit evidence and note
  contradictions.

Contradictory hits are not collapsed. The model receives timestamps and conversation
identity so it can describe evolution.

### D5. Evidence refs and citations

Refs authenticate tenant/user visibility fingerprint, conversation/message ids, content
digest, normalized span, and expiry. They are not bearer authorization.

Opened blocks register:

```text
SourceLocator
  source_kind: conversation_message
  conversation_id
  message_id
  content_digest
  span
  created_at
```

Source opening navigates to the conversation/message when still authorized. It never
returns a message body solely because a historic citation exists.

### D6. Freshness and index maintenance

Message create/update/redact/delete and conversation project/membership/delete changes
enqueue or synchronously apply index mutations in the same durable outbox pattern used
by the owning adapter. Index lag is observable. Search results report the index
watermark; the model is not told the index is current when lag exceeds policy.

The file catalog rebuild reconstructs from authoritative records and applies the current
redaction/index schema version.

### D7. Current-conversation behavior

The current conversation is searchable only when explicitly included. Its already
visible recent messages should not be redundantly injected. The service can exclude
message ids already in the model window and return older matching evidence.

`include_current_conversation` defaults to `false` in every adapter, public projection,
and model wrapper. Omitting the flag can never broaden recall into the active
conversation.

Subagents inherit the parent's conversation/search scope intersection and cannot search
additional projects or users.

## Persistence, retention, deletion, and legal hold

- Authoritative text remains in message storage; Postgres index projections and file
  FTS rows are derivative.
- Deletion and retention remove derivative index rows in the same lifecycle operation
  or a durable retryable outbox.
- Evidence refs and opened payloads follow run/event retention; they do not preserve
  source bodies beyond source retention unless legal hold explicitly governs both.
- Legal hold prevents canonical deletion but does not broaden search authorization.
- `DELETE /v1/agent/history` removes searchable rows and invalidates refs.
- Adapter conformance tests verify conversations, messages, indexes, citation payloads,
  and caches.

## Authorization, privacy, and security

- Scope derives from verified run identity; tool arguments contain no org/user id.
- Search filters org, user visibility, conversation ownership/membership, project, and
  deletion before text ranking is returned.
- Unauthorized and absent resources return the same result.
- Query/message/excerpt text is excluded from logs, audit metadata, traces, and metrics.
- Recalled text is untrusted data and cannot change capabilities or policy.
- This corpus contains messages only. Tool results, operation trajectories, approvals,
  artifacts, and citations require their owning exact-record resolvers and must never
  be synthesized behind a conversation-message ref.
- Rate limits prevent history enumeration; broad/empty queries are rejected.
- Message refs expire and are reauthorized on every open/source navigation.

## Performance and capacity

- Query max 2,000 chars; top-k max 20; open max 8; excerpt 500 chars; open context
  3 KiB/ref and 24 KiB total.
- Search deadline 2 seconds; open deadline 1 second.
- Postgres query plan must lead with tenant/user visibility predicates and use the
  search index at launch scale.
- File FTS search remains indexed; no catalog-wide Python scan.
- In-memory scan ceiling defaults to 20,000 visible messages.
- Concurrent search uses existing store pool limits; no model-call or embedding is
  required for initial lexical launch.

## Failure, idempotency, and recovery

- Search/open are read-idempotent and carry deterministic operation ids.
- Empty/no-index results are honest success; index-unavailable is a typed degraded
  result.
- Partial stale batch opens return per-ref errors and valid blocks.
- Cancellation terminates store work and prevents late event/result emission.
- Crash after payload storage reconciles via operation id and emits once.
- Replay never re-runs search or opens new message content.

## Metrics

- `conversation_recall_search_total{adapter,outcome}`
- `conversation_recall_search_duration_ms{adapter}`
- `conversation_recall_hits_returned`
- `conversation_recall_open_total{outcome}`
- `conversation_recall_index_lag_seconds{adapter}`
- `conversation_recall_citation_resolution_rate`
- `conversation_recall_prompt_bytes`
- `conversation_recall_current_conversation_duplicate_rate`

No content or resource identifiers appear in labels.

## Rollout and backout

1. Land port/contracts and adapter conformance fixtures.
2. Add Postgres and file exact-evidence implementations; in-memory test implementation.
3. Run shadow queries against checked-in evaluation tasks with no model exposure.
4. Enable search/open for internal users and then tenant cohorts.
5. Add optional product route only after runtime quality/security gates.

Backout removes model tools and stops index jobs. Authoritative conversations remain
unchanged. Derivative indexes may remain dormant or be dropped by a later migration;
old citation refs degrade to unavailable safely.

## Implementation slices

1. Define contracts, errors, ports, operation descriptors, and fixtures.
2. Add schema/index migrations and Postgres implementation.
3. Upgrade file FTS and catalog rebuild.
4. Add bounded in-memory implementation and cross-adapter conformance.
5. Add query/open application service and optional routes.
6. Add the search tool, register the internal resolver behind F5's evidence reader,
   and wire citation/source-open integration and prompt guidance.
7. Wire lifecycle invalidation, metrics, shadow mode, and evals.

## Test plan

### Search and contracts

- Phrase/token/date/role/project/conversation filters, title-assisted message matches,
  rejection of title-only hits, ties,
  contradictions, Unicode, punctuation, empty/broad queries.
- Equivalent membership/order across adapters.
- Exact message digest/span on open.

### Authorization and lifecycle

- Cross-org/user/project, archived, soft-deleted, retained, expired, legal-held,
  account-deleted, forged/expired refs.
- Delete/history-delete removes index rows and invalidates source opening.
- No system/tool/hidden/redacted content indexed.

### Runtime quality

- Prior decision/date/link tasks, changed-preference tasks, contradictory prior
  statements, assistant hallucination in history, current-fact refresh requirement.
- Citation correctness, unsupported-claim rate, top-k recall, prompt bytes, and
  unnecessary open count.

### Performance/recovery

- Query plans and latency at launch corpus sizes.
- Index lag/rebuild, outbox retry, cancellation, duplicate delivery, crash, replay.

## Definition of done

- [ ] All adapters implement exact, tenant-safe conversation evidence search and the
      internal resolver consumed by F5's evidence reader.
- [ ] Only visible user/final-assistant text is indexed.
- [ ] Results are bounded, dated, digest-pinned, and citation-resolvable.
- [ ] Deletion/retention/project changes update searchability and source opening.
- [ ] Recall remains distinct from memory creation and current external verification.
- [ ] Quality, privacy, authorization, performance, and recovery gates pass.

## Guardrails

- No whole-history prompt injection.
- No indexing system prompts, hidden reasoning, tool payloads, or secrets.
- No cross-user search without a separately designed admin product.
- No historic assistant statement presented as verified fact.
- No evidence ref accepted without current authorization.

## Open decisions

- Whether semantic/vector recall is a later additive PRD or part of a controlled second
  rollout after lexical launch.
- Whether the optional product search route belongs in this PR or remains runtime-only.
