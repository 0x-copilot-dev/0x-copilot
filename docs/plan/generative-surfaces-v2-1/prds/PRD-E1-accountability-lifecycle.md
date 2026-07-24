# PRD-E1 — Usage, receipts, audit, retention, and lifecycle hardening 🎨

**Goal.** Make the universal operation/artifact/effect model accountable and
supportable before cutover. Complete retry-safe usage attribution, receipts, Sources,
pending work, tamper-evident exports, artifact/effect authorization, retention,
deletion/legal-hold cascades, orphan repair, quotas, and security/operations metrics.

## Implementer brief

Read:

1. `../01-sdr.md` §§14–18 and §20.
2. All A–D PRDs, especially A1 golden journeys.
3. `services/ai-backend/src/agent_runtime/observability/usage_meter.py`.
4. `services/ai-backend/src/agent_runtime/surfaces_v2/receipt.py`.
5. `services/ai-backend/src/agent_runtime/surfaces_v2/receipt_export.py`.
6. `services/ai-backend/src/agent_runtime/surfaces_v2/pending_work.py`.
7. `packages/audit-chain/`.
8. runtime usage routes/schemas and postgres telemetry records.
9. `packages/chat-surface` receipt, Sources, approvals, Agents, and pending projections.
10. service deletion/retention jobs and audit adapters actually present in the repo.

Do not mark a lifecycle control complete based on interface intent. Code, persistent
adapter, tests, metrics, and runbook must all exist.

## Context

The Work Ledger is the causal record, but artifacts and large proposal/receipt bodies
live outside it. Model usage may occur before an artifact id exists. An effect may be
applied even if the worker loses acknowledgement. Deleting a conversation may leave
artifact blobs, preimages, prepared broker files, audit refs, or legal holds. Launch
requires one joined lifecycle and proof that every sensitive workflow answers:

- who acted;
- who approved;
- what exact digest/target changed;
- what happened;
- where it is logged;
- how long it is retained;
- how it is deleted or held.

## Interfaces consumed

- All canonical events/entities/ref formats.
- A2 Artifact Repository and GC enumerator.
- A5 claims/reconcile.
- C2 local journal/preimages.
- D adapters and operation tree.
- Existing UsageMeter and signed receipt export.

## Interfaces exposed

- `UsageAttributionEdgeStorePort` and rollups.
- `RunReceiptV2`, `ReceiptFoldV2`, `ReceiptExportV2`.
- `SourcesProjectionV2`.
- `PendingWorkProjectionV2`.
- `LifecycleReferenceEnumerator` and `RetentionCoordinator`.
- deletion/legal-hold service contracts.
- repair/reconciliation jobs and operational dashboards/alerts.

## Design

### D1. Usage record and immutable attribution

Every model call records once at completion/terminal accounting:

```text
usage_record_id
org_id, user_id, conversation_id, run_id
model, provider, purpose
tokens_in, tokens_out, cached_tokens?, reasoning_tokens?
cost_basis_version, estimated_cost?
operation_id?
created_at
```

Closed purposes include existing values and:

- `main`;
- `subagent_work`;
- `view_shaping`;
- `shape_request`;
- `artifact_assistance` only if a distinct model call is later added;
- `recovery_assistance` only if a distinct model call is later added.

Artifact/stage linkage learned later uses immutable attribution edges:

```text
edge_id, usage_record_id, operation_id
artifact_id?, stage_id?, surface_id?
relationship, created_at
```

Do not rewrite usage rows or duplicate token totals per edge. Rollups deduplicate by
`usage_record_id`.

### D2. Retry and stream accounting

- streaming accumulator emits one row per provider invocation;
- retries are separate attempts with one row each and same operation id;
- a provider retry that reports no tokens uses the existing documented fallback, never
  invented values;
- model-call completion and usage event dedupe by provider invocation id;
- cancellation/timeout records reported usage to date where available;
- artifact publication itself makes no hidden model call;
- shaping/regeneration records surface/artifact id when known.

### D3. Usage APIs

Identity-scoped facade routes:

