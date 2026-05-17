# Phase 4.D: tier1-salesforce

## Vision

First-party Salesforce Opportunity renderer implementing the frozen
`SaaSRendererAdapter<Opportunity, OpportunityDiff>` contract on the
`sf-opp://` URI scheme (PRD §3.4, D8, D28). The adapter is **pure render
of state**: no transport, no fetch, no `window`, no IPC, no actions —
every side-effect-capable concern (fetch current state, compute diff,
apply, approve, reject, suggest-changes) lives in the host
`TcSurfaceMount` (PRD D28). This is the tier-1 proof that the adapter
contract carries enough field detail for a real CRM record type without
re-opening the contract.

DRY principles applied:

- **One adapter, two render paths.** `renderCurrent(opportunity)` and
  `renderDiff(diff)` share the same field-row primitive — a field on the
  current view and a changed field on the diff view differ only by the
  provenance pill overlaid on the latter. Same component, two modes.
- **Generic field renderer for custom fields.** Standard fields (Account,
  Stage, Close Date, ARR, Owner) flow through the same `FieldRow`
  primitive as every custom field. Unknown custom-field schemas don't
  need a hand-written component: the generic row is the safety net (and
  the test for "renders unknown custom fields gracefully" exercises this
  path directly).
- **Single inline-diff primitive across all tier-1 renderers.** Diff
  overlay composes `TcInlineDiff` from `chat-surface` — the same primitive
  Email uses (4C), Sheets uses (4E), Slides uses (4F). No per-renderer
  copies of state machine, pill, or button row.
- **Palette parity with Email.** Same dark surface, lime accent, muted
  borders. The product reads as one app across SaaS surfaces — not four
  bolted-on widgets.
- **No comments by default; functional components only; no `any`.**

## Status

