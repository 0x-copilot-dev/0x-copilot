# PRD-B2 — Provenance footers + loading states + raw fallback

Every surface on the v2 Studio canvas must carry its accountability chrome and honest
states: a provenance footer (producing operation · latency · access class · stable ledger
id · deep link to the native app), a skeleton/"assembling" state while a view is prepared,
a lossless raw-payload fallback with Copy/Download/size when no view fits, and a one-line
status strip mirroring run state. All of it is a pure projection of Work Ledger events —
no ad-hoc client state. UI-only PR: no Python changes expected (one VERIFY below).

## Implementer brief

Work in a **fresh git worktree branched off `main`** of the 0x-copilot monorepo; run
`make setup` once if `node_modules` is missing. This PR touches only npm workspaces +
host apps — no Python service code (see VERIFY-3 for the one possible exception).

Test commands for every component touched:

```bash
npm run test --workspace @0x-copilot/chat-surface
npm run typecheck --workspace @0x-copilot/chat-surface
npm run lint --workspace @0x-copilot/chat-surface        # substrate-boundary eslint gate
npm run typecheck --workspace @0x-copilot/frontend && npm run build --workspace @0x-copilot/frontend
npm run test --workspace @0x-copilot/desktop             # desktop renderer suite
node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs   # parity live render
# only if VERIFY-3 forces an ai-backend payload addition:
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/surfaces/
```

Read these files first:

