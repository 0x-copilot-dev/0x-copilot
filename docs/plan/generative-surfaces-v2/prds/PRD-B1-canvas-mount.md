# PRD-B1 — Canvas mount + tabs (both hosts) 🎨

Make the Run cockpit's ThreadCanvas render **Generative Surfaces v2** surfaces as named
tabs, fed by a **client-side TypeScript projection of Work Ledger events** — the same
events the Python SurfaceStore fold (PRD-A3) consumes — behind an opt-in client feature
flag in both hosts (web + desktop). Flag off ⇒ the cockpit is byte-identical to today.
First user-visible slice of the v2 read path (FR-A1 surface canvas, FR-A2 named
surfaces per run in a tab strip).

## Implementer brief

You are working in a **fresh git worktree branched off `main`** of the 0x-copilot
monorepo. Run `make setup` once if `node_modules`/`.venv`s are missing. All paths are
repo-root-relative. Test commands for every component you may touch:

```bash
# for W in each touched workspace — chat-surface, api-types, desktop, frontend:
npm run test --workspace @0x-copilot/<W>        # vitest run
npm run typecheck --workspace @0x-copilot/<W>
npm run lint --workspace @0x-copilot/chat-surface   # substrate-purity eslint gate
npm run build --workspace @0x-copilot/frontend
# design-parity live-render harness (repo root, own vitest root):
node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs
# ai-backend is READ-ONLY for this PR; to confirm v2 event/fold shapes if needed, run the
# v2 ledger parity test A1/A3 add (VERIFY AT IMPL: exact path — grep the surfaces test dir
# for the golden-ledger fold test; do NOT rely on the pre-existing SurfaceSpec
# test_schema_parity.py, which pins the *v1* spec shape, not the v2 ledger vocabulary):
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/surfaces/ -k ledger
```

Read these files first:

1. `docs/plan/generative-surfaces-v2/02-sdr.md` — §5 is the authoritative event vocabulary; §11 the flag/compat rules.
2. `docs/plan/generative-surfaces-v2/03-prds.md` — PRD-B1 scope + DoD (binding minimums).
3. `packages/chat-surface/src/thread-canvas/eventProjector.ts` — the ONE-projector pattern (`project`, `projectSurfaceTabs`, `SurfaceTab`, `SurfacePayload`) your v2 fold must be a peer of, not a rival to.
4. `packages/chat-surface/src/destinations/run/RunDestination.tsx` — lines ~905–985 (tab-strip derivation: `projectSurfaceTabs`, `visibleSurfaceTabs`, `activeUri`, pin/close state) and ~1215 (`<ThreadCanvas tabs=… activeUri=…>`); the flag branches here.
5. `packages/chat-surface/src/thread-canvas/ThreadCanvas.tsx` — `ThreadCanvasProps` (~line 87) and the `surfaceState` memo (~line 272, `projection.surface.payloadFor(activeUri)`) where the new `resolveSurfaceState` seam lands.
6. `packages/chat-surface/src/thread-canvas/TcTabs.tsx` (`TcTab {uri, title, pinned?}`, reused unchanged) and `TcSurfaceMount.tsx` (adapter resolution by URI scheme, tier-3 fallback `TIER3_URI`, 100 ms render budget).
7. `apps/frontend/src/app/featureFlags.ts` — the sanctioned web flag pattern (`isRunCockpitWebEnabled`, env + localStorage, fail-safe polarity) you copy.
8. `apps/frontend/src/features/run/RunRoute.tsx` (web binder, mounts `RunDestination` ~line 370) and `apps/desktop/renderer/destinationBinders.tsx` (`RunBinder` function ~line 619; its `<RunDestination>` mount is ~line 832 — pass the prop there).
9. `packages/api-types/src/adapterAllowlist.ts` — precedent for importing a JSON file from `packages/service-contracts` by relative path into TS.
10. `packages/chat-surface/src/destinations/run/useRunSources.ts` — the **direct precedent for `useSurfacesV2`**: a Transport-fed GET hydration hook in the same directory that calls `transport.request({ method: "GET", path: … })` (via `useTransport()` internally) and returns a projected result. Copy its request/coalesce/error shape. The `Transport` type it uses is from `@0x-copilot/chat-transport` (`packages/chat-transport/src/transport.ts` — `request<TRes>(req: TypedRequest): Promise<TRes>`).
11. `packages/chat-surface/CLAUDE.md` — the substrate-agnostic hard rule (no `window`/`localStorage`/`fetch` in the package; ports only).

