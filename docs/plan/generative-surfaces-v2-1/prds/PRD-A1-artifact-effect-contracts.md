# PRD-A1 — Artifact, operation, and effect contracts

**Goal.** Establish one versioned, cross-language contract for operations, artifacts,
external effects, stages, executors, and their ledger events. This PR changes no runtime
behavior. It gives every later PR one vocabulary, one set of identifiers, and one shared
set of golden journeys.

## Implementer brief

Implement this PR in a fresh worktree based on the latest `main`. Read, in order:

1. `docs/plan/generative-surfaces-v2-1/00-overview.md`, especially principles P1–P11.
2. `docs/plan/generative-surfaces-v2-1/01-sdr.md` §§5–6 and §19.
3. `packages/service-contracts/src/copilot_service_contracts/work_ledger.json`.
4. `packages/service-contracts/src/copilot_service_contracts/work_ledger.py`.
5. `packages/api-types/src/ledger.ts`.
6. `services/ai-backend/src/agent_runtime/surfaces_v2/ledger_models.py`.
7. `services/ai-backend/src/agent_runtime/surfaces_v2/entities.py`.
8. `services/ai-backend/src/agent_runtime/surfaces_v2/ledger_ids.py`.
9. Existing parity tests under
   `services/ai-backend/tests/unit/agent_runtime/surfaces_v2/` and
   `packages/api-types/src/ledger.test.ts`.

Do not add a second ledger, a second event transport, or a second copy of the enum
values. Extend the existing Work Ledger contract additively.

Expected verification:

```bash
cd services/ai-backend && .venv/bin/python -m pytest \
  tests/unit/agent_runtime/surfaces_v2/
npm run typecheck --workspace @0x-copilot/api-types
npm run test --workspace @0x-copilot/api-types
python tools/check_migration_manifest.py --service ai-backend
```

No migration should be added by this PR.

## Context

Generative Surfaces v2 correctly made presentation declarative and external writes
reviewable, but its vocabulary is still coupled to MCP tool calls and surface-shaped
results. v2.1 must also represent:

- an answer that remains only chat;
- code or a document authored entirely by the model;
- a CSV created in app-owned storage and later saved to a local workspace;
- a workspace overlay edit that has not touched the host;
- an MCP write, browser submit, or sandbox mutation;
- a crash after an external side effect but before the worker records completion.

These are not special cases. They are different compositions of the same concepts:
operation, artifact, presentation decision, stage, decision, and effect.

## Requirements

This PR implements the contract foundation for UA-FR-A1–A6, UA-FR-B1–B6,
UA-FR-C1–C6, UA-FR-D1–D8, UA-FR-F1–F6, and UA-NFR-2, 5, 7, 9, 11, 12.

## Interfaces consumed

- The current `work_ledger.json` SSOT and its Python/TypeScript mirrors.
- Existing ledger identifier format `r<short>·<seq>`.
- Existing v2 entity types and events.
- Existing runtime event transport, which stores `event_type` as text and therefore
  needs no schema migration for additive event names.

## Interfaces exposed

Later PRs consume the exact names below:

- `OperationRequest`, `OperationDescriptor`, `OperationDisposition`.
- `Artifact`, `ArtifactRevision`, `ArtifactIntent`, `ArtifactKind`.
- `SurfaceSubject`.
- `EffectTarget`, `ProposalRef`, `EffectStage`, `EffectDecision`.
- `EffectExecutorKind`, `EffectExecutionRequest`, `EffectExecutionResult`.
- `ArtifactIdCodec`, `OperationIdCodec`, `EffectStageIdCodec`.
- the event payloads and enum values defined in D2.
- golden journey loader functions in `copilot_service_contracts.work_ledger`.

Names may be changed during implementation only if all three mirrors and this SDR are
updated in the same PR.

## Design

### D1. One SSOT, three mirrors

Extend
`packages/service-contracts/src/copilot_service_contracts/work_ledger.json`. Keep
`schema_version` for the contract document and `v` on each event payload. Additive event
payloads in this PR use `v: 1`; do not bump existing payload versions.

The three required mirrors are:

1. JSON SSOT and golden fixtures in `packages/service-contracts`.
2. TypeScript public contracts in `packages/api-types/src/ledger.ts`.
3. Pydantic/StrEnum contracts in
   `services/ai-backend/src/agent_runtime/surfaces_v2/`.

Python and TypeScript must read enum values from the JSON contract where the current
package pattern permits it. Domain logic must not parse the JSON dynamically on every
request.

### D2. Additive event vocabulary

Add these canonical event types:

