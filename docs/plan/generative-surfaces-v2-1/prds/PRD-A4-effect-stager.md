# PRD-A4 — Transport-neutral Effect Stager

**Goal.** Generalize the v2 `WriteStager` into a pure, transport-neutral proposal,
revision, decision, and policy state machine. The stager structurally has no executor,
connector, broker, browser, sandbox, queue consumer, or other effect handle. Approval
records intent over exact target/proposal digests; this PR performs zero external
effects.

## Implementer brief

Read:

1. `../01-sdr.md` §§5.6–5.7, 11, 14–15.
2. `PRD-A1-artifact-effect-contracts.md`,
   `PRD-A2-artifact-repository.md`, and
   `PRD-A3-operation-gateway.md`.
3. `services/ai-backend/src/agent_runtime/surfaces_v2/staging.py`.
4. `services/ai-backend/src/agent_runtime/api/stage_service.py`.
5. `services/ai-backend/src/agent_runtime/api/stage_ledger.py`.
6. `services/ai-backend/src/runtime_api/http/stages.py`.
7. Existing D1/D3 no-bypass, rowset, fold, and stage-route tests.

Do not touch an external executor in this PR. A recording fake with every effect method
set to throw must remain unused through every legal and illegal stage flow.

## Context

The current stager has strong revision-pinned decision behavior, but its entities are
draft/connector/rowset shaped. Local workspace change sets, browser forms, sandbox
patches, and argument-only MCP writes require one proposal union and one state machine.
Staging and commit are deliberately separate PRs because the absence of an executor in
the stager is the primary no-bypass boundary.

## Interfaces consumed

- A1 `EffectTarget`, `ProposalRef`, `EffectStage`, digest/id/ref contracts and events.
- A2 content refs for proposal bodies.
- A3 classification and immutable policy snapshot refs.
- Existing stage ledger persistence and runtime event append.

## Interfaces exposed

Create/refactor:

```text
agent_runtime/effects/
  contracts.py
  ports.py
  fold.py
  staging.py
  policy.py
  errors.py
```

```python
class EffectStageLedgerPort(Protocol):
    async def list_stage_events(run_id, stage_id) -> Sequence[StructuralEvent]: ...
    async def append_stage_event(run_id, event_type, payload, idempotency_key): ...

class EffectCommitOutboxPort(Protocol):
    async def enqueue_after_decision(command) -> None: ...

class EffectStager:
    async def stage(proposed_effect, policy_snapshot, actor) -> EffectStageState: ...
    async def revise(stage_id, expected_revision, proposal, actor) -> EffectStageState: ...
    async def decide(stage_id, revision, decision, actor) -> EffectStageState: ...

class EffectStageFold:
    @classmethod
    def fold(cls, events) -> EffectStageState: ...
```

The outbox stores a command only; it exposes no dispatch/executor API.

## Design

### D1. Proposal union

`ProposedEffect` has common fields:

```text
operation_id, executor
target_ref, target_digest, display_target
proposal_kind, proposal_ref, proposal_digest, proposal_media_type
precondition_ref?, precondition_digest?
effect_class, policy_snapshot_ref
agent_hold?
```

Closed proposal kinds:

- `canonical_arguments`;
- `artifact_revision`;
- `workspace_change_set`;
- `row_set`;
- `browser_submission`;
- `sandbox_patch`;
- `builtin_payload`.

Bodies remain behind refs. The public ledger contains safe summaries and digests only.

### D2. Stage states and transitions

States in A4:

```text
PROPOSED | HELD | REVISED | APPROVED | REJECTED | CANCELLED | SUPERSEDED
```

Execution states are defined in A1 but become producible only in A5.

Rules:

- stage starts `PROPOSED` when policy allows immediate apply, otherwise `HELD`;
- a product/user policy may auto-record an approval only for non-destructive,
  descriptor-known effects and must identify actor `policy`;
- unknown/destructive/agent-held always start `HELD`;
- revision increments exactly by one;
- revision after approval supersedes the approval and returns to `HELD`;
- approval binds revision, proposal digest, target digest, and decision actor;
- stale/foreign/invalid transitions emit nothing;
- reject/cancel never enqueue a commit command;
- restore is represented as a new revision or policy-reviewed transition, not history
  deletion.

### D3. Policy resolution

`EffectStagePolicyResolver` consumes:

- effect class;
- capability descriptor;
- immutable run policy snapshot;
- connector/workspace override;
- grant mode where relevant;
- sensitive-target classification;
- agent hold.

Most restrictive wins:

```text
deployment > org > grant/capability > destructive/unknown > agent hold >
user override > allow-always
```

