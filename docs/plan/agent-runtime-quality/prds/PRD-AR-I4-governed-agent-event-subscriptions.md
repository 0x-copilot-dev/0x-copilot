# PRD-AR-I4 — Governed agent lifecycle event subscriptions

**Goal:** Offer an optional, user-configured desktop integration that delivers
signed, redacted, replayable lifecycle events while the app is running, with
bounded local retries and no exposure of secrets or private reasoning.

## Metadata

| Field                   | Value                                                                                                                                                                                                |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Status                  | Optional after core desktop runtime-quality launch                                                                                                                                                   |
| Wave                    | I — durable agent operations                                                                                                                                                                         |
| Primary owner           | Backend subscription and delivery domain                                                                                                                                                             |
| Supporting owners       | AI backend event relay, backend facade, security and compliance                                                                                                                                      |
| Depends on              | [D2 builtins and subagents](../../generative-surfaces-v2-1/prds/PRD-D2-builtins-subagents.md), [E1 accountability lifecycle](../../generative-surfaces-v2-1/prds/PRD-E1-accountability-lifecycle.md) |
| Integrates with         | [I3 durable work items](./PRD-AR-I3-durable-agent-work-items.md) when work-item lifecycle events are enabled                                                                                         |
| Primary success measure | Every eligible lifecycle event is either delivered once logically or reaches an inspectable terminal delivery state, and no payload violates its redaction policy                                    |

## Implementer brief

Read:

- `services/ai-backend/src/agent_runtime/persistence/`
- `services/ai-backend/src/agent_runtime/observability/`
- `services/ai-backend/src/runtime_api/`
- `services/ai-backend/src/runtime_worker/`
- `services/backend/src/backend_app/audit_reader.py`
- `services/backend/src/backend_app/routes/audit_list.py`
- `services/backend/src/backend_app/routes/audit_export.py`
- `services/backend/src/backend_app/token_vault.py`
- `services/backend/src/backend_app/`
- `services/backend-facade/src/backend_facade/`
- `packages/api-types/`
- `packages/chat-surface/`
- I3, D2, and E1

This product sends lifecycle events outward. It does not accept event payloads as agent instructions and does not trigger routines, goals, runs, tools, or work items. Inbound routine webhooks remain an I1 concern and require their own target-specific authentication and policy checks.

Desktop delivery is not an always-on webhook service. It is disabled by
default, is visibly paused when the app is closed/offline/suspended, and may
resume only queued local deliveries on the next app boot. Cloud relay,
guaranteed background availability, and inbound internet triggers are deferred
to a future opt-in hosted B2C offering.

AI backend remains authoritative for persisted run event sequence. Backend owns user subscription records, endpoint secrets, redaction policy, fan-out, delivery outbox, retry, dead-letter queue, replay authorization, and audit.

## Desktop-first deployment and storage contract

The launch composition is the shipped `single_user_desktop` profile: Electron main supervises `backend`, `ai-backend`, `backend-facade`, and the local data services on loopback; the renderer still calls only the facade. This is a local application boundary, not a mandatory cloud dependency.

- Runtime-owned run/event/citation/artifact state must use existing `RuntimePorts`. Desktop defaults to the single-writer file adapter under `<userData>/agent-data/v1` with the in-process worker; tests use in-memory and future hosted deployments may use Postgres.
- Product configuration already owned by `backend` may use its existing embedded local Postgres database. This PRD must not add another database, daemon, queue, or network hop merely for desktop.
- Authorization is expressed as the signed-in local user, device capability grants, project/conversation scope, and provider credentials. Existing internal organization identifiers remain compatibility partition keys; they are not exposed as B2C team or administrator concepts.
- A future consumer web or opt-in sync product may add remote adapters behind the same ports and contracts. Sync, team administration, fleet policy, and always-online availability are not desktop launch dependencies.
- Feature flags resolve locally, work offline wherever no external provider is intrinsically required, and include a bounded disk/CPU/memory budget plus an immediate local backout path.

## Problem statement

Users and consumer integrations need agent activity in their own automation, monitoring, compliance, and operations systems: a run started, a tool was requested, an approval is waiting, a subagent changed state, a work item completed, or an artifact became available. Polling run APIs is inefficient and loses timely operational context.

Sending raw internal events is unacceptable. Runtime events can contain model text, tool arguments, connector data, artifact metadata, error details, and identifiers with different sensitivity. Webhook endpoints fail, retry, rotate secrets, and can be misconfigured or taken over. Without a durable delivery ledger, signed envelopes, stable schemas, deterministic redaction, and replay controls, users cannot rely on the feed and the product cannot prove what left its boundary.

