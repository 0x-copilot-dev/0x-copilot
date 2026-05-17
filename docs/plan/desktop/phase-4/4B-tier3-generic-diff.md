# Phase 4.B: tier3-generic-diff

## Vision

Tier-3 of the three-tier adapter strategy (PRD §3.4) — the universal
fallback `SaaSRendererAdapter` that resolves last via `SurfaceRegistry`'s
wildcard bucket (`scheme: '*'`, `matches: () => true`).

Tier-3 is the safety net the host (`TcSurfaceMount`, Phase 4-A) falls
back to when:

- no tier-1 / tier-2 adapter is registered for the artifact's scheme; or
- a registered adapter's `renderCurrent` / `renderDiff` throws or times
  out (D29 — the host's error boundary picks tier-3 next).

It must render **any** MCP tool-call payload as a structured diff card
without prior knowledge of the SaaS, the resource shape, or the diff
shape — "always works" per the PRD diagram. That means it accepts an
intentionally permissive `unknown` payload and walks it defensively.

DRY principles applied:

- **One walker for current and diff.** Recursive field rendering of a
  generic payload (resource id, scalar fields, nested objects, arrays)
  is the same primitive used for `renderCurrent` and for the `new` /
  `old` halves of `renderDiff`. One function — `renderValue` — handles
  both, with a depth cap and length cap baked in.
- **One truncation primitive.** Any string > `MAX_STRING_BYTES` (2048)
  is truncated to a "(+N chars hidden)" placeholder. No JS click handler
  in this phase — just rendered text. Used by every value cell.
- **One depth cap.** Recursion stops at `MAX_DEPTH` (5) levels and emits
  a `…` placeholder cell. Used by both renderers.
- **One field-row component.** `FieldRow` renders one labeled cell;
  reused for the resource header, for each top-level field in current
  state, and for each changed field in the diff card. The diff variant
  shows old → new side by side; the current variant shows one value.
- **Pure render only (D28).** No transport, no fetch, no `window`, no
  state, no effects, no callbacks. The "Open in {SaaS}" affordance is a
  plain `<a href>` — the host owns navigation policy.
- **No comments by default; functional components only; no `any`.**

The tier-3 adapter lives in `packages/chat-surface/src/surfaces/` (not
in `packages/surface-renderers/`) because it is the registry's universal
default, owned by the package that owns the registry. `surface-renderers`
is for first-party SaaS-specific tier-1 adapters (Email, Salesforce,
Sheets, Slides). Keeping tier-3 next to `SurfaceRegistry` is the
"single source of truth" placement: the resolver and the resolver's
last-resort answer ship from the same module boundary.

## Status

- Status: in-progress
- Agent slug: `tier3-generic-diff`
- Branch: `desktop/phase-4-tier3-generic-diff`
- Worktree: `.claude/worktrees/agent-afb1511aebad176a4`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-4/4B-tier3-generic-diff.md` — this file.
- `packages/chat-surface/src/surfaces/GenericStructuredDiff.tsx`
- `packages/chat-surface/src/surfaces/GenericStructuredDiff.test.tsx`
- `packages/chat-surface/src/surfaces/index.ts` — append exports inside a
  delimited block (see "Coordination" below).

**Out of scope** (do NOT touch):

- `packages/chat-surface/src/index.ts` — orchestrator-owned. The barrel
  re-export is wired at merge time.
- `packages/chat-surface/src/surfaces/SaaSRendererAdapter.ts` — FROZEN
  in Phase 4-A.
- `packages/chat-surface/src/surfaces/SurfaceRegistry.ts` — FROZEN in
  Phase 4-A.
- `packages/chat-surface/src/thread-canvas/TcInlineDiff.tsx` — consumed
  but not modified.
- Any tier-1 renderer in `packages/surface-renderers/` (4C/4D/4E/4F).

## Functional requirements

### Adapter contract conformance

- [x] FR-A1 — `GenericStructuredDiff` is a `SaaSRendererAdapter`
      with `scheme: TIER3_SCHEME` (= `'*'`), `matches: () => true`,
      `metadata: { origin: 'first-party', schemaVersion: 1 }`.
- [x] FR-A2 — A separately-exported function
      `registerGenericStructuredDiff()` calls
      `registerAdapter(GenericStructuredDiff)`. The chat-surface package
      does **not** auto-register tier-3; the host app opts in (so test
      environments can choose the empty-registry state).

### Generic payload model

- [x] FR-P1 — The adapter accepts an `unknown`-typed payload. It treats
      the payload as a `GenericCurrentState` for `renderCurrent`:

      ```ts
      interface GenericCurrentState {
        readonly resourceId?: unknown;
        readonly saas?: unknown;
        readonly openUrl?: unknown;
        readonly fields?: unknown;
      }
      ```

      Missing fields render gracefully (no crash). Unknown extra keys
      are ignored.

- [x] FR-P2 — `renderDiff` accepts a `GenericStructuredDiffPayload`:

      ```ts
      interface GenericStructuredDiffPayload {
        readonly resourceId?: unknown;
        readonly saas?: unknown;
        readonly openUrl?: unknown;
        readonly reasoning?: unknown;
        readonly fieldChanges?: unknown;
        readonly proposed?: unknown;
        readonly current?: unknown;
      }
      ```

      A `fieldChange` row is `{ field: string; old?: unknown; new?:
      unknown }`. If `fieldChanges` is absent or empty AND a `proposed`
      payload is present, the diff renders the proposed payload as the
      current-state view (per the prompt's "no proposed payload (renders
      current state only)" case — symmetrical: no diff payload → render
      current).

### Rendering — current state

- [x] FR-C1 — Top of the card: a header strip with the resource id
      (`resourceId` coerced to string) and the SaaS label (`saas`
      coerced to string). Both have fallbacks: `"(no resource id)"` /
      `"(unknown saas)"`.
- [x] FR-C2 — Field rows: each top-level key in `fields` (when it is an
      object) renders as a `FieldRow` with the key as label and the
      value walked by `renderValue`. Arrays render as a numbered list
      of recursive `renderValue` outputs. Nested objects recurse with
      depth +1.
- [x] FR-C3 — When `fields` is not an object (e.g. missing, primitive,
      array), it is walked directly by `renderValue` (so a string
      payload still renders something).
- [x] FR-C4 — "Open in {SaaS}" link: a plain
      `<a href={openUrl} target="_blank" rel="noreferrer noopener">`
      rendered at the bottom of the card if `openUrl` is a non-empty
      string AND it parses to a `http:` or `https:` URL (per a safe-URL
      guard — no `javascript:` etc.). When the URL is unsafe or missing,
      the link is omitted entirely.

### Rendering — diff

- [x] FR-D1 — Header: same resource id + SaaS chrome as the current
      state view.
- [x] FR-D2 — Pending pill ("PENDING DIFF") matching the right-rail
      design from the PRD §3.4 diagram. Implemented inline (does not
      depend on `TcInlineDiff`'s full interactive state machine — the
      host owns Approve / Reject buttons, D28).
- [x] FR-D3 — Reasoning text (when `reasoning` coerces to a non-empty
      string): a dedicated reasoning block under the header.
- [x] FR-D4 — Field changes: a list of `{ field, old, new }` rows where
      each row shows the field name, the old value walked by
      `renderValue`, and the new value walked by `renderValue`. Old
      values are styled struck-through / muted; new values are styled
      with an emphasized accent. Each side is independently truncated
      and depth-capped.
- [x] FR-D5 — When `fieldChanges` is empty / missing AND `proposed` is
      present, the diff card renders the proposed payload as if it
      were the current-state view (single side, no old/new pairs).
- [x] FR-D6 — When `fieldChanges` is empty / missing AND `proposed` is
      absent BUT `current` is present, the diff card renders `current`
      as the current-state view (gracefully degrades to "no proposed
      payload (renders current state only)").
- [x] FR-D7 — "Open in {SaaS}" link as in FR-C4.

### Defensive rendering primitives

- [x] FR-X1 — `renderValue(value: unknown, depth: number)` handles
      `null`, `undefined`, `boolean`, `number`, `string`, `array`,
      `object`. Anything else (e.g. function, symbol) renders as a
      placeholder (`"(unrepresentable)"`).
- [x] FR-X2 — `depth > MAX_DEPTH` (5) emits `"…"`. The cap is
      inclusive at the configured depth.
- [x] FR-X3 — Strings longer than `MAX_STRING_BYTES` (2048) are
      truncated to the first `MAX_STRING_BYTES` characters followed by
      `"… (+N chars hidden)"`. No JS click handler.
- [x] FR-X4 — `null` renders as `"null"`; `undefined` as `"—"`;
      booleans as their literal text; numbers as their literal text
      (no scientific normalization).
- [x] FR-X5 — Arrays larger than `MAX_ARRAY_ITEMS` (50) render the
      first 50 followed by `"… (+N items hidden)"`. Objects with more
      than `MAX_OBJECT_KEYS` (50) keys render the first 50 followed by
      a hidden-count tail.

### Accessibility / semantics

- [x] FR-N1 — Card uses `role="group"` with `aria-label` derived from
      the diff/current header (e.g. `"Pending diff: hubspot-deal Deal-92"`).
- [x] FR-N2 — Each field row is a `dl` `dt`/`dd` pair so screen readers
      announce label + value cleanly.
- [x] FR-N3 — The "Open in {SaaS}" link has explicit `aria-label`
      including the SaaS name (`"Open Deal-92 in Hubspot"`).

## Non-functional requirements

- **D28 purity.** No `Transport`, no `fetch`, no `window`, no
  `document`, no effects, no callbacks. Verified by the chat-surface
  ESLint config.
- **D29 robustness.** The component must not throw on any payload
  shape, including missing fields, unexpected types, deeply nested
  objects, very large strings, circular references. Circular detection
  uses a `WeakSet` of visited objects during one render pass.
- **No new dependency.** Uses only `react` and the existing
  chat-surface package primitives.
- **Inline styles only.** Consistent with `TcInlineDiff`, destinations,
  and other chat-surface components.
- **Test coverage.** The prompt's case list is exercised:
  - missing fields (only `resourceId`; only `saas`; nothing at all)
  - deeply nested payloads (5-level deep object; depth cap at 5)
  - very large payloads (string > 2 KB; array > 50; object > 50 keys)
  - unknown SaaS (no `saas`, no `openUrl`)
  - no `proposed` payload — renders current state only
  - circular references render without throwing
  - unsafe `openUrl` (`javascript:`) is dropped, not rendered
  - the adapter is registerable + resolvable via the registry
    (`registerGenericStructuredDiff()` round-trip; falls back from a
    non-existent scheme to the tier-3 adapter)

## Interfaces consumed

- `SaaSRendererAdapter`, `TIER3_SCHEME` from
  `packages/chat-surface/src/surfaces/SaaSRendererAdapter.ts`.
- `registerAdapter` from
  `packages/chat-surface/src/surfaces/SurfaceRegistry.ts`.

## Interfaces produced

```ts
export const GenericStructuredDiff: SaaSRendererAdapter<
  GenericCurrentState,
  GenericStructuredDiffPayload
>;
export function registerGenericStructuredDiff(): void;

export interface GenericCurrentState {
  readonly resourceId?: unknown;
  readonly saas?: unknown;
  readonly openUrl?: unknown;
  readonly fields?: unknown;
}

export interface GenericFieldChange {
  readonly field: string;
  readonly old?: unknown;
  readonly new?: unknown;
}

export interface GenericStructuredDiffPayload {
  readonly resourceId?: unknown;
  readonly saas?: unknown;
  readonly openUrl?: unknown;
  readonly reasoning?: unknown;
  readonly fieldChanges?: readonly GenericFieldChange[] | unknown;
  readonly proposed?: unknown;
  readonly current?: unknown;
}
```

`packages/chat-surface/src/surfaces/index.ts` exposes the adapter and
its register helper through a delimited Phase 4-B block (see
"Coordination" below). The top-level barrel
(`packages/chat-surface/src/index.ts`) is intentionally **not** touched
in this branch — the orchestrator will append the re-exports at merge
time:

```ts
// to be appended at merge time
export {
  GenericStructuredDiff,
  registerGenericStructuredDiff,
  type GenericCurrentState,
  type GenericFieldChange,
  type GenericStructuredDiffPayload,
} from "./surfaces";
```

## Coordination

- The agent does NOT modify `packages/chat-surface/src/index.ts`. The
  exact lines to append are listed in "Interfaces produced" above.
- The agent appends to `packages/chat-surface/src/surfaces/index.ts`
  inside a delimited block:

  ```ts
  // === Phase 4-B tier3-generic-diff ===
  export {
    GenericStructuredDiff,
    registerGenericStructuredDiff,
    type GenericCurrentState,
    type GenericFieldChange,
    type GenericStructuredDiffPayload,
  } from "./GenericStructuredDiff";
  // === end Phase 4-B ===
  ```

## Open questions

1. **Truncated-content "show more" interaction.** The prompt explicitly
   defers the click-to-expand affordance to a later phase — this
   adapter only renders the truncated placeholder text. When the
   product wants progressive disclosure, the host owns it (a parent
   component can pass an `expand` controller down via a future
   contract revision), keeping the adapter pure.
2. **SaaS label normalization.** `saas` is a free-form string from the
   MCP tool. We render it verbatim. Branding / capitalization
   normalization is the host's job (or a later contract revision); the
   tier-3 fallback deliberately does not gate on it.
3. **Field-change ordering.** We render `fieldChanges` in the order
   the agent emits them. Stable order is the producer's responsibility.

The agent **proceeds to implementation** with the documented
placeholders (per D21 — spec-first-then-continue).

## Done criteria

- [x] All FRs met
- [x] `npm run typecheck --workspace @enterprise-search/chat-surface`
      passes
- [x] `npm test --workspace @enterprise-search/chat-surface` passes
- [x] `npm run lint --workspace @enterprise-search/chat-surface` passes
- [x] No imports outside scope; no edits outside the In-scope list
- [x] No bare browser primitives; no transport; no callbacks (D28)
- [x] No new third-party dependency

## Notes for orchestrator review

- Tier-3 lives in `packages/chat-surface/src/surfaces/` (not
  `packages/surface-renderers/`) because it is the registry's last-resort
  default. The registry and its fallback ship from the same package
  boundary; `surface-renderers` stays reserved for SaaS-specific tier-1
  adapters.
- `registerGenericStructuredDiff()` is opt-in. The chat-surface package
  side-effect-imports nothing; the host app calls the registrar during
  startup (Phase 5 `auth-integration` agent wires this).
- Tests cover the explicit prompt list (missing fields, deeply nested,
  very large, unknown SaaS, no proposed) plus circular references and
  unsafe URLs (defensive surface).
