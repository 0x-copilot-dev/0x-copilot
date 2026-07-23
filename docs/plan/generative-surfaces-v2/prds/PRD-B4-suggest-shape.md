# PRD-B4 — Suggest-a-shape (user-invited shaping)

Implements FR-D4: from a raw or generic fallback surface, the user clicks
"Suggest a shape for this tool →" and the system runs an **immediate, user-invited
shaping attempt** that is allowed a bigger budget than the automatic pass. On success the
generated SurfaceSpec is persisted to the org shape registry (this surface upgrades now;
every future render of this tool is shaped), on failure the honest fallback stays exactly
as it was — and both the request and its outcome are ledgered (`shape.requested` + an
outcome event) and metered (`usage.recorded {purpose: shape_request}`).

## Implementer brief

You are implementing this in a **fresh git worktree branched off `main`** of the
0x-copilot monorepo. Work only inside your worktree. Run `make setup` once from repo root
if `.venv`s / `node_modules` are missing. This PRD touches: `services/ai-backend`,
`services/backend-facade`, `packages/api-types`, `packages/service-contracts`,
`packages/chat-surface`.

Test commands (run all of these before calling the PR done):

```bash
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/surfaces/ tests/unit/runtime_api/
cd services/ai-backend && .venv/bin/python -m pytest            # full suite before merge
cd services/backend-facade && .venv/bin/python -m pytest
npm run test --workspace @0x-copilot/api-types
npm run test --workspace @0x-copilot/chat-surface
npm run typecheck --workspace @0x-copilot/api-types
npm run typecheck --workspace @0x-copilot/chat-surface
npm run typecheck --workspace @0x-copilot/frontend
npm run build --workspace @0x-copilot/frontend
npm run lint --workspace @0x-copilot/chat-surface
```

Read these files first (all paths repo-relative):

1. `docs/plan/generative-surfaces-v2/01-problem-and-requirements.md` — FR-D4 is your contract; also NFR-2 (honesty), FR-G1 (metering).
2. `docs/plan/generative-surfaces-v2/02-sdr.md` — §5 event vocabulary (authoritative), §7 S5 sequence (your flow is the `else` branch), §8 usage seam.
3. `docs/plan/generative-surfaces-v2/03-prds.md` — PRD-B4 summary; its DoD items are binding minimums.
4. `services/ai-backend/src/agent_runtime/capabilities/surfaces/generator.py` — `SurfaceSpecGenerator`, `SpecAuthoringSkill`, `SpecCompletionPort`, `GenFailure`, `SurfaceSpecLinter`; the retry budget is `1 + skill.max_retries`.
5. `services/ai-backend/src/agent_runtime/capabilities/surfaces/store.py` — `SpecKey`, `StoredSpec`, `SurfaceSpecStorePort`; the `build_surface_spec_store` selection lives in sibling `backend_store.py` (both re-exported from the package `__init__`).
6. `services/ai-backend/src/agent_runtime/api/events.py` — `RuntimeEventProducer.append_api_event` (how ledger events are appended).
7. `services/ai-backend/src/agent_runtime/api/approval_coordinator.py` — the coordinator pattern your new coordinator copies (app.state injection, org-scoped lookups).
8. `services/ai-backend/src/runtime_api/http/routes.py` — `RuntimeApiRouter.create_router()` + `RuntimeApiRoutes` handler pattern for `/v1/agent/*` routes.
9. `services/backend-facade/src/backend_facade/app.py` — `forward_json` + inline `/v1/agent/*` route style; `AuthenticatedIdentity.scoped_payload`.
10. `services/ai-backend/CLAUDE.md` and `services/ai-backend/tests/CLAUDE.md` — engineering + test rules (Keys classes, typed errors, mixin fakes).
11. `packages/chat-surface/CLAUDE.md` — substrate-agnostic rules (no window/fetch; ports only).
12. The landed Wave-A/B code: `docs/plan/generative-surfaces-v2/prds/PRD-A1-*.md`, `PRD-A3-*.md`, `PRD-B3-*.md` and the modules they created (v2 event constants, SurfaceStore projection, ViewDeriver). B4 depends on B3 being merged.

## Context

Generative Surfaces v2 re-founds the agent's surface layer on a typed, append-only **Work
Ledger** (SDR §2, §5): every gate, read, view derivation, staged write, and usage record
is a ledger event on the run's existing event log; the canvas, receipts, and usage totals
are projections of that ledger. Wave A built the vocabulary (PRD-A1), the usage meter
(PRD-A2), and ledger emission + SurfaceStore projection (PRD-A3). Wave B builds the
Studio read path: canvas + tabs (PRD-B1), provenance footers + raw fallback (PRD-B2),
and the view lifecycle — generic-now / shaped-later, upgrade toast, keep-generic,
regenerate (PRD-B3, SDR §7 S5).

