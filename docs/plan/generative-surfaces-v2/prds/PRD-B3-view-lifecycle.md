# PRD-B3 — View lifecycle v2 (generic ⇄ shaped, upgrade, regenerate) 🎨

Give every v2 surface an explicit, auditable **view state**: a registry-miss tool renders a generic view
immediately (`view.derived {tier: generic, basis: schema}`), a background shaping pass upgrades it in place with a
non-modal "View upgraded" toast, the user can pin "Keep generic" / toggle back (`view.preference` — a ledger event,
so it survives reload by replay), and a per-surface **Regenerate** re-derives the view from the stored response
payload with **zero new connector traffic**. Shaping becomes on-by-default on desktop (cheapest model of the
configured default provider; off with no key); every shaping call is metered with `purpose: view_shaping`.
Requirements: FR-D1/D2, FR-A6, NFR-2/5; design: SDR §3 (ViewDeriver), §5 vocabulary, §7 S5.

## Implementer brief

You are implementing one PR in a monorepo. Work in a **fresh git worktree branched off `main`** (never commit on
`main` directly). Repo root contains `services/` (Python 3.13, one `.venv` per service), `packages/` + `apps/` (npm
workspace). If `.venv`s or `node_modules` are missing, run `make setup` at the repo root first.

Test commands for every component this PR touches:

```bash
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/surfaces_v2/ tests/unit/runtime_api/test_surface_view_routes.py
cd services/backend-facade && .venv/bin/python -m pytest tests/test_surface_view_proxy.py
npm run test --workspace @0x-copilot/chat-surface && npm run typecheck --workspace @0x-copilot/chat-surface
npm run test --workspace @0x-copilot/surface-renderers
npm run typecheck --workspace @0x-copilot/api-types && npm run test --workspace @0x-copilot/api-types
npm run typecheck --workspace @0x-copilot/frontend && npm run build --workspace @0x-copilot/frontend
```

Read these files first (paths relative to repo root):

1. `docs/plan/generative-surfaces-v2/01-problem-and-requirements.md` — §2D (FR-D1/D2), FR-A6, NFR-5.
2. `docs/plan/generative-surfaces-v2/02-sdr.md` — §5 (event vocabulary, authoritative), §7 S5, §11 (compat rules).
3. `docs/plan/generative-surfaces-v2/03-prds.md` — Wave A + B summaries; this PRD's DoD there is a binding minimum.
4. `services/ai-backend/src/agent_runtime/capabilities/surfaces/generator.py` — the reused generation subsystem
   (`SurfaceSpecGenerator`, `SurfaceGenerationScheduler`, `build_surface_generation_scheduler`, completion port).
5. `services/ai-backend/src/agent_runtime/capabilities/surfaces/store.py` + `builtin.py` — `SpecKey`,
   `SurfaceSpecStorePort`, rung-1 lookup; `build_surface_spec_store` is in sibling `backend_store.py`.
6. `services/ai-backend/src/agent_runtime/api/events.py` — `append_api_event` (the event append seam; line 60).
7. `services/ai-backend/src/runtime_api/http/routes.py` — `RuntimeApiRouter.create_router()` (~line 560):
   registration style + `Keys.RouteName` constants.
8. `services/backend-facade/src/backend_facade/app.py` — `forward_json` +
   `FacadeAuthenticator.authenticate_request` passthrough pattern (~line 396).
9. `packages/chat-surface/src/thread-canvas/ledgerProjection.ts` — B1's v2 client fold (`projectLedger`,
   `LedgerProjection`, `LedgerSurface`, `tabUriForSurface`, `toParitySnapshot`) that B3 extends with per-surface
   view state; `eventProjector.ts` is the untouched v1 projector (pattern reference only, for the in-place merge).
10. `packages/chat-surface/src/destinations/run/RunDestination.tsx` — `useTransport()` + `transport.request` POST
    pattern (~line 827) to copy for the new callbacks.
11. `services/ai-backend/CLAUDE.md` + `services/ai-backend/tests/CLAUDE.md` — engineering + test rules.

## Context

Generative Surfaces v2 re-founds the agent's surface layer on an explicit, typed **Work Ledger**: every
consequential runtime fact is an append-only event on the existing per-run event log, and everything the user sees
is a projection of those events (../02-sdr.md §2–§3). Wave A built the vocabulary (PRD-A1), the UsageMeter
(PRD-A2), and ledger emission + the SurfaceStore projection + `GET /v1/agent/runs/{id}/surfaces` behind the
`SURFACES_V2` runtime flag (PRD-A3). Wave B builds the canvas: PRD-B1 mounts ThreadCanvas tabs off a TS fold of the
same golden events; PRD-B2 adds provenance footers, skeletons, and the raw fallback.