## Current state and strengths to preserve

- AI backend persists typed runtime events with monotonic `sequence_no` per run.
- Event replay and resumable SSE recover from transient client disconnects.
- Runtime events already project explicit activity kind, display title, summary, and status rather than deriving semantics from string prefixes.
- Runs, tools, subagents, approvals, and artifacts have stable identifiers and lifecycle events.
- Backend already owns local profile identity, vault-backed secrets, audit, and product-facing policy.
- The facade is the only public API entry point.

## Objectives and outcomes

1. Offer a versioned catalog of safe outbound lifecycle event types.
2. Let authorized users create scoped endpoint subscriptions and verify endpoint ownership.
3. Relay persisted AI-backend lifecycle events to the backend without loss or duplicate logical fan-out.
4. Apply deterministic, event-specific redaction before payload persistence and delivery.
5. Sign every delivery and support user-side replay protection.
6. Retry transient failures with backoff and move permanent failures to a visible dead-letter state.
7. Support authorized replay without altering original delivery history.
8. Measure end-to-end lag, redaction conformance, delivery health, and user acknowledgement.

## Scope

- Run, tool, approval, subagent, work-item, artifact, and operation lifecycle event families
- Subscription creation, endpoint verification, scope/filter selection, secret rotation, disable, and delete
- AI-backend relay cursor, backend ingestion, fan-out, delivery outbox, retry, DLQ, and replay
- Signed/redacted CloudEvents-style envelopes with schema versions
- Per-subscription ordering within a run and documented cross-run behavior
- Delivery history, diagnostics, retention, deletion, audit, and quotas

## Non-goals

- Inbound event-triggered agents, routines, goals, or work items
- A core desktop-launch dependency, always-on delivery daemon, or 24/7 SLA
- A bidirectional event bus or workflow engine
- Exporting raw prompts, chain-of-thought, plaintext secrets, access tokens, unredacted tool arguments, or full connector payloads
- Global total ordering across runs or local profiles
- Exactly-once transport over HTTP
- user-authored transformation code
- Replacing the product's audit log, local audit export, SSE stream, or run event store

## Interfaces consumed

- AI-backend persisted runtime event envelopes and per-run monotonic sequence numbers
- AI-backend event replay/read APIs and typed run, tool, approval, subagent, artifact, and operation events
- Backend verified identity, local profile roles, resource authorization, token vault, and audit routes
- I3 backend work-item lifecycle projections
- Facade-only public management routing and shared API types
- E1 retention, deletion, legal-hold, audit-export, and sensitive-data requirements

## Event catalog

Initial public event types:

```text
agent.run.queued
agent.run.started
agent.run.waiting_approval
agent.run.completed
agent.run.failed
agent.run.cancelled
agent.tool.started
agent.tool.completed
agent.tool.failed
agent.approval.requested
agent.approval.resolved
agent.subagent.started
agent.subagent.completed
agent.subagent.failed
agent.work_item.ready
agent.work_item.running
agent.work_item.blocked
agent.work_item.completed
agent.artifact.available
agent.operation.committed
agent.operation.indeterminate
```

Each event type has a separately versioned public schema, sensitivity classification, redaction projector, minimum actor permission, and retention class. Internal event names are never automatically public.

## Interfaces exposed

Facade routes:

```text
GET    /v1/agent-event-types
POST   /v1/agent-event-subscriptions
GET    /v1/agent-event-subscriptions
GET    /v1/agent-event-subscriptions/{subscription_id}
POST   /v1/agent-event-subscriptions/{subscription_id}/verify
POST   /v1/agent-event-subscriptions/{subscription_id}/rotate-secret
POST   /v1/agent-event-subscriptions/{subscription_id}/enable
POST   /v1/agent-event-subscriptions/{subscription_id}/disable
DELETE /v1/agent-event-subscriptions/{subscription_id}
GET    /v1/agent-event-subscriptions/{subscription_id}/deliveries
GET    /v1/agent-event-deliveries/{delivery_id}
POST   /v1/agent-event-deliveries/{delivery_id}/replay
```

Authenticated internal relay:

```text
POST /internal/v1/agent-lifecycle-events/batch
GET  /internal/v1/agent-lifecycle-events/cursor/{consumer_id}
```

The internal route requires the per-boot internal service token plus explicit profile and user/system provenance. It is not exposed by the facade.

## Core contracts

