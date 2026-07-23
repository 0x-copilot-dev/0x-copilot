# PRD-A3 — Work Ledger emission + SurfaceStore projection

**Goal.** Behind a new runtime flag `SURFACES_V2` (default OFF), the ai-backend runtime emits the first four
Work-Ledger event types — `action.classified`, `read.executed`, `surface.created`, `view.derived` — for what it
already does today (MCP tool reads, v1 surface envelopes, async spec-generation upgrades), through the existing
per-run event pipeline (persistence, replay, SSE — no new store technology). A pure, rebuildable **SurfaceStore
projection** (a fold over those events) plus a new read endpoint `GET /v1/agent/runs/{run_id}/surfaces`
(runtime_api + facade passthrough) make canvas state queryable and replay-reconstructible. No behavior change with
the flag off: v1 `result["surface"]` emission is untouched, and the flag-off event stream is byte-identical.

## Implementer brief

You are implementing this in a **fresh git worktree branched off `main`** of the `enterprise-search` monorepo
(repo root = the worktree root). Run `make setup` once if the service `.venv`s / `node_modules` are missing.
Three components are touched; test commands:

```bash
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/surfaces_v2/
cd services/ai-backend && .venv/bin/python -m pytest    # full suite before merge
cd services/backend-facade && .venv/bin/python -m pytest tests/test_run_surfaces_proxy.py
cd services/backend-facade && .venv/bin/python -m pytest
npm run typecheck --workspace @0x-copilot/api-types
```

Read these files first (paths repo-relative):