1. `docs/plan/generative-surfaces-v2/01-problem-and-requirements.md` — FR-A4/A5, FR-D3, FR-F2, NFR-1/2 are this PR's contract.
2. `docs/plan/generative-surfaces-v2/02-sdr.md` — §5 event vocabulary (authoritative names/fields), §3 component table, §9 design-fidelity strategy.
3. `packages/chat-surface/CLAUDE.md` — substrate-agnostic hard rule, ports, barrel discipline, host-binder pattern.
4. `packages/chat-surface/src/thread-canvas/eventProjector.ts` — the one-projector pattern + pure peer selectors (`projectSurfaceTabs`); your selectors must follow it.
5. `packages/chat-surface/src/thread-canvas/TcSurfaceMount.tsx` — current surface-mount chrome the v2 frame wraps around.
6. `packages/chat-surface/src/thread-canvas/ThreadCanvas.tsx` + `src/destinations/run/RunDestination.tsx` — `ThreadCanvasProps` / `RunDestinationProps` (the prop-threading seams; RunDestinationProps lines ~197–300 show the host-callback pattern).
7. `packages/chat-surface/src/destinations/connectors/RevealOnce.tsx` — the established `onCopy: (text) => Promise<void>` host-callback pattern.
8. `packages/chat-surface/src/surfaces/GenericStructuredDiff.tsx` — existing tier-3 fallback living in chat-surface (precedent for the raw fallback's home).
9. `packages/design-system/src/styles.css` + `packages/design-system/SKILL.md` — token/recipe vocabulary; never write raw font-size/letter-spacing.
10. `tools/design-parity/SKILL.md` + `tools/design-parity/README.md` — the parity pipeline the DoD requires.
11. `docs/plan/generative-surfaces-v2/prds/PRD-B1-*.md` (sibling PRD) — the v2 canvas mount, client ledger projection, and canvas feature flag this PR builds on.

## Context

Generative Surfaces v2 re-founds the agent's work-product UI on an explicit, typed **Work
Ledger**: every consequential runtime event is append-only on the run's existing event
stream, and everything the user sees — canvas tabs, footers, receipts — is a **pure
projection** of that ledger (01 §1, 02 §2/§5). Wave A built the contracts (PRD-A1), the
usage meter (PRD-A2), and server-side emission + SurfaceStore projection behind the
`SURFACES_V2` runtime flag (PRD-A3). PRD-B1 mounted the client canvas: a ts projector
folds ledger events into tabbed surfaces in ThreadCanvas, behind a chat-surface canvas
feature flag, in both hosts.

PRD-B2 sits directly on B1 and makes each surface _accountable and honest_: FR-A5's
provenance footer, FR-A4/NFR-1's assembling state, FR-D3/NFR-2's raw fallback ("Nothing
is hidden"), FR-F2's status strip. It excludes view-lifecycle actions — Regenerate is
PRD-B3, Suggest-a-shape is PRD-B4 — but leaves the slots they mount into. Ledger-id
presentation is `r<short>·<seq>` (02 §5), e.g. `r7f3·042`. Core discipline: **footer
fields are sourced from ledger events only** — a value not derivable from
`surface.created` / `read.executed` / `action.classified` / `view.derived` payloads plus
the run envelope does not appear in the footer.

## Interfaces consumed / exposed

**Consumed (verify each against the merged A1/A3/B1 code before writing yours):**

- PRD-A1 (`packages/api-types`): `SurfaceEventV2` payload types for `surface.created`,
  `read.executed`, `action.classified`, `view.derived`; the ledger-id formatter
  (`r<short>·<seq>`) — **VERIFY AT IMPL (VERIFY-1):** exact exported symbol names
  (expected `formatLedgerId(runId, sequenceNo): string` or similar) and the event-type
  string constants re-exported from `packages/service-contracts`.
- PRD-A3 (ai-backend, behind `SURFACES_V2=true`): live emission of those events on the
  run's SSE/replay stream with `read.executed.latency_ms` populated —
  **VERIFY AT IMPL (VERIFY-3):** if A3 does not populate `latency_ms`, add it additively
  here (emission site + one unit test; additive field, no `v` bump per 02 §12).
- PRD-B1 (`packages/chat-surface`): the v2 client ledger projection (pure fold over
  `readonly RuntimeEventEnvelope[]`, peer of `projectSurfaceTabs`) giving per-surface
  `{surface_id, kind, source{connector,op}, title, payload, spec?}`; the v2 canvas pane
  rendering the active surface; the canvas feature-flag accessors in both hosts
  (host-read flag pattern, cf. `apps/frontend/src/app/featureFlags.ts`
  `isRunCockpitWebEnabled`) — **VERIFY AT IMPL (VERIFY-2):** exact projection/component/
  flag symbol names from B1's merged code; wrap whatever node B1 renders, do not fork it.
- Existing (verified in-repo): `ThreadCanvasProps`/`ThreadCanvas`, `RunDestinationProps`,
  `ClipboardPort.copyText`, web port bundle `apps/frontend/src/ports/PortProvider.tsx`
  (`usePorts`, `WebClipboardPort`), design-system kit (`StatusLine`, `Badge`,
  `.ui-mono-caps`, `.ui-caption`).

**Exposed (left behind for later PRDs):**

- `SurfaceProvenance` + `projectProvenance` (B3 reads `tier`; E1 receipt rows reuse the
  ledger-id string).
- `RawFallbackView.actionsSlot?: ReactNode` — B4 mounts "Suggest a shape →" there;
  `TcSurfaceFrame.frameActionsSlot` — B3 mounts "Back to generic/shaped".
- `TcStatusStrip` `kind: "gate"` switch arm as an explicit stub — C2 fills it (FR-F2
  gate context).
- Host callbacks `onCopyText`/`onSaveFile` on `RunDestinationProps` → `ThreadCanvasProps`
  — E1 reuses them for receipt export.

## Design

### D1 — Provenance selector (pure, ledger-only)

New file `packages/chat-surface/src/thread-canvas/provenance.ts`:

```ts
export type SurfaceAccessClass = "read" | "write_held";
export type SurfaceViewTier = "pending" | "raw" | "generic" | "shaped";

export interface SurfaceProvenance {
  readonly surfaceId: string;
  readonly ledgerId: string; // "r7f3·042" — A1 formatter(runId, seq of surface.created)
  readonly connector: string; // surface.created.source.connector
  readonly op: string; // surface.created.source.op
  readonly kind: string; // surface.created.kind (record|message|table|call|raw|receipt|gate)
  readonly latencyMs: number | null; // joined read.executed.latency_ms, else null
  readonly accessClass: SurfaceAccessClass;
  readonly tier: SurfaceViewTier; // latest view.derived.tier; none yet => "pending"
  readonly openIn: { readonly label: string; readonly url: string } | null;
}

export function projectProvenance(
  events: readonly RuntimeEventEnvelope[],
): ReadonlyMap<string, SurfaceProvenance>;
```

Join rules (all from SDR §5 payloads, verbatim field names):

1. `surface.created {surface_id, kind, source{connector,op}, title, payload_ref}` seeds
   the entry; `ledgerId` = A1 formatter over (run id, that event's `sequence_no`).
2. `read.executed {call_id, connector, op, latency_ms, payload_ref}` joins on
   `payload_ref` equality → `latencyMs`.
3. `action.classified {call_id, connector, op, class, basis}` joins via the matched
   `read.executed.call_id` → `accessClass`: `class: read` ⇒ `"read"`; `write`, `unknown`,
   or **no matching event** ⇒ `"write_held"` (fail closed, FR-C0 / 02 §10.1).
4. Latest `view.derived {surface_id, tier, basis, spec_ref?, gen}` per `surface_id` sets
   `tier`; absent ⇒ `"pending"`.
5. `openIn`: when B1's projection carries a spec with `link` (`SurfaceLink`:
   `{label, url_path}` — `url_path` only, no free-form URLs), resolve `link.url_path`
   against the surface payload with the D2 resolver; accept **only** `http:`/`https:`
   URL strings, anything else ⇒ `openIn: null` (link omitted, never a broken/unsafe
   anchor). Label = `link.label`, else `Open in <connector> ↗`, where `<connector>` is
   `humanizeConnector(connector)` — the slug→label helper barrel-exported from chat-surface
   (`./citations/connectorLabel`, e.g. `linear`→`Linear`) that the Sources chrome already
   uses. Humanizing happens in the component, never in the selector, so `projectProvenance`
   stores the raw slug in `connector` and stays pure.

Purity (DoD): `projectProvenance` is module-state-free — twice on the same array yields
deep-equal results; nothing read from context, storage, or clocks. Display strings:
`accessClass` → `read-only` / `write · held`; latency → `420ms` under 1s, `1.2s` at/above
(exported helper `formatLatency(ms)`).

### D2 — Local dot-path resolver

New file `packages/chat-surface/src/thread-canvas/dotPath.ts`: `resolveDotPath(data,
path): unknown` (iterative mapping-key/numeric-index walk, `undefined` on miss) +
`isSafeHttpUrl(value): value is string`. This intentionally duplicates
`packages/surface-renderers/src/_shared/path.ts` (`resolvePath`/`isSafeHttpUrl`) because
surface-renderers depends on chat-surface — importing back is a cycle. Keep it under ~40
lines and mirror the surface-renderers tests.

### D3 — Provenance footer component

New file `packages/chat-surface/src/thread-canvas/TcProvenanceFooter.tsx`:
`export function TcProvenanceFooter(props: { readonly provenance: SurfaceProvenance }):
ReactElement` — one-line footer bar, kit-only styling: op as `connector.op` in `.ui-mono-caps--9`;
latency `.ui-caption`; access class as a `Badge`-style pill — `read-only` (neutral) vs
`write · held` (warning tone via existing badge variants, no new hex); ledger id in
`.ui-mono-caps` (`data-testid="tc-provenance-ledger-id"`); deep link as a plain
`<a href={openIn.url} target="_blank" rel="noreferrer noopener">{label} ↗</a>` (anchor
markup is substrate-legal; **VERIFY AT IMPL (VERIFY-5):** desktop main already exposes an
`openExternal: (url) => shell.openExternal(url)` bridge to the renderer
(`apps/desktop/main/index.ts` ~lines 406/534/786), but no `setWindowOpenHandler` is
installed on the BrowserWindow — so a `target="_blank"` anchor click is NOT routed to the
OS browser today; add a `setWindowOpenHandler` in `apps/desktop` main that shells external
URLs, or route the deep link through the existing `openExternal` bridge instead).
Root `data-testid="tc-provenance-footer"`.

### D4 — Skeleton + frame

New files `packages/chat-surface/src/thread-canvas/TcSurfaceSkeleton.tsx` and
`packages/chat-surface/src/thread-canvas/TcSurfaceFrame.tsx`:

```ts
export interface TcSurfaceFrameProps {
  readonly provenance: SurfaceProvenance | null; // null => B1 pane renders bare (compat)
  readonly rawPayload?: unknown; // surface payload, for tier "raw"
  readonly onCopyText?: (text: string) => Promise<void>;
  readonly onSaveFile?: (text: string, filename: string) => Promise<void>;
  readonly frameActionsSlot?: ReactNode; // reserved: B3 toggle, B4 entry point
  readonly children: ReactNode; // B1's rendered surface content
}
export function TcSurfaceFrame(props: TcSurfaceFrameProps): ReactElement;
```

Dispatch by `provenance.tier`:

- `"pending"` → `TcSurfaceSkeleton`: line `"{Connector} · assembling {kind} view…"`
  (FR-A4 microcopy shape) — `{Connector}` = `humanizeConnector(provenance.connector)`,
  `{kind}` = `provenance.kind` — over 3 token-built shimmer bars (`--color-*`/`.ui-card`);
  `role="status"`, `data-testid="tc-surface-skeleton"`. Appears the moment
  `surface.created` lands, before any `view.derived` (NFR-1; shaping never delays it,
  FR-D1).
- `"raw"` → `RawFallbackView` (D5): `payload={rawPayload}`, `onCopy={onCopyText}`,
  `onDownload={onSaveFile}` (the frame maps its host-callback props onto D5's generic
  `onCopy`/`onDownload`), and `filename` derived **by the frame** from
  `provenance.ledgerId` — `·`→`-` plus a `-raw.json` suffix, e.g. `r7f3·042` →
  `r7f3-042-raw.json`. `actionsSlot` is left unset (B4 fills it via `frameActionsSlot`).
- `"generic"` / `"shaped"` → render `children` unchanged.
- Always: `TcProvenanceFooter` pinned at the frame's bottom edge (also under skeleton —
  op/ledger-id are known from `surface.created` before content).

The frame wraps B1's active-surface node inside the v2 canvas only; the legacy (flag-off)
path never mounts it.

### D5 — Raw fallback view (FR-D3, NFR-2)

New file `packages/chat-surface/src/surfaces/raw/RawFallbackView.tsx` — beside
`GenericStructuredDiff.tsx`, NOT in surface-renderers: SDR §3's "raw fallback" bullet is
satisfied here because surface-renderers → chat-surface is the only legal import
direction.

```ts
export const RAW_RENDER_MAX_BYTES = 262144; // 256 KiB display cap; Copy/Download always full

export interface RawFallbackViewProps {
  readonly payload: unknown;
  readonly filename: string; // e.g. "r7f3-042-raw.json" (ledger id, ·→-)
  readonly onCopy?: (text: string) => Promise<void>;
  readonly onDownload?: (text: string, filename: string) => Promise<void>;
  readonly actionsSlot?: ReactNode; // reserved for PRD-B4 "Suggest a shape →"
}
export function RawFallbackView(props: RawFallbackViewProps): ReactElement;
```

Behavior:

- Header carries the honesty line verbatim — **"This result doesn't fit a view — here's
  the raw result. Nothing is hidden."** — plus a human size label (`TextEncoder` byte
  length → `41.9 KB` / `1.4 MB`).
