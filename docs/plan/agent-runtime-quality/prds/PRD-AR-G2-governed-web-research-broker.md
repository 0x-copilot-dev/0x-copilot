# PRD-AR-G2 — Governed web research broker

**Goal.** Replace the single-provider search helper with a provider-neutral,
policy-governed search and extraction broker that produces bounded, replayable,
citation-ready public-web evidence without granting the agent arbitrary network access.

| Field           | Value                                                                                                                                                          |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Status          | Draft for review                                                                                                                                               |
| Primary owner   | `ai-backend` agent capabilities                                                                                                                                |
| UI impact       | Existing web-search run control; additive source metadata                                                                                                      |
| Runtime rollout | `WEB_RESEARCH_BROKER_MODE`: off → shadow → on                                                                                                                  |
| Depends on      | A3 Operation Gateway, B3 presentation lifecycle, D2 built-ins/subagents, D4 browser adapter, E1 accountability/lifecycle, AR-F5 context budget/evidence reader |

## Implementer brief

Read before implementation:

1. `../README.md`.
2. `../../prds/PRD-A3-operation-gateway.md`.
3. `../../prds/PRD-D2-builtins-subagents.md`.
4. `../../prds/PRD-D4-browser-adapter.md`.
5. `../../prds/PRD-E1-accountability-lifecycle.md`.
6. `services/ai-backend/src/runtime_worker/dependencies.py`.
7. `services/ai-backend/src/agent_runtime/capabilities/retrying_tool.py`.
8. `services/ai-backend/src/agent_runtime/capabilities/citation_capturing_tool.py`.
9. `services/ai-backend/src/agent_runtime/capabilities/citation_projection.py`.
10. `services/ai-backend/src/agent_runtime/capabilities/tool_budget_guard.py`.
11. `services/ai-backend/src/agent_runtime/capabilities/operations/builtin_operation_catalog.json`.
12. `services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py`.
13. `services/ai-backend/src/agent_runtime/api/run_coordinator.py`.
14. `services/ai-backend/src/runtime_api/schemas/workspace_defaults.py`.

Do not move authenticated browsing, file upload, form submit, or click automation into
this broker. D4 owns those capabilities and their external-effect protocol.

## Desktop-first deployment and storage contract

The launch composition is the shipped `single_user_desktop` profile: Electron main supervises `backend`, `ai-backend`, `backend-facade`, and the local data services on loopback; the renderer still calls only the facade. This is a local application boundary, not a mandatory cloud dependency.

- Runtime-owned run/event/citation/artifact state must use existing `RuntimePorts`. Desktop defaults to the single-writer file adapter under `<userData>/agent-data/v1` with the in-process worker; tests use in-memory and future hosted deployments may use Postgres.
- Product configuration already owned by `backend` may use its existing embedded local Postgres database. This PRD must not add another database, daemon, queue, or network hop merely for desktop.
- Authorization is expressed as the signed-in local user, device capability grants, project/conversation scope, and provider credentials. Existing internal organization identifiers remain compatibility partition keys; they are not exposed as B2C team or administrator concepts.
- A future consumer web or opt-in sync product may add remote adapters behind the same ports and contracts. Sync, team administration, fleet policy, and always-online availability are not desktop launch dependencies.
- Feature flags resolve locally, work offline wherever no external provider is intrinsically required, and include a bounded disk/CPU/memory budget plus an immediate local backout path.

## Problem statement

The runtime currently exposes a retry-wrapped DuckDuckGo results tool. It is useful but
does not establish a complete web-research boundary:

- provider output shapes and error semantics remain provider-specific;
- search snippets cannot reliably ground detailed claims;
- there is no governed page extraction operation;
- broad exception retries can repeat permanent failures;
- outbound URL, redirect, DNS, content-type, size, and decompression policy are not one
  explicit contract;
- evidence freshness and source identity are not replayable runtime concepts.

A general-purpose HTTP client would create a larger problem by letting model-supplied
URLs reach private networks or authenticated endpoints.

## Current implementation and predecessor contracts