1. `docs/plan/generative-surfaces-v2/02-sdr.md` — §5 = the authoritative event vocabulary; §3/§6 = this PR's architecture.
2. `docs/plan/generative-surfaces-v2/03-prds.md` — PRD-A3 summary; its DoD items are binding minimums.
3. `services/ai-backend/src/runtime_worker/handlers/run.py` — `_build_surface_generation_scheduler` (~line 1570) + bind/unbind block (~lines 315–345 / 559–575): the exact precedent for a per-run bound emitter with an `append_api_event` closure.
4. `services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py` — `_attach_surface` (~line 234) inside `ainvoke`: the tool-result hook point.
5. `services/ai-backend/src/runtime_api/schemas/events.py` + `common.py` — `RuntimeEventPresentationProjector` (events.py ~line 77), `_surface_spec_generated_payload` (~line 670): the allow-list pattern to copy; `RuntimeApiEventType` (common.py: `class` ~line 81, existing members through `SURFACE_SPEC_GENERATED` ~line 197).
6. `services/ai-backend/src/agent_runtime/api/events.py` — `RuntimeEventProducer.append_api_event` (line 60): the single emission chokepoint.
7. `services/ai-backend/src/agent_runtime/api/conversation_query_service.py` — `replay_events` (line 371): the scope-check + `list_events_after` pattern the new endpoint reuses.
8. `services/ai-backend/src/agent_runtime/capabilities/surfaces/config.py` + `generator.py` — `SurfaceEmissionFlag` (flag-class pattern; A3's flag defaults OFF, this one ON) and `SurfaceGenerationScheduler.bind_for_run/unbind/active` (the ContextVar run-binding pattern to mirror).
9. `services/backend-facade/src/backend_facade/app.py` — inline `/v1/agent/*` routes + `forward_json`: the passthrough pattern.
10. `services/ai-backend/CLAUDE.md` + `services/ai-backend/tests/CLAUDE.md` — engineering + test rules (constants classes, no module-level helpers, mixin-based tests).

## Context

**Generative Surfaces v2** re-founds the product's surface layer on an explicit, typed **Work Ledger**: every
consequential runtime act becomes a typed event on the run's existing append-only event log; everything
user-visible — canvas, receipts, sources, usage totals — is a **projection** of that ledger (../02-sdr.md §2–§3).
Requirements contract: ../01-problem-and-requirements.md FR-A2/A5 (named, provenance-carrying surfaces), FR-E1
(one ledger threads everything), NFR-6 (receipt = fold of the ledger).

This PR is **Wave A, PRD-A3** (../03-prds.md): the first ledger _emission_ + the first _projection_, after PRD-A1
(contracts) and PRD-A2 (UsageMeter — not consumed; A3 adds no LLM call sites). It changes **no behavior**: no
gates, no classification catalog, no staging — it records honestly what the v1 pipeline already does, so Wave B
(canvas UI) has real events to project and Waves C/D can swap richer semantics into the same event types. The v1
`result["surface"]` appendage keeps emitting unchanged (compat window, ../02-sdr.md §11).

Persistence/transport are reused wholesale (../02-sdr.md §6): events go through
`RuntimeEventProducer.append_api_event` → the adapter assigns monotonic `sequence_no` → replay
(`/v1/agent/runs/{id}/events`) and SSE (`/stream`) work with zero adapter or migration changes (`event_type` is a
text column).

## Interfaces consumed / exposed

**Consumed (from earlier PRDs / existing code):**

- PRD-A1 (exact names as A1 landed them — A3 imports these, never redefines them):
  - **Event-type values** — the `LedgerEventType` StrEnum in `agent_runtime.surfaces_v2.ledger_models`
    (members `ACTION_CLASSIFIED = "action.classified"`, `READ_EXECUTED`, `SURFACE_CREATED`, `VIEW_DERIVED`, …).
    The ordered value tuple `LEDGER_EVENT_TYPES` and `LEDGER_PAYLOAD_VERSION = 1` live in
    `copilot_service_contracts.work_ledger`. There are **no** per-event `EVENT_*` string constants.
  - **Ledger-id codec** — `LedgerIdCodec.format(run_id, sequence_no)` / `LedgerIdCodec.parse(text)` in
    `agent_runtime.surfaces_v2.ledger_ids` (py) and `formatLedgerId` / `parseLedgerId` in
    `packages/api-types/src/ledger.ts` (ts). Only the _format constants_ (`prefix`, `short_len`, `separator`,
    `seq_min_width`) live in the SSOT `work_ledger.json`; the codec **logic is not in service-contracts**.
  - **Golden fixture** — `packages/service-contracts/src/copilot_service_contracts/work_ledger_golden_events.json`,
    loaded py-side via `copilot_service_contracts.work_ledger.load_ledger_golden_events()`.
  - **Pydantic payload mirrors** — `agent_runtime.surfaces_v2.ledger_models` (14 payload models +
    `WorkLedgerVocabulary.validate_payload(event_type, payload)`); entity twins in `agent_runtime.surfaces_v2.entities`.
- Existing runtime (paths in the read-first list above): `RuntimeEventProducer.append_api_event`;
  `RuntimeApiEventType`/`RuntimeActivityKind`; `RuntimeEventPresentationProjector`;
  `CallMcpTool.ainvoke`/`_attach_surface`; `RuntimeRunHandler._build_surface_generation_scheduler` + bind/finally block;
  `ConversationQueryService.replay_events`/`_run_for_scope`; `server_slug`/`tool_slug`
  (`capabilities/surfaces/builtin.py`); `SurfaceEnvelope` (`capabilities/surfaces/spec_models.py`); facade
  `forward_json` + `FacadeAuthenticator` (`services/backend-facade/src/backend_facade/app.py`, `auth.py`).

**Exposed (later PRDs depend on these — keep names stable):**

- `SurfacesV2Flag` (env `SURFACES_V2`) — the runtime kill switch every later wave reuses.
- `WorkLedgerEmitter` + its ContextVar bind seam — C1 (ActionClassifier) replaces the hardcoded `class`/`basis`;
  C2 (gates) and D1 (staging) add their event types through the same emitter; B3's ViewDeriver takes over `view.derived`.
- `SurfaceStoreProjection.fold` (py) — B1 builds the ts twin + fixture-parity test against it; E1's receipt fold follows its shape.
- `GET /v1/agent/runs/{run_id}/surfaces` (runtime_api + facade) + `RunSurfacesResponse`/`SurfaceSnapshot`
  (api-types mirror) — B1/B2 read it for canvas hydration.
- The four `RuntimeApiEventType` members + projector allow-lists — additive-only until Wave E (../02-sdr.md §12
  "event-vocabulary churn" risk).

## Design

### D1. Event types + payloads (wire values are SDR §5 names, verbatim)

Add to `RuntimeApiEventType` in `src/runtime_api/schemas/common.py` four members whose values are sourced from the
A1 `LedgerEventType` StrEnum (`.value`), never re-typed string literals — e.g.
`ACTION_CLASSIFIED = LedgerEventType.ACTION_CLASSIFIED.value` (= `"action.classified"`), likewise
`READ_EXECUTED` (`"read.executed"`), `SURFACE_CREATED` (`"surface.created"`), `VIEW_DERIVED` (`"view.derived"`).
(`LedgerEventType` is a StrEnum in `agent_runtime.surfaces_v2.ledger_models`; `common.py` may import it — it is in
`agent_runtime`, not `runtime_api`, so no reverse-layering.) Payloads (field-by-field; `v: 1` always):

```text
action.classified  {v:1, call_id, connector, op, class:"unknown", basis:"default"}
read.executed      {v:1, call_id, connector, op, latency_ms?, payload_ref}
surface.created    {v:1, surface_id, kind, source:{connector,op}, title, payload_ref}
view.derived       {v:1, surface_id, tier:"generic"|"shaped", basis:"schema"|"registry"|"generated",
                    spec_ref?, gen?:{model}}
```

A3 semantics freeze (do not invent more):

- `connector` = `server_slug(server_name)`, `op` = `tool_slug(tool_name)` (`capabilities/surfaces/builtin.py` helpers).
- **`class` is always `"unknown"`, `basis` always `"default"` in A3** — no classifier until PRD-C1; recording
  `read` would claim a policy decision nobody made. `read.executed` still fires for every executed MCP tool call:
  in Wave A all executed calls are the read path (v1 approval middleware still governs real writes and keeps its
  own v1 events).
- `payload_ref` scheme (NEW, defined here): **always** `"call:<call_id>"` (`CALL_REF_PREFIX` + `call_id`, D7) —
  resolvable to the `tool_result` event carrying the same `call_id` in this run's replay (regenerate rides on
  replay, ../02-sdr.md §6). This is transitively sufficient: if that `tool_result` was later offloaded to
  `/large_tool_results/<sha256>` by the context/memory layer (`OffloadWriter`, resolved by the file-store Deep
  Agents backend — `execution/contracts.py:552`), following `call:<call_id>` → the `tool_result` event reaches it.
  The offload ref is **not** available at the `CallMcpTool.ainvoke` emission point (offloading happens downstream
  of the tool result), so A3 does not attempt to embed it here.