| Event                           | Required payload                                                                                                        |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `operation.requested`           | `v`, `operation_id`, `producer`, `capability`, `op`, `args_digest`                                                      |
| `operation.classified`          | `v`, `operation_id`, `effect_class`, `basis`, `confidence`                                                              |
| `operation.completed`           | `v`, `operation_id`, `outcome`, `result_ref?`, `latency_ms?`                                                            |
| `operation.failed`              | `v`, `operation_id`, `failure_code`, `retryable`                                                                        |
| `artifact.created`              | `v`, `artifact_id`, `kind`, `revision`, `content_ref`, `content_digest`, `author`                                       |
| `artifact.revised`              | `v`, `artifact_id`, `revision`, `parent_revision`, `content_ref`, `content_digest`, `author`                            |
| `artifact.promoted`             | `v`, `artifact_id`, `source_ref`, `kind`, `revision`                                                                    |
| `artifact.presentation_decided` | `v`, `artifact_id`, `decision`, `basis`, `surface_id?`                                                                  |
| `effect.staged`                 | `v`, `stage_id`, `operation_id`, `executor`, `target_ref`, `target_digest`, `proposal_ref`, `proposal_digest`, `policy` |
| `effect.revised`                | `v`, `stage_id`, `revision`, `proposal_ref`, `proposal_digest`, `author`                                                |
| `effect.decision_recorded`      | `v`, `stage_id`, `revision`, `decision`, `actor`, `proposal_digest`, `target_digest`                                    |
| `effect.claimed`                | `v`, `stage_id`, `revision`, `claim_id`, `executor`, `attempt`                                                          |
| `effect.applied`                | `v`, `stage_id`, `revision`, `outcome`, `receipt_ref?`, `result_digest?`                                                |
| `effect.indeterminate`          | `v`, `stage_id`, `revision`, `claim_id`, `reason`                                                                       |
| `effect.reconciled`             | `v`, `stage_id`, `revision`, `claim_id`, `outcome`, `receipt_ref?`                                                      |
| `gate.opened.v2`                | `v`, `gate_id`, `operation_id`, `gate_kind`, `capability`, `reason`                                                     |
| `gate.resolved.v2`              | `v`, `gate_id`, `decision`, `actor`                                                                                     |

The `.v2` suffix on generalized gate events avoids changing the payload meaning of the
existing v2 `gate.opened` and `gate.resolved` events. E2 may retire the old projections
after replay compatibility is proven.

Closed enum sets:

- `producer`: `model`, `subagent`, `user`, `system`.
- `effect_class`: `none`, `internal_reversible`, `external_reversible`,
  `external_destructive`, `unknown`.
- classification `basis`: `descriptor`, `catalog`, `provider_annotation`,
  `policy_override`, `default`.
- `operation outcome`: `succeeded`, `staged`, `blocked`, `cancelled`, `failed`.
- `ArtifactKind`: `code`, `document`, `dataset`, `file`.
- artifact author: `model`, `subagent`, `user`, `system`, `import`.
- presentation decision: `canvas`, `chat_card`, `activity_only`, `none`.
- effect decision: `approve`, `reject`, `restore`, `cancel`.
- effect outcome: `applied`, `partial`, `failed`, `cancelled`, `indeterminate`,
  `already_applied`, `precondition_drift`.
- executor: `mcp`, `workspace`, `browser`, `sandbox`, `builtin`.
- gate kind: `authentication`, `grant`, `capability`, `policy`.

Unknown enum values must fail validation in the contract layer. Compatibility
projectors may map a future unknown value to an honest raw fallback; writers may not
emit one.

### D3. Core entities

Define the following immutable wire entities. Full bodies are always references.

```text
OperationRequest
  operation_id, run_id, producer, capability, op
  canonical_args_ref, args_digest, requested_at
  artifact_intent?, effect_hint?, parent_operation_id?

OperationDescriptor
  capability, op, executor
  effect_class, result_kind, supports_prepare, supports_reconcile
  required_gate_kinds[], max_inline_result_bytes

OperationDisposition
  operation_id, outcome
  artifact_ids[], stage_ids[], activity_ref?
  agent_summary, retryable

Artifact
  artifact_id, org_id, user_id, conversation_id, run_id
  kind, title, media_type, current_revision
  created_by, created_at, updated_at, deleted_at?

ArtifactRevision
  artifact_id, revision, parent_revision?
  content_ref, content_digest, byte_size
  author, source_ref?, created_at

ArtifactIntent
  kind, title?, media_type?, suggested_filename?
  presentation_preference: auto | canvas | chat_card | none

SurfaceSubject
  subject_type: artifact | stage | record | receipt | gate
  subject_id

EffectTarget
  executor, capability, op, target_ref
  precondition_ref?, display_label

ProposalRef
  proposal_ref, proposal_digest, media_type, byte_size?

EffectStage
  stage_id, operation_id, run_id, executor
  target, proposal, revision, status
  policy_snapshot_ref, created_at, updated_at

EffectExecutionRequest
  stage_id, revision, idempotency_key
  target_ref, target_digest, proposal_ref, proposal_digest
  actor, decision_ledger_id

EffectExecutionResult
  outcome, receipt_ref?, result_digest?, retryable, safe_message?
```

Do not place physical host paths, connector secrets, OAuth material, raw MCP arguments,
or artifact bodies in these entities.

### D4. Identifier and reference formats

Add codecs with strict parse/format methods and typed errors:

