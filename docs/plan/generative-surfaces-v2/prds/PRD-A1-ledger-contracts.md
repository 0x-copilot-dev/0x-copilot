# PRD-A1 — Ledger vocabulary + contracts

**Goal:** the typed event vocabulary of SDR §5 (../02-sdr.md) exists as executable
contracts on both sides of the wire — a single JSON source of truth in
`packages/service-contracts`, a TypeScript mirror (`SurfaceEventV2` union + entity types)
in `packages/api-types`, pydantic mirrors in `services/ai-backend`, a ledger-id
formatter/parser (`r<short>·<seq>`) in both languages, and a golden fixture file of
example events that later PRDs (A3 server fold, B1 client fold) test against. No event is
emitted, no UI changes, no behavior changes: pure contracts, so every later wave compiles
against one vocabulary instead of inventing its own.

## Implementer brief

You are implementing this in a **fresh git worktree branched off `main`** of the
0x-copilot monorepo, with no other context. Run `make setup` from the repo root if
`services/ai-backend/.venv` or `node_modules` are missing. Test commands for every
component touched (run all before opening the PR):

```bash
# ai-backend — new tests, then the full suite
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/surfaces_v2/
cd services/ai-backend && .venv/bin/python -m pytest
# api-types
npm run test --workspace @0x-copilot/api-types
npm run typecheck --workspace @0x-copilot/api-types
# consumers of api-types must still typecheck (per packages/api-types/CLAUDE.md)
npm run typecheck --workspace @0x-copilot/frontend
npm run typecheck --workspace @0x-copilot/chat-surface
```

`packages/service-contracts` has no test suite of its own; the ai-backend and api-types
tests above exercise it. Lint/format = pre-commit (ruff + prettier).

Read these files first (repo-relative):

