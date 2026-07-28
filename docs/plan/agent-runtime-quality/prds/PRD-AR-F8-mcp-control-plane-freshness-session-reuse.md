# PRD-AR-F8 — MCP control-plane freshness and session reuse

**Status:** implemented\
**Priority:** P2\
**Owners:** Connector Platform, Backend, AI Runtime, Reliability\
**Depends on:** [D1 MCP convergence](../../generative-surfaces-v2-1/prds/PRD-D1-mcp-convergence.md)\
**Integrates with:** [F3 capability discovery](PRD-AR-F3-policy-aware-capability-discovery.md)

## Goal

Keep MCP descriptors fresh and connections warm without crossing profile/user scope,
duplicating control-plane work, or moving credential/transport ownership into the
wrong service.

## Implementer brief

Read:

1. `services/ai-backend/src/agent_runtime/capabilities/mcp/discovery_cache.py`.
2. `services/ai-backend/src/agent_runtime/capabilities/mcp/loader.py`.
3. `services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py`.
4. `services/ai-backend/src/runtime_worker/dependencies.py`.
5. `services/backend/src/backend_app/mcp_catalog.py`.
6. `services/backend/src/backend_app/mcp_oauth.py`.
7. `services/backend/src/backend_app/connectors/`.
8. D1 and the root service-boundary rules.

The current ai-backend cache is process-wide TTL/LRU, keyed by server/profile/user, returns
defensive copies, single-flights cold loads, and invalidates after reauthentication.
Default TTL is 900 seconds with 1,000 entries. Preserve these strengths. First verify
backend client/session pooling before assigning new transport ownership.

## Desktop-first deployment and storage contract

The launch composition is the shipped `single_user_desktop` profile: Electron main supervises `backend`, `ai-backend`, `backend-facade`, and the local data services on loopback; the renderer still calls only the facade. This is a local application boundary, not a mandatory cloud dependency.

- Runtime-owned run/event/citation/artifact state must use existing `RuntimePorts`. Desktop defaults to the single-writer file adapter under `<userData>/agent-data/v1` with the in-process worker; tests use in-memory and future hosted deployments may use Postgres.
- Product configuration already owned by `backend` may use its existing embedded local Postgres database. This PRD must not add another database, daemon, queue, or network hop merely for desktop.
- Authorization is expressed as the signed-in local user, device capability grants, project/conversation scope, and provider credentials. Existing internal organization identifiers remain compatibility partition keys; they are not exposed as B2C team or administrator concepts.
- A future consumer web or opt-in sync product may add remote adapters behind the same ports and contracts. Sync, team administration, fleet policy, and always-online availability are not desktop launch dependencies.
- Feature flags resolve locally, work offline wherever no external provider is intrinsically required, and include a bounded disk/CPU/memory budget plus an immediate local backout path.

## Problem and current strengths

MCP control-plane work includes connection initialization, authentication/session
setup, and listing tools/resources. The current cache avoids repeating this work on
ordinary hits and prevents same-key thundering herds. It has bounded staleness and no
negative caching.

Remaining concerns are:

- descriptor changes are observed at TTL expiry rather than through a revision signal;
- each process owns its own cache;
- cold loads may still repeat transport handshakes depending on backend pooling;
- large descriptor revisions must invalidate F3 catalogs promptly;
- stale calls need a consistent refresh/retry rule.

## Objectives

1. Instrument cold discovery and warm-use costs by phase.
2. Define an authenticated scoped MCP descriptor revision/invalidation contract.
3. React to tool-list/auth/config changes without transporting secrets or raw schemas in
   invalidation events.
4. Verify and, if needed, add backend-owned client/session pooling.
5. Preserve per-user/profile credential and authorization isolation.
6. Keep TTL/LRU as a safety backstop.
7. Add a distributed descriptor cache only when measured process churn justifies it.

## Non-goals

- Model-facing capability ranking or schema deferral; F3 owns that.
- Moving backend token vault, OAuth, registry, or transport clients into ai-backend.
- Sharing live sessions across users unless the connector/auth contract explicitly
  defines safe shared identity.
- Caching tool execution results.
- Assuming MCP notifications are reliable delivery.
- Making tool calls parallel; F6 owns execution concurrency.

## Interfaces consumed

- Backend MCP registry, OAuth/token vault, client/proxy, server configuration, and
  verified internal service identity.
- ai-backend `McpDiscoveryCache`, loader, card authorization, and F3 catalog revisions.
- D1 descriptor classification and operation path.

