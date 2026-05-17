# Phase 0.D: eslint-and-cleanup

## Vision

Two of Phase 0's load-bearing exit criteria are (a) ports + boundaries are
codified in lint so subsequent agents cannot drift, and (b) the legacy spec
artifacts are gone so there is one source of truth (the PRD). This agent
delivers the surface-renderers half of (a) — tightening
`packages/surface-renderers/eslint.config.js` to forcibly enforce D28's
pure-render rule on tier-1 renderers — and finishes (b) by deleting the
orphaned `docs/architecture/desktop-app-rollout.md`.

Staff-engineer framing: the adapter contract (D27/D28) is the single source
of truth for tier-1, tier-2, and tier-3 renderers. A renderer that quietly
imports `Transport`, calls `fetch`, or touches `window` defeats the
sandboxing posture for tier-2 (D29), couples the substrate-neutral
renderer back to the substrate, and gives every future tier-1 / tier-2 /
tier-3 renderer permission to do the same. The combination of
`no-restricted-globals` and `no-restricted-imports` is the smallest,
simplest mechanism that fails loudly the moment that rule is broken. The
chat-surface boundary is the model — surface-renderers mirrors and
extends it.

The Phase 4-a TODO exception for `@enterprise-search/chat-transport` is
the smallest possible deviation: `EmailRenderer.test.tsx` was scaffolded
in spike-prep with the deprecated `SurfaceRendererProps` shape, which
typed the `transport` prop as `Transport`. The renderer source itself
does not import from chat-transport directly, but the test does — and
the production adapter contract (Phase 4-a) replaces this entire flow
with pure `(state) => ReactNode`. Adding a strict ban now would block
spike-prep work without changing the production outcome; the exception
is narrowed to the package and dated for removal.

## Status

- Status: in-progress
- Agent slug: `eslint-and-cleanup`
- Branch: `desktop/phase-0-eslint-and-cleanup`
- Worktree: `.claude/worktrees/agent-adc53d23e3183f8c8`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-0/0D-eslint-and-cleanup.md` — this file.
- `packages/surface-renderers/eslint.config.js` — tighten globals and
  imports per D28.
- `docs/architecture/desktop-app-rollout.md` — delete.

**Out of scope** (do NOT touch):

- `packages/chat-surface/**` — owned by Agents 0-A and 0-B.
- `apps/desktop/**` — owned by Agent 0-C.
- `packages/surface-renderers/src/**` source code — this agent only
  modifies the lint config. If `EmailRenderer` cannot pass the boundary
  with the Phase 4-a exception, the agent stops and flags it rather than
  refactoring the renderer.
- `packages/chat-surface/eslint.config.js` — read-only reference. Even
  though it contains a stale `desktop-app-rollout.md` comment, fixing
  that is an orchestrator decision (see Open questions).
- `apps/frontend/eslint.config.js`, `apps/frontend/src/api/http.ts` —
  also contain stale `desktop-app-rollout.md` references, also out of
  scope per the agent brief.

## Functional requirements

- [ ] FR-1 — `packages/surface-renderers/eslint.config.js` bans every
      global listed in the agent brief: `window`, `document`,
      `localStorage`, `sessionStorage`, `history`, `navigator`, `location`,
      `fetch`, `XMLHttpRequest`, `EventSource`, `WebSocket`, `crypto`.
      The existing config already bans the first eleven; this work adds
      `crypto`.
- [ ] FR-2 — `packages/surface-renderers/eslint.config.js` bans imports
      from:
  - `@enterprise-search/frontend` and `@enterprise-search/frontend/*`
  - `@enterprise-search/desktop` and `@enterprise-search/desktop/*`
  - `apps/*` and `**/apps/*`
  - `@enterprise-search/chat-surface/shell` and its source-path variants
- [ ] FR-3 — `packages/surface-renderers/eslint.config.js` bans imports
      from `@enterprise-search/chat-transport` and
      `@enterprise-search/chat-transport/*` with a Phase 4-a TODO
      exception comment explaining: EmailRenderer's spike-prep
      `SurfaceRendererProps` shape forces a Transport-typed prop;
      Phase 4-a migrates it to the pure `SaaSRendererAdapter` contract,
      at which point this allowance is removed and the boundary becomes
      strict. The exception is implemented as a deliberate
      `no-restricted-imports` entry that lists the disallowed apps and
      chat-surface/shell groups, with the chat-transport package
      explicitly NOT in the deny list — and the TODO block above the
      rule documents why.
- [ ] FR-4 — `npm run lint --workspace @enterprise-search/surface-renderers`
      passes after the changes.
- [ ] FR-5 — `npm run typecheck --workspace @enterprise-search/surface-renderers`
      passes after the changes (no source touched, but verified).
- [ ] FR-6 — `docs/architecture/desktop-app-rollout.md` is removed from
      the repo tracked tree (via `git rm`).
- [ ] FR-7 — A grep audit across the repo for `desktop-app-rollout`
      (markdown / ts / tsx / py / js, excluding `node_modules`, `.git`,
      `.claude`) is run; any remaining references are listed in this
      sub-PRD's Open questions for orchestrator adjudication. They are
      not silently fixed.

## Non-functional requirements

- **No drift from chat-surface's pattern** — the surface-renderers config
  mirrors the chat-surface config's `no-restricted-globals` +
  `no-restricted-imports` shape. No new ESLint plugins; no flat-config
  cleverness; minimal diff against the existing file.
- **No comments except the one Phase-4-a TODO block** — per PRD §6.1
  and the agent brief. The existing header comment in the file (a
  multi-line explanation of substrate-port discipline) was written by
  spike-prep; per the brief, leave it as-is. Only the Phase 4-a TODO
  block is added.
- **Boundary failure must be loud** — restricted-imports / restricted-
  globals violations are `"error"` level; the lint run fails CI rather
  than warning.
- **Substrate-port discipline** — the boundary added here is the
  surface-renderers-specific instance of the discipline PRD §6.5
  codifies. Any future renderer added under
  `packages/surface-renderers/src/**` automatically inherits it.

## Interfaces consumed

- ESLint flat-config API (already loaded by the existing config):
  `@typescript-eslint/parser`, `globals`.
- The chat-surface config (`packages/chat-surface/eslint.config.js`) as
  the pattern reference — not imported, just mirrored.

## Interfaces produced

No new TypeScript exports. The single externally observable artifact is
the updated lint config, which enforces:

```text
DENY GLOBALS in packages/surface-renderers/src/**/*.{ts,tsx}:
  window, document, history, navigator, location,
  localStorage, sessionStorage,
  fetch, EventSource, XMLHttpRequest, WebSocket, crypto

