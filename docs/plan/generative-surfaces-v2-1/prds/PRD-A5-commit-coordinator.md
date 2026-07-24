# PRD-A5 — Commit Coordinator and executor protocol

**Goal.** Implement the sole path from an approved EffectStage to an external effect:
durable command consumption, approval/digest revalidation, prepare, claim-before-effect,
typed executor apply, exact completion, and reconcile after uncertain outcomes. Prove
the protocol first with a recording fake executor; ship only a legacy MCP compatibility
executor in this PR.

## Implementer brief

Read:

1. `../01-sdr.md` §§5.8, 10, 14–15.
2. `PRD-A4-effect-stager.md`.
3. `services/ai-backend/src/agent_runtime/surfaces_v2/commit_engine.py`.
4. `services/ai-backend/src/runtime_worker/handlers/stage_commit.py`.
5. `services/ai-backend/src/agent_runtime/api/stage_commit_queue.py`.
6. `services/ai-backend/src/agent_runtime/surfaces_v2/mcp_connector.py`.
7. Existing D2 duplicate/claim/flag-flip/adversarial tests.

Preserve and strengthen the existing sole-producer property. There must be exactly one
production module that emits canonical `effect.applied`.

## Context

A4 records decisions but cannot execute. A5 generalizes the existing CommitEngine so
workspace, MCP, browser, sandbox, and built-in executors can share one safety
coordinator. Executor adapters own transport mechanics only; they cannot decide policy
or synthesize approval.

## Interfaces consumed

- A4 fold, command/outbox, exact digests.
- A2 content resolver.
- A1 executor/result/receipt events.
- Existing durable run event store and adapter-specific claim patterns.

## Interfaces exposed

```text
agent_runtime/effects/
  coordinator.py
  executor.py
  executor_registry.py
  claims.py
  reconciliation.py

runtime_worker/handlers/effect_commit.py
runtime_worker/handlers/effect_reconcile.py
```

```python
class EffectExecutor(Protocol):
    kind: EffectExecutorKind
    async def prepare(request: EffectExecutionRequest) -> PreparedEffect: ...
    async def apply(prepared: PreparedEffect) -> EffectExecutionResult: ...
    async def reconcile(claim: EffectClaim) -> EffectExecutionResult: ...
    async def abort(prepared: PreparedEffect) -> None: ...

class EffectCoordinator:
    async def handle(command: EffectCommitCommand) -> None: ...
    async def reconcile(command: EffectReconcileCommand) -> None: ...
```

### Executor result contract

Outcomes:

```text
applied | partial | already_applied | failed_before_effect |
precondition_drift | cancelled | indeterminate
```

Only safe message/failure codes are public. Provider receipts/bodies remain behind
protected refs.

## Design

### D1. Worker algorithm

1. scope-load run/stage;
2. fold stage and require current `APPROVED`;
3. verify command revision/decision ledger id/proposal/target digests;
4. resolve immutable proposal/target refs and recompute digests;
5. resolve executor from closed registry;
6. call `prepare` (reads/reservations only, no user-visible mutation);
7. compare observed precondition;
8. durably claim idempotency key and emit `effect.claimed`;
9. call `apply`;
10. durably record result and emit `effect.applied`; or
11. on uncertain timeout/crash state, mark/schedule `effect.indeterminate` and
    reconcile without resending.

No claim means no effect. A claim without completion means no blind retry.

### D2. Claim store

Persist:

```text
org_id, run_id, stage_id, revision, claim_id
idempotency_key, executor, proposal_digest, target_digest
state, attempt, prepared_ref?, receipt_ref?, outcome?
created_at, updated_at
```

Unique idempotency key per tenant/executor. Same key/same digests returns existing
claim/result. Same key/different digests is a hard conflict and security audit.

Implement in-memory, file, and postgres adapters under one contract suite. File claim
must use atomic create; postgres uses unique constraint/transaction. Migration number is
next free at implementation.

### D3. Prepare semantics

Prepare may:

- fetch current remote/local state;
- validate target;
- reserve a provider idempotency token;
- stage bytes in an executor-private area;
- return observed target digest and expiry.

Prepare may not perform the requested user-visible mutation. An executor that cannot
separate prepare must declare that fact; coordinator still performs all validation
before claim and calls its single apply only after claim.

Precondition drift:

- abort prepared state;
- record terminal drift outcome;
- zero apply calls;
- stage remains visible for regenerate/rebase.