```text
AgentEventSubscription
  subscription_id
  profile_id
  owner_id
  revision
  endpoint_url
  endpoint_origin
  verification_status
  event_types[]
  resource_scope
  public_projection_level
  signing_key_ref
  signing_key_version
  retry_policy
  status: pending_verification | active | disabled |
          degraded | revoked | deleted
  created_at
  updated_at

PublicAgentLifecycleEvent
  specversion
  id
  type
  source
  subject
  time
  datacontenttype
  schema_version
  profile_public_id
  resource_refs
  actor_class
  status
  summary
  data
  redaction_profile
  source_event_id
  source_sequence

AgentEventDelivery
  delivery_id
  subscription_id
  subscription_revision
  public_event_id
  source_event_id
  source_sequence
  payload_digest
  signing_key_version
  attempt_count
  next_attempt_at
  status: pending | delivering | delivered |
          retry_scheduled | dead_letter | suppressed | expired
  last_http_status
  last_error_code
  delivered_at
  replay_of
```

The public event ID is stable across retries. A replay has a new delivery ID and references the original delivery while retaining the same event ID plus an explicit replay header.

## Detailed design

### 1. Subscription creation and endpoint verification

The signed-in local user may create a subscription after an explicit disclosure
of the endpoint, selected event types, data projection, local queue limits,
and app-open availability. Creation requires an HTTPS endpoint and endpoint
ownership verification.

The backend validates:

- public HTTPS URL;
- no embedded credentials;
- no loopback, link-local, private-network, cloud-metadata, or disallowed port targets unless an explicit private-egress product exists;
- DNS resolution and redirect policy;
- event scopes within actor rights; and
- endpoint count and delivery quotas.

Before activation, the backend sends a short-lived challenge. The endpoint must echo the challenge under the documented protocol. Redirects are disabled for verification and delivery by default.

### 2. Source relay from AI backend

AI backend first persists its canonical runtime event. A relay reads committed events in source order from a durable cursor or outbox and sends bounded batches to the backend. Source identity is `run_id + sequence_no + event schema version`.

Backend ingestion is idempotent on local profile and source identity. It records the normalized source metadata, advances its consumer cursor only after durable acceptance, and never acknowledges a batch that has not been stored.

If a transactional relay outbox is added to AI backend, it references canonical event rows rather than copying sensitive payloads unnecessarily.

### 3. Public event mapping

A reviewed projector maps each internal event type to a public schema. The mapping:

- allowlists fields;
- converts internal IDs to scoped opaque references where required;
- replaces tool arguments/results with operation class, capability ID, status, duration, and approved safe summary;
- includes artifact references only when the subscriber may read them;
- excludes prompts, hidden messages, private reasoning, raw connector content, credentials, tokens, headers, local paths, and stack traces;
- normalizes errors to public codes; and
- bounds every string, list, and nested object.

Unknown internal fields are dropped by default. Serialization from an unrestricted internal model is prohibited.

### 4. Fan-out and durable outbox

After normalization, the backend selects active subscriptions by profile, event type, and resource scope. It applies authorization and projection policy as of delivery creation. For each eligible subscriber, the backend transaction creates one delivery outbox row with a unique constraint on subscription revision and public event ID.

Fan-out is asynchronous. Source ingestion latency is not coupled to user-configured endpoint latency.

### 5. Signing protocol

Each HTTP delivery includes:

```text
X-Agent-Event-Id
X-Agent-Delivery-Id
X-Agent-Timestamp
X-Agent-Key-Version
X-Agent-Signature
X-Agent-Replay
```

The signature covers protocol version, delivery ID, event ID, timestamp, and exact body digest using HMAC-SHA256 for the initial release. Documentation requires constant-time verification and a bounded timestamp window. users deduplicate on event ID or delivery ID according to their desired semantics.

Signing secrets are generated by the backend, stored in the token vault, shown once at creation/rotation, and never logged. Rotation supports a bounded overlap with explicit key versions.

### 6. Delivery, ordering, and acknowledgement

A delivery worker claims backend outbox rows using the backend's shared durable job infrastructure. This is not an AI runtime workload.

Success is any configured 2xx response received within the timeout. The response body is ignored and capped. Delivery ordering is preserved per subscription and run when feasible: event `N+1` does not overtake a retrying `N` until the configured ordering wait expires. Cross-run ordering is not promised.

users cannot acknowledge with commands or altered state. Response content never enters the agent harness.

### 7. Retry and dead-letter queue

Network failures, timeouts, 408, 425, 429, and 5xx responses retry with exponential backoff, full jitter, `Retry-After` bounds, and a maximum delivery age. Most other 4xx responses are permanent after a small verification retry.

After attempts or age are exhausted, delivery enters `dead_letter`. The subscription becomes `degraded` when failure-rate or oldest-undelivered thresholds are exceeded. Users receive an in-product notification and can inspect redacted diagnostics.