- Serialize **once** per payload via `useMemo` → `JSON.stringify(payload, null, 2)`
  (non-serializable input falls back to `String(payload)` without throwing) into a single
  `<pre>` (mono tokens `--font-size-mono-*`) with CSS `contain: content` +
  `overflow: auto` — the >40 KB no-jank mechanism: one memoized string, one text node,
  no per-line elements, no highlighting.
- Above `RAW_RENDER_MAX_BYTES`: display the first 256 KiB then an explicit elision line
  `— showing first 256 KB of {total} · Copy and Download carry everything —`;
  Copy/Download always operate on the **full** serialized text (labeled cap, NFR-2).
- Copy → `onCopy(fullText)`; Download → `onDownload(fullText, filename)`; buttons
  disabled when the callback is absent; feedback is a transient inline label
  (`Copied` / `Copy failed`), no toast dependency.
- React text rendering only (no `dangerouslySetInnerHTML`) — payload HTML/script/
  injection phrases render inert as text.

### D6 — Status strip (FR-F2)

New files `packages/chat-surface/src/thread-canvas/statusLine.ts` +
`TcStatusStrip.tsx`:

```ts
export interface StatusStripLine {
  readonly kind: "idle" | "op" | "assembling" | "gate"; // "gate" = reserved stub for PRD-C2
  readonly text: string; // e.g. "read.executed · linear.get_issue · r7f3·042"
  readonly ledgerId: string | null;
}
export function projectStatusLine(
  events: readonly RuntimeEventEnvelope[],
): StatusStripLine;
```

