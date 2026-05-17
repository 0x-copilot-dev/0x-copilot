# Phase 4.F: tier1-slides

## Vision

A first-party tier-1 `SaaSRendererAdapter<Slide, SlideDiff>` that renders a
slide preview (title + bullets + thumbnail) for the `slide://` URI scheme,
plus its diff renderer that visualises a before/after slide change inline.

Slides are the fourth concrete tier-1 surface alongside email, sheet, and
salesforce (PRD §3.2). Phase 4-F lands the pure-render half of the
contract — the host (`TcSurfaceMount`, Phase 4-A) owns the
Approve / Reject / Suggest-changes wrapper, transport I/O, and any
host-side toggle UX.

DRY / single-source / simple-elegant principles applied:

- **One adapter, one barrel, one registration.** `index.ts` is the only
  public entry point and the only place that calls `registerAdapter`.
  `SlideRenderer.tsx` and `SlideDiff.tsx` carry no registration concern.
- **No transport, no fetch, no `window`.** Strictly D28. The renderer
  takes `Slide` state in (or a `SlideDiff` value) and returns a
  `ReactElement`. Nothing else is reachable from the function body.
- **Compose `TcInlineDiff` from chat-surface** for the diff annotation
  pill — no second pill component, no duplicate state-machine.
- **Render both before and after regions visibly.** The before/after
  _toggle_ is a host UX concern (D28: adapter is pure). The diff payload
  carries both sides; the renderer always renders both, distinguished by
  a clear "BEFORE" / "AFTER" label and dimmed-vs-full opacity. The host
  may hide one with CSS / its own state — the adapter does not.
- **Inline styles only.** Consistent with `EmailRenderer` /
  `EmailDiffOverlay` and the rest of `surface-renderers`. Design-system
  tokens are not yet wired into inline-style consumers in this package.
- **No comments by default; functional components only; no `any`.**

## Status

- Status: in-progress
- Agent slug: `tier1-slides`
- Branch: `desktop/phase-4-tier1-slides`
- Worktree: `.claude/worktrees/agent-a4229e26379aa1d71`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-4/4F-tier1-slides.md` — this file.
- `packages/surface-renderers/src/slide/SlideRenderer.tsx`
- `packages/surface-renderers/src/slide/SlideRenderer.test.tsx`
- `packages/surface-renderers/src/slide/SlideDiff.tsx`
- `packages/surface-renderers/src/slide/SlideDiff.test.tsx`
- `packages/surface-renderers/src/slide/index.ts`
- Delimited `=== Phase 4-F tier1-slides ===` block inside
  `packages/surface-renderers/src/index.ts` — barrel re-export +
  `registerSlideAdapter()` wired into `registerAll()`.

**Out of scope** (do NOT touch):

- `packages/chat-surface/**` — frozen Phase 0/4-A contract surface.
- `packages/surface-renderers/src/email/**` — owned by 4-C.
- `packages/surface-renderers/src/sheet/**` — owned by 4-D.
- `packages/surface-renderers/src/salesforce/**` — owned by 4-E.
- `TcSurfaceMount.tsx`, host approve/reject UI — owned by 4-A.

## Contract

```ts
// packages/surface-renderers/src/slide/SlideRenderer.tsx
export interface SlideBullet {
  readonly text: string;
}

export interface Slide {
  readonly slideId: string;
  readonly deckId: string;
  readonly slideNumber: number;
  readonly title: string;
  readonly bullets: readonly SlideBullet[];
  readonly thumbnailUrl?: string;
}

export interface SlideDiffPayload {
  readonly diffId: string;
  readonly before: Slide;
  readonly after: Slide;
  readonly summary?: string;
}

export const slideAdapter: SaaSRendererAdapter<Slide, SlideDiffPayload> = {
  scheme: "slide",
  matches: (uri) => uri.startsWith("slide://"),
  renderCurrent: (slide) => <SlideRenderer slide={slide} />,
  renderDiff: (diff) => <SlideDiff diff={diff} />,
  metadata: { origin: "first-party", schemaVersion: 1 },
};
```

`SlideRenderer` renders:

- A title row (slide number + title).
- A bullet list (zero-or-more bullets; renders an "empty deck" placeholder
  when zero).
- A thumbnail block. When `thumbnailUrl` is present, an `<img>` is rendered;
  when absent, a dashed placeholder block (the "missing thumbnail" path
  the PRD test calls out).

`SlideDiff` renders both BEFORE and AFTER regions side-by-side:

- Two `SlideRenderer` instances, each wrapped with a label pill.
- BEFORE is dimmed (opacity 0.6) so the AFTER state is visually dominant.
- An optional `summary` is rendered above the regions as the diff annotation
  via `TcInlineDiff` in the `pending` state (no actions — the host
  ultimately wraps `renderDiff`'s output with the Approve/Reject controls
  per D28; `TcInlineDiff` here is only the visual annotation).

## Tests (vitest + RTL)

- `SlideRenderer.test.tsx`:
  - contract conformance: `slideAdapter.scheme === "slide"`;
    `matches("slide://deck-1/3")` true; `matches("email://x")` false;
    `metadata.schemaVersion === 1`; `metadata.origin === "first-party"`.
  - renders title, slide number, and bullets.
  - renders the thumbnail `<img>` when `thumbnailUrl` is provided.
  - renders the missing-thumbnail placeholder when `thumbnailUrl` is absent.
  - renders empty-bullets placeholder.
- `SlideDiff.test.tsx`:
  - renders both BEFORE and AFTER regions (`data-testid="slide-diff-before"`,
    `data-testid="slide-diff-after"`).
  - BEFORE is visually dimmed (style includes `opacity: 0.6`).
  - renders the `summary` via `TcInlineDiff` when supplied.
  - omits the annotation when `summary` is absent.

## Workflow

1. `pwd && git branch --show-current` — verify worktree.
2. `git checkout -b desktop/phase-4-tier1-slides`.
3. `npm install` at worktree root.
4. Write this sub-PRD (✓).
5. Implement `slide/{SlideRenderer,SlideDiff}.tsx` + tests + `index.ts`.
6. Add delimited block to `packages/surface-renderers/src/index.ts`.
7. `npm test --workspace @enterprise-search/surface-renderers` — pass.
8. `npm run lint --workspace @enterprise-search/surface-renderers` — pass.
9. Commit. Report.