- `surface_id` = the v1 `SurfaceEnvelope.surface_uri` string (`<archetype>://<server-slug>/<tool>/<id>`) —
  stable, dedupes repeat reads of one record. Later waves may mint opaque ids; A3 does not.
- `kind` mapping from v1 `SurfaceArchetype` (10 values) onto the SDR kind set: `record→record`, `table→table`,
  `board→table`, `message→message`, all others (`doc,event,timeline,dashboard,file,form`) `→record`.
  `VERIFY AT IMPL:` reconcile with the A1 golden fixture's `surface.created.kind` examples; the fixture wins.
- `title`: the emitter receives the just-attached `surface` mapping = `SurfaceEnvelope.model_dump(mode="json",
exclude_none=True)` (`capabilities/surfaces/spec_models.py`), whose `state.spec` (`SurfaceSpec | None`) and
  `state.data` (untrusted tool output) are the inputs. With a spec present, resolve `surface["state"]["spec"]
["title_path"]` against `surface["state"]["data"]` via `DotPathResolver.resolve(data, path) -> (found, value)`
  (`capabilities/surfaces/generator.py`) — use the value when `found`, else fall through. No spec (or not found):
  `"<connector> · <op>"`. Truncate to 120 chars.
- `tier`/`basis` from the v1 envelope: spec present → `("shaped","registry")`; absent → `("generic","schema")`;
  the async upgrade path (D4 Hook 2) → `("shaped","generated")` with `gen.model` = the scheduler payload's
  `generator_model`. `spec_ref` **omitted in A3** (optional; B3 populates it); `gen.ms` omitted (not measured here).
- `latency_ms`: wall time of the connector dispatch inside `CallMcpTool.ainvoke`. `ainvoke` holds **no** perf
  counter today (verified — `call_tool.py` imports/uses no `time`/`perf_counter`), so add `time.perf_counter()`
  bracketing inside `ainvoke` only. Omit the field if unavailable.
- Raw/non-mapping tool output ⇒ **no** `surface.created`/`view.derived` (v1 creates no envelope);
  `action.classified` + `read.executed` still emit.
- All payload key strings live in a nested constants class (D7), never inline.

### D2. Flag

New `src/agent_runtime/surfaces_v2/config.py` with
`class SurfacesV2Flag: @staticmethod def enabled(environ: Mapping[str, str] | None = None) -> bool`.
Env var `SURFACES_V2`; **only** `true/1/yes/on` (case-insensitive) enable; unset, empty, and everything else ⇒
off. (Deliberately the opposite default of `RUNTIME_SURFACE_EMISSION`.)