DENY IMPORT PATTERNS in packages/surface-renderers/src/**/*.{ts,tsx}:
  @enterprise-search/frontend(/**)
  @enterprise-search/desktop(/**)
  apps/*, **/apps/*
  @enterprise-search/chat-surface/shell(/**)
  @enterprise-search/chat-surface/src/shell(/**)

ALLOW (no rule blocks them):
  react, react-dom
  @enterprise-search/chat-surface (the package's barrel — types + diff
    primitives + the (soon-to-be) SaaSRendererAdapter shape)
  @enterprise-search/chat-transport (TEMPORARY — see Phase 4-a TODO in
    the file)
```

## Open questions

1. **Stale `desktop-app-rollout.md` references in other files.** The
   grep audit (FR-7) finds three references to the deleted doc that are
   outside this agent's scope:
   - `packages/chat-surface/eslint.config.js:21` — comment block
     mentions `docs/architecture/desktop-app-rollout.md §3, §E3` as
     architecture context. Agent 0-A or the orchestrator should retarget
     the citation to `docs/plan/desktop/PRD.md` (or to the new
     desktop-app.md once Agent 0-A rewrites it).
   - `apps/frontend/eslint.config.js:15` — same form of stale citation.
     Agent 0-C / orchestrator decision.
   - `apps/frontend/src/api/http.ts:28` — code comment citing
     `desktop-app-rollout.md §3.1`. Same.

   This agent does not touch those files (out of scope). The PRD's §0
   and D22 already authorize the deletion; the references should be
   retargeted at the same time the doc is removed but assigning that
   fix-up to the right agent is an orchestrator call.

2. **Spike-prep's `desktop-app.md` reference to the rollout doc.** The
   on-disk `docs/architecture/desktop-app.md:7` reads "The prior
   `desktop-app-rollout.md` is replaced by the PRD and will be removed
   in Phase 0." Agent 0-A is rewriting `desktop-app.md` to reflect the
   PRD; that rewrite will naturally remove the dangling reference.
   Flagged here for completeness.

3. **S2-decision.md missing.** The agent brief states "Custom Electron
   substrate was confirmed in Phase S (see
   `docs/plan/desktop/phase-0.5/S2-decision.md`)." That file does not
   exist on disk — `docs/plan/desktop/phase-0.5/` contains only
   `S0-spike-prep.md`. Not blocking this work (the substrate decision
   has been communicated through the agent brief itself), but the
   orchestrator should confirm whether the S2 decision report was
   written elsewhere or skipped.

4. **`@enterprise-search/desktop` import ban scope.** The agent brief
   bans imports from `@enterprise-search/desktop`. That package does
   not yet exist on disk (`apps/desktop/` is Phase 1). The ban is added
   prospectively so that when the package lands a future renderer
   cannot reach into it. The `apps/*` ban already covers the source
   path; the package-name ban covers the published name. Both are added
   for completeness.

## Done criteria

- [ ] FR-1 through FR-7 met.
- [ ] `npm run lint --workspace @enterprise-search/surface-renderers`
      passes.
- [ ] `npm run typecheck --workspace @enterprise-search/surface-renderers`
      passes.
- [ ] Repo-wide typecheck (`npm run typecheck` at the root or per
      workspace as the root script dictates) still passes — no source
      file touched, but verified.
- [ ] No file outside the in-scope list is modified.
- [ ] One commit for the sub-PRD
      (`chore(plan): add Phase 0-D eslint-and-cleanup sub-PRD`).
- [ ] One commit for the implementation
      (`chore(desktop): tighten surface-renderers ESLint boundary; remove orphaned rollout doc`).
- [ ] Branch is not pushed and not merged. Orchestrator owns merge.

## Notes for orchestrator review

- The Phase 4-a TODO exception is a single comment block above the
  `no-restricted-imports` rule. It does not add a special-case allow
  entry — it documents that `@enterprise-search/chat-transport` is
  deliberately absent from the deny list. When Phase 4-a lands, the
  comment block and chat-transport import need to be added to the deny
  list together (and the spike-prep
  `surfaces/types.ts#SurfaceRendererProps` shape removed). Searching
  the repo for "Phase 4-a" should surface this in seconds.
- The existing header comment in the eslint config (about substrate
  portability and the "additional rule" for `chat-surface/shell`) was
  written by spike-prep. The agent brief says "Existing chat-surface
  config has comments — that's fine; don't rewrite them." The same
  treatment is given to the existing surface-renderers header.
- The grep audit deliberately excludes `.claude/` to avoid noise from
  agent worktrees / transcripts. If the orchestrator wants the
  exclusion lifted, the audit takes seconds to re-run.