Retries reuse the same body bytes, event ID, payload digest, and signing key version unless key compromise policy requires an explicitly recorded re-sign.

### 8. Replay

Authorized users may replay one delivery or a bounded time/event range. Replay:

- creates new delivery records linked to originals;
- applies current endpoint health and egress protections;
- defaults to the original immutable payload and schema;
- can reproject from retained canonical source only under an explicit current-schema option;
- is rate-limited and estimated before confirmation; and
- never mutates original attempt history.

Replaying expired source content is unavailable rather than reconstructed from incomplete logs.

### 9. Disable, revoke, delete, and endpoint drift

Disable prevents new delivery claims and delivery-row creation while preserving diagnostics. Re-enable requires current authorization and endpoint checks. Owner removal or policy revocation disables the subscription automatically.

DNS is re-resolved safely on delivery. An endpoint resolving to a prohibited address fails closed and degrades the subscription. Delete revokes signing keys, cancels pending deliveries, and follows retention/legal-hold policy.

### 10. Product visibility

The subscription view shows endpoint origin, selected event types, scope, key version, verification, last success, current failure streak, oldest pending delivery, dead-letter count, and recent safe diagnostics. It never displays the signing secret after creation.

Delivery detail shows the exact public payload when the viewer is authorized, response status, timestamps, attempts, and replay lineage. It does not show user response bodies.

## Ownership and service boundaries

| Responsibility                                                 | Owner                        |
| -------------------------------------------------------------- | ---------------------------- |
| Canonical run/tool/subagent events and sequence                | AI backend                   |
| Subscription records, verification, secrets, projection policy | Backend                      |
| Fan-out, delivery outbox, retry, DLQ, replay                   | Backend                      |
| Public management API                                          | Backend facade               |
| Subscription and diagnostics UI                                | Shared chat/settings surface |
| user endpoint                                                  | user                         |

AI backend does not deliver user webhooks or store endpoint secrets. Backend does not become the source of truth for runtime event ordering. user responses never call back into agent execution.

## Persistence, retention, and deletion

- AI backend retains canonical source events under run-event policy.
- Backend PostgreSQL stores subscriptions, revisions, normalized event references, delivery rows, attempts, cursors, and audit linkage.
- Exact public payload bytes or a content-addressed encrypted reference are retained long enough for deterministic retry and authorized replay.
- user response bodies are discarded; bounded status and safe headers may be retained.
- Delete traverses subscription, secret refs, pending jobs, payload refs, replay rows, notifications, and cursors as policy permits.
- Optional retained backup may preserve public payload and delivery history while endpoint delivery stays disabled.
- Dedupe keys outlive the maximum replay/redelivery window.

## Authentication, authorization, security, and audit

- Derive local profile and actor from verified sessions; never accept identity from event data.
- Require explicit integration-management permission for create, payload view, replay, secret rotation, and delete.
- Reauthorize resource scope when creating each delivery; suppress events no longer visible to the subscription owner/role.
- Enforce SSRF protections at create, verification, and every DNS/connect boundary.
- Use TLS verification, bounded connect/read timeouts, body size limits, no credential-bearing redirects, and an egress proxy where deployed.
- Redaction conformance is fail-closed: a projector or schema error suppresses delivery and pages operators.
- Audit covers create, verify, enable, disable, secret issue/rotate/revoke, event ingest, suppress, deliver, retry, dead-letter, payload view, replay, export, and delete.
- Never log signing secrets, payload bodies, request authorization headers, or user response bodies.

## Performance and capacity budgets

- Source-event durable ingestion: p95 under 500 ms for batches up to 100.
- Ingest-to-first-delivery-attempt: p95 under 10 seconds under normal load.
- Deterministic projection/redaction: p99 under 20 ms per event.
- Subscription disable/revocation enforcement: p95 under 5 seconds.
- Candidate fan-out lookup: `O(log S + M)` using local-profile/type/scope
  indexes, where `M` is matched subscriptions.
- Delivery claim: `O(log D + B)` using status/next-attempt indexes.
- Hard quotas cover subscriptions, matched events, payload bytes, pending deliveries, retries, replay range, and endpoint concurrency per local profile.

Endpoint response time is capped by a short configurable timeout and never blocks AI run completion.

## Failure, idempotency, and recovery

- Source ingestion deduplicates on canonical event identity.
- Fan-out uses a unique subscription-revision/public-event key.
- Delivery retries reuse stable bytes and identifiers.
- App quit or a worker crash after send but before acknowledgement may
  redeliver; signatures and IDs support user-side dedupe on next boot.