### D3. WorkLedgerEmitter (NEW)

New package `src/agent_runtime/surfaces_v2/` (the SDR §3 "agent_runtime/surfaces v2" component; v1
`capabilities/surfaces/` stays untouched). New `emitter.py`:

```python
EmitFn = Callable[[str, Mapping[str, object], str | None], Awaitable[None]]
#         (event_type_value, payload, summary) — event types as raw A1 constants so this
#         module never imports runtime_api (layering: runtime_api imports agent_runtime).

@dataclass(frozen=True)
class WorkLedgerEmitter:
    emit: EmitFn
    async def on_tool_result(self, *, server_name, tool_name, call_id,
                             output, surface, surface_uri, latency_ms) -> None: ...
    async def on_spec_generated(self, *, payload: Mapping[str, object]) -> None: ...
    # ContextVar run binding, mirror of the module-level ``_SCHEDULER_CTX`` ContextVar
    # behind ``SurfaceGenerationScheduler`` (capabilities/surfaces/generator.py: bind_for_run
    # ~line 992, unbind ~998, active ~1004, module-level ContextVar ~1010). Token type is
    # ``object`` to match that precedent exactly (the underlying value is a ``contextvars.Token``):
    @classmethod
    def bind_for_run(cls, emitter: "WorkLedgerEmitter") -> object: ...
    @classmethod
    def unbind(cls, token: object) -> None: ...
    @classmethod
    def active(cls) -> "WorkLedgerEmitter | None": ...
```

`on_tool_result` emits, in order: `action.classified`, `read.executed`, then (only when `surface` is a mapping)
`surface.created` + `view.derived`. Every method swallows its own exceptions (log
`[surfaces_v2] ledger.emit_raised`, warning) — ledger emission never fails a tool call. Summaries at emit time:
`read.executed` → `"auto-ran (read)"` (the FR-C1 label), `surface.created` → `"Prepared a surface"`,
`view.derived` → `"Derived a view"`, `action.classified` → `None`.

### D4. Hooks + run binding

- **Hook 1 — tool results.** In `CallMcpTool.ainvoke` (`src/agent_runtime/capabilities/mcp/middleware/call_tool.py`),
  right after the existing `self._attach_surface(...)` call (~line 224), add
  `await CallMcpTool._emit_ledger(result=result, server_name=..., tool_name=..., call_id=..., output=output, latency_ms=...)`
  — a new private static method that no-ops when `WorkLedgerEmitter.active()` is `None`, else awaits
  `emitter.on_tool_result(...)` passing `result.get("surface")`/`result.get("surface_uri")` (the just-attached v1
  envelope). Awaited, not fire-and-forget, for deterministic ordering; the emitter is only bound when
  `SURFACES_V2` is on, so flag-off leaves this a no-op.
- **Hook 2 — generated-spec upgrades.** In `RuntimeRunHandler._build_surface_generation_scheduler`
  (`src/runtime_worker/handlers/run.py:1570`), after the `_emit` closure appends `SURFACE_SPEC_GENERATED`, call
  `emitter.on_spec_generated(payload=payload)` when an emitter is active — emitting
  `view.derived {surface_id: payload["surface_uri"], tier:"shaped", basis:"generated", gen:{model: payload["generator_model"]}}`.
  v1 first, v2 second (additive ordering).
- **Run binding.** New `RuntimeRunHandler._build_work_ledger_emitter(run)` mirroring `_build_surface_generation_scheduler`:
  returns `None` unless `SurfacesV2Flag.enabled()`; its `EmitFn` closure maps the ledger value (a `LedgerEventType`
  member, i.e. a `str`) to the wire enum by value:
  `self.event_producer.append_api_event(run=run, source=StreamEventSource.SYSTEM, event_type=RuntimeApiEventType(str(event_type_value)), summary=summary, payload=dict(payload))`
  (both enums carry identical values, e.g. `"action.classified"`, so the by-value lookup succeeds).
  Bind next to the scheduler bind (~line 329); unbind in the same `finally` block (~line 559).

### D5. Projector (server-side presentation)