## Interfaces exposed

```text
McpDescriptorRevision
  server_id
  profile_id
  subject_scope_hash
  revision
  tool_count
  resource_count
  descriptor_digest
  observed_at
  source: initial_list | notification | auth_change | config_change | ttl_refresh

McpRevisionFeedItem
  cursor
  notice_id
  server_id
  profile_id
  subject_scope_hash?
  old_revision?
  new_revision?
  reason
  occurred_at

McpClientLease
  lease_id
  server_id
  credential_subject
  transport_kind
  session_revision
  expires_at
```

Ports:

- backend `McpRevisionFeed`.
- ai-backend `McpInvalidationConsumer`.
- backend `McpClientPool.acquire(context)`.
- `McpDiscoveryMetrics`.

Internal endpoints:

- `GET /internal/v1/mcp/servers/{id}/revision`
- `GET /internal/v1/mcp/descriptor-revisions?after_cursor=...&limit=...`

Feed items carry revisions and scope hashes only. Descriptor bodies remain behind existing
authorized internal APIs.

## Detailed design

### 1. Measurement first

On cache miss, record separately:

- card/authorization validation;
- client/session acquisition;
- initialize/connect;
- `list_tools` pages/bytes/count;
- `list_resources` pages/bytes/count;
- descriptor validation;
- cache insertion.

Record cache hit/miss/expiry/eviction/single-flight wait, process role, and stale-tool
response. Backend records client lease reuse, reconnect, keepalive, and pool saturation.

### 2. Revision source

The locally supervised backend owns authoritative server configuration and proxy
transport. It computes a monotonic view revision per
`(server, local_profile, credential_subject, tool_filter_policy)` from canonical
descriptor metadata in its existing persistence adapter. A separate server configuration revision lets broad config
changes invalidate every affected view. Sources include:

- successful initial/periodic discovery;
- supported tool-list change notification;
- OAuth credential subject change or reauthentication;
- user server/config/tool-filter change;
- client/session replacement where descriptor view can change.

Notification handling debounces bursts, single-flights refresh, and pages the complete
list before publishing a revision.

### 3. Invalidation

Backend commits a revision row and durable feed/outbox record in the same transaction.
On desktop, the single ai-backend process polls that feed over authenticated loopback
in the already-supported ai-backend-to-backend direction. Feed pages are
cursor-ordered, at-least-once, bounded, and safe to replay. The consumer verifies the
per-boot service token and profile/scope,
then:

1. evicts matching discovery entries;
2. invalidates matching F3 catalog revisions;
3. records the highest observed revision;
4. does not eagerly fetch descriptors unless active demand justifies it.

TTL remains a convergence backstop for lost feed items.

The process stores its highest cursor beside the process-local cache. A restart may
start from the current feed tail because its cache is empty. If a cursor falls behind
feed retention, backend returns `cursor_expired`; the consumer flushes affected
descriptor/catalog caches and resumes from a fresh cursor. On-demand revision checks
bound correctness when the poller is unhealthy. Backend never calls a private
ai-backend route.

### 4. Client/session pooling

Pooling remains in the locally supervised backend because it already owns credentials
and MCP proxy transport; Electron main and the renderer never receive connector
credentials.
Pool key includes server, credential subject, transport/config revision, and any
connector-required session scope.

Leases enforce:

- idle and maximum lifetime;
- keepalive using the least expensive supported method;
- bounded per-server/process counts;
- health check and exponential reconnect;
- clean close on auth/config revision;
- no cross-subject reuse.

For stdio servers, lifecycle and memory quotas are explicit. For remote sessions,
mTLS/proxy/egress policy is retained.

### 5. Stale call handling

A descriptor/schema mismatch or unknown tool result triggers one scoped revision check
and cache eviction. A read operation may be retried once only if no provider-side work
occurred and normal idempotency/retry policy permits. Effects are never blindly retried.

## Security, local-profile boundaries, privacy, and audit

- Internal endpoints require service authentication plus trusted profile/user headers.
- Caller-supplied scope hashes/revisions are not trusted without an authenticated feed
  item or direct backend revision response.
- Cache/pool keys include credential subject; defensive copies prevent mutation leaks.
- Notices contain no token, endpoint secret, raw descriptor, or user data.
- TokenVault remains backend-owned; ai-backend never receives plaintext credentials.
- Server config, auth, revision, invalidation, and user pool-policy changes are
  audited.