This PR (B3) makes the **honest ladder** (../01-problem-and-requirements.md FR-D1/D2, ../02-sdr.md §7 S5) a
first-class per-surface lifecycle instead of v1's implicit "spec absent ⇒ tier-3" contract: view tier is explicit
ledger state, upgrades merge in place, tier preference is durable, and "Looks wrong? Regenerate" (FR-A6)
re-derives from the stored payload — a pure function of the stored tool response (NFR-5), never a re-fetch. The v1
spec-generation subsystem (redaction, lint, injection kill-switch, retry protocol) is reused underneath unchanged
(../02-sdr.md §2 "what survives"). PRD-B4 ("Suggest a shape") builds directly on this PR's endpoints and deriver.

## Interfaces consumed / exposed

**Consumed (from earlier PRDs — confirm exact names in the sibling PRD docs in this directory before coding):**

- PRD-A1 (`../03-prds.md` Wave A): `SurfaceEventV2` union in `packages/api-types` with `view.derived` /
  `view.preference` payload types; ai-backend pydantic mirrors; event-type constants + ledger-id formatter
  (`r<short>·<seq>`) in `packages/service-contracts`; the golden-event fixture. VERIFY AT IMPL: exact exported type
  names (e.g. `ViewDerivedPayload`, `ViewPreferencePayload`) + constants module path.
- PRD-A2: `MeteredModelInvocation` in `services/ai-backend/src/agent_runtime/observability/usage_meter.py`
  (ctor `(*, meter: UsageMeter, run: RunRecord, purpose: Purpose)`; `record_attempt(*, model_id, input_tokens,
output_tokens, duration_ms, surface_id=None)`) and `Purpose.VIEW_SHAPING` in
  `agent_runtime/observability/attribution.py`. Note: A2 (D5b) **already** routes the async **scheduler** shaping
  path through this seam via `build_surface_generation_scheduler(usage_meter=...)`; B3 adds the seam only to the new
  **regenerate** direct path.
- PRD-A3: the `SURFACES_V2` flag reader; the v2 ledger append path (worker-side emitter wrapping
  `RuntimeEventProducer.append_api_event`, `services/ai-backend/src/agent_runtime/api/events.py:60`); the SurfaceStore
  projection with per-surface records `{surface_id, source{connector,op}, payload_ref}`; the
  `GET /v1/agent/runs/{id}/surfaces` route + facade passthrough. VERIFY AT IMPL: emitter class name, SurfaceStore
  record type, the payload-ref loader, and A3's flag-off route gating (mirror it).
- PRD-B1: the TS v2 fold `packages/chat-surface/src/thread-canvas/ledgerProjection.ts`
  (`projectLedger`/`LedgerProjection`/`LedgerSurface`/`tabUriForSurface`/`toParitySnapshot`), the `useSurfacesV2`
  hydration hook + the `resolveSurfaceState` prop on `ThreadCanvasProps`, the `surfacesV2` flag on
  `RunDestinationProps`, and the host flag helpers `isSurfacesV2CanvasEnabled()` (web) / `isSurfacesV2Enabled()`
  (desktop). The py↔ts golden-fixture parity test is `ledgerProjection.parity.test.ts`.
- Existing v1 subsystem (verified in this repo, reused as-is; all under
  `services/ai-backend/src/agent_runtime/capabilities/surfaces/`): `SurfaceSpecGenerator.generate`,
  `SpecCompletionPort`, `LangChainSpecCompletion`, `SurfaceGenerationScheduler`,
  `build_surface_generation_scheduler`, `SpecKey.build`, `output_shape_hash`, `builtin.lookup`,
  `build_surface_spec_store`, `SurfaceSpecLinter`, `SampleRedactor`.

**Exposed (later PRDs depend on these; do not rename after merge):**

- `ViewDeriver` (new, ai-backend) with `derive(...)` / `regenerate(...)` — B4 adds `shape_request(...)` beside them.
- `POST /v1/agent/surfaces/{surface_id}/regenerate` and `POST /v1/agent/surfaces/{surface_id}/view-preference`
  (runtime_api + facade) — B4 adds `/shape-request` on the same pattern.
- `ShapingModelResolver` (new) — the single place B4's "higher effort" budget also hooks.
- TS: per-surface `viewState` in the v2 fold; `ViewUpgradeToast` + `ViewTierToggle`; `onRegenerateView` /
  `onSetViewPreference` canvas callbacks — E1's receipt rows read the same `view.derived` events.

## Design

### Event payloads (SDR §5 verbatim; every payload carries `v: 1`)

```text
view.derived     {surface_id, tier: raw|generic|shaped, basis: schema|registry|generated, spec_ref?, gen: {model, ms}?}
view.preference  {surface_id, keep: generic|shaped, actor: user}
usage.recorded   {purpose: view_shaping, model, tokens_in, tokens_out, surface_id?}   ← via A2 meter
```