In `src/runtime_api/schemas/events.py`, following the `_surface_spec_generated_payload` precedent exactly: four
strict **allow-list** payload methods (`_action_classified_payload`, `_read_executed_payload`,
`_surface_created_payload`, `_view_derived_payload`) wired into `payload_for_event`; `activity_kind_for` returns
`RuntimeActivityKind.EVENT` for all four (surface-state merge, not a card). Allow-lists pass only the D1 keys
with type checks (str/int/nested dict with its own allow-list); unknown keys drop. Note: `_redaction_state_for`
marks any payload containing a `"ref"`-named key as `OFFLOADED` — `payload_ref`/`spec_ref` trigger this. That is
semantically correct (they _are_ references) — keep it and pin it with a test; do not special-case.

### D6. SurfaceStore projection (pure fold)

New `src/agent_runtime/surfaces_v2/projection.py` (Pydantic `RuntimeContract` models):

```python
class SurfaceViewState(RuntimeContract):
    tier: str            # "generic" | "shaped"
    basis: str           # "schema" | "registry" | "generated"
    spec_ref: str | None = None
    generator_model: str | None = None

class SurfaceSnapshot(RuntimeContract):
    surface_id: str
    kind: str
    connector: str
    op: str
    title: str
    payload_ref: str
    view: SurfaceViewState | None = None
    first_sequence_no: int
    last_sequence_no: int
    ledger_id: str       # LedgerIdCodec.format(run_id, first_sequence_no) → "r<short>·<seq>"
                         # (LedgerIdCodec from A1: agent_runtime.surfaces_v2.ledger_ids)

class SurfaceStoreState(RuntimeContract):
    run_id: str
    surfaces: tuple[SurfaceSnapshot, ...]   # creation order
    latest_sequence_no: int

class SurfaceStoreProjection:
    @staticmethod
    def fold(run_id: str, events: Iterable[RuntimeEventEnvelope]) -> SurfaceStoreState: ...
    @staticmethod
    def fold_raw(run_id: str, events: Iterable[Mapping[str, object]]) -> SurfaceStoreState: ...
```

Fold rules (deterministic, total): process in `sequence_no` order; `surface.created` **upserts** by `surface_id`
(repeat reads refresh `title`/`payload_ref`/`last_sequence_no`; `first_sequence_no`/`ledger_id` keep the first);
`view.derived` updates the matching snapshot's `view` (silently ignored if `surface_id` unseen — defensive,
pure); every other event type (including all future vocabulary) is **skipped without error**. `fold_raw` accepts
plain JSON dicts (`{event_type, sequence_no, payload}`) so the A1 golden fixture feeds py and (in B1) ts folds identically.

### D7. Read endpoint, facade, api-types, constants

- **runtime_api:** new route `GET /v1/agent/runs/{run_id}/surfaces` registered in `RuntimeApiRouter.create_router`
  (`src/runtime_api/http/routes.py`) next to `/runs/{run_id}/events`; handler `RuntimeApiRoutes.get_run_surfaces`
  follows `get_events` exactly (same `org_id`/`user_id` query params + `scoped_identity`); response model
  `RunSurfacesResponse` (`{run_id, surfaces: list[SurfaceSnapshot], latest_sequence_no}`) in new
  `src/runtime_api/schemas/surfaces_v2.py`. New `Keys.RouteName.GET_RUN_SURFACES` constant (add beside
  `Keys.RouteName.GET_MESSAGES` in `agent_runtime/api/constants.py`, where `class RouteName` is nested under
  `class Keys`).
- **Service:** new `ConversationQueryService.list_run_surfaces(*, org_id, user_id, run_id)` in
  `src/agent_runtime/api/conversation_query_service.py`: `_run_for_scope` (404 on wrong-tenant/unknown run, same
  as `replay_events`), `list_events_after(after_sequence=0)`, `SurfaceStoreProjection.fold`. **Not flag-gated**:
  additive endpoint; with no v2 events it returns an empty list — harmless and honest.
- **facade:** inline route in `src/backend_facade/app.py` beside the other `/v1/agent/*` passthroughs:
  authenticate → `forward_json(app, "GET", f"/v1/agent/runs/{run_id}/surfaces", target="ai_backend", params=identity.scoped_params({}), identity=identity)`.
  Error passthrough via the standard `_upstream_error_detail` path (free with `forward_json`).
- **api-types:** add `SurfaceSnapshot`, `SurfaceViewState`, `RunSurfacesResponse` to A1's ledger contracts file
  `packages/api-types/src/ledger.ts` and export them via the existing `export { … } from "./ledger";` block in
  `index.ts`. Facade rule: its surface is the public contract — the response shape must be mirrored here.
  Note A1 already ships a `Surface` entity + `SurfaceKind`/`ViewTier`/`ViewBasis` unions in `ledger.ts`; reuse
  those value unions where they apply. `SurfaceSnapshot`/`SurfaceViewState` are the SurfaceStore-fold output shape
  (they add `first_sequence_no`/`last_sequence_no`) — see Open questions for the `Surface` vs `SurfaceSnapshot`
  reconciliation.