- **[shipped]** Per-run and workspace-default web-search controls already omit the tool
  when disabled.
- **[depends on]** A3 descriptors classify `web_search` as a read/pure built-in.
- **[shipped]** Tool budgets, retry wrappers, citation ordinals, subagent checkpoints,
  and persisted operation events already exist.
- **[depends on]** D4 defines a separate, stronger boundary for browser sessions and
  side effects.
- **[shipped]** BYOK/provider configuration and shared HTTP-pool patterns exist in the
  services.

## Objectives

1. Normalize search and page extraction across providers.
2. Enforce one SSRF/redirect/content policy before every fetch.
3. Return concise evidence with canonical public URLs and stable citations.
4. Minimize model round-trips through batched search/open operations.
5. Preserve honest freshness, provider, and extraction limitations.
6. Support deterministic replay without refetching.

### Success measures

- Zero private/reserved/link-local/loopback fetches in the DNS rebinding suite.
- At least 95% citation URL resolution on the web-research evaluation set.
- Search p95 below 5 seconds and extraction p95 below 8 seconds excluding provider-wide
  incidents.
- Median model-visible web payload below 16 KiB per research task.
- At least 25% fewer search invocations than the existing tool on multi-source tasks
  without a decline in answer correctness.

## Non-goals

- Authenticated/private browsing, cookies, login, client-side application automation,
  downloads, uploads, or submissions.
- Arbitrary HTTP methods, headers, request bodies, JavaScript execution, or local file
  URLs.
- Bypassing publisher access controls, paywalls, robots policy, or provider terms.
- Treating search rank as source authority.

## Interfaces consumed

- A3 Gateway, operation descriptors, and result disposition.
- Existing run-level `web_search_enabled` policy and tool budget.
- Backend-owned, versioned local-user web-research policy snapshot.
- Citation ledger/source projection and large-result offload.
- Shared bounded HTTP pool and provider-key resolution.
- D4 browser adapter for explicit handoff when a page requires browser context.

## Interfaces exposed

### Backend policy snapshot

The locally supervised `backend` remains the canonical owner of user settings,
credential assignment, provider disclosure, and policy revisions. `ai-backend`
consumes the snapshot over authenticated loopback HTTP:

```text
GET /internal/v1/web-research/policy-snapshot
GET /v1/settings/web-research-policy
PUT /v1/settings/web-research-policy

WebResearchPolicySnapshot
  policy_revision: string
  enabled: bool
  allowed_operations: search | extract[]
  provider_routes[]
    provider_class
    credential_pool_class
    allowed_regions[]
    fallback_order
  egress_policy_revision: string
  allowed_schemes[]
  allow_domains[], deny_domains[]
  allow_user_supplied_urls: bool
  robots_and_terms_mode
  query_disclosure_notice_revision
  cache_policy
    search_ttl_seconds
    extraction_ttl_seconds
    cross_user_cache: forbidden | metadata_only
  budgets
    max_queries_per_call
    max_sources_per_call
    max_calls_per_run
    max_bytes_per_run
  issued_at, expires_at
```

The endpoint derives profile/user from verified service headers; the request body carries no
identity or policy override. The response is signed or transported over the existing
trusted service channel and validated against a closed schema. A missing, expired, or
unknown policy revision fails closed and removes web tools from the run.
Apps access the public settings routes only through the facade. Mutation requires the
signed-in user, expected policy revision, idempotency key, a local audit record, and a
secret-free diff; provider credentials remain token-vault references rather than
policy-body values.

### Domain ports

```text
WebSearchProvider
  search(request: ProviderSearchRequest) -> ProviderSearchResponse

WebExtractProvider
  extract(request: ProviderExtractRequest) -> ProviderExtractResponse

WebNetworkPolicy
  authorize_url(url, resolution_context) -> AuthorizedPublicTarget

WebEvidenceStore
  put(run_scope, evidence) -> WebEvidenceRef
  get(run_scope, ref) -> WebEvidence
```

Provider SDKs and HTTP implementation remain in runtime adapters. Domain contracts do
not import them.

### Model-visible tools