`projectStatusLine` folds to the **latest** consequential ledger event
(`read.executed`/`surface.created`/`view.derived` → `kind: "op"` with
`event-name · connector.op · ledgerId`); a surface currently `pending` →
`kind: "assembling"`; no v2 events → `kind: "idle"`. Sourcing `connector.op`:
`read.executed` carries `connector`/`op` directly; `surface.created` via
`source{connector,op}`; `view.derived` carries **neither**, so resolve `connector.op` by
joining to that surface's `surface.created` (same events array, by `surface_id`) — if
unresolved, omit the `· connector.op` segment (never fabricate one). `ledgerId` is always
the latest event's own (`run_id`, `sequence_no`) via the A1 formatter. The `"gate"` arm is typed but
unreachable until C2 emits `gate.opened`. `TcStatusStrip` renders one line via the
design-system `StatusLine` kit component, `role="status"`,
`data-testid="tc-status-strip"`; mounted by ThreadCanvas at the bottom of the center
pane, v2-canvas-flag-on only — **VERIFY AT IMPL (VERIFY-6):** exact placement (above vs
below the mini-timeline) against the v2 mock during the parity step.

### D7 — Prop threading + host wiring

- Add to `ThreadCanvasProps` and `RunDestinationProps` (optional, default absent —
  flag-off byte-identical): `onCopyText?: (text: string) => Promise<void>`,
  `onSaveFile?: (text: string, filename: string) => Promise<void>`.