- Retention covers diagnostic metrics/feed items; credentials follow vault deletion.

## Performance and complexity budgets

- Warm descriptor lookup p95 below 5 ms in-process.
- Invalidation handling p95 below 100 ms after feed-item receipt.
- Target cache hit rate at least 90% for repeatedly used stable servers.
- Revision convergence target below 5 seconds where notification support exists; TTL is
  the hard fallback.
- Per-key discovery remains single-flight.
- Pool size is bounded globally and per server/credential subject.
- List processing is `O(total descriptor bytes)`; no keepalive may routinely download a
  full tool list.

## Failure, idempotency, and recovery

- Feed idempotency uses `(notice_id, revision)`; stale/lower revisions are ignored.
- Lost/duplicate/out-of-order feed items converge through highest revision and TTL.
- Failed discovery is not cached as a successful empty server.
- Client pool failure returns structured connector unavailability; no alternate
  credential subject is used.
- Process restart rebuilds local cache and reconnects on demand.
- Backend restart invalidates leases; revision state is durable if the registry store is
  durable.
- A distributed cache is not part of desktop launch. A future hosted adapter is
  optional and cannot become the sole source of truth.

## Observability and quality gates

Local diagnostics and exportable development metrics:

- discovery hit/miss/expiry/eviction by process/server;
- cold latency by phase, descriptors count/bytes;
- single-flight wait and thundering-herd prevention;
- feed items received/rejected/lagged and convergence delay;
- pool reuse, lease age, reconnect, keepalive, saturation;
- stale-tool/schema errors and retry outcomes;
- F3 catalog invalidations and rebuilds.

Alerts cover invalid service authentication, cross-scope feed items, persistent stale
errors, pool leak, discovery backlog, and excessive descriptor churn.

## Rollout and backout

1. Land phase metrics with no lifecycle change.
2. Audit backend transport creation/reuse and document current ownership.
3. Add durable revision to backend discovery responses.
4. Publish and pull auth/config invalidations through the durable revision feed.
5. Add tool-list notification refresh where supported.
6. Add or tune backend client pooling based on measurements.
7. Consider a shared encrypted descriptor cache only after a capacity review.

Backout disables feed consumption and pooling changes independently. Existing TTL/LRU
cache and on-demand backend proxy path remain the safe fallback.

## Implementation slices

1. Metrics and latency-phase instrumentation.
2. Revision contracts and golden cross-service fixtures.
3. Backend revision persistence and cursor feed.
4. ai-backend consumer, scoped eviction, and F3 invalidation.
5. Notification/debounce/full-refresh support.
6. Backend client-pool adapter and lifecycle controls.
7. Failure injection, dashboards, and runbook.

## Test plan

- Existing TTL/LRU profile/user keying and defensive copy tests remain.
- Concurrent cold callers perform one discovery.
- Tool-list change publishes one debounced higher view revision and evicts matching
  entries.
- Out-of-order/duplicate/lost feed-item behavior converges.
- Expired feed cursor flushes affected process-local caches and resumes without a
  backend-to-ai-backend callback.
- Reauthentication invalidates old credential-subject leases and descriptors.
- Forged service/scope feed response is rejected and audited.
- Pool never reuses a client across credential subjects.
- Process/backend restart recovers on demand.
- Large paginated tool list is processed once; keepalive does not refetch it.
- Stale read follows one safe refresh; effect mismatch is not retried.

## Definition of done

- Control-plane latency and cache/session reuse are measurable by phase.
- Descriptor changes converge through the authenticated pull feed, on-demand revision
  checks, and TTL fallback.
- ai-backend cache and F3 catalogs invalidate together.
- Credential/transport ownership remains in backend with tested subject isolation.
- Warm reuse lowers cold-handshake frequency without stale-tool or security regression.
- Independent kill switches, dashboards, and operational runbook are complete.

## Guardrails and open decisions

Guardrails:

- Do not add Redis merely because multiple processes exist; require measured need.
- Do not transmit raw schemas or secrets in invalidation events.
- Do not pool across credential subjects.
- Do not use list-tools as a high-frequency keepalive.
- Do not add a backend-to-ai-backend private callback; retain the documented internal
  request direction.

Open decisions:

1. Which MCP transports/providers expose trustworthy tool-list notifications?
2. What pool ownership exists today in the backend proxy path?
3. What revision-feed retention and poll interval meet measured process count and
   convergence needs?