## Context

Generative Surfaces v2 re-founds the product's surface layer on an explicit, typed
**Work Ledger**: append-only per-run events (SDR §5) persisted on the existing runtime
event log, with everything user-visible — canvas, tabs, receipts, sources, usage —
defined as **projections** of that ledger (../02-sdr.md §2–§3). The requirements
contract is ../01-problem-and-requirements.md: FR-A1 (canvas beside the conversation),
FR-A2 (named surfaces in a per-run tab strip), FR-E1 (one ledger, stable ids
`r<short>·<seq>`).

This PR opens Wave B (Studio read path, ../03-prds.md). Wave A already landed: PRD-A1
gave both languages the event contracts + golden fixtures; PRD-A3 made the runtime emit
`surface.created` / `view.derived` (and peers) behind the runtime flag `SURFACES_V2`,
with a Python SurfaceStore fold and a `GET /v1/agent/runs/{id}/surfaces` endpoint. B1
gives those events a client: a TS fold with **fixture-proven parity to the Python
fold**, wired into the already-mounted Run cockpit in both hosts. Per SDR §11 the v2
canvas reads **only ledger events**; v1 `result["surface"]` keeps streaming for the
flag-off path until E-wave retirement. Later Wave-B PRDs stack on this mount (B2
footers/loading/raw, B3 view lifecycle, B4 suggest-a-shape); B1 ships tabs + rendered
content only.

## Interfaces consumed / exposed

**Consumed (must exist on `main` from Wave A — verify before starting):**

- **PRD-A1**, `packages/api-types`: `SurfaceEventV2` payload types + event-type
  constants for `surface.created`/`view.derived`; the ledger-id formatter producing
  `r<short>·<seq>` (e.g. `r7f3·042`). `VERIFY AT IMPL:` exact exported names (e.g.
  `SurfaceCreatedPayload`, `formatLedgerId(runId, sequenceNo)`) — grep
  `packages/api-types/src` for `surface.created` and reuse verbatim; do NOT redefine.
- **PRD-A1**, `packages/service-contracts`: the golden ledger-events fixture JSON.
  `VERIFY AT IMPL:` exact path — expected
  `packages/service-contracts/src/copilot_service_contracts/surfaces_v2/golden_ledger_events.json`
  (or similar); import by relative path like `adapterAllowlist.ts` does.