- **Constants:** new `src/agent_runtime/surfaces_v2/constants.py` with nested classes per service rules (no
  inline strings): `Keys.Field` (V, CALL*ID, CONNECTOR, OP, CLASS, BASIS, LATENCY_MS, PAYLOAD_REF, SURFACE_ID,
  KIND, SOURCE, TITLE, TIER, SPEC_REF, GEN, MODEL), `Values` (CLASS_UNKNOWN, BASIS_DEFAULT, TIER_GENERIC/SHAPED,
  BASIS_SCHEMA/REGISTRY/GENERATED, CALL_REF_PREFIX = "call:", PAYLOAD_V = 1), `Messages` (the three summaries).
  Event-type \_values* come from the A1 service-contracts constants.

### D8. Errors, migrations, metering

No new migrations (`runtime_events.event_type` is text; no new table — SurfaceStore is computed on demand,
../02-sdr.md §6). Every emitter path is best-effort: exceptions logged + swallowed, never propagated into the
tool call or run loop (same doctrine as `_attach_surface`). No new LLM call sites ⇒ the A2 UsageMeter rule is
vacuously satisfied; do not add model calls in this PR.

## Implementation plan

All ai-backend paths under `services/ai-backend/`.

1. **Flag + constants.** Create `src/agent_runtime/surfaces_v2/__init__.py`, `config.py` (`SurfacesV2Flag`),
   `constants.py` (D7).
2. **Event types + projector.** Modify `src/runtime_api/schemas/common.py` (four `RuntimeApiEventType` members)
   and `src/runtime_api/schemas/events.py` (four allow-list methods, `payload_for_event` wiring, `activity_kind_for` → EVENT).
3. **Emitter.** Create `src/agent_runtime/surfaces_v2/emitter.py` per D3.
4. **Tool hook.** Modify `src/agent_runtime/capabilities/mcp/middleware/call_tool.py`: `_emit_ledger` static
   method + awaited call in `ainvoke` after `_attach_surface`; latency bracket if needed (D1).
5. **Worker wiring.** Modify `src/runtime_worker/handlers/run.py`: `_build_work_ledger_emitter`, bind/unbind
   alongside the scheduler, `on_spec_generated` call inside `_build_surface_generation_scheduler`'s `_emit`.
6. **Projection.** Create `src/agent_runtime/surfaces_v2/projection.py` (D6).
7. **Endpoint.** Create `src/runtime_api/schemas/surfaces_v2.py`; modify
   `src/agent_runtime/api/conversation_query_service.py` (`list_run_surfaces`), `src/runtime_api/http/routes.py`
   (handler + registration), the RouteName constants file.
8. **Facade.** Modify `services/backend-facade/src/backend_facade/app.py` (inline route).
9. **Contracts.** Modify `packages/api-types/src/<A1 v2 file>.ts` + `packages/api-types/src/index.ts`.
10. **Tests** (next section), then full suites + typecheck.

## Test plan

ai-backend (`cd services/ai-backend && .venv/bin/python -m pytest <path>`; fakes in mixins, typed-error
assertions, no network — per `tests/CLAUDE.md`):

- `tests/unit/agent_runtime/surfaces_v2/test_config.py` — unset/empty/garbage ⇒ off; `true/1/yes/on` any case ⇒ on.
- `tests/unit/agent_runtime/surfaces_v2/test_ledger_emitter.py` (recording-`EmitFn` mixin): tool result emits
  four events in order; spec-less envelope ⇒ generic/schema view; spec envelope ⇒ shaped/registry; no envelope ⇒
  classified + read only; `payload_ref` is always the `call:<call_id>` scheme (D1); **adversarial:** no
  input yields `class:"read"` in A3; emit exception swallowed; unbound `active()` is None; `on_spec_generated`
  emits the generated view.