- Web: `apps/frontend/src/features/run/RunRoute.tsx` passes
  `onCopyText = ports.clipboard.copyText` (`usePorts()`) and `onSaveFile` from a NEW
  helper `apps/frontend/src/ports/download.ts` → `downloadTextFile(text, filename)`
  (Blob + anchor click — substrate APIs allowed in host code).
- Desktop: `apps/desktop/renderer/destinationBinders.tsx` `RunBinder` passes `onCopyText`
  via the renderer's clipboard and `onSaveFile` via an IPC save channel —
  **VERIFY AT IMPL (VERIFY-7):** confirmed no `dialog.showSaveDialog` / save-dialog IPC
  channel exists in `apps/desktop` today — add one (`dialog.showSaveDialog` + `fs.writeFile`
  in main, exposed via preload).
- Barrel: export `projectProvenance`, `SurfaceProvenance`, `formatLatency`,
  `TcProvenanceFooter`, `TcSurfaceFrame`, `TcSurfaceSkeleton`, `RawFallbackView`,
  `RAW_RENDER_MAX_BYTES`, `TcStatusStrip`, `projectStatusLine` from
  `src/thread-canvas/index.ts` + `src/index.ts` in a new delimited block
  `// === Surfaces v2 — PRD-B2 provenance + honest states ===`.

### Flags & error behavior

No new flags: server emission is `SURFACES_V2` (A3); client mount is B1's canvas flag —
all B2 UI mounts strictly inside B1's flag-gated v2 canvas subtree; both off ⇒ zero
DOM/byte difference (snapshot-tested). Malformed/partial v2 events never throw — the
affected surface degrades per-field (`latencyMs: null`, `accessClass: "write_held"`,
`tier: "pending"`); unknown event types are ignored. `onCopy`/`onDownload` rejections
are caught → inline failure label, never an unhandled rejection.

## Implementation plan

1. **Selectors.** Create `packages/chat-surface/src/thread-canvas/dotPath.ts`,
   `provenance.ts`, `statusLine.ts` (+ colocated `.test.ts` each), consuming A1's event
   types/formatter and golden-event fixture.
2. **Components.** Create `packages/chat-surface/src/surfaces/raw/RawFallbackView.tsx`
   and `packages/chat-surface/src/thread-canvas/` `TcProvenanceFooter.tsx`,
   `TcSurfaceSkeleton.tsx`, `TcSurfaceFrame.tsx`, `TcStatusStrip.tsx` (+ tests per Test
   plan); mount the strip in `ThreadCanvas.tsx` behind the v2 canvas condition.
3. **Integrate with B1.** Wrap B1's active-surface node in `TcSurfaceFrame`; thread
   `onCopyText`/`onSaveFile` through `ThreadCanvas.tsx` and
   `destinations/run/RunDestination.tsx`; barrel exports in `src/thread-canvas/index.ts`
   - `src/index.ts`.
4. **Host wiring.** Web: `apps/frontend/src/ports/download.ts` (new) +
   `apps/frontend/src/features/run/RunRoute.tsx` (pass callbacks). Desktop:
   `apps/desktop/renderer/destinationBinders.tsx` (+ main/preload IPC per VERIFY-7).