Basis mapping (normative): `builtin.lookup` hit **or** spec-store hit ⇒ `basis: "registry"`, `tier: "shaped"`,
`spec_ref` set. No spec, structured Mapping payload ⇒ `tier: "generic"`, `basis: "schema"`. Spec authored by the
generation subsystem (this run or a regenerate) ⇒ `tier: "shaped"`, `basis: "generated"`, `gen: {model, ms}` set.
Non-mapping payload ⇒ `tier: "raw"`, `basis: "schema"` (renders B2's raw fallback; see the surface-creation Open
question — A3 emits no `surface.created` for non-mapping payloads today). `spec_ref` uses the same ref
convention A3 uses for `payload_ref` (VERIFY AT IMPL). Ledger ids shown in toast/footer use A1's `r<short>·<seq>`
formatter over `run_id` + `sequence_no`.

### Event-type registration + projection (extends A3 — do this or the events don't survive replay)

A3 registered only four wire event types + their projector allow-lists. B3 adds a fifth and widens one — without
these, the "Never derive activity types from event-name prefixes" rule means the events project to nothing and the
reload DoD cannot hold:

- **`view.preference` is a new wire event.** Add `RuntimeApiEventType.VIEW_PREFERENCE = "view.preference"`
  (`services/ai-backend/src/runtime_api/schemas/common.py`, value from the A1 constant) plus a strict
  `_view_preference_payload` allow-list (`{v, surface_id, keep, actor}`) wired into `payload_for_event`, with an
  `activity_kind_for` branch → `RuntimeActivityKind.EVENT` (`.../schemas/events.py`) — mirror A3 D5 exactly.
- **Widen A3's `_view_derived_payload` allow-list.** A3's allow-list admits only its D1 keys (`gen.model`, not
  `gen.ms`). B3 populates `gen.ms` and `spec_ref`, so extend the nested `gen` allow-list to admit `ms` (int);
  otherwise the projector silently drops it and B3's own payload spec (`gen: {model, ms}?`) is not honored on the wire.
- **Fold `view.preference` into A3's SurfaceStore (server half of the reload DoD).** A3's
  `SurfaceStoreProjection.fold` (`agent_runtime/surfaces_v2/projection.py`) folds only `surface.created` +
  `view.derived`. Add a `view.preference` branch that sets a new `preference: str | None` (`"generic"|"shaped"`) on
  `SurfaceViewState`, exposed on `SurfaceSnapshot` and the api-types `Surface.view` mirror (A1 already declares
  `Surface.view.preference?: ViewKeep`; the py side lacks it). This is what makes
  `test_replay_after_preference_shows_generic` provable from a rebuilt store.

### ViewDeriver (new; ai-backend)

New file `services/ai-backend/src/agent_runtime/surfaces_v2/view_deriver.py` (the v2 package A1/A3 created — a
sibling of, **not** inside, `capabilities/surfaces/`):

```python
# ViewTier (raw|generic|shaped) and ViewBasis (schema|registry|generated) are the A1 StrEnums —
# import them from agent_runtime.surfaces_v2.ledger_models; do NOT redefine (one source of truth).
from agent_runtime.surfaces_v2.ledger_models import ViewTier, ViewBasis, ViewKeep

class ViewGenInfo(RuntimeContract):     # nested gen block of view.derived
    model: str
    ms: int

class ViewDerivation(RuntimeContract):  # returned to callers + shipped as event payload
    surface_id: str
    tier: ViewTier
    basis: ViewBasis
    spec_ref: str | None = None
    gen: ViewGenInfo | None = None      # {model: str, ms: int}

class ViewDeriverError(Exception): ...  # typed, safe public message
class RegenerateLimitError(ViewDeriverError): ...

@dataclass(frozen=True)
class ViewDeriver:
    store: SurfaceSpecStorePort                    # build_surface_spec_store(...)
    emit: EmitFn                                   # A3's ledger append closure
    generator: SurfaceSpecGenerator | None         # None ⇒ shaping unavailable
    scheduler: SurfaceGenerationScheduler | None   # A3's run-scoped async path (build_surface_generation_scheduler)

    def derive(self, *, surface_id, server, tool, payload, tool_descriptor=None) -> ViewDerivation
    async def regenerate(self, *, surface_id, server, tool, payload, regen_count) -> ViewDerivation
```