- `/v1/usage/me`
- `/v1/usage/conversations/{id}`
- `/v1/usage/runs/{id}`
- `/v1/usage/runs/{id}/calls`
- `/v1/usage/org/purpose` for authorized admin scope.

Return totals by model/purpose/operation and optional artifact/stage associations.
Implementing the final Usage settings button remains out of scope, but contracts and
query performance are launch-ready.

### D4. Receipt v2

Pure fold over canonical plus compatibility events:

```text
RunReceiptV2
  run_id, status, generated_at, fold_ref
  operations: requested/completed/failed/blocked
  artifacts: created/revised/promoted
  reads: completed
  effects: proposed/approved/rejected/applied/partial/held/indeterminate
  gates: opened/resolved/pending
  usage totals by purpose
  attribution rows[]
  unresolved warnings[]
```

Receipt truth comes from events/usage refs, not mutable UI state. It distinguishes
internal artifacts from external writes and does not claim “read-only” merely because no
MCP write occurred.

B3 selection rules remain: chat-only zero-operation receipt is available but does not
auto-open the canvas.

### D5. Sources v2

Sources are provenance edges:

- connector/tool/origin;
- artifact revision/source;
- workspace grant label and keyed virtual-path token;
- browser origin;
- sandbox operation;
- subagent task;
- external receipt ref.

No physical path, cookie, secret, raw arguments, full body, or provider token. Titles
are untrusted text. Opening a source rechecks authorization.

### D6. Pending work

Pure predicate from ledger/claims/gates:

- unresolved held/approved/queued/claimed/indeterminate stages;
- open auth/grant/capability/policy gates;
- recovery proposals;
- excludes rejected/cancelled/applied/reconciled;
- carries exact run/subject ids.

Aggregate across authorized runs with bounded cursor pagination. One corrupt run
degrades to an explicit warning/omitted item, not a cross-tenant leak or 500.

### D7. Audit export v2

Version signed bundles. Include canonical event rows and terminal receipt row:

```text
bundle_version, run_id, generated_at, key_id
rows[{sequence_no,event_type,created_at,payload_digest,safe_payload,prev_hash,signature}]
receipt_digest
```

Large/private bodies are represented by digest/ref class, not content. Export verifier:

- supports old v2 bundle indefinitely;
- validates chain, row order/count, signatures, key rotation, receipt fold;
- detects dropped/reordered/modified/forged rows;
- runs offline with public/key-verification material appropriate to current audit-chain
  design.

Production without signing key fails export closed with safe 503; run execution does not
silently claim audit exportability if the adapter is no-op/in-memory.

### D8. Authorization

Every artifact/revision/content/stage/receipt/export/usage/source route:

- derives org/user/role/scope from verified identity;
- returns 404 for foreign resources;
- checks parent run/conversation membership;
- content/download uses `nosniff`, safe disposition, range limits;
- support/admin access is explicit, audited, and least-privilege;
- signed opaque refs are not treated as authorization by themselves.

Add an endpoint matrix test that enumerates every new route and every identity class.

### D9. Reference graph

`LifecycleReferenceEnumerator` returns edges among:

- conversation/message/run/events;
- operation args/results;
- artifacts/revisions/blobs;
- surfaces/views/specs;
- stages/proposals/claims/receipts;
- workspace overlays/preimages/prepared/journal/recovery;
- sandbox snapshots/patches/resources;
- browser downloads/uploads/receipts;
- usage attribution;
- audit exports;
- legal holds.

Every ref scheme from A1 has one owner and enumerator. Unknown refs fail a launch gate.

### D10. Retention

Configurable per tenant/deployment with documented defaults:

- active run/stage refs retained;
- artifact retention inherits conversation/user policy unless independently retained;
- preimages/prepared temp data use shorter bounded retention after terminal state;
- audit/legal-hold retention may outlive product content but stores only safe
  digests/metadata;
- usage follows billing/compliance policy;
- orphan grace period before physical deletion.

Retention job is idempotent, resumable, cursor-based, quota-aware, and emits metrics.
It never deletes a body still referenced by a stage, receipt, hold, or recovery.

### D11. Deletion and legal hold

