# Work Ledger Vocabulary — Single Source of Truth

`work_ledger.json` is the canonical specification of the **Work Ledger** event
vocabulary: the typed events an agent's work appends to the
runtime's per-run event log, from which canvas state, receipts, sources, and
usage totals are pure _projections_. It is the frozen cross-PRD interface for
the Generative Surfaces v2/v2.1 effort (PRD-A1). Three mirrors derive from
this one file:

- **AI backend** —
  `services/ai-backend/src/agent_runtime/surfaces_v2/ledger_models.py`
  declares the pydantic payload models + `LedgerEventType` StrEnum + value
  enums, and `WorkLedgerVocabulary.validate_payload` gates an untrusted dict
  against them (loaded via
  `copilot_service_contracts.work_ledger.load_work_ledger_contract`).
- **Frontend / api-types** — `packages/api-types/src/ledger.ts` mirrors the
  `SurfaceEventV2` union, payload interfaces, value unions, entities, strict
  writer guard, digest helpers, and codecs. It imports this JSON directly by
  relative path so the same on-disk file drives its guards.

Cross-language parity tests
(`test_ledger_contract_parity.py`, `ledger.test.ts`) assert that the pydantic
models' field/enum/required sets **and** the ts union tuples match this file.
If any of the three drift, CI fails — the JSON, the pydantic models, and the ts
types cannot disagree silently.

The JSON is Python-primary and lives here (a constants-only package); the
codec _logic_ lives in ai-backend and api-types, only its _constants_
(`ledger_id` block) live in this file — service-contracts stays constants, JSON,
and trivial loaders.

## The contract

```jsonc
{
  "schema_version": 2,
  "payload_version": 1, // every event payload carries `v: 1` from day one
  "ledger_id": {
    // user-visible id format `r<short>·<seq>` (e.g. `ra7f·042`)
    "prefix": "r",
    "short_len": 3, // first 3 chars of run_id.lower() with `-` stripped
    "separator": "·", // U+00B7 MIDDLE DOT
    "seq_min_width": 3, // zero-pad seq to width 3, grow beyond without truncation
  },
  "enums": {
    "auth_state": ["missing", "expired", "insufficient"],
    // ... one entry per closed value set in the vocabulary ...
  },
  "events": {
    "gate.opened": {
      "required": [
        "v",
        "gate_id",
        "connector",
        "purpose",
        "scopes",
        "auth_state",
      ],
    },
    // ... 32 events total, in append-only contract order ...
  },
}
```

### Event types (contract order)

The original 15 v2 values remain first and unchanged. v2.1 appends
`operation.*`, `artifact.*`, `effect.*`, and generalized
`gate.opened.v2`/`gate.resolved.v2` values. The `.v2` suffix prevents a
payload-meaning change to legacy gate events.

Order is part of the contract: the JSON `events` insertion order, the
`LEDGER_EVENT_TYPES` tuple, the pydantic `LedgerEventType` StrEnum, and the ts
`LEDGER_EVENT_TYPES` tuple all list them identically. Later waves **append**
(never reorder or remove).

### Required vs optional fields

Each event declares both `required` (always `v` first) and `optional` keys.
Python rejects extras through `RuntimeContract`; TypeScript's
`isLedgerPayloadForWrite` rejects missing/extra fields, unknown closed-enum
values, malformed v2.1 ids/digests/refs/scalars, and cross-reference
mismatches. `isSurfaceEventV2` remains a replay-tolerant read guard.

### The `class` wire key

`action.classified` carries `class` on the wire (SDR-verbatim). `class` is a
Python keyword, so the pydantic model stores it as `action_class` with
`Field(alias="class")`; producers dump `by_alias=True`. It is a legal TS
property name, so `ledger.ts` keeps it as `class`.

### `actor` on `view.preference` / `shape.requested`

Both pin `actor` to the constant `"user"` (SDR §5) — modelled as
`Literal["user"]` (py) / `"user"` (ts), **not** the `decision_actor` enum
(which also permits `policy`). It deliberately has no `enums` entry.

### Ownership and wire-shape tenancy

No event payload carries `org_id` / `user_id`; attribution rides the run
envelope. Durable v2.1 `Artifact` entities explicitly carry ownership because
they can outlive a run. Legacy per-run projection entities remain unchanged.

## v2.1 identifiers, references, and digests

`OperationIdCodec`, `ArtifactIdCodec`, and `EffectStageIdCodec` accept only
prefixed canonical lowercase UUID4/UUID7 values. Reference codecs accept only
the exact `artifact://`, `operation://`, `proposal://`, `receipt://effects`,
and opaque `workspace-target://` shapes in the JSON. Physical host paths,
traversal, extra segments, revision zero, and overlong values fail closed.

Structured digests use UTF-8 canonical JSON (code-point-sorted object keys,
dense order-preserving arrays, no insignificant whitespace, finite numbers)
and lowercase SHA-256. `work_ledger_v2_1_vectors.json` is the Python/TypeScript
referee.

## Golden journeys

`work_ledger_v2_1_golden_journeys.json` contains deterministic journeys for
chat-only work, model/subagent artifacts, MCP reads and writes, workspace
staging/commit/drift, crash reconciliation, generalized gates, and destructive
holds. Every prefix must fold without throwing; both language tests compare
final artifact/stage/canvas/receipt/pending snapshots to the same fixture.

## Legacy read compatibility

`project_legacy_ledger_for_read` (Python) and `projectLegacyLedgerForRead`
(TypeScript) replay the old v2 fixture into one shared compatibility snapshot.
They retain legacy call/stage identifiers and the information actually present
in old payloads; they never invent v2.1 operation ids, digests, policy
snapshots, or valid writer payloads. Legacy gates remain readable and are
explicitly marked invalid as generalized-gate write inputs. The expected
snapshot lives in `work_ledger_v2_1_vectors.json`.

## The ledger id — `r<short>·<seq>`

Pure presentation over the existing `run_id` + `sequence_no`; never stored,
never parsed back into a run lookup. Both codecs read the constants from the
`ledger_id` block.

- **format** `(run_id, sequence_no)` → `"r" + short + "·" + seq`, where `short`
  is the first 3 chars of `run_id.lower()` with `-` stripped and `seq` is
  zero-padded to width 3 (`7 → "007"`, `1042 → "1042"`, no truncation).
  `sequence_no < 1` or a normalized run_id shorter than 3 chars raises
  `LedgerIdFormatError` (py) / throws `RangeError` (ts).
- **parse** `(text)` matches `^r([a-z0-9]{3})·([0-9]{3,})$` → `{run_short,
sequence_no}`; a non-match is a typed error (py) / `null` (ts).

## Versioning policy

The vocabulary is **additive-only** until the E-wave freeze (SDR §12): later
PRDs may add optional fields, enum values, or appended event types (`v` stays
`1` because every addition is optional). Removing or renaming anything here
after A1 merges is a **breaking** contract change and touches all three mirrors
and both parity tests. When you change this file:

1. Update the pydantic mirror (`ledger_models.py` / `entities.py`) in the same PR.
2. Update the TypeScript mirror (`packages/api-types/src/ledger.ts`).
3. The parity tests enforce (1) and (2); keep them green.

## What is NOT here

- The pydantic payload models + validator (live in ai-backend `surfaces_v2/`).
- The ts types + guards + codec (live in `packages/api-types/src/ledger.ts`).
- New v2.1 event **emission**, artifact persistence, projections, and routes
  (later PRs — A1 adds contracts only).

This file is a vocabulary contract only.