- **PRD-A3**: (a) runtime emission of v2 events on the ordinary run event stream
  (`RuntimeEventEnvelope` rows over the existing SSE/replay path — no new subscription);
  (b) the expected-fold snapshot fixture its SurfaceStore golden test checks against
  (`VERIFY AT IMPL:` path — expected sibling of the events fixture); (c)
  `GET /v1/agent/runs/{run_id}/surfaces` via facade returning the SurfaceStore snapshot
  with per-surface materialized content (`VERIFY AT IMPL:` exact response field names
  from A3's api-types mirror).
- Existing: `useRunSession` (`session.events`), `Transport` port (`transport.request`),
  `TcTab`, `TcSurfaceMount` tier-3 fallback, `RunDestinationProps`.

**Exposed (later PRDs depend on these — keep names stable):**

- `projectLedger`, `LedgerProjection`, `LedgerSurface`, `tabUriForSurface`,
  `surfaceIdForTabUri`, `ledgerTabsAsSurfaceTabs`, `toParitySnapshot` from
  `packages/chat-surface/src/thread-canvas/ledgerProjection.ts` (B2 reads per-surface
  provenance from this fold; B3 extends it with view state).
- `useSurfacesV2` hydration hook (B3's Regenerate re-hydrates through it);
  `resolveSurfaceState` prop on `ThreadCanvasProps` (B2/B3 reuse the seam).
- `surfacesV2` prop on `RunDestinationProps`; flag helpers
  `isSurfacesV2CanvasEnabled()` (web) / `isSurfacesV2Enabled()` (desktop) — C2/D/E UI
  waves gate on the same helpers.

## Design

### 1. Events consumed (SDR §5 vocabulary, verbatim)

B1 folds exactly two event types; every other v2 event type in the stream is
**tolerated and ignored** (C/D/E waves add consumers, not projector rewrites):

```text
surface.created  {surface_id, kind: record|message|table|call|raw|receipt|gate,
                  source{connector,op}, title, payload_ref, v}
view.derived     {surface_id, tier: raw|generic|shaped,
                  basis: schema|registry|generated, spec_ref?, gen: {model, ms}?, v}
```

Wire form: each is a `RuntimeEventEnvelope` (existing type in
`packages/api-types/src/index.ts`) whose `event_type` is the A1 constant and whose
`payload` is the object above. `sequence_no` is the envelope's — the ledger reuses the
run event log's monotonic sequence (SDR §5: ledger id = presentation over
`run_id` + `sequence_no`).

### 2. TS fold — `packages/chat-surface/src/thread-canvas/ledgerProjection.ts` (NEW)

A pure selector over the SAME `session.events` array (FR-3.3 one-projector invariant —
peer of `projectSurfaceTabs`/`projectSubagents`, never a second SSE subscription):

```ts
import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";
import type { SurfaceTab } from "./eventProjector"; // {uri, archetype?, title?, lastSeq}
export type LedgerSurfaceKind =
  | "record"
  | "message"
  | "table"
  | "call"
  | "raw"
  | "receipt"
  | "gate";
export type LedgerViewTier = "raw" | "generic" | "shaped";

export interface LedgerSurface {
  readonly surfaceId: string;
  readonly kind: LedgerSurfaceKind;
  readonly title: string;
  readonly source?: { readonly connector: string; readonly op: string };
  readonly viewTier: LedgerViewTier | null; // null until first view.derived
  readonly createdSeq: number;
  readonly lastSeq: number; // highest seq touching this surface — tab order key
  readonly ledgerId: string; // "r<short>·<seq>" via A1 formatter, from createdSeq
}

export interface LedgerProjection {
  readonly surfaces: ReadonlyMap<string, LedgerSurface>; // keyed by surfaceId
  readonly tabs: readonly LedgerSurface[]; // ordered lastSeq desc
  readonly lastLedgerSeq: number; // highest v2-event seq seen; 0 = none
}

export function projectLedger(
  events: readonly RuntimeEventEnvelope[],
): LedgerProjection;

/** Mount/tab URI: "<scheme>://surfaces-v2/<surface_id>". kind→scheme is 1:1 except
 *  call→record (FR-A3). raw/receipt/gate keep their own scheme — no adapter matches
 *  ⇒ TcSurfaceMount's tier-3 fallback renders them honestly (D29), no mount branch. */
export function tabUriForSurface(surface: LedgerSurface): string;

/** Inverse of tabUriForSurface: recover the surface_id from a tab/mount URI (the
 *  path tail after the last "/"), scheme-independent so it works for every kind.
 *  Returns null for a URI that is not a surfaces-v2 URI (v1 URIs, garbage) — the
 *  host uses this to build `resolveSurfaceState` (§5). Exported so the host never
 *  hand-parses URIs. Round-trips: surfaceIdForTabUri(tabUriForSurface(s)) === s.surfaceId. */
export function surfaceIdForTabUri(uri: string): string | null;

/** Adapt to the existing strip shape so pin/close/activeUri logic is shared. */
export function ledgerTabsAsSurfaceTabs(
  p: LedgerProjection,
): readonly SurfaceTab[];

/** Language-neutral snapshot (snake_case keys, arrays sorted by surface_id) that
 *  MUST byte-equal PRD-A3's Python fold snapshot of the same events. */
export function toParitySnapshot(p: LedgerProjection): unknown;
```

Fold rules (mirror the Python fold; the parity test is the referee): process ascending
`sequence_no`; dedupe by `event_id`; a duplicate `surface_id` create updates title/kind
but keeps `createdSeq` (idempotent); `view.derived` for an unknown `surface_id` is
dropped; malformed payloads are skipped, never thrown; re-projection is deep-equal.

### 3. Content hydration — `packages/chat-surface/src/destinations/run/useSurfacesV2.ts` (NEW)

Ledger events carry `payload_ref`, not content (SDR §5). Content comes from PRD-A3's
SurfaceStore endpoint, fetched through the Transport port (substrate rule):

```ts
export interface UseSurfacesV2Result {
  /** Keyed by surfaceId; undefined = not yet hydrated (mount shows its existing
   *  skeleton/tier-3 state — B2 polishes). */
  readonly stateFor: (surfaceId: string) => SurfacePayload | undefined;
  readonly status: "idle" | "loading" | "ready" | "error";
}
export function useSurfacesV2(
  transport: Transport,
  runId: string | null, // `session.runId` from useRunSession is `string | null` (not branded RunId)
  lastLedgerSeq: number,
  enabled: boolean,
): UseSurfacesV2Result;
```

Behavior: when `enabled && runId !== null && lastLedgerSeq > 0`, issue
`transport.request({ method: "GET", path: "/v1/agent/runs/" + runId + "/surfaces" })`;
re-fetch when `lastLedgerSeq` advances, coalescing (one in flight; a bump during flight
schedules exactly one follow-up). Adapt each snapshot entry into the `SurfacePayload`
envelope shape the renderers already read (`{surface_uri, archetype, state: {spec?, data}}`
— `SurfaceEnvelope` in api-types). Errors fail soft: `status: "error"`, `stateFor` →
undefined, no retry storm (next seq advance retries), never a throw into React.

### 4. ThreadCanvas seam — one optional prop

Add to `ThreadCanvasProps` (`packages/chat-surface/src/thread-canvas/ThreadCanvas.tsx`):

```ts
/** SURFACES_V2 (PRD-B1): when provided, the surface column resolves the active
 *  surface's state through this instead of projection.surface.payloadFor(uri). */
readonly resolveSurfaceState?: (uri: string) => SurfacePayload | undefined;
```

Wire it inside the existing `surfaceState` memo (~line 272): if the prop is set and
`scrubbedSeq === null`, return `resolveSurfaceState(activeUri)`. Scrub behavior is
unchanged (v2 time-travel is out of scope; scrubbed ⇒ same `undefined` path as today).

### 5. RunDestination flag branch

Add to `RunDestinationProps` (`packages/chat-surface/src/destinations/run/RunDestination.tsx`):

```ts
readonly surfacesV2?: boolean; // Generative Surfaces v2 canvas (PRD-B1). Default false.
```

In the tab-strip block (~line 918): compute
`const ledger = useMemo(() => projectLedger(session.events), [session.events]);` and
branch `const surfaceTabList = surfacesV2 ? ledgerTabsAsSurfaceTabs(ledger) : projectSurfaceTabs(session.events);`
— everything downstream (`visibleSurfaceTabs`, `closedUris`, `pinnedUri`, `activeUri`
derivation, `MAX_SURFACE_TABS` cap, `TcTab` mapping) is shared unchanged. Mount
`useSurfacesV2(transport, session.runId, ledger.lastLedgerSeq, surfacesV2 === true)` —
`transport` is the in-scope `const transport = useTransport()` (RunDestination.tsx ~line
357), the run-id accessor is `session.runId` (`const session = useRunSession(…)` at ~line
424; `RunSession.runId` is `string | null`, `null` while no run is bound). Pass
`resolveSurfaceState` to `<ThreadCanvas>` **only when `surfacesV2`**, defined as
`(uri) => { const id = surfaceIdForTabUri(uri); return id ? hydration.stateFor(id) : undefined; }`
(the exported inverse; do not hand-parse the URI). When `surfacesV2` is false, no new prop
is passed and the hook is still called (Rules of Hooks) but inert via `enabled: false`.

Strictness (SDR §11): flag on ⇒ tabs come **only** from ledger events. If the runtime
flag `SURFACES_V2` is off server-side the v2 canvas is empty — the client flag is an
opt-in Wave-B dev/preview toggle, enabled together with the runtime flag; never mix v1
envelope surfaces into the v2 strip. `pendingDiff`/edit-overlay/approval flows keep
deriving from v1 projections and won't match v2 URIs — inert by construction (D-wave).

### 6. Host flags (client side of `SURFACES_V2`)

- Web — extend `apps/frontend/src/app/featureFlags.ts` (same pattern as
  `isRunCockpitWebEnabled`, **opposite polarity — default OFF, opt-in**):

```ts
export const SURFACES_V2_FLAG_KEY = "enterprise.flags.surfaces-v2";
/** ON iff import.meta.env.VITE_SURFACES_V2 === "true" OR
 *  localStorage[SURFACES_V2_FLAG_KEY] === "true". Storage errors ⇒ OFF. */
export function isSurfacesV2CanvasEnabled(): boolean;
```

`apps/frontend/src/features/run/RunRoute.tsx` passes
`surfacesV2={isSurfacesV2CanvasEnabled()}` at the `RunDestination` mount (~line 370).

- Desktop — NEW `apps/desktop/renderer/featureFlags.ts`: `isSurfacesV2Enabled()` reads
  `globalThis.localStorage.getItem("enterprise.flags.surfaces-v2") === "true"` in a
  try/catch (renderer is app code; the localStorage ban is chat-surface-only).
  `RunBinder` in `apps/desktop/renderer/destinationBinders.tsx` passes
  `surfacesV2={isSurfacesV2Enabled()}`.

### 7. Error behavior summary

Malformed v2 event ⇒ skipped, fold continues. Hydration HTTP error ⇒ tabs still render
(fold is event-only); surface column shows the not-yet-hydrated/tier-3 state. Unknown
`kind` ⇒ treat as `raw` (tier-3, honest). Adapter throw / render-budget overrun ⇒
existing TcSurfaceMount tier-3 fallback (no new code).

## Implementation plan

1. **Fold**: create `packages/chat-surface/src/thread-canvas/ledgerProjection.ts`;
   export from `packages/chat-surface/src/index.ts` (and `src/thread-canvas/index.ts`).
   Consume A1 constants + ledger-id formatter from `@0x-copilot/api-types`.
2. **Fold tests + parity**: `ledgerProjection.test.ts` +
   `ledgerProjection.parity.test.ts` (paths in Test plan) against the A1/A3 fixtures.
3. **Hydration hook**: create
   `packages/chat-surface/src/destinations/run/useSurfacesV2.ts` + tests.
4. **Canvas seam**: add `resolveSurfaceState` to
   `packages/chat-surface/src/thread-canvas/ThreadCanvas.tsx` (prop + memo branch).
5. **Cockpit branch**: add `surfacesV2` prop + wiring to
   `packages/chat-surface/src/destinations/run/RunDestination.tsx`.
6. **Web host**: extend `apps/frontend/src/app/featureFlags.ts`; pass the prop in
   `apps/frontend/src/features/run/RunRoute.tsx`.
7. **Desktop host**: create `apps/desktop/renderer/featureFlags.ts`; pass the prop in
   `apps/desktop/renderer/destinationBinders.tsx` (`RunBinder`).
8. **Design parity**: vendor the v2 mock's canvas/tab-strip region into
   `tools/design-parity/surfaces/surfaces-v2-canvas/{design/, anchors.json}` and add a
   `render-live-surfaces-v2.test.tsx` under `tools/design-parity/lib/` (picked up by the
   existing `render-live*.test.tsx` glob). `VERIFY AT IMPL:` where the mirrored
   `Generative Surfaces v2.dc.html` mock lives (SDR §9 says mirrored locally; if absent,
   fetch via DesignSync from Claude Design project `ceb081f6`, see
   `tools/design-parity/SKILL.md`).
9. **Docs**: update the program STATUS/progress notes if present; update SDR §3 only if
   the implementation diverged.

No ai-backend/backend/facade changes. No migrations. No new npm dependencies.

## Test plan

New/extended test files (colocated `*.test.ts(x)`, vitest):

- `packages/chat-surface/src/thread-canvas/ledgerProjection.test.ts`
  - `folds surface.created into a tab`; `view.derived bumps lastSeq + sets viewTier`
  - `tabs order by lastSeq desc; same-surface updates never duplicate`
  - `dedupes by event_id (SSE resend yields identical projection)`; `re-projection idempotent`
  - `unknown v2 event types ignored` (feed `usage.recorded`, `action.classified`)
  - adversarial: `view.derived for unknown surface_id dropped without throw`;
    `malformed payload (no surface_id/kind) skipped without throw`;
    `unknown kind falls to raw scheme (tier-3)`
  - `tabUriForSurface maps call→record and round-trips surfaceId`;
    `ledgerId uses the A1 formatter (r<short>·<seq>)`
- `packages/chat-surface/src/thread-canvas/ledgerProjection.parity.test.ts`
  - `ts fold of golden events === py fold snapshot` — import both A1 JSON fixtures by
    relative path (adapterAllowlist precedent) and assert
    `toParitySnapshot(projectLedger(goldenEvents))` deep-equals the checked-in expected
    fold. **This is DoD item 1 — it must fail if either language drifts.**
- `packages/chat-surface/src/destinations/run/useSurfacesV2.test.ts`
  - `disabled/idle: no request`; `fetches on first lastLedgerSeq > 0`
  - `seq advance during flight coalesces to exactly one follow-up request`
  - adversarial: `HTTP error → status error, stateFor undefined, no throw`
  - `snapshot entries adapt to SurfacePayload envelope shape`
- `packages/chat-surface/src/destinations/run/RunDestination.test.tsx` (extend)
  - `surfacesV2 off: identical tab strip + zero /surfaces requests` (byte-identity
    proof: all pre-existing RunDestination tests stay green **unmodified**)
  - `surfacesV2 on: seeded v2 events render named tabs; activation switches surface`
  - `surfacesV2 on with zero v2 events: empty canvas, no v1 tabs leak` (strictness)
  - adversarial: `hostile title string renders as text (no markup injection)`
- `apps/frontend/src/app/featureFlags.test.ts` (extend or create) and
  `apps/desktop/renderer/featureFlags.test.ts` (NEW): `default OFF`,
  `localStorage "true" enables`, `storage throw ⇒ OFF`.

**Live smoke (desktop, DoD item 2):**

1. `SURFACES_V2=true make dev` (full local stack; facade :8200 — runtime flag on).
2. `COPILOT_FACADE_URL=http://127.0.0.1:8200 npm run dev --workspace @0x-copilot/desktop`.
3. Electron devtools console:
   `localStorage.setItem("enterprise.flags.surfaces-v2", "true")`; reload.
4. Start a run whose tool calls produce a record and a table surface (an MCP connector
   returning an issue/record + a list; `docs/dev-testing.md` has run-creation recipes).
5. Verify: two named tabs, ordered newest-first; clicking switches the rendered surface;
   record renders via `RecordRenderer`, table via `TableRenderer` (or honest tier-3 if
   hydration lags); no console errors.
6. Remove the localStorage key, reload → cockpit behaves exactly as before (v1 tabs).
7. Design parity: `cd tools/design-parity && python3 -m http.server 8099`, extract
   computed styles for design + live tab strip (per `SKILL.md`), then
   `node lib/compare.mjs <design.json> <live.json> --anchors surfaces/surfaces-v2-canvas/anchors.json --out surfaces/surfaces-v2-canvas/out/report.md` — **0 HIGH**.

## Definition of done

From 03-prds.md PRD-B1 (binding, never weakened):

- [ ] **ts projector × golden events === py projection** — proven by
      `ledgerProjection.parity.test.ts` folding PRD-A1's golden events and matching
      PRD-A3's checked-in fold snapshot byte-for-byte (shared JSON fixtures).
- [ ] **Live desktop run shows tabbed record + table surfaces from real tool calls** —
      the smoke script above on the real stack; artifact: run id noted in the PR body.
- [ ] **Flag off ⇒ cockpit unchanged (existing cockpit tests still green)** — zero edits
      to pre-existing `RunDestination.test.tsx` / `ThreadCanvas.test.tsx` assertions;
      chat-surface, desktop, and frontend suites green.

Standard DoD (every PRD):

- [ ] Unit tests in owning workspaces pass; `typecheck` + `build` green (commands in the brief).
- [ ] Flags off ⇒ byte-identical behavior (no regression to shipped flows).
- [ ] No service-boundary violations (apps→facade only; no cross-`src/` imports; the
      `/surfaces` fetch goes through the Transport port, never `fetch`).
- [ ] New LLM call sites: **none in this PR** (any would need the A2 UsageMeter seam).
- [ ] Docs: SDR §3 updated if implementation diverges.

UI DoD (🎨):

- [ ] Built from design-system/chat-surface kit components — `TcTabs`/`TcSurfaceMount`
      reused; no host-app one-off styling.
- [ ] `tools/design-parity/` run against the staged v2 mock canvas/tab region: **0 HIGH
      drift** — artifact: `tools/design-parity/surfaces/surfaces-v2-canvas/out/report.md`.
- [ ] Live desktop smoke on the real stack (not just tests) — same artifact as DoD item 2.

Studio shell & posture DoD (close-out 2026-07-23 — B1 owns the FR-A7/FR-F1 mode/rail
clauses the coverage sweep flagged as unowned; see 06-coverage-report.md):

- [ ] **FR-A7 — approvals never in chat (Studio).** With the canvas mounted (Studio), no
      approval/decision affordance appears in the chat rail; a test asserts staged-write and
      gate approvals render **only** on the canvas surface, never as a chat-rail control.
- [ ] **FR-F1 — mode → canvas-visibility gate.** The canvas mount is gated by mode: **Focus
      ⇒ canvas not mounted** (the chat renders rich cards only — no generative surfaces);
      **Studio ⇒ canvas mounted**. A test asserts both branches of the mode gate.

## Out of scope

- Provenance footers, skeleton/assembling states, raw-fallback Copy/Download UI (B2);
  view lifecycle — upgrade toast, keep-generic, regenerate, suggest-a-shape (B3/B4).
- Gate cards + posture chip (C2); staged writes / approve bar / diffs on v2 surfaces
  (D-wave — v1 approval flow continues on the flag-off path only); receipt/gate surface
  rendering (E1/C2 — they tab as tier-3 if ever emitted).
- Any ai-backend/backend/facade change; any new event emission; time-travel (scrub) of
  v2 surfaces; v1 `result["surface"]` emission removal (E3); tab overflow "+N more" UX
  beyond the existing `MAX_SURFACE_TABS` cap.
- **Scope note (FR-F1, 2026-07-23 close-out):** B1 **owns the mode → canvas-visibility
  gate** (Studio ⇒ canvas mounted; Focus ⇒ canvas not mounted, rich cards only — DoD
  above). A _user-facing_ Studio/Focus toggle beyond the shell's existing mode control is
  **out of scope** unless already present — B1 wires the gate to the existing mode signal,
  it does not add a new switch.

## Guardrails

- **Service boundaries**: apps call the facade only (`/v1/*`); `packages/chat-surface`
  imports no `apps/*` and no other component's `src/`; all HTTP through the `Transport`
  port.
- **Substrate purity** (packages/chat-surface/CLAUDE.md): no `window` / `document` /
  `localStorage` / `fetch` / `EventSource` inside the package — the eslint gate must
  stay green. Flag reads live in the host apps only.
- **One projector invariant (FR-3.3)**: `projectLedger` is a pure selector over the SAME
  `session.events` array `useEventProjector` consumes; a second SSE subscription or a
  re-projection elsewhere is a defect.
- **Flag-off byte-identity (SDR §11)**: with `surfacesV2` unset/false and the flag keys
  absent, every code path, network call, and rendered byte matches today; the DoD's
  untouched-tests rule is the proof.
- **Adapter purity (D28/D29)**: no new adapter code; adapters stay pure-render; tier-3
  fallback stays the honest floor.
- **Contracts discipline** (packages/api-types/CLAUDE.md): consume A1's types verbatim;
  add no new api-types shapes in this PR — if A3's `/surfaces` response is missing a
  needed field, stop and file it against A3 instead of a client-side workaround.
- **Design fidelity (SDR §9)**: tab strip/canvas chrome stay on design-system tokens and
  kit recipes; never raw font-size/letter-spacing (design-system SKILL.md rule).

## Open questions

These are genuine cross-PRD / undecided coordination points surfaced by the
implementability audit. They do not block starting the fold + host wiring, but each must
be resolved before the DoD item it touches can be signed off. Neither weakens a DoD item —
each names the contract the DoD assumes.

1. **RESOLVED (2026-07-23 close-out) — shared parity-snapshot schema (unblocks DoD item 1).**
   Decision (owner sign-off, mirrors PRD-A3 Open questions item 1): A3 serves
   `SurfaceSnapshot`/`SurfaceViewState` as its distinct, **additive** SurfaceStore-fold
   output, and B1's `toParitySnapshot(projectLedger(golden))` targets the **same
   `SurfaceSnapshot` metadata shape** — one **shared JSON fixture**, **snake_case keys,
   sorted by `surface_id`, metadata-only**: per-surface `{surface_id, kind, title, source,
view_tier, created_seq, last_seq, ledger_id}`, deliberately _excluding_ hydrated
   `payload`/materialized content that only the `/surfaces` endpoint has and the pure fold
   cannot produce. **Owner of the shared fixture: PRD-A1**, checked in beside
   `golden_ledger_events.json`; A3's SurfaceStore golden snapshot emits exactly this
   metadata-only shape (not the full materialized store), so B1 byte-matches it from events
   alone. If A3's snapshot is ever found to include hydrated content, stop and file against
   A1/A3 per the Contracts guardrail rather than shimming client-side.

2. **v2 design-parity mock provenance (blocks the UI 0-HIGH DoD).** SDR §9 states the v2
   mock `Generative Surfaces v2.dc.html` is "already mirrored locally," but it is not
   present anywhere under the repo (no file matches, and `tools/design-parity/surfaces/`
   has no `surfaces-v2-canvas` region). Step 8's fallback — fetch via DesignSync from
   Claude Design project `ceb081f6` — needs confirmation of (a) the correct DesignSync
   project id and (b) the specific canvas/tab-strip frame name to vendor into
   `tools/design-parity/surfaces/surfaces-v2-canvas/design/`. Resolve by either committing
   the mirrored mock into the repo (making SDR §9 true) or confirming the project id +
   frame, before the parity artifact `out/report.md` (0 HIGH) can be produced.