```text
web_search(
  queries: string[],                    # 1..3
  recency_days?: int,
  domains?: string[],                   # max 10
  exclude_domains?: string[],           # max 10
  results_per_query?: int               # default 5, max 10
)

web_extract(
  sources: WebSourceRef[],              # max 6, issued by search
  focus?: string                        # <= 500 chars
)
```

Direct user-provided HTTPS URLs may be converted into a source ref only through a
separate validation path that applies the same network policy. The model cannot mint a
ref.

```text
WebSearchHit
  source_ref: string                    # opaque, run/user scoped
  canonical_url: string
  display_domain: string
  title: string
  snippet: string
  published_at?: datetime
  retrieved_at: datetime
  provider_rank: int

WebEvidence
  source_ref: string
  canonical_url: string
  title: string
  extracted_text: string
  content_digest: sha256
  published_at?: datetime
  retrieved_at: datetime
  extraction_method: provider | html_readability | text
  truncation: none | byte_limit | content_limit
  warnings: string[]
```

### Events

```text
web.research.searched.v1
web.research.extracted.v1
web.research.blocked.v1
web.research.degraded.v1
```

Events include operation id, provider class, counts, duration, outcome, and source-ref
digests. Query text, full URLs, page text, and provider exception messages are excluded.

## Design

### D1. Broker pipeline

```text
tool request
  → A3 Gateway/read descriptor
  → per-run policy and budget
  → provider router
  → normalized search results
  → source-ref issuance
  → optional extraction through network policy
  → content normalization and bounds
  → citation registration
  → operation result
```

Run assembly snapshots the exact `WebResearchPolicySnapshot.policy_revision`.
Every search/extract operation rechecks that the snapshot remains usable and intersects
it with run tool budgets; neither prompts nor tool arguments may select provider,
credential class, region, disclosure posture, egress rules, or cache policy. Emergency
disable and credential revocation take effect immediately through a generation check.

Provider routing considers configured credentials, locale/recency support, health,
price, and the user's allowlist. It does not silently broaden domain filters. A fallback
provider receives the same normalized request and only runs when the first attempt is
known not to have produced a result.

### D2. Network safety

Every extraction:

1. accepts only `https` and, if deployment policy allows, `http`;
2. rejects credentials, fragments, non-standard ambiguous encodings, and overlong URLs;
3. resolves all addresses and rejects loopback, private, carrier-grade NAT, link-local,
   multicast, documentation, reserved, and metadata-service ranges;
4. pins the authorized address set for the connection;
5. revalidates every redirect with a maximum of five hops;
6. refuses scheme downgrade and cross-origin credential forwarding;
7. enforces connection, first-byte, total-time, compressed-byte, expanded-byte, and
   redirect limits;
8. accepts only configured textual media types;
9. never uses browser cookies, host credentials, proxy credentials, or ambient auth.

DNS validation and connection establishment must be coupled closely enough to resist
rebinding. URL strings and resolved IPs never appear in logs.

### D3. Search normalization and de-duplication

Canonicalization removes tracking parameters only from a closed reviewed list, retains
semantic query parameters, normalizes host casing/IDNA, and follows declared canonical
links only after network validation.

Hits de-duplicate by canonical URL and content identity while retaining query coverage.
The response reports which query produced each hit internally, but model output avoids
duplicated snippets.

### D4. Extraction

Extraction prefers a configured provider, then bounded parsing inside the local
ai-backend process.
It drops scripts, styles, navigation repetition, hidden nodes, and forms. It never
executes JavaScript. PDF/office/media extraction is not performed by this initial
broker; such sources return a typed unsupported-media result or flow to an approved
artifact pipeline.

Extracted content is untrusted evidence. Prompt wrappers make that boundary explicit.
The broker records exact content digest and retrieval timestamp.

### D5. Batching and stopping

One search tool call accepts up to three distinct queries and one extraction call opens
up to six sources. The runtime de-duplicates identical requests within a run. Existing
per-tool budgets remain hard limits; the harness should stop after evidence is adequate
or two consecutive batches add no new source/content digest.