“Allow always” may create a policy approval only for known
`external_reversible` effects. It never bypasses exact digests or future precondition
checks.

### D4. Exact revision/digest behavior

Every revision stores:

- proposal ref/digest/media type;
- target ref/digest;
- optional precondition ref/digest;
- author and timestamp;
- safe diff/summary ref.

`decide` recomputes/resolves the current revision metadata before appending. The
decision payload repeats both proposal and target digest to make approval auditable
without dereferencing mutable state.

### D5. Decision-to-outbox atomicity

Approve:

1. scope/fold stage;
2. validate exact current revision/digests;
3. append `effect.decision_recorded`;
4. enqueue `EffectCommitCommand` transactionally or through an outbox in the same
   durability boundary;
5. return folded state.

Command:

```text
run_id, stage_id, revision, decision_ledger_id,
proposal_digest, target_digest, idempotency_key
```

The command contains no body and no executor credential. A5 revalidates everything.

### D6. Compatibility fold

Read-side mapping:

- `write.staged` → initial canonical stage;
- `revision.added` → revision;
- `decision.recorded` → decision;
- `write.applied` → execution state for display only.

New A4 stages emit only `effect.staged`, `effect.revised`, and
`effect.decision_recorded`. Never emit old and new authoritative stage events for one
stage.

Existing `/v1/agent/stages/*` routes become aliases over the canonical service during
migration. New routes:

- `GET /v1/agent/effect-stages/{stage_id}?run_id=...`
- `POST /v1/agent/effect-stages/{stage_id}/revisions`
- `POST /v1/agent/effect-stages/{stage_id}/decisions`

All app traffic goes through facade.

### D7. Structural no-executor invariant

`EffectStager`, `EffectStageFold`, StageService, HTTP routes, and model-facing proposal
builders may depend only on:

- ledger port;
- content metadata resolver;
- policy resolver;
- outbox enqueue port;
- clock/id generator.

They may not import/hold:

- MCP client/connector;
- desktop broker/authority;
- browser controller;
- sandbox provider;
- built-in side-effect client;
- executor registry or `apply/commit/execute` callable.

Add AST/import/object-graph tests and a planted violation canary.

### D8. Idempotency and concurrency

- stage id/idempotency key + same digest replays;
- same key/different digest conflicts;
- concurrent decisions on one revision serialize to one terminal decision;
- concurrent revisions serialize by expected revision;
- duplicate approve enqueues one command;
- policy auto-approval and user approval cannot both become current.

## Implementation plan

1. Extract a compatibility-preserving pure fold.
2. Add proposal union/contracts/errors.
3. Add policy resolver and exhaustive matrix.
4. Generalize stage/revision/decision methods.
5. Add transactional decision outbox port/adapters.
6. Add generalized routes, facade passthrough, and API types.
7. Point old routes/folds to the canonical service.
8. Add structural no-executor gate.
9. Add golden-journey and adapter parity tests.

## Test plan

### State/fold

- every allowed transition and every forbidden transition;
- all event prefixes fold deterministically;
- stale approval, foreign actor, digest mismatch emit nothing;
- edit-after-approve invalidates approval;
- legacy fixtures fold identically.

### Policy

- unknown/destructive/agent-held always held;
- allow-always only affects known reversible;
- policy snapshot change does not rewrite an existing stage;
- sensitive target tightens posture.

### No-bypass

- exploding executor/client fakes record zero calls across random sequences;
- AST/import gate finds no effect handles;
- approve only appends decision and outbox command;
- direct route request cannot apply.

### Concurrency/idempotency

- duplicate stage/revise/decide;
- same key/different digest 409;
- concurrent approve/reject yields one winner;
- decision/outbox crash injection never produces an unqueued durable approval.

## Definition of done

- [ ] One proposal union covers all declared executor families.
- [ ] Pure canonical stage fold and policy resolver exist.
- [ ] Approvals bind exact target/proposal digests.
- [ ] Approve transactionally writes one outbox command.
- [ ] Stager and all upstream code structurally hold no executor.
- [ ] Existing v2 fixtures/routes remain compatible.
- [ ] Zero external effects occur in the entire PR test suite.
- [ ] Standard DoD passes.

## Out of scope

- Claim, prepare, apply, reconcile.
- Any real executor.
- Full stage UI.
- Producer cutovers.

## Guardrails

- No executor registry import in staging layers.
- No inline bodies in ledger/outbox.
- No approval of a mutable or digestless ref.
- No auto-approval for unknown/destructive/held work.
- No dual old/new stage truth.