`derive` (called on the A3 emission path right after `surface.created`): run the existing ladder —
`builtin.lookup(server, tool)` then `store.get(server=, tool=)`. Hit ⇒ emit `view.derived {tier: shaped, basis:
registry, spec_ref}`. Miss on a Mapping payload ⇒ emit `{tier: generic, basis: schema}` **immediately** (never
wait on shaping — NFR-1), then `scheduler.maybe_schedule(...)` exactly as today. Non-mapping payload ⇒ `{tier:
raw, basis: schema}` (see the raw-surface Open question — no `surface.created` exists for these today). Async-success
hook: A3's Hook 2 (`RuntimeRunHandler._build_surface_generation_scheduler`,
`services/ai-backend/src/runtime_worker/handlers/run.py:1570`) **already** appends
`view.derived {tier: shaped, basis: generated, gen: {model}}` beside the v1 `surface_spec_generated` on every
spec-generated upgrade. B3 does **not** add a second emission — it **extends A3's existing Hook 2 view.derived** to
also carry `spec_ref` and `gen.ms` (A3 emitted `gen.model` only). v1 `surface_spec_generated` keeps emitting,
unaltered, through the compat window (SDR §11); `surface_uri` → `surface_id` maps via the SurfaceStore projection
(the payload's `surface_uri` is already the `surface_id` per A3 D1).

`regenerate` (user-invited, out-of-run — may run after run completion, in the runtime_api process): a pure function
of the **stored** payload:

1. Load the surface record + payload via `payload_ref`. Missing ⇒ `ViewDeriverError("surface_not_found")` → 404.
2. `regen_count >= _Limits.MAX_REGEN_PER_SURFACE` (new constant, `3`) ⇒ `RegenerateLimitError` → 409
   `regenerate_limit_reached`. `regen_count` folds from the ledger (prior non-first `view.derived` with
   `basis != "registry"`); no mutable state.
3. Re-run the ladder. Hit ⇒ emit shaped/registry (covers "a curated/team spec landed since first render").
4. Miss + `generator` present ⇒ call `SurfaceSpecGenerator.generate` **directly** (the scheduler's per-run `seen`
   dedup and stored/failed skip would wrongly suppress a user-requested retry), through the A2 meter
   (`purpose="view_shaping"`, `surface_id`). Success ⇒ `store.put(key, stored)` (overwrites the cached spec — the
   repair) + emit `{tier: shaped, basis: generated, gen}`. `GenFailure` ⇒ emit `{tier: generic, basis: schema}`
   (honest re-affirmation; never fabricate).
5. Miss + no generator ⇒ emit `{tier: generic, basis: schema}`.
6. **Never** touches the MCP client, connector sessions, or `CallMcpTool` — asserted adversarially in tests.

### Shaping-on default for desktop (SDR §13 open decision #1 — this PR implements it)

New file `services/ai-backend/src/agent_runtime/surfaces_v2/shaping_policy.py`:

```python
class ShapingModelResolver:
    @classmethod
    def resolve(cls, *, environ: Mapping[str, str], run_provider: str | None) -> str | None:
        # 1. SURFACE_SPEC_MODEL set (non-empty) → return it verbatim (today's behavior).
        # 2. SURFACES_V2 off → None  (flag off ⇒ byte-identical: generation stays opt-in).
        # 3. SURFACES_V2 on: cheapest shaping model for run_provider from
        #    _ShapingDefaults (constant map, per provider); run_provider None
        #    (no BYOK key configured) → None — shaping off, generic/raw only.
```

`_ShapingDefaults` is a constants class (per ai-backend CLAUDE.md, no module-level helpers). VERIFY AT IMPL: the
cheapest per-provider model ids against the model catalog ai-backend already ships (grep `services/ai-backend/src`
for the `list_models` source) — do not invent ids. Hook: `build_surface_generation_scheduler` (`generator.py`)
consults `ShapingModelResolver.resolve` instead of the bare `SURFACE_SPEC_MODEL` check. VERIFY AT IMPL: where
`RuntimeRunHandler._build_surface_generation_scheduler` (run.py:1570) can read the run-start policies fetch
(`/internal/v1/policies/runtime` → `provider_keys`) to pass `run_provider`.

### Metering (DoD: shaping metered, purpose `view_shaping`)

Every shaping completion — scheduler async path and `regenerate` direct path — goes through PRD-A2's
`MeteredModelInvocation` seam wrapping the `SpecCompletionPort` call: `purpose=Purpose.VIEW_SHAPING`, `surface_id`
set, org/user/conversation/run from the run envelope. Retries record **per attempt** (A2 rule). The async scheduler
path is **already** metered by A2 (D5b, via `build_surface_generation_scheduler(usage_meter=...)`); B3 adds only the
`regenerate` direct path's `MeteredModelInvocation(purpose=Purpose.VIEW_SHAPING)`, passing `surface_id`. No other new
model call sites exist in this PR.

### HTTP endpoints

runtime_api (`services/ai-backend/src/runtime_api/http/routes.py`, registered in
`RuntimeApiRouter.create_router()` under the `/v1/agent` prefix, which already carries
`RequireScopes(RUNTIME_USE)`; add `Keys.RouteName.REGENERATE_SURFACE_VIEW` / `.SET_SURFACE_VIEW_PREFERENCE` to
`services/ai-backend/src/agent_runtime/api/constants.py`):

| Route                                                  | Handler (new)                                  | Body                          | 200 response                           |
| ------------------------------------------------------ | ---------------------------------------------- | ----------------------------- | -------------------------------------- |
| `POST /v1/agent/surfaces/{surface_id}/regenerate`      | `RuntimeApiRoutes.regenerate_surface_view`     | `{}`                          | `{surface_id, tier, basis, ledger_id}` |
| `POST /v1/agent/surfaces/{surface_id}/view-preference` | `RuntimeApiRoutes.set_surface_view_preference` | `{keep: "generic"\|"shaped"}` | `{surface_id, keep, ledger_id}`        |

Request/response models (`SurfaceViewPreferenceRequest`, `SurfaceViewActionResponse`,
`SurfaceViewPreferenceResponse`) live beside A3's surface schemas in `runtime_api/schemas/` (VERIFY AT IMPL: A3's
schema module). Tenancy = resolve `surface_id` → owning run → same org/user gate as `GET /v1/agent/runs/{run_id}`
(VERIFY AT IMPL: reuse A3's `surface_id` resolver). `SURFACES_V2` off ⇒ mirror A3's flag-off behavior.
`view-preference` targeting a tier with no derivation ⇒ 409 `view_tier_unavailable`. Preference handler appends
`view.preference {surface_id, keep, actor: "user"}` via the API-side event producer (the append path approval
decisions use — `agent_runtime/api/events.py`; VERIFY AT IMPL which `StreamEventSource` member user-initiated API
appends use). Regenerate handler builds a `ViewDeriver` on demand:
`build_surface_spec_store(environ=os.environ, org_id=..., user_id=...)` + generator from `ShapingModelResolver`;
no run-scoped scheduler — a single awaited generation attempt is fine because the user explicitly asked. The
handler computes `regen_count` by folding this surface's prior `view.derived` events (`basis != "registry"`,
excluding the first derivation) from the **same** replayed run ledger it loads for tenancy — so it needs the owning
`run_id` (see the surface→run resolution Open question). Response `ledger_id` = `LedgerIdCodec.format(run_id,
sequence_no)` (A1 py codec) over the appended event's `sequence_no`.