PRD-B4 is the last Wave-B PR: the user-invited escape hatch when the automatic ladder
ends at a generic or raw view. Per 01 §FR-D4 the contract is: an immediate shaping
attempt, explicitly invited by the user, allowed to spend more effort than the automatic
pass; persisted to the shape registry on success so the tool is shaped from now on; the
attempt and outcome ledgered. "Trains the generator" reduces to "persists to the shape
registry + eval corpus" — no ML training. Honesty (01 §NFR-2) governs the failure path:
if the invited attempt cannot produce a spec that passes schema + lint, the fallback view
stays byte-identical and the ledger says so.

The generation machinery itself **survives from v1** (SDR §2 "what survives"): sample
redaction, structural + injection lint, forced source, retry-with-correction all live in
`agent_runtime/capabilities/surfaces/generator.py` and are reused, not reimplemented.
B4's new code is the invited-attempt orchestration (bigger budget), the HTTP path
(facade → runtime_api), the two ledger events, and the button.

## Interfaces consumed / exposed

**Consumed (must exist before this PR; from earlier PRDs and shipped code):**

- `SURFACES_V2` runtime flag (PRD-A3) — the env-var gate for all v2 emission/routes. VERIFY AT IMPL: the exact reader symbol A3 landed (expected: a `SurfacesV2Flag.enabled(environ)` class mirroring `SurfaceEmissionFlag` in `agent_runtime/capabilities/surfaces/config.py`).
- v2 event-type constants + ledger-id formatter `r<short>·<seq>` (PRD-A1) in `packages/service-contracts` and the `RuntimeApiEventType` additions in `services/ai-backend/src/runtime_api/schemas/common.py`. VERIFY AT IMPL: A1's enum naming convention for dot-form names (expected `RuntimeApiEventType.SHAPE_REQUESTED = "shape.requested"`; note the shipped v1 enum uses snake values — `SURFACE_SPEC_GENERATED = "surface_spec_generated"` — so match whatever A1 actually chose).
- `SurfaceEventV2` union + golden event fixtures (PRD-A1) in `packages/api-types` — `shape.requested` is in the SDR §5 vocabulary so A1 should already carry its payload type; B4 extends the union with the outcome event (below).
- SurfaceStore projection + `GET /v1/agent/runs/{id}/surfaces` (PRD-A3): B4 needs a per-run, org-scoped lookup `surface_id → (run_id, source{connector,op}, payload_ref, current view tier)`. VERIFY AT IMPL: the store's read API name from A3.
- ViewDeriver + `view.derived` emission (PRD-B3) — success reuses B3's upgrade path (`view.derived {tier: shaped, basis: generated, spec_ref}`) so the canvas merges the shaped view exactly as it does for automatic upgrades.
- UsageMeter seam (PRD-A2) — every completion call in this PR goes through `MeteredModelInvocation` (A2's wrapper; VERIFY AT IMPL exact import path) and emits `usage.recorded {purpose: shape_request, model, tokens_in, tokens_out, surface_id}`.
- Shipped v1 generation subsystem (all in `services/ai-backend/src/agent_runtime/capabilities/surfaces/`): `SurfaceSpecGenerator`, `SpecAuthoringSkill.load()`, `SpecCompletionPort`, `LangChainSpecCompletion`, `GenFailure`, `SpecKey.build`, `StoredSpec.from_generation`, `output_shape_hash`, `build_surface_spec_store(environ=, org_id=, user_id=)`, `validate_surface_spec`, `SurfaceSpecLinter`.
- `RuntimeEventProducer.append_api_event` (`agent_runtime/api/events.py`) and the runtime_api coordinator pattern (`agent_runtime/api/approval_coordinator.py`, wired in `runtime_api/app.py` onto `app.state`).
- Facade: `forward_json(app, method, path, target="ai_backend", ...)`, `AuthenticatedIdentity.scoped_payload` (`services/backend-facade/src/backend_facade/app.py`, `auth.py`).
- Backend org shape registry (shipped PRD-08 v1): `PUT /internal/v1/surfaces/specs` via `BackendHttpSurfaceSpecStore` — reached indirectly through `build_surface_spec_store` when `SURFACE_SPEC_STORE_BACKEND=backend`. No backend service changes in this PR.

**Exposed (later PRDs rely on):**

- `POST /v1/agent/surfaces/{surface_id}/shape-request` on facade + runtime_api (SDR §4). PRD-E1's receipt fold and PRD-E3's usage rollups will see its events; no other PR calls the endpoint.
- Ledger events `shape.requested` / `shape.resolved` (below) — consumed by the receipt fold (E1: "no view fit" attribution rows) and by both projectors' golden-fixture suites.
- The invited-attempt budget seam (`InvitedShapeAttempt`) — reused verbatim if Phase-2 adds re-invitation UX.

## Design

### Ledger events (SDR §5 vocabulary)

From SDR §5, verbatim:

```text
shape.requested    {surface_id, actor: user}                    ← "Suggest a shape" (FR-D4)
```

FR-D4 requires the **outcome** ledgered too. SDR §5 has no failure event; per SDR §12
("additive-only until E-wave") this PR adds one, named symmetrically with
`gate.opened/gate.resolved` (**NEW name, defined here**; update SDR §5 per the standard
DoD docs item):

```text
shape.resolved     {surface_id, outcome: shaped|no_fit, reason?}
```

- Success path emits, in order: `shape.requested` → (per attempt) `usage.recorded {purpose: shape_request, model, tokens_in, tokens_out, surface_id}` → `view.derived {surface_id, tier: shaped, basis: generated, spec_ref, gen: {model, ms}}` (via B3's ViewDeriver path) → `shape.resolved {surface_id, outcome: shaped}`.
- Failure path: `shape.requested` → `usage.recorded` per attempt → `shape.resolved {surface_id, outcome: no_fit, reason}` (`reason` is the safe lint/validation summary, never raw model output). The surface's view state does not change.
- Every payload carries the `v: 1` field (A1 convention). Ledger ids shown in UI use the A1 formatter `r<short>·<seq>`.

### HTTP contract

`POST /v1/agent/surfaces/{surface_id}/shape-request` (facade → runtime_api, both under
`/v1/agent` — app traffic stays facade-only).

Request body (untyped dict passthrough at the facade, so nothing is silently dropped —
see facade gotcha below):

```jsonc
{ "run_id": "run_...", "org_id": "...", "user_id": "..." } // org/user stamped by facade scoped_payload
```

`run_id` is required: the canvas is per-run (FR-A2), the client always knows it, and it
lets the runtime resolve the surface without a global surface index. The server verifies
`surface_id ∈ run_id ∈ org_id` and 404s on any mismatch.

Response `202 Accepted`:

```jsonc
{ "surface_id": "...", "status": "requested" }
```

The outcome arrives over the existing run SSE stream as ledger events — no polling
endpoint. Errors (typed, safe messages; codes are the `detail` strings):

| Condition                                                        | Status | detail                                                                                                                                                |
| ---------------------------------------------------------------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SURFACES_V2` off                                                | 404    | route not found (route registered but handler 404s when flag off — keeps OpenAPI stable; VERIFY AT IMPL against how A3 gated its routes and match it) |
| surface/run not found or wrong org                               | 404    | `surface_not_found`                                                                                                                                   |
| surface tier is already `shaped`                                 | 409    | `surface_already_shaped`                                                                                                                              |
| an invited attempt already in flight for this surface            | 409    | `shape_request_in_flight`                                                                                                                             |
| no shaping model configured (BYOK posture, SDR open decision #1) | 422    | `shaping_unavailable` — checked **before** emitting `shape.requested` (nothing ledgered for a request that never starts)                              |

### ai-backend: domain runner + API coordinator

> **Relationship to B3's reserved seam.** PRD-B3 §Interfaces states "B4 adds
> `shape_request(...)` beside `derive`/`regenerate` on `ViewDeriver`." B4 realizes that
> reserved seam as the standalone `ShapeRequestRunner` + `ShapeRequestCoordinator` below
> rather than a bare `ViewDeriver` method, because the invited attempt carries
> orchestration a pure `ViewDeriver` method cannot: the raised-budget generator, the
> per-surface in-flight guard, the two new ledger events, and its own HTTP path. The two
> paths stay consistent by **sharing B3's `ShapingModelResolver`** (model resolution) and
> B3's `payload_ref` loader (sample retrieval); no logic is duplicated. If B3-symmetry is
> wanted, `ViewDeriver.shape_request(...)` may be a one-line delegate to
> `ShapeRequestRunner.run`, but it is not required by this PRD. See Open questions.

New module `services/ai-backend/src/agent_runtime/capabilities/surfaces/shape_request.py`
(NEW — all symbols in it are new names defined by this PRD):

```python
class InvitedShapeAttempt:
    """Budget profile for a user-invited attempt (bigger than the automatic pass)."""
    ENV_MODEL = "SURFACE_SHAPE_REQUEST_MODEL"          # override; else B3 ShapingModelResolver.resolve()
    ENV_MAX_RETRIES = "SURFACE_SHAPE_REQUEST_MAX_RETRIES"
    DEFAULT_MAX_RETRIES = 3                            # automatic pass: skill.json max_retries = 1

class ShapeRequestOutcome(StrEnum):
    SHAPED = "shaped"
    NO_FIT = "no_fit"

class ShapeRequestError(Exception): ...                # typed, safe-message domain error

class ShapeRequestRunner:
    def __init__(self, *, generator: SurfaceSpecGenerator, store: SurfaceSpecStorePort,
                 emit: EmitFn) -> None: ...
        # `generator` arrives already budget-raised AND meter-wired: the coordinator
        # builds SurfaceSpecGenerator(completion=…, skill=…with_max_retries(n),
        # usage_meter=MeteredModelInvocation(purpose=SHAPE_REQUEST)) and hands it in.
        # The runner therefore takes no separate meter param — metering happens inside
        # generate() via the injected usage_meter (A2 seam).
    async def run(self, *, server: str, tool: str, sample_output: object,
                  surface_id: str) -> ShapeRequestOutcome:
        """generate (invited budget) -> on success store.put + emit view.derived +
        shape.resolved{shaped}; on GenFailure store.record_failure + shape.resolved{no_fit}.
        Ignores store.has_failure (an invited request may retry a shape the automatic
        pass already failed)."""
```

The bigger budget is implemented **without forking the generator**: construct
`SurfaceSpecGenerator(completion=…, skill=SpecAuthoringSkill.load().with_max_retries(n), usage_meter=…)`
where `with_max_retries` is a small NEW method on `SpecAuthoringSkill` returning a copy
with an overridden `max_retries`. Because the shipped `SpecAuthoringSkill.__init__` is
keyword-only over `skill_version, model_hint, max_retries, doctrine, examples` and stores
`doctrine`/`examples` in private fields, `with_max_retries(n)` returns
`SpecAuthoringSkill(skill_version=self.skill_version, model_hint=self.model_hint,
max_retries=n, doctrine=self._doctrine, examples=self._examples)` (the generator already
computes `attempts = 1 + max(self._skill.max_retries, 0)`, so a raised `max_retries`
raises the attempt count with zero generator changes).

**Model id — reuse B3's resolver (do not reinvent the env chain).** B3 lands
`ShapingModelResolver.resolve(environ=…, run_provider=…) -> str | None` as the single
place the automatic shaping model is chosen; it already encodes SDR §13 decision #1
(cheapest model of the configured default provider; `None` when no BYOK key ⇒ shaping
off). B4 layers exactly one override on top: the model id is
`environ.get("SURFACE_SHAPE_REQUEST_MODEL")` if set (the "stronger model" knob), **else
`ShapingModelResolver.resolve(environ=os.environ, run_provider=…)`**. A `None` result (no
request-model override AND resolver returns `None`) ⇒ `shaping_unavailable` (422). Do
**not** fall back to a bare `SURFACE_SPEC_MODEL` read — that would bypass decision #1's
BYOK/default-provider logic. VERIFY AT IMPL: `ShapingModelResolver`'s import path and the
`run_provider` argument source (B3 threads the run's provider; resolve from the run record
the coordinator already loaded). The chat model itself is built via `build_chat_model_from_id`
exactly as `build_surface_generation_scheduler` does.

**Metering — the A2 seam is a generator kwarg, not a completion wrapper.** Per PRD-A2
§(c)/§(b), the completion port is **not** wrapped; instead the generator records each
attempt through an injected `usage_meter: MeteredModelInvocation`
(`agent_runtime/observability/usage_meter.py`). The coordinator builds
`UsageMeter(recorder=…, emit_event=<closure over event_producer.append_api_event>,
surfaces_v2=…)` then `MeteredModelInvocation(meter=…, run=run, purpose=Purpose.SHAPE_REQUEST)`
and injects it as the `usage_meter=` kwarg when it constructs the budget-raised
`SurfaceSpecGenerator`, then hands the finished generator to the runner;
`SurfaceSpecGenerator.generate` calls `await usage_meter.record_attempt(...)` once per
attempt (this is what makes retries count — A2 DoD). Attribute `surface_id` on each record.
VERIFY AT IMPL: A2's exact `record_attempt` / `surface_id` argument names.

Sample redaction, `_force_source`, schema validation, and `SurfaceSpecLinter` (incl. the
injection kill-switch phrases) run unchanged — the invited path gets more attempts and
possibly a stronger model, **never** weaker lint.

**Generator call shape.** `SurfaceSpecGenerator.generate` is
`generate(*, server: str, tool_descriptor: GenToolDescriptor, sample_output: object)` —
it takes a **`GenToolDescriptor`, not a bare `tool` string**. The runner receives
`tool: str` and synthesizes the minimal descriptor the generator's own docstring blesses:
`GenToolDescriptor(name=tool)` (description/input_schema/output_shape default to empty; a
richer descriptor is not available from the SurfaceStore snapshot, which carries only
`source{connector,op}` + `payload_ref`). The `server`/`tool` used for the descriptor and
for `SpecKey.build` come from the resolved surface's `source{connector, op}`.

Persistence uses the shipped key scheme:
`SpecKey.build(server=server, tool=tool, output_shape_hash=output_shape_hash(sample_output), skill_version=generator.skill_version)`
(the keyword-only `skill_version` is required by the shipped signature — the automatic
scheduler passes `self._generator.skill_version` the same way)

- `StoredSpec.from_generation(key=key, spec=spec, generator_model=…)` + `store.put(key, stored)` where the store comes from
  `build_surface_spec_store(environ=os.environ, org_id=…, user_id=…)` — so desktop persists
  to the file store and team deployments persist to the backend org registry with zero new
  code. On `GenFailure`, call `store.record_failure(key, reason, raw_output)` (keeps the
  _automatic_ scheduler suppressed; invited attempts ignore it, see above).

New API coordinator `services/ai-backend/src/agent_runtime/api/shape_request_coordinator.py`
(NEW), following `approval_coordinator.py`:

```python
class ShapeRequestCoordinator:
    def __init__(self, *, persistence: PersistencePort, event_producer: RuntimeEventProducer,
                 surface_store: <A3 SurfaceStore read port>, usage_recorder: <A2 UsageRecorderPort>,
                 environ: Mapping[str, str], schedule: ScheduleFn | None = None) -> None: ...
        # usage_recorder is required to build UsageMeter(recorder=…, emit_event=…, surfaces_v2=…)
        # (A2 §(b) seam) → MeteredModelInvocation(purpose=Purpose.SHAPE_REQUEST); wire it from
        # app.state the same way A2 exposes the recorder on the run handler.
        #   VERIFY AT IMPL: A2's recorder port name + how app.py surfaces it on app.state.
    async def request_shape(self, *, org_id: str, user_id: str, run_id: str,
                            surface_id: str) -> ShapeRequestAccepted:
        # 1 flag check; 2 resolve run (org-scoped) + surface in SurfaceStore; 3 tier guard
        # (raw|generic only); 4 model-availability guard; 5 per-surface in-flight guard
        # (instance dict[str, asyncio.Task], test seam via injectable `schedule`);
        # 6 emit shape.requested; 7 schedule ShapeRequestRunner.run as asyncio task;
        # 8 return 202 body. Task exceptions are caught and emitted as shape.resolved{no_fit}.
```

The runner executes **in the runtime_api process** (the run may already be completed; the
worker's per-run scheduler binding is not available post-run). `emit` closes over
`event_producer.append_api_event(run=run_record, source=StreamEventSource.SYSTEM,
event_type=…, payload=…)` — the same append path approvals use, so `sequence_no`
monotonicity, SSE fan-out, and replay come for free. **Sample-output retrieval:** the
resolved SurfaceStore snapshot carries `payload_ref` in A3's `"call:<call_id>"` scheme
(A3 §D-notes); resolve it by loading the `tool_result`/`read.executed` event for that
`call_id` from the persistence event store (replay/`list_events`) and reading its output
payload — this is the identical seam B3's `ViewDeriver.regenerate` step 1 uses ("Load the
surface record + payload via `payload_ref`; missing ⇒ `surface_not_found` → 404"). VERIFY
AT IMPL: reuse B3's `payload_ref` loader verbatim rather than re-deriving the `call:`
parse (B3 owns it); a missing payload maps to the same `surface_not_found` 404 as a
missing surface.

Route: in `services/ai-backend/src/runtime_api/http/routes.py` add
`RuntimeApiRoutes.shape_request` and register on the existing `/v1/agent` router
(`RequireScopes(RUNTIME_USE)` comes from the router-level dependency):

```python
router.add_api_route("/surfaces/{surface_id}/shape-request", RuntimeApiRoutes.shape_request,
                     methods=["POST"], name=Keys.RouteName.SHAPE_REQUEST)   # NEW constant
```

Wire the coordinator in `runtime_api/app.py` `create_app` onto
`app.state.shape_request_coordinator` (mirror `approval_coordinator`). Add
`Keys.RouteName.SHAPE_REQUEST = "shape_request"` in `agent_runtime/api/constants.py`.

Event projection: `runtime_api/schemas/events.py` gets strict allow-list projections for
both payloads (mirror `_surface_spec_generated_payload`): text keys `surface_id`,
`actor` / `outcome`, `reason`; int `v`. `activity_kind = RuntimeActivityKind.EVENT`
(state merge, not a card).

### Facade

Inline route in `services/backend-facade/src/backend_facade/app.py`, next to the other
`/v1/agent/*` closures: authenticate → `identity.scoped_payload(body)` (stamps
org/user; body is an **untyped `dict[str, object]`** — do NOT add a typed Pydantic model,
the `extra="ignore"` + `model_dump` combination silently drops fields) →
`forward_json(app, "POST", f"/v1/agent/surfaces/{surface_id}/shape-request",
target="ai_backend", json=payload, identity=identity)`. Error passthrough is automatic
(`_upstream_error_detail`). Add the path to `tests/test_public_route_contract.py`.

### Contracts (packages/api-types)

Additions to `packages/api-types/src/index.ts` (public facade surface ⇒ must be
mirrored): `ShapeRequestBody { run_id }`, `ShapeRequestAccepted { surface_id, status:
"requested" }`, `ShapeResolvedPayload { surface_id, outcome: "shaped" | "no_fit",
reason?, v }` added to the `SurfaceEventV2` union (A1 owns `ShapeRequestedPayload`
already — VERIFY AT IMPL, add if missing). Python constant for `"shape.resolved"` lands
beside A1's event-type constants in `packages/service-contracts` (VERIFY AT IMPL: A1's
module name for these constants).

### chat-surface (🎨 button + states)

Per D28 purity, the button is host-side chrome, not an adapter concern. It lives in the
same canvas surface-chrome component B2/B3 built for footers + Regenerate/Keep-generic
(VERIFY AT IMPL: exact file from B2/B3, expected under
`packages/chat-surface/src/thread-canvas/` — which exists today with the v1 `Tc*`
components incl. `TcSurfaceMount.tsx` — or a new B1 directory; `src/surfaces-v2/` does
not exist yet). New pieces:

- Prop `onShapeRequest?: (surfaceId: string) => void` threaded the same way B3 threads its Regenerate callback (RunDestination → ThreadCanvas → surface chrome); RunDestination wires it to `transport.request({ method: "POST", path: "/v1/agent/surfaces/{id}/shape-request", body: { run_id } })` via the injected `Transport` port (`request<TRes>(req: TypedRequest)` — a single request-object argument, not positional method/path) — never `fetch`.
- Client projection: the ts event projector (B1's v2 fold) folds `shape.requested` / `shape.resolved` into per-surface state `shapeRequest: "idle" | "requested" | "no_fit"`. Success needs no special casing — the `view.derived {tier: shaped}` merge from B3 flips the view; `shape.resolved{shaped}` just returns the button state to idle (button then hidden because tier is shaped).
- States on the fallback view: idle → "Suggest a shape for this tool →" (kit `.ui-button--ghost`); requested → disabled with assembling label ("Attempting a shape…", reuse B2's skeleton/assembling idiom); `no_fit` → inline honest line ("No confident fit — keeping the raw/generic view. Nothing is hidden.") + the button re-enabled. Copy grade: the honesty sentence is requirement-grade (01 §FR-D3); the rest is draft microcopy.
- Button renders only when the surface tier is `raw` or `generic` and the v2 canvas flag (B1) is on.

## Implementation plan

1. **service-contracts + api-types**: add `"shape.resolved"` constant beside A1's event-type constants (`packages/service-contracts/src/copilot_service_contracts/<A1 module>.py` — VERIFY AT IMPL); add `ShapeResolvedPayload`, `ShapeRequestBody`, `ShapeRequestAccepted` (+ `ShapeRequestedPayload` if A1 missed it) to `packages/api-types/src/index.ts`; extend A1's golden event fixture file with one success and one no-fit sequence.
2. **ai-backend domain**: create `src/agent_runtime/capabilities/surfaces/shape_request.py` (`InvitedShapeAttempt`, `ShapeRequestOutcome`, `ShapeRequestError`, `ShapeRequestRunner`); add `SpecAuthoringSkill.with_max_retries` to `src/agent_runtime/capabilities/surfaces/generator.py`; export new names from `src/agent_runtime/capabilities/surfaces/__init__.py`.
3. **ai-backend events**: add `SHAPE_REQUESTED` / `SHAPE_RESOLVED` to `src/runtime_api/schemas/common.py` (skip if A1 landed them); allow-list projections in `src/runtime_api/schemas/events.py`.
4. **ai-backend API**: create `src/agent_runtime/api/shape_request_coordinator.py`; add `Keys.RouteName.SHAPE_REQUEST` to `src/agent_runtime/api/constants.py`; handler + route in `src/runtime_api/http/routes.py`; wiring in `src/runtime_api/app.py`.
5. **facade**: inline POST route in `src/backend_facade/app.py`; extend `tests/test_public_route_contract.py`.
6. **chat-surface**: fold the two events in the v2 ts projector (B1's file); button + three states in the B2/B3 surface-chrome component; `onShapeRequest` threading in `src/destinations/run/RunDestination.tsx`; transport call in the RunDestination wiring.
7. **tests** (§Test plan), **parity run**, **live smoke**; update SDR §5 with `shape.resolved` (docs DoD item).

## Test plan

**ai-backend — `tests/unit/agent_runtime/surfaces/test_shape_request.py` (NEW).** Inject a
counting fake `SpecCompletionPort` (per tests/CLAUDE.md: fakes in mixins, typed-error +
safe-message assertions):

- `test_invited_budget_exceeds_automatic` — DoD adversarial: fake completion always fails validation; assert invited attempt count == `1 + SURFACE_SHAPE_REQUEST_MAX_RETRIES` (default 4) > automatic (2 with the packaged skill's `max_retries: 1`).
- `test_success_persists_to_store_and_future_renders_hit_registry` — after success, `SurfaceProjector(store=…).resolve(server, tool, same-shape output)` returns an envelope **with** the spec (rung-2 hit).
- `test_success_emits_view_derived_then_shape_resolved_shaped` — event order + payload fields verbatim.
- `test_failure_emits_no_fit_and_view_state_unchanged` — DoD: `GenFailure` ⇒ `shape.resolved {outcome: no_fit, reason}`, `record_failure` called, no `view.derived` emitted.
- `test_failure_reason_is_safe_message` — raw model output never appears in the event payload.
- `test_invited_attempt_ignores_recorded_failure` — a prior `record_failure` for the key does not block the invited run.
- `test_injection_lint_still_enforced` — adversarial: sample crafted so the model's spec labels contain `ignore previous` / markdown links ⇒ lint fails ⇒ `no_fit` (the invited path must not bypass the kill-switch).
- `test_every_attempt_metered_with_purpose_shape_request` — one usage record per attempt, `purpose == "shape_request"`, `surface_id` attributed.
- `test_model_resolution_chain` — `SURFACE_SHAPE_REQUEST_MODEL` set ⇒ used verbatim; unset ⇒ falls to B3's `ShapingModelResolver.resolve(...)` (NOT a bare `SURFACE_SPEC_MODEL` read — that would bypass decision #1's BYOK/default-provider logic); resolver returns `None` (no BYOK key) ⇒ `ShapeRequestError("shaping_unavailable")` → 422.

**ai-backend — `tests/unit/runtime_api/test_shape_request_route.py` (NEW).** TestClient
over `create_app` with in-memory adapters:

- `test_returns_202_and_emits_shape_requested`
- `test_flag_off_is_404_and_no_events` (flags-off byte-identical DoD)
- `test_wrong_org_is_404_surface_not_found` (tenant isolation)
- `test_run_surface_mismatch_is_404`
- `test_already_shaped_is_409` / `test_in_flight_is_409` (second POST while a fake-slow task runs)
- `test_shaping_unavailable_is_422_and_nothing_ledgered`
- `test_runner_crash_resolves_no_fit` (task exception ⇒ `shape.resolved{no_fit}`, never a hung "requested" state)

**facade — `tests/test_shape_request_proxy.py` (NEW)**, pattern of
`test_approval_decision_proxy.py` (capturing forwarder): forwards to
`/v1/agent/surfaces/{id}/shape-request` with `target="ai_backend"`, body passes through
with org/user stamped, 401 without bearer, upstream 409/422 status passthrough. Plus the
`test_public_route_contract.py` addition.

**api-types** — `packages/api-types/src/shapeRequest.test.ts` (NEW): guards/shape checks
for the three new types; golden fixture round-trip.

**chat-surface** — extend the v2 projector test file (B1's, sibling `*.test.ts`) with the
success + no-fit golden sequences (ts fold === py fold parity via the shared fixture);
component test for the three button states and that the button is absent on shaped
surfaces and when the flag is off.

**Live smoke (desktop stack, step by step):**

1. `make dev` (or packaged desktop boot) with `SURFACES_V2=true`, `SURFACE_SPEC_MODEL=""` (automatic shaping OFF ⇒ guaranteed generic fallback), `SURFACE_SHAPE_REQUEST_MODEL=<cheap model id>`, BYOK key configured.
2. `export TOKEN=$(make dev-bearer)`; start a run that calls an MCP tool with **no** builtin spec (any connector outside the builtin twelve).
3. Canvas shows the tier-3 generic view with the "Suggest a shape" button (footer shows ledger id `r<short>·<seq>`).
4. Click the button → assembling state → view upgrades in place; `curl -H "Authorization: Bearer $TOKEN" localhost:8200/v1/agent/runs/<id>/events` shows `shape.requested` → `usage.recorded{purpose:"shape_request"}` → `view.derived{tier:"shaped",basis:"generated"}` → `shape.resolved{outcome:"shaped"}` in order.
5. Re-run the same tool in a new run → surface renders shaped immediately (registry hit, no generation events).
6. Failure leg: point `SURFACE_SHAPE_REQUEST_MODEL` at a model that cannot satisfy the schema (or force lint failure via a fixture connector) → button click ⇒ fallback view unchanged, honest no-fit line, `shape.resolved{outcome:"no_fit"}` in the events.
7. Flag-off leg: restart with `SURFACES_V2` unset → button absent, POST returns 404, event stream byte-identical to pre-B4.

## Definition of done

From 03-prds.md PRD-B4 (binding minimums, never weakened):

- [ ] Invited attempt allowed a bigger budget than automatic (asserted via injected completion) — proven by `test_invited_budget_exceeds_automatic`.
- [ ] Success upgrades this surface now and future renders of the tool (registry hit) — proven by `test_success_persists_to_store_and_future_renders_hit_registry` + live-smoke steps 4–5.
- [ ] Failure stays honest (fallback unchanged, outcome ledgered); metered (purpose: shape_request) — proven by `test_failure_emits_no_fit_and_view_state_unchanged`, `test_every_attempt_metered_with_purpose_shape_request`, live-smoke step 6.

Standard DoD:

- [ ] Unit tests in every touched component's venv/workspace pass; typecheck + build green (all commands in the Implementer brief).
- [ ] Flags off ⇒ byte-identical behavior — `test_flag_off_is_404_and_no_events` + live-smoke step 7.
- [ ] No service-boundary violations (apps→facade only; no cross-`src/` imports) — the button calls the facade route via the Transport port; ai-backend reaches the backend registry only through `BackendHttpSurfaceSpecStore` over HTTP.
- [ ] New LLM call sites go through the UsageMeter seam — the invited attempt is metered by threading A2's `MeteredModelInvocation` (purpose `shape_request`) as the generator's `usage_meter=` kwarg (per-attempt `record_attempt`); no chat model is constructed outside `build_chat_model_from_id`, so A2's grep-gate stays green.
- [ ] Docs: SDR §5 updated with `shape.resolved` (additive), S5 note that the invited branch emits an explicit outcome event.

UI DoD (🎨 — the button):

- [ ] Built from design-system/chat-surface kit components (`.ui-button--ghost`, existing assembling/skeleton idiom; no host-app one-off styling).
- [ ] `tools/design-parity/` run against the staged v2 mock's raw-fallback region (walkthrough part 06 — the "Suggest a shape for this tool →" affordance): **0 HIGH drift**; report committed under `tools/design-parity/surfaces/<name>/out/report.md`.
- [ ] Live desktop smoke of the full flow on the real stack (the script above), not just tests.

## Out of scope

- Any change to the **automatic** shaping pass (B3 owns it) or to redaction/lint content.
- Backend service changes — the registry PUT path exists (v1 PRD-08); no new backend routes, tables, or migrations.
- Eval-corpus capture of invited samples ("persists to … eval corpus" in FR-D4): record the ledger events now; corpus export is PRD-E3's harness territory.
- Pricing/dollars, usage UI (FR-G4), receipts rendering of these events (E1).
- Re-shaping an already-shaped surface, per-user shape preferences beyond B3's keep-generic, and any Phase-2 failure-path styling (states must exist; polish iterates).
- v1 `result["surface"]` emission — untouched (compat window ends in E3).

## Guardrails

- **Service boundaries are hard**: apps → facade only (never `:8100`/`:8000` directly); facade may not import ai-backend/backend Python modules; ai-backend ↔ backend is internal HTTP with the service token; no sibling `src/` imports, no shared `.venv`s, no `PYTHONPATH` siblings.
- **Flag-off byte-identical**: with `SURFACES_V2` unset, the event stream, the facade OpenAPI behavior toward existing routes, and the canvas UI must be indistinguishable from pre-B4 `main` (snapshot-style tests, not eyeballing).
- **ai-backend rules** (services/ai-backend/CLAUDE.md): Pydantic at every IO boundary (event payloads, request/response models); typed domain errors with safe public messages — never leak lint/model internals to HTTP or model output; no module-level helper functions (behavior lives inside classes); repeated keys/messages in nested `Keys`/message classes; tool payloads and model output are untrusted until validated — the invited path keeps `_force_source`, schema validation, and the injection linter exactly as shipped.
- **Test rules** (tests/CLAUDE.md): fakes/mixins, no network or live LLM calls in unit tests; assert typed error classes and safe messages; use this service's `.venv` only.
- **chat-surface rules** (packages/chat-surface/CLAUDE.md): substrate-agnostic — no `window`/`fetch`/`localStorage` (eslint-enforced); all IO through the `Transport` port; components presentational, data + callbacks via props; both hosts get the feature through the same component, host adapters only wire data.
- **Facade rules**: untyped dict body passthrough (typed models silently drop undeclared fields); `scoped_payload` stamps identity — never trust client-sent org/user; error shapes come from upstream via `_upstream_error_detail`, never invented; new public routes get `test_public_route_contract.py` entries and api-types mirrors.
- **Fail-closed** (SDR §10): a shape request can only ever change _how_ data is displayed — it must not touch write policy, approvals, or commit paths; the generated spec is data validated by the schema gate on every write path (runtime + backend registry both re-validate).

## Open questions

These are genuine design choices not settled by 01/02/03 or a sibling PRD; they do not
block the implementation plan above (each has a stated default) but a human should ratify
them.

1. **`ViewDeriver.shape_request(...)` symmetry vs the standalone runner.** PRD-B3
   §Interfaces promises "B4 adds `shape_request(...)` beside `derive`/`regenerate` on
   `ViewDeriver`." B4 instead realizes the invited attempt as `ShapeRequestRunner` +
   `ShapeRequestCoordinator` (it needs a raised budget, a per-surface in-flight guard, two
   ledger events, and its own HTTP path — more than a pure `ViewDeriver` method). **Default
   taken:** no `ViewDeriver.shape_request` method; B3's forward-reference is satisfied by
   the runner (and B3's prose should be updated to say so — a docs edit B4 cannot make from
   its own file). Ratify: keep the runner as the sole home, or also add a one-line
   `ViewDeriver.shape_request` delegate for call-site symmetry.

2. **Descriptor richness for the "higher-effort" attempt.** The generator prompt is
   stronger when `GenToolDescriptor` carries the tool's `description`/`input_schema`, but
   the A3 SurfaceStore snapshot exposes only `source{connector,op}` + `payload_ref`, so
   B4's runner synthesizes the minimal `GenToolDescriptor(name=tool)`. **Default taken:**
   minimal descriptor (name only) — the invited budget is spent on _more attempts / a
   stronger model_, not a richer prompt. Ratify: is a minimal descriptor acceptable, or
   should the invited path additionally fetch the live MCP tool descriptor (a backend/MCP
   catalog lookup not otherwise in B4's scope) so "higher effort" also means a fuller
   prompt?
