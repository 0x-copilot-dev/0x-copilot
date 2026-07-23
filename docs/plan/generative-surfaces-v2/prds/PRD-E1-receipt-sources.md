# PRD-E1 — Receipt + Sources 🎨

Give every run its accountability artifacts as **pure folds of the Work Ledger**: a
**run receipt** surface (stat tiles over per-action rows, each row carrying its stable
ledger id `r<short>·<seq>` and a decision attribution — "auto-ran" / "you approved" /
"you held" / "no view fit") emitted at run termination as `surface.created
{kind: "receipt"}` + `receipt.emitted`, and a **Sources** rail tab listing everything
read this run, grouped by connector, with time + ledger id + qualifiers. Neither
artifact holds hand-assembled state: the receipt is a deterministic fold of the ledger
in both languages (py fold = emission + E3 export; ts fold = rendering), pinned to each
other by the shared golden fixtures. Flag off ⇒ byte-identical behavior. FR-E2/E3,
NFR-6; SDR §7 S6.

## Implementer brief

You are implementing this in a **fresh git worktree branched off `main`** of the
0x-copilot monorepo (repo root = worktree root); never commit on `main`. Run
`make setup` once if `services/*/.venv` or `node_modules` are missing. Components
touched: `services/ai-backend` (Python 3.13), `packages/chat-surface` (TypeScript),
`packages/service-contracts` (the expected-receipt fixture JSON + its
`load_ledger_expected_receipt()` loader in `work_ledger.py`, mirroring A1's
`load_ledger_golden_events`). Test commands (from repo root unless
noted):

```bash
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/surfaces_v2/
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_worker/test_receipt_emission.py
cd services/ai-backend && .venv/bin/python -m pytest            # full suite before PR
npm run test --workspace @0x-copilot/chat-surface
npm run typecheck --workspace @0x-copilot/chat-surface
npm run lint --workspace @0x-copilot/chat-surface               # substrate-purity gate
npm run typecheck --workspace @0x-copilot/api-types             # consumed types only
npm run build --workspace @0x-copilot/frontend
# design-parity live-render harness (own vitest root):
node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs
```

Read these files first (repo-relative):