### D4. Apply and cancellation

After claim:

- cancellation before `apply` calls `abort` and records cancelled-before-effect;
- cancellation while `apply` is in flight is treated according to executor certainty;
- timeout/network loss after request transmission is `indeterminate` unless executor
  proves not applied;
- never report failed when the effect may have happened.

### D5. Reconcile

Reconcile uses claim/prepared/provider idempotency data and returns:

- applied/already_applied → record completion;
- failed before effect → record failure;
- indeterminate → retain and surface support/user remediation.

Reconcile itself is idempotent and never performs an unapproved new mutation. If an
executor lacks reconciliation, uncertain outcomes stay indeterminate.

### D6. Executor registry

- closed A1 kinds;
- one factory/kind;
- per-run dependencies from verified context;
- startup fails if an enforced capability references a missing executor;
- model-facing code cannot resolve registry;
- executor receives only exact execution request/refs, no mutable stage object.

Architecture gate rejects `apply/commit/execute` clients upstream of the worker.

### D7. Sole producer

Only `runtime_worker/handlers/effect_commit.py` (or one named emission helper used only
there and reconcile handler) may emit `effect.applied`. If reconcile shares the event,
both handlers call one restricted `EffectResultRecorder` not importable from capability
code.

A repository test scans for event construction and a planted canary proves violations
fail.

### D8. Legacy MCP compatibility executor

Wrap current `McpStageCommitConnector`:

- maps canonical argument proposal to exact MCP call;
- uses existing server resolution/client creation;
- passes provider idempotency data where supported;
- returns protected receipt ref;
- does not classify or approve.

Port one current v2 staged write end-to-end under a dark per-capability flag. It must
dispatch byte-/JSON-equivalent approved arguments and preserve existing user behavior.

### D9. Audit

Separate facts:

- commit requested;
- prepare result;
- claim;
- apply outcome;
- reconcile outcome.

Audit actor, stage/revision/decision, executor/capability/op, digests, safe code,
timestamps. Never raw args/content/paths/secrets/provider errors.

## Implementation plan

1. Add executor/claim/result contracts and recording fake.
2. Implement claim adapters and migration.
3. Implement coordinator with injected crash points.
4. Implement result recorder/sole-producer gate.
5. Implement reconcile queue/handler.
6. Add executor registry/startup conformance.
7. Add LegacyMcpEffectExecutor.
8. Port one compatibility flow under dark flag.
9. Extend generalized apply/reconcile routes where needed; model cannot call them.
10. Run adversarial and full persistence suites.

## Test plan

### Recording fake protocol

- exact order `prepare → claim committed → apply → complete`;
- prepare drift yields zero claim/apply;
- crash before claim yields zero effect;
- crash after claim before apply reconciles safely;
- crash/timeout during apply never blind-retries;
- duplicate delivery makes one apply call;
- changed digest conflicts.

### Revalidation

- stale revision, wrong decision id, wrong actor scope, changed target/proposal all
  no-op/audit with zero apply;
- rejected/cancelled/superseded stage cannot execute;
- deleted proposal body fails before claim.

### Adapter parity/concurrency

- memory/file/postgres claims;
- two workers race, one claim;
- restart survives prepared/claimed/indeterminate states;
- reconcile duplicate is stable.

### Compatibility MCP

- exact approved args dispatched once;
- pre-existing v2 fixture/route semantics remain;
- flag off uses old path; dark flag cannot enable two workers for one stage;
- no new MCP effect path upstream.

## Definition of done

- [ ] One Commit Coordinator implements the full algorithm.
- [ ] Claim adapters survive concurrency/restart.
- [ ] Recording fake proves claim-before-effect and no blind retry.
- [ ] Reconcile represents uncertain outcomes honestly.
- [ ] Executor registry is unavailable to model-facing adapters.
- [ ] One legacy MCP flow passes through the coordinator.
- [ ] Canonical effect result has one production producer.
- [ ] Effect-path and standard DoD pass.

## Out of scope

- Full MCP cutover.
- Workspace/browser/sandbox executors.
- Stage UI.
- Legacy event/worker deletion.

## Guardrails

- Never apply before durable claim.
- Never blind-retry a claimed uncertain effect.
- Never let an executor own policy/approval.
- Never resolve mutable proposal bytes after claim without digest verification.
- Never expose provider receipt bodies publicly.