facade (`services/backend-facade/src/backend_facade/app.py`): two passthroughs on the existing pattern —
`FacadeAuthenticator.authenticate_request(request)` then `forward_json(app, "POST",
f"/v1/agent/surfaces/{surface_id}/regenerate", target="ai_backend", json=..., identity=identity)`; same for
`view-preference`.

### Client (chat-surface; 🎨)

- **Fold** — extend B1's `ledgerProjection.ts` fold: widen `LedgerSurface` with per-surface `viewState: {tier,
basis, specRef?, keep?, shapedAvailable: boolean, regenCount}` (B1 today carries only `viewTier`). Effective-tier
  rule (identical py + ts, pinned by the golden-fixture parity test):

  ```
  effective = keep ?? tier-of-latest-view.derived
  keep = "shaped" folds only when shapedAvailable; keep = "generic" always folds
  ```

  A later shaped `view.derived` on a surface with `keep: "generic"` sets `shapedAvailable` (toggle enabled) but
  does not change the rendered tier and fires no toast. Upgrades merge in place: same tab identity (`surface_id`),
  no remount flicker (the same in-place-merge discipline the v1 projector uses in `eventProjector.ts`).

- **`ViewUpgradeToast`** (new, `packages/chat-surface/src/thread-canvas/ViewUpgradeToast.tsx`) — non-modal, kit
  recipes only (`.ui-card`, `.ui-caption`, `.ui-button--ghost`; see `packages/design-system/SKILL.md`): "View
  upgraded · `r<short>·<seq>`" + **Keep generic** action → `onSetViewPreference(surfaceId, "generic")`. Shown when
  an upgrade flips effective tier generic→shaped with no preference set; dismisses on timeout/action.
- **`ViewTierToggle`** (new, same directory) — the persistent way back (FR-D2): in the surface chrome beside B2's
  provenance footer; "Generic ⇄ Shaped" (shaped side disabled until `shapedAvailable`); fires `onSetViewPreference`.
  The **Regenerate** affordance ("Looks wrong? Regenerate") lives in the same cluster →
  `onRegenerateView(surfaceId)`; disabled at `regenCount >= 3` (client mirror; server cap is authoritative).
- **Generic tier with a spec present**: the v2 surface mount passes the spec through only when effective tier is
  `shaped`; `generic` renders the spec-less path (existing `GenericStructuredDiff` from
  `packages/chat-surface/src/surfaces/index.ts`); `raw` renders B2's raw fallback. VERIFY AT IMPL: B1's mount (if
  it reuses `packages/chat-surface/src/thread-canvas/TcSurfaceMount.tsx`, add `forcedTier?: "generic" | "shaped"`).
- **Wiring**: `RunDestination` (`packages/chat-surface/src/destinations/run/RunDestination.tsx`) implements both
  callbacks via `useTransport()` → `transport.request({ method: "POST", path: ... })` (same pattern as the
  approval-decision POST at ~line 827). No bare `fetch`/`window` (eslint-enforced). No new SSE subscription —
  resulting events arrive on the one run stream and fold in (FR-3.3 one-projector invariant).

### Error behavior

- Deriver errors are typed with safe public messages — no payload contents in HTTP responses or logs beyond the
  existing `[surfaces.specgen]` metering lines.
- Generation failure on regenerate is a **success response** carrying `{tier: "generic", basis: "schema"}` —
  honesty, not an error. Emit failure on the async upgrade path: warning only; the spec store remains truth (same
  rule as v1 `_emit_generated`).

## Implementation plan

1. Contracts check: confirm A1 shipped `view.derived` / `view.preference` payload types + constants; add any
   missing field in `packages/api-types/src/index.ts` (v2 block), the ai-backend pydantic mirror, and A1's
   constants module in `packages/service-contracts/src/copilot_service_contracts/`.
2. Create `services/ai-backend/src/agent_runtime/surfaces_v2/view_deriver.py` (`ViewGenInfo`, `ViewDerivation`,
   `ViewDeriver`, typed errors, `_Limits`; import `ViewTier`/`ViewBasis`/`ViewKeep` from `surfaces_v2/ledger_models`)
   and `services/ai-backend/src/agent_runtime/surfaces_v2/shaping_policy.py` (`ShapingModelResolver`,
   `_ShapingDefaults`).
3. Modify `services/ai-backend/src/agent_runtime/capabilities/surfaces/generator.py`
   (`build_surface_generation_scheduler` consults the resolver) and
   `services/ai-backend/src/runtime_worker/handlers/run.py` (pass `run_provider`; **extend** A3's existing Hook 2
   `view.derived {basis: generated}` emission to add `spec_ref` + `gen.ms` — do not add a second emission).
4. Wire `ViewDeriver.derive` into A3's post-`surface.created` emission path (A3's `WorkLedgerEmitter` — modify in
   place, one projector path); route the regenerate completion through the A2 meter (the async path is already
   metered by A2).
5. Event-type registration + routes + schemas:
   - `services/ai-backend/src/runtime_api/schemas/common.py` — add `RuntimeApiEventType.VIEW_PREFERENCE`.
   - `services/ai-backend/src/runtime_api/schemas/events.py` — add `_view_preference_payload` allow-list +
     `payload_for_event`/`activity_kind_for` branch; widen `_view_derived_payload`'s nested `gen` allow-list with `ms`.
   - `services/ai-backend/src/agent_runtime/surfaces_v2/projection.py` — fold `view.preference` into the
     SurfaceStore; add `preference` to `SurfaceViewState`/`SurfaceSnapshot` + the api-types `Surface.view` mirror.
   - `services/ai-backend/src/runtime_api/http/routes.py`, `services/ai-backend/src/agent_runtime/api/constants.py`,
     A3's schema module under `services/ai-backend/src/runtime_api/schemas/`; facade passthroughs in
     `services/backend-facade/src/backend_facade/app.py`.
6. TS: extend B1's v2 fold; create `packages/chat-surface/src/thread-canvas/ViewUpgradeToast.tsx` +
   `.../ViewTierToggle.tsx`; export via `packages/chat-surface/src/index.ts` (new delimited barrel block); add
   `forcedTier` to the v2 mount; wire callbacks in `packages/chat-surface/src/destinations/run/RunDestination.tsx`.
7. Extend the shared golden-event fixture (A1) with a view-lifecycle scenario consumed by both folds' parity tests.
8. Tests (below), design-parity run, live smoke; update `../02-sdr.md` §7 S5 if the implementation diverges.

## Test plan

**ai-backend** — `services/ai-backend/tests/unit/agent_runtime/surfaces_v2/test_view_deriver.py` (mixins per the
tests CLAUDE.md; fakes only — fake store, fake `SpecCompletionPort`, collector emit closure, injected `environ`):

- `test_registry_miss_emits_generic_immediately_then_schedules`;
  `test_builtin_hit_emits_shaped_registry_with_spec_ref`; `test_store_hit_emits_shaped_registry`;
  `test_non_mapping_payload_emits_raw_schema`; `test_generation_success_emits_shaped_generated_with_gen_info`
- `test_regenerate_uses_stored_payload_only` — **adversarial**: fake MCP client/connector seam raises on any
  invocation; zero connector traffic on every regenerate branch (DoD)
- `test_regenerate_bypasses_scheduler_dedup_and_overwrites_cached_spec`;
  `test_regenerate_generation_failure_stays_generic` (honest, non-error);
  `test_regenerate_cap_raises_typed_error_at_limit` (`RegenerateLimitError` + safe message)
- `test_shaping_metered_per_attempt_purpose_view_shaping` — fake completion fails once then succeeds ⇒ two usage
  records, both `purpose="view_shaping"`, `surface_id` set (DoD)
- `test_flag_off_emits_no_v2_events` (byte-identical guard, same snapshot approach as A3)

`services/ai-backend/tests/unit/agent_runtime/surfaces_v2/test_shaping_policy.py`: `test_explicit_model_env_wins`,
`test_flag_off_returns_none_when_env_empty` (pins today), `test_flag_on_resolves_cheapest_for_provider`,
`test_no_provider_key_disables_shaping`.

`services/ai-backend/tests/unit/runtime_api/test_surface_view_routes.py`:
`test_regenerate_returns_derivation_and_ledger_id`, `test_preference_appends_ledger_event_actor_user`,
`test_preference_unavailable_tier_409`, `test_cross_tenant_surface_404`, `test_flag_off_matches_a3_gating`,
`test_replay_after_preference_shows_generic` (preference survives store rebuild — DoD reload, server half).

**facade** — `services/backend-facade/tests/test_surface_view_proxy.py`: both routes forward with identity
scoping; error passthrough.

**chat-surface** — extend B1's fold test (golden fixture) +
`packages/chat-surface/src/thread-canvas/ViewUpgradeToast.test.tsx`, `ViewTierToggle.test.tsx`, and
`RunDestination.test.tsx` additions: generic→shaped sequence flips effective tier; toast condition; `keep:
"generic"` pins tier across later shaped events; a fresh `project()` over the replayed event array reproduces the
pinned tier (DoD reload, client half); tab identity stable across upgrade (no remount); py fold × golden events
=== ts fold (extends A3/B1 parity test); Regenerate/Keep-generic click issues exactly one `transport.request`
POST; no second SSE subscription.

**Live smoke (desktop, real stack)** — record the transcript in the PR description:

1. `make dev` (or staged desktop per `apps/desktop/README.md`). ai-backend env: `SURFACES_V2=true`,
   `RUNTIME_SURFACE_EMISSION` unset, `SURFACE_SPEC_MODEL` **unset** (exercises the resolver), one BYOK key set.
2. `export TOKEN=$(make dev-bearer)`; start a run whose tool has **no builtin spec** (any MCP tool outside
   `services/ai-backend/src/agent_runtime/capabilities/surfaces/builtin_specs/`).
3. Generic view renders immediately; "View upgraded" toast appears when shaping lands; `curl -H "Authorization:
Bearer $TOKEN" http://127.0.0.1:8200/v1/agent/runs/<run_id>/surfaces` shows `tier: shaped, basis: generated`.
4. Click **Keep generic** → view reverts; reload the app → still generic (replay).
5. Toggle back to shaped; click **Regenerate** → new `view.derived` event; the events replay endpoint shows no new
   `tool_call`/`tool_result` events (zero connector traffic).