1. `docs/plan/generative-surfaces-v2/02-sdr.md` — §5 vocabulary (authoritative,
   verbatim), §7 S6 (this PR's sequence), §10 item 6 ("receipt = fold"), §11 compat.
2. `docs/plan/generative-surfaces-v2/01-problem-and-requirements.md` — FR-E2/E3 incl.
   the quoted microcopy that is contract, FR-C8/C9 (receipt rows for bypass + holds).
3. `docs/plan/generative-surfaces-v2/prds/PRD-A1-ledger-contracts.md`, `PRD-A3-…`,
   `PRD-D2-…`, `PRD-D3-…` — their **Exposed** sections are your consumed contracts.
4. `services/ai-backend/src/agent_runtime/surfaces_v2/` (as merged by A1/A3/D1) —
   `ledger_models.py`, `entities.py`, `ledger_ids.py`, the emitter, `staging.py` fold.
5. `services/ai-backend/src/runtime_worker/handlers/run.py` — `handle()` terminal path
   - `RunTerminationCoordinator.terminate` (where the ReceiptEmitter hooks) and
     `_build_surface_generation_scheduler` (~L1570, the bound-emitter precedent).
6. `services/ai-backend/src/runtime_api/schemas/events.py` + `common.py` — projector
   allow-list pattern (`_surface_spec_generated_payload`) + `RuntimeApiEventType`.
7. `packages/chat-surface/src/destinations/run/projectCitations.ts` — the pure
   peer-selector pattern (FR-3.3) both new selectors copy.
8. `packages/chat-surface/src/destinations/run/RunWorkspaceRail.tsx` — the rail's
   `sources` prop + `SourcesTab` mount you extend (`[Chat · Agents · Approvals · Sources]`).
9. `packages/chat-surface/src/thread-canvas/ledgerProjection.ts` (B1) — the client
   ledger fold + `tabUriForSurface` (`receipt` keeps its own scheme, tier-3 today).
10. `packages/chat-surface/CLAUDE.md`, `services/ai-backend/CLAUDE.md`,
    `services/ai-backend/tests/CLAUDE.md`, `tools/design-parity/SKILL.md` — binding
    rules (see Guardrails) + the parity pipeline.

## Context

Generative Surfaces v2 renders an agent's work on real SaaS tools as live artifact
surfaces on a per-run canvas: reads flow, writes stage and are decided on the artifact
with a what-you-approve-is-what-executes guarantee, and every consequential act is a
typed event on an append-only **Work Ledger** riding the runtime's existing per-run
event log; everything user-visible is a projection of that ledger
(../02-sdr.md §2–§3). The requirements contract is ../01-problem-and-requirements.md:
FR-E2 (run receipt as a surface: stat tiles over a per-action ledger with decision
attribution, "Assembled from the run ledger · immutable"), FR-E3 (Sources panel:
everything read, grouped by connector, time + ledger id + qualifiers), NFR-6
(receipt assembled from the ledger, not the narrative).

This PR opens Wave E (../03-prds.md PRD-E1). Everything it folds already exists:
A1 shipped the contracts (incl. the `RunReceipt` entity and `ReceiptAttribution`
union), A3 the ledger emission + SurfaceStore fold behind `SURFACES_V2`, B1/B2 the
canvas tabs + provenance footers, C2 gates, D1–D3 staging/commit — so
`read.executed`, `write.staged`, `decision.recorded`, `write.applied` (incl. row
scope and `actor: "policy"` auto-applies) are all on the stream. E1 adds **no new
decision or write path**: it folds those events into the receipt surface + Sources
tab, and emits exactly two events at run termination per SDR §7 S6. PRD-E2 owns the
Approvals queue/Agents tab; PRD-E3 hardens this receipt into a tamper-evident export
and consumes this PR's py fold.

## Interfaces consumed / exposed

**Consumed (must exist on `main` from earlier waves — reconcile names against merged
code before writing yours):**

- PRD-A1: entities `RunReceipt`, `RunReceiptRow`, `ReceiptAttribution`
  (`"auto_ran"|"approved"|"held"|"rejected"|"auto_applied"|"no_view_fit"`) in
  `packages/api-types` + pydantic twins in
  `services/ai-backend/src/agent_runtime/surfaces_v2/entities.py`; `LedgerEventType`
  constants; `LedgerIdCodec.format` (py) / `formatLedgerId` (ts) producing
  `r<short>·<seq>`; golden fixture `work_ledger_golden_events.json` in
  `packages/service-contracts`. `VERIFY AT IMPL:` exact merged symbol names + fixture path.
- PRD-A3: the v2 ledger emitter (per-run wrapper over
  `RuntimeEventProducer.append_api_event`, bound in `runtime_worker/handlers/run.py`),
  the `SURFACES_V2` flag accessor (`surfaces_v2/config.py`), the v2
  `RuntimeApiEventType` members (`surface.created` exists — E1 adds only
  `receipt.emitted`), replay via `event_store.list_events_after(org_id=…,
run_id=…, after_sequence=0)` (keyword-only on `EventStorePort`).
  `VERIFY AT IMPL:` emitter class name + bind seam as merged.
- PRD-B1: `projectLedger`/`tabUriForSurface` in
  `packages/chat-surface/src/thread-canvas/ledgerProjection.ts` (kind `receipt` tabs
  already; scheme `receipt://` has no adapter ⇒ tier-3 until E1), the `surfacesV2`
  prop on `RunDestinationProps`, host flag helpers `isSurfacesV2CanvasEnabled()` /
  `isSurfacesV2Enabled()`, and the canvas surface-mount seam — URI-scheme→adapter resolution in
  `packages/chat-surface/src/thread-canvas/TcSurfaceMount.tsx` — that D1 used to mount
  `StagedDraftSurface` (receipt tabs already carry the `receipt://` scheme via
  `tabUriForSurface`, tier-3 until E1 registers an adapter for it).
  `VERIFY AT IMPL:` the exact merged adapter-registration API in `TcSurfaceMount.tsx`.
- PRD-B2: the provenance footer (`SurfaceProvenance`) reused on the receipt surface;
  host callback `onCopyText` on `RunDestinationProps` → `ThreadCanvasProps` (B2
  reserved it for E1's "Copy receipt"). `VERIFY AT IMPL:` exported names as merged.
- PRD-D1/D2/D3 payloads folded here (SDR §5 verbatim): `write.staged {stage_id,
surface_id, target{connector,op}, proposal_ref, rows?, agent_holds?}`;
  `decision.recorded {stage_id, decision, scope: {rev}|{row_keys[]}, actor:
user|policy}`; `write.applied {stage_id, rev, row_keys?, result:
applied|partial|failed, connector_receipt_ref?}`; `StagedWriteFold` /
  `StagedWriteState` in `surfaces_v2/staging.py` (E1 reuses this fold for stage
  status — never re-derives it a second way).
- Existing: `RunWorkspaceRail.tsx` (`sources` prop, `RunRailTabId "sources"`),
  `projectCitations.ts`, `RuntimeEventEnvelope`, `RunTerminationCoordinator`.

**Exposed (later PRDs rely on these — keep names stable):**

- `ReceiptFold.fold(run_id, events) -> RunReceipt` (py) + the `fold_ref` scheme
  `"ledger://<run_id>@<through_seq>"` — E3's `/receipt/export` re-folds with it.
- `ReceiptEmitter` + the `receipt.emitted` emission (its ONLY producer) +
  `RuntimeApiEventType.RECEIPT_EMITTED` — E3's export precondition; the
  emit-at-every-terminal-status rule (E3's 409-on-non-terminal mirrors it).
- Expected-receipt golden fixture `work_ledger_expected_receipt.json` — checked in to
  `packages/service-contracts/src/copilot_service_contracts/` **beside A1's
  `work_ledger_golden_events.json`** (NOT beside A3's py-only test snapshot: this
  fixture must be consumable by BOTH the py fold and the ts parity test, so it lives in
  the shared cross-language package exactly as the golden-events fixture does). E3's
  tamper property tests re-fold against it.
- ts: `projectReceipt`, `ReceiptProjection`, `projectLedgerSources`,
  `LedgerSourcesProjection`, `ReceiptSurface`, `LedgerSourcesTab`, and the
  `ledgerSources` prop on `RunWorkspaceRailProps` — Phase-2 failure-path designs and
  E3's cutover restyle/retire around these.

## Design

### 1. Events consumed and emitted (SDR §5 verbatim; payloads carry `v: 1`)

E1 folds (read-only): `read.executed`, `surface.created`, `view.derived`,
`write.staged`, `decision.recorded`, `write.applied`. It tolerates and ignores every
other type (`gate.*`, `usage.recorded`, `shape.requested`, `view.preference`,
`revision.added`, future vocabulary). E1 emits exactly two, at run termination:

```text
surface.created  {v, surface_id: "receipt://<run_id>", kind: "receipt",
                  source: {connector: "runtime", op: "receipt"},
                  title: "Run receipt", payload_ref: <fold_ref>}
receipt.emitted  {v, surface_id: "receipt://<run_id>", fold_ref: "ledger://<run_id>@<through_seq>"}
```

NEW definitions (E1 owns them): `surface_id` is the stable `"receipt://<run_id>"` —
one receipt surface per run; re-emission on a later terminal transition upserts per
A3's fold rules. `fold_ref = "ledger://<run_id>@<through_seq>"` where `through_seq` is
the highest `sequence_no` folded — the receipt is re-derivable by folding the run's
events with `sequence_no <= through_seq` (NFR-5/6; E3's export verifies exactly this).
`source.connector = "runtime"` / `op = "receipt"` are NEW constant values (the receipt
has no SaaS connector). Wire mechanics: add `RECEIPT_EMITTED` to
`RuntimeApiEventType` (`src/runtime_api/schemas/common.py`) with its value sourced from
the A1 enum member `LedgerEventType.RECEIPT_EMITTED.value` (in
`agent_runtime.surfaces_v2.ledger_models`), not a string literal — the same import
pattern A3 used for `SURFACE_CREATED` et al. + allow-list `_receipt_emitted_payload` (keys `v`, `surface_id`,
`fold_ref` only) + `activity_kind_for` → `RuntimeActivityKind.EVENT` in
`src/runtime_api/schemas/events.py`. `event_type` is a text column — no migration.
Note: `fold_ref`/`payload_ref` contain `"ref"` ⇒ the projector marks
`redaction_state=OFFLOADED` (they ARE references; keep A3's stance, pin with a test).

### 2. Receipt fold (py) — `services/ai-backend/src/agent_runtime/surfaces_v2/receipt.py` (NEW)

```python
class ReceiptFold:
    """Pure fold: run events in -> RunReceipt out. No IO, no clock, no state."""
    @classmethod
    def fold(cls, *, run_id: str, events: Sequence[RuntimeEventEnvelope]) -> RunReceipt: ...
    @classmethod
    def fold_raw(cls, *, run_id: str, events: Sequence[Mapping[str, object]]) -> RunReceipt: ...
    # fold_raw takes {event_type, sequence_no, created_at, payload} dicts so the A1
    # golden fixture feeds py and ts identically (A3 fold_raw precedent).
```

Fold rules (deterministic and total; process in ascending `sequence_no`; malformed or
unknown payloads are skipped, never raised; reuse `StagedWriteFold` for stage status
rather than re-deriving it):

- **Tiles** (`RunReceipt.tiles`, row-granular where rows exist):
  `reads_auto_ran` = count of `read.executed`; `writes_proposed` = Σ over
  `write.staged` of `(rows ?? 1)`; `writes_approved` = Σ over `write.applied` with
  `result` in `{applied, partial}` of `(len(row_keys) ?? 1)`; `holds_untouched` = Σ
  over stages of the held remainder — for a stage with a `write.applied`:
  `(rows ?? 1) − applied_count`; for a stage whose `StagedWriteFold` status is
  `StagedWriteStatus.REJECTED` with no apply: `(rows ?? 1)`; stages whose status is
  `STAGED` or `APPROVED` with no apply contribute 0 (pending work is E2's queue, not a
  receipt hold). Here `rows` is read from the `write.staged` payload (`rows?: n`) and
  `applied_count = len(row_keys) ?? 1` from the matching `write.applied` payload — D1's
  `StagedWriteState` carries no row data, so E1 reads row counts from the raw events and
  uses `StagedWriteFold` **only** for the per-stage terminal status.
  `VERIFY AT IMPL:` reconcile tile arithmetic with the A1
  golden fixture's expected receipt — the fixture wins; change it only with both
  folds' review.
- **Rows** (`RunReceipt.rows`, one per consequential event, sequence order; every
  row's `ledger_id = LedgerIdCodec.format(run_id, <event sequence_no>)`, `at` = the
  event's `created_at`, and `event_type` = the `LedgerEventType` of the source event
  the row is anchored to — for the aggregated held-remainder row that is
  `decision.recorded` when anchored to the last hold decision, else `write.applied`
  (A1's `RunReceiptRow.event_type` is required — populate it on every row)):
  - `read.executed` → attribution `auto_ran`; title = the `surface.created` title
    sharing this call's `payload_ref` when one exists, else `"<connector> · <op>"`.
  - `write.applied` → attribution `approved` when the matching approve
    `decision.recorded` has `actor: "user"`, `auto_applied` when `actor: "policy"`
    (allow-always, FR-C8); title = the stage's surface title.
  - `decision.recorded {decision: "reject", actor: "user"}` → attribution `rejected`.
  - Held remainder: for each stage with held rows at apply time (or rejected whole),
    ONE aggregated row, attribution `held`, title `"<n> rows held, untouched"`
    (n = the held count above; FR-C9), anchored to the sequence_no of the last hold
    decision (else the `write.applied`).
  - `surface.created {kind: "raw"}` and `view.derived {tier: "raw"}` → attribution
    `no_view_fit` (one row per surface, first raw event wins; FR-E2 "no view fit").
  - `approve`/`hold`/`restore` decisions and all other events produce no standalone
    row (approve surfaces via its `write.applied`; restore is transitional).
- **Determinism:** `generated_at` = `created_at` of the highest-sequence folded event
  (never wall-clock — refolding must be identity). `surface_id`/`fold_ref` per §1.
  Same events ⇒ byte-identical `RunReceipt`; input is re-sorted by `sequence_no`
  before folding.

### 3. Receipt emission — `ReceiptEmitter` (same file)

```python
class ReceiptEmitter:
    """Sole producer of receipt.emitted. Constructed by the run handler only when
    SurfacesV2Flag is enabled; best-effort (exceptions logged, never propagated)."""
    def __init__(self, *, event_producer, event_store): ...
    async def emit_for_run(self, *, run: RunRecord) -> None:
        # events = await event_store.list_events_after(org_id=run.org_id, run_id=run.run_id,
        #                                              after_sequence=0)  # keyword-only, §1 signature
        # receipt = ReceiptFold.fold(run_id=run.run_id, events=events)
        # then append surface.created, then receipt.emitted, each via
        # event_producer.append_api_event(run=run, source=StreamEventSource.SYSTEM,
        #   event_type=RuntimeApiEventType.RECEIPT_EMITTED (resp. SURFACE_CREATED),
        #   summary=Messages.…, payload=dict(...))
```

Hook: `runtime_worker/handlers/run.py` calls `await emitter.emit_for_run(run=run)`
immediately **before** `RunTerminationCoordinator.terminate` on **every** terminal
path (completed/failed/cancelled/timed_out) — receipts are accountability, and a
cancelled run's receipt matters most. Ordering rationale: `RuntimeSseAdapter.stream`
stops on terminal run status, so receipt events must be appended before the terminal
event/status flip or live clients never see them. The merged handler has **three**
`self.run_termination.terminate(...)` call sites (run.py L497 / L534 / L586), so route
them all through **one** private `RuntimeRunHandler._emit_receipt_then_terminate(run,
…)` chokepoint that (a) constructs the emitter via
`ReceiptEmitter(event_producer=self.event_producer, event_store=self.event_store)`
(both attributes already exist on the handler) only when `SurfacesV2Flag.enabled()`,
(b) `await emitter.emit_for_run(run=run)`, then (c) delegates to `terminate`.
`VERIFY AT IMPL:` confirm the coordinator does not flip status before its terminal
event; if the three sites already funnel through one internal method, hook there instead. No
receipt is emitted mid-run; the fold itself is safe to run any time (E3's route — not
the fold — rejects non-terminal exports). Emit-time summary strings live in the
`surfaces_v2/constants.py` `Messages` class.

### 4. Client folds (chat-surface; pure peer selectors, FR-3.3)

NEW `packages/chat-surface/src/destinations/run/projectReceipt.ts` — a peer of
`projectCitations`, folding the SAME `session.events` array:

```ts
export interface ReceiptProjection {
  readonly receipt: RunReceipt | null; // null until receipt.emitted seen
  readonly emittedSeq: number | null;
}
export function projectReceipt(
  events: readonly RuntimeEventEnvelope[],
): ReceiptProjection;
```

The ts fold implements §2's rules identically (the parity fixture is the referee);
`receipt.emitted` marks the receipt live (before it, the receipt tab does not exist —
B1's fold only tabs `surface.created`, which E1 emits at the same moment).

NEW `packages/chat-surface/src/destinations/run/projectLedgerSources.ts` (FR-E3):

```ts
export interface LedgerSourceRow {
  readonly op: string;
  readonly title: string;
  readonly at: string;
  readonly ledgerId: string;
  readonly latencyMs: number | null;
  readonly qualifier: "auto-ran (read)";
}
export interface LedgerSourceGroup {
  readonly connector: string;
  readonly rows: readonly LedgerSourceRow[];
}
export interface LedgerSourcesProjection {
  readonly groups: readonly LedgerSourceGroup[];
  readonly total: number;
}
export function projectLedgerSources(
  events: readonly RuntimeEventEnvelope[],
): LedgerSourcesProjection;
```

Fold: one row per `read.executed` (title resolution as §2), grouped by `connector` in
first-seen order, rows in sequence order. Malformed payloads skipped. The Sources
projection is **client-side only** in v2 — no server fold/endpoint, because no
consumer exists (SDR §3's "Sources fold" box is satisfied by this selector; note the
divergence in SDR §3 per the standard docs DoD).

### 5. UI — receipt surface + Sources tab (kit components only)

- NEW `packages/chat-surface/src/surfaces/receipt/ReceiptSurface.tsx` (+ `index.ts`;
  home mirrors D1's `surfaces/staged/`): stat-tile row (4 tiles from `receipt.tiles`,
  built on `.ui-card` + `SectionLabel`/`Caption` kit recipes) over the per-action row
  list (attribution label chip · title · time · ledger-id chip `r<short>·<seq>`).
  Attribution display labels (constant map, FR-E2/C8 wording): `auto_ran` →
  "auto-ran", `approved` → "you approved", `held` → "you held", `rejected` → "you
  rejected", `auto_applied` → "auto-sent under allow-always", `no_view_fit` → "no
  view fit". Footer carries the two contract sentences verbatim — "Every write was
  decided on its surface — nothing was approved from chat." and "Assembled from the
  run ledger · immutable." — plus B2's `SurfaceProvenance` (read-only access class)
  and a "Copy receipt" action serializing rows as plain text via B2's `onCopyText`
  (hashing export is E3). All strings render as text — hostile titles never inject markup.
- Canvas mapping: register a `receipt://` URI-scheme adapter in `TcSurfaceMount.tsx`
  (the same seam D1 used to mount `StagedDraftSurface`) so receipt tabs mount
  `ReceiptSurface` (fed from `projectReceipt`); before `receipt.emitted` there is no
  receipt tab, so no loading state is needed.
- NEW `packages/chat-surface/src/workspace/LedgerSourcesTab.tsx`: presentational body
  for `LedgerSourcesProjection` — connector group headers (`.ui-section-label`), rows
  with op · time · latency · qualifier · ledger-id chip; empty state "Sources will
  appear here as the run reads your tools."; reuses `workspace.css` chrome classes.
- MOD `RunWorkspaceRail.tsx`: optional prop
  `readonly ledgerSources?: LedgerSourcesProjection | null` — when non-null, the
  Sources panel mounts `LedgerSourcesTab` instead of `SourcesTab`; absent/null ⇒
  existing behavior byte-identical (all existing tests untouched).
- MOD `RunDestination.tsx`: when `surfacesV2` is true, compute
  `useMemo(() => projectLedgerSources(session.events), [session.events])` (and
  `projectReceipt` likewise); pass `ledgerSources` to the rail and receipt state to
  the canvas mapping. When false, neither output is passed. No host-app changes —
  B1 already threads `surfacesV2` from both hosts.
- Barrel: export the two selectors + two components from
  `packages/chat-surface/src/index.ts` in a delimited `// === Surfaces v2 E1 ===` block.

### 6. Error behavior

Fold-side: skip-never-throw for malformed/unknown events (both languages). Emitter:
best-effort — a fold or append failure logs `[surfaces_v2] receipt.emit_raised`
(warning) and never blocks termination. Projector allow-list drops unknown payload
keys. UI: `receipt: null` ⇒ no tab; a receipt whose rows reference surfaces the
client never saw still renders (rows are self-contained). No new HTTP routes ⇒ no
new API error shapes; facade and backend are untouched.

## Implementation plan

1. **Contracts wiring** — MOD `services/ai-backend/src/runtime_api/schemas/common.py`
   (`RECEIPT_EMITTED` member) + `events.py` (allow-list, activity kind, display
   title "Run receipt").
2. **Fold + emitter** — NEW `services/ai-backend/src/agent_runtime/surfaces_v2/receipt.py`
   (`ReceiptFold`, `ReceiptEmitter`); MOD `surfaces_v2/constants.py` (new `Keys.Field`
   FOLD_REF, `Values` RECEIPT_SURFACE_PREFIX / `"runtime"` / `"receipt"` / fold-ref
   template, `Messages`).
3. **Worker hook** — MOD `services/ai-backend/src/runtime_worker/handlers/run.py`:
   construct `ReceiptEmitter` when `SurfacesV2Flag` on; call before every terminal
   `RunTerminationCoordinator.terminate` (single chokepoint per §3).
4. **Expected-receipt fixture** — NEW `work_ledger_expected_receipt.json` in
   `packages/service-contracts/src/copilot_service_contracts/` (the same directory as
   A1's `work_ledger_golden_events.json`, so both languages consume it — do NOT put it
   in A3's py-only `tests/.../fixtures/` dir). Add a py loader
   `load_ledger_expected_receipt()` to `copilot_service_contracts.work_ledger`,
   mirroring A1's `load_ledger_golden_events()`; the ts parity test imports the JSON by
   relative path (the `adapterAllowlist.ts` precedent). Its content is `ReceiptFold.fold_raw`
   applied to A1's golden events — regenerate it from the fold, never hand-author it.
5. **ts selectors** — NEW `packages/chat-surface/src/destinations/run/projectReceipt.ts`
   and `projectLedgerSources.ts`.
6. **UI** — NEW `packages/chat-surface/src/surfaces/receipt/ReceiptSurface.tsx` +
   `index.ts`; NEW `packages/chat-surface/src/workspace/LedgerSourcesTab.tsx`; MOD
   `RunWorkspaceRail.tsx`, `RunDestination.tsx`, `packages/chat-surface/src/index.ts`
   (barrel block); MOD `packages/chat-surface/src/thread-canvas/TcSurfaceMount.tsx`
   (register the `receipt://` scheme adapter).
7. **Tests + parity + smoke** (below); full suites + typechecks + frontend build.
8. **Docs** — SDR §3 note (Sources projection is client-side) + §5/§7-S6 if any
   payload detail diverged.

## Test plan

ai-backend (`cd services/ai-backend && .venv/bin/python -m pytest <path>`; fixtures in
mixins, typed errors + safe messages, no network — per `tests/CLAUDE.md`):

- `tests/unit/agent_runtime/surfaces_v2/test_receipt_fold.py` —
  `test_golden_events_fold_matches_expected_receipt` (A1 golden events via
  `load_ledger_golden_events()` through `fold_raw`, compared to
  `load_ledger_expected_receipt()` — the pair the ts fold must reproduce); `test_fold_is_independent_of_hand_state` (**DoD property test**: tiles
  equal counts recomputed by an independent naive counter written inline in the test;
  shuffled input re-sorted ⇒ identical output; refold ⇒ byte-identical `RunReceipt`,
  incl. deterministic `generated_at`); `test_two_decision_paths_two_receipts`
  (**DoD session-accuracy**: identical staged events, scenario A user-approve→applied
  vs scenario B reject → different tiles, `approved` vs `rejected`/`held` rows);
  adversarial: `test_unknown_and_malformed_events_skipped_without_error`,
  `test_policy_actor_apply_rows_are_auto_applied` (FR-C8),
  `test_partial_apply_yields_held_remainder_row` (FR-C9),
  `test_raw_view_yields_no_view_fit_row`, `test_row_ledger_ids_use_codec`.
- `tests/unit/runtime_worker/test_receipt_emission.py` (`RecordingEventProducer`
  pattern from `tests/unit/runtime_worker/test_stream_events.py`) —
  `test_terminal_emits_surface_created_then_receipt_emitted_before_run_completed`
  (ordering pin, §3); `test_all_terminal_statuses_emit` (cancelled/failed too);
  `test_flag_off_emits_nothing` (event-type sequence identical — and the hermetic
  keystone `tests/unit/runtime_worker/test_fake_model_run_stream.py` stays green
  **untouched**); `test_emitter_exception_never_blocks_termination`;
  `test_reemission_upserts_stable_surface_id`.
- `tests/unit/runtime_api/test_runtime_event_timeline.py` (extend) —
  `receipt.emitted` projects with `activity_kind: "event"`, allow-listed keys only,
  and `redaction_state: "offloaded"` (the `"ref"`-key rule, pinned deliberately).

chat-surface (`npm run test --workspace @0x-copilot/chat-surface`, colocated):

- `packages/chat-surface/src/destinations/run/projectReceipt.test.ts` — fold cases +
  **parity**: ts fold of the A1 golden events deep-equals the expected-receipt JSON
  (imported by relative path, `adapterAllowlist.ts` precedent) — must fail if either
  language drifts; adversarial: malformed payloads skipped; no `receipt.emitted` ⇒
  `receipt: null`.
- `packages/chat-surface/src/destinations/run/projectLedgerSources.test.ts` —
  first-seen group order, row order, ledger-id format, latency null-tolerance;
  adversarial: hostile connector/op strings survive as plain strings.
- `packages/chat-surface/src/surfaces/receipt/ReceiptSurface.test.tsx` — four tiles
  render counts; attribution label map exact (incl. "auto-sent under allow-always");
  both microcopy sentences verbatim; Copy invokes `onCopyText`; adversarial: hostile
  title renders as text (no markup injection).
- `packages/chat-surface/src/workspace/LedgerSourcesTab.test.tsx` — groups, rows,
  empty state.
- `packages/chat-surface/src/destinations/run/RunWorkspaceRail.test.tsx` (extend) —
  `ledgerSources` non-null swaps the Sources body; prop absent ⇒ all pre-existing
  assertions green **unmodified** (byte-identity proof).
- `packages/chat-surface/src/destinations/run/RunDestination.test.tsx` (extend) —
  `surfacesV2` on + seeded terminal events ⇒ receipt tab renders `ReceiptSurface`;
  flag off ⇒ zero new behavior.

**Live smoke (desktop, step by step):**

1. `SURFACES_V2=true RUNTIME_START_IN_PROCESS_WORKER=true make dev`;
   `export TOKEN=$(make dev-bearer)`; desktop:
   `COPILOT_FACADE_URL=http://127.0.0.1:8200 npm run dev --workspace @0x-copilot/desktop`,
   then in devtools `localStorage.setItem("enterprise.flags.surfaces-v2", "true")`.
2. Run a goal that reads two connectors and stages one write (recipes:
   `docs/dev-testing.md`); approve it on the surface; let the run complete.
3. `curl -H "Authorization: Bearer $TOKEN" ':8200/v1/agent/runs/<run_id>/events'` —
   final events show `… → surface.created {kind:"receipt"} → receipt.emitted →
run_completed`, `fold_ref` = `ledger://<run_id>@<seq>`.
4. Canvas: "Run receipt" tab appears at completion; tiles match what actually
   happened (reads N, proposed 1, approved 1, held 0); rows carry `r<short>·<seq>`
   ids and "you approved" on the write; both microcopy sentences render. Rail →
   Sources: reads grouped by connector with time + ledger id + "auto-ran (read)".
   Reload the app: receipt + sources reconstruct from replay.
5. Second run: reject the staged write, then cancel the run — receipt still emits;
   row says "you rejected"; tiles differ from run 1 (session accuracy, live).
6. Flag-off pass: remove the localStorage key + restart the stack without
   `SURFACES_V2`; run again — no receipt tab, Sources tab shows the legacy citation
   list, `/events` has no v2 types.
7. Design parity: vendor the mock's receipt region (walkthrough part 07) + Sources
   rail into `tools/design-parity/surfaces/v2-receipt/{design/,anchors.json}`
   (`VERIFY AT IMPL:` local mirror of `Generative Surfaces v2.dc.html`; DesignSync
   from Claude Design project `ceb081f6` if absent), render live via a
   `render-live-v2-receipt.test.tsx` under `tools/design-parity/lib/`, extract
   computed styles (`cd tools/design-parity && python3 -m http.server 8099`), then
   `node lib/compare.mjs <design.json> <live.json> --anchors
surfaces/v2-receipt/anchors.json --out surfaces/v2-receipt/out/report.md` — **0 HIGH**.

## Definition of done

From ../03-prds.md PRD-E1 (binding minimums, never weakened):

- [ ] **Receipt equals an independent fold of the golden ledger (property test: no
      hand-assembled state).** Proof:
      `test_receipt_fold.py::test_golden_events_fold_matches_expected_receipt` +
      `test_fold_is_independent_of_hand_state` green, and the ts parity case in
      `projectReceipt.test.ts` folding the same fixture to the same JSON.
- [ ] **Session-accuracy: different decision paths produce different, correct receipts
      (two-scenario test).** Proof: `test_two_decision_paths_two_receipts` + live-smoke
      steps 4–5 (two runs, two different receipts).
- [ ] **Parity vs mock receipt, 0 HIGH; live desktop demo.** Proof:
      `tools/design-parity/surfaces/v2-receipt/out/report.md` checked in with 0 HIGH
      rows; live-smoke steps 1–6 executed on the real stack, run ids noted in the PR body.

Standard DoD (every PRD):

- [ ] Unit tests pass in owning components (full ai-backend suite via its `.venv`;
      chat-surface tests + typecheck + lint); `npm run typecheck --workspace
@0x-copilot/api-types` and frontend build green.
- [ ] Flags off ⇒ byte-identical behavior. Proof: `test_flag_off_emits_nothing`, the
      untouched-green `test_fake_model_run_stream.py` keystone, unmodified pre-existing
      RunWorkspaceRail/RunDestination assertions, smoke step 6.
- [ ] No service-boundary violations (apps→facade only; no cross-`src/` imports; no
      facade/backend change in this PR; chat-surface eslint gate green).
- [ ] New LLM call sites: **none** (folds are pure; the A2 grep-gate stays green).
- [ ] Docs: SDR §3 (client-side Sources projection note) and §5/§7-S6 updated if
      implementation diverged.

UI DoD (🎨):

- [ ] Built from design-system/chat-surface kit components (`.ui-card`,
      `SectionLabel`, `Caption`, pill/chip recipes; `SurfaceProvenance` reused) — no
      host-app one-off styling, no raw font-size/letter-spacing (design-system SKILL.md).
- [ ] `tools/design-parity/` run against the staged v2 mock receipt region: **0 HIGH
      drift** (artifact above).
- [ ] Live desktop smoke of the flow on the real stack (script above), not just tests.

## Out of scope

- Approvals queue cards, pending counter, Agents/fleet tab, jump-to-surface routing
  (PRD-E2); audit-chain hashing, receipt **export**/download, `/v1/usage/*`, v1
  `result["surface"]`/`DraftSurfaceProjector` retirement (PRD-E3).
- Any new decision/write path or ledger event beyond the two in §1; any change to
  gate/staging/commit semantics (C/D waves own them).
- A server-side Sources fold or v2 sources endpoint (no consumer; the rail folds
  client-side — documented divergence, §4). A `GET …/receipt` endpoint (E3's export
  is the wire surface for the fold).
- Timeline (FR-E4, shelved); Focus-mode changes (rich cards only, untouched);
  failure-path visual polish (Phase-2 designer track — `failed`/`partial` rows render
  with correct state, styling iterates there).
- Modifying the legacy citations `SourcesTab`/`useRunSources` path (flag-off surface).

## Guardrails

- **Service boundaries (hard):** apps call `backend-facade:8200` `/v1/*` only — never
  `:8000`/`:8100`; no deployable component imports another's `src/`; no sibling
  `PYTHONPATH`/`.venv` reuse; contracts move only via `packages/api-types` /
  `packages/service-contracts`. This PR touches no facade/backend code at all.
- **Flag-off byte-identical (SDR §11):** with `SURFACES_V2` unset and the client flag
  absent, every event stream, wire payload, and rendered byte matches today — the
  untouched-tests rule and the keystone stream test are the gate, not review promises.
- **Receipt = fold, never state (SDR §10 item 6):** no stored receipt rows, no
  incremental accumulator, no mutation — any "fix" to a receipt is a fold-rule change
  proven against the golden fixture in BOTH languages.
- **One projector invariant (FR-3.3):** `projectReceipt`/`projectLedgerSources` are
  pure selectors over the SAME `session.events` array — a second SSE subscription or
  a rival projector is a defect.
- **ai-backend rules** (`services/ai-backend/CLAUDE.md`): Pydantic at every boundary
  (fold output is the A1 `RunReceipt` entity, not a dict); no module-level helper
  functions (fold/emitter are classes); payload keys via `Keys.Field` constants,
  never literals; typed domain errors with safe public messages; event payloads are
  untrusted until validated; never derive activity types from event-name prefixes —
  register the explicit projector branch.
- **ai-backend tests** (`tests/CLAUDE.md`): fakes/mixins, no network or live LLMs;
  concrete test classes contain only `test_*` methods; assert typed error classes and
  safe messages; this service's `.venv` only.
- **chat-surface** (`packages/chat-surface/CLAUDE.md`): substrate-agnostic — no
  `window`/`document`/`fetch`/`localStorage`/`EventSource` (eslint-enforced); hosts
  consume only via the `src/index.ts` barrel; presentational components + callbacks,
  all IO through ports; both host binders change only if a destination prop contract
  changes (E1 changes none).
- **Additive-only vocabulary until E-wave close (SDR §12):** `receipt.emitted` fields
  are frozen after merge; E3 may add, never rename; the ledger is append-only.

## Open questions

- **Design-parity mock source for the receipt region (blocks UI DoD item 3).** SDR §9
  states the v2 mock `Generative Surfaces v2.dc.html` is "already mirrored locally" but
  does not pin (a) the receipt/Sources region within it that this PR must vendor into
  `tools/design-parity/surfaces/v2-receipt/design/`, nor (b) the authoritative
  DesignSync project id to re-pull from if the local mirror is stale — the smoke step
  cites "walkthrough part 07" and project `ceb081f6`, neither confirmable from the repo
  or the linked docs. A fresh implementer cannot produce the `anchors.json` / `0 HIGH`
  parity artifact without the correct mock region. **Needs:** the exact mock filename +
  receipt region + DesignSync project id from the design owner (or confirmation that the
  cited part 07 / `ceb081f6` are correct). Until then, treat DoD item 3's parity
  artifact as the one item that may lag the code by the time it takes to source the mock.