Deletion cascade:

1. authorize/request tombstone;
2. prevent new operations;
3. enumerate graph;
4. cancel safe pending work;
5. retain/mark indeterminate or legally required effects;
6. delete metadata/content in dependency order;
7. verify no unauthorized refs remain;
8. emit completion/audit.

Legal hold:

- blocks physical deletion for covered refs;
- does not make deleted product content user-visible;
- is explicit deployment/product control with tests;
- hold access and release are audited.

Cross-tenant shared physical blobs are deleted only when all logical refs are gone.

### D12. Repair and reconciliation

Jobs:

- artifact metadata event/outbox repair;
- orphan blob/temp upload repair;
- stale prepared workspace/sandbox/browser resource cleanup;
- claimed/indeterminate effect reconcile;
- receipt/source projection rebuild;
- usage edge orphan repair;
- audit export verification sampling.

Repair never invents an approval or resends an uncertain effect.

### D13. Metrics and alerts

At minimum:

- operation/stage/artifact rates/failures/latency;
- classification/disposition mismatches;
- unresolved/aged gates/stages/indeterminate claims;
- artifact/blob bytes, temp/orphan bytes, quota rejects;
- retention/deletion lag/failures;
- broker/sandbox/browser reconcile backlog;
- usage accounting gaps/duplicates;
- audit export/verification failures;
- cross-tenant authorization denials.

Labels are low-cardinality and content/path/tenant-safe. Define alert thresholds and
owner/runbook links.

## Implementation plan

1. Add immutable usage edges and migration/rollups.
2. Implement ReceiptFoldV2 and TS parity over A1 journeys.
3. Implement Sources/Pending V2 projections and shared UI.
4. Version audit export/verifier with old-bundle fixtures.
5. Add route authorization matrix.
6. Implement reference graph/enumerator registry.
7. Implement retention/deletion/legal-hold coordinator.
8. Add repair/reconcile jobs.
9. Add metrics/alerts/runbooks.
10. Run large-scale/restart/tamper/security suites.

## Test plan

### Usage

- retries/stream/cancellation/subagents/shaping counted once per invocation;
- edge joins do not double totals;
- per-user/conversation/run/purpose sums match independent fixtures;
- cross-tenant calls denied.

### Receipt/projections

- Python/TypeScript folds byte-equal every golden journey/prefix;
- chat-only receipt conditional behavior;
- artifact vs external effect counts correct;
- pending items appear/disappear exactly;
- Sources contain provenance but no sensitive fields.

### Audit

- tamper, reorder, drop, forge, wrong key, rotation, old bundle verification;
- production missing key safe failure;
- export route authorization.

### Lifecycle

- deletion graph for every ref family;
- legal hold blocks physical deletion;
- shared blob survives one logical deletion;
- stage/receipt prevents premature artifact GC;
- crash/restart/resume jobs;
- repair never applies an effect.

### Scale/operations

- million-event/reference synthetic pagination;
- bounded memory/query plans/indexes;
- quota/retention metrics;
- alert canaries and runbook checks.

## Definition of done

- [ ] Usage is complete, retry-safe, and attributable without row mutation.
- [ ] Receipt/Sources/Pending V2 fold all golden journeys in Python/TypeScript.
- [ ] Signed audit exports are tamper-evident and old exports remain verifiable.
- [ ] Every sensitive route passes the identity matrix.
- [ ] Every ref scheme participates in retention/deletion/legal hold.
- [ ] Repair/reconcile jobs and operational metrics/runbooks exist.
- [ ] No no-op/in-memory-only control is claimed production-complete.
- [ ] UI and standard DoD pass.

## Out of scope

- The final user-facing Usage settings button.
- Deployment controls not evidenced in repo (WAF/KMS/SIEM/etc.); document them as
  deployment requirements, not implemented product controls.
- Cutover/legacy deletion.

## Guardrails

- No token double-count through attribution edges.
- No physical paths/content/secrets in receipts/audit.
- No deletion without complete reference enumeration.
- No repair that invents approval or retries uncertain effects.
- No compliance claim based only on an interface/no-op adapter.
