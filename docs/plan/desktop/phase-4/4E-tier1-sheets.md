# Phase 4.E: tier1-sheets

## Vision

Tier-1 hand-built renderer for spreadsheet regions (`sheet-row://`), conforming
to the FROZEN `SaaSRendererAdapter<TResource, TDiff>` contract from
Phase 0-A / 4-A. Pure render of a sheet region — a header row plus data rows —
with read-only formula chrome and a diff renderer that highlights changed
cells and overlays a `TcInlineDiff` provenance pill.

This is the substrate-neutral spreadsheet sibling of `EmailRenderer`. Same
contract, same purity discipline (D28): no transport, no MCP, no fetch, no
`window`. The host (`TcSurfaceMount`) supplies state via `renderCurrent` and
diffs via `renderDiff`; the adapter only renders.

DRY / single-source / simple-elegant applied:

- **Single contract.** Implements `SaaSRendererAdapter<SheetRegion, SheetDiff>`
  with `scheme: 'sheet-row'`, `matches: uri => uri.startsWith('sheet-row://')`.
  No second adapter shape, no per-renderer custom prop bag.
- **Compose, don't duplicate.** The diff-cell annotation pill reuses the
  frozen `TcInlineDiff` primitive from chat-surface. No re-implementation of
  the lime/pending/accepted/rejected palette.
- **Column virtualization without a library.** Wide sheets (≥ 50 columns)
  render a JavaScript-sliced visible window inside a `overflow-x: auto`
  container. No `react-window`, no `react-virtualized`. The whole-region
  case (<50 columns) renders all cells. One decision point, one code path
  per regime.
- **Read-only formula chrome.** Cells with a `formula` show the formula bar
  (`D5 =SUM(D2:D4) * RENEWAL_UPLIFT`) underneath the computed value. Phase 4
  scope is render-only — no editing, no recalc, no expression parsing.
- **Functional components, no `any`, no comments by default.**

## Status