1. `docs/plan/generative-surfaces-v2/02-sdr.md` — §5 is the authoritative vocabulary; §4 names the contract types; §12: additive-only until E-wave.
2. `docs/plan/generative-surfaces-v2/01-problem-and-requirements.md` — FR-E1 (one ledger) + FR-G1–G4 (usage attribution) motivate this vocabulary.
3. `packages/service-contracts/src/copilot_service_contracts/surface_spec.py` — the SSOT-JSON + loader precedent to copy exactly.
4. `packages/api-types/src/adapterAllowlist.ts` — precedent: TS imports a service-contracts JSON by relative path so one file feeds both runtimes.
5. `packages/api-types/src/index.ts` (~2130–2300) — SurfaceSpec mirror block: comment style, `as const satisfies readonly T[]` tuples, guard style.
6. `services/ai-backend/src/agent_runtime/capabilities/surfaces/spec_models.py` — pydantic mirror conventions (StrEnums, `RuntimeContract` base, `_Messages` classes, "contracts only, nothing wired" stance).
7. `services/ai-backend/tests/unit/agent_runtime/surfaces/test_schema_parity.py` — the parity-test discipline to replicate.
8. `services/ai-backend/src/agent_runtime/execution/contracts.py` — `RuntimeContract` (line 40): frozen, `extra="forbid"` base for every new model.
9. `services/ai-backend/src/runtime_api/schemas/common.py` + `events.py` — the existing `RuntimeApiEventType` / `RuntimeEventEnvelope` wire vocabulary. **Context only — do not modify** (PRD-A3's job).
10. `services/ai-backend/CLAUDE.md` + `services/ai-backend/tests/CLAUDE.md` — engineering/test rules; `packages/api-types/CLAUDE.md` — contract stewardship.
11. `packages/service-contracts/pyproject.toml` — package-data is a glob; new JSON files ship in the wheel automatically (do not enumerate).

## Context

Generative Surfaces v2 re-founds the surface layer on an explicit, typed **Work
Ledger**: an agent's work on real SaaS tools renders as live artifact surfaces on a
per-run canvas; reads flow while writes stage for on-artifact approval; gates, reads,
staged writes, view derivations, and usage are all typed events appended to the
runtime's existing per-run event log, and canvas state / receipts / sources / usage
totals are _projections_ of that ledger (../02-sdr.md §2–§3, ../01 FR-E1 + §2G).

PRD-A1 is the first PR of Wave A (../03-prds.md), sitting **before** emission (A3),
metering (A2), and any UI (Wave B): it makes the SDR §5 vocabulary exist as code so the
ts and py folds written later cannot drift (the SDR §12 "double-projection" risk). The
precedent is the shipped SurfaceSpec contract — JSON SSOT in service-contracts, pydantic
mirror pinned by a parity test, TS mirror in api-types — and A1 strengthens it: the TS
side is _also_ automatically pinned to the SSOT JSON, not kept in step by convention.
Everything here is dead code at runtime by design: no producer constructs these models,
no route serves them, the `SURFACES_V2` flag is registered but read by nothing.

## Interfaces consumed / exposed

**Consumed (all pre-existing on `main`; A1 depends on no earlier v2 PRD):**

- `agent_runtime.execution.contracts.RuntimeContract` (`services/ai-backend/src/agent_runtime/execution/contracts.py:40`).
- `copilot_service_contracts` package mechanics (importlib.resources loading, glob package-data).
- api-types barrel conventions (per-domain module + re-export from `src/index.ts`, vitest node-env colocated tests).
- `_EnvFields` constant class in `services/ai-backend/src/agent_runtime/settings.py` (line 25).

**Exposed (later PRDs import these exact names; do not rename):**

- `copilot_service_contracts.work_ledger` (NEW): `LEDGER_PAYLOAD_VERSION = 1`, `LEDGER_EVENT_TYPES` (14-value ordered tuple), `WORK_LEDGER_CONTRACT_PATH`, `load_work_ledger_contract()`, `load_ledger_golden_events()`.
- `agent_runtime.surfaces_v2.ledger_models` (NEW): `LedgerEventType` StrEnum, 14 payload models, `WorkLedgerVocabulary` registry, all value enums (D1). → A3 emission, A2 `usage.recorded`, C/D waves.
- `agent_runtime.surfaces_v2.entities` (NEW): `Surface`, `StagedWrite`, `Revision`, `Decision`, `UsageRecord`, `RunReceipt`. → A3 SurfaceStore fold, D-wave decisions, E-wave receipt.
- `agent_runtime.surfaces_v2.ledger_ids` (NEW): `LedgerIdCodec.format/parse`, `LedgerIdFormatError`. → A3 projections, B2 footer.
- `@0x-copilot/api-types` (NEW barrel exports): `SurfaceEventV2`, `LedgerEventType`, `LEDGER_EVENT_TYPES`, `LedgerEventPayloadMap`, all payload interfaces + value unions, the six entity types, `formatLedgerId`, `parseLedgerId`, `isLedgerEventType`, `isSurfaceEventV2`. → B1's client projector in `packages/chat-surface`.
- Golden fixture `work_ledger_golden_events.json` — one scripted mini-run covering all 14 event types; A3's py fold and B1's ts projector must both fold this exact file and agree (their DoD in ../03-prds.md).
- Settings: `SURFACES_V2` env name in `_EnvFields` + `surfaces_v2: bool = False` field (unread until A3).

## Design

### D1 — SSOT contract JSON (service-contracts)

NEW `packages/service-contracts/src/copilot_service_contracts/work_ledger.json`.
Object-key order is contract (both languages preserve JSON insertion order):

```json
{
  "schema_version": 1,
  "payload_version": 1,
  "ledger_id": {
    "prefix": "r",
    "short_len": 3,
    "separator": "·",
    "seq_min_width": 3
  },
  "enums": {
    "auth_state": ["missing", "expired", "insufficient"],
    "gate_outcome": ["connected", "cancelled"],
    "write_policy": ["ask_first", "allow_always"],
    "action_class": ["read", "write", "unknown"],
    "classification_basis": ["catalog", "annotation", "default"],
    "surface_kind": [
      "record",
      "message",
      "table",
      "call",
      "raw",
      "receipt",
      "gate"
    ],
    "view_tier": ["raw", "generic", "shaped"],
    "view_basis": ["schema", "registry", "generated"],
    "view_keep": ["generic", "shaped"],
    "revision_author": ["agent", "user"],
    "decision_kind": ["approve", "reject", "hold", "restore"],
    "decision_actor": ["user", "policy"],
    "apply_result": ["applied", "partial", "failed"],
    "usage_purpose": ["run", "subagent", "view_shaping", "shape_request"]
  },
  "events": {
    "gate.opened": {
      "required": [
        "v",
        "gate_id",
        "connector",
        "purpose",
        "scopes",
        "auth_state"
      ]
    },
    "gate.resolved": { "required": ["v", "gate_id", "outcome"] },
    "action.classified": {
      "required": ["v", "call_id", "connector", "op", "class", "basis"]
    },
    "read.executed": {
      "required": [
        "v",
        "call_id",
        "connector",
        "op",
        "latency_ms",
        "payload_ref"
      ]
    },
    "surface.created": {
      "required": ["v", "surface_id", "kind", "source", "title", "payload_ref"]
    },
    "view.derived": { "required": ["v", "surface_id", "tier", "basis"] },
    "view.preference": { "required": ["v", "surface_id", "keep", "actor"] },
    "shape.requested": { "required": ["v", "surface_id", "actor"] },
    "write.staged": {
      "required": ["v", "stage_id", "surface_id", "target", "proposal_ref"]
    },
    "revision.added": {
      "required": ["v", "stage_id", "rev", "author", "diff_ref"]
    },
    "decision.recorded": {
      "required": ["v", "stage_id", "decision", "scope", "actor"]
    },
    "write.applied": { "required": ["v", "stage_id", "rev", "result"] },
    "usage.recorded": {
      "required": ["v", "purpose", "model", "tokens_in", "tokens_out"]
    },
    "receipt.emitted": { "required": ["v", "surface_id", "fold_ref"] }
  }
}
```

Event names, field names, and enum values are SDR §5 **verbatim** — the classification
wire key is `"class"` (a Python keyword; handled by alias, D3). Every payload carries
`v` (payload version, const 1) from day one. Optional fields (SDR §5, not in
`required`): `gate.resolved.write_policy`; `view.derived.spec_ref` + `gen {model, ms}`;
`write.staged.rows` + `agent_holds [{row_key, reason}]`; `write.applied.row_keys` +
`connector_receipt_ref`; `usage.recorded.surface_id`. `decision.recorded.scope` is
one-of `{rev}` / `{row_keys[]}`. **`actor` on `view.preference` and `shape.requested`
is the constant `"user"`** (SDR §5 pins both to user-only) — model it as
`Literal["user"]` (py) / `"user"` (ts), **not** the `decision_actor` enum (which also
permits `policy`) nor `revision_author` (which permits `agent`). It deliberately has no
`enums` entry, so the per-enum parity check does not cover it; the value is fixed in each
payload model directly. Tenant/org ids never appear in any payload —
attribution rides the run envelope (mirrors the existing rule: `RuntimeEventEnvelope`
deliberately has no `org_id`).

NEW `work_ledger.py` beside it, copying `surface_spec.py`: constants
`LEDGER_PAYLOAD_VERSION`, `LEDGER_EVENT_TYPES` (the 14 `events` keys, same order),
`WORK_LEDGER_CONTRACT_PATH: Traversable`, `load_work_ledger_contract()`, plus
`LEDGER_GOLDEN_EVENTS_PATH` / `load_ledger_golden_events()` (D5). NEW
`work_ledger.README.md` documenting the three-way mirror (JSON ⇄ pydantic ⇄ api-types),
following `surface_spec.README.md`. No re-export from `__init__.py` (matches surface_spec).

### D2 — TypeScript mirror (api-types)

NEW `packages/api-types/src/ledger.ts` (new domain module, re-exported from
`src/index.ts` like `adapterAllowlist`). It imports the SSOT JSON by relative path —
`import contract from "../../service-contracts/src/copilot_service_contracts/work_ledger.json"`
— exactly as `adapterAllowlist.ts:10` does.

> **Canonical home (2026-07-23 close-out).** `packages/api-types/src/ledger.ts` is the
> single canonical home for **all** v2 ledger/domain type additions across every wave,
> re-exported from `src/index.ts`. Where a later PRD (B3, B4, C2, D1, D3, E2, …) says "add
> to `index.ts`", read it as "add to `ledger.ts`, re-export from `index.ts`" — the barrel
> only ever gains the `export { … } from "./ledger";` line, never a type body. (Resolves
> 05-consistency-report.md close-out item 4 / the axis-6 `ledger.ts`-vs-`index.ts` nuance.)

```ts
export type LedgerEventType = "gate.opened" | "gate.resolved" | "action.classified"
  | "read.executed" | "surface.created" | "view.derived" | "view.preference"
  | "shape.requested" | "write.staged" | "revision.added" | "decision.recorded"
  | "write.applied" | "usage.recorded" | "receipt.emitted";
export const LEDGER_EVENT_TYPES = [/* same 14, same order */] as const
  satisfies readonly LedgerEventType[];

// One union per `enums` key in the JSON, values verbatim, e.g.:
export type GateAuthState = "missing" | "expired" | "insufficient";
export type ActionClass = "read" | "write" | "unknown";
export type UsagePurpose = "run" | "subagent" | "view_shaping" | "shape_request";

export interface LedgerOpRef { connector: string; op: string; }
export interface AgentHold { row_key: string; reason: string; }
export type DecisionScope =
  | { rev: number; row_keys?: never }
  | { row_keys: readonly string[]; rev?: never };

// All 14 payload interfaces, fields per D1. Exemplar (note `class` is a legal
// TS property name; the wire key stays SDR-verbatim):
export interface ActionClassifiedPayload { v: 1; call_id: string; connector: string;
  op: string; class: ActionClass; basis: ClassificationBasis; }

export interface LedgerEventPayloadMap {
  "gate.opened": GateOpenedPayload; /* ... all 14 ... */ }
/** One v2 ledger event on the wire (envelope-lite: the fields every projector folds;
 * `ledger_id` is derived, never carried — SDR §5). */
export type SurfaceEventV2 = {
  [K in LedgerEventType]: { event_type: K; run_id: string; sequence_no: number;
    created_at: string; payload: LedgerEventPayloadMap[K]; };
}[LedgerEventType];
```

Entity types (projection outputs later endpoints serve — A3 `GET
/v1/agent/runs/{id}/surfaces`, D-wave decisions, E-wave receipt; additive evolution
allowed until E-wave, removals/renames are breaking per `packages/api-types/CLAUDE.md`):

```ts
export interface Revision {
  rev: number;
  author: RevisionAuthor;
  diff_ref: string;
  created_at: string;
  ledger_id: string;
}
export interface Decision {
  decision: DecisionKind;
  scope: DecisionScope;
  actor: DecisionActor;
  decided_at: string;
  ledger_id: string;
}
export interface Surface {
  surface_id: string;
  run_id: string;
  kind: SurfaceKind;
  title: string;
  source: LedgerOpRef;
  payload_ref: string;
  ledger_id: string;
  created_at: string;
  view: {
    tier: ViewTier;
    basis: ViewBasis;
    spec_ref?: string;
    preference?: ViewKeep;
  } | null;
}
export interface StagedWrite {
  stage_id: string;
  surface_id: string;
  run_id: string;
  target: LedgerOpRef;
  proposal_ref: string;
  rows: number | null;
  agent_holds: readonly AgentHold[];
  revisions: readonly Revision[];
  decisions: readonly Decision[];
  latest_rev: number;
}
export type ReceiptAttribution =
  | "auto_ran"
  | "approved"
  | "held"
  | "rejected"
  | "auto_applied"
  | "no_view_fit"; // FR-E2 wording, wire-safe (NEW, A1-defined)
export interface UsageRecord {
  purpose: UsagePurpose;
  model: string;
  tokens_in: number;
  tokens_out: number;
  run_id: string;
  conversation_id: string;
  surface_id?: string;
  created_at: string;
  ledger_id: string;
}
export interface RunReceiptRow {
  ledger_id: string;
  event_type: LedgerEventType;
  title: string;
  attribution: ReceiptAttribution;
  at: string;
}
export interface RunReceipt {
  run_id: string;
  surface_id: string;
  fold_ref: string;
  generated_at: string;
  tiles: {
    reads_auto_ran: number;
    writes_proposed: number;
    writes_approved: number;
    holds_untouched: number;
  };
  rows: readonly RunReceiptRow[];
}
```

**Entity vs fold-projection note (2026-07-23 close-out).** `Surface` above is the **ledger
entity** (the richer canvas/B-E-wave entity); A3's `SurfaceSnapshot`/`SurfaceViewState`
(the `GET /v1/agent/runs/{run_id}/surfaces` fold output, which adds
`first_sequence_no`/`last_sequence_no`) is the **fold projection**. The two are
intentionally distinct and coexist additively — A3 does **not** edit this frozen `Surface`.
See PRD-A3 Open questions item 1 (RESOLVED).

Runtime members (only non-type exports, mirroring the `isSurfaceSpec` precedent):
`isLedgerEventType(x)`; `isSurfaceEventV2(x)` — structural: `event_type` in the map,
positive-int `sequence_no`, `payload.v === 1`, every key in the SSOT
`events[event_type].required` present; `formatLedgerId` / `parseLedgerId` (D4). Grep
confirms none of the six entity names exist in the barrel today (only
`ApprovalDecision` / `AdapterReviewDecision`). VERIFY AT IMPL: re-grep
`packages/api-types/src/index.ts` for name collisions after rebasing on latest main.

### D3 — Pydantic mirrors (ai-backend)

NEW package `services/ai-backend/src/agent_runtime/surfaces_v2/` — the SDR §3
"agent_runtime/surfaces v2" home; deliberately a sibling of (not inside)
`capabilities/surfaces` (v1, untouched). All models extend `RuntimeContract` (frozen,
`extra="forbid"` — extra/malformed keys fail as typed `pydantic.ValidationError`).

`ledger_models.py`:

```python
class LedgerEventType(StrEnum):
    GATE_OPENED = "gate.opened"
    GATE_RESOLVED = "gate.resolved"
    # ... all 14, order == LEDGER_EVENT_TYPES ...

class ActionClass(StrEnum): READ = "read"; WRITE = "write"; UNKNOWN = "unknown"
# ... one StrEnum per `enums` key in the SSOT JSON ...

class LedgerPayload(RuntimeContract):
    """Base for all v2 payloads: versioned from day one (SDR §5)."""
    v: Literal[1]   # REQUIRED, no default — see note below

# One model per event type, fields per D1. Exemplar with the alias:
class ActionClassifiedPayload(LedgerPayload):
    model_config = ConfigDict(extra="forbid", frozen=True,
                              validate_assignment=True, populate_by_name=True)
    call_id: str
    connector: str
    op: str
    action_class: ActionClass = Field(alias="class")   # wire key is SDR-verbatim
    basis: ClassificationBasis

class DecisionScope(RuntimeContract):
    rev: PositiveInt | None = None
    row_keys: tuple[str, ...] | None = None
    # model_validator: exactly one of rev / row_keys; both or neither invalid

class WorkLedgerVocabulary:
    """Event-type → payload-model registry; the single validation chokepoint."""
    PAYLOAD_MODELS: ClassVar[Mapping[LedgerEventType, type[LedgerPayload]]] = {...}
    @classmethod
    def validate_payload(cls, event_type: str, payload: Mapping[str, object]) -> LedgerPayload: ...
    # unknown event_type → LedgerContractError (typed, safe message)
```

`v` is declared **required with no default** on purpose: a Pydantic field carrying a
default is omitted from `model_json_schema()["required"]`, but the SSOT JSON lists `"v"`
first in every event's `required` array (D1) and the ts `SurfaceEventV2` interfaces make
`v` mandatory — so a `v: Literal[1] = 1` default would silently drop `"v"` from the py
schema and break `test_required_lists_match_models` (and the ts↔py parity pin). Required
`v` also makes the schema field order (`v` first, from the base class) match the JSON.
The golden events (D5) all carry `"v": 1`, so they validate unchanged.

Producers (A3+) must dump with `by_alias=True` so `"class"` hits the wire. `latency_ms`,
`tokens_in/out`, `rev`, `rows` use non-negative/positive int types per
`services/ai-backend/CLAUDE.md`; messages in a `_Messages` class; no module-level helper
functions. `entities.py` — pydantic twins of the six D2 entity types (same field
names/optionality). `ledger_ids.py` — D4. Typed errors `LedgerContractError`,
`LedgerIdFormatError` (NEW) with safe public messages.

Also: register the runtime flag — add `SURFACES_V2 = "SURFACES_V2"` to `_EnvFields`
(`settings.py` line 25 class) and `surfaces_v2: bool = False` on
`RuntimeExecutionSettings` (line 115). Nothing reads it until A3. VERIFY AT IMPL: the
exact pattern `RuntimeSettings` (line 182) uses to map `_EnvFields` names onto nested
settings fields — follow `ALLOW_EMPTY_CAPABILITIES` end-to-end.

### D4 — Ledger-id codec (`r<short>·<seq>`)

User-visible id per SDR §5: pure presentation over existing `run_id` + `sequence_no` —
never stored, never parsed back into a run lookup. Both codecs read constants from the
SSOT `ledger_id` block. NEW spec (A1 defines it; SDR left the format open):

- `format(run_id, sequence_no)`: `short` = first 3 chars of `run_id.lower()` with `-` stripped; result = `"r" + short + "·" + seq` with seq zero-padded to width 3 (`42 → "042"`; `1000 → "1000"`, no truncation). Separator is U+00B7 MIDDLE DOT (UTF-8 `0xC2 0xB7`). `sequence_no < 1` or normalized run_id shorter than 3 chars ⇒ `LedgerIdFormatError` (py) / `RangeError` (ts).
- `parse(text)`: regex `^r([a-z0-9]{3})·([0-9]{3,})$` ⇒ `{run_short, sequence_no}`; non-match ⇒ typed error (py) / `null` (ts). Charset `[a-z0-9]`, not hex-only — run ids follow `_ID_PATTERN` in `execution/contracts.py` and may contain non-hex chars.
- Round-trip property (DoD): for every `(run_id, sequence_no, ledger_id)` triple in the golden file, `format(run_id, seq) == ledger_id` and `parse(ledger_id) == (normalized_prefix, seq)` — asserted in **both** languages.

Py home: `agent_runtime/surfaces_v2/ledger_ids.py`, `class LedgerIdCodec` with
`format`/`parse` classmethods. Ts home: `formatLedgerId`/`parseLedgerId` in `ledger.ts`.

### D5 — Golden fixture

NEW `packages/service-contracts/src/copilot_service_contracts/work_ledger_golden_events.json`
(ships via the pyproject glob; loadable py-side via importlib.resources, ts-side via
relative import in the test file only — never from `ledger.ts` src). Shape:

```json
{
  "run_id": "a7f3c9d2e5b14f60a7f3c9d2e5b14f60",
  "conversation_id": "c0ffee00c0ffee00c0ffee00c0ffee00",
  "golden_ids": [
    {
      "run_id": "a7f3c9d2e5b14f60a7f3c9d2e5b14f60",
      "sequence_no": 7,
      "ledger_id": "ra7f·007"
    },
    {
      "run_id": "a7f3c9d2e5b14f60a7f3c9d2e5b14f60",
      "sequence_no": 1042,
      "ledger_id": "ra7f·1042"
    }
  ],
  "events": [
    {
      "event_type": "gate.opened",
      "run_id": "…",
      "sequence_no": 1,
      "created_at": "2026-07-23T10:00:01Z",
      "payload": {
        "v": 1,
        "gate_id": "gate_01",
        "connector": "linear",
        "purpose": "to read ENG-142",
        "scopes": ["read:issues"],
        "auth_state": "missing"
      }
    }
  ]
}
```

The `events` array is one scripted mini-run, monotonic `sequence_no`, covering **all 14
event types at least once** plus variants: `view.derived` generic then shaped,
`decision.recorded` with `{rev}` and with `{row_keys}` scope, `write.staged` with
`rows` + `agent_holds`, `write.applied` `applied` and `partial`, `surface.created` for
a `record` and for the `receipt`. This exact file is what A3's py SurfaceStore fold and
B1's ts projector both consume; any post-A1 edit is a contract change requiring both
folds' review.

**Error behavior:** unknown `event_type` ⇒ `LedgerContractError` / guard false; extra
keys, wrong enum value, `v != 1`, both-or-neither `DecisionScope` ⇒
`pydantic.ValidationError` / guard false — never a silent pass. No HTTP surface exists
in this PR, so no new API error shapes.

### D6 — Forward-additive registry (documentation only; later PRDs implement)

A1 ships the SDR §5 vocabulary **as it stands at Wave A** — the 14 event types of D1 with
their SDR §5 required/optional fields. Later waves extend this vocabulary **additively**
(SDR §12: additive-only until E-wave; `v` stays `1` because every addition is an optional
field or an appended event type, never a removal/rename/retype). This registry is the
contract home's record of that evolution so no later PRD reinvents a name; **A1 emits
none of it** — each row lands with its own PRD, which owns the JSON/pydantic/api-types
edit + both parity tests at merge time. When a later PRD merges, its additions append to
`work_ledger.json` / `LEDGER_EVENT_TYPES` / the ts union in the same additive style A1
established; A1's base 14-tuple order is never reordered, only appended.

| Owning PRD | Event                                 | Additive field(s) — all optional, `v:1`                                                                                                                                     |
| ---------- | ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D1         | `revision.added`                      | `proposal_ref` (snapshot of this rev), `authorship_spans: [{start,end,author}]` (`[]` for rev 1)                                                                            |
| D1         | `decision.recorded`                   | `scope` narrowed to `{rev}` for single-artifact (`{row_keys[]}` is D3)                                                                                                      |
| D2         | `write.applied`                       | `failure: {code, detail}` (failed only), `decided_by: {actor, decision_seq}`                                                                                                |
| D3         | `write.staged`                        | `rows: n`, `agent_holds: [{row_key, reason}]` (both already optional in SDR §5)                                                                                             |
| D3         | `revision.added`                      | `rowset: {rows: [StagedRow…]}` (full row data)                                                                                                                              |
| D3         | `decision.recorded`                   | `scope: {row_keys[]}` activated, `apply: true` (apply-scoped approve only)                                                                                                  |
| D3         | `write.applied`                       | `row_keys[]`, `result: "partial"` activated, `row_results: [{row_key, outcome, detail?}]`                                                                                   |
| B3         | `view.derived`                        | `gen.ms` + `spec_ref` populated (A3 emits `gen.model` only)                                                                                                                 |
| **B4**     | **`shape.resolved`** (NEW 15th event) | `{v, surface_id, outcome: shaped\|no_fit, reason?}` — appended to `LEDGER_EVENT_TYPES` / enum / ts union additively; named symmetrically with `gate.opened`/`gate.resolved` |

E3 closes the compat window and **freezes** the vocabulary (no more additions). The
export bundle's synthetic `"receipt.export"` row (E3) is an export-format construct, **not**
a ledger event type — it is never appended to `LEDGER_EVENT_TYPES` nor to the run stream.

## Implementation plan

1. **service-contracts SSOT** — create `packages/service-contracts/src/copilot_service_contracts/work_ledger.json` (D1), `work_ledger.py`, `work_ledger_golden_events.json` (D5), `work_ledger.README.md`.
2. **ai-backend models** — create `services/ai-backend/src/agent_runtime/surfaces_v2/__init__.py` (docstring: "Work Ledger contracts — nothing wired until PRD-A3"), `ledger_models.py`, `entities.py`, `ledger_ids.py` (D3, D4).
3. **Flag registration** — modify `services/ai-backend/src/agent_runtime/settings.py`: `SURFACES_V2` in `_EnvFields`, `surfaces_v2: bool = False` on `RuntimeExecutionSettings`, env mapping per existing pattern.
4. **api-types mirror** — create `packages/api-types/src/ledger.ts` (D2); modify `packages/api-types/src/index.ts`: add the `export { … } from "./ledger";` block beside the `adapterAllowlist` export, plus a pointer comment in the SurfaceSpec block.
5. **Tests** — files in Test plan below.
6. **Docs** — if any field/name diverged from SDR §5 during implementation, update `docs/plan/generative-surfaces-v2/02-sdr.md` §5 in the same PR (standard DoD).
7. Run every command in the Implementer brief; run pre-commit.

## Test plan

Python (mixins for fixtures per tests/CLAUDE.md; assert typed error classes + safe
messages; all sync):

- `services/ai-backend/tests/unit/agent_runtime/surfaces_v2/__init__.py` (empty).
- `.../test_ledger_contract_parity.py` — the keystone, replicating `test_schema_parity.py` against `load_work_ledger_contract()`: `test_event_type_values_match_contract`; `test_event_type_order_is_stable` (ordered, incl. `LEDGER_EVENT_TYPES` tuple); `test_every_event_type_has_a_payload_model` (registry covers all 14, nothing extra); `test_enum_values_match_contract` (each StrEnum vs its `enums` list, order included); `test_required_lists_match_models` (per event type: `model_json_schema(by_alias=True)["required"]` == JSON `required`); `test_payload_version_const_is_one`; `test_golden_events_all_validate` (every golden event through `WorkLedgerVocabulary.validate_payload`; all 14 types present); `test_no_org_or_user_fields_on_any_payload` (adversarial: wire-shape tenancy rule).
- `.../test_ledger_models.py` — `test_unknown_event_type_raises_typed_error` (`LedgerContractError` + safe message); `test_extra_keys_rejected`, `test_wrong_enum_value_rejected`, `test_v_field_other_than_one_rejected` (adversarial malformed inputs); `test_decision_scope_requires_exactly_one_of_rev_row_keys` (both / neither fail, each alone passes); `test_action_classified_round_trips_class_alias` (`"class"` in; `by_alias=True` dump emits `"class"` back).
- `.../test_ledger_ids.py` — `test_format_matches_golden_triples`; `test_parse_round_trips_golden_triples`; `test_format_pads_to_three_and_grows` (`7→"007"`, `1042→"1042"`); `test_format_rejects_sequence_below_one` + `test_format_rejects_short_run_id` (both `LedgerIdFormatError`); `test_parse_rejects_malformed` (adversarial table: wrong prefix, ASCII `.` or `*` for `·`, uppercase short, 2-char short, 2-digit seq, trailing junk, empty string).

TypeScript — `packages/api-types/src/ledger.test.ts` (node env, colocated):
`LEDGER_EVENT_TYPES matches the service-contracts JSON` (deep-equal incl. order — the
ts↔py parity pin, transitively through the SSOT); `every enum union tuple matches
contract enums`; `all golden events pass isSurfaceEventV2` + `guard rejects unknown
type / missing required key / v !== 1` (adversarial mutations of a golden event);
`formatLedgerId/parseLedgerId round-trip the golden_ids triples` + malformed-parse
table returns `null`; type-level `satisfies` assertions pinning `LedgerEventPayloadMap`
keys to `LedgerEventType` (compile-time via `npm run typecheck`).

Live smoke (contracts-only PR — the smoke proves _nothing changed_):

1. `make dev` from repo root (backend :8100, ai-backend :8000, facade :8200, UI :5173).
2. `export TOKEN=$(make dev-bearer)`; sanity: `curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8200/v1/me/profile`.
3. Create a conversation + run and stream it via the facade per the recipes in `docs/dev-testing.md` (always :8200, never :8000 directly). Confirm the SSE stream contains only existing `RuntimeApiEventType` values — no dotted v2 event names.
4. Repeat step 3 with `SURFACES_V2=true` in ai-backend's env: stream must be identical (flag registered but unread).
5. `npm run build --workspace @0x-copilot/frontend` still green.

## Definition of done

From ../03-prds.md PRD-A1 (binding minimums, never weakened):

- [ ] **Cross-language parity test pins ts ↔ py shapes** (same discipline as the SurfaceSpec schema parity test). Artifacts: `test_ledger_contract_parity.py` green (pydantic ⇄ SSOT JSON) **and** `ledger.test.ts` green (ts ⇄ same SSOT JSON + same golden events) — drift on any side fails CI.
- [ ] **Golden fixture file of example events checked in** (consumed later by A3/B1 projector tests). Artifact: `work_ledger_golden_events.json` covering all 14 event types + listed variants, validated by both languages' suites.
- [ ] **Ledger-id formatter + parser round-trip tested in both languages.** Artifact: `test_ledger_ids.py` + the `golden_ids` cases in `ledger.test.ts`, both consuming the same triples from the fixture file.

Standard DoD (every PRD):

- [ ] Unit tests pass in the owning components: full ai-backend suite via its own `.venv`, `npm run test --workspace @0x-copilot/api-types`; typechecks for api-types, frontend, chat-surface; frontend build green.
- [ ] Flags off ⇒ byte-identical behavior. Artifact: live-smoke steps 3–4 (stream identical with flag unset and set); grep proves no runtime code path imports `surfaces_v2` outside the new modules and their tests.
- [ ] No service-boundary violations: no `apps/*` change, no cross-`src/` import; the only cross-package file read is the sanctioned service-contracts JSON pattern.
- [ ] New LLM call sites: none (this PR adds no model invocation).
- [ ] Docs: SDR §5 updated in-PR if implementation diverged.
- UI DoD: not applicable (no UI is touched — Wave B owns design-parity runs).

## Out of scope

- Any event **emission** — no `RuntimeEventProducer` call, no worker/handler change, and no new members in `RuntimeApiEventType` (`services/ai-backend/src/runtime_api/schemas/common.py`) or the api-types `RuntimeApiEventType` union: api-types mirrors what the server actually serves, and the server serves nothing new until PRD-A3 wires emission + projector allow-lists via the established recipe in `runtime_api/schemas/events.py`.
- Any HTTP route, facade passthrough, or migration (`runtime_events.event_type` is a text column; the vocabulary needs no DDL).
- UsageMeter seam / usage index tables / reconciliation with the existing `RuntimeModelCallUsageRecord` machinery (`services/ai-backend/src/agent_runtime/persistence/records/telemetry.py`) — PRD-A2. VERIFY AT IMPL (flagged for A2, noted here): the existing telemetry `purpose` column defaults to `"main"`; A2 must map it onto this PR's `UsagePurpose` values without breaking existing rows.
- Any UI / chat-surface / surface-renderers change; the client projector fold is B1.
- Classification catalogs, gate logic, staging/commit logic (C/D waves) — A1 only names their vocabulary.
- Changing the v1 SurfaceSpec contract or `result["surface"]` appendage (compat window ends at E3).

## Guardrails

- **Service boundaries are hard.** Apps call the facade only; no deployable imports another's `src/`; `packages/service-contracts` stays constants + JSON + trivial loaders — therefore the ledger-id _codec logic_ lives in ai-backend and api-types, only its _constants_ live in the SSOT JSON. backend (:8100) is untouched.
- **Flag-off byte-identical** (SDR §11): `SURFACES_V2` defaults false and is read by nothing in this PR; any behavioral diff in the live smoke is a blocker.
- **Additive-only vocabulary until E-wave** (SDR §12): later PRDs may add optional fields/enum values; removing or renaming anything in `work_ledger.json` after A1 merges is breaking per `packages/api-types/CLAUDE.md` and touches both parity tests.
- **ai-backend rules** (`services/ai-backend/CLAUDE.md`): Pydantic at every boundary; no module-level helper functions (codec/vocabulary are classes); nested constant classes instead of inline string literals; typed domain errors with safe public messages; frozen models via `RuntimeContract`.
- **ai-backend test rules** (`services/ai-backend/tests/CLAUDE.md`): fakes/mixins, no network or live LLM calls; assert typed error classes and safe messages; valid + invalid parsing for every new contract.
- **api-types rules** (`packages/api-types/CLAUDE.md`): type-only mirrors plus the narrow sanctioned runtime members (guards, const tuples, the id codec — precedent: `isSurfaceSpec`, `SURFACE_ARCHETYPES`); no fetch wrappers; no `/internal/v1/*` shapes; the server remains the source of truth.
- **Wire-shape tenancy rule:** no `org_id`/`user_id` on any payload or entity — attribution rides the run envelope server-side (mirrors the `RuntimeEventDraft` vs `RuntimeEventEnvelope` split in `runtime_api/schemas/events.py`).
- Work only in the worktree; never touch the main checkout; run pre-commit before pushing.