Independent search provider requests may run concurrently up to the per-run and
process-wide limit. Work remains `O(q + u)` provider operations for `q` queries and `u`
selected URLs; concurrency reduces wall time, not total work.

### D6. Retry and fallback

Retry only typed transient failures: connect timeout, reset, provider 429/5xx with
bounded `Retry-After`, and explicitly retryable provider codes. Validation blocks,
4xx requests, unsupported media, policy denial, and oversized content are never
retried.

An ambiguous provider timeout after a search is safe to retry because search is a read.
Extraction retries only GET and never crosses the source-ref policy boundary.

### D7. Evidence and citations

Each search hit receives a source ref. Extraction upgrades the source with a digest and
bounded body. Citation ordinals point to the canonical URL, title, retrieval timestamp,
and content digest. Replay uses the persisted evidence ref; it never refetches a page
and relabels new content as old evidence.

### D8. Cache

- Search cache: maximum five minutes; key includes provider, normalized query digest,
  locale, recency, domain filters, policy generation, and credential-pool class.
- Extract cache: content-addressed, maximum one hour by default; disabled for responses
  marked private/no-store or requests with user-specific URL tokens.
- Direct URL query strings are treated as sensitive and never used as metric labels.
- Cross-profile cache sharing is disabled initially. It may be revisited only with a
  privacy review and proof that stored material is public and credential-free.

### D9. Browser handoff

If extraction requires authentication, JavaScript, a protected user gesture, download,
or interaction, the result says `browser_required`. The agent may use D4 only when the
browser capability is present. The broker never copies cookies or session state into
its HTTP client.

## Persistence, retention, and deletion

- Run-scoped evidence stores normalized metadata, bounded extracted text, digest, and
  retrieval timestamp behind a payload ref.
- Public runtime events carry refs/digests only.
- Evidence follows run/event retention and is deleted with user history. An optional
  backup/sync adapter may retain an inaccessible encrypted copy under its separate
  user-visible policy.
- Search/extraction caches expire independently and are never backup-retention records.
- Provider request ids may be retained in restricted operational records, never public
  receipts.
- Run records retain only the policy revision and safe provider class needed for
  replay/audit. Canonical policy snapshots and their retention are backend-owned;
  `ai-backend` does not create a second policy store.
- Deletion covers evidence payloads, cache entries keyed by user/run, citation locators,
  and pending provider work.

## Authorization, privacy, supply chain, and compliance

- The run's verified profile/user and policy select provider credentials; request
  bodies cannot choose another saved credential.
- Provider adapters are allowlisted, pinned dependencies with license/terms review.
- Search queries may contain confidential intent. They are sent only to the selected
  provider, not logged, and subject to user policy disclosure.
- Egress allow/deny policy is deployment-controlled and immutable from model content.
- Page content is untrusted; it cannot change runtime/tool/approval policy.
- Secrets, full URLs, queries, and extracted text are redacted from telemetry and audit.
- A user who disables web research sees no web tools.
- Policy cache keys include local profile, user-policy visibility, policy revision, and
  expiry. Cache reuse across profiles or credential-pool classes is forbidden.

## Performance and capacity

- Queries per call: 3; results per query: 10; extracted sources per call: 6.
- Search deadline 8 seconds; extraction per source 10 seconds; batch deadline 15
  seconds; all configurable downward by local profile.
- Compressed response max 5 MiB, expanded response max 10 MiB, extracted text max
  32 KiB/source, model-visible batch max 48 KiB.
- Per-run extraction concurrency 3; process concurrency controlled by a shared
  semaphore and connection pool.
- Cache hit, provider latency, DNS/redirect time, parsing time, and prompt bytes are
  separately measured.

## Failure, idempotency, and recovery

- Tool calls carry deterministic operation/idempotency ids; duplicate completion folds
  to one persisted result.
- Partial batch results are returned with per-item typed errors.
- Cancellation closes provider requests, releases semaphores, and persists no late
  result.
- Worker crash after evidence storage but before event emission reconciles by operation
  id and emits once.
