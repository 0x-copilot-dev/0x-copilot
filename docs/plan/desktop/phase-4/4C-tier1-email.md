# Phase 4.C: tier1-email

## Vision

Rewrite the spike-prep `EmailRenderer` as a pure `SaaSRendererAdapter<EmailState, EmailDiff>` (PRD D28) so the email composer renders solely from props the host hands it — no `Transport`, no `fetch`, no actions inside the adapter. The adapter exposes `renderCurrent(state)` (a fresh composer reflecting the persisted draft) and `renderDiff(diff)` (the same composer with a PENDING block highlight, provenance pill, and streaming cursor). Approve / Reject / Suggest-changes live one layer up in the host's `TcSurfaceMount` (already shipped in Phase 4-A).

This locks the first tier-1 adapter against the frozen contract — proving the contract accommodates a real, screenshot-fidelity SaaS render — and clears the legacy `chat-transport` carve-out from the surface-renderers ESLint config, so the substrate-agnostic boundary becomes strict.

## Status

- Status: in-progress
- Agent slug: `tier1-email`
- Branch: `desktop/phase-4-tier1-email`
- Worktree: `.claude/worktrees/agent-adc88c1330e449600`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-4/4C-tier1-email.md` — this file.
- `packages/surface-renderers/src/email/EmailRenderer.tsx` (REWRITE — adapter object + pure render functions; no hooks; no transport)
- `packages/surface-renderers/src/email/EmailRenderer.test.tsx` (REWRITE — render-current / render-diff / accessibility)
- `packages/surface-renderers/src/email/EmailDiffOverlay.tsx` (REWRITE — folded into `renderDiff` if redundant; otherwise kept as a small pure inner component)
- `packages/surface-renderers/src/email/EmailDiffOverlay.test.tsx` (REWRITE or DELETE)
- `packages/surface-renderers/src/email/index.ts` (REWRITE — `registerEmailAdapter()` replaces `registerEmailSurface()`)
- `packages/surface-renderers/src/index.ts` (MODIFY — re-export `registerEmailAdapter`; `registerAll()` calls it)
- `packages/surface-renderers/eslint.config.js` (MODIFY — remove the `src/email/**` carve-out / TODO; ban `@0x-copilot/chat-transport` imports across surface-renderers)

**Out of scope** (do NOT touch):

- `packages/chat-surface/src/**` — surfaces, registry, `TcSurfaceMount`, host wiring are FROZEN here.
- `packages/chat-surface/src/index.ts` — new exports (`EmailState`, `EmailDiff`, `registerEmailAdapter`) live in surface-renderers, not chat-surface.
- `packages/surface-renderers/src/{salesforce,sheets,slides,generic}/**` — Phase 4-D / 4-E / 4-F / 4-B own those.
- `apps/desktop/**` — no consumer change needed; the bootstrap never called `registerEmailSurface()`.

## Functional requirements

- [x] FR-1: `emailAdapter: SaaSRendererAdapter<EmailState, EmailDiff>` is a plain object literal — no closures over state, no hooks at construction. `scheme: 'email'`. `matches(uri)` returns true iff `uri.startsWith('email://')`. `metadata: { origin: 'first-party', schemaVersion: 1 }`.
- [x] FR-2: `EmailState` shape: `{ readonly to: string; readonly cc: string; readonly subject: string; readonly body: string; readonly autoSavedLabel?: string }`. Substring of what the spike-prep `EmailDraftPayload` carried; `bodyPrefix`/`bodySuffix` collapse into a single `body` because the adapter no longer assembles them around a streaming PENDING block — that's the diff's job.
- [x] FR-3: `EmailDiff` shape: `{ readonly base: EmailState; readonly pending: { readonly provenance: string; readonly title: string; readonly description?: string; readonly bodyPrefix: string; readonly streamingBody: string; readonly bodySuffix: string; readonly progressPercent?: number; readonly streaming?: boolean } }`. Captures what's needed to render the highlighted PENDING block over the rest of the composer.
- [x] FR-4: `renderCurrent(state)` renders the composer chrome — `<form>` wrapping a card with: header label "New message", a "Save draft" affordance (ghost button, no `onClick` handler — pure render), three semantic `<label for="…">` + value pairs for To / Cc / Subject, the body in `<p>` paragraphs (whitespace-preserving), and a footer with primary "Send" + ghost "Schedule" buttons and an "Auto-saved · 2s ago" indicator (text overridable via `state.autoSavedLabel`). NO Approve / Reject — those are host-rendered.
- [x] FR-5: `renderDiff(diff)` renders the same composer chrome populated from `diff.base`, plus a PENDING block highlight in the body region. The PENDING block: `<section aria-label="Pending edit" data-state="pending|streaming">` containing a "PENDING · {provenance}" label, the streaming body text (with a streaming cursor when `diff.pending.streaming === true`), and provenance/progress surfaced via a composed `<TcInlineDiff>` in the `pending` / `streaming` state. Approve / Reject callbacks on `TcInlineDiff` are NOT wired — the diff render passes neither `onApprove` nor `onReject`, so the inline-diff card does NOT show those buttons (host's `TcSurfaceMount` renders them outside this adapter's output).
- [x] FR-6: Streaming cursor is a pure visual element (`<span data-testid="streaming-cursor" aria-hidden="true">▍</span>` styled with `animation`-based blink via inline CSS keyframes, mirroring `TcInlineDiff`'s pattern of a single `<style>` block keyed by id to avoid duplicate injection).
- [x] FR-7: `registerEmailAdapter()` calls `registerAdapter(emailAdapter)`. Exported from `packages/surface-renderers/src/email/index.ts` and re-exported from `packages/surface-renderers/src/index.ts`. The top-level `registerAll()` calls `registerEmailAdapter()` (renamed entry, same semantics).
- [x] FR-8: NO state hooks (`useState`, `useReducer`, `useEffect`) inside `renderCurrent` / `renderDiff`. NO `useRef`. NO `transport.*`. NO `fetch`. NO browser globals. The adapter is callable from a worker if needed (D29 forward-compat).
- [x] FR-9: Tests:
  - `renderCurrent` populates To / Cc / Subject from the passed state and surfaces the "Auto-saved · 2s ago" string. NO Approve / Reject in the DOM.
  - `renderDiff` shows the PENDING block, the provenance pill, the streaming cursor when `streaming: true`, and the same composer chrome as `renderCurrent` would render around it.
  - Accessibility: every field has a semantic `<label htmlFor>` paired with the field's id; the form is keyboard-tabbable through To → Cc → Subject → Save draft → Send → Schedule; Approve / Reject are NOT in the document (host renders those). Use Testing Library's queryByRole / getByLabelText assertions, not just `data-testid`.
  - Registration: after `registerEmailAdapter()`, `resolveAdapter('email://draft-1')` returns the email adapter.
- [x] FR-10: ESLint carve-out for `src/email/**` is removed AND the boundary now bans `@0x-copilot/chat-transport` imports across all of `src/**`. Phase 4-A's TODO comment is also removed; the deprecated `SurfaceRendererProps` import path no longer needs an exception.

## Non-functional requirements

- Pure functions for `renderCurrent` / `renderDiff`. Idempotent. Substrate-agnostic.
- No `any`. Use `readonly` on every shape field. `import type` for type-only imports.
- React functional components only. The adapter object itself is not a component; the JSX trees returned by `renderCurrent` and `renderDiff` are built from small private functional sub-components (`<EmailComposerShell>` etc.) inside `EmailRenderer.tsx`. No class components.
- Comments: none by default per `packages/chat-surface` discipline. One short line is fine where a non-obvious trade-off exists (e.g. the dual-call site of `<EmailComposerShell>` in both render functions).
- Tests in Vitest + React Testing Library. `data-testid` is acceptable in addition to (not instead of) role/label queries.
- ESLint passes with the carve-out removed. `npm run lint --workspace @0x-copilot/surface-renderers` clean.

## Interfaces consumed

- `SaaSRendererAdapter`, `registerAdapter`, `resolveAdapter`, `TcInlineDiff`, `InlineDiffState` from `@0x-copilot/chat-surface`.
- NO `Transport`, NO `SurfaceRendererProps`, NO `MockTransport`, NO `EMAIL_FIXTURE` — those belonged to the spike-prep flow and are dropped.
- The PRD's "frozen contract" comments in `SaaSRendererAdapter.ts` and `SurfaceRegistry.ts` govern the public shape.

## Interfaces produced

```ts
// packages/surface-renderers/src/email/EmailRenderer.tsx
export interface EmailState {
  readonly to: string;
  readonly cc: string;
  readonly subject: string;
  readonly body: string;
  readonly autoSavedLabel?: string;
}

export interface EmailDiffPending {
  readonly provenance: string;
  readonly title: string;
  readonly description?: string;
  readonly bodyPrefix: string;
  readonly streamingBody: string;
  readonly bodySuffix: string;
  readonly progressPercent?: number;
  readonly streaming?: boolean;
}

export interface EmailDiff {
  readonly base: EmailState;
  readonly pending: EmailDiffPending;
}

export const emailAdapter: SaaSRendererAdapter<EmailState, EmailDiff>;

// packages/surface-renderers/src/email/index.ts
export { emailAdapter, type EmailState, type EmailDiff } from "./EmailRenderer";
export function registerEmailAdapter(): void;

// packages/surface-renderers/src/index.ts
export {
  emailAdapter,
  registerEmailAdapter,
  type EmailState,
  type EmailDiff,
} from "./email";
export function registerAll(): void; // calls registerEmailAdapter()
```

## Open questions

- **Q1 — Single composer sub-component, both render paths.** `renderCurrent` and `renderDiff` need to share the composer chrome (To/Cc/Subject/footer) to stay screenshot-faithful. They're functional components defined inside `EmailRenderer.tsx`. Not exported. Both `renderCurrent` and `renderDiff` return JSX that includes the shared `<EmailComposerShell>` + a body region that differs by call site. This is the only justified inner-helper duplication; no closures over module-level state.
- **Q2 — Should `EmailDiffOverlay` survive?** Folded into `EmailRenderer.tsx` (`renderDiff`) as a small inline-rendered region wrapping `<TcInlineDiff>`. Keeping the separate file would just be a one-line floating-position wrapper, which `TcInlineDiff` already styles inside its card. Net: delete the file; its test file moves into `EmailRenderer.test.tsx` as a `describe('renderDiff', …)` block.
- **Q3 — Why no `onApprove` / `onReject` callbacks on `TcInlineDiff` inside `renderDiff`?** PRD D28: adapters never own actions. The host's `TcSurfaceMount` already renders the host-owned Approve / Reject buttons around the adapter output when `pendingDiff != null`. `TcInlineDiff`'s built-in buttons only appear when `onApprove`/`onReject` are passed; not passing them keeps the inline-diff card to its provenance pill + title + description visual only — no duplicate action surface.
- **Q4 — `autoSavedLabel` overridable.** The screenshot says "Auto-saved · 2s ago"; the host owns the actual clock. The adapter takes the formatted string from `state.autoSavedLabel` and falls back to the literal "Auto-saved · 2s ago" when absent. This preserves the screenshot fidelity in tests/dev (no host clock) without making the adapter time-aware.

## Done criteria

- [x] All FRs met
- [x] `npm test --workspace @0x-copilot/surface-renderers` passes
- [x] `npm test --workspace @0x-copilot/chat-surface` passes
- [x] `npm run lint --workspace @0x-copilot/surface-renderers` passes with no carve-out
- [x] `npm run typecheck --workspace @0x-copilot/surface-renderers` passes
- [x] No imports of `@0x-copilot/chat-transport` anywhere under `packages/surface-renderers/src/`
- [x] No `Transport`, `fetch`, `window`, `document`, `localStorage`, `EventSource`, `useState`, `useEffect`, `useRef` inside `packages/surface-renderers/src/email/EmailRenderer.tsx`
- [x] `packages/chat-surface/src/index.ts` UNCHANGED by this branch

## Notes for orchestrator review

- The `EMAIL_FIXTURE` re-export from `@0x-copilot/chat-transport` was load-bearing for the spike-prep tests. The Phase 4-C tests construct local `EmailState` / `EmailDiff` objects inline. Net effect: surface-renderers no longer depends on chat-transport for runtime OR test code, which is what lets the carve-out die.
- The `package.json` keeps `@0x-copilot/chat-transport` only if it's still needed elsewhere in surface-renderers. After this branch, it isn't — but I leave the `dependencies` entry alone for now because (a) Phase 4-D / 4-E / 4-F may import it for their own fixtures, and (b) removing it is a no-op for behavior. Phase 4 cleanup or a later boundary-tightening agent can drop it once all four tier-1 renderers have landed.
- The `desktop/renderer/bootstrap.tsx` comment referencing "EmailRenderer's `hasMounted` guard" is now stale (the guard is gone, the renderer no longer has effects). I leave it alone — Phase 5 or a later docs sweep can prune.
- The 2E inline-diff fixtures already cover `streaming`/`pending`/`accepted`/`rejected` rendering for the `TcInlineDiff` primitive. The Phase 4-C `renderDiff` tests verify the adapter passes the right `state` and `provenance` through, not that the inline-diff card itself renders correctly — that's already covered.