5. **Parity baseline.** Vendor the footer + raw-fallback + status-strip region of the v2
   mock (`Generative Surfaces v2.dc.html`, Claude Design project `ceb081f6`) into
   `tools/design-parity/surfaces/surfaces-v2-footer/design/` via DesignSync
   (**VERIFY AT IMPL (VERIFY-4):** the mock is not yet vendored under
   `tools/design-parity/surfaces/`); write `anchors.json`; add
   `tools/design-parity/lib/render-live-surfaces-v2-footer.test.tsx`; run the pipeline;
   commit `out/report.md`.
6. **Live smoke** (below) and fix what it finds.

## Test plan

Unit tests (vitest, jsdom for `.tsx`, colocated per package convention):

- `packages/chat-surface/src/thread-canvas/provenance.test.ts` —
  `projects footer fields from golden ledger events only` (A1 fixture → exact
  `SurfaceProvenance`); `ledger id formats as r<short>·<seq>`; `joins read.executed via
payload_ref and action.classified via call_id`; adversarial:
  `missing action.classified ⇒ write_held (fail closed)`, `class unknown ⇒ write_held`,
  `unsafe url_path value (javascript:, data:, relative) ⇒ openIn null`,
  `malformed v2 payload never throws, degrades per-field`; purity:
  `same events twice ⇒ deep-equal; input array not mutated`.
- `packages/chat-surface/src/thread-canvas/dotPath.test.ts` — mirror of the
  surface-renderers resolver cases (nested, numeric index, miss ⇒ undefined).
- `packages/chat-surface/src/thread-canvas/statusLine.test.ts` — latest-op line,
  assembling, idle; a latest `view.derived` resolves `connector.op` via its
  `surface.created` (and omits the segment when unresolved); the `gate` arm typed but
  unreachable.
- `packages/chat-surface/src/thread-canvas/TcProvenanceFooter.test.tsx` — all five
  fields render; `write · held` badge tone; anchor has `rel="noreferrer noopener"`;
  no anchor when `openIn` null.
- `packages/chat-surface/src/thread-canvas/TcSurfaceFrame.test.tsx` — pending ⇒ skeleton
  `"Linear · assembling record view…"`; `view.derived` arrival flips to children; raw
  tier ⇒ `RawFallbackView`; footer present in all three states; legacy/flag-off path
  never renders the frame (B1-pane snapshot unchanged).
- `packages/chat-surface/src/surfaces/raw/RawFallbackView.test.tsx` — honesty line +
  size label; Copy/Download receive the FULL serialized text (+ filename);
  `>40KB payload renders as one <pre> text node with a single JSON.stringify` (spy);
  `>256KB shows labeled elision, copy/download still full`; adversarial:
  `<script>`/markdown/injection-phrase payload renders inert as text (assert
  `textContent`), non-serializable payload falls back without throwing, callback
  rejection ⇒ inline `Copy failed`, no unhandled rejection.
- `packages/chat-surface/src/thread-canvas/TcStatusStrip.test.tsx` — mirrors
  `projectStatusLine`; `role="status"`.
- If VERIFY-3 triggers: extend the A3 emission test under
  `services/ai-backend/tests/unit/agent_runtime/surfaces/` asserting
  `read.executed.latency_ms` is populated.

Parity run (UI DoD): the `tools/design-parity` pipeline per its SKILL.md (vendor design →
live-render → serve on :8099 → extract computed styles → `node lib/compare.mjs …
--anchors surfaces/surfaces-v2-footer/anchors.json --out
surfaces/surfaces-v2-footer/out/report.md`) → **0 HIGH**.

Live smoke (desktop-first, per UI DoD):

1. `make dev` with ai-backend env `SURFACES_V2=true` (`RUNTIME_SURFACE_EMISSION` left
   default-on); enable B1's canvas flag in the host.
2. `export TOKEN=$(make dev-bearer)`; start a run whose goal exercises a builtin-spec
   connector op (e.g. github.list_issues via an installed MCP server; recipes in
   `docs/dev-testing.md`).
3. Canvas: skeleton line appears before content; footer then shows `connector.op`,
   latency, `read-only`, `r<short>·<seq>`, and `Open in … ↗` opening the OS browser.