- `tests/unit/agent_runtime/surfaces_v2/test_surface_store_projection.py`:
  `test_golden_events_fold_matches_expected_snapshot` (A1 golden fixture loaded via
  `load_ledger_golden_events()`, run through `fold_raw`, compared to a checked-in expected-state JSON that A3
  creates at `tests/unit/agent_runtime/surfaces_v2/fixtures/surface_store_golden_state.json` — the `(golden events,
expected state)` pair B1's ts fold must reproduce); repeat `surface.created` upserts keeping first
  `ledger_id`; **adversarial:** fixture salted with future vocabulary + junk types is skipped without error;
  `view.derived` for an unknown surface ignored; out-of-order input sorted by `sequence_no` (pin the behavior).
- `tests/unit/agent_runtime/capabilities/mcp/test_call_tool_surface.py` (extend): `ainvoke` emits ledger when
  emitter bound; flag off ⇒ no emitter + result byte-identical (reuse the file's existing byte-identity assertions).
- `tests/unit/runtime_worker/test_surfaces_v2_emission.py` (`RecordingEventProducer` pattern from
  `tests/unit/runtime_worker/test_stream_events.py`): handler binds/unbinds emitter with flag on; scheduler
  `_emit` adds `view.derived` after `surface_spec_generated`; **adversarial snapshot:** flag off ⇒ event-type
  sequence identical — and the hermetic keystone `tests/unit/runtime_worker/test_fake_model_run_stream.py` must
  stay green **untouched** (the true byte-identity gate).
- `tests/unit/runtime_api/test_run_surfaces_endpoint.py` (real `InMemoryRuntimeApiStore`): scope mismatch ⇒ 404;
  empty run ⇒ empty surfaces; fold reflects appended ledger events; `"ref"`-keyed payloads marked OFFLOADED (pins D5).
- `tests/unit/runtime_adapters/file/test_surfaces_v2_replay_file.py` — restart/reopen DoD: append ledger events
  through the file store, fold; new store instance over the same root, replay `list_events_after(0)`, fold again
  ⇒ `SurfaceStoreState` equal field-for-field.

backend-facade: `tests/test_run_surfaces_proxy.py` — capture-`forward_json` pattern from
`tests/test_approval_decision_proxy.py`: method/path/target=`ai_backend`, `org_id`/`user_id` params, 401 without
bearer, upstream 404 passthrough; extend `tests/test_public_route_contract.py` with `/v1/agent/runs/{run_id}/surfaces`.

api-types: `npm run typecheck --workspace @0x-copilot/api-types`; add the new types to the A1 ts↔py parity
harness (`VERIFY AT IMPL:` harness location from A1's PR).

**Live smoke (step by step):**

1. `SURFACES_V2=true make dev` (or export in `services/ai-backend/.env`; facade at :8200).
2. `export TOKEN=$(make dev-bearer)`; create a conversation + run that calls an MCP read tool (recipes:
   `docs/dev-testing.md`), always via the facade `:8200`.
3. `curl -H "Authorization: Bearer $TOKEN" ':8200/v1/agent/runs/<run_id>/events'` — `action.classified` →
   `read.executed` → `surface.created` → `view.derived` with D1 payloads, `activity_kind: "event"`, monotonic `sequence_no`.
4. `curl ... ':8200/v1/agent/runs/<run_id>/surfaces'` — list matches the events; `ledger_id` renders `r<short>·<seq>`.
5. With `SURFACE_SPEC_MODEL` set + an uncurated tool: `view.derived {basis:"generated"}` follows `surface_spec_generated`.
6. Flag-off pass: restart without `SURFACES_V2`, new run ⇒ **zero** v2 event types in `/events`, empty `/surfaces`.
7. Desktop/file variant: `RUNTIME_STORE_BACKEND=file` + `RUNTIME_FILE_STORE_ROOT` +
   `ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop`; run with flag on, restart the process, re-`curl /surfaces`
   — identical response (replay reconstruction).

## Definition of done

Carried over from ../03-prds.md PRD-A3 (binding minimums, never weakened):

- [ ] **With flag on, a live run's ledger replays into a SurfaceStore state that matches the canvas fixture fold
      (same golden events as A1).** Proof: golden-fold test in `test_surface_store_projection.py` + live-smoke steps 3–4.
- [ ] **Restart/reopen: replay reconstructs identical store state (file adapter test).** Proof:
      `test_surfaces_v2_replay_file.py` + smoke step 7.
- [ ] **Flag off ⇒ event stream byte-identical to today (snapshot test).** Proof:
      `test_flag_off_event_type_sequence_identical`, the untouched-green `test_fake_model_run_stream.py` keystone,
      the byte-identity case in `test_call_tool_surface.py`, smoke step 6.

Standard DoD (every PRD):

- [ ] Unit tests pass in each owning component; ai-backend + facade full suites green;
      `npm run typecheck --workspace @0x-copilot/api-types` green.
- [ ] Flags off ⇒ byte-identical behavior (v1 `result["surface"]` emission untouched — existing surface tests all green).
- [ ] No service-boundary violations (apps→facade only; no cross-`src/` imports; the new facade route calls
      ai-backend over HTTP only).
- [ ] No new LLM call sites (UsageMeter-seam rule holds vacuously; A2 grep-gate green).
- [ ] Docs: update ../02-sdr.md §5/§6 if implementation diverges (e.g. `payload_ref` scheme, `kind` mapping);
      record divergences in the PR description.

(Not a 🎨 PRD — no UI touched, so the design-parity DoD does not apply.)

## Out of scope

- Gates, park/resume, `gate.*` events (PRD-C2); real classification catalog and any `class` value other than
  `"unknown"` (PRD-C1).
- Staging/decisions/commit (`write.*`, `revision.*`, `decision.*` — Wave D); receipt/sources folds
  (`receipt.emitted` — PRD-E1); `usage.recorded` emission (PRD-A2 owns it).
- Client/canvas work, ts fold, chat-surface changes (PRD-B1+); `view.preference`, `shape.requested` (B3/B4).
- Removing or altering v1 `result["surface"]` / `DraftSurfaceProjector` emission (compat window ends in PRD-E3).
  Draft surfaces get **no** v2 events in A3 (not `(server, tool)` reads; D-wave rehomes them).
- New tables, migrations, or store adapters; facade `/v1/usage/*`.

## Guardrails

- **Service boundaries (hard):** apps → facade only; facade calls ai-backend over HTTP via `forward_json`, never
  imports its Python modules; no sibling `src/` imports, shared `.venv`s, or new `PYTHONPATH` entries.
  Surfaces/ledger logic lives in **ai-backend** only — none of it goes into backend or the facade.
- **Flag-off byte-identical (R8 blast radius):** with `SURFACES_V2` unset, every payload, event sequence, and API
  response is unchanged — any diff is a blocker, not a nit.
- **ai-backend rules** (`services/ai-backend/CLAUDE.md`): Pydantic at every boundary (no long-lived
  `dict[str, Any]` state); no module-level helper functions; no inline repeated strings
  (`Keys`/`Values`/`Messages`, D7); typed domain errors with safe public messages; never derive activity types
  from event-name prefixes — register explicit projector branches; tool payloads are untrusted (allow-lists in
  D5, bounded title).
- **Circular-import trap:** `runtime_api/schemas/events.py` must not import `agent_runtime.capabilities` at
  module level (lazy-import precedent in `_display_title_for`); the emitter module never imports `runtime_api`
  (string event types through `EmitFn`, D3).
- **Additive-only vocabulary:** the four event types and D1 fields are frozen until Wave E; later PRDs add
  fields/types, never rename (`v` is the escape hatch).
- **Tests** (`services/ai-backend/tests/CLAUDE.md`): fakes/mixins, no network, no live LLM; assert typed error
  classes and safe messages; this service's `.venv` only.

## Open questions

1. **RESOLVED (2026-07-23 close-out) — `Surface` (A1) vs `SurfaceSnapshot` (A3) as the `GET /surfaces` entity.**
   Owner decision: **option (a)** — keep `SurfaceSnapshot`/`SurfaceViewState` as A3's distinct, **additive**
   SurfaceStore-fold output. They carry the fold-bookkeeping fields A1's `Surface` lacks
   (`first_sequence_no` / `last_sequence_no`, used for upsert bookkeeping + `ledger_id` derivation), and A3's
   `SurfaceViewState` carries `generator_model?` (A1's `gen.model`) rather than `preference?` — which is exactly
   why the fold output stays a distinct type. A1's `Surface` entity is **left untouched** as the ledger/canvas
   entity; A3 does **not** edit A1's frozen contract. A3 serves `SurfaceSnapshot` from
   `GET /v1/agent/runs/{run_id}/surfaces`; B1's client fold + parity snapshot target the **same `SurfaceSnapshot`
   metadata shape** — snake_case keys, sorted by `surface_id`, metadata-only (no hydrated payload content). The
   two types coexist intentionally (ledger entity vs fold projection); see PRD-A1's entity-vs-fold-projection
   note. This does not block A3's DoD (the fold + endpoint are fully specified by D6/D7 regardless of the type name).