6. A2's usage query shows `purpose=view_shaping` rows for this run.
7. `SURFACES_V2` unset: rerun a normal run; event stream matches the pre-B3 snapshot.

## Definition of done

Binding items from `../03-prds.md` (PRD-B3), each with its proving artifact:

- [ ] **Registry-miss tool renders generic immediately; upgrade merges in-place on a live run** — live-smoke steps
      2–3 recorded; `test_registry_miss_emits_generic_immediately_then_schedules` + fold merge test green.
- [ ] **"Keep generic" survives reload (preference is a ledger event, replay honors it)** — live-smoke step 4;
      `test_replay_after_preference_shows_generic` + ts replay test.
- [ ] **Regenerate produces a view without any MCP call (asserted: zero connector traffic)** —
      `test_regenerate_uses_stored_payload_only` + live-smoke step 5 event check.
- [ ] **Shaping calls metered (purpose: view_shaping)** — `test_shaping_metered_per_attempt_purpose_view_shaping` +
      live-smoke step 6 usage rows.

Standard DoD (every PRD):

- [ ] Unit tests in the owning component's venv/workspace pass; typecheck + build green (Implementer brief cmds).
- [ ] Flags off ⇒ byte-identical behavior — `test_flag_off_emits_no_v2_events`,
      `test_flag_off_returns_none_when_env_empty`, live-smoke step 7.