- operation id: `op_<uuid7-or-uuid4>`;
- artifact id: `art_<uuid7-or-uuid4>`;
- effect stage id: `stg_<uuid7-or-uuid4>`;
- artifact content ref: `artifact://<artifact_id>/revisions/<positive-int>`;
- operation args ref: `operation://<operation_id>/args`;
- proposal ref: `proposal://<stage_id>/revisions/<positive-int>`;
- executor receipt ref: `receipt://effects/<stage_id>/<claim-id>`;
- broker target refs are opaque `workspace-target://<grant-id>/<path-token>`, never
  physical paths.

The UUID representation must be canonical lowercase. Parsing rejects whitespace,
embedded traversal, a zero revision, or additional path segments. Codecs may wrap the
existing uuid utility but must not silently accept bare UUIDs.

### D5. Digests and canonicalization

- Use SHA-256, lowercase hexadecimal.
- Bytes are hashed as bytes.
- Structured arguments are canonical JSON: UTF-8, sorted keys, no insignificant
  whitespace, finite numbers only, and arrays preserve order.
- `args_digest`, `target_digest`, and `proposal_digest` are mandatory before a stage
  can be approved.
- The exact canonicalization algorithm must have shared fixture vectors, including
  Unicode, nested objects, booleans, null, and floating-point rejection cases.

### D6. Golden journeys

Extend the existing golden-event fixture or add
`work_ledger_v2_1_golden_journeys.json` beside it. Include independent journeys for:

1. chat-only arithmetic answer: operation/model events, no artifact, no stage;
2. model-authored code artifact: artifact plus canvas presentation;
3. MCP read whose result remains activity-only;
4. MCP read that creates a table surface;
5. CSV authored as an internal artifact, edited, then staged to workspace;
6. workspace approval followed by successful commit;
7. workspace precondition drift and no host mutation;
8. MCP write staged, revised, approved, claimed, applied;
9. crash after claim and later `effect.reconciled`;
10. generalized grant gate opened/resolved;
11. subagent-authored artifact with parent operation attribution;
12. destructive effect that remains held despite `allow_always`.

Each journey includes expected folded artifact, stage, canvas, receipt, and pending-work
state. Fixtures must contain no wall-clock-generated values.

### D7. Compatibility mappings

Document and test these temporary mappings:

- existing `action.classified` → `operation.classified`;
- existing `read.executed` → successful read `operation.completed`;
- existing `surface.created`/`view.derived` remain presentation events;
- existing `write.staged`/`revision.added`/`decision.recorded`/`write.applied`
  project into the new stage model;
- existing gates remain readable but are not valid generalized-gate write inputs.

Compatibility is read-side only. New producers emit the new event vocabulary once
their owning PR cuts over; they must not emit old and new authoritative stage events
for the same operation.

## Implementation plan

1. Extend the JSON contract with enum sets, entity schema metadata, and events.
2. Add deterministic golden journeys and digest/codec vectors.
3. Extend the service-contract loader with explicit loader functions.
4. Add TypeScript contracts, codecs, guards, and barrel exports.
5. Add Python enums, contracts, codecs, and vocabulary validation.
6. Add parity tests that compare event order, required keys, enum values, codecs, and
   fixture folds across Python and TypeScript.
7. Add a repository gate that rejects duplicate inline definitions of the new event
   values outside the SSOT/mirror files.
8. Update `01-sdr.md` only if implementation discovers a reviewed contract change.

## Test plan

### Contract tests

- Every event validates its golden payload.
- Removing any required key fails.
- Adding an unknown key fails for write-side validation.
- Every enum has byte-identical values in JSON, Python, and TypeScript.
- Existing v2 golden events still validate unchanged.

### Codec and digest tests

- Round-trip all valid ids/refs.
- Reject traversal, uppercase UUIDs, empty ids, revision zero, extra segments, and
  overlong values.
- Python and TypeScript canonical-JSON digests match all vectors.
- Non-finite numbers and unsupported values fail before staging.

### Fold fixture tests

- Both languages fold every prefix of every golden journey without throwing.
- Final fold snapshots are byte-identical after normalized key ordering.
- Legacy journey fixtures remain replayable through the compatibility projector.

## Definition of done

- [ ] One SSOT contains all new enum/event values.
- [ ] Python and TypeScript mirrors expose the interfaces listed above.
- [ ] Golden journeys cover all twelve cases in D6.
- [ ] Digest and id/ref parity is proven across languages.
- [ ] Existing v2 fixtures replay unchanged.
- [ ] No behavior flag, route, store, or runtime emission is added.
- [ ] No migration is added.
- [ ] All standard DoD checks in `../02-prds.md` pass.

## Out of scope

- Artifact persistence or APIs.
- Runtime operation classification.
- Staging, approvals, or executor dispatch.
- UI rendering.
- Migration of existing draft or surface records.

## Guardrails

- Do not make `Surface` a subtype of `Artifact` or vice versa.
- Do not put physical paths or raw argument bodies in the ledger.
- Do not introduce a second source of enum truth.
- Do not “reserve” future values with an open string union.
- Do not modify existing event payload meaning in place.