- Provider outage yields typed unavailable status and optional configured fallback.
- Policy fetch failure, expiry, unknown provider class, or revision mismatch fails
  closed; fallback is allowed only when explicitly ordered in the pinned snapshot.
- Replay reads stored evidence and never performs network I/O.

## Metrics and alerts

- `web_research_requests_total{operation,provider_class,outcome}`
- `web_research_duration_ms{operation,provider_class}`
- `web_research_cache_total{operation,result}`
- `web_research_sources_returned`
- `web_research_evidence_bytes`
- `web_research_policy_blocks_total{reason}`
- `web_research_citation_resolution_rate`
- `web_research_duplicate_source_rate`

Alert on sustained provider errors, policy-block spikes, extraction size-limit spikes,
and citation resolution regression. Labels must be low-cardinality and content-free.

## Rollout and backout

1. Land contracts, providers, network-policy tests, and fake adapters with tools absent.
2. Shadow normalized search beside the existing helper without additional provider
   calls; compare shaping against captured test fixtures.
3. Enable broker search for local dogfood profiles while extraction remains off.
4. Enable extraction for an allowlisted set of public domains.
5. Expand domain/provider coverage and retire the old registry path.

Backout removes broker tools and restores the existing search registry while it remains
supported. Stored evidence remains readable until ordinary retention. No external
effect or migration requires undo.

## Implementation slices

1. Add normalized contracts, typed errors, provider ports, and fakes.
2. Implement URL/DNS/redirect/content network policy.
3. Add search provider adapter and routing/fallback.
4. Add extraction adapter and bounded normalizer.
5. Add run-scoped evidence store and citation source type.
6. Register A3 descriptors, tools, budgets, and prompt guidance.
7. Add cache, metrics, replay/reconciliation, and shadow comparison.
8. Run adversarial network and answer-quality launch suites.

## Test plan

### Network/security

- IPv4/IPv6 private ranges, decimal/octal/hex IP forms, mixed IDNA, credentials,
  redirect chains, DNS rebinding, metadata endpoints, proxy/ambient credentials.
- Compression bombs, chunked endless responses, slowloris, huge headers, MIME lies,
  unsupported media, and malformed HTML.
- Prompt injection cannot alter tools, credentials, approval, or provider policy.
- Forged body identity/provider/region/cache settings and stale-policy reuse are denied.
- Emergency disable and credential revocation invalidate in-flight reuse before fetch.

### Provider and correctness

- Normalization fixtures for every adapter.
- Typed retry/fallback, rate limit, empty results, duplicated URLs, recency/domain
  filters, canonical URL semantics.
- Search→extract citation retains exact digest/retrieval time.

### Runtime/recovery

- Web-disabled run has no tools and no outbound call.
- Batch partial success, timeout, cancellation, duplicate delivery, worker crash,
  persisted replay.
- Subagent capability remains an intersection of parent policy.

### Performance and quality

- Concurrency and connection-pool bounds under load.
- Search-call count, source diversity, citation correctness, factual accuracy,
  freshness, unsupported-claim rate, prompt bytes, and end-to-end latency.

## Definition of done

- [ ] Web search and extraction use one provider-neutral governed broker.
- [ ] Every fetch passes DNS/redirect/content policy and carries no ambient auth.
- [ ] Results are bounded, evidence-pinned, citation-ready, and replayable.
- [ ] Browser/authenticated interactions remain exclusively behind D4.
- [ ] Typed retries do not repeat permanent failures.
- [ ] Privacy, performance, security, and answer-quality launch gates pass.
- [ ] The existing helper is retired for enabled cohorts without duplicate calls.

## Guardrails

- No arbitrary HTTP tool.
- No cookies, browser profile, local network, or ambient credentials.
- No extraction without redirect-by-redirect policy validation.
- No raw page body in events or logs.
- No claim that a snippet proves content that was not extracted.
- No browser side effect through the research broker.

## Open decisions

- Initial provider set and user-facing data-processing disclosure.
- Whether `http` is disabled globally or allowed only for an explicit domain allowlist.