- [ ] No service-boundary violations (apps→facade only; no cross-`src/` imports) — the only new HTTP surface is
      behind the facade; TS callbacks ride the Transport port.
- [ ] New LLM call sites go through the UsageMeter seam — both shaping paths wrap `SpecCompletionPort` via A2; A2's
      grep-gate stays green.
- [ ] Docs: `../02-sdr.md` §7 S5 updated if implementation diverges.

UI DoD (🎨):

- [ ] Built from design-system/chat-surface kit components — toast + toggle use kit recipes only; no raw
      `font-size`/`letter-spacing` (`packages/design-system/SKILL.md`).
- [ ] `tools/design-parity/` run against the staged v2 mock region (toast + toggle + generic/shaped states): **0
      HIGH drift**; report committed under `tools/design-parity/surfaces/<name>/out/report.md`.
- [ ] Live desktop smoke of the flow on the real stack (script above), not just tests.

## Out of scope

- "Suggest a shape" / user-invited higher-effort shaping, `POST /v1/agent/surfaces/{surface_id}/shape-request`,
  `shape.requested` event (PRD-B4).
- Gates, classification, staged writes, receipt (Waves C/D/E).
- Any change to v1 emission (`CallMcpTool._attach_surface`, `DraftSurfaceProjector`, the `surface_spec_generated`
  v1 payload) — compat window per SDR §11; removal is PRD-E3.