- Status: in-progress
- Agent slug: `tier1-sheets`
- Branch: `desktop/phase-4-tier1-sheets`
- Worktree: `.claude/worktrees/agent-a6e510bd018c7519d`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-4/4E-tier1-sheets.md` — this file.
- `packages/surface-renderers/src/sheet/SheetRenderer.tsx`
- `packages/surface-renderers/src/sheet/SheetDiff.tsx`
- `packages/surface-renderers/src/sheet/index.ts`
- `packages/surface-renderers/src/sheet/SheetRenderer.test.tsx`
- `packages/surface-renderers/src/sheet/SheetDiff.test.tsx`
- A delimited Phase 4-E block appended to
  `packages/surface-renderers/src/index.ts` (and only that block).

**Out of scope** (do NOT touch):

- `packages/surface-renderers/src/email/**` — Phase 4-B territory.
- Any other Phase 4-b sibling subdirectory (salesforce, slide, generic).
- `packages/chat-surface/**` — frozen for renderer consumers.
- `apps/**`, transport/IPC, host shells.
- Spreadsheet editing, formula evaluation, recalc, cell selection,
  column resize — deferred to a later phase.

## Functional requirements

### Adapter contract (sheet-row://)

- [x] FR-S1 — `sheetAdapter` is a `SaaSRendererAdapter<SheetRegion, SheetDiff>`
      with `scheme: 'sheet-row'`,
      `matches: (uri) => uri.startsWith('sheet-row://')`,
      `metadata: { origin: 'first-party', schemaVersion: 1 }`.
- [x] FR-S2 — `registerSheetAdapter()` calls
      `registerAdapter(sheetAdapter)`. Idempotent: re-calling replaces the
      same `{scheme, version}` entry per `SurfaceRegistry.registerAdapter`
      semantics.

### `SheetRegion` shape (resource)

```ts
export interface SheetCellValue {
  readonly value: string | number | null;
  readonly formula?: string; // e.g. "=SUM(D2:D4) * RENEWAL_UPLIFT"
  readonly format?: "text" | "number" | "currency" | "percent" | "date";
}

export interface SheetRegion {
  readonly sheetId: string;
  readonly regionId: string;
  readonly headers: readonly string[]; // length = column count
  readonly rows: readonly (readonly SheetCellValue[])[]; // each row.length === headers.length
  readonly rowAnchors?: readonly string[]; // optional row identifiers (e.g. "D5")
  readonly viewport?: {
    readonly startColumn: number; // inclusive
    readonly endColumn: number; // exclusive
  };
}
```

### `SheetDiff` shape

```ts
export interface SheetCellChange {
  readonly row: number; // index into SheetRegion.rows
  readonly column: number; // index into SheetRegion.headers
  readonly before: SheetCellValue;
  readonly after: SheetCellValue;
}

export interface SheetDiff {
  readonly diffId: string;
  readonly provenance: string;
  readonly title: string;
  readonly description?: string;
  readonly region: SheetRegion;
  readonly changes: readonly SheetCellChange[];
}
```

### `renderCurrent(region: SheetRegion)`

- [x] FR-R1 — Renders a `<table>` with a `<thead>` (one row of `<th>` cells
      from `region.headers`) and a `<tbody>` (one `<tr>` per row, one `<td>`
      per column).
- [x] FR-R2 — Each cell renders the computed value (or empty string for
      `null`). When `cell.formula` is present, the cell also renders a
      read-only formula bar showing `{rowAnchor} {formula}` (e.g.
      `D5 =SUM(D2:D4) * RENEWAL_UPLIFT`) below the value. The formula bar is
      `aria-readonly="true"`.
- [x] FR-R3 — Column virtualization: if `region.headers.length < 50`, render
      all columns. Otherwise, slice `[viewport.startColumn, viewport.endColumn)`
      from headers and from each row; if no `viewport` is supplied for a
      ≥ 50-column region, fall back to `[0, 50)`. The outer container has
      `overflow-x: auto`.
- [x] FR-R4 — Empty region (`headers.length === 0`) renders an
      `aria-label="Empty sheet region"` placeholder. No crash.

### `renderDiff(diff: SheetDiff)`

- [x] FR-D1 — Renders the same table layout as `renderCurrent(diff.region)`
      so the diff is visually positioned exactly where the cells live.
- [x] FR-D2 — Each cell present in `diff.changes` shows the `after` value
      with a `data-changed="true"` attribute and a highlight border. The
      `before` value is rendered alongside as struck-through chrome
      (`<del>`) so the change is legible in the same cell.
- [x] FR-D3 — A single `TcInlineDiff` provenance card is rendered above the
      table with `state="streaming"`, the diff's `provenance` / `title` /
      `description`. The adapter does **not** render Approve / Reject /
      Suggest-changes buttons — those are the host's responsibility (D28).
      `TcInlineDiff` only renders Approve / Reject in `state="pending"`,
      so `state="streaming"` is the D28-correct fit: it conveys "diff
      in flight" without rendering host-owned actions. `onApprove`,
      `onReject`, `onSuggestChanges` are intentionally omitted from the
      `TcInlineDiff` call.
- [x] FR-D4 — Cells not in `changes` render the current value (no formula
      chrome on the diff view to keep the diff dense; the formula bar is
      visible only on `renderCurrent`).

### Purity (D28)

- [x] FR-P1 — Module file imports only from `react`,
      `@enterprise-search/chat-surface`, and sibling files in
      `./sheet/`. No transport / no MCP / no fetch / no `window`. ESLint
      rule from Phase 4-A enforces.
- [x] FR-P2 — Both render functions are pure functions of their argument.
      Re-invocation with the same input produces the same output.

## Non-functional requirements

- **Accessibility.** `<table>` with `role="table"`, `<th scope="col">`,
  cells reachable via the table's natural tab order is acceptable for
  read-only chrome. Changed cells expose `aria-label` of the form
  `"{header}: {before} → {after}"`.
- **Performance.** A 100×20 region renders in one synchronous pass. A
  region with `headers.length >= 50` is sliced before render so the DOM
  receives only the visible-column window.
- **No third-party dependency.** No virtualization library is added. CSS
  `overflow-x: auto` plus JavaScript slice is sufficient at Phase 4 scope.
- **Test coverage.**
  - Adapter conformance: scheme / matches / metadata shape.
  - `renderCurrent` standard small region: headers, all rows, all cells,
    formula bar visible when present.
  - `renderCurrent` empty region: placeholder shown, no crash.
  - `renderDiff` highlights changed cells, leaves unchanged cells alone,
    renders the `TcInlineDiff` pill with `state="pending"` and no buttons.
  - Wide-sheet virtualization: 120-column region with `viewport: {0, 30}`
    renders 30 columns; without `viewport`, falls back to `[0, 50)`.
  - Wide-sheet header / body length parity.

## Interfaces consumed

- `SaaSRendererAdapter`, `SaaSRendererAdapterMetadata`, `registerAdapter` from
  `@enterprise-search/chat-surface`.
- `TcInlineDiff`, `InlineDiffState` from `@enterprise-search/chat-surface`.

## Interfaces produced

```ts
// barrel exports from packages/surface-renderers/src/sheet/index.ts
export {
  SheetRenderer,
  sheetAdapter,
  registerSheetAdapter,
} from "./SheetRenderer";
export { SheetDiff as SheetDiffView } from "./SheetDiff";
export type {
  SheetRegion,
  SheetCellValue,
  SheetDiff,
  SheetCellChange,
} from "./SheetRenderer";
```

The barrel intentionally aliases the `SheetDiff` _component_ as
`SheetDiffView` so the consumer can import both the `SheetDiff` _type_
and the diff component without name collision. Both names are exposed at
the package root via the Phase 4-E delimited block in
`packages/surface-renderers/src/index.ts`.

## Open questions

1. **Wide-sheet viewport policy.** Phase 4 defaults to `[0, 50)` when a
   ≥ 50-column region arrives without a `viewport`. A future phase will
   wire scroll position from the host into `viewport` so the visible
   window tracks the user. No host-side scroll port exists yet.
2. **Formula chrome interactivity.** Read-only this phase. A later phase
   may add `onEditFormula` to the adapter contract; the chrome already
   carries `aria-readonly="true"` so the read-only intent is explicit.
3. **Per-cell provenance pills.** PRD-implied "diff renderer highlights
   changed cells" satisfied by a single inline-diff card + cell-level
   `data-changed`. A future phase may add per-cell mini pills; deferred.

The agent **proceeds to implementation** with the documented placeholders.

## Done criteria

- [ ] All FRs met
- [ ] `npm test --workspace @enterprise-search/surface-renderers` passes
- [ ] `npm run lint --workspace @enterprise-search/surface-renderers` passes
- [ ] No imports outside scope; no edits outside the In-scope list
- [ ] No transport / fetch / MCP / `window` / `document` references
- [ ] No new third-party dependency

## Notes for orchestrator review

- The Phase 4-E block in `packages/surface-renderers/src/index.ts` is
  delimited by `// === Phase 4-E tier1-sheets ===` / `// === end Phase
4-E ===` markers so the orchestrator can merge it alongside sibling
  Phase 4-b blocks (4-B, 4-C, 4-D, 4-F) without conflict — each agent
  adds its own delimited block.
- Tests use plain `render(adapter.renderCurrent(region))` and
  `render(adapter.renderDiff(diff))` against the JSDOM environment
  already configured in `vitest.config.ts`. No MockTransport is needed
  because the adapter is pure.
- `EmailRenderer` is intentionally left untouched in this branch — its
  migration off the deprecated `SurfaceRendererProps` shape is Phase 4-B's
  responsibility. The Phase 4-E block in `index.ts` only adds — never
  removes — exports.