- Status: in-progress
- Agent slug: `tier1-salesforce`
- Branch: `desktop/phase-4-tier1-salesforce`
- Worktree: `.claude/worktrees/agent-a9359e3de0dea6ee2`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-4/4D-tier1-salesforce.md` — this file.
- `packages/surface-renderers/src/salesforce/OpportunityRenderer.tsx`
- `packages/surface-renderers/src/salesforce/OpportunityRenderer.test.tsx`
- `packages/surface-renderers/src/salesforce/OpportunityDiff.tsx`
- `packages/surface-renderers/src/salesforce/OpportunityDiff.test.tsx`
- `packages/surface-renderers/src/salesforce/index.ts`
- `packages/surface-renderers/src/index.ts` — delimited Phase 4-D block
  only (orchestrator merges the rest).

**Out of scope** (do NOT touch):

- `packages/chat-surface/src/surfaces/SaaSRendererAdapter.ts` — frozen
  by Phase 4-A.
- `packages/chat-surface/src/thread-canvas/TcInlineDiff.tsx` — frozen
  primitive composed from.
- `packages/surface-renderers/src/email/**` — Phase 4-C territory.
- Any sheet / slide / tier-3 renderer — sibling agents 4B / 4E / 4F.
- `packages/surface-renderers/eslint.config.js` — owned by Phase 4-A.

## Contract & types

Adapter is typed as
`SaaSRendererAdapter<SalesforceOpportunity, SalesforceOpportunityDiff>`.

`SalesforceOpportunity`:

```ts
interface SalesforceOpportunityCustomField {
  readonly key: string;
  readonly label: string;
  readonly value: string;
}

interface SalesforceOpportunity {
  readonly id: string;
  readonly name: string;
  readonly account: string;
  readonly stage: string;
  readonly closeDate: string; // ISO yyyy-mm-dd or display string; adapter is pure render
  readonly arr: string; // pre-formatted; adapter does not localize currency
  readonly owner: string;
  readonly customFields: readonly SalesforceOpportunityCustomField[];
}
```

`SalesforceOpportunityDiff`:

```ts
interface SalesforceOpportunityFieldChange {
  readonly key: string; // 'account' | 'stage' | 'closeDate' | 'arr' | 'owner' | custom key
  readonly label: string; // human label for the field
  readonly previousValue: string;
  readonly nextValue: string;
  readonly provenance: string; // pill copy (e.g. "DRAFTED FROM Q4 SHEET")
}

interface SalesforceOpportunityDiff {
  readonly diffId: string;
  readonly opportunity: SalesforceOpportunity; // the unchanged baseline
  readonly changes: readonly SalesforceOpportunityFieldChange[];
}
```

Why both the baseline opportunity AND the changes live in the diff: the
host fetches state and computes the diff (D28). The adapter is pure
render — given the diff payload, it must be able to draw "the record as
it would look after the diff applies, with each changed field annotated
with provenance." No second state argument needed.

## Functional requirements

- [x] FR-S1 — Adapter exports `scheme: 'sf-opp'` and
      `matches(uri) === uri.startsWith('sf-opp://')`. Verified against
      `sf-opp://acme/006XYZ` (match) and `email://draft-1` (no match) and
      empty string (no match).
- [x] FR-S2 — `renderCurrent(opportunity)` renders the five standard
      fields in this order: Account, Stage, Close Date, ARR, Owner. Each
      uses the shared `FieldRow` primitive (label + value, no diff
      overlay).
- [x] FR-S3 — Custom fields render after standard fields, in the order
      they appear in `customFields`, also through `FieldRow`. Custom
      fields with empty values still render the label and a blank value
      cell (no crashes, no hidden rows).
- [x] FR-S4 — `renderDiff(diff)` renders the same field layout as
      `renderCurrent` but for each field in `diff.changes` overlays:
      previous value (struck-through, muted) → next value (highlighted)
      and a provenance pill on the changed row. Fields not in
      `diff.changes` render exactly as in `renderCurrent` (no change
      annotation).
- [x] FR-S5 — Changed-field annotation composes `TcInlineDiff` only for
      the provenance pill and state semantics; the field-row chrome is
      local to the salesforce renderer. The state passed is `pending`
      (the host controls accept/reject — D28 — so no buttons are wired
      from the adapter).
- [x] FR-S6 — Unknown custom-field keys (anything not in the standard
      five) fall through to the generic `FieldRow` path. This is the same
      code path as known custom fields; the test exercises a custom field
      that does NOT appear in any registry to prove there is no crash and
      no missing rendering.
- [x] FR-S7 — Adapter `metadata` is `{ origin: 'first-party',
schemaVersion: 1 }`.
- [x] FR-S8 — No transport, no fetch, no `window`, no `document`, no
      `localStorage`, no `IPC`. Adapter component is a pure function of
      its single argument. Enforced by ESLint config from Phase 4-A and
      by the contract types (`renderCurrent: (state) => ReactElement`,
      `renderDiff: (diff) => ReactElement`).
- [x] FR-S9 — Module exports `registerSalesforceAdapter()` which calls
      `registerAdapter(opportunityAdapter)`. Idempotent for the same
      schemaVersion (`SurfaceRegistry.registerAdapter` already replaces
      same-version entries — Phase 4-A guarantee).

## Test plan

`OpportunityRenderer.test.tsx`:

- [x] T-S1 — Adapter satisfies the contract: `scheme === 'sf-opp'`,
      `matches('sf-opp://acme/006XYZ')` is `true`,
      `matches('email://draft-1')` is `false`.
- [x] T-S2 — `renderCurrent` renders all five standard fields with the
      expected label + value pairings.
- [x] T-S3 — `renderCurrent` renders custom fields below standard fields
      in input order.
- [x] T-S4 — `renderCurrent` handles an empty `customFields` array
      without crashing.
- [x] T-S5 — Unknown custom-field key renders through the generic field
      row (label + value visible; no error).
- [x] T-S6 — `registerSalesforceAdapter()` registers, and
      `resolveAdapter('sf-opp://acme/006XYZ')` returns the salesforce
      adapter.

`OpportunityDiff.test.tsx`:

- [x] T-S7 — `renderDiff` overlays a provenance pill on each changed
      field.
- [x] T-S8 — `renderDiff` shows previous and next value for each changed
      field (previous struck-through, next highlighted).
- [x] T-S9 — `renderDiff` leaves unchanged fields untouched (no pill, no
      strikethrough).
- [x] T-S10 — `renderDiff` works with a custom-field change (proves the
      diff path also flows through the generic row).

## Non-goals

- No tier-2 / tier-3 fallback work — tier-3 (`GenericStructuredDiff`) is
  Phase 4-B. Tier-2 codegen + sandbox is Phase 6.
- No actions: approve, reject, suggest-changes live in the host
  (`TcSurfaceMount`) per D28. The adapter exposes no buttons of its own.
- No SF API binding: this is rendering only. The host fetches `state`
  and computes `diff` upstream of the adapter.
- No currency formatting / locale handling: the host pre-formats `arr`
  and `closeDate`. Keeps the adapter pure and deterministic.
- No covering all SF page-layout permutations; tier-1 coverage SLO is
  the named five standard fields plus the generic custom-field row.
  Long-tail layouts will fall through to tier-2 (Phase 6) or tier-3
  GenericStructuredDiff (Phase 4-B).

## DRY review

- Reused `TcInlineDiff` for provenance + state. No re-implementation of
  the inline-diff state machine.
- Single `FieldRow` covers both standard and custom fields. The diff
  path overloads the same row with a `change` argument instead of
  creating a separate `DiffFieldRow` component.
- Adapter wires into the existing `registerAdapter` API; no parallel
  registration plumbing.
- Palette constants mirror Email's `PALETTE` so this renderer reads
  visually as part of the same product, not a transplanted module.