- Pricing/dollars, usage UI (FR-G4), facade `/v1/usage/*` exposure (PRD-E3).
- Failure-path visual polish (Phase-2 designer track; the honest states must exist and be correct).

## Guardrails

- **Service boundaries are hard**: apps call the facade only (`:8200`); facade forwards over HTTP (`forward_json`)
  and never imports ai-backend modules; ai-backend never gains product persistence or app-specific presentation.
  No sibling `src/` imports, no shared `.venv`s, no new `PYTHONPATH` entries.
- **Flag-off byte-identical**: `SURFACES_V2` unset ⇒ no v2 event appended, route behavior matches A3's gating, and
  shaping stays governed by `SURFACE_SPEC_MODEL` exactly as today (empty ⇒ off). Snapshot tests enforce this.
- **ai-backend rules** (`services/ai-backend/CLAUDE.md`): pydantic at every boundary (no `dict[str, Any]` domain
  state); enums/literals for known domains; typed domain errors with safe public messages; repeated keys/messages
  in nested `Keys`/message classes; helper behavior inside classes; tool payloads and model output are untrusted —
  the redaction/lint/injection kill-switch stays in the path for every shaping attempt, including regenerate.
- **Test rules** (`services/ai-backend/tests/CLAUDE.md`): fakes only — never network, live LLMs, or real
  credentials; assert typed error classes and safe messages; shared fixtures in mixins.
- **chat-surface rules** (`packages/chat-surface/CLAUDE.md`): substrate-agnostic — no
  `window`/`fetch`/`localStorage` (eslint-enforced); all IO through the Transport port; exports only via the
  `src/index.ts` barrel in a delimited block; presentational components take props/callbacks; ONE event projection
  — new state folds from the same `session.events` array, never a second SSE subscription.
- **Never derive activity types from event-name prefixes** (streaming-model rule); v2 events carry typed payloads.

## Open questions

Genuine design choices this PRD cannot settle from the SDR / sibling PRDs alone. Each blocks a step that would
otherwise require guessing; resolve before or during implementation.

1. **How do the surface-keyed endpoints resolve the owning run?** The exposed contract keys both endpoints on
   `surface_id` only (`POST /v1/agent/surfaces/{surface_id}/regenerate` and `…/view-preference`), and the design
   says to "reuse A3's `surface_id` resolver". **A3 ships no such resolver** — A3's `surface_id` is the v1
   `SurfaceEnvelope.surface_uri` (`<archetype>://<server-slug>/<tool>/<id>`), which carries **no run component**, and
   every A3 endpoint/projection is keyed on a _known_ `run_id`. So there is no way to go `surface_id → run_id`, which
   the handlers need for (a) the org/user tenancy gate, (b) loading the stored payload via `payload_ref`, and (c)
   folding `regen_count`. Recommended default (keeps the exposed path stable, so B4 is unaffected): require the
   owning `run_id` as a query param on both endpoints — B1's canvas always holds it (it fetched
   `GET /v1/agent/runs/{run_id}/surfaces`) and passes it through the Transport callback. Decide this vs. building a
   persistent `surface_id → run_id` index (heavier; new persistence not in any Wave-A PRD).

2. **Who emits `surface.created {kind: raw}` for non-mapping payloads?** B3's `derive` has a `tier: "raw"` branch and
   B2 renders a raw fallback view, but A3 explicitly emits **no** `surface.created`/`view.derived` for non-mapping
   tool output ("v1 creates no envelope"). With no `surface.created`, B3's `view.derived {tier: raw}` has no surface
   to attach to and is dropped by both folds (their "unknown `surface_id` ⇒ drop" rule), so FR-D3's honest raw
   fallback never materializes on the v2 canvas. Options: (a) B3 extends the A3 emission path to create a
   `kind: raw` surface for non-mapping read payloads (widens A3's frozen behavior — additive, but a behavior change
   the compat window must cover); (b) B2 owns raw-surface emission; (c) a separate follow-up. B3's DoD does not
   exercise the raw path (its live smoke uses a mapping payload), so this is not a B3 blocker, but the `tier: "raw"`
   branch is dead until it is resolved. Pick an owner so the branch is either wired or explicitly deferred.