4. Raw surface: call an op with no builtin/stored spec while `SURFACE_SPEC_MODEL` is
   empty (generation off) returning >40 KB (any list op, large page size). Verify honesty
   line, size label, smooth scroll (no jank), Copy pastes the full JSON, Download writes
   `r<short>-<seq>-raw.json` byte-matching the copied text.
5. Status strip mirrors the latest op line throughout; kill both flags and confirm the
   cockpit is identical to main.

## Definition of done

Carried verbatim from 03-prds.md PRD-B2, expanded with proving artifacts:

- [ ] **Footer fields sourced from ledger events only (no ad-hoc client state).** Proof:
      `provenance.test.ts` purity + golden-event cases; `TcProvenanceFooter` accepts only
      a `SurfaceProvenance` (no port/context reads).
- [ ] **Raw fallback renders a >40KB packed payload without jank; copy/download verified
      live.** Proof: the 45 KB unit case + live-smoke step 4 (scroll check,
      byte-identical copy vs downloaded file).
- [ ] **Parity: footer + fallback vs mock, 0 HIGH.** Proof: committed
      `tools/design-parity/surfaces/surfaces-v2-footer/out/report.md` showing 0 HIGH.

Standard DoD (every PRD):

- [ ] Unit tests in the owning workspaces pass; typecheck + build green (`chat-surface`
      test/typecheck/lint, `frontend` typecheck/build, `desktop` tests).
- [ ] Flags off ⇒ byte-identical behavior (frame/strip unmounted; existing cockpit
      snapshot tests green unchanged).
- [ ] No service-boundary violations (apps→facade only; no cross-`src/` imports; no
      chat-surface→surface-renderers import — D2 documents why).
- [ ] New LLM call sites: none in this PR (assert: no model invocation added).
- [ ] Docs: update `02-sdr.md` §3 if the raw-fallback home (chat-surface, not
      surface-renderers) is judged a divergence worth recording.

UI DoD (UI-touching PRD):

- [ ] Built from design-system/chat-surface kit components — no host-app one-off styling;
      no raw font-size/letter-spacing (design-system SKILL.md rule).
- [ ] `tools/design-parity/` run against the staged v2 mock region: **0 HIGH drift**.
- [ ] Live desktop smoke of the flow on the real stack (steps above), not just tests.

## Out of scope

- Regenerate / view-lifecycle toggles (`view.derived` upgrades, `view.preference`) —
  PRD-B3; this PR only reserves `frameActionsSlot`.
- "Suggest a shape" (`shape.requested`, `POST /v1/agent/surfaces/{surface_id}/shape-request`) —
  PRD-B4; this PR only reserves `actionsSlot` on `RawFallbackView`.
- Gate cards, posture chip, `gate.opened`/`gate.resolved` rendering — PRD-C2 (the
  strip's `"gate"` arm stays a stub).
- New ledger event types, ai-backend emission changes (except the additive VERIFY-3
  `latency_ms` fix), facade endpoints, python projections, pricing/usage UI, receipts,
  Sources rail.

## Guardrails

- **Service boundaries:** apps call the facade only; no deployable imports another's
  `src/`; chat-surface must not import surface-renderers (dependency direction) or any
  `apps/*` module (eslint-enforced); hosts consume chat-surface only through the barrel.
- **Substrate-agnostic (chat-surface CLAUDE.md):** no `window`/`document`/`fetch`/
  `localStorage`/`EventSource` inside the package — clipboard/file-save are host-callback
  props; the deep link is plain anchor markup; package lint must stay green.
- **One-projector invariant (02 §2 — "one projector, one vocabulary, one source of truth"):** `projectProvenance`/`projectStatusLine` are pure
  selectors over the SAME `session.events` array ThreadCanvas already projects — a second
  SSE subscription or stateful store is a defect.
- **Flag-off byte-identical:** with `SURFACES_V2` off or B1's canvas flag off, no new DOM
  nodes, props, or event handling anywhere in the cockpit (02 §11).
- **Honesty (NFR-2):** never truncate silently — every display cap is labeled;
  Copy/Download always carry the complete payload; never fabricate footer values —
  unknown is absent or fail-closed, never guessed.
- **Kit discipline:** tokens/recipes from `packages/design-system/src/styles.css` only;
  both hosts get the feature through the shared package + their own binders (update BOTH
  binders when props change).