- Relay outage resumes from the last backend-acknowledged source cursor.
- Backend delivery outage accumulates within quotas, then applies documented backpressure and alerts without affecting agent execution.
- Missing redaction projector, secret vault, audit, or policy service fails closed.
- Poison endpoints reach DLQ; they do not retry forever.
- Secret compromise revokes the key, disables delivery, and requires explicit rotation/replay decisions.

## Observability and quality gates

Metrics:

- relay cursor lag and ingestion duplicates;
- source events, eligible matches, suppressions, and fan-out;
- projection latency and redaction failures;
- first-attempt latency, delivery success, retry, DLQ, and expiry;
- response-code and error taxonomy;
- endpoint/subscription health and oldest pending age;
- payload bytes, quota actions, and replay volume;
- secret rotation age; and
- cross-profile authorization denials.

Trace lineage is `run_id/sequence → public_event_id → subscription/revision → delivery/attempt → endpoint result`.

Quality and security gates:

- fixture tests prove every public schema drops forbidden fields;
- property tests reject unknown/nested sensitive fields;
- secrets and private reasoning never appear in sampled payload scans;
- duplicate relay and delivery storms produce one logical public event;
- endpoint takeover, DNS rebinding, redirects, private address, timeout, and response-body attacks fail safely;
- replay preserves immutable history;
- disabled/revoked subscriptions receive no new attempts after the enforcement SLO; and
- deletion, optional retained backup, audit export, and key rotation pass.

## Rollout and backout

1. Publish event catalog and schemas without external delivery.
2. Relay events to a backend shadow store and compare source sequences.
3. Enable one internal sink with strict payload scanning.
4. Enable run terminal events for opt-in desktop profiles.
5. Add approval, tool, subagent, work-item, artifact, and operation families after projector review.
6. Add replay ranges and higher quotas after delivery reliability gates.

Backout disables fan-out or delivery workers independently, preserves source cursors and outbox rows, and does not interrupt agent runs. Operators may drain, expire, or replay retained deliveries after remediation according to policy.

## Implementation slices

1. Public event catalog, schemas, classifications, and redaction projectors
2. Backend subscription/revision/secret/verification domain
3. AI-backend committed-event relay and backend cursor ingestion
4. Transactional fan-out and delivery outbox
5. Signing, egress controls, retry, ordering, and DLQ
6. Facade management, delivery diagnostics, and secret rotation UI
7. Replay, retention, deletion, audit, and incident controls
8. Security fixtures, load tests, user verification examples, and rollout

## Test plan

- Unit: event mapping, bounds, redaction, signing, retry taxonomy, ordering
- Schema: backward compatibility and forbidden-field fixtures per event type
- Integration: persisted run event through signed user receipt
- Concurrency: duplicate relay, fan-out workers, delivery workers, replay requests
- Fault injection: crashes after source read, ingest, fan-out, send, and acknowledgement
- Security: cross-profile scope, SSRF, DNS rebinding, redirects, forged verification, key theft
- Privacy: prompts, tool arguments, connector content, paths, secrets, and stack traces are absent
- Recovery: cursor replay, endpoint outage, DLQ, disable, rotation, and re-enable
- Retention: payload expiry, subscription deletion, local-profile deletion, optional retained backup, export
- Load: high event volume, slow endpoints, noisy profile isolation, bounded queue growth

## Definition of done

- Authorized users can create, verify, manage, rotate, inspect, replay, and delete subscriptions.
- AI-backend canonical event sequence reaches the backend through a durable replayable relay.
- Public payloads use reviewed versioned schemas and deterministic fail-closed redaction.
- Every delivery is signed, deduplicable, retried within bounds, and terminally visible.
- DLQ and replay preserve original history and do not affect agent execution.
- No outbound payload contains prohibited content or crosses local profile/resource authorization.
- Security, privacy, retention, audit, load, and recovery gates pass.

## Guardrails

- Outbound lifecycle delivery never grants inbound agent authority.
- user response bodies are ignored and never enter prompts or state transitions.
- Only allowlisted public schemas may cross the boundary.
- Unknown fields are dropped; projector uncertainty fails closed.
- No unsigned delivery, plaintext secret, unrestricted redirect, private-network egress, or infinite retry.
- Subscription scope can narrow automatically but cannot widen without an authorized revision.
- Delivery failure must never block or roll back an agent run.

## Open decisions

1. Whether the first release uses HMAC only or also supports user public-key encryption/signature modes.
2. Which tool and artifact metadata is safe in the default versus elevated projection level.
3. Maximum ordering hold before later events may overtake a failing delivery.
4. Default delivery retention, replay window, attempts, and maximum age.
5. Whether private-network endpoints require a separate managed egress product.
